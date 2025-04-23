# System Architecture

### Agent Stack
- `TradingAgent`: Per-domain learner (crypto, macro, equities)
- `AgentSwarm`: Votes across domains
- `MemoryVotingSwarm`: Boosts confidence with vector memory

### Signal Pipeline
1. Ingest signal (text or stream)
2. Embed into vector space
3. Check memory for similarity
4. Score with Asymmetry Index
5. Adjust confidence via auto-tuner
6. Route to trade/watch/ignore

### Execution
- Wallet integration via Web3 (simulation or live)
- Strategy planner maps optimal futures/options spreads
- Risk manager enforces size, streaks, and max drawdown

### Monitoring
- Live dashboard
- Heatmaps for confidence vs. P&L
- Intent graph: Signal → Agent → Outcome

### Learning Loop
- Trades logged and recursively used to update agents
- Winning signals clustered and synthetically expanded