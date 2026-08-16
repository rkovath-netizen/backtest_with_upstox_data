import pandas as pd
import pandas_ta as ta
from datetime import timedelta, datetime
from common.market_data import fetch_upstox_intraday_candles, get_nfo_lot_size, get_option_legs

def get_premium_at_time(df, target_time):
    past = df[df['timestamp'] <= target_time]
    return past.iloc[-1]['close'] if not past.empty else 0.0

def process_ema_vwap_strategy(symbols, start_date, end_date, upstox_token, sell_offset=2, buy_offset=4, progress_callback=None, log_func=print):
    all_trades = []
    total_symbols = len(symbols)
    
    for sym_idx, symbol in enumerate(symbols):
        log_func(f"\n========================================\n🚀 Processing {symbol} (EMA/VWAP Retracement Strategy)\n========================================")
        
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        warmup_start = start_dt - timedelta(days=15)
        
        spot_1m = fetch_upstox_intraday_candles(symbol, warmup_start, end_dt, upstox_token, is_key=False, log_func=log_func)
        if spot_1m.empty:
            log_func(f"❌ Failed to fetch underlying spot data for {symbol}. Skipping.")
            continue

        log_func(f"📊 Building 15m and 3m dataframes for {symbol}...")
        
        # 15-Minute Dataframe (Macro Trend & Trailing SL)
        df_15m = spot_1m.set_index('timestamp').resample('15min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        
        df_15m['EMA_9'] = ta.ema(df_15m['close'], length=9)
        df_15m['EMA_21'] = ta.ema(df_15m['close'], length=21)
        
        atr_15m = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
        df_15m['ATR_Trailing_Long'] = df_15m['close'] - (3 * atr_15m)
        df_15m['ATR_Trailing_Short'] = df_15m['close'] + (3 * atr_15m)
        df_15m = df_15m.reset_index()

        # 3-Minute Dataframe (Retracement Entries)
        df_3m = spot_1m.set_index('timestamp').resample('3min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        
        df_3m['EMA_9'] = ta.ema(df_3m['close'], length=9)
        typical_price = (df_3m['high'] + df_3m['low'] + df_3m['close']) / 3
        df_3m['VWAP'] = (typical_price * df_3m['volume']).cumsum() / df_3m['volume'].cumsum()
        df_3m = df_3m.reset_index()
        
        entries = []
        lot_size = get_nfo_lot_size(symbol)
        
        for j in range(1, len(df_3m) - 1):
            c_time = df_3m.loc[j, 'timestamp']
            if c_time < start_dt or c_time > end_dt:
                continue
                
            matching_15m = df_15m[df_15m['timestamp'] <= c_time]
            if matching_15m.empty: continue
            curr_15m = matching_15m.iloc[-1]
            
            ema9_15 = curr_15m['EMA_9']
            ema21_15 = curr_15m['EMA_21']
            
            c_low = df_3m.loc[j, 'low']
            c_high = df_3m.loc[j, 'high']
            c_close = df_3m.loc[j, 'close']
            c_ema9 = df_3m.loc[j, 'EMA_9']
            c_vwap = df_3m.loc[j, 'VWAP']
            
            is_bullish_trend = ema9_15 > ema21_15
            bullish_retracement = (c_low < c_ema9 or c_low < c_vwap) and (c_close > c_ema9 or c_close > c_vwap)
            
            is_bearish_trend = ema9_15 < ema21_15
            bearish_retracement = (c_high > c_ema9 or c_high > c_vwap) and (c_close < c_ema9 or c_close < c_vwap)
            
            if is_bullish_trend and bullish_retracement:
                entries.append({
                    'time': df_3m.loc[j+1, 'timestamp'],
                    'price': df_3m.loc[j+1, 'open'],
                    'type': 'PE_SPREAD',
                    '3m_idx': j+1
                })
            elif is_bearish_trend and bearish_retracement:
                entries.append({
                    'time': df_3m.loc[j+1, 'timestamp'],
                    'price': df_3m.loc[j+1, 'open'],
                    'type': 'CE_SPREAD',
                    '3m_idx': j+1
                })

        log_func(f"🎯 Found {len(entries)} valid retracement setups for {symbol}.")
        if not entries: continue

        api_cache = {}
        chain_cache = {}
        
        for idx, trade in enumerate(entries):
            entry_time = trade['time']
            entry_price = trade['price']
            trade_type = trade['type']
            start_3m_idx = trade['3m_idx']
            
            if progress_callback: progress_callback(sym_idx + 1, total_symbols, f"[{symbol}] Processing Trade {idx+1}/{len(entries)}")
            log_func(f"⚡ [{symbol}] Executing {trade_type} at {entry_time} (Spot: {entry_price})")

            strat_name = "Bull Put Spread" if trade_type == 'PE_SPREAD' else "Bear Call Spread"
            legs = get_option_legs(symbol, entry_time, entry_price, strat_name, upstox_token, sell_offset=sell_offset, buy_offset=buy_offset, chain_cache=chain_cache, log_func=log_func)
                
            if len(legs) != 2:
                log_func(f"⚠️ [{symbol}] Could not resolve option legs. Skipping.")
                continue

            fetch_end = entry_time + timedelta(days=10)
            leg_data = []
            for leg in legs:
                cache_key = f"{leg['key']}_{entry_time.date()}"
                if cache_key not in api_cache:
                    api_cache[cache_key] = fetch_upstox_intraday_candles(leg['key'], entry_time - timedelta(days=1), fetch_end, upstox_token, is_key=True, is_expired=leg['is_expired'], log_func=log_func)
                df_1m_leg = api_cache[cache_key]
                if not df_1m_leg.empty:
                    df_3m_leg = df_1m_leg.set_index('timestamp').resample('3min').agg({'close': 'last'}).dropna().reset_index()
                    leg_data.append({'side': leg['side'], 'df': df_3m_leg})

            if len(leg_data) != 2: continue

            leg1_entry = get_premium_at_time(leg_data[0]['df'], entry_time)
            leg2_entry = get_premium_at_time(leg_data[1]['df'], entry_time)
            initial_net_credit = (leg1_entry * 1) - (leg2_entry * 1)
            if initial_net_credit <= 0: continue

            # Capital Employed = Spread Width * Lot Size
            spread_width = abs(legs[0]['strike'] - legs[1]['strike'])
            capital_employed = spread_width * lot_size

            exit_time = df_3m.iloc[-1]['timestamp']
            exit_reason = "Data Ended"
            exit_bar_step = len(df_3m) - 1

            for step in range(start_3m_idx + 1, len(df_3m)):
                curr_time = df_3m.loc[step, 'timestamp']
                curr_spot_close = df_3m.loc[step, 'close']
                
                matching_15m = df_15m[df_15m['timestamp'] <= curr_time]
                if not matching_15m.empty:
                    m15_row = matching_15m.iloc[-1]
                    if trade_type == 'PE_SPREAD' and curr_spot_close < m15_row['ATR_Trailing_Long']:
                        exit_reason = "15m Close < ATR Trailing SL"
                        exit_time = curr_time
                        exit_bar_step = step
                        break
                    elif trade_type == 'CE_SPREAD' and curr_spot_close > m15_row['ATR_Trailing_Short']:
                        exit_reason = "15m Close > ATR Trailing SL"
                        exit_time = curr_time
                        exit_bar_step = step
                        break

                l1_curr = get_premium_at_time(leg_data[0]['df'], curr_time)
                l2_curr = get_premium_at_time(leg_data[1]['df'], curr_time)
                current_spread_val = l1_curr - l2_curr
                current_pnl_per_qty = initial_net_credit - current_spread_val
                
                target_hit = current_pnl_per_qty >= (0.50 * initial_net_credit)
                sl_hit = current_pnl_per_qty <= (-0.50 * initial_net_credit)

                if target_hit:
                    exit_reason = "Target Hit (50% Premium Decay)"
                    exit_time = curr_time
                    exit_bar_step = step
                    break
                elif sl_hit:
                    exit_reason = "SL Hit (50% Premium Appreciation)"
                    exit_time = curr_time
                    exit_bar_step = step
                    break

            l1_final = get_premium_at_time(leg_data[0]['df'], exit_time)
            l2_final = get_premium_at_time(leg_data[1]['df'], exit_time)
            final_spread_val = l1_final - l2_final
            exit_pnl_abs = (initial_net_credit - final_spread_val) * lot_size
            
            bars_in_trade = exit_bar_step - start_3m_idx
            pnl_pct = (exit_pnl_abs / capital_employed * 100) if capital_employed > 0 else 0.0
            trade_duration = str(exit_time - entry_time)

            all_trades.append({
                'Symbol': symbol,
                'Type': trade_type,
                'Entry Time': entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                'Exit Time': exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                'Duration': trade_duration,
                'Bars in Trade': bars_in_trade,
                'Strike Pair': f"{legs[0]['strike']} / {legs[1]['strike']}",
                'Lot Size': lot_size,
                'Net Credit (₹)': round(initial_net_credit, 2),
                'Capital Employed (₹)': round(capital_employed, 2),
                'Exit Reason': exit_reason,
                'PnL (₹)': round(exit_pnl_abs, 2),
                'PnL (%)': round(pnl_pct, 2)
            })

    return pd.DataFrame(all_trades)
