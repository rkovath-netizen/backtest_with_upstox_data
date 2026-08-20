import pandas as pd
import numpy as np

def process_ema_rsi_guided_strategy(df, sl_pct=0.02, target_rr=2.0):
    """
    Backtest engine for EMA/VWAP/RSI strategy with Dynamic Invalidation Exits.
    Assumes df already contains columns: 'open', 'high', 'low', 'close', 'ema_9', 'ema_50', 'vwap', 'rsi'
    """
    active_trade = None
    trade_history = []
    
    # Initialize columns for UI visualization in Streamlit
    df['signal'] = ''
    df['trade_pnl_pct'] = 0.0

    # Iterate through the dataframe row by row
    for index, row in df.iterrows():
        
        # ==========================================
        # 1. EXIT LOGIC (Evaluated on active trades)
        # ==========================================
        if active_trade is not None:
            exit_price = 0
            exit_reason = ""
            trade_closed = False

            # A. Hard Stop Loss Check (Intra-candle: checks high/low)
            if active_trade['type'] == 'BUY' and row['low'] <= active_trade['sl_price']:
                exit_price = active_trade['sl_price']
                exit_reason = f"Hard SL ({sl_pct*100}%)"
                trade_closed = True
            elif active_trade['type'] == 'SELL' and row['high'] >= active_trade['sl_price']:
                exit_price = active_trade['sl_price']
                exit_reason = f"Hard SL ({sl_pct*100}%)"
                trade_closed = True

            # B. Target Check (Intra-candle: checks high/low)
            elif active_trade['type'] == 'BUY' and row['high'] >= active_trade['target_price']:
                exit_price = active_trade['target_price']
                exit_reason = f"Target Reached (1:{target_rr} RR)"
                trade_closed = True
            elif active_trade['type'] == 'SELL' and row['low'] <= active_trade['target_price']:
                exit_price = active_trade['target_price']
                exit_reason = f"Target Reached (1:{target_rr} RR)"
                trade_closed = True

            # C. NEW: Dynamic Invalidation Exit (Evaluated at Candle Close)
            else:
                # Identify which EMA triggered this specific trade
                reference_ema = row['ema_9'] if active_trade['entry_basis'] == 'EMA_9' else row['ema_50']
                
                if active_trade['type'] == 'BUY' and row['close'] < reference_ema:
                    exit_price = row['close'] # Exit at market close
                    exit_reason = f"Invalidation: Closed below {active_trade['entry_basis']}"
                    trade_closed = True
                    
                elif active_trade['type'] == 'SELL' and row['close'] > reference_ema:
                    exit_price = row['close'] # Exit at market close
                    exit_reason = f"Invalidation: Closed above {active_trade['entry_basis']}"
                    trade_closed = True

            # D. Execute Trade Closure
            if trade_closed:
                # Calculate PnL Percentage
                if active_trade['type'] == 'BUY':
                    pnl_pct = (exit_price - active_trade['entry_price']) / active_trade['entry_price']
                else:
                    pnl_pct = (active_trade['entry_price'] - exit_price) / active_trade['entry_price']
                
                # Save trade data
                active_trade['exit_time'] = index
                active_trade['exit_price'] = exit_price
                active_trade['pnl_pct'] = pnl_pct
                active_trade['exit_reason'] = exit_reason
                
                trade_history.append(active_trade)
                
                # Mark DataFrame for UI plotting
                df.at[index, 'signal'] = f"EXIT {active_trade['type']} ({exit_reason})"
                df.at[index, 'trade_pnl_pct'] = pnl_pct
                
                # Reset trade state, continue to next candle without entering a new trade instantly
                active_trade = None
                continue 

        # ==========================================
        # 2. ENTRY LOGIC (Evaluated if flat)
        # ==========================================
        if active_trade is None:
            # Baseline Filters: Trend & Momentum
            uptrend = (row['close'] > row['vwap']) and (row['rsi'] > 50)
            downtrend = (row['close'] < row['vwap']) and (row['rsi'] < 50)
            
            # Scenario 1: BUY Rejection at 9 EMA
            if uptrend and (row['low'] <= row['ema_9']) and (row['close'] > row['ema_9']):
                entry_price = row['close']
                risk = entry_price * sl_pct
                active_trade = {
                    'entry_time': index,
                    'type': 'BUY',
                    'entry_price': entry_price,
                    'entry_basis': 'EMA_9', # Tagging the entry basis
                    'sl_price': entry_price - risk,
                    'target_price': entry_price + (risk * target_rr)
                }
                df.at[index, 'signal'] = "ENTER BUY (9 EMA)"
            
            # Scenario 2: BUY Rejection at 50 EMA
            elif uptrend and (row['low'] <= row['ema_50']) and (row['close'] > row['ema_50']):
                entry_price = row['close']
                risk = entry_price * sl_pct
                active_trade = {
                    'entry_time': index,
                    'type': 'BUY',
                    'entry_price': entry_price,
                    'entry_basis': 'EMA_50', # Tagging the entry basis
                    'sl_price': entry_price - risk,
                    'target_price': entry_price + (risk * target_rr)
                }
                df.at[index, 'signal'] = "ENTER BUY (50 EMA)"
            
            # Scenario 3: SELL Rejection at 9 EMA
            elif downtrend and (row['high'] >= row['ema_9']) and (row['close'] < row['ema_9']):
                entry_price = row['close']
                risk = entry_price * sl_pct
                active_trade = {
                    'entry_time': index,
                    'type': 'SELL',
                    'entry_price': entry_price,
                    'entry_basis': 'EMA_9', # Tagging the entry basis
                    'sl_price': entry_price + risk,
                    'target_price': entry_price - (risk * target_rr)
                }
                df.at[index, 'signal'] = "ENTER SELL (9 EMA)"

            # Scenario 4: SELL Rejection at 50 EMA
            elif downtrend and (row['high'] >= row['ema_50']) and (row['close'] < row['ema_50']):
                entry_price = row['close']
                risk = entry_price * sl_pct
                active_trade = {
                    'entry_time': index,
                    'type': 'SELL',
                    'entry_price': entry_price,
                    'entry_basis': 'EMA_50', # Tagging the entry basis
                    'sl_price': entry_price + risk,
                    'target_price': entry_price - (risk * target_rr)
                }
                df.at[index, 'signal'] = "ENTER SELL (50 EMA)"

    # Convert the list of trade dictionaries to a DataFrame for easy metric calculations
    trades_df = pd.DataFrame(trade_history)
    
    return df, trades_df
