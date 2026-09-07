# Trading Agentic App

A modular trading research and execution system: signal ingestion, agent
arbitration, risk controls and a broker execution path.

It supports:
- Crypto, macro, and equities domains
- Real-time signal ingestion
- Swarm-based decision arbitration, governed by `core/swarm_arbitration.py`
- Asymmetry scoring to detect rare alpha
- Geographic and global trend overlays
- Dynamic confidence and risk tuning - tightening only

**Live execution supports equities and crypto.** Futures, options and spreads
are research output and are refused for live execution; see the root README
and `MISSING_SEMANTICS` in `core/venue_rules.py`.

**This system is not production ready.** `docs/READINESS_ASSESSMENT.md` and
`docs/ULTRAPLAN_PRODUCTION_HARDENING.md` track what is outstanding, and
`GET /api/ready` answers the question at runtime rather than in prose. The
line that used to sit here compared this system favourably against
institutional trading desks, on no evidence, so it is gone.

## Quick Start
```bash
pip install -r requirements.txt
cp .env.template .env  # Fill in your API keys
python tests/backtest.py  # Run a simulation
python run_web.py  # Dashboard + terminal at /terminal
```

## Key Modules
- `core/agent.py`: Reinforcement learner
- `core/risk.py`: Trade risk and exposure management
- `core/score_signals.py`: Correlates tweet timing with price
- `core/auto_tuner.py`: Learns when to be aggressive
- `core/asymmetry_index.py`: Measures signal uniqueness
- `core/signal_memory.py`: Long-term signal embedding
- `core/precision_trade_planner.py`: Maps alpha to futures/options
- `core/signal_router.py`: Chooses trade, watchlist, or ignore