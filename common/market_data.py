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
        'NIFTY': 'NSE_INDEX|Nifty 50', 'BANKNIFTY': 'NSE_INDEX|Nifty Bank',
        'FINNIFTY': 'NSE_INDEX|Nifty Fin Service', 'SENSEX': 'BSE_INDEX|SENSEX',
        'BANKEX': 'BSE_INDEX|BANKEX', 'MIDCPNIFTY': 'NSE_INDEX|NIFTY MID SELECT'
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
    sym_upper = symbol.upper()
    if sym_upper == 'NIFTY': sym_upper = 'NIFTY 50'
    elif sym_upper == 'BANKNIFTY': sym_upper = 'NIFTY BANK'
    elif sym_upper == 'SENSEX': sym_upper = 'BSX'
    elif sym_upper == 'BANKEX': sym_upper = 'BKX'
    
    valid_exchanges = ['NSE_FO', 'BSE_FO', 'MCX_FO']
    if 'underlying_symbol' in df.columns:
        derivatives = df[(df['underlying_symbol'].astype(str).str.upper() == sym_upper) & (df['exchange'].isin(valid_exchanges))]
    else:
        derivatives = df[(df['name'].astype(str).str.upper() == sym_upper) & (df['exchange'].isin(valid_exchanges))]
        
    if derivatives.empty:
        derivatives = df[(df['tradingsymbol'].astype(str).str.upper().str.startswith(sym_upper)) & (df['exchange'].isin(valid_exchanges))]
        
    if not derivatives.empty: return int(derivatives.iloc[0]['lot_size'])
    return 1 

def fetch_upstox_intraday_candles(symbol_or_key, start_dt, end_dt, access_token, interval="1minute", is_key=False, is_expired=False, log_func=print):
    if not is_key:
        instrument_key = get_upstox_key(symbol_or_key)
        if not instrument_key: return pd.DataFrame()
    else:
        instrument_key = symbol_or_key

    safe_key = urllib.parse.quote(instrument_key)
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
        url = base_url.format(instrument_key=safe_key, unit=interval, to_date=chunk_end.strftime("%Y-%m-%d"), from_date=chunk_start.strftime("%Y-%m-%d"))
        try:
            time.sleep(0.3) 
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                candles = res.json().get("data", {}).get("candles", [])
                if candles: all_candles.extend(candles)
        except Exception: pass
        chunk_start = chunk_end + timedelta(days=1)

    if not all_candles: return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert(pytz.timezone("Asia/Kolkata")).dt.tz_localize(None)
    return df.drop_duplicates(subset=['timestamp']).sort_values("timestamp").reset_index(drop=True)

def get_available_expiries(symbol, target_date, access_token, log_func=print):
    """Pure Data Layer: Only fetches what exists in CSV and API. No math logic."""
    df_inst = get_instrument_df()
    if df_inst.empty: return []
    
    eq_key = get_upstox_key(symbol)
    safe_key = urllib.parse.quote(eq_key) if eq_key else ""
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    
    spot_sym = symbol.upper()
    if spot_sym == 'NIFTY': spot_sym = 'NIFTY 50'
    elif spot_sym == 'BANKNIFTY': spot_sym = 'NIFTY BANK'
    elif spot_sym == 'SENSEX': spot_sym = 'BSX'
    
    valid_ex = ['NSE_FO', 'BSE_FO', 'MCX_FO']
    
    if 'underlying_symbol' in df_inst.columns:
        opts_active = df_inst[(df_inst['exchange'].isin(valid_ex)) & (df_inst['underlying_symbol'].astype(str).str.upper() == spot_sym)]
    else:
        opts_active = df_inst[(df_inst['exchange'].isin(valid_ex)) & ((df_inst['name'].astype(str).str.upper() == spot_sym) | (df_inst['tradingsymbol'].astype(str).str.upper().str.startswith(spot_sym)))]
        
    active_expiries = pd.to_datetime(opts_active['expiry'], errors='coerce').dt.date.dropna().unique().tolist() if not opts_active.empty else []
        
    expired_expiries = []
    if safe_key:
        try:
            time.sleep(0.3)
            res = requests.get(f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={safe_key}", headers=headers, timeout=10)
            if res.status_code == 200:
                expired_expiries = [pd.to_datetime(d).date() for d in res.json().get('data', [])]
        except Exception: pass 

    all_expiries = sorted(list(set(active_expiries + expired_expiries)))
    return [d for d in all_expiries if d >= target_date]

def get_target_option_chain(symbol, target_expiry, access_token, chain_cache=None, log_func=print):
    """Pure Data Layer: Fetches the raw chain for a specific date."""
    df_inst = get_instrument_df()
    if df_inst.empty: return pd.DataFrame(), False
    
    current_date = pd.Timestamp.now(tz="Asia/Kolkata").date()
    cache_key = f"{symbol}_{target_expiry}"
    if chain_cache is not None and cache_key in chain_cache:
        return chain_cache[cache_key]['df'], chain_cache[cache_key]['is_expired']

    eq_key = get_upstox_key(symbol)
    safe_key = urllib.parse.quote(eq_key) if eq_key else ""
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    
    spot_sym = symbol.upper()
    if spot_sym == 'NIFTY': spot_sym = 'NIFTY 50'
    elif spot_sym == 'SENSEX': spot_sym = 'BSX'
    
    is_expired = target_expiry < current_date
    
    def fetch_expired(target_d):
        if not safe_key: return pd.DataFrame()
        try:
            time.sleep(0.3)
            res = requests.get(f"https://api.upstox.com/v2/expired-instruments/option/contract?instrument_key={safe_key}&expiry_date={target_d.strftime('%Y-%m-%d')}", headers=headers, timeout=10)
            if res.status_code == 200:
                df = pd.DataFrame(res.json().get('data', []))
                if not df.empty:
                    if 'strike_price' in df.columns: df.rename(columns={'strike_price': 'strike'}, inplace=True)
                    if 'trading_symbol' in df.columns: df.rename(columns={'trading_symbol': 'tradingsymbol'}, inplace=True)
                    return df
        except Exception: pass
        return pd.DataFrame()

    chain_df = pd.DataFrame()
    if is_expired:
        chain_df = fetch_expired(target_expiry)
    else:
        if 'underlying_symbol' in df_inst.columns:
            chain_df = df_inst[(df_inst['exchange'].isin(['NSE_FO', 'BSE_FO'])) & (df_inst['underlying_symbol'].astype(str).str.upper() == spot_sym) & (pd.to_datetime(df_inst['expiry']).dt.date == target_expiry)].copy()
        else:
            chain_df = df_inst[(df_inst['exchange'].isin(['NSE_FO', 'BSE_FO'])) & (df_inst['tradingsymbol'].astype(str).str.upper().str.startswith(spot_sym)) & (pd.to_datetime(df_inst['expiry']).dt.date == target_expiry)].copy()
        
        # Fallback if contract is in Limbo but date is technically not historical yet
        if chain_df.empty:
            chain_df = fetch_expired(target_expiry)
            if not chain_df.empty: is_expired = True

    if not chain_df.empty:
        chain_df['strike'] = pd.to_numeric(chain_df['strike'], errors='coerce')
        chain_df = chain_df.dropna(subset=['strike'])

    if chain_cache is not None:
        chain_cache[cache_key] = {'df': chain_df, 'is_expired': is_expired}

    return chain_df, is_expired
