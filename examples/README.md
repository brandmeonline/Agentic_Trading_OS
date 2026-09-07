# Examples

Demonstration code. Not part of the running system, not covered by CI, and not
safe to read as a source of truth.

## `streamlit_demo/`

The original Streamlit dashboard, retired in Phase 6 of
`Alpha IO/docs/ULTRA_PLAN.md` in favour of the authenticated Flask app under
`Alpha IO/web/`.

It is kept for reference only. Two things to know before running it:

- `app.py` reads `data/trade_log.csv`, which the current system does not
  produce. Generate one with `Alpha IO/utils/data_generator.py` first.
- `live_stream.py` emits three hardcoded signals on a ten-second loop. Those
  numbers are fabricated. Nothing in it is connected to the ingestion corpus,
  the ledger, or the risk engine.

The live dashboard is `python Alpha IO/run_web.py`, and the market view is at
`/terminal`.
