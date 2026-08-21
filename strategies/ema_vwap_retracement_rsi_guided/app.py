import streamlit as st
import datetime as dt
import pandas as pd
import time
import traceback
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

    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⏱️ Timeframe Settings")
    ltf_choice = st.sidebar.selectbox("Entry Timeframe (LTF)", options=["3min", "5min", "1min", "15min"], index=0)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🚦 Entry Conditions")
    require_color = st.sidebar.checkbox("Require Trend Candle Color", value=False)
    require_expansion = st.sidebar.checkbox("Require Body Expansion (>Avg)", value=False)
    require_rsi_sma = st.sidebar.checkbox("Require 15m RSI > RSI SMA", value=True)
    require_1h_sma = st.sidebar.checkbox("Require 1h Close > SMA 20", value=True)

    require_adx = st.sidebar.checkbox("Require 15m ADX Filter", value=True)
    adx_threshold = st.sidebar.number_input("Min 15m ADX Threshold", min_value=10.0, max_value=50.0, value=20.0, step=1.0)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🛡️ Risk Management")
    max_concurrent = st.sidebar.number_input("Max Concurrent Trades (Per Symbol)", min_value=1, max_value=10, value=3, step=1)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🎯 Strike Configuration")
    sell_offset = st.sidebar.number_input("Sell Leg OTM Offset", min_value=0, max_value=10, value=0, step=1)
    buy_offset = st.sidebar.number_input("Buy Hedge OTM Offset", min_value=1, max_value=15, value=2, step=1)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📅 Date Range")
    default_end = dt.date.today()
    default_start = default_end - timedelta(days=30)
    start_date = st.sidebar.date_input("Start Date", value=default_start)
    end_date = st.sidebar.date_input("End Date", value=default_end)

    # SECRETS INGESTION
    upstox_token = st.secrets.get("UPSTOX_ACCESS_TOKEN", st.secrets.get("UPSTOX_TOKEN", st.session_state.get("upstox_access_token", "")))
    
    github_secrets = {
        "pat": st.secrets.get("GITHUB_PAT", ""),
        "repo": st.secrets.get("GITHUB_REPO", "rkovath-netizen/backtest_with_upstox_data")
    }
    
    email_secrets = {
        "sender": st.secrets.get("EMAIL_SENDER", ""),
        "password": st.secrets.get("EMAIL_PASSWORD", ""),
        "receiver": st.secrets.get("EMAIL_RECEIVER", st.secrets.get("EMAIL_SENDER", ""))
    }

    # -------------------------------------------------------------
    # 🚀 TABS: Backtester vs Live Scanner
    # -------------------------------------------------------------
    tab1, tab2 = st.tabs(["📊 Historical Backtest", "📡 Live Forward Scanner"])

    with tab1:
        log_expander = st.expander("🛠️ Execution Logs", expanded=True)
        log_container = log_expander.container()

        def log_message(msg):
            log_container.write(msg)

        if st.button("🚀 Run Backtest"):
            if not symbols_selected or not upstox_token:
                st.error("❌ Missing Indices or Upstox Token.")
                st.stop()

            progress_bar = st.progress(0)
            status_text = st.empty()

            def update_progress(current, total, message):
                progress_bar.progress(current / total)
                status_text.text(f"[{current}/{total}] {message}")

            try:
                with st.spinner(f"Running Pure Price Retracement Strategy ({ltf_choice})..."):
                    trades_df = process_ema_rsi_guided_strategy(
                        symbols=symbols_selected, start_date=start_date, end_date=end_date,
                        upstox_token=upstox_token, sell_offset=sell_offset, buy_offset=buy_offset,
                        require_color=require_color, require_expansion=require_expansion,
                        require_rsi_sma=require_rsi_sma, require_1h_sma=require_1h_sma,
                        require_adx=require_adx, adx_threshold=adx_threshold, ltf=ltf_choice,
                        max_concurrent_trades=max_concurrent, progress_callback=update_progress, log_func=log_message
                    )
            except Exception as e:
                st.error("🚨 CRITICAL BACKTEST EXCEPTION ENCOUNTERED:")
                st.code(traceback.format_exc())
                return

            progress_bar.empty()
            status_text.empty()

            if trades_df.empty:
                st.warning("⚠️ No trades were generated.")
            else:
                st.success(f"✅ Backtest completed! Found {len(trades_df)} trades.")
                st.dataframe(trades_df, use_container_width=True)
                st.download_button(label="📥 Download Trades CSV", data=trades_df.to_csv(index=False).encode('utf-8'), file_name=f"{report_name}.csv", mime="text/csv")

    with tab2:
        st.markdown("### 📡 Real-Time Market Scanner & Virtual Portfolio")
        st.caption("Auto-captures live setups, builds option spread, pushes to GitHub CSV, and sends Email alerts.")
        
        if 'paper_trades' not in st.session_state:
            st.session_state.paper_trades = {}

        col1, col2, col3 = st.columns([1, 1, 2])
        col1.metric("Max Allowed Trades", max_concurrent)
        col2.metric("Active Virtual Trades", len(st.session_state.paper_trades))
        
        with col3:
            st.write("Virtual Portfolio Manager")
            if st.button("🗑️ Square-Off / Reset All Virtual Trades"):
                st.session_state.paper_trades = {}
                st.rerun()

        st.markdown("---")
        col_btn, col_tgl = st.columns([1, 3])
        with col_btn:
            manual_scan = st.button("📡 Scan Now")
        with col_tgl:
            auto_scan = st.toggle("🔄 Auto-Scan (Every 60 Seconds)", value=False)
        
        if len(st.session_state.paper_trades) >= (max_concurrent * len(symbols_selected)):
            st.warning("⚠️ Max Concurrency Reached. Blocking new entries.")
        
        if manual_scan or auto_scan:
            if not upstox_token:
                st.error("❌ Upstox Access Token is missing from Streamlit secrets.")
                st.stop()
            
            debug_expander = st.expander("🛠️ Deep Debug Console (API Traces)", expanded=True)
            debug_container = debug_expander.container()

            def scanner_debug_log(msg):
                debug_container.write(msg)

            try:
                with st.spinner("Fetching Live Edges & Premiums..."):
                    scan_df = run_live_scanner(
                        symbols=symbols_selected, upstox_token=upstox_token, 
                        require_color=require_color, require_expansion=require_expansion, 
                        require_rsi_sma=require_rsi_sma, require_1h_sma=require_1h_sma, 
                        require_adx=require_adx, adx_threshold=adx_threshold, ltf=ltf_choice,
                        sell_offset=sell_offset, buy_offset=buy_offset,
                        paper_trades=st.session_state.paper_trades,
                        report_name=report_name,
                        github_secrets=github_secrets,
                        email_secrets=email_secrets,
                        debug_func=scanner_debug_log
                    )
                
                st.dataframe(scan_df, use_container_width=True)
                ist_time = dt.datetime.utcnow() + timedelta(hours=5, minutes=30)
                st.caption(f"Last scanned at: {ist_time.strftime('%I:%M:%S %p')} (IST)")
                
                if '🟢 ACTIVE POSITION' in scan_df['Signal / Reason'].values:
                    st.success("🔔 **VIRTUAL PORTFOLIO ACTIVE!**")
                    
            except Exception as e:
                st.error("🚨 LIVE SCANNER EXCEPTION ENCOUNTERED:")
                st.code(traceback.format_exc())

            if auto_scan:
                time.sleep(60)
                st.rerun()
