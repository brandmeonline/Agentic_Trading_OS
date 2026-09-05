Trading Agentic App
This is a modular, autonomous, self-learning trading intelligence system built to detect early alpha signals, execute precision trades, and evolve over time through recursive memory and adaptive agents.

It supports:

Crypto, macro, and equities domains
Real-time signal ingestion
Swarm-based decision arbitration
Asymmetry scoring to detect rare alpha
Execution via futures, options, spreads
Geographic and global trend overlays
Dynamic confidence and risk tuning
Designed to outperform institutional bots by operating higher in the signal funnel at a fraction of the cost.

Credentials
Never commit credentials. The authoritative source is the environment:

  export ALPACA_API_KEY=...
  export ALPACA_API_SECRET=...

For local development only, copy `Alpha IO/config/alpaca_credentials.example.json`
to `Alpha IO/config/alpaca_credentials.json` and fill it in. That path is
gitignored and must stay untracked.

A credential that has ever been committed is compromised. Deleting the file
does not undo the exposure -- the key must be REVOKED and replaced at the
provider. This applies even to paper-only, expired, or seemingly unused keys.
See docs/runbooks/credential-incident.md.

CI fails the build if a credential-shaped secret appears in tracked content
(`Alpha IO/tests/test_secret_hygiene.py`).

Quick Start
pip install -r requirements.txt
cp .env.template .env  # Fill in your API keys
python tests/backtest.py  # Run a simulation
python run_web.py  # Dashboard + terminal at /terminal
Key Modules
core/agent.py: Reinforcement learner
core/risk.py: Trade risk and exposure management
core/score_signals.py: Correlates tweet timing with price
core/auto_tuner.py: Learns when to be aggressive
core/asymmetry_index.py: Measures signal uniqueness
core/signal_memory.py: Long-term signal embedding
core/precision_trade_planner.py: Maps alpha to futures/options
core/signal_router.py: Chooses trade, watchlist, or ignore
core/news_feed.py: RSS/Atom/EDGAR ingestion and the rolling crowding corpus
core/llm_client.py: Single access point for the openai>=1.0 SDK# Agentic_Trading_OS
