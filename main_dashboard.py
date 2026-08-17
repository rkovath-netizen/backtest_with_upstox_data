import streamlit as st

st.set_page_config(page_title="Upstox Trading Hub", page_icon="⚡", layout="wide")

st.sidebar.title("⚡ Upstox Trading Hub")
st.sidebar.markdown("Select a Strategy Dashboard below:")


app_mode = st.sidebar.selectbox(
    "Select Strategy Dashboard:",
    [
        "📊 Options Straddle Scaffold",
        "🌊 EMA/VWAP Retracement",
        "📈 Comparative Options Backtester",
        "🐂 15m RSI Bull Put Spread",
        "📉 RSI Divergence Engine"    # <--- ADD THIS LINE
    ]
)

# ... your other if statements ...

elif app_mode == "📉 RSI Divergence Engine":
    from strategies.rsi_divergence.app import run_rsi_divergence_app
    run_rsi_divergence_app()

if app_mode == "📈 Comparative Options Backtester":
    from strategies.comparative_options.app import run_comparative_options_app
    run_comparative_options_app()
elif app_mode == "🐂 15m RSI Bull Put Spread":
    from strategies.rsi_ubb_bull_put.app import run_rsi_ubb_app
    run_rsi_ubb_app()
elif app_mode == "🌊 EMA & VWAP Retracement Scanner":
    from strategies.ema_vwap_retracement.app import run_ema_vwap_app
    run_ema_vwap_app()
elif app_mode == "📉 RSI Divergence Engine":
    from strategies.rsi_divergence.app import run_rsi_divergence_app
    run_rsi_divergence_app()
else:
    st.title(app_mode)
    st.info("🚧 Module under development.")
