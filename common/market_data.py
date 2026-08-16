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
        'BANKEX': 'BSE_INDEX|BANKEX'
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
    # Map for lot size
    if symbol_upper == 'NIFTY': symbol_upper = 'NIFTY 50'
    elif symbol_upper == 'BANKNIFTY': symbol_upper = 'NIFTY BANK'
    elif symbol_upper == 'SENSEX': symbol_upper = 'BSX'
    
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
        log_func(f"🔍 [DEBUG SPOT] Resolved {symbol_or_key} to API Key: {instrument_key}")
        if not instrument_key:
            return pd.DataFrame()
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
                if not is_key: log_func(f"   ✅ [DEBUG SPOT] Fetched chunk {chunk_start.date()} ({len(candles)} candles)")
                if candles: all_candles.extend(candles)
            else:
                if not is_key: log_func(f"   ❌ [DEBUG SPOT] API Error {response.status_code} for chunk {chunk_start.date()}: {response.text}")
        except Exception as e:
            if not is_key: log_func(f"   ❌ [DEBUG SPOT] Network Exception: {str(e)}")
        
        chunk_start = chunk_end + timedelta(days=1)

    if not all_candles: 
        if not is_key: log_func(f"⚠️ [DEBUG SPOT] No Spot data retrieved across all chunks.")
        return pd.DataFrame()
        
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert(pytz.timezone("Asia/Kolkata")).dt.tz_localize(None)
    df = df.drop_duplicates(subset=['timestamp']).sort_values("timestamp").reset_index(drop=True)
    return df

def get_option_legs(symbol, entry_time, entry_price, strategy, access_token, chain_cache=None, log_func=print):
    log_func(f"🔍 [DEBUG OPTIONS] Starting leg resolution for {symbol} at {entry_time} (Spot: {entry_price})")
    df_inst = get_instrument_df()
    if df_inst.empty: return []
    
    entry_date = pd.to_datetime(entry_time).date()
    current_date = pd.Timestamp.now(tz="Asia/Kolkata").date()
    cache_key = f"{symbol}_{entry_date}"
    
    if chain_cache is not None and cache_key in chain_cache:
        cached_data = chain_cache[cache_key]
        chain_df = cached_data['df']
        is_expired = cached_data['is_expired']
    else:
        eq_key = get_upstox_key(symbol)
        if not eq_key:
            log_func(f"⚠️ [DEBUG OPTIONS] No underlying instrument found for Options chain.")
            return []
            
        safe_eq_key = urllib.parse.quote(eq_key)
        headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
        
        spot_sym = symbol.upper()
        if spot_sym == 'NIFTY': spot_sym = 'NIFTY 50'
        elif spot_sym == 'BANKNIFTY': spot_sym = 'NIFTY BANK'
        elif spot_sym == 'FINNIFTY': spot_sym = 'NIFTY FIN SERVICE'
        elif spot_sym == 'SENSEX': spot_sym = 'BSX'  
        elif spot_sym == 'BANKEX': spot_sym = 'BKX'  
        
        log_func(f"🔍 [DEBUG OPTIONS] Mapped to Options Underlying Symbol: {spot_sym}")
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
        
        log_func(f"🔍 [DEBUG OPTIONS] Found {len(active_expiries)} active, {len(expired_expiries)} expired. {len(future_expiries)} valid futures.")
        if not future_expiries: 
            log_func("⚠️ [DEBUG OPTIONS] No valid expiries found on or after entry date.")
            return []
            
        closest_expiry = future_expiries[0]
        is_expired = closest_expiry < current_date
        log_func(f"🔍 [DEBUG OPTIONS] Target Expiry: {closest_expiry} (Is Expired: {is_expired})")
        
        chain_df = pd.DataFrame()
        if is_expired:
            try:
                time.sleep(0.3)
                opt_url = f"https://api.upstox.com/v2/expired-instruments/option/contract?instrument_key={safe_eq_key}&expiry_date={closest_expiry.strftime('%Y-%m-%d')}"
                res = requests.get(opt_url, headers=headers, timeout=10)
                if res.status_code == 200:
                    contracts = res.json().get('data', [])
                    chain_df = pd.DataFrame(contracts)
                    if not chain_df.empty:
                        if 'strike_price' in chain_df.columns: chain_df.rename(columns={'strike_price': 'strike'}, inplace=True)
                        if 'trading_symbol' in chain_df.columns: chain_df.rename(columns={'trading_symbol': 'tradingsymbol'}, inplace=True)
            except Exception: pass
        else:
            if 'underlying_symbol' in df_inst.columns:
                chain_df = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & (df_inst['underlying_symbol'] == spot_sym) & (pd.to_datetime(df_inst['expiry']).dt.date == closest_expiry)].copy()
            else:
                chain_df = df_inst[(df_inst['exchange'].isin(valid_fo_exchanges)) & (df_inst['tradingsymbol'].str.startswith(spot_sym)) & (pd.to_datetime(df_inst['expiry']).dt.date == closest_expiry)].copy()

        if chain_cache is not None:
            chain_cache[cache_key] = {'df': chain_df, 'is_expired': is_expired, 'closest_expiry': closest_expiry}

    if chain_df.empty: 
        log_func("⚠️ [DEBUG OPTIONS] Option chain DataFrame is empty.")
        return []

    chain_df['strike'] = pd.to_numeric(chain_df['strike'], errors='coerce')
    chain_df = chain_df.dropna(subset=['strike'])
    unique_strikes = sorted(chain_df['strike'].unique())
    log_func(f"🔍 [DEBUG OPTIONS] Found {len(unique_strikes)} unique strikes in chain.")
    
    if not unique_strikes: return []
        
    closest_idx = min(range(len(unique_strikes)), key=lambda i: abs(unique_strikes[i] - entry_price))
    
    try:
        otm2_pe = unique_strikes[max(0, closest_idx - 2)]
        otm4_pe = unique_strikes[max(0, closest_idx - 4)]
        log_func(f"✅ [DEBUG OPTIONS] Mapped Strikes -> OTM2 PE: {otm2_pe}, OTM4 PE: {otm4_pe}")
    except Exception as e:
        log_func(f"⚠️ [DEBUG OPTIONS] Error resolving strikes: {e}")
        return [] 

    def get_key(s, opt_type):
        target_strike = float(s)
        col_type = 'option_type' if 'option_type' in chain_df.columns else 'instrument_type'
        leg = chain_df[
            (abs(chain_df['strike'] - target_strike) < 0.05) & 
            ((chain_df[col_type] == opt_type) | (chain_df['tradingsymbol'].astype(str).str.endswith(opt_type)))
        ]
        return leg.iloc[0]['instrument_key'] if not leg.empty else None

    legs = []
    if strategy == "Bull Put Spread (OTM2 Sell & OTM4 Buy)":
        legs.append({'type': 'OTM2 PE (Sell)', 'key': get_key(otm2_pe, 'PE'), 'side': -1, 'is_expired': is_expired})
        legs.append({'type': 'OTM4 PE (Buy)', 'key': get_key(otm4_pe, 'PE'), 'side': 1, 'is_expired': is_expired})
        
    valid_legs = [l for l in legs if l['key'] is not None]
    log_func(f"✅ [DEBUG OPTIONS] Final Valid Legs Retrieved: {len(valid_legs)}")
    return valid_legs
