import pytz
from datetime import datetime, timedelta

# Pre-defined list of NSE holidays for 2026 (Format: YYYY-MM-DD)
NSE_HOLIDAYS_2026 = [
    "2026-01-26", # Republic Day
    "2026-03-20", # Id-ul-Fitr (Ramzan Id)
    "2026-03-31", # Mahavir Jayanti
    "2026-04-03", # Good Friday
    "2026-04-14", # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01", # Maharashtra Day
    "2026-08-15", # Independence Day
    "2026-09-04", # Ganesh Chaturthi
    "2026-10-02", # Mahatma Gandhi Jayanti
    "2026-11-08", # Diwali (Laxmi Pujan)
    "2026-11-24", # Gurunanak Jayanti
    "2026-12-25"  # Christmas
]

def is_market_open():
    """Checks if the current time in IST falls within active Indian market hours."""
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    
    if now_ist.weekday() >= 5:
        return False, "Weekend"
        
    current_date_str = now_ist.strftime("%Y-%m-%d")
    if current_date_str in NSE_HOLIDAYS_2026:
        return False, "Market Holiday"
        
    market_start = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if now_ist < market_start:
        return False, "Pre-Market"
    if now_ist >= market_end:
        return False, "Post-Market"
        
    return True, "Market Open"

def get_next_market_open():
    """Calculates the exact datetime of the next opening bell."""
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    
    market_start_today = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    
    if now_ist < market_start_today:
        check_date = market_start_today
    else:
        check_date = market_start_today + timedelta(days=1)
        
    while True:
        # Skip Weekends
        if check_date.weekday() >= 5:
            check_date += timedelta(days=1)
            continue
            
        # Skip Holidays
        if check_date.strftime("%Y-%m-%d") in NSE_HOLIDAYS_2026:
            check_date += timedelta(days=1)
            continue
            
        return check_date
