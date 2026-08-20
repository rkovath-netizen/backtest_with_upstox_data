import streamlit as st
import pandas as pd
import datetime as dt
from datetime import timedelta

# Import the isolated backtest engine
from strategies.backtest_lab.strategy_engine_backtest import process_ema_rsi_guided_strategy

def run_ema_rsi_app():
    st.title("🔬 R&D Backtest Lab: EMA/RSI Spread Strategy")
    st.markdown("Isolated environment for testing dynamic exits and entry variations without impacting the live scanner.")

    # ==========================================
    # SIDEBAR: CONFIGURATION & PARAMETERS
    # ==========================================
    st.sidebar.header("1. Data Configuration")
    upstox_token = st.sidebar.text_input("Upstox Access Token", type="password", help="Required to fetch historical OHLCV data.")
    
    # Date Range Selection
    today = dt.date.today()
    end_date = st.sidebar.date_input("End Date", today)
    start_date = st.sidebar.date_input("Start Date", today - timedelta(days=30))
    
    # Symbol Input
    default_symbols = "NIFTY, BANKNIFTY"
    symbols_input = st.sidebar.text_input("Symbols (comma separated)", default_symbols)
    symbols = [s.strip().upper() for s in symbols_input.split(",") if s.strip()]

    st.sidebar.header("2. Strategy Parameters")
    ltf = st.sidebar.selectbox("Lower Timeframe (LTF)", ["1min", "3min", "5min", "15min"], index=1)
    sell_offset = st.sidebar.number_input("Sell Leg Offset (from Spot)", value=2, step=1)
    buy_offset = st.sidebar.number_input("Buy Leg Offset (from Spot)", value=4, step=1)
    max_concurrent_trades = st.sidebar.number_input("Max Concurrent Trades", value=3, step=1)
    
    st.sidebar.header("3. Entry Filters")
    require_color = st.sidebar.checkbox("Require Candle Color Match", value=False)
    require_expansion = st.sidebar.checkbox("Require Candle Expansion", value=False)
    require_rsi_sma = st.sidebar.checkbox("Require RSI > RSI SMA", value=True)
    require_1h_sma = st.sidebar.checkbox("Require 1H Trend Alignment", value=True)
    require_adx = st.sidebar.checkbox("Require ADX Filter", value=True)
    adx_threshold = st.sidebar.number_input("ADX Threshold", value=20.0, step=1.0) if require_adx else 0.0

    # ==========================================
    # MAIN PANEL: EXECUTION & RESULTS
    # ==========================================
    if st.button("🚀 Run Historical Backtest", use_container_width=True):
        if not upstox_token:
            st.error("Please enter your Upstox Access Token in the sidebar to fetch data.")
            return
        if not symbols:
            st.error("Please enter at least one symbol.")
            return
            
        st.info(f"Starting backtest from {start_date} to {end_date}. Fetching historical options chains...")
        
        # UI Elements for Tracking Progress
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_container = st.expander("Engine Execution Logs", expanded=False)
        
        # Callback functions to route engine output to the Streamlit UI
        def ui_log(msg):
            # Using print ensures it shows in the standard terminal/cloud logs for debugging
            print(msg) 
            
        def ui_progress(current, total, msg):
            progress_pct = min(int((current / total) * 100), 100)
            progress_bar.progress(progress_pct)
            status_text.text(msg)
        
        try:
            # ------------------------------------------------
            # EXECUTE THE STRATEGY ENGINE
            # ------------------------------------------------
            results_df = process_ema_rsi_guided_strategy(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                upstox_token=upstox_token,
                sell_offset=sell_offset,
                buy_offset=buy_offset,
                require_color=require_color,
                require_expansion=require_expansion,
                require_rsi_sma=require_rsi_sma,
                require_1h_sma=require_1h_sma,
                require_adx=require_adx,
                adx_threshold=adx_threshold,
                ltf=ltf,
                max_concurrent_trades=max_concurrent_trades,
                progress_callback=ui_progress,
                log_func=ui_log
            )
            
            progress_bar.progress(100)
            status_text.text("✅ Backtest Processing Complete!")
            
            # ------------------------------------------------
            # DISPLAY METRICS & DATAFRAMES
            # ------------------------------------------------
            if results_df.empty:
                st.warning("No trades were generated with the current parameters and date range.")
            else:
                st.success(f"Successfully processed {len(results_df)} trades.")
                
                # --- High-Level Performance Metrics ---
                st.subheader("📊 Performance Summary")
                total_trades = len(results_df)
                winning_trades = len(results_df[results_df['PnL (₹)'] > 0])
                win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
                
                total_pnl = results_df['PnL (₹)'].sum()
                avg_pnl = results_df['PnL (₹)'].mean()
                
                # Highlight metrics layout
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Trades", total_trades)
                col2.metric("Win Rate", f"{win_rate:.2f}%")
                col3.metric("Total PnL", f"₹ {total_pnl:,.2f}")
                col4.metric("Avg Trade PnL", f"₹ {avg_pnl:,.2f}")
                
                st.markdown("---")
                
                # --- Exit Reason Breakdown ---
                st.subheader("🚪 Exit Analytics")
                st.markdown("Review how often trades are hitting the new dynamic exits versus hard stops or targets.")
                
                exit_counts = results_df['Exit Reason'].value_counts().reset_index()
                exit_counts.columns = ['Exit Reason', 'Number of Trades']
                st.dataframe(exit_counts, use_container_width=True, hide_index=True)
                
                st.markdown("---")
                
                # --- Full Trade Log ---
                st.subheader("📝 Detailed Trade Ledger")
                # Reorder columns slightly for better readability if desired, or show raw
                display_cols = ['Symbol', 'Type', 'Entry Basis', 'Entry Time', 'Exit Time', 'Bars in Trade', 'Net Credit (₹)', 'PnL (₹)', 'PnL (%)', 'Exit Reason']
                st.dataframe(results_df[display_cols] if all(c in results_df.columns for c in display_cols) else results_df, use_container_width=True)
                
                # --- CSV Export ---
                csv = results_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="⬇️ Download Trade Log as CSV",
                    data=csv,
                    file_name=f"backtest_results_{today.strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                )
                
        except Exception as e:
            st.error(f"An error occurred during backtesting: {e}")
