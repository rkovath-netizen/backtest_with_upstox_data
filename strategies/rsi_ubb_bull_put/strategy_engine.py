
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from common.github_uploader import push_csv_to_github
from .strategy_engine import process_autonomous_rsi_ubb

def run_rsi_ubb_app():
    st.title("🐂 Autonomous 15m RSI & UBB Scanner")
    st.markdown("**(Native Entry Scanning + OTM2/4 Bull Put Exits)**")

    st.sidebar.header("⚙️ Configuration")
    strategy_name = st.sidebar.text_input("Report Name", value="15m_RSI_UBB_Autonomous")
    
    st.sidebar.markdown("### 🔍 Scanner Inputs")
    symbol = st.sidebar.text_input("Symbol (e.g., NIFTY, BANKNIFTY, RELIANCE)", value="NIFTY")
    
    # Default to testing the last 30 days
    start_date = st.sidebar.date_input("Start Date", datetime.today() - timedelta(days=30))
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
        log_box.code("\n".join(log_messages[-25:]), language="text")
    
    if st.button("🚀 Run Autonomous Backtest"):
        if not upstox_token:
            st.error("❌ UPSTOX_ACCESS_TOKEN missing from Secrets.")
            return

        progress_bar = st.progress(0)
        status_text = st.empty()

        def update_progress(current, total, message):
            progress_bar.progress(int((current / total) * 100))
            status_text.text(f"[{current}/{total}] {message}")

        # Pass the date range instead of CSVs
        trades_df = process_autonomous_rsi_ubb(
            symbol=symbol.strip().upper(),
            start_date=start_date,
            end_date=end_date,
            upstox_token=upstox_token,
            progress_callback=update_progress,
            log_func=ui_log
        )

        if trades_df.empty:
            st.warning("⚠️ No trades found matching the RSI/UBB criteria in this date range.")
        else:
            st.success("✅ Autonomous Backtest Complete!")
            
            total_pnl = trades_df['PnL (₹)'].sum()
            win_rate = (len(trades_df[trades_df['PnL (₹)'] > 0]) / len(trades_df)) * 100
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Trades Found", len(trades_df))
            col2.metric("💰 Total Net PnL", f"₹ {round(total_pnl, 2)}")
            col3.metric("🎯 Win Rate", f"{round(win_rate, 2)}%")

            st.dataframe(trades_df, use_container_width=True)

            csv_buffer = trades_df.to_csv(index=False)
            export_filename = f"{strategy_name.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

            if github_pat and github_repo:
                with st.spinner("Pushing to GitHub `data_outputs/`..."):
                    success, path = push_csv_to_github(csv_buffer, strategy_name, github_pat, github_repo, github_branch)
                    if success: st.success(f"✅ Archiving complete: `{path}`")
                    else: st.error("❌ GitHub push failed.")
            else:
                st.download_button("📥 Download Result CSV", csv_buffer, export_filename, "text/csv")
