import streamlit as st
import pandas as pd
import plotly.express as px
from core.risk import RiskManager

st.set_page_config(page_title="Agent Dashboard", layout="wide")

# Load trade log
df = pd.read_csv("data/trade_log.csv")
df["action_time"] = pd.to_datetime(df["action_time"])
df = df.sort_values("action_time")
df["cumulative_pnl"] = df["pnl"].cumsum()

# Sidebar - metrics
st.sidebar.title("Performance Overview")
st.sidebar.metric("Total Trades", len(df))
st.sidebar.metric("Total P&L (USD)", f"{df['pnl'].sum():.2f}")
st.sidebar.metric("Win Rate", f"{(df['pnl'] > 0).mean():.2%}")

# Dashboard
st.title("Trading Agent Performance Dashboard")

# Cumulative P&L
st.subheader("Cumulative P&L Over Time")
fig1 = px.line(df, x="action_time", y="cumulative_pnl", title="Cumulative P&L")
st.plotly_chart(fig1, use_container_width=True)

# P&L by Asset
st.subheader("P&L by Asset")
fig2 = px.bar(df.groupby("asset")["pnl"].sum().reset_index(), x="asset", y="pnl", title="P&L by Asset")
st.plotly_chart(fig2, use_container_width=True)

# Confidence histogram
st.subheader("Confidence Score Distribution")
fig3 = px.histogram(df, x="confidence", nbins=10, title="Confidence Distribution")
st.plotly_chart(fig3, use_container_width=True)

# Streak & risk summary
st.subheader("Risk Summary")
risk = RiskManager()
for pnl in df["pnl"]:
    risk.update_after_trade("AGG", pnl)
st.json(risk.get_summary())