Trading Agentic App
A modular trading research and execution system: signal ingestion, agent
arbitration, risk controls and a broker execution path. It learns from its own
history, and every parameter that learning can change is governed - see
`core/tuning_governance.py` and `core/model_governance.py`.

**This system is not production ready.** Readiness is a runtime question, not
a banner: `GET /api/ready` answers it, and `docs/ULTRAPLAN_PRODUCTION_HARDENING.md`
tracks what is outstanding. The description here used to end with a
comparison against institutional trading desks, which was a claim with
nothing behind it, so it is gone.

It supports:

Crypto, macro, and equities domains
Real-time signal ingestion
Swarm-based decision arbitration (governed: see core/swarm_arbitration.py)
Asymmetry scoring to detect rare alpha
Geographic and global trend overlays
Dynamic confidence and risk tuning - tightening only; loosening a limit
requires out-of-sample evidence and a named approver (core/tuning_governance.py)

## What can actually be executed

Live execution supports **equities and crypto** only.

Futures, options and spreads are **research output, not executable**. This
line used to read "Execution via futures, options, spreads"; the code behind
that claim returns hardcoded structures and the system has none of the
semantics they need - no contract multipliers, no expiry or roll handling, no
margin model, no assignment or exercise, no Greeks, no open-interest limits.
`core/precision_trade_planner.py` still produces those structures as research
notes, stamped `executable: False`, and its `plan_for_execution()` raises.
`SafetyConfig` refuses a live configuration with `allow_options` or
`allow_futures` set. The full list of what is missing is
`MISSING_SEMANTICS` in `core/venue_rules.py`.

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
core/precision_trade_planner.py: Maps alpha to futures/options structures as RESEARCH OUTPUT ONLY (not executable; see "What can actually be executed")
core/signal_router.py: Chooses trade, watchlist, or ignore
core/news_feed.py: RSS/Atom/EDGAR ingestion and the rolling crowding corpus
core/llm_client.py: Single access point for the openai>=1.0 SDK# Agentic_Trading_OS
