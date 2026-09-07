import streamlit as st
import pandas as pd
import plotly.express as px
from core.signal_memory import SignalMemory

st.set_page_config(page_title="Signal Insight Overlay", layout="wide")

st.title("Live Signal Heatmap + Memory Resonance")

# Load trade data
df = pd.read_csv("data/trade_log.csv")
df["action_time"] = pd.to_datetime(df["action_time"])
df = df.sort_values("action_time")
df["cumulative_pnl"] = df["pnl"].cumsum()

# Load memory interface
memory = SignalMemory()

# Extract most recent signals
recent_signals = df.tail(15)

# Generate resonance scores
heatmap_data = []
for _, row in recent_signals.iterrows():
    signal = f"{row['asset']} with confidence {row['confidence']}"
    match = memory.search_similar(signal, k=3)
    resonance_score = sum(float(m.get("score", 0)) for m in match) / max(len(match), 1)
    heatmap_data.append({
        "Time": row["action_time"],
        "Asset": row["asset"],
        "Confidence": row["confidence"],
        "PnL": row["pnl"],
        "Resonance": round(resonance_score, 3)
    })

heatmap_df = pd.DataFrame(heatmap_data)

# Plot heatmap
st.subheader("Signal Confidence vs Memory Resonance")
fig = px.density_heatmap(
    heatmap_df,
    x="Confidence",
    y="Resonance",
    z="PnL",
    color_continuous_scale="Viridis",
    title="Signal Strength & Resonance Heatmap"
)
st.plotly_chart(fig, use_container_width=True)

# Show table
st.dataframe(heatmap_df)