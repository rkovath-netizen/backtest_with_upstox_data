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
        df = df[df['exchange'].isin(['NSE_EQ', 'NSE_FO', 'NSE_INDEX', 'BSE_INDEX', 'BSE_FO', 'MCX_FO'])]
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
    
    valid_exchanges = ['NSE_FO', 'BSE_FO', 'MCX_FO']
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

def fetch_continuous_futures_candles(symbol, start_dt, end_dt, access_token, interval="1minute", log_func=print):
    df_inst = get_instrument_df()
    fut_name = symbol.upper()
    current_date = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).date()
    eval_date = pd.to_datetime(start_dt).tz_localize(None).date()
    
    eq_key = get_upstox_key(symbol)
    safe_eq_key = urllib.parse.quote(eq_key) if eq_key else ""
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    
    active_expiries = []
    valid_fo_exchanges = ['NSE_FO', 'BSE_FO', 'MCX_FO']
    
    if 'instrument_type' in df_inst.columns:
        futures_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & 
                                 (df_inst['name'] == fut_name) & 
                                 (df_inst['instrument_type'].isin(['FUTIDX', 'FUTCOM', 'FUTSTK']))]
    else:
        futures_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & 
                                 (df_inst['name'] == fut_name) &
                                 (df_inst['tradingsymbol'].str.contains('FUT', na=False))]
                                 
    if futures_active.empty:
        futures_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & 
                                 (df_inst['tradingsymbol'].str.startswith(fut_name)) &
                                 (df_inst['tradingsymbol'].str.contains('FUT', na=False))]

    if not futures_active.empty:
        active_expiries = pd.to_datetime(futures_active['expiry'], errors='coerce').dt.date.dropna().unique().tolist()

    expired_expiries = []
    if eval_date < current_date and safe_eq_key:
        try:
            time.sleep(0.3)
            # This returns ALL expiries (Weekly Options + Monthly Futures)
            exp_url = f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={safe_eq_key}"
            res = requests.get(exp_url, headers=headers, timeout=10)
            if res.status_code == 200:
                expired_expiries = [pd.to_datetime(d).date() for d in res.json().get('data', [])]
        except Exception: pass 

    all_expiries = sorted(list(set(active_expiries + expired_expiries)))
    future_expiries = [d for d in all_expiries if d >= eval_date]
    
    if not future_expiries:
        log_func(f"⚠️ [DATA HYGIENE] No Upstox API expiries found for {symbol} after {eval_date}. Falling back to Spot.")
        df_spot = fetch_upstox_intraday_candles(symbol, start_dt, end_dt, access_token, interval, False, False, log_func)
        df_spot.attrs['contract_name'] = f"{symbol} (Spot Fallback)"
        return df_spot
        
    front_month_key = None
    front_month_sym = None
    is_expired = False
    
    # 🚨 BUG FIX: Loop through the dates until we find one that actually has a FUTURES contract!
    # This ignores weekly options expiries automatically.
    for exp_date in future_expiries:
        is_expired = exp_date < current_date
        
        if is_expired and safe_eq_key:
            try:
                time.sleep(0.3)
                opt_url = f"https://api.upstox.com/v2/expired-instruments/future/contract?instrument_key={safe_eq_key}&expiry_date={exp_date.strftime('%Y-%m-%d')}"
                res = requests.get(opt_url, headers=headers, timeout=10)
                if res.status_code == 200:
                    contracts = res.json().get('data', [])
                    if contracts:
                        front_month_key = contracts[0].get('instrument_key')
                        front_month_sym = contracts[0].get('trading_symbol')
                        break # Found the monthly future! Stop looking.
            except Exception: pass
        else:
            # Check live active contracts
            if not futures_active.empty:
                fallback_contracts = futures_active[pd.to_datetime(futures_active['expiry'], errors='coerce').dt.date == exp_date]
                if not fallback_contracts.empty:
                    front_month_key = fallback_contracts.iloc[0]['instrument_key']
                    front_month_sym = fallback_contracts.iloc[0]['tradingsymbol']
                    is_expired = False
                    break
                    
    # Ultimate fallback if everything fails
    if not front_month_key and not futures_active.empty:
        fallback_contracts = futures_active[pd.to_datetime(futures_active['expiry'], errors='coerce').dt.date >= eval_date]
        if fallback_contracts.empty: fallback_contracts = futures_active
        fallback_contracts = fallback_contracts.sort_values(by='expiry')
        front_month_key = fallback_contracts.iloc[0]['instrument_key']
        front_month_sym = fallback_contracts.iloc[0]['tradingsymbol']
        is_expired = False

    if not front_month_key:
        df_spot = fetch_upstox_intraday_candles(symbol, start_dt, end_dt, access_token, interval, False, False, log_func)
        df_spot.attrs['contract_name'] = f"{symbol} (Spot Fallback)"
        return df_spot

    log_func(f"🛡️ [API CONFIRMED] Target Date: {eval_date} -> Resolved Future: {front_month_sym}")
    df = fetch_upstox_intraday_candles(front_month_key, start_dt, end_dt, access_token, interval, is_key=True, is_expired=is_expired, log_func=log_func)
    
    if not df.empty:
        df.attrs['contract_name'] = front_month_sym or f"{symbol} FUT"
    return df

def get_target_option_chain(symbol, target_date, access_token, chain_cache=None, log_func=print):
    df_inst = get_instrument_df()
    if df_inst.empty: return pd.DataFrame(), False
    
    current_date = pd.Timestamp.now(tz="Asia/Kolkata").date()
    cache_key = f"{symbol}_{target_date}"
    
    if chain_cache is not None and cache_key in chain_cache:
        return chain_cache[cache_key]['df'], chain_cache[cache_key]['is_expired']

    eq_key = get_upstox_key(symbol)
    safe_eq_key = urllib.parse.quote(eq_key) if eq_key else ""
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    
    spot_sym = symbol.upper()
    if spot_sym == 'NIFTY': spot_sym = 'NIFTY 50'
    elif spot_sym == 'BANKNIFTY': spot_sym = 'NIFTY BANK'
    elif spot_sym == 'FINNIFTY': spot_sym = 'NIFTY FIN SERVICE'
    elif spot_sym == 'SENSEX': spot_sym = 'BSX'
    elif spot_sym == 'BANKEX': spot_sym = 'BKX'
    
    valid_fo_exchanges = ['NSE_FO', 'BSE_FO', 'MCX_FO']
    if 'underlying_symbol' in df_inst.columns:
        opts_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & (df_inst['underlying_symbol'] == spot_sym)]
    else:
        opts_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & ((df_inst['name'] == spot_sym) | (df_inst['tradingsymbol'].str.startswith(spot_sym)))]
        
    active_expiries = pd.to_datetime(opts_active['expiry'], errors='coerce').dt.date.dropna().unique().tolist() if not opts_active.empty else []
        
    expired_expiries = []
    if safe_eq_key:
        try:
            time.sleep(0.3)
            exp_url = f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={safe_eq_key}"
            res = requests.get(exp_url, headers=headers, timeout=10)
            if res.status_code == 200:
                expired_expiries = [pd.to_datetime(d).date() for d in res.json().get('data', [])]
        except Exception: pass 

    all_expiries = sorted(list(set(active_expiries + expired_expiries)))
    future_expiries = [d for d in all_expiries if d >= target_date]
    
    if not future_expiries: 
        log_func(f"⚠️ [DATA HYGIENE] No valid options expiries found for {symbol} after {target_date}.")
        return pd.DataFrame(), False
        
    # Options strictly use the closest expiry (including weeklies)
    closest_expiry = future_expiries[0]
    is_expired = closest_expiry < current_date
    
    log_func(f"🛡️ [API CONFIRMED] Target Date: {target_date} -> Resolved Option Expiry: {closest_expiry}")
    
    def fetch_expired_chain():
        if not safe_eq_key: return pd.DataFrame()
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
        
        if chain_df.empty:
            chain_df = fetch_expired_chain()
            if not chain_df.empty: is_expired = True

    if not chain_df.empty:
        chain_df['strike'] = pd.to_numeric(chain_df['strike'], errors='coerce')
        chain_df = chain_df.dropna(subset=['strike'])

    if chain_cache is not None:
        chain_cache[cache_key] = {'df': chain_df, 'is_expired': is_expired}

    return chain_df, is_expired

def get_option_legs(symbol, entry_time, entry_price, strategy, access_token, sell_offset=2, buy_offset=4, chain_cache=None, log_func=print):
    try:
        from common.options_builder import build_spread_legs
        return build_spread_legs(symbol, entry_time, entry_price, strategy, access_token, sell_offset, buy_offset, chain_cache, log_func)
    except ImportError:
        return []
