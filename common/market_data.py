import requests
import pandas as pd
import pytz
import streamlit as st
import urllib.parse
import time
from datetime import datetime

UPSTOX_INSTRUMENT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
UPSTOX_HISTORICAL_URL = "https://api.upstox.com/v2/historical-candle/{instrument_key}/{unit}/{to_date}/{from_date}"
UPSTOX_EXPIRED_HISTORICAL_URL = "https://api.upstox.com/v2/expired-instruments/historical-candle/{instrument_key}/{unit}/{to_date}/{from_date}"

@st.cache_data(ttl=3600, show_spinner=False)
def get_instrument_df():
    try:
        df = pd.read_csv(UPSTOX_INSTRUMENT_URL, compression='gzip')
        df = df[df['exchange'].isin(['NSE_EQ', 'NSE_FO'])]
        df['expiry'] = pd.to_datetime(df['expiry'], errors='coerce')
        return df
    except Exception as e:
        st.error(f"❌ Failed to download Upstox Instrument Master: {e}")
        return pd.DataFrame()

def get_nfo_lot_size(symbol):
    df = get_instrument_df()
    if df.empty: return 1
    
    if 'underlying_symbol' in df.columns:
        derivatives = df[(df['underlying_symbol'] == symbol) & (df['exchange'] == 'NSE_FO')]
    else:
        derivatives = df[(df['name'] == symbol) & (df['exchange'] == 'NSE_FO')]
        
    if derivatives.empty:
        derivatives = df[(df['tradingsymbol'].str.startswith(symbol)) & (df['exchange'] == 'NSE_FO')]
        
    if not derivatives.empty: return int(derivatives.iloc[0]['lot_size'])
    return 1 

def fetch_upstox_intraday_candles(symbol_or_key, start_dt, end_dt, access_token, interval="1minute", is_key=False, is_expired=False, log_func=print):
    df_inst = get_instrument_df()
    if df_inst.empty:
        return pd.DataFrame()

    if not is_key:
        clean_sym = symbol_or_key.replace("NSE:", "").replace("BSE:", "").strip()
        eq_rows = df_inst[(df_inst['tradingsymbol'] == clean_sym) & (df_inst['exchange'] == 'NSE_EQ')]
        if eq_rows.empty:
            return pd.DataFrame()
        instrument_key = eq_rows.iloc[0]['instrument_key']
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

    base_url = UPSTOX_EXPIRED_HISTORICAL_URL if is_expired else UPSTOX_HISTORICAL_URL
    url = base_url.format(
        instrument_key=safe_instrument_key, 
        unit=interval,
        to_date=end_dt.strftime("%Y-%m-%d"), 
        from_date=start_dt.strftime("%Y-%m-%d")
    )
    
    try:
        time.sleep(0.2)  # Rate Limit Pacing for Cloudflare
        headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            candles = response.json().get("data", {}).get("candles", [])
            if not candles:
                return pd.DataFrame()
            df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert(pytz.timezone("Asia/Kolkata")).dt.tz_localize(None)
            return df.sort_values("timestamp").reset_index(drop=True)
        else:
            return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

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
        eq_rows = df_inst[(df_inst['tradingsymbol'] == symbol) & (df_inst['exchange'] == 'NSE_EQ')]
        if eq_rows.empty:
            if strategy == "Options: Naked Call Buy":
                log_func(f"⚠️ {symbol}: No underlying Cash instrument found in Master.")
            return []
            
        eq_key = eq_rows.iloc[0]['instrument_key']
        safe_eq_key = urllib.parse.quote(eq_key)
        headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
        
        active_expiries = []
        if 'underlying_symbol' in df_inst.columns:
            opts_active = df_inst[(df_inst['exchange'] == 'NSE_FO') & (df_inst['underlying_symbol'] == symbol)]
        else:
            opts_active = df_inst[(df_inst['exchange'] == 'NSE_FO') & ((df_inst['name'] == symbol) | (df_inst['tradingsymbol'].str.startswith(symbol)))]
            
        if not opts_active.empty:
            active_expiries = pd.to_datetime(opts_active['expiry'], errors='coerce').dt.date.dropna().unique().tolist()
            
        expired_expiries = []
        try:
            time.sleep(0.2)
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
            if strategy == "Options: Naked Call Buy":
                log_func(f"⚠️ {symbol}: No expiries found >= {entry_date}. (Cash-only stock?)")
            return []
            
        closest_expiry = future_expiries[0]
        is_expired = closest_expiry < current_date
        
        chain_df = pd.DataFrame()
        if is_expired:
            try:
                time.sleep(0.2)
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
                chain_df = df_inst[(df_inst['exchange'] == 'NSE_FO') & (df_inst['underlying_symbol'] == symbol) & (pd.to_datetime(df_inst['expiry']).dt.date == closest_expiry)].copy()
            else:
                chain_df = df_inst[(df_inst['exchange'] == 'NSE_FO') & (df_inst['tradingsymbol'].str.startswith(symbol)) & (pd.to_datetime(df_inst['expiry']).dt.date == closest_expiry)].copy()

        if chain_cache is not None:
            chain_cache[cache_key] = {
                'df': chain_df,
                'is_expired': is_expired,
                'closest_expiry': closest_expiry
            }

    if chain_df.empty:
        return []

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
        
    if strategy == "Options: Naked Call Buy":
        log_func(f"✅ {symbol} | Found ATM {atm} | via {'API (Expired)' if is_expired else 'Master (Active)'}")

    def get_key(s, opt_type):
        target_strike = float(s)
        col_type = 'option_type' if 'option_type' in chain_df.columns else 'instrument_type'
        leg = chain_df[
            (abs(chain_df['strike'] - target_strike) < 0.05) & 
            ((chain_df[col_type] == opt_type) | (chain_df['tradingsymbol'].astype(str).str.endswith(opt_type)))
        ]
        return leg.iloc[0]['instrument_key'] if not leg.empty else None

    legs = []
    if strategy == "Options: Naked Call Buy":
        legs.append({'type': 'ATM CE', 'key': get_key(atm, 'CE'), 'side': 1, 'is_expired': is_expired})
    elif strategy == "Options: Naked Put Buy": 
        legs.append({'type': 'ATM PE', 'key': get_key(atm, 'PE'), 'side': 1, 'is_expired': is_expired})
    elif strategy == "Options: Long Straddle":
        legs.append({'type': 'ATM CE', 'key': get_key(atm, 'CE'), 'side': 1, 'is_expired': is_expired})
        legs.append({'type': 'ATM PE', 'key': get_key(atm, 'PE'), 'side': 1, 'is_expired': is_expired})
    elif strategy == "Options: Bull Put Spread (ATM & OTM1)":
        legs.append({'type': 'ATM PE', 'key': get_key(atm, 'PE'), 'side': -1, 'is_expired': is_expired})
        legs.append({'type': 'OTM1 PE', 'key': get_key(otm1_pe, 'PE'), 'side': 1, 'is_expired': is_expired})
    elif strategy == "Options: Bull Put Spread (ATM & OTM2)":
        legs.append({'type': 'ATM PE', 'key': get_key(atm, 'PE'), 'side': -1, 'is_expired': is_expired})
        legs.append({'type': 'OTM2 PE', 'key': get_key(otm2_pe, 'PE'), 'side': 1, 'is_expired': is_expired})
    elif strategy == "Options: Bear Call Spread (ATM & OTM1)":
        legs.append({'type': 'ATM CE', 'key': get_key(atm, 'CE'), 'side': -1, 'is_expired': is_expired})
        legs.append({'type': 'OTM1 CE', 'key': get_key(otm1_ce, 'CE'), 'side': 1, 'is_expired': is_expired})
    elif strategy == "Options: Bear Call Spread (ATM & OTM2)":
        legs.append({'type': 'ATM CE', 'key': get_key(atm, 'CE'), 'side': -1, 'is_expired': is_expired})
        legs.append({'type': 'OTM2 CE', 'key': get_key(otm2_ce, 'CE'), 'side': 1, 'is_expired': is_expired})
    
    # NEW STRATEGY MAPPING ADDED HERE
    elif strategy == "Bull Put Spread (OTM2 Sell & OTM4 Buy)":
        legs.append({'type': 'OTM2 PE (Sell)', 'key': get_key(otm2_pe, 'PE'), 'side': -1, 'is_expired': is_expired})
        legs.append({'type': 'OTM4 PE (Buy)', 'key': get_key(otm4_pe, 'PE'), 'side': 1, 'is_expired': is_expired})
        
    return [l for l in legs if l['key'] is not None]# Upstox API & Data Layer (Paste your robust code here)
