import os
import gc
import itertools
import pandas as pd
import numpy as np
import datetime as dt
from datetime import timedelta, datetime
import pandas_ta as ta

from common.market_data import fetch_upstox_intraday_candles, get_available_expiries
from common.market_calendar import resolve_expiry
from common.options_builder import build_spread_legs

def get_premium_at_time(df, target_time):
    past = df[df['timestamp'] <= target_time]
    return past.iloc[-1]['close'] if not past.empty else 0.0

def run_grid_search_optimization(symbols, start_date, end_date, upstox_token, 
                                 param_grid, ltf="3min", max_concurrent_trades=3, 
                                 progress_callback=None, log_func=print):
    """
    Fetches raw price history ONCE per symbol, then evaluates all parameter
    permutations across the exact same dataset to prevent timeouts and RAM crashes.
    """
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())
    warmup_start = start_dt - timedelta(days=15)
    
    # 1. Generate all parameter combinations
    keys, values = zip(*param_grid.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    total_combos = len(combinations)
    log_func(f"⚡ Starting Grid Optimization: Testing {total_combos} unique strategy configurations...")

    # Dictionary to store cached symbol data
    cached_market_data = {}

    # 2. Fetch & Prepare Indicators ONCE
    for symbol in symbols:
        log_func(f"📥 Pre-fetching data and indicators for {symbol}...")
        spot_1m = fetch_upstox_intraday_candles(symbol, warmup_start, end_dt, upstox_token, interval="1minute", log_func=log_func)
        if spot_1m.empty:
            continue

        # Higher Timeframes
        df_1h = spot_1m.set_index('timestamp').resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        df_1h['SMA_20'] = ta.sma(df_1h['close'], length=20)
        df_1h['RSI_14'] = ta.rsi(df_1h['close'], length=14)
        df_1h = df_1h.reset_index()

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

        df_ltf = spot_1m.set_index('timestamp').resample(ltf).agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        df_ltf['EMA_9'] = ta.ema(df_ltf['close'], length=9)
        df_ltf['EMA_50'] = ta.ema(df_ltf['close'], length=50)
        df_ltf['body_abs'] = abs(df_ltf['close'] - df_ltf['open'])
        df_ltf['avg_body_10'] = df_ltf['body_abs'].rolling(10).mean().shift(1)
        df_ltf = df_ltf.reset_index()

        # Release raw 1-minute dataframe to prevent OOM
        del spot_1m
        gc.collect()

        cached_market_data[symbol] = {
            'df_1h': df_1h,
            'df_15m': df_15m,
            'df_ltf': df_ltf
        }

    # Caches for option chains & candle data shared across all iterations
    api_cache, chain_cache = {}, {}
    leaderboard_records = []

    # 3. Loop Through Parameter Permutations
    for combo_idx, params in enumerate(combinations):
        target_decay_pct = params['target_decay_pct']
        sl_appreciation_pct = params['sl_appreciation_pct']
        blocked_days = params['blocked_days']
        require_1h_rsi = params['require_1h_rsi']
        adx_threshold = params['adx_threshold']
        
        combo_name = f"SL{int(sl_appreciation_pct*100)}_TGT{int(target_decay_pct*100)}_ADX{int(adx_threshold)}_{'NoThFr' if blocked_days else 'AllDays'}_1HRSI_{require_1h_rsi}"
        if progress_callback:
            progress_callback(combo_idx + 1, total_combos, f"Evaluating Config {combo_idx+1}/{total_combos}: {combo_name}")

        combo_trades = []

        for symbol, mdata in cached_market_data.items():
            df_1h = mdata['df_1h']
            df_15m = mdata['df_15m']
            df_ltf = mdata['df_ltf']

            # Entry Scan
            entries = []
            for j in range(1, len(df_ltf) - 1):
                c_time = df_ltf.loc[j, 'timestamp']
                if c_time < start_dt or c_time > end_dt: continue

                c_day_name = c_time.strftime('%A')
                if c_day_name in blocked_days: continue
                if symbol == "SENSEX" and (c_time.weekday() == 4 or c_time.time() >= dt.time(13, 0)): continue

                matching_1h = df_1h[df_1h['timestamp'] <= c_time]
                matching_15m = df_15m[df_15m['timestamp'] <= c_time]
                if matching_1h.empty or matching_15m.empty: continue

                c_1h_close, c_1h_sma20 = matching_1h.iloc[-1]['close'], matching_1h.iloc[-1]['SMA_20']
                c_1h_rsi = matching_1h.iloc[-1]['RSI_14']
                curr_15m = matching_15m.iloc[-1]
                ema9_15, ema21_15, c_adx = curr_15m['EMA_9'], curr_15m['EMA_21'], curr_15m['ADX_14']

                if pd.isna(c_adx) or c_adx < adx_threshold: continue

                c_open, c_high, c_low, c_close = df_ltf.loc[j, 'open'], df_ltf.loc[j, 'high'], df_ltf.loc[j, 'low'], df_ltf.loc[j, 'close']
                c_ema9, c_ema50 = df_ltf.loc[j, 'EMA_9'], df_ltf.loc[j, 'EMA_50']

                is_bullish_trend = ema9_15 > ema21_15
                is_bearish_trend = ema9_15 < ema21_15
                if not (is_bullish_trend or is_bearish_trend): continue

                bullish_retracement = is_bullish_trend and (c_low < c_ema9 or c_low < c_ema50) and (c_close > c_ema9 or c_close > c_ema50)
                bearish_retracement = is_bearish_trend and (c_high > c_ema9 or c_high > c_ema50) and (c_close < c_ema9 or c_close < c_ema50)
                if not (bullish_retracement or bearish_retracement): continue

                rsi_ok = (curr_15m['RSI_14'] > curr_15m['RSI_SMA_14']) if bullish_retracement else (curr_15m['RSI_14'] < curr_15m['RSI_SMA_14'])
                h1_ok = (c_1h_close > c_1h_sma20) if bullish_retracement else (c_1h_close < c_1h_sma20)
                h1_rsi_ok = ((c_1h_rsi > 50.0) if bullish_retracement else (c_1h_rsi < 50.0)) if require_1h_rsi else True

                if not (rsi_ok and h1_ok and h1_rsi_ok): continue

                trade_type = 'PE_SPREAD' if bullish_retracement else 'CE_SPREAD'
                entries.append({'time': df_ltf.loc[j+1, 'timestamp'], 'price': df_ltf.loc[j+1, 'open'], 'type': trade_type, 'ltf_idx': j+1})

            # Trade Simulation
            active_exits = []
            for tr in entries:
                entry_time, entry_price, trade_type = tr['time'], tr['price'], tr['type']
                start_ltf_idx = tr['ltf_idx']
                
                active_exits = [ext for ext in active_exits if ext > entry_time]
                if len(active_exits) >= max_concurrent_trades: continue

                trade_date = entry_time.date()
                raw_expiries = get_available_expiries(symbol, trade_date, upstox_token, log_func=lambda x: None)
                valid_expiries = resolve_expiry(symbol, trade_date, raw_expiries, log_func=lambda x: None)
                if not valid_expiries: continue
                
                target_expiry = valid_expiries[0]
                if target_expiry == trade_date and len(valid_expiries) > 1: 
                    target_expiry = valid_expiries[1]

                strat_name = "Bull Put Spread" if trade_type == 'PE_SPREAD' else "Bear Call Spread"
                legs = build_spread_legs(symbol=symbol, entry_price=entry_price, strategy_type=strat_name, 
                                         target_expiry_date=target_expiry, access_token=upstox_token, 
                                         sell_offset=2, buy_offset=4, chain_cache=chain_cache, log_func=lambda x: None)
                if len(legs) != 2: continue
                
                trade_lot_size = legs[0]['lot_size']
                fetch_end = entry_time + timedelta(days=10)
                leg_data = []

                for leg in legs:
                    cache_key = f"{leg['key']}_{entry_time.date()}"
                    if cache_key not in api_cache:
                        df_1m_leg = fetch_upstox_intraday_candles(leg['key'], entry_time - timedelta(days=1), fetch_end, upstox_token, is_key=True, is_expired=leg['is_expired'], log_func=lambda x: None)
                        if not df_1m_leg.empty:
                            api_cache[cache_key] = df_1m_leg.set_index('timestamp').resample(ltf).agg({'close': 'last'}).dropna().reset_index()
                            del df_1m_leg
                            gc.collect()
                    if cache_key in api_cache:
                        leg_data.append({'side': leg['side'], 'df': api_cache[cache_key]})

                if len(leg_data) != 2: continue

                leg1_entry = get_premium_at_time(leg_data[0]['df'], entry_time)
                leg2_entry = get_premium_at_time(leg_data[1]['df'], entry_time)
                initial_net_credit = leg1_entry - leg2_entry
                if initial_net_credit < 15.0: continue

                spread_width = abs(legs[0]['strike'] - legs[1]['strike'])
                capital_employed = spread_width * trade_lot_size

                exit_time = df_ltf.iloc[-1]['timestamp']
                max_hold_time = entry_time + timedelta(days=3)

                for step in range(start_ltf_idx + 1, len(df_ltf)):
                    curr_time = df_ltf.loc[step, 'timestamp']
                    if curr_time > max_hold_time: exit_time = curr_time; break
                    if curr_time.weekday() == 0 and curr_time.time() >= dt.time(15, 27): exit_time = curr_time; break
                    if curr_time > leg_data[0]['df'].iloc[-1]['timestamp']: exit_time = leg_data[0]['df'].iloc[-1]['timestamp']; break

                    curr_spot_close = df_ltf.loc[step, 'close']
                    matching_15m = df_15m[df_15m['timestamp'] <= curr_time]
                    if not matching_15m.empty:
                        m15_row = matching_15m.iloc[-1]
                        if trade_type == 'PE_SPREAD' and curr_spot_close < m15_row['ATR_Trailing_Long']: exit_time = curr_time; break
                        elif trade_type == 'CE_SPREAD' and curr_spot_close > m15_row['ATR_Trailing_Short']: exit_time = curr_time; break

                    l1_curr = get_premium_at_time(leg_data[0]['df'], curr_time)
                    l2_curr = get_premium_at_time(leg_data[1]['df'], curr_time)
                    current_pnl_per_qty = initial_net_credit - (l1_curr - l2_curr)

                    if current_pnl_per_qty >= (target_decay_pct * initial_net_credit): exit_time = curr_time; break
                    elif current_pnl_per_qty <= (-sl_appreciation_pct * initial_net_credit): exit_time = curr_time; break

                l1_final = get_premium_at_time(leg_data[0]['df'], exit_time)
                l2_final = get_premium_at_time(leg_data[1]['df'], exit_time)
                exit_pnl_abs = (initial_net_credit - (l1_final - l2_final)) * trade_lot_size

                combo_trades.append({
                    'Symbol': symbol,
                    'PnL (₹)': exit_pnl_abs,
                    'Exit Time': exit_time
                })
                active_exits.append(exit_time)

        # 4. Compute Metrics for this Combination
        if combo_trades:
            df_res = pd.DataFrame(combo_trades).sort_values('Exit Time').reset_index(drop=True)
            trades_count = len(df_res)
            wins = df_res[df_res['PnL (₹)'] > 0]
            losses = df_res[df_res['PnL (₹)'] <= 0]
            
            win_rate = (len(wins) / trades_count) * 100
            total_pnl = df_res['PnL (₹)'].sum()
            avg_win = wins['PnL (₹)'].mean() if not wins.empty else 0
            avg_loss = losses['PnL (₹)'].mean() if not losses.empty else 0
            rr_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
            
            df_res['Cum_PnL'] = df_res['PnL (₹)'].cumsum()
            df_res['Peak'] = df_res['Cum_PnL'].cummax()
            df_res['Drawdown'] = df_res['Cum_PnL'] - df_res['Peak']
            max_dd = df_res['Drawdown'].min()
            
            calmar_ratio = abs(total_pnl / max_dd) if max_dd != 0 else 0

            leaderboard_records.append({
                'Configuration': combo_name,
                'SL %': f"{int(sl_appreciation_pct*100)}%",
                'Target %': f"{int(target_decay_pct*100)}%",
                'ADX Min': int(adx_threshold),
                'Days Blocked': "Thu, Fri" if blocked_days else "None",
                '1H RSI': require_1h_rsi,
                'Total Trades': trades_count,
                'Win Rate (%)': round(win_rate, 2),
                'Total PnL (₹)': round(total_pnl, 2),
                'Max Drawdown (₹)': round(max_dd, 2),
                'Reward/Risk': round(rr_ratio, 2),
                'Return / DD Ratio': round(calmar_ratio, 2)
            })

    leaderboard_df = pd.DataFrame(leaderboard_records)
    if not leaderboard_df.empty:
        # Rank by Return/Drawdown ratio
        leaderboard_df = leaderboard_df.sort_values(by='Return / DD Ratio', ascending=False).reset_index(drop=True)
        
    return leaderboard_df
