import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
import json

st.set_page_config(page_title="ETHUSDT Forward Test", layout="wide")

st.title("ETHUSDT Volatility Breakout - Forward Testing Dashboard")

def get_connection():
    with open("config.json", "r") as f:
        config = json.load(f)
    return sqlite3.connect(config.get("db_name", "trading_data.db"))

conn = get_connection()

# Load Data
try:
    df_trades = pd.read_sql_query("SELECT * FROM trades", conn)
    df_equity = pd.read_sql_query("SELECT * FROM equity_curve", conn)
    sys_status = pd.read_sql_query("SELECT * FROM system_status WHERE id=1", conn).iloc[0]
except Exception as e:
    st.error(f"Waiting for database to initialize... ({e})")
    st.stop()

# Top Level Metrics
col1, col2, col3, col4, col5 = st.columns(5)

current_balance = sys_status["current_balance"]
open_pos = "None" if not sys_status["open_position"] else "Active"
last_update = sys_status["last_heartbeat"]

# Calculate KPIs
total_trades = len(df_trades[df_trades["status"] == "CLOSED"])
if total_trades > 0:
    closed_trades = df_trades[df_trades["status"] == "CLOSED"]
    winners = closed_trades[closed_trades["pnl"] > 0]
    losers = closed_trades[closed_trades["pnl"] < 0]
    
    win_rate = len(winners) / total_trades
    profit_factor = winners["pnl"].sum() / abs(losers["pnl"].sum()) if abs(losers["pnl"].sum()) > 0 else float('inf')
    
    # R-Multiple approximation (simplified as risk was ~1% at entry)
    expectancy_usd = closed_trades["pnl"].mean()
else:
    win_rate = 0.0
    profit_factor = 0.0
    expectancy_usd = 0.0

col1.metric("Current Balance", f"${current_balance:.2f}")
col2.metric("Total Trades (Closed)", total_trades)
col3.metric("Win Rate", f"{win_rate*100:.1f}%")
col4.metric("Profit Factor", f"{profit_factor:.2f}")
col5.metric("Open Position", open_pos)

st.write(f"**Last System Heartbeat:** {last_update}")

st.markdown("---")

# Equity Curve
st.subheader("Equity Curve")
if len(df_equity) > 0:
    df_equity["timestamp"] = pd.to_datetime(df_equity["timestamp"])
    fig = px.line(df_equity, x="timestamp", y="balance", title="Account Balance Over Time")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No equity data yet.")

# Open Trades
st.subheader("Open Positions")
open_trades_df = df_trades[df_trades["status"] == "OPEN"]
if len(open_trades_df) > 0:
    st.dataframe(open_trades_df[["instrument", "direction", "entry_time", "entry_price", "position_size"]])
else:
    st.info("No open positions.")

# Trade History
st.subheader("Trade History")
if total_trades > 0:
    st.dataframe(closed_trades[["instrument", "direction", "entry_time", "exit_time", "entry_price", "exit_price", "pnl", "fees"]].sort_values("exit_time", ascending=False))
else:
    st.info("No completed trades yet.")
