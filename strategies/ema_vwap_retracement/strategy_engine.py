import requests
import pandas as pd
import pytz
import streamlit as st
import urllib.parse
import time
from datetime import datetime, timedelta

UPSTOX_INSTRUMENT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
UPSTOX_HISTORICAL_URL = "https://api.upstox.com/v2/historical-candle/{instrument_key}/{unit}/{to_date}/{from_date}"
UPSTOX_EXPIRED_HISTORICAL_URL = "https://api.upstox.com/v2/expired-instruments/historical-candle/{instrument_key}/{unit}/{to_date}/{from_date}"

@st.cache_data(ttl=3600, show_spinner=False)
def get_instrument_df():
    try:
        df = pd.read_csv(UPSTOX_INSTRUMENT_URL, compression='gzip')
        df = df[df['exchange'].isin(['NSE_EQ', 'NSE_FO', 'NSE_INDEX', 'BSE_INDEX', 'BSE_FO'])]
        df['expiry'] = pd.to_datetime(df['expiry'], errors='coerce')
        return df
    except Exception as e:
        st.error(f"❌ Failed to download Upstox Instrument Master: {e}")
        return pd.DataFrame()

def get_upstox_key(symbol):
    sym_upper = symbol.upper()
    index_keys = {
        'NIFTY': 'NSE_INDEX|Nifty 50',
        'BANKNIFTY': 'NSE_INDEX|Nifty Bank',
        'FINNIFTY': 'NSE_INDEX|Nifty Fin Service',
        'SENSEX': 'BSE_INDEX|SENSEX',
        'BANKEX': 'BSE_INDEX|BANKEX',
        'MIDCPNIFTY': 'NSE_INDEX|NIFTY MID SELECT'
    }
    if sym_upper in index_keys: return index_keys[sym_upper]
        
    df = get_instrument_df()
    if df.empty: return None
    eq_rows = df[(df['exchange'].isin(['NSE_EQ', 'BSE_EQ'])) & (df['tradingsymbol'].str.upper() == sym_upper)]
    if not eq_rows.empty: return eq_rows.iloc[0]['instrument_key']
    return None

def get_nfo_lot_size(symbol):
    df = get_instrument_df()
    if df.empty: return 1
    
    symbol_upper = symbol.upper()
    if symbol_upper == 'NIFTY': symbol_upper = 'NIFTY 50'
    elif symbol_upper == 'BANKNIFTY': symbol_upper = 'NIFTY BANK'
    elif symbol_upper == 'SENSEX': symbol_upper = 'BSX'
    elif symbol_upper == 'BANKEX': symbol_upper = 'BKX'
    
    valid_exchanges = ['NSE_FO', 'BSE_FO']
    if 'underlying_symbol' in df.columns:
        derivatives = df[(df['underlying_symbol'] == symbol_upper) & (df['exchange'].isin(valid_exchanges))]
    else:
        derivatives = df[(df['name'] == symbol_upper) & (df['exchange'].isin(valid_exchanges))]
        
    if derivatives.empty:
        derivatives = df[(df['tradingsymbol'].str.startswith(symbol_upper)) & (df['exchange'].isin(valid_exchanges))]
        
    if not derivatives.empty: return int(derivatives.iloc[0]['lot_size'])
    return 1 

def fetch_upstox_intraday_candles(symbol_or_key, start_dt, end_dt, access_token, interval="1minute", is_key=False, is_expired=False, log_func=print):
    if not is_key:
        instrument_key = get_upstox_key(symbol_or_key)
        if not instrument_key: return pd.DataFrame()
    else:
        instrument_key = symbol_or_key

    safe_instrument_key = urllib.parse.quote(instrument_key)
    current_date = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)
    start_dt = pd.to_datetime(start_dt).tz_localize(None)
    end_dt = pd.to_datetime(end_dt).tz_localize(None)

    if end_dt > current_date: end_dt = current_date
    if start_dt > current_date: return pd.DataFrame()

    all_candles = []
    chunk_start = start_dt
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    base_url = UPSTOX_EXPIRED_HISTORICAL_URL if is_expired else UPSTOX_HISTORICAL_URL

    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(days=20), end_dt)
        url = base_url.format(
            instrument_key=safe_instrument_key, 
            unit=interval,
            to_date=chunk_end.strftime("%Y-%m-%d"), 
            from_date=chunk_start.strftime("%Y-%m-%d")
        )
        
        try:
            time.sleep(0.3) 
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                candles = response.json().get("data", {}).get("candles", [])
                if candles: all_candles.extend(candles)
        except Exception: pass
        chunk_start = chunk_end + timedelta(days=1)

    if not all_candles: return pd.DataFrame()
        
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert(pytz.timezone("Asia/Kolkata")).dt.tz_localize(None)
    df = df.drop_duplicates(subset=['timestamp']).sort_values("timestamp").reset_index(drop=True)
    return df

def get_option_legs(symbol, entry_time, entry_price, strategy, access_token, sell_offset=2, buy_offset=4, chain_cache=None, log_func=print):
    df_inst = get_instrument_df()
    if df_inst.empty: return []
    
    entry_date = pd.to_datetime(entry_time).date()
    current_date = pd.Timestamp.now(tz="Asia/Kolkata").date()
    cache_key = f"{symbol}_{entry_date}_{strategy}_{sell_offset}_{buy_offset}"
    
    if chain_cache is not None and cache_key in chain_cache:
        cached_data = chain_cache[cache_key]
        chain_df = cached_data['df']
        is_expired = cached_data['is_expired']
    else:
        eq_key = get_upstox_key(symbol)
        if not eq_key: return []
            
        safe_eq_key = urllib.parse.quote(eq_key)
        headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
        
        spot_sym = symbol.upper()
        if spot_sym == 'NIFTY': spot_sym = 'NIFTY 50'
        elif spot_sym == 'BANKNIFTY': spot_sym = 'NIFTY BANK'
        elif spot_sym == 'FINNIFTY': spot_sym = 'NIFTY FIN SERVICE'
        elif spot_sym == 'SENSEX': spot_sym = 'BSX'
        elif spot_sym == 'BANKEX': spot_sym = 'BKX'
        
        valid_fo_exchanges = ['NSE_FO', 'BSE_FO']
        
        if 'underlying_symbol' in df_inst.columns:
            opts_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & (df_inst['underlying_symbol'] == spot_sym)]
        else:
            opts_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & ((df_inst['name'] == spot_sym) | (df_inst['tradingsymbol'].str.startswith(spot_sym)))]
            
        active_expiries = pd.to_datetime(opts_active['expiry'], errors='coerce').dt.date.dropna().unique().tolist() if not opts_active.empty else []
            
        expired_expiries = []
        try:
            time.sleep(0.3)
            exp_url = f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={safe_eq_key}"
            res = requests.get(exp_url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json().get('data', [])
                expired_expiries = [pd.to_datetime(d).date() for d in data]
        except Exception: pass 

        all_expiries = sorted(list(set(active_expiries + expired_expiries)))
        future_expiries = [d for d in all_expiries if d >= entry_date]
        if not future_expiries: return []
            
        closest_expiry = future_expiries[0]
        is_expired = closest_expiry < current_date
        
        def fetch_expired_chain():
            try:
                time.sleep(0.3)
                opt_url = f"https://api.upstox.com/v2/expired-instruments/option/contract?instrument_key={safe_eq_key}&expiry_date={closest_expiry.strftime('%Y-%m-%d')}"
                res = requests.get(opt_url, headers=headers, timeout=10)
                if res.status_code == 200:
                    contracts = res.json().get('data', [])
                    df = pd.DataFrame(contracts)
                    if not df.empty:
                        if 'strike_price' in df.columns: df.rename(columns={'strike_price': 'strike'}, inplace=True)
                        if 'trading_symbol' in df.columns: df.rename(columns={'trading_symbol': 'tradingsymbol'}, inplace=True)
                        return df
            except Exception: pass
            return pd.DataFrame()

        chain_df = pd.DataFrame()
        if is_expired:
            chain_df = fetch_expired_chain()
        else:
            if 'underlying_symbol' in df_inst.columns:
                chain_df = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & (df_inst['underlying_symbol'] == spot_sym) & (pd.to_datetime(df_inst['expiry']).dt.date == closest_expiry)].copy()
            else:
                chain_df = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & (df_inst['tradingsymbol'].str.startswith(spot_sym)) & (pd.to_datetime(df_inst['expiry']).dt.date == closest_expiry)].copy()
            
            # 🚨 THE FIX: Weekend Limbo Fallback
            # If the engine thinks it's active, but the chain_df returns empty (meaning Upstox purged it from complete.csv.gz early),
            # forcefully query the expired API as a fallback.
            if chain_df.empty:
                chain_df = fetch_expired_chain()
                if not chain_df.empty:
                    is_expired = True # Force it to act like an expired contract for historical data fetching

        if chain_cache is not None:
            chain_cache[cache_key] = {'df': chain_df, 'is_expired': is_expired, 'closest_expiry': closest_expiry}

    if chain_df.empty: return []

    chain_df['strike'] = pd.to_numeric(chain_df['strike'], errors='coerce')
    chain_df = chain_df.dropna(subset=['strike'])
    unique_strikes = sorted(chain_df['strike'].unique())
    if not unique_strikes: return []
        
    closest_idx = min(range(len(unique_strikes)), key=lambda i: abs(unique_strikes[i] - entry_price))
    
    try:
        if "Bull Put" in strategy:
            strike_sell = unique_strikes[max(0, closest_idx - sell_offset)]
            strike_buy = unique_strikes[max(0, closest_idx - buy_offset)]
        else: 
            strike_sell = unique_strikes[min(len(unique_strikes)-1, closest_idx + sell_offset)]
            strike_buy = unique_strikes[min(len(unique_strikes)-1, closest_idx + buy_offset)]
    except Exception:
        return [] 

    def get_key(s, o_type):
        target_strike = float(s)
        col_type = 'option_type' if 'option_type' in chain_df.columns else 'instrument_type'
        leg = chain_df[
            (abs(chain_df['strike'] - target_strike) < 0.05) & 
            ((chain_df[col_type] == o_type) | (chain_df['tradingsymbol'].astype(str).str.endswith(o_type)))
        ]
        return leg.iloc[0]['instrument_key'] if not leg.empty else None

    legs = []
    if "Bull Put" in strategy:
        legs.append({'type': f'OTM{sell_offset} PE (Sell)', 'strike': strike_sell, 'key': get_key(strike_sell, 'PE'), 'side': -1, 'is_expired': is_expired})
        legs.append({'type': f'OTM{buy_offset} PE (Buy)', 'strike': strike_buy, 'key': get_key(strike_buy, 'PE'), 'side': 1, 'is_expired': is_expired})
    elif "Bear Call" in strategy:
        legs.append({'type': f'OTM{sell_offset} CE (Sell)', 'strike': strike_sell, 'key': get_key(strike_sell, 'CE'), 'side': -1, 'is_expired': is_expired})
        legs.append({'type': f'OTM{buy_offset} CE (Buy)', 'strike': strike_buy, 'key': get_key(strike_buy, 'CE'), 'side': 1, 'is_expired': is_expired})
        
    return [l for l in legs if l['key'] is not None]            if matching_15m.empty: continue
            curr_15m = matching_15m.iloc[-1]
            
            ema9_15 = curr_15m['EMA_9']
            ema21_15 = curr_15m['EMA_21']
            
            c_open = df_3m.loc[j, 'open']
            c_low = df_3m.loc[j, 'low']
            c_high = df_3m.loc[j, 'high']
            c_close = df_3m.loc[j, 'close']
            c_ema9 = df_3m.loc[j, 'EMA_9']
            c_vwap = df_3m.loc[j, 'VWAP']
            c_vol = df_3m.loc[j, 'volume']
            p_vol = df_3m.loc[j-1, 'volume']
            
            # --- Bullish Conditions (PE Spread) ---
            is_bullish_trend = ema9_15 > ema21_15
            bullish_retracement = (c_low < c_ema9 or c_low < c_vwap) and (c_close > c_ema9 or c_close > c_vwap)
            
            bull_color_ok = (c_close > c_open) if require_color else True
            bull_vol_ok = (c_vol > p_vol) if require_volume else True
            
            # --- Bearish Conditions (CE Spread) ---
            is_bearish_trend = ema9_15 < ema21_15
            bearish_retracement = (c_high > c_ema9 or c_high > c_vwap) and (c_close < c_ema9 or c_close < c_vwap)
            
            bear_color_ok = (c_close < c_open) if require_color else True
            bear_vol_ok = (c_vol > p_vol) if require_volume else True
            
            if is_bullish_trend and bullish_retracement and bull_color_ok and bull_vol_ok:
                entries.append({
                    'time': df_3m.loc[j+1, 'timestamp'],
                    'price': df_3m.loc[j+1, 'open'],
                    'type': 'PE_SPREAD',
                    '3m_idx': j+1
                })
            elif is_bearish_trend and bearish_retracement and bear_color_ok and bear_vol_ok:
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
