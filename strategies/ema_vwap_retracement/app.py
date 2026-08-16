import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from common.github_uploader import push_csv_to_github
from .strategy_engine import process_ema_vwap_strategy

def run_ema_vwap_app():
    st.title("🌊 EMA & VWAP Retracement Quant Scanner")
    st.markdown("**(Dual Timeframe: 15m Trend + 3m Retracement + Configurable Spread Pairs + Detailed Analytics)**")

    st.sidebar.header("⚙️ Strategy Configuration")
    strategy_name = st.sidebar.text_input("Report Name", value="ema_vwap_retracement_scan")
    
    st.sidebar.markdown("### 🔍 Scanner Inputs")
    selected_symbols = st.sidebar.multiselect(
        "Indices to Scan Automatically:",
        ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "BANKEX"],
        default=["NIFTY", "SENSEX"]
    )
    
    # Dynamic Strike Selectors
    st.sidebar.markdown("### 🎯 Option Strike Configuration")
    sell_offset_map = {
        "ATM (0 Strikes OTM)": 0,
        "OTM 1 (1 Strike OTM)": 1,
        "OTM 2 (2 Strikes OTM)": 2,
        "OTM 3 (3 Strikes OTM)": 3,
        "OTM 4 (4 Strikes OTM)": 4
    }
    buy_offset_map = {
        "OTM 1 (1 Strike OTM)": 1,
        "OTM 2 (2 Strikes OTM)": 2,
        "OTM 3 (3 Strikes OTM)": 3,
        "OTM 4 (4 Strikes OTM)": 4,
        "OTM 5 (5 Strikes OTM)": 5,
        "OTM 6 (6 Strikes OTM)": 6
    }
    
    selected_sell_label = st.sidebar.selectbox("Sell Leg Offset", list(sell_offset_map.keys()), index=2) # Default OTM2
    selected_buy_label = st.sidebar.selectbox("Buy Hedge Leg Offset", list(buy_offset_map.keys()), index=3) # Default OTM4
    
    sell_offset = sell_offset_map[selected_sell_label]
    buy_offset = buy_offset_map[selected_buy_label]
    
    if buy_offset <= sell_offset:
        st.sidebar.error("⚠️ Buy Hedge Leg must be further OTM than the Sell Leg!")
    
    start_date = st.sidebar.date_input("Start Date", datetime.today() - timedelta(days=30))
    end_date = st.sidebar.date_input("End Date", datetime.today())
    
    upstox_token = st.secrets.get("UPSTOX_ACCESS_TOKEN", None)
    github_pat = st.secrets.get("GITHUB_PAT", None)
    github_repo = st.secrets.get("GITHUB_REPO", None)
    github_branch = st.secrets.get("GITHUB_BRANCH", "main")

    log_expander = st.expander("🛠️ Real-Time Execution Logs", expanded=True)
    log_box = log_expander.empty()
    
    if "ema_vwap_logs" not in st.session_state:
        st.session_state["ema_vwap_logs"] = []

    def ui_log(msg):
        st.session_state["ema_vwap_logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        log_box.code("\n".join(st.session_state["ema_vwap_logs"][-30:]), language="text")
    
    if st.button("🚀 Run EMA/VWAP Backtest"):
        st.session_state["ema_vwap_logs"] = []
        if not upstox_token:
            st.error("❌ UPSTOX_ACCESS_TOKEN missing from Secrets.")
            return
        if not selected_symbols:
            st.error("⚠️ Please select at least one index to scan.")
            return
        if buy_offset <= sell_offset:
            st.error("❌ Buy Hedge must be further OTM than Sell Leg.")
            return

        progress_bar = st.progress(0)
        status_text = st.empty()

        def update_progress(current, total, message):
            progress_bar.progress(int((current / total) * 100))
            status_text.text(f"[{current}/{total}] {message}")

        trades_df = process_ema_vwap_strategy(
            symbols=selected_symbols,
            start_date=start_date,
            end_date=end_date,
            upstox_token=upstox_token,
            sell_offset=sell_offset,
            buy_offset=buy_offset,
            progress_callback=update_progress,
            log_func=ui_log
        )

        st.session_state["ema_vwap_trades_df"] = trades_df

    # Persistent Summary Dashboard
    if "ema_vwap_trades_df" in st.session_state:
        trades_df = st.session_state["ema_vwap_trades_df"]
        if trades_df.empty:
            st.warning("⚠️ No trades found matching the criteria in this date range.")
        else:
            st.success("✅ Backtest Analysis Complete!")
            
            total_pnl = trades_df['PnL (₹)'].sum()
            win_count = len(trades_df[trades_df['PnL (₹)'] > 0])
            total_trades = len(trades_df)
            win_rate = (win_count / total_trades) * 100
            avg_bars = trades_df['Bars in Trade'].mean()
            avg_capital = trades_df['Capital Employed (₹)'].mean()
            avg_pnl_pct = trades_df['PnL (%)'].mean()
            avg_pnl_trade = trades_df['PnL (₹)'].mean()
            
            st.markdown("### 📊 Strategy Performance Summary")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total Trades", total_trades)
            col1.metric("🎯 Win Rate", f"{round(win_rate, 2)}%")
            
            col2.metric("💰 Total Net PnL", f"₹ {round(total_pnl, 2):,}")
            col2.metric("💵 Avg PnL / Trade", f"₹ {round(avg_pnl_trade, 2)}")
            
            col3.metric("⏳ Avg Bars in Trade", f"{round(avg_bars, 1)} bars")
            col3.metric("📈 Avg Return (PnL %)", f"{round(avg_pnl_pct, 2)}%")
            
            col4.metric("🏦 Avg Capital Employed", f"₹ {round(avg_capital, 2):,}")
            pe_trades = len(trades_df[trades_df['Type'] == 'PE_SPREAD'])
            ce_trades = len(trades_df[trades_df['Type'] == 'CE_SPREAD'])
            col4.metric("Spread Split", f"PE: {pe_trades} | CE: {ce_trades}")

            st.markdown("### 📝 Detailed Trade Log")
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
