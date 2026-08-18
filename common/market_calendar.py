import requests
import pandas as pd
import streamlit as st
from datetime import timedelta, date

# 🚨 Fetch official Market Holidays directly from Upstox API
@st.cache_data(ttl=86400, show_spinner=False) # Cache for 24 hours
def get_market_holidays():
    url = "https://api.upstox.com/v2/market/holidays"
    headers = {'Accept': 'application/json'}
    holidays = set()
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json().get('data', [])
            for item in data:
                if 'date' in item:
                    try:
                        holidays.add(pd.to_datetime(item['date']).date())
                    except: pass
    except Exception as e:
        print(f"⚠️ Failed to fetch Upstox Holiday API: {e}")
    return holidays

def get_mathematical_expiry(symbol, target_date):
    """Calculates theoretical expiry dates, skipping official holidays."""
    official_holidays = get_market_holidays()
    
    def get_valid_expiry(d, target_weekday):
        # 1. Find the natural mathematical expiry day
        days_ahead = target_weekday - d.weekday()
        if days_ahead < 0: days_ahead += 7
        calc_date = d + timedelta(days_ahead)
        
        # 2. Shift backwards if it lands on an official holiday or weekend
        while calc_date in official_holidays or calc_date.weekday() >= 5: # 5=Sat, 6=Sun
            calc_date -= timedelta(days=1)
        return calc_date
        
    sym = symbol.upper()
    if sym == 'NIFTY':       # Expires Tuesday
        return [get_valid_expiry(target_date, 1), get_valid_expiry(target_date + timedelta(days=7), 1)]
    elif sym == 'SENSEX':    # Expires Thursday
        return [get_valid_expiry(target_date, 3), get_valid_expiry(target_date + timedelta(days=7), 3)]
    elif sym == 'BANKNIFTY': # Expires Wednesday
        return [get_valid_expiry(target_date, 2), get_valid_expiry(target_date + timedelta(days=7), 2)]
    return []

def resolve_expiry(symbol, trade_date, api_expiries, log_func=print):
    """
    Acts as a middleware bridge. If the API provides the expiries, it passes them through.
    If the API is empty (Limbo Bug), it triggers the Calendar Engine to calculate them.
    """
    valid_api_expiries = [d for d in api_expiries if d >= trade_date]
    
    if valid_api_expiries:
        return valid_api_expiries
        
    log_func(f"⚠️ [CALENDAR ENGINE] API data missing for {symbol} on {trade_date}. Activating Limbo Injector...")
    math_expiries = get_mathematical_expiry(symbol, trade_date)
    
    if math_expiries:
        log_func(f"🛡️ [CALENDAR ENGINE] Mathematically injected valid dates: {math_expiries}")
        return math_expiries
        
    return []
