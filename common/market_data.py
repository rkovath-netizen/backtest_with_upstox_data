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
        df = df[df['exchange'].isin(['NSE_EQ', 'NSE_FO', 'NSE_INDEX'])]
        df['expiry'] = pd.to_datetime(df['expiry'], errors='coerce')
        return df
    except Exception as e:
        st.error(f"❌ Failed to download Upstox Instrument Master: {e}")
        return pd.DataFrame()

def get_nfo_lot_size(symbol):
    df = get_instrument_df()
    if df.empty: return 1
    
    symbol_upper = symbol.upper()
    if 'underlying_symbol' in df.columns:
        derivatives = df[(df['underlying_symbol'] == symbol_upper) & (df['exchange'] == 'NSE_FO')]
    else:
        derivatives = df[(df['name'] == symbol_upper) & (df['exchange'] == 'NSE_FO')]
        
    if derivatives.empty:
        derivatives = df[(df['tradingsymbol'].str.startswith(symbol_upper)) & (df['exchange'] == 'NSE_FO')]
        
    if not derivatives.empty: return int(derivatives.iloc[0]['lot_size'])
    return 1 

def fetch_upstox_intraday_candles(symbol_or_key, start_dt, end_dt, access_token, interval="1minute", is_key=False, is_expired=False, log_func=print):
    if not is_key:
        clean_sym = symbol_or_key.replace("NSE:", "").replace("BSE:", "").strip().upper()
        
        # 🐛 FIX: Bulletproof Index Key Mapping (Bypasses CSV search entirely)
        if clean_sym == "NIFTY": 
            instrument_key = "NSE_INDEX|Nifty 50"
        elif clean_sym == "BANKNIFTY": 
            instrument_key = "NSE_INDEX|Nifty Bank"
        elif clean_sym == "FINNIFTY": 
            instrument_key = "NSE_INDEX|Nifty Fin Service"
        else:
            df_inst = get_instrument_df()
            if df_inst.empty:
                return pd.DataFrame()
                
            eq_rows = df_inst[(df_inst['tradingsymbol'] == clean_sym) & (df_inst['exchange'] == 'NSE_EQ')]
            if eq_rows.empty:
                log_func(f"⚠️ [DEBUG] Could not find {clean_sym} in NSE_EQ Master List.")
                return pd.DataFrame()
            instrument_key = eq_rows.iloc[0]['instrument_key']
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
            time.sleep(0.3)  # Cloudflare pacing
            if not is_key:
                log_func(f"   📡 Fetching {symbol_or_key} Spot: {chunk_start.date()} to {chunk_end.date()}...")
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                candles = response.json().get("data", {}).get("candles", [])
                if candles:
                    all_candles.extend(candles)
            else:
                log_func(f"   ⚠️ [DEBUG] API Error {response.status_code} for chunk {chunk_start.date()}: {response.text}")
                
        except Exception as e:
            log_func(f"   ⚠️ [DEBUG] Network Exception: {str(e)}")
        
        chunk_start = chunk_end + timedelta(days=1)

    if not all_candles:
        if not is_key:
            log_func(f"⚠️ [DEBUG] No data returned from API for {symbol_or_key} across all chunks.")
        return pd.DataFrame()
        
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert(pytz.timezone("Asia/Kolkata")).dt.tz_localize(None)
    
    # Drop duplicates caused by chunk boundaries
    df = df.drop_duplicates(subset=['timestamp']).sort_values("timestamp").reset_index(drop=True)
    return df

def get_option_legs(symbol, entry_time, entry_price, strategy, access_token, chain_cache=None, log_func=print):
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
        spot_sym = symbol.upper()
        
        # 🐛 FIX: Bulletproof Index Key Mapping for Expired API Lookups
        if spot_sym == "NIFTY": 
            eq_key = "NSE_INDEX|Nifty 50"
        elif spot_sym == "BANKNIFTY": 
            eq_key = "NSE_INDEX|Nifty Bank"
        elif spot_sym == "FINNIFTY": 
            eq_key = "NSE_INDEX|Nifty Fin Service"
        else:
            eq_rows = df_inst[(df_inst['tradingsymbol'] == spot_sym) & (df_inst['exchange'] == 'NSE_EQ')]
            if eq_rows.empty:
                log_func(f"⚠️ [DEBUG] {symbol}: No underlying Cash instrument found in Master.")
                return []
            eq_key = eq_rows.iloc[0]['instrument_key']
            
        safe_eq_key = urllib.parse.quote(eq_key)
        headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
        
        active_expiries = []
        if 'underlying_symbol' in df_inst.columns:
            opts_active = df_inst[(df_inst['exchange'] == 'NSE_FO') & (df_inst['underlying_symbol'] == spot_sym)]
        else:
            opts_active = df_inst[(df_inst['exchange'] == 'NSE_FO') & ((df_inst['name'] == spot_sym) | (df_inst['tradingsymbol'].str.startswith(spot_sym)))]
            
        if not opts_active.empty:
            active_expiries = pd.to_datetime(opts_active['expiry'], errors='coerce').dt.date.dropna().unique().tolist()
            
        expired_expiries = []
        try:
            time.sleep(0.3)
            exp_url = f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={safe_eq_key}"
            res = requests.get(exp_url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json().get('data', [])
                expired_expiries = [pd.to_datetime(d).date() for d in data]
        except Exception:
            pass 

        all_expiries = sorted(list(set(active_expiries + expired_expiries)))
        future_expiries = [d for d in all_expiries if d >= entry_date]
        
        if not future_expiries:
            log_func(f"⚠️ [DEBUG] {symbol}: No expiries found >= {entry_date}.")
            return []
            
        closest_expiry = future_expiries[0]
        is_expired = closest_expiry < current_date
        
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
                        if 'strike_price' in chain_df.columns:
                            chain_df.rename(columns={'strike_price': 'strike'}, inplace=True)
                        if 'trading_symbol' in chain_df.columns:
                            chain_df.rename(columns={'trading_symbol': 'tradingsymbol'}, inplace=True)
            except Exception:
                return []
        else:
            if 'underlying_symbol' in df_inst.columns:
                chain_df = df_inst[(df_inst['exchange'] == 'NSE_FO') & (df_inst['underlying_symbol'] == spot_sym) & (pd.to_datetime(df_inst['expiry']).dt.date == closest_expiry)].copy()
            else:
                chain_df = df_inst[(df_inst['exchange'] == 'NSE_FO') & (df_inst['tradingsymbol'].str.startswith(spot_sym)) & (pd.to_datetime(df_inst['expiry']).dt.date == closest_expiry)].copy()

        if chain_cache is not None:
            chain_cache[cache_key] = {
                'df': chain_df,
                'is_expired': is_expired,
                'closest_expiry': closest_expiry
            }

    if chain_df.empty: return []

    chain_df['strike'] = pd.to_numeric(chain_df['strike'], errors='coerce')
    chain_df = chain_df.dropna(subset=['strike'])
    unique_strikes = sorted(chain_df['strike'].unique())
    if not unique_strikes: return []
        
    closest_idx = min(range(len(unique_strikes)), key=lambda i: abs(unique_strikes[i] - entry_price))
    
    try:
        atm = unique_strikes[closest_idx]
        otm1_pe = unique_strikes[max(0, closest_idx - 1)]
        otm2_pe = unique_strikes[max(0, closest_idx - 2)]
        otm3_pe = unique_strikes[max(0, closest_idx - 3)]
        otm4_pe = unique_strikes[max(0, closest_idx - 4)]
        
        otm1_ce = unique_strikes[min(len(unique_strikes)-1, closest_idx + 1)]
        otm2_ce = unique_strikes[min(len(unique_strikes)-1, closest_idx + 2)]
        otm3_ce = unique_strikes[min(len(unique_strikes)-1, closest_idx + 3)]
        otm4_ce = unique_strikes[min(len(unique_strikes)-1, closest_idx + 4)]
    except Exception:
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
        
    return [l for l in legs if l['key'] is not None]
