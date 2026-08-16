import pandas as pd
import pandas_ta as ta
from datetime import timedelta
from common.market_data import fetch_upstox_intraday_candles, get_nfo_lot_size, get_option_legs

def get_premium_at_time(df, target_time):
    past = df[df['timestamp'] <= target_time]
    return past.iloc[-1]['close'] if not past.empty else 0.0

def process_rsi_ubb_strategy(csv_files, upstox_token, progress_callback=None, log_func=print):
    all_signals = []
    for f in csv_files:
        df = pd.read_csv(f)
        df = df[df['seg_sym'].astype(str).str.strip() != 'seg_sym']
        if not df.empty and 'seg_sym' in df.columns and 'time' in df.columns:
            all_signals.append(df)

    if not all_signals:
        log_func("❌ No valid signals found.")
        return pd.DataFrame()

    combined_df = pd.concat(all_signals, ignore_index=True)
    combined_df['time'] = pd.to_datetime(combined_df['time']).dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
    combined_df = combined_df.dropna(subset=['time'])
    
    trade_results = []
    total_trades = len(combined_df)
    api_cache = {}
    chain_cache = {}

    log_func(f"🚀 Processing {total_trades} trades for RSI/UBB Bull Put Spread...")

    for idx, row in combined_df.iterrows():
        clean_symbol = str(row['seg_sym']).replace("NSE:", "").replace("BSE:", "").strip()
        entry_time = row['time']
        entry_price = float(row['ltp'])
        lot_size = get_nfo_lot_size(clean_symbol)

        if progress_callback: progress_callback(idx + 1, total_trades, f"Processing: {clean_symbol}")
        log_func(f"🔍 Trade {idx+1}: {clean_symbol} | Entry: {entry_time} at {entry_price}")

        # Fetch enough historical Spot data to calculate RSI(14) and Supertrend(7,3) accurately
        fetch_start = entry_time - timedelta(days=5) 
        fetch_end = entry_time + timedelta(days=10) 

        spot_1m = fetch_upstox_intraday_candles(clean_symbol, fetch_start, fetch_end, upstox_token, is_key=False, log_func=log_func)
        if spot_1m.empty: continue

        # Resample Spot Data to 15-Minute Timeframe
        spot_15m = spot_1m.set_index('timestamp').resample('15min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
        }).dropna()
        
        # Calculate Indicators using pandas-ta
        spot_15m['RSI_14'] = ta.rsi(spot_15m['close'], length=14)
        sti = ta.supertrend(spot_15m['high'], spot_15m['low'], spot_15m['close'], length=7, multiplier=3)
        if sti is not None and not sti.empty:
            spot_15m['Supertrend'] = sti.iloc[:, 0]
        else:
            spot_15m['Supertrend'] = 0.0
            
        spot_15m = spot_15m.reset_index()
        spot_forward = spot_15m[spot_15m['timestamp'] >= entry_time].reset_index(drop=True)

        if spot_forward.empty: continue

        legs = get_option_legs(clean_symbol, entry_time, entry_price, "Bull Put Spread (OTM2 Sell & OTM4 Buy)", upstox_token, chain_cache, log_func)
        if len(legs) != 2:
            log_func(f"⚠️ {clean_symbol}: Could not resolve exact OTM2/4 strikes. Skipping.")
            continue

        leg_data = []
        for leg in legs:
            cache_key = f"{leg['key']}_{fetch_start.date()}"
            if cache_key not in api_cache:
                api_cache[cache_key] = fetch_upstox_intraday_candles(leg['key'], fetch_start, fetch_end, upstox_token, is_key=True, is_expired=leg['is_expired'], log_func=log_func)
            
            df_1m = api_cache[cache_key]
            if not df_1m.empty:
                df_15m = df_1m.set_index('timestamp').resample('15min').agg({'close': 'last'}).dropna().reset_index()
                leg_data.append({'side': leg['side'], 'df': df_15m, 'type': leg['type']})

        if len(leg_data) != 2: continue

        leg1_entry_prem = get_premium_at_time(leg_data[0]['df'], entry_time)
        leg2_entry_prem = get_premium_at_time(leg_data[1]['df'], entry_time)
        
        initial_net_credit = (leg1_entry_prem * 1) - (leg2_entry_prem * 1) 
        
        if initial_net_credit <= 0:
            log_func(f"⚠️ {clean_symbol}: Inverted spread pricing (Credit <= 0). Skipping.")
            continue

        exit_time = spot_forward.iloc[-1]['timestamp']
        exit_reason = "Data Ended"
        exit_pnl_abs = 0.0

        for i in range(1, len(spot_forward)):
            curr_time = spot_forward.loc[i, 'timestamp']
            
            prev_rsi = spot_forward.loc[i-1, 'RSI_14']
            curr_rsi = spot_forward.loc[i, 'RSI_14']
            prev_close = spot_forward.loc[i-1, 'close']
            curr_close = spot_forward.loc[i, 'close']
            curr_st = spot_forward.loc[i, 'Supertrend']
            prev_st = spot_forward.loc[i-1, 'Supertrend']

            rsi_exit = (prev_rsi < 50) and (curr_rsi > 50)
            st_exit = (prev_close < prev_st) and (curr_close > curr_st)

            l1_curr = get_premium_at_time(leg_data[0]['df'], curr_time)
            l2_curr = get_premium_at_time(leg_data[1]['df'], curr_time)
            current_spread_value = l1_curr - l2_curr
            
            current_pnl_per_qty = initial_net_credit - current_spread_value
            
            target_hit = current_pnl_per_qty >= (0.50 * initial_net_credit) 
            sl_hit = current_pnl_per_qty <= (-1.00 * initial_net_credit)    

            if target_hit:
                exit_reason = "Premium Target Hit (50% Profit)"
                exit_time, exit_pnl_abs = curr_time, current_pnl_per_qty * lot_size; break
            elif sl_hit:
                exit_reason = "Premium SL Hit (100% Loss)"
                exit_time, exit_pnl_abs = curr_time, current_pnl_per_qty * lot_size; break
            elif rsi_exit:
                exit_reason = "RSI Crossed Above 50"
                exit_time, exit_pnl_abs = curr_time, current_pnl_per_qty * lot_size; break
            elif st_exit:
                exit_reason = "Close Crossed Above Supertrend"
                exit_time, exit_pnl_abs = curr_time, current_pnl_per_qty * lot_size; break

        if exit_reason == "Data Ended":
            l1_curr = get_premium_at_time(leg_data[0]['df'], exit_time)
            l2_curr = get_premium_at_time(leg_data[1]['df'], exit_time)
            exit_pnl_abs = (initial_net_credit - (l1_curr - l2_curr)) * lot_size

        trade_results.append({
            'Symbol': clean_symbol,
            'Entry Time': entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            'Exit Time': exit_time.strftime("%Y-%m-%d %H:%M:%S"),
            'Lot Size': lot_size,
            'Initial Net Credit': round(initial_net_credit, 2),
            'Exit Reason': exit_reason,
            'PnL (₹)': round(exit_pnl_abs, 2)
        })

    return pd.DataFrame(trade_results)
