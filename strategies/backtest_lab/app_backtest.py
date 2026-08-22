import os
import streamlit as st
import pandas as pd
import datetime as dt
from datetime import timedelta
from strategies.backtest_lab.strategy_engine_backtest import process_ema_rsi_guided_strategy

def upload_results_to_github(df, file_name, gh_token):
    """
    Saves results to local disk and commits directly to GitHub repository.
    Dynamically resolves repository ownership to prevent 404 routing errors.
    """
    csv_data = df.to_csv(index=False)
    
    # 1. Local Disk Persistence (Backup)
    os.makedirs("data_outputs", exist_ok=True)
    local_path = os.path.join("data_outputs", file_name)
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(csv_data)
        
    if not gh_token:
        return False, "GITHUB_TOKEN not found in Streamlit secrets."
        
    try:
        from github import Github, GithubException
        g = Github(gh_token.strip())
        
        target_repo_name = "backtest_with_upstox_data"
        repo = None
        
        # Method A: Dynamic lookup across authenticated repositories
        try:
            for r in g.get_user().get_repos():
                if r.name.lower() == target_repo_name.lower():
                    repo = r
                    break
        except Exception:
            pass
            
        # Method B: Direct fallback path resolution
        if repo is None:
            candidates = [
                f"rkovath-netizen/{target_repo_name}",
                f"rkovath/{target_repo_name}"
            ]
            for cand in candidates:
                try:
                    repo = g.get_repo(cand)
                    if repo:
                        break
                except Exception:
                    continue
                    
        if repo is None:
            return False, f"Repository '{target_repo_name}' not found. Please verify token access permissions."
            
        file_path = f"data_outputs/{file_name}"
        
        # Create or Update handling
        try:
            existing_file = repo.get_contents(file_path, ref="main")
            repo.update_file(
                path=file_path,
                message=f"Automated backtest update: {file_name}",
                content=csv_data,
                sha=existing_file.sha,
                branch="main"
            )
        except GithubException as ge:
            if ge.status == 404:
                repo.create_file(
                    path=file_path,
                    message=f"Automated backtest export: {file_name}",
                    content=csv_data,
                    branch="main"
                )
            else:
                raise ge
                
        return True, f"✅ Successfully saved to GitHub: `{repo.full_name}/{file_path}`"
        
    except Exception as e:
        return False, f"⚠️ GitHub Auto-Save Error: {e}"

def run_ema_rsi_app():
    st.title("🔬 R&D Backtest Lab: Configurable Risk & Schedule")
    st.markdown("Test individual risk variables, day-of-week blocks, and macro trend filters with automated cloud persistence.")

    try:
        upstox_token = st.secrets["UPSTOX_TOKEN"] 
    except KeyError:
        st.error("⚠️ UPSTOX_TOKEN not found in Streamlit secrets.")
        st.stop()

    gh_token = st.secrets.get("GITHUB_TOKEN", None)

    # 1. Data Config
    st.sidebar.header("1. Data Configuration")
    today = dt.date.today()
    end_date = st.sidebar.date_input("End Date", today)
    start_date = st.sidebar.date_input("Start Date", today - timedelta(days=365))
    symbols = st.sidebar.multiselect("Symbols", ["NIFTY", "SENSEX", "BANKNIFTY", "CRUDEOILM", "NATGASMINI"], default=["NIFTY", "SENSEX"])

    # 2. Risk & Target Framework
    st.sidebar.header("2. Risk & Target Rules")
    target_pct = st.sidebar.number_input("Profit Target (% Net Credit Decay)", value=50, min_value=10, max_value=100, step=5) / 100.0
    sl_pct = st.sidebar.number_input("Stop Loss (% Premium Appreciation)", value=40, min_value=10, max_value=150, step=5) / 100.0
    max_concurrent = st.sidebar.number_input("Max Concurrent Trades", value=3, step=1)
    
    # 3. Schedule & Expiry Filter
    st.sidebar.header("3. Day of Week Exclusions")
    blocked_days = st.sidebar.multiselect(
        "Block Trade Entries on Specific Days",
        options=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        default=["Thursday", "Friday"] # Defaults to Test 3 baseline
    )

    # 4. Strategy & Entry Filters
    st.sidebar.header("4. Strategy & Trend Filters")
    ltf = st.sidebar.selectbox("Lower Timeframe (LTF)", ["1min", "3min", "5min", "15min"], index=1)
    sell_offset = st.sidebar.number_input("Sell Leg Offset (from Spot)", value=2, step=1)
    buy_offset = st.sidebar.number_input("Buy Leg Offset (from Spot)", value=4, step=1)
    
    require_1h_rsi = st.sidebar.checkbox("Require 1H RSI Momentum (>50 for Bull, <50 for Bear)", value=True)
    require_1h_sma = st.sidebar.checkbox("Require 1H Trend Alignment (SMA 20)", value=True)
    require_rsi_sma = st.sidebar.checkbox("Require 15m RSI > RSI SMA", value=True)
    require_adx = st.sidebar.checkbox("Require ADX Filter", value=True)
    adx_threshold = st.sidebar.number_input("ADX Threshold", value=20.0, step=1.0) if require_adx else 0.0

    if st.button("🚀 Run Backtest Experiment", use_container_width=True):
        if not symbols: 
            return st.error("Please select at least one symbol.")
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        def ui_log(msg): 
            print(msg)
            
        def ui_progress(current, total, msg): 
            progress_bar.progress(min(int((current/total)*100), 100))
            status_text.text(msg)
        
        try:
            results_df = process_ema_rsi_guided_strategy(
                symbols=symbols, start_date=start_date, end_date=end_date, upstox_token=upstox_token,
                sell_offset=sell_offset, buy_offset=buy_offset,
                target_decay_pct=target_pct,
                sl_appreciation_pct=sl_pct,
                blocked_days=blocked_days,
                require_color=False, require_expansion=False,
                require_rsi_sma=require_rsi_sma, require_1h_sma=require_1h_sma, 
                require_1h_rsi=require_1h_rsi,
                require_adx=require_adx, adx_threshold=adx_threshold,
                ltf=ltf, max_concurrent_trades=max_concurrent,
                progress_callback=ui_progress, log_func=ui_log
            )
            progress_bar.progress(100)
            status_text.text("✅ Backtest Complete!")
            
            if results_df.empty: 
                st.warning("No trades generated with current parameters.")
            else:
                # ==========================================
                # INSTANT REPO & CLOUD PERSISTENCE
                # ==========================================
                ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                blocked_str = "_No" + "-".join([d[:2] for d in blocked_days]) if blocked_days else "_AllDays"
                file_name = f"BT_SL{int(sl_pct*100)}_TGT{int(target_pct*100)}{blocked_str}_1HRSI_{require_1h_rsi}_{ts}.csv"
                
                success, push_msg = upload_results_to_github(results_df, file_name, gh_token)
                if success:
                    st.success(push_msg)
                else:
                    st.warning(push_msg)

                # --- Performance Metrics Display ---
                st.subheader("📊 Performance Summary")
                total = len(results_df)
                wins = len(results_df[results_df['PnL (₹)'] > 0])
                win_rate = (wins / total) * 100 if total > 0 else 0
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Trades", total)
                col2.metric("Win Rate", f"{win_rate:.2f}%")
                col3.metric("Total PnL", f"₹ {results_df['PnL (₹)'].sum():,.2f}")
                col4.metric("Avg Trade PnL", f"₹ {results_df['PnL (₹)'].mean():,.2f}")
                
                st.markdown("---")
                st.subheader("🚪 Exit Analytics")
                exit_counts = results_df['Exit Reason'].value_counts().reset_index()
                exit_counts.columns = ['Exit Reason', 'Count']
                st.dataframe(exit_counts, use_container_width=True, hide_index=True)
                
                st.markdown("---")
                st.subheader("📝 Trade Ledger")
                st.dataframe(results_df, use_container_width=True)
                
                # Instant manual download button
                csv_bytes = results_df.to_csv(index=False).encode('utf-8')
                st.download_button(f"⬇️ Download {file_name}", data=csv_bytes, file_name=file_name, mime="text/csv")
                
        except Exception as e:
            st.error(f"Error executing backtest: {e}")
