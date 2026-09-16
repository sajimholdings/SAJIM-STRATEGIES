import streamlit as st
import pandas as pd
import json
import os

st.set_page_config(page_title="Sovereign Sniper Engine v2.0", layout="wide")

st.title("Sovereign Sniper Engine v2.0")

# Read directly from the risk manager log stream
log_file = "ui/telemetry_log.json"
logs = []
if os.path.exists(log_file):
    try:
        with open(log_file, "r") as f:
            logs = json.load(f)
    except Exception as e:
        st.error(f"Error reading logs: {e}")

latest_log = logs[0] if logs else {}
latest_market_data = latest_log.get("MarketData", {})
latest_risk_metrics = latest_log.get("RiskMetrics", {})

# Render Metric Blocks
col1, col2, col3, col4 = st.columns(4)
if latest_risk_metrics:
    equity = latest_risk_metrics.get("equity", 0.0)
    balance = latest_risk_metrics.get("balance", 0.0)
    dd_percent = latest_risk_metrics.get("drawdown_percent", 0.0)
    open_trades = latest_risk_metrics.get("open_trades", 0)
    
    col1.metric("Account Equity", f"${equity:,.2f}", f"DD: {dd_percent:.2f}%" if dd_percent > 0 else "Peak")
    col2.metric("Account Balance", f"${balance:,.2f}")
    col3.metric("Open Trades", f"{open_trades} Active")
else:
    col1.metric("Account Equity", "Live Data Pending...", "+0.0%")
    col2.metric("Account Balance", "Pending...")
    col3.metric("Open Trades", "Pending...")
    
col4.metric("System Phase", latest_log.get("Phase", "SCANNING"))

st.subheader("AI Internal Vision & Constraints")
m_col1, m_col2, m_col3, m_col4 = st.columns(4)
slope = latest_market_data.get('ema_slope_degrees', 0) if latest_market_data else 0
pullbacks = latest_market_data.get("pullback_count", 0) if latest_market_data else 0

m_col1.metric("EMA Slope Angle", f"{slope:.2f}°")
m_col2.metric("Pullback Count", pullbacks)
m_col3.metric("ATR Value", f"{latest_market_data.get('atr_value', 0):.4f}" if latest_market_data else "0")
m_col4.metric("Last Price", f"{latest_market_data.get('last_price', 0):.2f}" if latest_market_data else "0")

# Visual Filters
st.write("**Filter Constraints Pipeline**")
f_col1, f_col2 = st.columns(2)
with f_col1:
    st.write("1. EMA Slope > 20° (Trend Strength)")
    # Normalize slope between 0 and 40 for progress bar
    progress_val = min(max(slope / 40.0, 0.0), 1.0)
    if slope > 20:
        st.success(f"Slope {slope:.1f}° - Passed ✅")
    else:
        st.warning(f"Slope {slope:.1f}° - Failed ❌")
    st.progress(progress_val)

with f_col2:
    st.write("2. Pullbacks between 1 and 3 (Window Open)")
    if 1 <= pullbacks <= 3:
        st.success(f"Pullbacks: {pullbacks} - Passed ✅")
    else:
        st.warning(f"Pullbacks: {pullbacks} - Failed ❌")
    st.progress(min(pullbacks / 4.0, 1.0))


st.info(f"**Gatekeeper Verdict:** {latest_log.get('Action', 'WAITING')} - {latest_log.get('Reason', 'Waiting for logs...')}")

st.subheader("Gatekeeper Telemetry History")
if logs:
    # Flatten the market data into the main dataframe for a clean table
    flat_logs = []
    for log in logs:
        md = log.get("MarketData", {})
        rm = log.get("RiskMetrics", {})
        flat_log = {
            "Timestamp": log.get("Timestamp"),
            "Phase": log.get("Phase"),
            "Action": log.get("Action"),
            "Reason": log.get("Reason"),
            "Eqty": f"${rm.get('equity', 0):.0f}" if rm else "Pending",
            "Trades": str(rm.get("open_trades", "Pending")) if rm else "Pending",
            "Structure": str(md.get("market_structure", "N/A")) if md else "N/A",
            "Slope": f"{md.get('ema_slope_degrees', 0):.1f}" if md else "N/A",
            "Pullbacks": str(md.get("pullback_count", "N/A")) if md else "N/A"
        }
        flat_logs.append(flat_log)
        
    log_data = pd.DataFrame(flat_logs)
    st.dataframe(log_data, width='stretch')
else:
    st.info("No logs generated yet. Ensure main.py is running.")
