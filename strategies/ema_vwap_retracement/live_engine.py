import os
import json
import pandas as pd
import pandas_ta as ta
import pytz
from datetime import datetime, timedelta
from common.market_data import fetch_continuous_futures_candles, fetch_upstox_intraday_candles
from common.options_builder import build_spread_legs
from common.notifications import send_trade_email
from common.market_schedule import is_market_open

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
    dt_to = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)
    dt_from = dt_to - timedelta(days=3)
    df = fetch_upstox_intraday_candles(symbol_or_key, dt_from, dt_to, access_token, interval="1minute", is_key=is_key)
    return df.iloc[-1]['close'] if not df.empty else 0.0

def run_live_scan_cycle(symbols, upstox_token, sell_offset, buy_offset, 
                        require_color, require_volume, require_obv_sma, require_1h_sma,
                        email_sender, email_password, log_func):
    
    market_open, reason = is_market_open()
    if not market_open:
        log_func(f"💤 Market is closed ({reason}). Scanner is sleeping...")
        return
    
    log_df = load_live_log()
    receiver_email = "ramkov199@gmail.com"
    
    for symbol in symbols:
        # Force IST Time for API requests
        end_dt = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)
        start_dt = end_dt - timedelta(days=15)
        
        df_1m = fetch_continuous_futures_candles(symbol, start_dt, end_dt, upstox_token, log_func=lambda x: None)
        
        # 🚨 DEBUG: Explicit Data Failure Warning
        if df_1m.empty: 
            log_func(f"❌ [CRITICAL] {symbol} returned 0 candles! Is your Upstox API Token expired?")
            continue
            
        curr_futures_price = df_1m.iloc[-1]['close']
        
        # --- 1H Indicators ---
        df_1h = df_1m.set_index('timestamp').resample('1h').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'}).dropna().reset_index()
        df_1h['SMA_20'] = ta.sma(df_1h['close'], length=20)
        
        # --- 15M Indicators ---
        df_15m = df_1m.set_index('timestamp').resample('15min').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'}).dropna().reset_index()
        df_15m['EMA_9'] = ta.ema(df_15m['close'], length=9)
        df_15m['EMA_21'] = ta.ema(df_15m['close'], length=21)
        df_15m['OBV'] = ta.obv(df_15m['close'], df_15m['volume'])
        df_15m['OBV_SMA_20'] = ta.sma(df_15m['OBV'], length=20)
        atr_15m = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
        df_15m['ATR_Trailing_Long'] = df_15m['close'] - (3 * atr_15m)
        df_15m['ATR_Trailing_Short'] = df_15m['close'] + (3 * atr_15m)
        
        # --- 3M Indicators ---
        df_3m = df_1m.set_index('timestamp').resample('3min').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'}).dropna().reset_index()
        df_3m['EMA_9'] = ta.ema(df_3m['close'], length=9)
        
        # 🚨 BUG FIX: Calculate TRUE Intraday Anchored VWAP (Resets daily at 9:15)
        df_3m['Date'] = df_3m['timestamp'].dt.date
        typical_price = (df_3m['high'] + df_3m['low'] + df_3m['close']) / 3
        df_3m['TPV'] = typical_price * df_3m['volume']
        df_3m['Cum_Vol'] = df_3m.groupby('Date')['volume'].cumsum()
        df_3m['Cum_TPV'] = df_3m.groupby('Date')['TPV'].cumsum()
        df_3m['VWAP'] = df_3m['Cum_TPV'] / df_3m['Cum_Vol'].replace(0, 1)
        
        if len(df_1h) < 1 or len(df_15m) < 1 or len(df_3m) < 2: 
            log_func(f"⚠️ [WARN] Not enough data to calculate indicators for {symbol}.")
            continue

        # ==========================================
        # 1. CHECK EXITS FOR CURRENTLY OPEN TRADES
        # ==========================================
        open_trades = log_df[(log_df['Status'] == 'OPEN') & (log_df['Symbol'] == symbol) & (~log_df['Trade_ID'].str.contains("RSIDIV"))]
        for idx, trade in open_trades.iterrows():
            net_credit = float(trade['Net Credit (₹)'])
            lot_size = int(trade['Lot Size'])
            cap_emp = float(trade['Capital Employed (₹)'])
            trade_type = trade['Type']
            legs = json.loads(trade['Legs_JSON'])
            
            l1_curr = get_latest_close(legs[0]['key'], True, upstox_token)
            l2_curr = get_latest_close(legs[1]['key'], True, upstox_token)
            
            if l1_curr == 0.0 or l2_curr == 0.0:
                log_func(f"⚠️ [WARN] Could not fetch live option premiums for {trade['Trade_ID']}. Will re-check next cycle.")
                continue

            curr_pnl_qty = net_credit - (l1_curr - l2_curr)
            
            exit_reason = None
            if trade_type == 'PE_SPREAD' and curr_futures_price < df_15m.iloc[-1]['ATR_Trailing_Long']:
                exit_reason = "15m Futures Close < ATR Trailing SL"
            elif trade_type == 'CE_SPREAD' and curr_futures_price > df_15m.iloc[-1]['ATR_Trailing_Short']:
                exit_reason = "15m Futures Close > ATR Trailing SL"
            elif curr_pnl_qty >= (0.50 * net_credit):
                exit_reason = "Target Hit (50% Premium Decay)"
            elif curr_pnl_qty <= (-1.00 * net_credit):
                exit_reason = "SL Hit (100% Premium Appreciation)"
                
            if exit_reason:
                exit_pnl = curr_pnl_qty * lot_size
                pnl_pct = (exit_pnl / cap_emp * 100) if cap_emp > 0 else 0
                
                log_df.at[idx, 'Status'] = 'CLOSED'
                log_df.at[idx, 'Exit Time'] = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).strftime("%Y-%m-%d %H:%M:%S")
                log_df.at[idx, 'Exit Reason'] = exit_reason
                log_df.at[idx, 'PnL (₹)'] = round(exit_pnl, 2)
                log_df.at[idx, 'PnL (%)'] = round(pnl_pct, 2)
                save_live_log(log_df)
                
                msg = f"🔴 EXIT SIGNAL: {symbol} {trade_type}\nReason: {exit_reason}\nFinal PnL: ₹{round(exit_pnl, 2)}\nReturn: {round(pnl_pct, 2)}%"
                log_func(msg)
                send_trade_email(f"Trade Closed: {symbol}", msg, email_sender, email_password, receiver_email)

        # ==========================================
        # 2. CHECK ENTRIES ON LAST CLOSED 3M CANDLE
        # ==========================================
        closed_3m = df_3m.iloc[-2]
        c_time = closed_3m['timestamp']
        trade_id_base = f"{symbol}_{c_time.strftime('%Y%m%d_%H%M')}"
        
        if any(log_df['Trade_ID'].str.startswith(trade_id_base)): 
            continue # Already traded this candle
            
        # 🚨 Sync 15m and 1h indicators exactly to the 3m close time
        match_15m = df_15m[df_15m['timestamp'] <= c_time]
        latest_15m = match_15m.iloc[-1] if not match_15m.empty else df_15m.iloc[-1]
        
        match_1h = df_1h[df_1h['timestamp'] <= c_time]
        latest_1h = match_1h.iloc[-1] if not match_1h.empty else df_1h.iloc[-1]
            
        c_open, c_low, c_high, c_close = closed_3m['open'], closed_3m['low'], closed_3m['high'], closed_3m['close']
        c_ema9, c_vwap, c_vol = closed_3m['EMA_9'], closed_3m['VWAP'], closed_3m['volume']
        p_vol = df_3m.iloc[-3]['volume'] if len(df_3m) >= 3 else c_vol
        
        # Boolean Evaluations
        is_bullish = (latest_15m['EMA_9'] > latest_15m['EMA_21'])
        bull_retracement = (c_low < c_ema9 or c_low < c_vwap) and (c_close > c_ema9 or c_close > c_vwap)
        
        is_bearish = (latest_15m['EMA_9'] < latest_15m['EMA_21'])
        bear_retracement = (c_high > c_ema9 or c_high > c_vwap) and (c_close < c_ema9 or c_close < c_vwap)
        
        bull_color_ok = (c_close > c_open)
        bear_color_ok = (c_close < c_open)
        vol_ok = (c_vol > p_vol)
        obv_bull_ok = (latest_15m['OBV'] > latest_15m['OBV_SMA_20'])
        obv_bear_ok = (latest_15m['OBV'] < latest_15m['OBV_SMA_20'])
        h1_bull_ok = (latest_1h['close'] > latest_1h['SMA_20'])
        h1_bear_ok = (latest_1h['close'] < latest_1h['SMA_20'])

        # 🛠️ DEEP DEBUG LOGGING 🛠️
        # This prints directly to the UI box so you can see why trades are failing or passing
        log_func(f"[{symbol}] Eval {c_time.strftime('%H:%M')} Bar | Trend: {'BULL' if is_bullish else 'BEAR' if is_bearish else 'FLAT'} | Retrace: {'Bull' if bull_retracement else 'Bear' if bear_retracement else 'NONE'}")
        log_func(f"   ↳ VolSurge={'PASS' if vol_ok else 'FAIL'} | Color={'BULL' if bull_color_ok else 'BEAR'} | OBV={'BULL' if obv_bull_ok else 'BEAR'} | 1H={'BULL' if h1_bull_ok else 'BEAR'}")

        # Final Logic Compounding
        bull_conds = [
            is_bullish, bull_retracement,
            bull_color_ok if require_color else True,
            vol_ok if require_volume else True,
            obv_bull_ok if require_obv_sma else True,
            h1_bull_ok if require_1h_sma else True
        ]
        
        bear_conds = [
            is_bearish, bear_retracement,
            bear_color_ok if require_color else True,
            vol_ok if require_volume else True,
            obv_bear_ok if require_obv_sma else True,
            h1_bear_ok if require_1h_sma else True
        ]
        
        trade_type = 'PE_SPREAD' if all(bull_conds) else ('CE_SPREAD' if all(bear_conds) else None)
            
        if trade_type:
            log_func(f"✅ [SIGNAL TRIGGERED] Initiating {trade_type} Builder...")
            strat_name = "Bull Put Spread" if trade_type == 'PE_SPREAD' else "Bear Call Spread"
            
            legs = build_spread_legs(symbol, c_time, c_close, strat_name, upstox_token, sell_offset=sell_offset, buy_offset=buy_offset)
            
            if len(legs) == 2:
                l1_curr = get_latest_close(legs[0]['key'], True, upstox_token)
                l2_curr = get_latest_close(legs[1]['key'], True, upstox_token)
                
                if l1_curr == 0.0 or l2_curr == 0.0:
                    log_func(f"⚠️ [WARN] Could not fetch entry premiums for {symbol}. Trade skipped.")
                    continue
                    
                net_credit = l1_curr - l2_curr
                
                if net_credit >= 15.0:
                    lot_size = legs[0]['lot_size']
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
                    log_func(f"⚠️ [REJECTED] {symbol} setup valid, but Premium (₹{net_credit:.2f}) < Minimum ₹15.00 limit.")
            else:
                log_func(f"⚠️ [REJECTED] Options Builder could not find valid options strikes for {symbol}.")
