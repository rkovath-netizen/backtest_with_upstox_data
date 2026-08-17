import pandas as pd
from common.market_data import get_target_option_chain, get_nfo_lot_size

def build_spread_legs(symbol, entry_time, entry_price, strategy_type, access_token, sell_offset=2, buy_offset=4, chain_cache=None):
    """
    Independent module to build strategy-specific option legs.
    Takes raw data from market_data and applies quantitative strike selection logic.
    """
    # 1. Fetch raw data from the data connector
    chain_df, is_expired = get_target_option_chain(symbol, pd.to_datetime(entry_time).date(), access_token, chain_cache)
    
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
            # Extract historical lot size, safely falling back to current lot size if API returns bad data
            ls = int(row['lot_size']) if 'lot_size' in row and pd.notna(row['lot_size']) else fallback_lot_size
            if ls <= 1: 
                ls = fallback_lot_size
            return row['instrument_key'], ls
        return None, None

    legs = []
    try:
        # 2. Apply strategy-specific logic boundary
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
        print(f"Error building legs: {e}")

    return legs
