import pandas as pd
import pandas_ta as ta
from datetime import timedelta, datetime
from common.market_data import fetch_continuous_futures_candles, fetch_upstox_intraday_candles
from common.options_builder import build_spread_legs

def get_premium_at_time(df, target_time):
    past = df[df['timestamp'] <= target_time]
    return past.iloc[-1]['close'] if not past.empty else 0.0

def process_rsi_divergence(symbols, start_date, end_date, upstox_token, timeframe="15min", 
                           sell_offset=2, buy_offset=4, 
                           use_regular=True, use_hidden=True, require_extreme=False,
                           progress_callback=None, log_func=print):
    all_trades = []
    total_symbols = len(symbols)
    
    tf_map = {
        "3 Minutes": "3min",
        "5 Minutes": "5min",
        "15 Minutes": "15min",
        "30 Minutes": "30min",
        "1 Hour": "1h"
    }
    tf_str = tf_map.get(timeframe, "15min")
    
    for sym_idx, symbol in enumerate(symbols):
        log_func(f"\n========================================\n🚀 Processing {symbol} (RSI Divergence | {timeframe})\n========================================")
        
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        warmup_start = start_dt - timedelta(days=20)
        
        df_1m = fetch_continuous_futures_candles(symbol, warmup_start, end_dt, upstox_token, log_func=log_func)
        if df_1m.empty:
            log_func(f"❌ Failed to fetch futures data for {symbol}. Skipping.")
            continue

        fut_contract_name = df_1m.attrs.get('contract_name', f"{symbol} Future")
        log_func(f"📊 Resampling {fut_contract_name} to {timeframe} and calculating Divergences...")
        
        df_tf = df_1m.set_index('timestamp').resample(tf_str).agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna().reset_index()
        
        df_tf['RSI'] = ta.rsi(df_tf['close'], length=14)
        
        entries = []
        left_bars = 3
        right_bars = 2
        
        last_pl_idx, last_pl_price, last_pl_rsi = -1, 0, 0
        last_ph_idx, last_ph_price, last_ph_rsi = -1, 0, 0
        
        for j in range(left_bars + right_bars, len(df_tf) - 1):
            c_time = df_tf['timestamp'].iloc[j]
            if c_time < start_dt or c_time > end_dt: continue
                
            center_idx = j - right_bars
            
            # Pivot Low
            window_lows = df_tf['low'].iloc[j - left_bars - right_bars : j + 1].values
            center_low = df_tf['low'].iloc[center_idx]
            
            if center_low == min(window_lows):
                curr_price = df_tf['low'].iloc[center_idx]
                curr_rsi = df_tf['RSI'].iloc[center_idx]
                
                if last_pl_idx != -1 and (center_idx - last_pl_idx) <= 50:
                    is_reg_bull = (curr_price < last_pl_price) and (curr_rsi > last_pl_rsi)
                    is_hid_bull = (curr_price > last_pl_price) and (curr_rsi < last_pl_rsi)
                    
                    extreme_ok = True
                    if require_extreme:
                        extreme_ok = (curr_rsi < 40) or (last_pl_rsi < 40)
                    
                    if extreme_ok and ((use_regular and is_reg_bull) or (use_hidden and is_hid_bull)):
                        sig_type = 'Regular Bull (LL/HL)' if is_reg_bull else 'Hidden Bull (HL/LL)'
                        entries.append({
                            'time': df_tf['timestamp'].iloc[j + 1],
                            'price': df_tf['open'].iloc[j + 1],
                            'type': 'PE_SPREAD',
                            'signal': sig_type
                        })
                
                last_pl_idx, last_pl_price, last_pl_rsi = center_idx, curr_price, curr_rsi

            # Pivot High
            window_highs = df_tf['high'].iloc[j - left_bars - right_bars : j + 1].values
            center_high = df_tf['high'].iloc[center_idx]
            
            if center_high == max(window_highs):
                curr_price = df_tf['high'].iloc[center_idx]
                curr_rsi = df_tf['RSI'].iloc[center_idx]
                
                if last_ph_idx != -1 and (center_idx - last_ph_idx) <= 50:
                    is_reg_bear = (curr_price > last_ph_price) and (curr_rsi < last_ph_rsi)
                    is_hid_bear = (curr_price < last_ph_price) and (curr_rsi > last_ph_rsi)
                    
                    extreme_ok = True
                    if require_extreme:
                        extreme_ok = (curr_rsi > 60) or (last_ph_rsi > 60)
                    
                    if extreme_ok and ((use_regular and is_reg_bear) or (use_hidden and is_hid_bear)):
                        sig_type = 'Regular Bear (HH/LH)' if is_reg_bear else 'Hidden Bear (LH/HH)'
                        entries.append({
                            'time': df_tf['timestamp'].iloc[j + 1], 
                            'price': df_tf['open'].iloc[j + 1],
                            'type': 'CE_SPREAD',
                            'signal': sig_type
                        })
                
                last_ph_idx, last_ph_price, last_ph_rsi = center_idx, curr_price, curr_rsi

        log_func(f"🎯 Found {len(entries)} Divergence setups for {symbol} on {timeframe}.")
        if not entries: continue

        api_cache = {}
        chain_cache = {}
        df_3m = df_1m.set_index('timestamp').resample('3min').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna().reset_index()

        for idx, trade in enumerate(entries):
            entry_time = trade['time']
            entry_price = trade['price']
            trade_type = trade['type']
            signal_name = trade['signal']
            
            if progress_callback: progress_callback(sym_idx + 1, total_symbols, f"[{symbol}] Processing {signal_name} {idx+1}/{len(entries)}")
            
            # 📌 PRINT EXACT CONTRACT SYMBOL WITH PRICE
            log_func(f"⚡ [{symbol}] Executing {signal_name} at {entry_time} ({fut_contract_name} Price: {entry_price})")

            strat_name = "Bull Put Spread" if trade_type == 'PE_SPREAD' else "Bear Call Spread"
            legs = build_spread_legs(symbol, entry_time, entry_price, strat_name, upstox_token, sell_offset=sell_offset, buy_offset=buy_offset, chain_cache=chain_cache, log_func=log_func)
                
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
                    df_3m_leg = df_1m_leg.set_index('timestamp').resample('3min').agg({'close': 'last'}).dropna().reset_index()
                    leg_data.append({'side': leg['side'], 'df': df_3m_leg})

            if len(leg_data) != 2: continue

            leg1_entry = get_premium_at_time(leg_data[0]['df'], entry_time)
            leg2_entry = get_premium_at_time(leg_data[1]['df'], entry_time)
            initial_net_credit = (leg1_entry * 1) - (leg2_entry * 1)
            
            if initial_net_credit < 15.0:
                log_func(f"⚠️ [{symbol}] Premium (₹{initial_net_credit:.2f}) too low. Skipped.")
                continue

            spread_width = abs(legs[0]['strike'] - legs[1]['strike'])
            capital_employed = spread_width * trade_lot_size

            future_3m_data = df_3m[df_3m['timestamp'] >= entry_time].copy()
            if future_3m_data.empty: continue
            
            exit_time = future_3m_data.iloc[-1]['timestamp']
            exit_reason = "Data Ended"
            bars_in_trade = len(future_3m_data)

            for step in range(1, len(future_3m_data)):
                curr_time = future_3m_data.iloc[step]['timestamp']
                
                l1_curr = get_premium_at_time(leg_data[0]['df'], curr_time)
                l2_curr = get_premium_at_time(leg_data[1]['df'], curr_time)
                current_spread_val = l1_curr - l2_curr
                current_pnl_per_qty = initial_net_credit - current_spread_val
                
                if current_pnl_per_qty >= (0.50 * initial_net_credit):
                    exit_reason = "Target Hit (50% Premium Decay)"
                    exit_time = curr_time
                    bars_in_trade = step
                    break
                elif current_pnl_per_qty <= (-1.00 * initial_net_credit):
                    exit_reason = "SL Hit (100% Premium Appreciation)"
                    exit_time = curr_time
                    bars_in_trade = step
                    break

            l1_final = get_premium_at_time(leg_data[0]['df'], exit_time)
            l2_final = get_premium_at_time(leg_data[1]['df'], exit_time)
            final_spread_val = l1_final - l2_final
            
            exit_pnl_abs = (initial_net_credit - final_spread_val) * trade_lot_size
            pnl_pct = (exit_pnl_abs / capital_employed * 100) if capital_employed > 0 else 0.0

            all_trades.append({
                'Symbol': symbol,
                'Contract': fut_contract_name,
                'Signal': signal_name,
                'Type': trade_type,
                'Entry Time': entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                'Exit Time': exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                'Bars in Trade': bars_in_trade,
                'Strike Pair': f"{legs[0]['strike']} / {legs[1]['strike']}",
                'Lot Size': trade_lot_size,
                'Net Credit (₹)': round(initial_net_credit, 2),
                'Capital Employed (₹)': round(capital_employed, 2),
                'Exit Reason': exit_reason,
                'PnL (₹)': round(exit_pnl_abs, 2),
                'PnL (%)': round(pnl_pct, 2)
            })

    return pd.DataFrame(all_trades)
