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
    if sym_upper in index_keys: 
        return index_keys[sym_upper]
        
    df = get_instrument_df()
    if df.empty: 
        return None
    eq_rows = df[(df['exchange'].isin(['NSE_EQ', 'BSE_EQ'])) & (df['tradingsymbol'].str.upper() == sym_upper)]
    if not eq_rows.empty: 
        return eq_rows.iloc[0]['instrument_key']
    return None

def get_nfo_lot_size(symbol):
    df = get_instrument_df()
    if df.empty: 
        return 1
    symbol_upper = symbol.upper()
    if symbol_upper == 'NIFTY': symbol_upper = 'NIFTY 50'
    elif symbol_upper == 'BANKNIFTY': symbol_upper = 'NIFTY BANK'
    elif symbol_upper == 'SENSEX': symbol_upper = 'BSX'
    elif symbol_upper == 'BANKEX': symbol_upper = 'BKX'
    
    valid_exchanges = ['NSE_FO', 'BSE_FO', 'MCX_FO']
    
    # Case-insensitive robust matching
    if 'underlying_symbol' in df.columns:
        derivatives = df[(df['underlying_symbol'].astype(str).str.upper() == symbol_upper) & (df['exchange'].isin(valid_exchanges))]
    else:
        derivatives = df[(df['name'].astype(str).str.upper() == symbol_upper) & (df['exchange'].isin(valid_exchanges))]
        
    if derivatives.empty:
        derivatives = df[(df['tradingsymbol'].astype(str).str.upper().str.startswith(symbol_upper)) & (df['exchange'].isin(valid_exchanges))]
        
    if not derivatives.empty: 
        return int(derivatives.iloc[0]['lot_size'])
    return 1 

def fetch_upstox_intraday_candles(symbol_or_key, start_dt, end_dt, access_token, interval="1minute", is_key=False, is_expired=False, log_func=print):
    if not is_key:
        instrument_key = get_upstox_key(symbol_or_key)
        if not instrument_key: 
            return pd.DataFrame()
    else:
        instrument_key = symbol_or_key

    safe_instrument_key = urllib.parse.quote(instrument_key)
    current_date = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)
    start_dt = pd.to_datetime(start_dt).tz_localize(None)
    end_dt = pd.to_datetime(end_dt).tz_localize(None)

    if end_dt > current_date: 
        end_dt = current_date
    if start_dt > current_date: 
        return pd.DataFrame()

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
                if candles: 
                    all_candles.extend(candles)
        except Exception: 
            pass
        chunk_start = chunk_end + timedelta(days=1)

    if not all_candles: 
        return pd.DataFrame()
        
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert(pytz.timezone("Asia/Kolkata")).dt.tz_localize(None)
    df = df.drop_duplicates(subset=['timestamp']).sort_values("timestamp").reset_index(drop=True)
    return df

def fetch_continuous_futures_candles(symbol, start_dt, end_dt, access_token, interval="1minute", eval_date=None, log_func=print):
    df_inst = get_instrument_df()
    fut_name = symbol.upper()
    current_date = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).date()
    target_eval = pd.to_datetime(eval_date).tz_localize(None).date() if eval_date else pd.to_datetime(start_dt).tz_localize(None).date()
    
    eq_key = get_upstox_key(symbol)
    safe_eq_key = urllib.parse.quote(eq_key) if eq_key else ""
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    
    active_expiries = []
    valid_fo_exchanges = ['NSE_FO', 'BSE_FO', 'MCX_FO']
    
    if 'instrument_type' in df_inst.columns:
        futures_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & 
                                 (df_inst['name'].astype(str).str.upper() == fut_name) & 
                                 (df_inst['instrument_type'].isin(['FUTIDX', 'FUTCOM', 'FUTSTK']))]
    else:
        futures_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & 
                                 (df_inst['name'].astype(str).str.upper() == fut_name) &
                                 (df_inst['tradingsymbol'].str.contains('FUT', na=False))]
                                 
    if futures_active.empty:
        futures_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & 
                                 (df_inst['tradingsymbol'].astype(str).str.upper().str.startswith(fut_name)) &
                                 (df_inst['tradingsymbol'].str.contains('FUT', na=False))]

    if not futures_active.empty:
        active_expiries = pd.to_datetime(futures_active['expiry'], errors='coerce').dt.date.dropna().unique().tolist()

    expired_expiries = []
    if target_eval < current_date and safe_eq_key:
        try:
            time.sleep(0.3)
            exp_url = f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={safe_eq_key}"
            res = requests.get(exp_url, headers=headers, timeout=10)
            if res.status_code == 200:
                expired_expiries = [pd.to_datetime(d).date() for d in res.json().get('data', [])]
        except Exception: 
            pass 

    all_expiries = sorted(list(set(active_expiries + expired_expiries)))
    future_expiries = [d for d in all_expiries if d >= target_eval]
    
    if not future_expiries:
        df_spot = fetch_upstox_intraday_candles(symbol, start_dt, end_dt, access_token, interval, False, False, log_func)
        df_spot.attrs['contract_name'] = f"{symbol} (Spot Fallback)"
        return df_spot
        
    front_month_key = None
    front_month_sym = None
    is_expired = False
    
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
                        break 
            except Exception: 
                pass
        else:
            if not futures_active.empty:
                fallback_contracts = futures_active[pd.to_datetime(futures_active['expiry'], errors='coerce').dt.date == exp_date]
                if not fallback_contracts.empty:
                    front_month_key = fallback_contracts.iloc[0]['instrument_key']
                    front_month_sym = fallback_contracts.iloc[0]['tradingsymbol']
                    is_expired = False
                    break
                    
    if not front_month_key and not futures_active.empty:
        fallback_contracts = futures_active[pd.to_datetime(futures_active['expiry'], errors='coerce').dt.date >= target_eval]
        if fallback_contracts.empty: 
            fallback_contracts = futures_active
        fallback_contracts = fallback_contracts.sort_values(by='expiry')
        front_month_key = fallback_contracts.iloc[0]['instrument_key']
        front_month_sym = fallback_contracts.iloc[0]['tradingsymbol']
        is_expired = False

    if not front_month_key:
        df_spot = fetch_upstox_intraday_candles(symbol, start_dt, end_dt, access_token, interval, False, False, log_func)
        df_spot.attrs['contract_name'] = f"{symbol} (Spot Fallback)"
        return df_spot

    df = fetch_upstox_intraday_candles(front_month_key, start_dt, end_dt, access_token, interval, is_key=True, is_expired=is_expired, log_func=log_func)
    
    if not df.empty:
        df.attrs['contract_name'] = front_month_sym or f"{symbol} FUT"
    return df

def get_available_expiries(symbol, target_date, access_token, log_func=print):
    """
    Diagnostic Telemetry Probe injected to catch the Upstox API Archiving Limbo bug.
    """
    log_func(f"🔍 [DEBUG] Starting expiry resolution for {symbol} on Trade Date: {target_date}")
    df_inst = get_instrument_df()
    
    if df_inst.empty: 
        log_func("❌ [DEBUG] complete.csv.gz is empty or failed to load!")
        return []
    
    eq_key = get_upstox_key(symbol)
    safe_eq_key = urllib.parse.quote(eq_key) if eq_key else ""
    log_func(f"🔍 [DEBUG] Upstox Key resolved: {eq_key}")
    
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    
    spot_sym = symbol.upper()
    if spot_sym == 'NIFTY': spot_sym = 'NIFTY 50'
    elif spot_sym == 'BANKNIFTY': spot_sym = 'NIFTY BANK'
    elif spot_sym == 'FINNIFTY': spot_sym = 'NIFTY FIN SERVICE'
    elif spot_sym == 'SENSEX': spot_sym = 'BSX'
    elif spot_sym == 'BANKEX': spot_sym = 'BKX'
    
    valid_fo_exchanges = ['NSE_FO', 'BSE_FO', 'MCX_FO']
    
    log_func(f"🔍 [DEBUG] Searching CSV for underlying: {spot_sym}")
    
    # Case-insensitive string matching
    if 'underlying_symbol' in df_inst.columns:
        opts_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & (df_inst['underlying_symbol'].astype(str).str.upper() == spot_sym)]
    else:
        opts_active = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & ((df_inst['name'].astype(str).str.upper() == spot_sym) | (df_inst['tradingsymbol'].astype(str).str.upper().str.startswith(spot_sym)))]
        
    log_func(f"🔍 [DEBUG] Found {len(opts_active)} active derivative rows in complete.csv.gz")
    
    active_expiries = pd.to_datetime(opts_active['expiry'], errors='coerce').dt.date.dropna().unique().tolist() if not opts_active.empty else []
    log_func(f"🔍 [DEBUG] Active Expiries extracted from CSV: {sorted(active_expiries)[:5]} ... (showing first 5)")
        
    expired_expiries = []
    if safe_eq_key:
        exp_url = f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={safe_eq_key}"
        log_func(f"🔍 [DEBUG] Calling Expired API: {exp_url}")
        try:
            time.sleep(0.3)
            res = requests.get(exp_url, headers=headers, timeout=10)
            log_func(f"🔍 [DEBUG] Expired API Status Code: {res.status_code}")
            
            if res.status_code == 200:
                raw_data = res.json().get('data', [])
                expired_expiries = [pd.to_datetime(d).date() for d in raw_data]
                log_func(f"🔍 [DEBUG] Expired Expiries parsed: {len(expired_expiries)} dates found.")
                if expired_expiries:
                    log_func(f"🔍 [DEBUG] Most recent expired dates: {sorted(expired_expiries)[-5:]}")
            else:
                log_func(f"⚠️ [DEBUG] Expired API Error Response: {res.text}")
        except Exception as e: 
            log_func(f"⚠️ [DEBUG] Expired API Request Exception: {e}")

    all_expiries = sorted(list(set(active_expiries + expired_expiries)))
    log_func(f"🔍 [DEBUG] Total Unique Expiries (Active + Expired): {len(all_expiries)}")
    
    future_expiries = [d for d in all_expiries if d >= target_date]
    log_func(f"🔍 [DEBUG] Final Filtered Expiries (>= {target_date}): {future_expiries[:5]}")
    
    return future_expiries

def get_target_option_chain(symbol, target_expiry, access_token, chain_cache=None, log_func=print):
    """
    Pure Data Function: Fetches raw option chain for an EXPLICIT target expiry date.
    Zero Strategy Logic.
    """
    df_inst = get_instrument_df()
    if df_inst.empty: 
        return pd.DataFrame(), False
    
    current_date = pd.Timestamp.now(tz="Asia/Kolkata").date()
    cache_key = f"{symbol}_{target_expiry}"
    
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
    is_expired = target_expiry < current_date
    
    def fetch_expired_chain(target_d):
        if not safe_eq_key: 
            return pd.DataFrame()
        try:
            time.sleep(0.3)
            opt_url = f"https://api.upstox.com/v2/expired-instruments/option/contract?instrument_key={safe_eq_key}&expiry_date={target_d.strftime('%Y-%m-%d')}"
            res = requests.get(opt_url, headers=headers, timeout=10)
            if res.status_code == 200:
                contracts = res.json().get('data', [])
                df = pd.DataFrame(contracts)
                if not df.empty:
                    if 'strike_price' in df.columns: df.rename(columns={'strike_price': 'strike'}, inplace=True)
                    if 'trading_symbol' in df.columns: df.rename(columns={'trading_symbol': 'tradingsymbol'}, inplace=True)
                    return df
        except Exception: 
            pass
        return pd.DataFrame()

    chain_df = pd.DataFrame()
    if is_expired:
        chain_df = fetch_expired_chain(target_expiry)
    else:
        if 'underlying_symbol' in df_inst.columns:
            chain_df = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & (df_inst['underlying_symbol'].astype(str).str.upper() == spot_sym) & (pd.to_datetime(df_inst['expiry']).dt.date == target_expiry)].copy()
        else:
            chain_df = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & (df_inst['tradingsymbol'].astype(str).str.upper().str.startswith(spot_sym)) & (pd.to_datetime(df_inst['expiry']).dt.date == target_expiry)].copy()
        
        # Data layer fallback: If missing from active cache, try historical anyway
        if chain_df.empty:
            chain_df = fetch_expired_chain(target_expiry)
            if not chain_df.empty:
                is_expired = True

    if not chain_df.empty:
        chain_df['strike'] = pd.to_numeric(chain_df['strike'], errors='coerce')
        chain_df = chain_df.dropna(subset=['strike'])

    if chain_cache is not None:
        chain_cache[cache_key] = {'df': chain_df, 'is_expired': is_expired}

    return chain_df, is_expired
