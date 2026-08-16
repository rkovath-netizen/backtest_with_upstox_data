import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from common.github_uploader import push_csv_to_github
from .strategy_engine import process_autonomous_rsi_ubb

def run_rsi_ubb_app():
    st.title("🐂 Autonomous Multi-Index Quant Scanner")
    st.markdown("**(Configurable Timeframes + Dynamic RSI & UBB Scanner + OTM2/4 Bull Put Exits)**")

    st.sidebar.header("⚙️ Configuration")
    strategy_name = st.sidebar.text_input("Report Name", value="quant_rsi_ubb_scan")
    
    st.sidebar.markdown("### 🔍 Scanner Inputs")
    selected_symbols = st.sidebar.multiselect(
        "Indices to Scan Automatically:",
        ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "BANKEX"],
        default=["NIFTY", "SENSEX"]
    )
    
    # Timeframe Selector
    timeframe_map = {
        "5 Minutes": "5min",
        "15 Minutes": "15min",
        "30 Minutes": "30min"
    }
    selected_tf_label = st.sidebar.selectbox("Candle Timeframe", list(timeframe_map.keys()), index=0)
    resample_freq = timeframe_map[selected_tf_label]
    
    # RSI Threshold Slider
    rsi_threshold = st.sidebar.slider("RSI Crossing Threshold", min_value=30, max_value=90, value=60, step=1)
    
    start_date = st.sidebar.date_input("Start Date", datetime.today() - timedelta(days=365))
    end_date = st.sidebar.date_input("End Date", datetime.today())
    
    upstox_token = st.secrets.get("UPSTOX_ACCESS_TOKEN", None)
    github_pat = st.secrets.get("GITHUB_PAT", None)
    github_repo = st.secrets.get("GITHUB_REPO", None)
    github_branch = st.secrets.get("GITHUB_BRANCH", "main")

    log_expander = st.expander("🛠️ Real-Time Execution Logs", expanded=True)
    log_box = log_expander.empty()
    log_messages = []

    def ui_log(msg):
        log_messages.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        log_box.code("\n".join(log_messages[-30:]), language="text")
    
    if st.button("🚀 Run Autonomous Backtest"):
        if not upstox_token:
            st.error("❌ UPSTOX_ACCESS_TOKEN missing from Secrets.")
            return
        if not selected_symbols:
            st.error("⚠️ Please select at least one index to scan.")
            return

        progress_bar = st.progress(0)
        status_text = st.empty()

        def update_progress(current, total, message):
            progress_bar.progress(int((current / total) * 100))
            status_text.text(f"[{current}/{total}] {message}")

        trades_df = process_autonomous_rsi_ubb(
            symbols=selected_symbols,
            resample_freq=resample_freq,
            rsi_threshold=rsi_threshold,
            start_date=start_date,
            end_date=end_date,
            upstox_token=upstox_token,
            progress_callback=update_progress,
            log_func=ui_log
        )

        if trades_df.empty:
            st.warning("⚠️ No trades found matching the criteria across selected indices in this date range.")
        else:
            st.success("✅ Autonomous Backtest Complete!")
            
            total_pnl = trades_df['PnL (₹)'].sum()
            win_rate = (len(trades_df[trades_df['PnL (₹)'] > 0]) / len(trades_df)) * 100
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Trades Found", len(trades_df))
            col2.metric("💰 Total Net PnL", f"₹ {round(total_pnl, 2)}")
            col3.metric("🎯 Win Rate", f"{round(win_rate, 2)}%")

            st.dataframe(trades_df, width='stretch')

            csv_buffer = trades_df.to_csv(index=False)
            export_filename = f"{strategy_name.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

            if github_pat and github_repo:
                with st.spinner("Pushing combined report to GitHub `data_outputs/`..."):
                    success, path_or_err = push_csv_to_github(csv_buffer, strategy_name, github_pat, github_repo, github_branch)
                    if success: 
                        st.success(f"✅ Archiving complete: `{path_or_err}`")
                    else: 
                        st.error(f"❌ GitHub push failed! Error Message: {path_or_err}")
            else:
                st.download_button("📥 Download Result CSV", csv_buffer, export_filename, "text/csv")
