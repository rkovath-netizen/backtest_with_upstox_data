import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import pytz
from common.github_uploader import push_csv_to_github
from .strategy_engine import process_ema_rsi_guided_strategy

def run_ema_rsi_app():
    st.title("🎯 Pure Price Retracement Engine")
    st.markdown("**(A/B Test Variant: Spot Data + EMA 50 + RSI Momentum Validation)**")

    app_mode = st.sidebar.radio("Operating Mode", ["Historical Backtest"])
    
    st.sidebar.header("⚙️ Strategy Configuration")
    strategy_name = st.sidebar.text_input("Report Name", value="pure_price_retracement_scan")
    selected_symbols = st.sidebar.multiselect("Indices to Scan:", ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "BANKEX"], default=["NIFTY", "SENSEX"])
    
    st.sidebar.markdown("### 🧲 Entry Conditions")
    require_color = st.sidebar.checkbox("Require Trend Candle Color", value=False)
    require_expansion = st.sidebar.checkbox("Require Body Expansion (>Avg)", value=True, help="Replaces Volume Surge. Requires the trigger candle body to be larger than the recent average.")
    require_rsi_sma = st.sidebar.checkbox("Require 15m RSI > RSI SMA", value=True, help="Replaces OBV. Ensures price momentum is accelerating.")
    require_1h_sma = st.sidebar.checkbox("Require 1h Close > SMA 20", value=True)

    st.sidebar.markdown("### 🎯 Strike Configuration")
    sell_offset = st.sidebar.number_input("Sell Leg OTM Offset", min_value=0, max_value=5, value=2)
    buy_offset = st.sidebar.number_input("Buy Hedge OTM Offset", min_value=1, max_value=10, value=4)
    
    start_date = st.sidebar.date_input("Start Date", datetime.today() - timedelta(days=30))
    end_date = st.sidebar.date_input("End Date", datetime.today())
    
    upstox_token = st.secrets.get("UPSTOX_ACCESS_TOKEN", None)
    github_pat = st.secrets.get("GITHUB_PAT", None)
    github_repo = st.secrets.get("GITHUB_REPO", None)
    github_branch = st.secrets.get("GITHUB_BRANCH", "main")
    
    ist_tz = pytz.timezone('Asia/Kolkata')
    
    if app_mode == "Historical Backtest":
        log_expander = st.expander("🛠️ Execution Logs", expanded=True)
        log_box = log_expander.empty()
        
        if "rsi_guided_logs" not in st.session_state:
            st.session_state["rsi_guided_logs"] = []

        def ui_log(msg):
            st.session_state["rsi_guided_logs"].append(f"[{datetime.now(ist_tz).strftime('%H:%M:%S')}] {msg}")
            log_box.code("\n".join(st.session_state["rsi_guided_logs"][-40:]), language="text")
        
        if st.button("🚀 Run Backtest"):
            st.session_state["rsi_guided_logs"] = []
            if not upstox_token: st.error("❌ UPSTOX_ACCESS_TOKEN missing.") ; return
            if buy_offset <= sell_offset: st.error("❌ Buy Hedge must be further OTM.") ; return

            progress_bar = st.progress(0)
            status_text = st.empty()

            def update_progress(current, total, message):
                progress_bar.progress(int((current / total) * 100))
                status_text.text(f"[{current}/{total}] {message}")

            trades_df = process_ema_rsi_guided_strategy(
                symbols=selected_symbols, start_date=start_date, end_date=end_date, upstox_token=upstox_token,
                sell_offset=sell_offset, buy_offset=buy_offset,
                require_color=require_color, require_expansion=require_expansion, 
                require_rsi_sma=require_rsi_sma, require_1h_sma=require_1h_sma,
                progress_callback=update_progress, log_func=ui_log
            )

            st.session_state["rsi_guided_trades_df"] = trades_df

        if "rsi_guided_trades_df" in st.session_state:
            trades_df = st.session_state["rsi_guided_trades_df"]
            if not trades_df.empty:
                st.success("✅ Backtest Analysis Complete!")
                
                total_pnl = trades_df['PnL (₹)'].sum()
                win_count = len(trades_df[trades_df['PnL (₹)'] > 0])
                total_trades = len(trades_df)
                win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0
                
                st.markdown("### 📊 Strategy Performance Summary")
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Trades", total_trades)
                col1.metric("🎯 Win Rate", f"{round(win_rate, 2)}%")
                col2.metric("💰 Total Net PnL", f"₹ {round(total_pnl, 2):,}")
                col3.metric("⏳ Avg Bars in Trade (3m)", f"{round(trades_df['Bars in Trade'].mean(), 1)}")
                
                pe_trades = len(trades_df[trades_df['Type'] == 'PE_SPREAD'])
                ce_trades = len(trades_df[trades_df['Type'] == 'CE_SPREAD'])
                col4.metric("Spread Split", f"PE: {pe_trades} | CE: {ce_trades}")

                st.markdown("### 📝 Detailed Trade Log")
                st.dataframe(trades_df, width='stretch')

                csv_buffer = trades_df.to_csv(index=False)
                export_filename = f"{strategy_name.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

                if github_pat and github_repo:
                    with st.spinner("Pushing report to GitHub..."):
                        success, path_or_err = push_csv_to_github(csv_buffer, strategy_name, github_pat, github_repo, github_branch)
                        if success: st.success(f"✅ Archiving complete: `{path_or_err}`")
                        else: st.error(f"❌ GitHub push failed! {path_or_err}")
                else:
                    st.download_button("📥 Download Result CSV", csv_buffer, export_filename, "text/csv")
