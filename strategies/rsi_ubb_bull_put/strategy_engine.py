import pandas as pd
import pandas_ta as ta
from datetime import timedelta, datetime
from common.market_data import fetch_upstox_intraday_candles, get_nfo_lot_size, get_option_legs

def get_premium_at_time(df, target_time):
    past = df[df['timestamp'] <= target_time]
    return past.iloc[-1]['close'] if not past.empty else 0.0

def process_autonomous_rsi_ubb(symbols, resample_freq, rsi_threshold, start_date, end_date, upstox_token, progress_callback=None, log_func=print):
    all_trades = []
    total_symbols = len(symbols)
    
    for sym_idx, symbol in enumerate(symbols):
        log_func(f"\n========================================\n🚀 Processing Index [{sym_idx+1}/{total_symbols}]: {symbol} ({resample_freq} Timeframe)\n========================================")
        
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        warmup_start = start_dt - timedelta(days=20)
        
        spot_1m = fetch_upstox_intraday_candles(symbol, warmup_start, end_dt, upstox_token, is_key=False, log_func=log_func)
        if spot_1m.empty:
            log_func(f"❌ Failed to fetch underlying spot data for {symbol}. Skipping.")
            continue

        log_func(f"📊 Resampling {symbol} to {resample_freq} timeframe and calculating indicators...")
        
        # Resample using the selected timeframe frequency (e.g., 5min, 15min, 30min)
        spot_df = spot_1m.set_index('timestamp').resample(resample_freq).agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
        }).dropna()
        
        spot_df['RSI_14'] = ta.rsi(spot_df['close'], length=14)
        
        bbands = ta.bbands(spot_df['close'], length=20, std=2)
        if bbands is not None and not bbands.empty and bbands.shape[1] >= 2:
            spot_df['UBB_20_2'] = bbands.iloc[:, 1]
        else:
            spot_df['UBB_20_2'] = 0.0
        
        sti = ta.supertrend(spot_df['high'], spot_df['low'], spot_df['close'], length=7, multiplier=3)
        spot_df['Supertrend'] = sti.iloc[:, 0] if sti is not None else 0.0
        
        spot_df = spot_df.reset_index()
        spot_df = spot_df.dropna().reset_index(drop=True)
        
        entries = []
        lot_size = get_nfo_lot_size(symbol)
        
        for i in range(1, len(spot_df)):
            curr_time = spot_df.loc[i, 'timestamp']
            if curr_time < start_dt or curr_time > end_dt:
                continue
                
            prev_rsi = spot_df.loc[i-1, 'RSI_14']
            curr_rsi = spot_df.loc[i, 'RSI_14']
            curr_close = spot_df.loc[i, 'close']
            curr_ubb = spot_df.loc[i, 'UBB_20_2']
            
            rsi_crossed_above = (prev_rsi <= rsi_threshold) and (curr_rsi > rsi_threshold)
            closed_below_ubb = curr_close < curr_ubb
            
            if rsi_crossed_above and closed_below_ubb:
                if i + 1 < len(spot_df):
                    entry_time = spot_df.loc[i+1, 'timestamp']
                    entry_price = spot_df.loc[i+1, 'open']
                    entries.append({'time': entry_time, 'price': entry_price, 'idx': i+1})

        log_func(f"🎯 Found {len(entries)} valid setups for {symbol}.")
        if not entries:
            continue

        api_cache = {}
        chain_cache = {}
        
        for idx, trade in enumerate(entries):
            entry_time = trade['time']
            entry_price = trade['price']
            start_idx = trade['idx']
            
            if progress_callback: 
                progress_callback(sym_idx + 1, total_symbols, f"[{symbol}] Processing Trade {idx+1}/{len(entries)}")
            
            log_func(f"⚡ [{symbol}] Executing Setup {idx+1}: {entry_time} at {entry_price}")

            legs = get_option_legs(symbol, entry_time, entry_price, "Bull Put Spread (OTM2 Sell & OTM4 Buy)", upstox_token, chain_cache, log_func)
            if len(legs) != 2:
                log_func(f"⚠️ [{symbol}] Could not resolve exact OTM2/4 strikes for {entry_time}. Skipping.")
                continue

            fetch_end = entry_time + timedelta(days=10) 
            leg_data = []
            for leg in legs:
                cache_key = f"{leg['key']}_{entry_time.date()}"
                if cache_key not in api_cache:
                    api_cache[cache_key] = fetch_upstox_intraday_candles(leg['key'], entry_time - timedelta(days=1), fetch_end, upstox_token, is_key=True, is_expired=leg['is_expired'], log_func=log_func)
                
                df_1m = api_cache[cache_key]
                if not df_1m.empty:
                    df_tf = df_1m.set_index('timestamp').resample(resample_freq).agg({'close': 'last'}).dropna().reset_index()
                    leg_data.append({'side': leg['side'], 'df': df_tf})

            if len(leg_data) != 2: continue

            leg1_entry_prem = get_premium_at_time(leg_data[0]['df'], entry_time)
            leg2_entry_prem = get_premium_at_time(leg_data[1]['df'], entry_time)
            initial_net_credit = (leg1_entry_prem * 1) - (leg2_entry_prem * 1) 
            
            if initial_net_credit <= 0: continue

            exit_time = spot_df.iloc[-1]['timestamp']
            exit_reason = "Data Ended"
            exit_pnl_abs = 0.0

            for step in range(start_idx + 1, len(spot_df)):
                curr_time = spot_df.loc[step, 'timestamp']
                
                prev_rsi = spot_df.loc[step-1, 'RSI_14']
                curr_rsi = spot_df.loc[step, 'RSI_14']
                curr_close = spot_df.loc[step, 'close']
                curr_st = spot_df.loc[step, 'Supertrend']

                rsi_exit = (prev_rsi < 50) and (curr_rsi > 50)
                st_exit = curr_close > curr_st

                l1_curr = get_premium_at_time(leg_data[0]['df'], curr_time)
                l2_curr = get_premium_at_time(leg_data[1]['df'], curr_time)
                current_spread_value = l1_curr - l2_curr
                current_pnl_per_qty = initial_net_credit - current_spread_value
                
                target_hit = current_pnl_per_qty >= (0.50 * initial_net_credit) 
                sl_hit = current_pnl_per_qty <= (-1.00 * initial_net_credit)    

                if target_hit:
                    exit_reason = "Target Hit (50% Profit)"
                    exit_time, exit_pnl_abs = curr_time, current_pnl_per_qty * lot_size; break
                elif sl_hit:
                    exit_reason = "SL Hit (100% Loss)"
                    exit_time, exit_pnl_abs = curr_time, current_pnl_per_qty * lot_size; break
                elif rsi_exit:
                    exit_reason = "RSI Crossed Above 50"
                    exit_time, exit_pnl_abs = curr_time, current_pnl_per_qty * lot_size; break
                elif st_exit:
                    exit_reason = "Close > Supertrend"
                    exit_time, exit_pnl_abs = curr_time, current_pnl_per_qty * lot_size; break

            if exit_reason == "Data Ended":
                l1_curr = get_premium_at_time(leg_data[0]['df'], exit_time)
                l2_curr = get_premium_at_time(leg_data[1]['df'], exit_time)
                exit_pnl_abs = (initial_net_credit - (l1_curr - l2_curr)) * lot_size

            all_trades.append({
                'Symbol': symbol,
                'Entry Time': entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                'Exit Time': exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                'Lot Size': lot_size,
                'Net Credit (₹)': round(initial_net_credit, 2),
                'Exit Reason': exit_reason,
                'PnL (₹)': round(exit_pnl_abs, 2)
            })

    return pd.DataFrame(all_trades)
