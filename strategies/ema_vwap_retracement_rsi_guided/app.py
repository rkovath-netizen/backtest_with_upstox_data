import streamlit as st
import datetime as dt
import pandas as pd
import time
from datetime import timedelta
from strategies.ema_vwap_retracement_rsi_guided.strategy_engine import process_ema_rsi_guided_strategy, run_live_scanner

def run_ema_rsi_app():
    st.title("🎯 Pure Price Retracement Engine")
    st.caption("(A/B Test Variant: Spot Data + EMA 50 + RSI Momentum Validation)")

    # -------------------------------------------------------------
    # ⚙️ Sidebar Strategy Configuration
    # -------------------------------------------------------------
    st.sidebar.markdown("### ⚙️ Strategy Configuration")
    
    report_name = st.sidebar.text_input("Report Name", value="pure_price_retracement_scan")
    
    symbols_selected = st.sidebar.multiselect(
        "Indices to Scan:",
        options=["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
        default=["NIFTY", "SENSEX"]
    )

    # ⏱️ Execution Timeframe (LTF)
    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⏱️ Timeframe Settings")
    ltf_choice = st.sidebar.selectbox(
        "Entry Timeframe (LTF)",
        options=["3min", "5min", "1min", "15min"],
        index=0,
        help="Select the timeframe used for EMA 50 touch detection and trailing stop management."
    )

    # 🚦 Entry Conditions
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🚦 Entry Conditions")
    require_color = st.sidebar.checkbox("Require Trend Candle Color", value=False)
    require_expansion = st.sidebar.checkbox("Require Body Expansion (>Avg)", value=False, help="Requires candle body to be larger than 10-bar average.")
    require_rsi_sma = st.sidebar.checkbox("Require 15m RSI > RSI SMA", value=True, help="Validates momentum using 15m RSI vs 14 SMA.")
    require_1h_sma = st.sidebar.checkbox("Require 1h Close > SMA 20", value=True, help="Confirms macro trend alignment on 1H chart.")

    # 🚨 ADX Trend Strength Filter
    require_adx = st.sidebar.checkbox("Require 15m ADX Filter", value=True, help="Filters out signals when market is in sideways consolidation.")
    
    adx_threshold = 20.0
    if require_adx:
        adx_threshold = st.sidebar.number_input(
            "Min 15m ADX Threshold",
            min_value=10.0,
            max_value=50.0,
            value=20.0,
            step=1.0,
            help="Signals with 15m ADX below this level will be blocked as sideways chop."
        )

    # 🛡️ Risk Management (Concurrency)
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🛡️ Risk Management")
    max_concurrent = st.sidebar.number_input(
        "Max Concurrent Trades (Per Symbol)", 
        min_value=1, max_value=10, value=3, step=1,
        help="Prevents risk stacking by limiting how many trades can be open simultaneously."
    )

    # 🎯 Strike Configuration
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🎯 Strike Configuration")
    sell_offset = st.sidebar.number_input("Sell Leg OTM Offset", min_value=0, max_value=10, value=0, step=1)
    buy_offset = st.sidebar.number_input("Buy Hedge OTM Offset", min_value=1, max_value=15, value=2, step=1)

    # 📅 Date Range
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📅 Date Range")
    default_end = dt.date.today()
    default_start = default_end - timedelta(days=30)
    
    start_date = st.sidebar.date_input("Start Date", value=default_start)
    end_date = st.sidebar.date_input("End Date", value=default_end)

    # -------------------------------------------------------------
    # 🔑 Token Retrieval (Silent & Secure)
    # -------------------------------------------------------------
    if "UPSTOX_ACCESS_TOKEN" in st.secrets:
        upstox_token = st.secrets["UPSTOX_ACCESS_TOKEN"]
    elif "UPSTOX_TOKEN" in st.secrets:
        upstox_token = st.secrets["UPSTOX_TOKEN"]
    else:
        upstox_token = st.session_state.get("upstox_access_token", "")

    # -------------------------------------------------------------
    # 🚀 TABS: Backtester vs Live Scanner
    # -------------------------------------------------------------
    tab1, tab2 = st.tabs(["📊 Historical Backtest", "📡 Live Forward Scanner"])

    # ==========================================
    # TAB 1: HISTORICAL BACKTEST
    # ==========================================
    with tab1:
        log_expander = st.expander("🛠️ Execution Logs", expanded=True)
        log_container = log_expander.container()

        def log_message(msg):
            log_container.write(msg)

        if st.button("🚀 Run Backtest"):
            if not symbols_selected:
                st.error("❌ Please select at least one index to scan.")
                st.stop()

            if not upstox_token:
                st.error("❌ Upstox Access Token is missing from Streamlit secrets.")
                st.stop()

            progress_bar = st.progress(0)
            status_text = st.empty()

            def update_progress(current, total, message):
                progress_bar.progress(current / total)
                status_text.text(f"[{current}/{total}] {message}")

            with st.spinner(f"Running Pure Price Retracement Strategy ({ltf_choice})..."):
                trades_df = process_ema_rsi_guided_strategy(
                    symbols=symbols_selected, start_date=start_date, end_date=end_date,
                    upstox_token=upstox_token, sell_offset=sell_offset, buy_offset=buy_offset,
                    require_color=require_color, require_expansion=require_expansion,
                    require_rsi_sma=require_rsi_sma, require_1h_sma=require_1h_sma,
                    require_adx=require_adx, adx_threshold=adx_threshold, ltf=ltf_choice,
                    max_concurrent_trades=max_concurrent, progress_callback=update_progress, log_func=log_message
                )

            progress_bar.empty()
            status_text.empty()

            if trades_df.empty:
                st.warning("⚠️ No trades were generated for the selected parameters and date range.")
            else:
                st.success(f"✅ Backtest completed! Found {len(trades_df)} trades.")
                
                # Performance Metrics
                total_trades = len(trades_df)
                wins = trades_df[trades_df['PnL (₹)'] > 0]
                win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
                total_pnl = trades_df['PnL (₹)'].sum()
                avg_pnl = trades_df['PnL (₹)'].mean()

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Trades", f"{total_trades}")
                col2.metric("Win Rate", f"{win_rate:.1f}%")
                col3.metric("Total Net PnL", f"₹{total_pnl:,.2f}")
                col4.metric("Avg PnL / Trade", f"₹{avg_pnl:,.2f}")
                
                st.dataframe(trades_df, use_container_width=True)
                st.download_button(label="📥 Download Trades CSV", data=trades_df.to_csv(index=False).encode('utf-8'), file_name=f"{report_name}.csv", mime="text/csv")

    # ==========================================
    # TAB 2: LIVE FORWARD SCANNER
    # ==========================================
    with tab2:
        st.markdown("### 📡 Real-Time Market Scanner")
        st.caption("Fetches live Upstox data to evaluate the exact current market state for forward testing.")
        
        if 'active_forward_trades' not in st.session_state:
            st.session_state['active_forward_trades'] = 0

        col1, col2, col3 = st.columns([1, 1, 2])
        col1.metric("Max Allowed Trades", max_concurrent)
        col2.metric("Active Open Trades", st.session_state['active_forward_trades'])
        
        with col3:
            st.write("Virtual Trade Manager")
            c1, c2 = st.columns(2)
            if c1.button("➕ Open a Trade"):
                st.session_state['active_forward_trades'] += 1
                st.rerun()
            if c2.button("➖ Close a Trade"):
                if st.session_state['active_forward_trades'] > 0:
                    st.session_state['active_forward_trades'] -= 1
                    st.rerun()

        # ---------------------------------------------------------
        # 🔄 AUTO-SCANNER CONTROLS
        # ---------------------------------------------------------
        st.markdown("---")
        
        col_btn, col_tgl = st.columns([1, 3])
        with col_btn:
            manual_scan = st.button("📡 Scan Now")
        with col_tgl:
            auto_scan = st.toggle("🔄 Auto-Scan (Every 60 Seconds)", value=False, help="Turn this on to automatically fetch new data every minute.")
        
        if st.session_state['active_forward_trades'] >= max_concurrent:
            st.error(f"🚨 **MAX CONCURRENCY REACHED ({max_concurrent}).** DO NOT ENTER NEW POSITIONS.")
        
        # Trigger scan if button is clicked OR if auto-scan is toggled ON
        if manual_scan or auto_scan:
            if not upstox_token:
                st.error("❌ Upstox Access Token is missing from Streamlit secrets.")
                st.stop()
                
            if st.session_state['active_forward_trades'] >= max_concurrent:
                st.warning("Scanning for educational purposes, but you are at your concurrency limit.")
            
            with st.spinner("Fetching Live Edges..."):
                scan_df = run_live_scanner(
                    symbols=symbols_selected, upstox_token=upstox_token, 
                    require_color=require_color, require_expansion=require_expansion, 
                    require_rsi_sma=require_rsi_sma, require_1h_sma=require_1h_sma, 
                    require_adx=require_adx, adx_threshold=adx_threshold, ltf=ltf_choice
                )
            
            st.dataframe(scan_df, use_container_width=True)
            st.caption(f"Last scanned at: {dt.datetime.now().strftime('%H:%M:%S')}")
            
            if '✅ ACTIVE SETUP DETECTED' in scan_df['Reason'].values:
                st.success("🔔 **VALID TRADE SETUP DETECTED RIGHT NOW!** Check your Upstox terminal.")
                
            # 🚨 THE AUTO-REFRESH ENGINE
            if auto_scan:
                time.sleep(60) # Wait 60 seconds
                st.rerun()     # Automatically refresh the Streamlit app
