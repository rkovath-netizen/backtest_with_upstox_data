import os
import pandas as pd
import pandas_ta as ta
import datetime as dt
from datetime import timedelta, datetime
import traceback
from common.market_data import fetch_upstox_intraday_candles, get_available_expiries
from common.market_calendar import resolve_expiry
from common.options_builder import build_spread_legs

def get_premium_at_time(df, target_time):
    past = df[df['timestamp'] <= target_time]
    return past.iloc[-1]['close'] if not past.empty else 0.0

# -----------------------------------------------------------------------------------------
# 📊 HISTORICAL BACKTEST ENGINE (R&D Sandbox)
# -----------------------------------------------------------------------------------------
def process_ema_rsi_guided_strategy(symbols, start_date, end_date, upstox_token, sell_offset=2, buy_offset=4, 
                                    require_color=False, require_expansion=False, require_rsi_sma=True, require_1h_sma=True, 
                                    require_adx=True, adx_threshold=20.0, ltf="3min", max_concurrent_trades=3, 
                                    progress_callback=None, log_func=print):
    all_trades = []
    total_symbols = len(symbols)
    
    for sym_idx, symbol in enumerate(symbols):
        log_func(f"\n========================================\n🚀 Processing {symbol} (LTF: {ltf} | Max Concurrent: {max_concurrent_trades})\n========================================")
        
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        warmup_start = start_dt - timedelta(days=15)
        
        spot_1m = fetch_upstox_intraday_candles(symbol, warmup_start, end_dt, upstox_token, interval="1minute", log_func=log_func)
        if spot_1m.empty:
            continue

        actual_start = spot_1m['timestamp'].min().strftime('%Y-%m-%d')
        actual_end = spot_1m['timestamp'].max().strftime('%Y-%m-%d')
        log_func(f"📊 Resampling {symbol} SPOT (Data: {actual_start} to {actual_end}) to 1H, 15m, and {ltf}...")
        
        # --- 1H Data Prep ---
        df_1h = spot_1m.set_index('timestamp').resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        df_1h['SMA_20'] = ta.sma(df_1h['close'], length=20)
        df_1h = df_1h.reset_index()

        # --- 15m Data Prep ---
        df_15m = spot_1m.set_index('timestamp').resample('15min').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        df_15m['EMA_9'] = ta.ema(df_15m['close'], length=9)
        df_15m['EMA_21'] = ta.ema(df_15m['close'], length=21)
        df_15m['RSI_14'] = ta.rsi(df_15m['close'], length=14)
        df_15m['RSI_SMA_14'] = ta.sma(df_15m['RSI_14'], length=14)
        
        adx_df = ta.adx(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
        df_15m['ADX_14'] = adx_df['ADX_14'] if (adx_df is not None and not adx_df.empty) else 0.0

        atr_15m = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
        df_15m['ATR_Trailing_Long'] = df_15m['close'] - (3 * atr_15m)
        df_15m['ATR_Trailing_Short'] = df_15m['close'] + (3 * atr_15m)
        df_15m = df_15m.reset_index()

        # --- LTF Data Prep ---
        df_ltf = spot_1m.set_index('timestamp').resample(ltf).agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        df_ltf['EMA_9'] = ta.ema(df_ltf['close'], length=9)
        df_ltf['EMA_50'] = ta.ema(df_ltf['close'], length=50)
        df_ltf['body_abs'] = abs(df_ltf['close'] - df_ltf['open'])
        df_ltf['avg_body_10'] = df_ltf['body_abs'].rolling(10).mean().shift(1)
        df_ltf = df_ltf.reset_index()
        
        entries = []
        stats = {'total': 0, 'adx_blocked': 0, 'sensex_blocked': 0, 'trend': 0, 'retrace': 0, 'color': 0, 'expansion': 0, 'rsi': 0, 'h1': 0}
        
        # ==========================================
        # 1. ENTRY LOGIC & SCANNING
        # ==========================================
        for j in range(1, len(df_ltf) - 1):
            c_time = df_ltf.loc[j, 'timestamp']
            
            if c_time < start_dt or c_time > end_dt: continue
                
            stats['total'] += 1

            if symbol == "SENSEX":
                if c_time.weekday() == 4:
                    stats['sensex_blocked'] += 1
                    continue
                if c_time.time() >= dt.time(13, 0):
                    stats['sensex_blocked'] += 1
                    continue

            matching_1h = df_1h[df_1h['timestamp'] <= c_time]
            if matching_1h.empty: continue
            curr_1h = matching_1h.iloc[-1]
            c_1h_close, c_1h_sma20 = curr_1h['close'], curr_1h['SMA_20']

            matching_15m = df_15m[df_15m['timestamp'] <= c_time]
            if matching_15m.empty: continue
            curr_15m = matching_15m.iloc[-1]
            
            ema9_15, ema21_15 = curr_15m['EMA_9'], curr_15m['EMA_21']
            c_rsi, c_rsi_sma, c_adx = curr_15m['RSI_14'], curr_15m['RSI_SMA_14'], curr_15m['ADX_14']
            
            if require_adx and (pd.isna(c_adx) or c_adx < adx_threshold):
                stats['adx_blocked'] += 1
                continue
            
            c_open, c_high, c_low, c_close = df_ltf.loc[j, 'open'], df_ltf.loc[j, 'high'], df_ltf.loc[j, 'low'], df_ltf.loc[j, 'close']
            c_ema9, c_ema50 = df_ltf.loc[j, 'EMA_9'], df_ltf.loc[j, 'EMA_50']
            c_body, avg_body = df_ltf.loc[j, 'body_abs'], df_ltf.loc[j, 'avg_body_10']
            
            is_bullish_trend = ema9_15 > ema21_15
            is_bearish_trend = ema9_15 < ema21_15
            
            if not (is_bullish_trend or is_bearish_trend): continue
            stats['trend'] += 1
            
            # --- NEW EXPLICIT BOUNCE TRACKING ---
            bounce_9 = False
            bounce_50 = False
            
            if is_bullish_trend:
                if (c_low < c_ema9) and (c_close > c_ema9): bounce_9 = True
                elif (c_low < c_ema50) and (c_close > c_ema50): bounce_50 = True
            elif is_bearish_trend:
                if (c_high > c_ema9) and (c_close < c_ema9): bounce_9 = True
                elif (c_high > c_ema50) and (c_close < c_ema50): bounce_50 = True
                
            bullish_retracement = is_bullish_trend and (bounce_9 or bounce_50)
            bearish_retracement = is_bearish_trend and (bounce_9 or bounce_50)
            
            if not (bullish_retracement or bearish_retracement): continue
            stats['retrace'] += 1
            
            # Identify which EMA triggered this setup
            entry_basis = 'EMA_9' if bounce_9 else 'EMA_50'
            
            color_ok = (c_close > c_open) if (require_color and bullish_retracement) else ((c_close < c_open) if require_color else True)
            if not color_ok: continue
            stats['color'] += 1
            
            expansion_ok = (c_body > avg_body) if require_expansion else True
            if not expansion_ok: continue
            stats['expansion'] += 1
            
            rsi_ok = (c_rsi > c_rsi_sma) if (require_rsi_sma and bullish_retracement) else ((c_rsi < c_rsi_sma) if require_rsi_sma else True)
            if not rsi_ok: continue
            stats['rsi'] += 1
            
            h1_ok = (c_1h_close > c_1h_sma20) if (require_1h_sma and bullish_retracement) else ((c_1h_close < c_1h_sma20) if require_1h_sma else True)
            if not h1_ok: continue
            stats['h1'] += 1
            
            trade_type = 'PE_SPREAD' if bullish_retracement else 'CE_SPREAD'
            
            # Append entry_basis to dictionary for the exit engine
            entries.append({
                'time': df_ltf.loc[j+1, 'timestamp'], 
                'price': df_ltf.loc[j+1, 'open'], 
                'type': trade_type, 
                'ltf_idx': j+1,
                'entry_basis': entry_basis
            })

        log_func(f"🔎 Spot Diagnostic Funnel for {symbol} ({ltf}): Bars Scanned: {stats['total']} | Passed Trend: {stats['trend']}")
        if not entries: continue

        # ==========================================
        # 2. TRADE EXECUTION & EXIT LOGIC
        # ==========================================
        api_cache, chain_cache = {}, {}
        active_exits = []
        concurrency_blocked = 0
        
        for idx, trade in enumerate(entries):
            entry_time, entry_price = trade['time'], trade['price']
            trade_type, start_ltf_idx = trade['type'], trade['ltf_idx']
            
            active_exits = [ext for ext in active_exits if ext > entry_time]
            if len(active_exits) >= max_concurrent_trades:
                concurrency_blocked += 1
                continue 
            
            if progress_callback: progress_callback(sym_idx + 1, total_symbols, f"[{symbol}] Processing Trade {idx+1}/{len(entries)}")
            
            trade_date = entry_time.date()
            raw_expiries = get_available_expiries(symbol, trade_date, upstox_token, log_func=lambda x: None)
            valid_expiries = resolve_expiry(symbol, trade_date, raw_expiries, log_func=log_func)
            
            if not valid_expiries: continue
            target_expiry = valid_expiries[0]
            if target_expiry == trade_date and len(valid_expiries) > 1: target_expiry = valid_expiries[1]
            
            strat_name = "Bull Put Spread" if trade_type == 'PE_SPREAD' else "Bear Call Spread"
            legs = build_spread_legs(symbol=symbol, entry_price=entry_price, strategy_type=strat_name, target_expiry_date=target_expiry, access_token=upstox_token, sell_offset=sell_offset, buy_offset=buy_offset, chain_cache=chain_cache, log_func=lambda x: None)
                
            if len(legs) != 2: continue
            trade_lot_size = legs[0]['lot_size']
            fetch_end = entry_time + timedelta(days=10)
            leg_data = []
            
            for leg in legs:
                cache_key = f"{leg['key']}_{entry_time.date()}"
                if cache_key not in api_cache:
                    api_cache[cache_key] = fetch_upstox_intraday_candles(leg['key'], entry_time - timedelta(days=1), fetch_end, upstox_token, is_key=True, is_expired=leg['is_expired'], log_func=lambda x: None)
                df_1m_leg = api_cache[cache_key]
                if not df_1m_leg.empty:
                    leg_data.append({'side': leg['side'], 'df': df_1m_leg.set_index('timestamp').resample(ltf).agg({'close': 'last'}).dropna().reset_index()})

            if len(leg_data) != 2: continue

            leg1_entry = get_premium_at_time(leg_data[0]['df'], entry_time)
            leg2_entry = get_premium_at_time(leg_data[1]['df'], entry_time)
            initial_net_credit = leg1_entry - leg2_entry
            
            if initial_net_credit < 15.0: continue
            spread_width = abs(legs[0]['strike'] - legs[1]['strike'])
            capital_employed = spread_width * trade_lot_size

            exit_time, exit_reason, exit_bar_step = df_ltf.iloc[-1]['timestamp'], "Data Ended", len(df_ltf) - 1
            max_hold_time = entry_time + timedelta(days=3)

            for step in range(start_ltf_idx + 1, len(df_ltf)):
                curr_time = df_ltf.loc[step, 'timestamp']
                
                # ----------------------------------------------------
                # 🛡️ TIME & STRUCTURAL EXITS
                # ----------------------------------------------------
                if curr_time > max_hold_time:
                    exit_reason, exit_time, exit_bar_step = "Time Stop (Max 3 Days Hit)", curr_time, step
                    break
                if curr_time.weekday() == 0 and curr_time.time() >= dt.time(15, 27):
                    exit_reason, exit_time, exit_bar_step = "Monday Pre-Expiry Auto Square-Off", curr_time, step
                    break
                if curr_time > leg_data[0]['df'].iloc[-1]['timestamp']:
                    exit_reason, exit_time, exit_bar_step = "Contract Expired (Failsafe Exit)", leg_data[0]['df'].iloc[-1]['timestamp'], step
                    break

                # ----------------------------------------------------
                # 🛡️ UPDATED DYNAMIC EXITS (Referencing specific EMA)
                # ----------------------------------------------------
                curr_spot_close = df_ltf.loc[step, 'close']
                curr_ema9, curr_ema50 = df_ltf.loc[step, 'EMA_9'], df_ltf.loc[step, 'EMA_50']
                
                # Fetch the specific EMA this trade was based on
                reference_ema = curr_ema9 if trade['entry_basis'] == 'EMA_9' else curr_ema50
                
                if trade_type == 'PE_SPREAD' and curr_spot_close < reference_ema:
                    exit_reason, exit_time, exit_bar_step = f"Dynamic Exit: LTF Close < {trade['entry_basis']}", curr_time, step
                    break
                elif trade_type == 'CE_SPREAD' and curr_spot_close > reference_ema:
                    exit_reason, exit_time, exit_bar_step = f"Dynamic Exit: LTF Close > {trade['entry_basis']}", curr_time, step
                    break

                matching_15m = df_15m[df_15m['timestamp'] <= curr_time]
                if not matching_15m.empty:
                    m15_row = matching_15m.iloc[-1]
                    if trade_type == 'PE_SPREAD' and curr_spot_close < m15_row['ATR_Trailing_Long']:
                        exit_reason, exit_time, exit_bar_step = "15m Spot Close < ATR Trailing SL", curr_time, step
                        break
                    elif trade_type == 'CE_SPREAD' and curr_spot_close > m15_row['ATR_Trailing_Short']:
                        exit_reason, exit_time, exit_bar_step = "15m Spot Close > ATR Trailing SL", curr_time, step
                        break

                # ----------------------------------------------------
                # 💰 PREMIUM DECAY/APPRECIATION EXITS
                # ----------------------------------------------------
                l1_curr, l2_curr = get_premium_at_time(leg_data[0]['df'], curr_time), get_premium_at_time(leg_data[1]['df'], curr_time)
                current_pnl_per_qty = initial_net_credit - (l1_curr - l2_curr)
                
                if current_pnl_per_qty >= (0.50 * initial_net_credit):
                    exit_reason, exit_time, exit_bar_step = "Target Hit (50% Premium Decay)", curr_time, step
                    break
                elif current_pnl_per_qty <= (-0.60 * initial_net_credit):
                    exit_reason, exit_time, exit_bar_step = "SL Hit (60% Premium Appreciation)", curr_time, step
                    break

            l1_final, l2_final = get_premium_at_time(leg_data[0]['df'], exit_time), get_premium_at_time(leg_data[1]['df'], exit_time)
            exit_pnl_abs = (initial_net_credit - (l1_final - l2_final)) * trade_lot_size

            all_trades.append({
                'Symbol': symbol, 'Contract': f"{symbol} SPOT", 'Type': trade_type,
                'Entry Basis': trade['entry_basis'], # Added to final output for UI tracking
                'Entry Time': entry_time.strftime("%Y-%m-%d %H:%M:%S"), 'Exit Time': exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                'Duration': str(exit_time - entry_time), 'Bars in Trade': exit_bar_step - start_ltf_idx,
                'Strike Pair': f"{legs[0]['strike']} / {legs[1]['strike']}", 'Lot Size': trade_lot_size,
                'Net Credit (₹)': round(initial_net_credit, 2), 'Capital Employed (₹)': round(capital_employed, 2),
                'Exit Reason': exit_reason, 'PnL (₹)': round(exit_pnl_abs, 2),
                'PnL (%)': round((exit_pnl_abs / capital_employed * 100) if capital_employed > 0 else 0.0, 2)
            })
            active_exits.append(exit_time)

    return pd.DataFrame(all_trades)
