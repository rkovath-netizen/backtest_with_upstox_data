import streamlit as st
from strategies.backtest_lab.app_backtest import run_ema_rsi_app

st.set_page_config(page_title="R&D Backtest Lab", page_icon="🔬", layout="wide")

# Launch the backtest UI
run_ema_rsi_app()
