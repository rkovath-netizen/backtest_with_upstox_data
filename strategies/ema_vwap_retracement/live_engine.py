import os
import json
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from common.market_data import fetch_continuous_futures_candles, fetch_upstox_intraday_candles, get_option_legs, get_nfo_lot_size
from common.notifications import send_trade_email

LOG_FILE = "live_trade_log.csv"

def load_live_log():
    if os.path.exists(LOG_FILE):
        return pd.read_csv(LOG_FILE)
    return pd.DataFrame(columns=[
        'Trade_ID', 'Status', 'Symbol', 'Type', 'Entry Time', 'Exit Time', 
        'Strike Pair', 'Lot Size', 'Net Credit (₹)', 'Capital Employed (₹)', 
        'Exit Reason', 'PnL (₹)', 'PnL (%)', 'Legs_JSON'
    ])

def save_live_log(df):
    df.to_csv(LOG_FILE, index=False)

def get_latest_close(symbol_or_key, is_key, access_token):
    dt_to = datetime.now()
    dt_from = dt_to - timedelta(days=3) # Ensure we cover weekends
    df = fetch_upstox_intraday_candles(symbol_or_key, dt_from, dt_to, access_token, interval="1minute", is_key=is_key)
    return df.iloc[-1]['close'] if not df.empty else 0.0

def run_live_scan_cycle(symbols, upstox_token, sell_offset, buy_offset, 
                        require_color, require_volume, require_obv_sma, require_1h_sma,
                        email_sender, email_password, log_func):
    
    log_df = load_live_log()
    receiver_email = "ramko199@gmail.com"
    
    for symbol in symbols:
        log_func(f"🔄 Polling {symbol} Live Market Data...")
        
        # 1. Fetch live futures data up to this exact minute
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=15)
        df_1m = fetch_continuous_futures_candles(symbol, start_dt, end_dt, upstox_token, log_func=lambda x: None)
        if df_1m.empty: continue
        
        curr_futures_price = df_1m.iloc[-1]['close']
        
        # 2. Build live indicators
        df_1h = df_1m.set_index('timestamp').resample('1h').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'}).dropna()
        df_1h['SMA_20'] = ta.sma(df_1h['close'], length=20)
        
        df_15m = df_1m.set_index('timestamp').resample('15min').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'}).dropna()
        df_15m['EMA_9'] = ta.ema(df_15m['close'], length=9)
        df_15m['EMA_21'] = ta.ema(df_15m['close'], length=21)
        df_15m['OBV'] = ta.obv(df_15m['close'], df_15m['volume'])
        df_15m['OBV_SMA_20'] = ta.sma(df_15m['OBV'], length=20)
        atr_15m = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
        df_15m['ATR_Trailing_Long'] = df_15m['close'] - (3 * atr_15m)
        df_15m['ATR_Trailing_Short'] = df_15m['close'] + (3 * atr_15m)
        
        df_3m = df_1m.set_index('timestamp').resample('3min').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'}).dropna()
        df_3m['EMA_9'] = ta.ema(df_3m['close'], length=9)
        typical_price = (df_3m['high'] + df_3m['low'] + df_3m['close']) / 3
        df_3m['VWAP'] = (typical_price * df_3m['volume']).cumsum() / df_3m['volume'].cumsum().replace(0, 1)
        
        df_1h, df_15m, df_3m = df_1h.reset_index(), df_15m.reset_index(), df_3m.reset_index()
        if len(df_1h) < 1 or len(df_15m) < 1 or len(df_3m) < 2: continue
        
        latest_15m = df_15m.iloc[-1]
        latest_1h = df_1h.iloc[-1]

        # ==========================================
        # 3. CHECK EXITS FOR CURRENTLY OPEN TRADES
        # ==========================================
        open_trades = log_df[(log_df['Status'] == 'OPEN') & (log_df['Symbol'] == symbol)]
        for idx, trade in open_trades.iterrows():
            net_credit = float(trade['Net Credit (₹)'])
            lot_size = int(trade['Lot Size'])
            cap_emp = float(trade['Capital Employed (₹)'])
            trade_type = trade['Type']
            legs = json.loads(trade['Legs_JSON'])
            
            l1_curr = get_latest_close(legs[0]['key'], True, upstox_token)
            l2_curr = get_latest_close(legs[1]['key'], True, upstox_token)
            
            curr_pnl_qty = net_credit - (l1_curr - l2_curr)
            
            exit_reason = None
            if trade_type == 'PE_SPREAD' and curr_futures_price < latest_15m['ATR_Trailing_Long']:
                exit_reason = "15m Futures Close < ATR Trailing SL"
            elif trade_type == 'CE_SPREAD' and curr_futures_price > latest_15m['ATR_Trailing_Short']:
                exit_reason = "15m Futures Close > ATR Trailing SL"
            elif curr_pnl_qty >= (0.50 * net_credit):
                exit_reason = "Target Hit (50% Premium Decay)"
            elif curr_pnl_qty <= (-1.00 * net_credit):
                exit_reason = "SL Hit (100% Premium Appreciation)"
                
            if exit_reason:
                exit_pnl = curr_pnl_qty * lot_size
                pnl_pct = (exit_pnl / cap_emp * 100) if cap_emp > 0 else 0
                
                log_df.at[idx, 'Status'] = 'CLOSED'
                log_df.at[idx, 'Exit Time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_df.at[idx, 'Exit Reason'] = exit_reason
                log_df.at[idx, 'PnL (₹)'] = round(exit_pnl, 2)
                log_df.at[idx, 'PnL (%)'] = round(pnl_pct, 2)
                save_live_log(log_df)
                
                msg = f"🔴 EXIT SIGNAL: {symbol} {trade_type}\nReason: {exit_reason}\nFinal PnL: ₹{round(exit_pnl, 2)}\nReturn: {round(pnl_pct, 2)}%"
                log_func(msg)
                send_trade_email(f"Trade Closed: {symbol}", msg, email_sender, email_password, receiver_email)

        # ==========================================
        # 4. CHECK ENTRIES ON LAST CLOSED 3M CANDLE
        # ==========================================
        closed_3m = df_3m.iloc[-2] # Evaluates only fully printed candles
        c_time = closed_3m['timestamp']
        trade_id_base = f"{symbol}_{c_time.strftime('%Y%m%d_%H%M')}"
        
        # Skip if we already alerted on this specific candle
        if any(log_df['Trade_ID'].str.startswith(trade_id_base)): continue
            
        c_open, c_low, c_high, c_close = closed_3m['open'], closed_3m['low'], closed_3m['high'], closed_3m['close']
        c_ema9, c_vwap, c_vol = closed_3m['EMA_9'], closed_3m['VWAP'], closed_3m['volume']
        p_vol = df_3m.iloc[-3]['volume'] if len(df_3m) >= 3 else c_vol
        
        is_bullish = (latest_15m['EMA_9'] > latest_15m['EMA_21'])
        bull_retracement = (c_low < c_ema9 or c_low < c_vwap) and (c_close > c_ema9 or c_close > c_vwap)
        
        is_bearish = (latest_15m['EMA_9'] < latest_15m['EMA_21'])
        bear_retracement = (c_high > c_ema9 or c_high > c_vwap) and (c_close < c_ema9 or c_close < c_vwap)
        
        bull_conds = [
            is_bullish, bull_retracement,
            (c_close > c_open) if require_color else True,
            (c_vol > p_vol) if require_volume else True,
            (latest_15m['OBV'] > latest_15m['OBV_SMA_20']) if require_obv_sma else True,
            (latest_1h['close'] > latest_1h['SMA_20']) if require_1h_sma else True
        ]
        
        bear_conds = [
            is_bearish, bear_retracement,
            (c_close < c_open) if require_color else True,
            (c_vol > p_vol) if require_volume else True,
            (latest_15m['OBV'] < latest_15m['OBV_SMA_20']) if require_obv_sma else True,
            (latest_1h['close'] < latest_1h['SMA_20']) if require_1h_sma else True
        ]
        
        trade_type = 'PE_SPREAD' if all(bull_conds) else ('CE_SPREAD' if all(bear_conds) else None)
            
        if trade_type:
            strat_name = "Bull Put Spread" if trade_type == 'PE_SPREAD' else "Bear Call Spread"
            legs = get_option_legs(symbol, c_time, c_close, strat_name, upstox_token, sell_offset=sell_offset, buy_offset=buy_offset)
            
            if len(legs) == 2:
                l1_curr = get_latest_close(legs[0]['key'], True, upstox_token)
                l2_curr = get_latest_close(legs[1]['key'], True, upstox_token)
                net_credit = l1_curr - l2_curr
                
                if net_credit >= 15.0:
                    lot_size = legs[0].get('lot_size', get_nfo_lot_size(symbol))
                    cap_emp = abs(legs[0]['strike'] - legs[1]['strike']) * lot_size
                    
                    new_trade = {
                        'Trade_ID': f"{trade_id_base}_{trade_type}",
                        'Status': 'OPEN',
                        'Symbol': symbol,
                        'Type': trade_type,
                        'Entry Time': c_time.strftime("%Y-%m-%d %H:%M:%S"),
                        'Exit Time': '',
                        'Strike Pair': f"{legs[0]['strike']} / {legs[1]['strike']}",
                        'Lot Size': lot_size,
                        'Net Credit (₹)': round(net_credit, 2),
                        'Capital Employed (₹)': round(cap_emp, 2),
                        'Exit Reason': '',
                        'PnL (₹)': 0.0,
                        'PnL (%)': 0.0,
                        'Legs_JSON': json.dumps(legs)
                    }
                    log_df = pd.concat([log_df, pd.DataFrame([new_trade])], ignore_index=True)
                    save_live_log(log_df)
                    
                    msg = f"🟢 ENTRY SIGNAL: {symbol} {trade_type}\nEntry Time: {new_trade['Entry Time']}\nStrikes: {new_trade['Strike Pair']}\nNet Credit: ₹{net_credit:.2f}\nCapital: ₹{cap_emp:.2f}"
                    log_func(msg)
                    send_trade_email(f"Trade Opened: {symbol}", msg, email_sender, email_password, receiver_email)
                else:
                    log_func(f"⚠️ {symbol} valid setup found, but Premium (₹{net_credit:.2f}) < ₹15. Skipped.")
