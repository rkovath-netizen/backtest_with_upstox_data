
import pandas as pd
import pandas_ta as ta
from datetime import timedelta, datetime
from common.market_data import fetch_upstox_intraday_candles, get_nfo_lot_size, get_option_legs

def get_premium_at_time(df, target_time):
    past = df[df['timestamp'] <= target_time]
    return past.iloc[-1]['close'] if not past.empty else 0.0

def process_autonomous_rsi_ubb(symbol, start_date, end_date, upstox_token, progress_callback=None, log_func=print):
    log_func(f"🚀 Fetching raw 1-minute data for {symbol} from {start_date} to {end_date}...")
    
    # 1. Fetch Master Spot Data
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())
    
    # We fetch a few days prior to start_date to allow RSI(14) and UBB(20) to calculate without NaN values at the start
    warmup_start = start_dt - timedelta(days=10)
    
    spot_1m = fetch_upstox_intraday_candles(symbol, warmup_start, end_dt, upstox_token, is_key=False, log_func=log_func)
    if spot_1m.empty:
        log_func("❌ Failed to fetch underlying spot data.")
        return pd.DataFrame()

    log_func("📊 Resampling to 15-minute timeframe and calculating indicators...")
    
    # 2. Resample to 15m and calculate Indicators
    spot_15m = spot_1m.set_index('timestamp').resample('15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    
    spot_15m['RSI_14'] = ta.rsi(spot_15m['close'], length=14)
    
    # Bollinger Bands (Length 20, StdDev 2)
    bbands = ta.bbands(spot_15m['close'], length=20, std=2)
    # The Upper Band is always the second column (iloc[:, 1]) in pandas-ta
    if bbands is not None and not bbands.empty and bbands.shape[1] >= 2:
        spot_15m['UBB_20_2'] = bbands.iloc[:, 1]
    else:
        spot_15m['UBB_20_2'] = 0.0
    
    # Supertrend (7, 3)
    sti = ta.supertrend(spot_15m['high'], spot_15m['low'], spot_15m['close'], length=7, multiplier=3)
    spot_15m['Supertrend'] = sti.iloc[:, 0] if sti is not None else 0.0
    
    spot_15m = spot_15m.reset_index()
    
    # 3. Native Scanner: Find exact entry triggers
    entries = []
    lot_size = get_nfo_lot_size(symbol)
    
    for i in range(1, len(spot_15m)):
        curr_time = spot_15m.loc[i, 'timestamp']
        if curr_time < start_dt: continue # Skip warmup period
        
        # Rule 1: Must be Monday (0 = Monday in Python) and time >= 09:15
        # (Handling holidays automatically: if it's Tuesday and it's the first trading day of week, we can adapt, 
        # but for now we look for the literal first candle of the week)
        day_of_week = curr_time.weekday()
        if day_of_week > 1: continue # Restrict to Mon/Tue to satisfy "Monday or next trading day" loosely
        
        prev_rsi = spot_15m.loc[i-1, 'RSI_14']
        curr_rsi = spot_15m.loc[i, 'RSI_14']
        curr_close = spot_15m.loc[i, 'close']
        curr_ubb = spot_15m.loc[i, 'UBB_20_2']
        
        # Rule 2: RSI(14) crosses above 70
        rsi_crossed_above = (prev_rsi <= 70) and (curr_rsi > 70)
        
        # Rule 3: Close is below UBB
        closed_below_ubb = curr_close < curr_ubb
        
        if rsi_crossed_above and closed_below_ubb:
            # Entry occurs on the NEXT candle open
            if i + 1 < len(spot_15m):
                entry_time = spot_15m.loc[i+1, 'timestamp']
                entry_price = spot_15m.loc[i+1, 'open']
                entries.append({'time': entry_time, 'price': entry_price, 'idx': i+1})

    total_trades = len(entries)
    if total_trades == 0:
        return pd.DataFrame()

    log_func(f"🎯 Found {total_trades} valid entry setups! Processing Option Exits...")

    # 4. Process Option Spread Exits
    trade_results = []
    api_cache = {}
    chain_cache = {}
    
    for idx, trade in enumerate(entries):
        entry_time = trade['time']
        entry_price = trade['price']
        start_idx = trade['idx']
        
        if progress_callback: progress_callback(idx + 1, total_trades, f"Processing Trade {idx+1}/{total_trades}")
        log_func(f"⚡ Executing Setup {idx+1}: {entry_time} at {entry_price}")

        # Fetch Option Legs (Sell OTM2, Buy OTM4)
        legs = get_option_legs(symbol, entry_time, entry_price, "Bull Put Spread (OTM2 Sell & OTM4 Buy)", upstox_token, chain_cache, log_func)
        if len(legs) != 2:
            log_func(f"⚠️ Could not resolve exact OTM2/4 strikes for {entry_time}. Skipping.")
            continue

        fetch_end = entry_time + timedelta(days=10) # Buffer for exit logic
        leg_data = []
        for leg in legs:
            cache_key = f"{leg['key']}_{entry_time.date()}"
            if cache_key not in api_cache:
                api_cache[cache_key] = fetch_upstox_intraday_candles(leg['key'], entry_time - timedelta(days=1), fetch_end, upstox_token, is_key=True, is_expired=leg['is_expired'], log_func=log_func)
            
            df_1m = api_cache[cache_key]
            if not df_1m.empty:
                df_15m = df_1m.set_index('timestamp').resample('15min').agg({'close': 'last'}).dropna().reset_index()
                leg_data.append({'side': leg['side'], 'df': df_15m})

        if len(leg_data) != 2: continue

        # Calculate Initial Entry Premium
        leg1_entry_prem = get_premium_at_time(leg_data[0]['df'], entry_time)
        leg2_entry_prem = get_premium_at_time(leg_data[1]['df'], entry_time)
        initial_net_credit = (leg1_entry_prem * 1) - (leg2_entry_prem * 1) 
        
        if initial_net_credit <= 0: continue

        exit_time = spot_15m.iloc[-1]['timestamp']
        exit_reason = "Data Ended"
        exit_pnl_abs = 0.0

        # Step forward bar-by-bar to check dynamic exits
        for step in range(start_idx + 1, len(spot_15m)):
            curr_time = spot_15m.loc[step, 'timestamp']
            
            prev_rsi = spot_15m.loc[step-1, 'RSI_14']
            curr_rsi = spot_15m.loc[step, 'RSI_14']
            curr_close = spot_15m.loc[step, 'close']
            curr_st = spot_15m.loc[step, 'Supertrend']

            # Exits requested: RSI crosses > 50 from below, Close > Supertrend
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

        trade_results.append({
            'Symbol': symbol,
            'Entry Time': entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            'Exit Time': exit_time.strftime("%Y-%m-%d %H:%M:%S"),
            'Lot Size': lot_size,
            'Net Credit (₹)': round(initial_net_credit, 2),
            'Exit Reason': exit_reason,
            'PnL (₹)': round(exit_pnl_abs, 2)
        })

    return pd.DataFrame(trade_results)
