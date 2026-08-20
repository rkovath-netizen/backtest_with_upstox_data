import os
import streamlit as st
import pandas as pd
import datetime as dt
from datetime import timedelta
from strategies.backtest_lab.strategy_engine_backtest import process_ema_rsi_guided_strategy

def run_ema_rsi_app():
    st.title("🔬 R&D Backtest Lab: Configurable Strategies")
    st.markdown("Mix and match filters, test Naked Options vs Spreads, and optimize your dynamic exits.")

    try: upstox_token = st.secrets["UPSTOX_TOKEN"] 
    except KeyError:
        st.error("⚠️ UPSTOX_TOKEN not found in Streamlit secrets.")
        st.stop()

    # 1. Data Config
    st.sidebar.header("1. Data Configuration")
    today = dt.date.today()
    end_date = st.sidebar.date_input("End Date", today)
    start_date = st.sidebar.date_input("Start Date", today - timedelta(days=30))
    symbols = st.sidebar.multiselect("Symbols", ["NIFTY", "SENSEX", "BANKNIFTY", "CRUDEOILM", "NATGASMINI"], default=["NIFTY", "SENSEX"])

    # 2. Strategy Architecture
    st.sidebar.header("2. Strategy Architecture")
    trade_mode = st.sidebar.radio("Execution Mode", ["CREDIT_SPREAD", "NAKED_BUY"])
    ltf = st.sidebar.selectbox("Lower Timeframe (LTF)", ["1min", "3min", "5min", "15min"], index=1)
    
    if trade_mode == "CREDIT_SPREAD":
        sell_offset = st.sidebar.number_input("Sell Leg Offset (from Spot)", value=2, step=1)
        buy_offset = st.sidebar.number_input("Buy Leg Offset (from Spot)", value=4, step=1)
    else:
        st.sidebar.info("Naked Buy defaults to ATM (0 Offset).")
        sell_offset, buy_offset = 0, 0
        
    max_concurrent = st.sidebar.number_input("Max Concurrent Trades", value=3, step=1)

    # 3. Modular Entry Filters
    st.sidebar.header("3. Entry Filters")
    require_high_break = st.sidebar.checkbox("Require High/Low Break (Momentum Confirm)", value=False)
    require_ltf_rsi = st.sidebar.checkbox("Require LTF RSI Alignment", value=False)
    require_rsi_sma = st.sidebar.checkbox("Require 15m RSI > RSI SMA", value=True)
    require_1h_sma = st.sidebar.checkbox("Require 1H Trend Alignment", value=True)
    require_adx = st.sidebar.checkbox("Require ADX Filter", value=True)
    adx_threshold = st.sidebar.number_input("ADX Threshold", value=20.0, step=1.0) if require_adx else 0.0

    # 4. Modular Exit Filters
    st.sidebar.header("4. Dynamic Exit Filters")
    dynamic_exit_ema = st.sidebar.selectbox("Exit Reference Moving Average", ["Trigger EMA", "9 EMA", "21 EMA", "50 EMA"])
    use_2_candle_exit = st.sidebar.checkbox("Require 2 Consecutive Closes (Ignore Whipsaw)", value=True)

    if st.button("🚀 Run Advanced Backtest", use_container_width=True):
        if not symbols: return st.error("Please select at least one symbol.")
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        def ui_log(msg): print(msg)
        def ui_progress(current, total, msg): progress_bar.progress(min(int((current/total)*100), 100)); status_text.text(msg)
        
        try:
            results_df = process_ema_rsi_guided_strategy(
                symbols=symbols, start_date=start_date, end_date=end_date, upstox_token=upstox_token,
                trade_mode=trade_mode, sell_offset=sell_offset, buy_offset=buy_offset,
                require_rsi_sma=require_rsi_sma, require_1h_sma=require_1h_sma, 
                require_adx=require_adx, adx_threshold=adx_threshold,
                require_high_break=require_high_break, require_ltf_rsi=require_ltf_rsi,
                dynamic_exit_ema=dynamic_exit_ema, use_2_candle_exit=use_2_candle_exit,
                ltf=ltf, max_concurrent_trades=max_concurrent,
                progress_callback=ui_progress, log_func=ui_log
            )
            progress_bar.progress(100)
            status_text.text("✅ Backtest Complete!")
            
            if results_df.empty: st.warning("No trades generated.")
            else:
                # Metrics
                st.subheader("📊 Performance Summary")
                total = len(results_df)
                wins = len(results_df[results_df['PnL (₹)'] > 0])
                win_rate = (wins / total) * 100
                st.write(f"**Mode:** {trade_mode} | **Exit EMA:** {dynamic_exit_ema} | **2-Candle Exit:** {use_2_candle_exit}")
                
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
                
                # Dynamic File Naming
                ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = f"BT_{trade_mode}_{dynamic_exit_ema[:2]}_{'2Candle' if use_2_candle_exit else '1Candle'}_{ts}.csv"
                
                csv = results_df.to_csv(index=False).encode('utf-8')
                st.download_button(f"⬇️ Download {file_name}", data=csv, file_name=file_name, mime="text/csv")
                
        except Exception as e:
            st.error(f"Error: {e}")
