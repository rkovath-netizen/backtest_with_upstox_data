import pandas as pd

def generate_comparison_metrics(trades_df):
    if trades_df.empty: return pd.DataFrame()
    
    pnl_cols = [c for c in trades_df.columns if c.endswith(" PnL (₹)")]
    metrics_list = []
    total_trades = len(trades_df)
    
    for abs_col in pnl_cols:
        strat_name = abs_col.replace(" PnL (₹)", "")
        pct_col = f"{strat_name} Return (%)"
        
        winning_trades = (trades_df[abs_col] > 0).sum()
        win_rate = round((winning_trades / total_trades) * 100, 2) if total_trades > 0 else 0
        total_pnl = round(trades_df[abs_col].sum(), 2)
        avg_return = round(trades_df[pct_col].mean(), 2) if pct_col in trades_df.columns else 0.0
        
        cum_pnl = trades_df[abs_col].cumsum()
        peak = cum_pnl.cummax()
        drawdown = cum_pnl - peak
        max_dd = round(drawdown.min(), 2)

        metrics_list.append({
            "Strategy Variant": strat_name.replace("Options: ", ""),
            "Win Rate (%)": win_rate,
            "Total PnL (₹)": total_pnl,
            "Avg Return per Trade (%)": avg_return,
            "Max Drawdown (₹)": max_dd
        })

    metrics_df = pd.DataFrame(metrics_list)
    if not metrics_df.empty:
        metrics_df = metrics_df.sort_values(by="Total PnL (₹)", ascending=False).reset_index(drop=True)
    return metrics_df
