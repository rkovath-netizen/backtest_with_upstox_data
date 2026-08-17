import streamlit as st
import pandas as pd
import time
import pytz
from datetime import datetime, timedelta
from common.github_uploader import push_csv_to_github
from .strategy_engine import process_ema_vwap_strategy
from .live_engine import run_live_scan_cycle, load_live_log
from common.market_schedule import is_market_open, get_next_market_open

def run_ema_vwap_app():
    st.title("🌊 EMA & VWAP Quant Engine")
    
    app_mode = st.sidebar.radio("Operating Mode", ["Historical Backtest", "Live Forward Tester"])
    
    st.sidebar.header("⚙️ Strategy Configuration")
    selected_symbols = st.sidebar.multiselect("Indices to Scan:", ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "BANKEX"], default=["NIFTY", "SENSEX"])
    
    st.sidebar.markdown("### 🧪 Entry Conditions")
    require_color = st.sidebar.checkbox("Require Trend Candle Color", value=True)
    require_volume = st.sidebar.checkbox("Require Volume Surge", value=True)
    require_obv_sma = st.sidebar.checkbox("Require 15m OBV > SMA 20", value=True)
    require_1h_sma = st.sidebar.checkbox("Require 1h Close > SMA 20", value=True)

    st.sidebar.markdown("### 🎯 Strike Configuration")
    sell_offset = st.sidebar.number_input("Sell Leg OTM Offset", min_value=0, max_value=5, value=2)
    buy_offset = st.sidebar.number_input("Buy Hedge OTM Offset", min_value=1, max_value=10, value=4)
    
    upstox_token = st.secrets.get("UPSTOX_ACCESS_TOKEN", None)
    email_sender = st.secrets.get("EMAIL_SENDER", None)
    email_password = st.secrets.get("EMAIL_PASSWORD", None)
    
    ist_tz = pytz.timezone('Asia/Kolkata')

    if app_mode == "Historical Backtest":
        st.markdown("**(Dual Timeframe: 1h/15m Trend + 3m Retracement + Futures Data + Detailed Analytics)**")
        start_date = st.sidebar.date_input("Start Date", datetime.today() - timedelta(days=30))
        end_date = st.sidebar.date_input("End Date", datetime.today())
        
        log_expander = st.expander("🛠️ Execution Logs", expanded=True)
        log_box = log_expander.empty()
        if "ema_vwap_logs" not in st.session_state: st.session_state["ema_vwap_logs"] = []

        def ui_log(msg):
            st.session_state["ema_vwap_logs"].append(f"[{datetime.now(ist_tz).strftime('%H:%M:%S')}] {msg}")
            log_box.code("\n".join(st.session_state["ema_vwap_logs"][-30:]), language="text")
        
        if st.button("🚀 Run Backtest"):
            st.session_state["ema_vwap_logs"] = []
            if not upstox_token: st.error("❌ UPSTOX_ACCESS_TOKEN missing.") ; return
            if buy_offset <= sell_offset: st.error("❌ Buy Hedge must be further OTM than Sell Leg.") ; return

            progress_bar = st.progress(0)
            status_text = st.empty()
            def update_progress(current, total, message):
                progress_bar.progress(int((current / total) * 100))
                status_text.text(f"[{current}/{total}] {message}")

            trades_df = process_ema_vwap_strategy(
                selected_symbols, start_date, end_date, upstox_token, sell_offset, buy_offset,
                require_color, require_volume, require_obv_sma, require_1h_sma, update_progress, ui_log
            )
            st.session_state["ema_vwap_trades_df"] = trades_df

        if "ema_vwap_trades_df" in st.session_state:
            trades_df = st.session_state["ema_vwap_trades_df"]
            if not trades_df.empty:
                st.success("✅ Backtest Analysis Complete!")
                st.dataframe(trades_df, width='stretch')
                
    else:
        st.markdown("### 🔴 Live Forward Tester (Paper Trading)")
        st.info("When started, the scanner checks the market every 60 seconds, manages active trades, and sends email alerts.")
        
        col1, col2 = st.columns(2)
        if col1.button("▶️ Start Live Scanner", type="primary"):
            if not email_sender or not email_password:
                st.warning("⚠️ Email secrets not configured. Alerts will log to screen but not send to your inbox.")
            st.session_state['live_running'] = True
            st.session_state['sleeping_logged'] = False # Reset the sleep log flag
            
        if col2.button("⏹️ Stop Scanner"):
            st.session_state['live_running'] = False
            
        st.markdown("#### 📂 Live Trade Database")
        live_df = load_live_log()
        if not live_df.empty:
            display_df = live_df.drop(columns=['Legs_JSON'])
            st.dataframe(display_df, width='stretch')
            st.download_button("📥 Download Live Logs", live_df.to_csv(index=False), "live_trade_log.csv", "text/csv")
        else:
            st.write("No live trades logged yet.")
            
        log_box = st.empty()
        if "live_scan_logs" not in st.session_state: st.session_state["live_scan_logs"] = []

        def live_ui_log(msg):
            st.session_state["live_scan_logs"].append(f"[{datetime.now(ist_tz).strftime('%H:%M:%S')}] {msg}")
            log_box.code("\n".join(st.session_state["live_scan_logs"][-20:]), language="text")

        if st.session_state.get('live_running', False):
            market_open, reason = is_market_open()
            
            if market_open:
                st.session_state['sleeping_logged'] = False # Reset flag for next close
                next_check = (datetime.now(ist_tz) + timedelta(seconds=60)).strftime('%I:%M:%S %p')
                with st.spinner(f"Scanner active. Next market check at {next_check}..."):
                    run_live_scan_cycle(
                        symbols=selected_symbols, upstox_token=upstox_token, 
                        sell_offset=sell_offset, buy_offset=buy_offset,
                        require_color=require_color, require_volume=require_volume, 
                        require_obv_sma=require_obv_sma, require_1h_sma=require_1h_sma,
                        email_sender=email_sender, email_password=email_password, log_func=live_ui_log
                    )
                    time.sleep(60)
                    st.rerun()
            else:
                next_open = get_next_market_open()
                
                # 🚨 Log exactly ONE message about entering deep sleep
                if not st.session_state.get('sleeping_logged', False):
                    live_ui_log(f"💤 Market Closed ({reason}). Deep sleep until {next_open.strftime('%d %b, %I:%M %p')}.")
                    st.session_state['sleeping_logged'] = True
                
                with st.spinner(f"Market Closed ({reason}). Deep sleep until {next_open.strftime('%d %b, %I:%M %p')}..."):
                    time.sleep(60) # Silent micro-sleep to keep Server/Stop button alive
                    st.rerun()
