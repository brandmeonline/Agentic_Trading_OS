import streamlit as st
import pandas as pd
import time
from datetime import datetime
from core.signal_memory import SignalMemory
from core.multi_agent_fusion_memory import MemoryVotingSwarm

# Set up the Streamlit app layout
st.set_page_config(page_title="Live Signal Stream", layout="wide")
st.title("Real-Time Trading Signal Monitor")

# Create instances of memory and swarm voting engine
memory = SignalMemory()
swarm = MemoryVotingSwarm(memory)

# Auto-refresh interval in seconds
refresh_interval = 10

# Simulated incoming signals (in a real app, this would be streaming from a queue or API)
def generate_live_signals():
    current_time = datetime.now().strftime("%H:%M:%S")
    signals = [
        {"timestamp": current_time, "text": "Bullish breakout on ADA", "domain_conf": {"crypto": 0.78, "macro": 0.68, "equities": 0.60}},
        {"timestamp": current_time, "text": "Bearish momentum on BTC due to CPI", "domain_conf": {"crypto": 0.65, "macro": 0.82, "equities": 0.66}},
        {"timestamp": current_time, "text": "ETH showing strong RSI bounce", "domain_conf": {"crypto": 0.72, "macro": 0.65, "equities": 0.61}}
    ]
    return signals

# Run loop to simulate stream
while True:
    signals = generate_live_signals()

    # Display each signal with memory-enhanced voting decision
    for signal in signals:
        st.markdown(f"### {signal['text']} ({signal['timestamp']})")
        vote_result = swarm.vote_with_memory(signal["domain_conf"], signal["text"])
        st.json(vote_result)

    # Auto-refresh after interval
    st.markdown(f"Auto-refreshing every {refresh_interval} seconds...")
    time.sleep(refresh_interval)
    st.rerun()