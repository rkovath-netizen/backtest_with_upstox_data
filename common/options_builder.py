import pandas as pd
from common.market_data import get_available_expiries, get_target_option_chain, get_nfo_lot_size

def build_spread_legs(symbol, entry_time, entry_price, strategy_type, access_token, 
                      sell_offset=2, buy_offset=4, roll_on_expiry_day=True, 
                      chain_cache=None, log_func=print):
    """
    Strategy Business Logic Layer:
    1. Determines which expiry to pick (current vs next week).
    2. Maps strikes according to strategy rules.
    """
    trade_date = pd.to_datetime(entry_time).date()
    valid_expiries = get_available_expiries(symbol, trade_date, access_token)
    
    if not valid_expiries:
        log_func(f"⚠️ No valid expiries found for {symbol} on/after {trade_date}.")
        return []

    # Expiry selection logic
    target_expiry = valid_expiries[0]
    
    # Apply strategy rule: if trading on expiry day, roll to next week if requested
    if roll_on_expiry_day and target_expiry == trade_date and len(valid_expiries) > 1:
        target_expiry = valid_expiries[1]
        log_func(f"🛡️ [STRATEGY RULE] Expiry Day detected -> Rolling to Next Week ({target_expiry})")

    chain_df, is_expired = get_target_option_chain(symbol, target_expiry, access_token, chain_cache=chain_cache, log_func=log_func)
    
    if chain_df.empty:
        return []

    unique_strikes = sorted(chain_df['strike'].unique())
    if not unique_strikes: 
        return []
        
    closest_idx = min(range(len(unique_strikes)), key=lambda i: abs(unique_strikes[i] - entry_price))
    fallback_lot_size = get_nfo_lot_size(symbol)

    def extract_leg(target_strike, opt_type):
        col_type = 'option_type' if 'option_type' in chain_df.columns else 'instrument_type'
        match = chain_df[(abs(chain_df['strike'] - target_strike) < 0.05) & 
                         ((chain_df[col_type] == opt_type) | (chain_df['tradingsymbol'].astype(str).str.endswith(opt_type)))]
        if not match.empty:
            row = match.iloc[0]
            ls = int(row['lot_size']) if 'lot_size' in row and pd.notna(row['lot_size']) else fallback_lot_size
            if ls <= 1: 
                ls = fallback_lot_size
            return row['instrument_key'], ls
        return None, None

    legs = []
    try:
        if strategy_type == "Bull Put Spread":
            strike_sell = unique_strikes[max(0, closest_idx - sell_offset)]
            strike_buy = unique_strikes[max(0, closest_idx - buy_offset)]
            k_sell, ls_sell = extract_leg(strike_sell, 'PE')
            k_buy, ls_buy = extract_leg(strike_buy, 'PE')
            
            if k_sell and k_buy:
                legs = [
                    {'strike': strike_sell, 'key': k_sell, 'lot_size': ls_sell, 'side': -1, 'is_expired': is_expired},
                    {'strike': strike_buy, 'key': k_buy, 'lot_size': ls_buy, 'side': 1, 'is_expired': is_expired}
                ]
                
        elif strategy_type == "Bear Call Spread":
            strike_sell = unique_strikes[min(len(unique_strikes)-1, closest_idx + sell_offset)]
            strike_buy = unique_strikes[min(len(unique_strikes)-1, closest_idx + buy_offset)]
            k_sell, ls_sell = extract_leg(strike_sell, 'CE')
            k_buy, ls_buy = extract_leg(strike_buy, 'CE')
            
            if k_sell and k_buy:
                legs = [
                    {'strike': strike_sell, 'key': k_sell, 'lot_size': ls_sell, 'side': -1, 'is_expired': is_expired},
                    {'strike': strike_buy, 'key': k_buy, 'lot_size': ls_buy, 'side': 1, 'is_expired': is_expired}
                ]
    except Exception as e:
        log_func(f"Error building legs: {e}")

    return legs
