import pandas as pd
from datetime import timedelta
from common.market_data import fetch_upstox_intraday_candles, get_nfo_lot_size, get_option_legs

def get_premium_at_time(df, target_time, use_open=False):
    past = df[df['timestamp'] <= target_time]
    if not past.empty: return past.iloc[-1]['open'] if use_open else past.iloc[-1]['close']
    future = df[df['timestamp'] >= target_time]
    if not future.empty: return future.iloc[0]['open']
    return 0.0

def process_streak_comparative_batch(csv_files, upstox_token, setup_direction, tp_pct, sl_pct, primary_hold_days, secondary_hold_days, progress_callback=None, log_func=print):
    all_signals = []
    
    for f in csv_files:
        try:
            df = pd.read_csv(f)
            df = df[df['seg_sym'].astype(str).str.strip() != 'seg_sym']
            if not df.empty and 'seg_sym' in df.columns and 'time' in df.columns:
                all_signals.append(df)
        except Exception as e:
            log_func(f"❌ Error parsing input: {e}")

    if not all_signals:
        log_func("❌ No valid signals found across inputs.")
        return pd.DataFrame()

    combined_df = pd.concat(all_signals, ignore_index=True)
    
    try:
        combined_df['time'] = pd.to_datetime(combined_df['time'], errors='coerce')
        if combined_df['time'].dt.tz is not None:
            combined_df['time'] = combined_df['time'].dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
    except Exception as e:
        log_func(f"❌ Error parsing time column: {e}")
        
    combined_df = combined_df.dropna(subset=['time'])
    
    trade_results = []
    total_trades = len(combined_df)
    
    api_cache = {}
    chain_cache = {} 
    
    is_bullish = (setup_direction == "Bullish")
    timeframes = sorted(list(set([primary_hold_days, secondary_hold_days])), reverse=True)
    
    strategies = [
        "Long Equity", "Short Equity", 
        "Options: Naked Call Buy", "Options: Naked Put Buy", "Options: Long Straddle", 
        "Options: Bull Put Spread (ATM & OTM1)", "Options: Bull Put Spread (ATM & OTM2)",
        "Options: Bear Call Spread (ATM & OTM1)", "Options: Bear Call Spread (ATM & OTM2)"
    ]

    log_func(f"🚀 Starting backtest processing for {total_trades} trade signals...")
    
    def get_exit_trajectory(spot_df, target_price, sl_price, max_days, is_bullish, tp_pct, sl_pct):
        unique_days = []
        for i, candle in spot_df.iterrows():
            c_time, c_date = candle['timestamp'], candle['timestamp'].date()
            open_p, high_p, low_p = candle['open'], candle['high'], candle['low']

            if c_date not in unique_days:
                unique_days.append(c_date)
                if len(unique_days) > 1:
                    if is_bullish:
                        if open_p >= target_price: return c_time, "Target Hit on Gap-Up", True, len(unique_days)
                        elif open_p <= sl_price: return c_time, "SL Hit on Gap-Down", True, len(unique_days)
                    else:
                        if open_p <= target_price: return c_time, "Target Hit on Gap-Down", True, len(unique_days)
                        elif open_p >= sl_price: return c_time, "SL Hit on Gap-Up", True, len(unique_days)

            if len(unique_days) > max_days:
                return c_time, f"Time Exit ({max_days} Days)", True, len(unique_days)

            if is_bullish:
                if high_p >= target_price: return c_time, f"Target Hit (+{tp_pct*100:.1f}%)", False, len(unique_days)
                elif low_p <= sl_price: return c_time, f"SL Hit (-{sl_pct*100:.1f}%)", False, len(unique_days)
            else:
                if low_p <= target_price: return c_time, f"Target Hit (+{tp_pct*100:.1f}%)", False, len(unique_days)
                elif high_p >= sl_price: return c_time, f"SL Hit (-{sl_pct*100:.1f}%)", False, len(unique_days)

        return spot_df.iloc[-1]['timestamp'], "Data Ended", False, len(unique_days)

    for idx, row in combined_df.iterrows():
        raw_symbol = str(row['seg_sym'])
        entry_time = row['time']
        entry_price = float(row['ltp'])
        clean_symbol = raw_symbol.replace("NSE:", "").replace("BSE:", "").strip()
        lot_size = get_nfo_lot_size(clean_symbol)

        if progress_callback:
            progress_callback(idx + 1, total_trades, f"Processing Trade {idx+1}/{total_trades}: {clean_symbol}")

        log_func(f"🔍 Trade {idx+1}: {clean_symbol} | Entry: {entry_price} | Lot: {lot_size}")

        target_price = entry_price * (1 + tp_pct) if is_bullish else entry_price * (1 - tp_pct)
        sl_price = entry_price * (1 - sl_pct) if is_bullish else entry_price * (1 + sl_pct)

        fetch_start = entry_time
        fetch_end = entry_time + timedelta(days=max(timeframes) + 5) 

        spot_df = fetch_upstox_intraday_candles(
            clean_symbol, fetch_start, fetch_end, upstox_token, 
            is_key=False, is_expired=False, log_func=log_func
        )
        if spot_df.empty:
            log_func(f"⚠️ Could not retrieve candles for {clean_symbol}. Skipping.")
            continue
            
        spot_df = spot_df[spot_df['timestamp'] >= entry_time].reset_index(drop=True)
        if spot_df.empty:
            continue

        exits = {}
        for tf in timeframes:
            exits[tf] = get_exit_trajectory(spot_df, target_price, sl_price, tf, is_bullish, tp_pct, sl_pct)

        trade_data = {
            'Symbol': clean_symbol, 'Lot Size': lot_size,
            'Entry Time': entry_time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        for tf in timeframes:
            trade_data[f'Exit Time ({tf}D)'] = exits[tf][0].strftime("%Y-%m-%d %H:%M:%S")
            trade_data[f'Days Held ({tf}D)'] = exits[tf][3]
            trade_data[f'Exit Reason ({tf}D)'] = exits[tf][1]

        for strat in strategies:
            legs = []
            if strat not in ["Long Equity", "Short Equity"]:
                legs = get_option_legs(
                    clean_symbol, entry_time, entry_price, strat, 
                    access_token=upstox_token, chain_cache=chain_cache, log_func=log_func
                )
                
            for tf in timeframes:
                e_time, e_reason, is_gap_exit, d_held = exits[tf]
                pnl_abs = 0.0
                
                if strat in ["Long Equity", "Short Equity"]:
                    underlying_exit = spot_df[spot_df['timestamp'] == e_time].iloc[0]
                    exit_price = underlying_exit['open'] if is_gap_exit else underlying_exit['close']
                    pnl_abs = (exit_price - entry_price) * lot_size if strat == "Long Equity" else (entry_price - exit_price) * lot_size
                else:
                    for leg in legs:
                        if leg['key'] is None: continue
                        cache_key = f"{leg['key']}_{fetch_start.date()}"
                        if cache_key not in api_cache:
                            api_cache[cache_key] = fetch_upstox_intraday_candles(
                                symbol_or_key=leg['key'], 
                                start_dt=fetch_start, 
                                end_dt=fetch_end, 
                                access_token=upstox_token, 
                                is_key=True, 
                                is_expired=leg.get('is_expired', False),
                                log_func=log_func
                            )
                        
                        leg_df = api_cache[cache_key]
                        if not leg_df.empty:
                            leg_entry = get_premium_at_time(leg_df, entry_time, use_open=False)
                            leg_exit = get_premium_at_time(leg_df, e_time, use_open=is_gap_exit)
                            pnl_abs += (leg_exit - leg_entry) * leg['side'] * lot_size
                
                capital_exposure = entry_price * lot_size
                pnl_pct = (pnl_abs / capital_exposure) * 100 if capital_exposure > 0 else 0
                
                trade_data[f"{strat} ({tf}D) PnL (₹)"] = round(pnl_abs, 2)
                trade_data[f"{strat} ({tf}D) Return (%)"] = round(pnl_pct, 2)

        trade_results.append(trade_data)

    return pd.DataFrame(trade_results)
