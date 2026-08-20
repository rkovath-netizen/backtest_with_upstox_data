import os
import pandas as pd
import pandas_ta as ta
import datetime as dt
from datetime import timedelta, datetime
from common.market_data import fetch_upstox_intraday_candles, get_available_expiries
from common.market_calendar import resolve_expiry
from common.options_builder import build_spread_legs

def get_premium_at_time(df, target_time):
    past = df[df['timestamp'] <= target_time]
    return past.iloc[-1]['close'] if not past.empty else 0.0

# -----------------------------------------------------------------------------------------
# 📊 HISTORICAL BACKTEST ENGINE (Fully Configurable)
# -----------------------------------------------------------------------------------------
def process_ema_rsi_guided_strategy(symbols, start_date, end_date, upstox_token, 
                                    trade_mode="CREDIT_SPREAD", # NEW: 'CREDIT_SPREAD' or 'NAKED_BUY'
                                    sell_offset=2, buy_offset=4, 
                                    require_color=False, require_expansion=False, 
                                    require_rsi_sma=True, require_1h_sma=True, require_adx=True, adx_threshold=20.0, 
                                    require_high_break=False, # NEW
                                    require_ltf_rsi=False, # NEW
                                    dynamic_exit_ema="Trigger EMA", # NEW: 'Trigger EMA', '9 EMA', '21 EMA', '50 EMA'
                                    use_2_candle_exit=False, # NEW
                                    ltf="3min", max_concurrent_trades=3, 
                                    progress_callback=None, log_func=print):
    all_trades = []
    total_symbols = len(symbols)
    
    for sym_idx, symbol in enumerate(symbols):
        log_func(f"\n========================================\n🚀 Processing {symbol} ({trade_mode})\n========================================")
        
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        warmup_start = start_dt - timedelta(days=15)
        
        spot_1m = fetch_upstox_intraday_candles(symbol, warmup_start, end_dt, upstox_token, interval="1minute", log_func=log_func)
        if spot_1m.empty: continue

        # --- Data Prep ---
        df_1h = spot_1m.set_index('timestamp').resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        df_1h['SMA_20'] = ta.sma(df_1h['close'], length=20)
        df_1h = df_1h.reset_index()

        df_15m = spot_1m.set_index('timestamp').resample('15min').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        df_15m['EMA_9'] = ta.ema(df_15m['close'], length=9)
        df_15m['EMA_21'] = ta.ema(df_15m['close'], length=21)
        df_15m['RSI_14'] = ta.rsi(df_15m['close'], length=14)
        df_15m['RSI_SMA_14'] = ta.sma(df_15m['RSI_14'], length=14)
        adx_df = ta.adx(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
        df_15m['ADX_14'] = adx_df['ADX_14'] if (adx_df is not None and not adx_df.empty) else 0.0
        df_15m = df_15m.reset_index()

        df_ltf = spot_1m.set_index('timestamp').resample(ltf).agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        df_ltf['EMA_9'] = ta.ema(df_ltf['close'], length=9)
        df_ltf['EMA_21'] = ta.ema(df_ltf['close'], length=21) # NEW
        df_ltf['EMA_50'] = ta.ema(df_ltf['close'], length=50)
        df_ltf['RSI_14'] = ta.rsi(df_ltf['close'], length=14) # NEW
        df_ltf['body_abs'] = abs(df_ltf['close'] - df_ltf['open'])
        df_ltf['avg_body_10'] = df_ltf['body_abs'].rolling(10).mean().shift(1)
        df_ltf = df_ltf.reset_index()
        
        entries = []
        
        # ==========================================
        # 1. ENTRY LOGIC & SCANNING
        # ==========================================
        for j in range(1, len(df_ltf) - 1):
            c_time = df_ltf.loc[j, 'timestamp']
            if c_time < start_dt or c_time > end_dt: continue

            if symbol == "SENSEX" and (c_time.weekday() == 4 or c_time.time() >= dt.time(13, 0)): continue

            matching_1h = df_1h[df_1h['timestamp'] <= c_time]
            matching_15m = df_15m[df_15m['timestamp'] <= c_time]
            if matching_1h.empty or matching_15m.empty: continue
            
            c_1h_close, c_1h_sma20 = matching_1h.iloc[-1]['close'], matching_1h.iloc[-1]['SMA_20']
            curr_15m = matching_15m.iloc[-1]
            ema9_15, ema21_15, c_adx = curr_15m['EMA_9'], curr_15m['EMA_21'], curr_15m['ADX_14']
            
            if require_adx and (pd.isna(c_adx) or c_adx < adx_threshold): continue
            
            c_open, c_high, c_low, c_close = df_ltf.loc[j, 'open'], df_ltf.loc[j, 'high'], df_ltf.loc[j, 'low'], df_ltf.loc[j, 'close']
            c_ema9, c_ema50 = df_ltf.loc[j, 'EMA_9'], df_ltf.loc[j, 'EMA_50']
            
            is_bullish_trend = ema9_15 > ema21_15
            is_bearish_trend = ema9_15 < ema21_15
            if not (is_bullish_trend or is_bearish_trend): continue
            
            bounce_9, bounce_50 = False, False
            if is_bullish_trend:
                if (c_low < c_ema9) and (c_close > c_ema9): bounce_9 = True
                elif (c_low < c_ema50) and (c_close > c_ema50): bounce_50 = True
            elif is_bearish_trend:
                if (c_high > c_ema9) and (c_close < c_ema9): bounce_9 = True
                elif (c_high > c_ema50) and (c_close < c_ema50): bounce_50 = True
                
            bullish_retracement = is_bullish_trend and (bounce_9 or bounce_50)
            bearish_retracement = is_bearish_trend and (bounce_9 or bounce_50)
            if not (bullish_retracement or bearish_retracement): continue
            
            # --- NEW: LTF RSI FILTER ---
            if require_ltf_rsi:
                ltf_rsi = df_ltf.loc[j, 'RSI_14']
                if bullish_retracement and ltf_rsi < 50: continue
                if bearish_retracement and ltf_rsi > 50: continue
            
            # Additional Filters
            color_ok = (c_close > c_open) if (require_color and bullish_retracement) else ((c_close < c_open) if require_color else True)
            expansion_ok = (df_ltf.loc[j, 'body_abs'] > df_ltf.loc[j, 'avg_body_10']) if require_expansion else True
            rsi_ok = (curr_15m['RSI_14'] > curr_15m['RSI_SMA_14']) if (require_rsi_sma and bullish_retracement) else ((curr_15m['RSI_14'] < curr_15m['RSI_SMA_14']) if require_rsi_sma else True)
            h1_ok = (c_1h_close > c_1h_sma20) if (require_1h_sma and bullish_retracement) else ((c_1h_close < c_1h_sma20) if require_1h_sma else True)
            
            if not (color_ok and expansion_ok and rsi_ok and h1_ok): continue
            
            # --- NEW: HIGH/LOW BREAK CONFIRMATION ---
            entry_time = df_ltf.loc[j+1, 'timestamp']
            entry_price = df_ltf.loc[j+1, 'open']
            
            if require_high_break:
                if bullish_retracement:
                    if df_ltf.loc[j+1, 'high'] > c_high: entry_price = c_high
                    else: continue
                elif bearish_retracement:
                    if df_ltf.loc[j+1, 'low'] < c_low: entry_price = c_low
                    else: continue

            # Determine Trade Intent
            trade_intent = 'BULLISH' if bullish_retracement else 'BEARISH'
            
            entries.append({
                'time': entry_time, 'price': entry_price, 'intent': trade_intent, 
                'ltf_idx': j+1, 'entry_basis': 'EMA_9' if bounce_9 else 'EMA_50'
            })

        if not entries: continue

        # ==========================================
        # 2. TRADE EXECUTION & EXIT LOGIC
        # ==========================================
        api_cache, chain_cache = {}, {}
        active_exits = []
        
        for idx, trade in enumerate(entries):
            entry_time, entry_price, intent = trade['time'], trade['price'], trade['intent']
            
            active_exits = [ext for ext in active_exits if ext > entry_time]
            if len(active_exits) >= max_concurrent_trades: continue 
            if progress_callback: progress_callback(sym_idx + 1, total_symbols, f"[{symbol}] Processing Trade {idx+1}/{len(entries)}")
            
            trade_date = entry_time.date()
            raw_expiries = get_available_expiries(symbol, trade_date, upstox_token, log_func=lambda x: None)
            valid_expiries = resolve_expiry(symbol, trade_date, raw_expiries, log_func=lambda x: None)
            if not valid_expiries: continue
            
            target_expiry = valid_expiries[0]
            if target_expiry == trade_date and len(valid_expiries) > 1: target_expiry = valid_expiries[1]
            
            # --- STRUCTURE LEGS BASED ON TRADE MODE ---
            legs = []
            if trade_mode == "CREDIT_SPREAD":
                strat_name = "Bull Put Spread" if intent == 'BULLISH' else "Bear Call Spread"
                legs = build_spread_legs(symbol=symbol, entry_price=entry_price, strategy_type=strat_name, target_expiry_date=target_expiry, access_token=upstox_token, sell_offset=sell_offset, buy_offset=buy_offset, chain_cache=chain_cache, log_func=lambda x: None)
            elif trade_mode == "NAKED_BUY":
                # Trick the builder into grabbing CE options for Bullish, PE for Bearish, at ATM (0 offset)
                strat_name = "Bear Call Spread" if intent == 'BULLISH' else "Bull Put Spread" 
                temp_legs = build_spread_legs(symbol=symbol, entry_price=entry_price, strategy_type=strat_name, target_expiry_date=target_expiry, access_token=upstox_token, sell_offset=0, buy_offset=2, chain_cache=chain_cache, log_func=lambda x: None)
                if len(temp_legs) > 0:
                    leg = temp_legs[0]
                    leg['side'] = 'B' # Force it to Buy
                    legs = [leg]
                
            if (trade_mode == "CREDIT_SPREAD" and len(legs) != 2) or (trade_mode == "NAKED_BUY" and len(legs) != 1): continue
            
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

            if len(leg_data) != len(legs): continue

            # Financial Calculations Setup
            if trade_mode == "CREDIT_SPREAD":
                l1_entry = get_premium_at_time(leg_data[0]['df'], entry_time)
                l2_entry = get_premium_at_time(leg_data[1]['df'], entry_time)
                entry_value = l1_entry - l2_entry
                if entry_value < 15.0: continue
                capital_employed = abs(legs[0]['strike'] - legs[1]['strike']) * trade_lot_size
                strike_pair_str = f"{legs[0]['strike']} / {legs[1]['strike']}"
            else: # NAKED_BUY
                entry_value = get_premium_at_time(leg_data[0]['df'], entry_time) # Debit paid
                capital_employed = entry_value * trade_lot_size
                strike_pair_str = f"{legs[0]['strike']} (Naked)"

            exit_time, exit_reason, exit_bar_step = df_ltf.iloc[-1]['timestamp'], "Data Ended", len(df_ltf) - 1
            close_below_count, close_above_count = 0, 0

            # Exit Loop
            for step in range(trade['ltf_idx'] + 1, len(df_ltf)):
                curr_time = df_ltf.loc[step, 'timestamp']
                
                # 1. Structural Exits
                if curr_time > entry_time + timedelta(days=3): exit_reason, exit_time, exit_bar_step = "Time Stop (Max 3 Days)", curr_time, step; break
                if curr_time.weekday() == 0 and curr_time.time() >= dt.time(15, 27): exit_reason, exit_time, exit_bar_step = "Monday Pre-Expiry", curr_time, step; break
                
                curr_spot_close = df_ltf.loc[step, 'close']

                # 2. Dynamic Exit Setup (EMA Routing)
                if dynamic_exit_ema == "9 EMA": ref_ema = df_ltf.loc[step, 'EMA_9']
                elif dynamic_exit_ema == "21 EMA": ref_ema = df_ltf.loc[step, 'EMA_21']
                elif dynamic_exit_ema == "50 EMA": ref_ema = df_ltf.loc[step, 'EMA_50']
                else: ref_ema = df_ltf.loc[step, 'EMA_9'] if trade['entry_basis'] == 'EMA_9' else df_ltf.loc[step, 'EMA_50']

                # 3. Apply 2-Candle Rule Logic
                if curr_spot_close < ref_ema:
                    close_below_count += 1
                    close_above_count = 0
                elif curr_spot_close > ref_ema:
                    close_above_count += 1
                    close_below_count = 0
                else:
                    close_below_count = 0; close_above_count = 0

                threshold = 2 if use_2_candle_exit else 1
                
                if intent == 'BULLISH' and close_below_count >= threshold:
                    exit_reason, exit_time, exit_bar_step = f"Dynamic Exit (< {dynamic_exit_ema})", curr_time, step; break
                elif intent == 'BEARISH' and close_above_count >= threshold:
                    exit_reason, exit_time, exit_bar_step = f"Dynamic Exit (> {dynamic_exit_ema})", curr_time, step; break

                # 4. Premium Exits (Spread vs Naked)
                if trade_mode == "CREDIT_SPREAD":
                    l1_curr = get_premium_at_time(leg_data[0]['df'], curr_time)
                    l2_curr = get_premium_at_time(leg_data[1]['df'], curr_time)
                    current_pnl_per_qty = entry_value - (l1_curr - l2_curr)
                    if current_pnl_per_qty >= (0.50 * entry_value): exit_reason, exit_time, exit_bar_step = "Target Hit (50% Decay)", curr_time, step; break
                    elif current_pnl_per_qty <= (-0.60 * entry_value): exit_reason, exit_time, exit_bar_step = "SL Hit (60% Appreciation)", curr_time, step; break
                else: # NAKED_BUY
                    l1_curr = get_premium_at_time(leg_data[0]['df'], curr_time)
                    current_pnl_per_qty = l1_curr - entry_value # Buy low, sell high
                    if current_pnl_per_qty >= (1.00 * entry_value): exit_reason, exit_time, exit_bar_step = "Target Hit (100% ROI)", curr_time, step; break
                    elif current_pnl_per_qty <= (-0.50 * entry_value): exit_reason, exit_time, exit_bar_step = "SL Hit (-50% Premium)", curr_time, step; break

            # Final Settlement
            if trade_mode == "CREDIT_SPREAD":
                l1_final = get_premium_at_time(leg_data[0]['df'], exit_time)
                l2_final = get_premium_at_time(leg_data[1]['df'], exit_time)
                exit_pnl_abs = (entry_value - (l1_final - l2_final)) * trade_lot_size
            else:
                l1_final = get_premium_at_time(leg_data[0]['df'], exit_time)
                exit_pnl_abs = (l1_final - entry_value) * trade_lot_size

            all_trades.append({
                'Symbol': symbol, 'Mode': trade_mode, 'Intent': intent, 'Entry Basis': trade['entry_basis'],
                'Entry Time': entry_time.strftime("%Y-%m-%d %H:%M:%S"), 'Exit Time': exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                'Duration': str(exit_time - entry_time), 'Bars in Trade': exit_bar_step - trade['ltf_idx'],
                'Strike Pair': strike_pair_str, 'Lot Size': trade_lot_size,
                'Premium Init (₹)': round(entry_value, 2), 'Capital Employed (₹)': round(capital_employed, 2),
                'Exit Reason': exit_reason, 'PnL (₹)': round(exit_pnl_abs, 2),
                'PnL (%)': round((exit_pnl_abs / capital_employed * 100) if capital_employed > 0 else 0.0, 2)
            })
            active_exits.append(exit_time)

    return pd.DataFrame(all_trades)
