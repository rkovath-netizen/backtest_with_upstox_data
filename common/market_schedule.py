import pytz
from datetime import datetime

# Pre-defined list of NSE holidays for 2026 (Format: YYYY-MM-DD)
# You can update this list easily if the exchange adds special trading days or changes holidays.
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
    """
    Checks if the current time in IST falls within Indian market hours.
    Market hours: Monday to Friday, 09:15 AM to 03:30 PM.
    Excludes weekends and known holidays.
    """
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    
    # 1. Check Weekends (5 = Saturday, 6 = Sunday)
    if now_ist.weekday() >= 5:
        return False, "Weekend"
        
    # 2. Check NSE Holidays
    current_date_str = now_ist.strftime("%Y-%m-%d")
    if current_date_str in NSE_HOLIDAYS_2026:
        return False, "Market Holiday"
        
    # 3. Check Market Hours (09:15 AM to 03:30 PM IST)
    market_start = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if now_ist < market_start:
        return False, "Pre-Market"
    if now_ist > market_end:
        return False, "Post-Market"
        
    return True, "Market Open"
