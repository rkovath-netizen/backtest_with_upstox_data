import streamlit as st
import pandas as pd
import io
from datetime import datetime
from common.github_uploader import push_csv_to_github
from .strategy_engine import process_rsi_ubb_strategy

def run_rsi_ubb_app():
    st.title("🐂 15m RSI & UBB Bull Put Spread")
    st.markdown("**(OTM2 Sell & OTM4 Buy | Dynamic Exits)**")

    st.sidebar.header("⚙️ Configuration")
    strategy_name = st.sidebar.text_input("Report Name", value="15m_RSI_Bull_Put")
    
    st.sidebar.info("💡 **Entry Logic:** Upload your Streak CSVs. The engine assumes the CSV already filtered for Monday 9:16 AM entry triggers.")
    
    upstox_token = st.secrets.get("UPSTOX_ACCESS_TOKEN", None)
    github_pat = st.secrets.get("GITHUB_PAT", None)
    github_repo = st.secrets.get("GITHUB_REPO", None)
    github_branch = st.secrets.get("GITHUB_BRANCH", "main")

    log_expander = st.expander("🛠️ Real-Time Execution Logs", expanded=True)
    log_box = log_expander.empty()
    log_messages = []

    def ui_log(msg):
        log_messages.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        log_box.code("\n".join(log_messages[-20:]), language="text")

    uploaded_files = st.file_uploader("Upload Streak Scanner CSVs", accept_multiple_files=True)
    
    if st.button("🚀 Run Dynamic Backtest") and uploaded_files:
        if not upstox_token:
            st.error("❌ UPSTOX_ACCESS_TOKEN missing from Secrets.")
            return

        progress_bar = st.progress(0)
        status_text = st.empty()

        def update_progress(current, total, message):
            progress_bar.progress(int((current / total) * 100))
            status_text.text(f"[{current}/{total}] {message}")

        trades_df = process_rsi_ubb_strategy(uploaded_files, upstox_token, update_progress, ui_log)

        if not trades_df.empty:
            st.success("✅ Backtest Complete!")
            
            total_pnl = trades_df['PnL (₹)'].sum()
            win_rate = (len(trades_df[trades_df['PnL (₹)'] > 0]) / len(trades_df)) * 100
            
            st.metric("💰 Total Net PnL", f"₹ {round(total_pnl, 2)}")
            st.metric("🎯 Win Rate", f"{round(win_rate, 2)}%")

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
