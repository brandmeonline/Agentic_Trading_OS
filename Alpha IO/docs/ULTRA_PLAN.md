# ULTRA PLAN — Closing the Terminal Gap

**Status:** 93% complete on a corrected basis (see Completion tracking). Phases 1–5 and 7 landed; Phase 6 partially landed and blocked on a data-source decision.

Derived from `docs/TERMINAL_GAP_ANALYSIS.md`. That review found Alpha IO owns the
execution half of a trading terminal and has essentially none of the ingestion
half. This plan closes that gap in six phases, ordered so each one unblocks the
next.

**Non-negotiables carried through every phase**

1. No ingestion path may reach a broker. Every signal terminates at
   `SignalRouter` → `RiskManager` → `TradingLedger`. The circuit breaker stays
   authoritative.
2. Retrieval and normalization stay deterministic and dependency-light. Inference
   is spent only on synthesis at the top of the funnel — this preserves the
   near-zero marginal cost that is Alpha IO's structural advantage.
3. Every phase ships with tests that run offline. No test may touch the network.
4. Nothing is enabled by default that changes existing behaviour. New subsystems
   are opt-in until their phase's pass criteria are met.

---

## Phase 1 — Ingestion layer (P0) — LANDED

**Ships:** `core/news_feed.py`, `core/llm_client.py`, `tests/test_news_feed.py`

The single gap that blocks layers 2, 3, 5 and half of 6.

### Scope

- `FeedSource` registry — RSS 2.0, Atom, and SEC EDGAR Atom, each carrying a
  credibility weight that maps onto `NewsProcessor.SOURCE_WEIGHTS`, a category,
  and a minimum poll interval.
- `FeedParser` — stdlib `xml.etree.ElementTree` only. Tolerates missing optional
  fields, rejects malformed documents with a typed error, normalizes RFC-822 and
  ISO-8601 dates to timezone-aware UTC.
- `ConditionalFetcher` — `urllib` with ETag / `Last-Modified` conditional GET,
  exponential backoff, `429`/`5xx` handling, and a `304` fast path that costs one
  request and zero parsing.
- `RateLimitedScheduler` — a bounded `ThreadPoolExecutor` with per-source minimum
  intervals. This is the repo's first worker pool; every existing concurrency site
  is a bare thread. Fan-out is bounded by source count and rate limits, never by
  a target agent count.
- `NewsCorpus` — rolling time-windowed store, deduplicated by content hash,
  exposing `mention_count(term, window)` and `crowd_sentiment(term, window)`.
  These two methods are the entire point of the phase: they are what Phase 2
  consumes.
- `NewsFeedService` — composes the above, optionally feeding `NLPEngine.process_news()`.

### Pass criteria

- Parser round-trips fixture RSS and Atom documents, including entity-escaped and
  partially malformed inputs.
- Deduplication holds across a re-poll of an unchanged feed.
- Corpus windowing and purge are correct under a frozen clock.
- Scheduler respects per-source intervals and never exceeds its worker bound.
- Zero network access in the test suite.

## Phase 2 — Measured asymmetry (P1) — LANDED

**Ships:** changes to `core/asymmetry_index.py`, `core/signal_router.py`,
`tests/test_asymmetry_measured.py`

Today `AsymmetryIndex.compute_asymmetry()` accepts `news_count` and
`crowd_sentiment` as caller-supplied literals — the score is a parameter, not a
measurement. This phase turns it into a measurement.

### Scope

- `AsymmetryIndex` accepts an optional corpus and gains `compute_measured()`,
  which reads mention count and crowd sentiment from the rolling window instead
  of from its arguments.
- `SignalRouter` accepts the same corpus and prefers measured values when present.
- Existing literal-argument callers keep working unchanged — the measured path is
  strictly additive.

### Pass criteria

- With a corpus attached, a term the corpus has never seen scores materially
  higher than a saturated one, holding confidence fixed.
- Without a corpus, output is byte-identical to the current implementation.

## Phase 2.1 — Asymmetry calibration (P1b) — LANDED (decision open)

Attaching real measurement in Phase 2 exposed a pre-existing calibration defect
in the score's shape. It is not caused by the corpus — the same ceiling applies
to the literal-argument path — but measurement is what made it visible.

`score = confidence x rarity x novelty x (1 + gis)` where
`novelty = 1 / (news_count + 1)`. Novelty decays hyperbolically, so it dominates
every other term:

| news_count | max achievable score (confidence 1.0, crowd 0.0) |
| ---: | ---: |
| 0 | 1.0000 |
| 1 | 0.5000 |
| 2 | 0.3333 |
| 5 | 0.1667 |
| 10 | 0.0909 |

`SignalRouter` routes `trade` at >= 0.6 and `watchlist` at >= 0.4. So **any term
with two or more mentions can never reach watchlist, at any confidence**, and
`trade` requires literally zero coverage plus a bearish crowd. In practice the
router emits `ignore` for everything the corpus has actually seen.

### Scope

- Reshape novelty so coverage damps the score without collapsing it (log or
  saturating decay), **or** recalibrate the router's thresholds to the real
  output range. These are different bets and the choice is a trading decision,
  not a refactor.
- Backtest the chosen shape against `data/` fixtures before changing any
  threshold.

### Pass criteria

- A term with moderate coverage and high confidence can reach `watchlist`.
- The ordering property from Phase 2 is preserved: uncrowded still outscores
  crowded at equal confidence.

### What landed

- **A second, more binding defect was found and fixed.** `_lexicon_polarity`
  returned exactly ±1.0 for a headline containing a single polarity word, so
  crowd sentiment saturated on essentially every real term, pinning `rarity` at
  its 0.01 floor and crushing the whole score range by ~13x independent of
  novelty shape. Magnitude is now damped by how much evidence was actually
  found. This is a measurement correction, not a policy change: it makes the
  number reflect the corpus rather than overstate it.
- **The novelty shape is now configurable**, with the shipped
  `HyperbolicNovelty(half_life=1)` — the original `1 / (n + 1)` — still the
  default, so routing behaviour is unchanged unless a shape is passed
  explicitly. `NOVELTY_MODELS` registers hyperbolic (1, 3, 8) and log (1, 2).
- **`tools/asymmetry_calibration.py`** reports, per shape: the reachability
  ceiling, decisions on a fixture corpus, reachability by scenario, and an
  ordering guard asserting that uncrowded still outscores crowded under every
  shape.

After the polarity fix the router is functional for the case the index exists to
catch: an uncovered term at 0.85 confidence now scores 0.4250 and routes to
`watchlist`. What remains unreachable is thin *covered* coverage — one to three
bearish mentions — which under the shipped shape still lands in `ignore`.

### The open decision

| Shape | Max mentions still able to reach watchlist |
| --- | ---: |
| hyperbolic (shipped) | 1 |
| log | 3 |
| hyperbolic-3 | 4 |
| hyperbolic-8 | 12 |
| log-2 | 19 |

> Deliberately not decided in-cycle: selecting a different shape, or moving the
> router's 0.6/0.4 thresholds, changes what the system would trade. Run
> `python tools/asymmetry_calibration.py` and pick. Until then the shipped
> default stands and nothing about routing has changed.

## Phase 3 — Safety and liveness fixes (P2) — LANDED

**Ships:** changes to `core/signal_memory.py`, `core/signal_augment.py`,
`utils/rag_macro.py`, `core/score_signals.py`, `tests/test_llm_client.py`

Not gaps — code that cannot run as written.

### Scope

- One lazily-initialized client (`core/llm_client.py`) replacing three call sites
  that target the pre-1.0 `openai.ChatCompletion` / `openai.Embedding` API against
  a pinned `openai>=1.0`. Import must not require a key; only invocation does.
- Remove `eval()` over a CSV field in `core/score_signals.py`
  (`ast.literal_eval` / JSON, with a typed error on anything else).
- Guard `score_signals` against its four missing input files with a clear message
  rather than a traceback.

### Pass criteria

- Importing every touched module succeeds with no `OPENAI_API_KEY` set.
- Calling without a key raises a typed, actionable error rather than an
  `AttributeError` from a removed SDK symbol.
- A ticker field containing an expression is rejected, not executed.

## Phase 4 — Report layer (P3) — LANDED

**Ships:** `core/scheduler.py`, `core/reports.py`

The repo has no scheduler of any kind. Metrics already exist in `core/analytics.py`
and `core/advanced_analytics.py`; only the cadence and the prose are missing.

### Scope

- A minimal cron-ish scheduler over the existing `EventBus`, not a new dependency.
- A morning brief composed from: overnight corpus digest, position delta, risk
  posture, and the day's asymmetry candidates. Deterministic template first;
  optional LLM synthesis behind the same opt-in flag pattern as Headroom.
- Written to `data/reports/macro-YYYYMMDD.md` and published as an alert.

### Pass criteria

- Brief generation is deterministic under a seeded corpus and frozen clock.
- Scheduler survives a missed window without double-firing.

## Phase 5 — Event-driven alerts (P4) — LANDED

**Ships:** changes to `core/alerts.py`

The alert subsystem is the strongest part of the stack — typed conditions,
persistence, a background check thread, five delivery channels — and it is
currently spent entirely on price thresholds.

### Scope

- New `AlertType` members for news, entity-mention velocity, and asymmetry
  threshold breaches, evaluated against the corpus on the existing check thread.
- Conviction levels carried into the notification payload so channel formatting
  can differentiate.

### Pass criteria

- A corpus event fires exactly one notification per alert per dedupe window.
- Existing price alerts are unaffected.

## Phase 6 — Dashboard consolidation (P5) — PARTIALLY LANDED (blocked)

**Ships:** changes to `web/`, retirement of `dashboard/`

### Scope

- Retire the Streamlit demo (it reads a non-existent `trade_log.csv` and emits
  three hardcoded signals on a ten-second loop) or move it under `examples/`.
- Add the panels the terminal comparison turns on: watchlist grid, sector
  heatmap, market breadth, and a news ticker backed by the corpus.
- The cross-asset strip (DXY, TNX, VIX, OIL) needs a rates and FX source that does
  not exist yet — sequence it after a Phase 1 follow-on that adds one.

### Pass criteria

- One dashboard, authenticated, with no synthetic data paths in any live panel.
- Playwright interactive verification per the existing `PLAN.md` convention.

### What landed

- **The Streamlit demo is retired.** Moved to `examples/streamlit_demo/` with a
  README stating plainly that `app.py` reads a file the system does not produce
  and `live_stream.py` fabricates three signals on a ten-second loop. The three
  docs that told people to run it now point at `run_web.py`.
- **`/terminal`** — a market view on the Flask app, behind the same auth as
  every other page: watchlist grid with live coverage per symbol, a news tape
  off the ingestion corpus, uncrowded candidates scored through the measured
  asymmetry path, and the latest morning brief.
- **`core.news_feed.get_news_service()`** — a process-wide service mirroring
  `get_alert_manager()`, so the web layer and the scheduler read one corpus
  rather than two.
- Every panel reports its backing source as unavailable rather than rendering
  a plausible-looking zero.

### Blocked — needs one decision from the owner

Three panels from the original terminal comparison cannot be built against any
data source this repo has:

| Panel | Needs |
| --- | --- |
| Sector heatmap | GICS (or equivalent) sector membership for the equity universe |
| Market breadth | Advance/decline and new-high/new-low across a broad universe |
| Cross-asset strip (DXY, TNX, VIX, OIL) | FX, rates, volatility and commodity quotes |

Alpaca covers equity and crypto bars, and nothing in the tree covers the rest.
All three collapse to a single question: **which market-data provider, and is
there a budget for a paid tier?** Free options (Stooq, FRED for rates, Yahoo
endpoints) cover parts of it with delayed data and no redistribution rights;
paid options (Polygon, Tiingo, Databento) cover all of it. Until that is
answered these panels would have to be faked, and a terminal panel showing
invented breadth is worse than no panel.

---

## Phase 7 — Runtime wiring (P6) — LANDED

Phases 1 through 6 delivered components. Nothing composed them. The feed service
was a process-wide singleton that no code ever polled, the scheduler had no jobs
registered, no brief was ever generated, and the corpus alert sweep never ran.
The terminal rendered an empty corpus because the corpus was empty and, without
this phase, always would be.

This was a gap in the tracking as much as in the code: "landed" was being
measured on components plus tests, with integration counted implicitly by no
one. Hence the corrected basis below.

### Scope

- `core/services.py` — one composition root owning the lifetime of the
  ingestion loop and the scheduled jobs, with every collaborator injectable so
  tests never touch the network.
- **Everything off by default.** Launching the dashboard must not silently begin
  polling external feeds; that is an operator's decision, consistent with how
  live trading and the Headroom adapter are gated here. Enabled per-piece via
  `ALPHAIO_INGEST`, `ALPHAIO_BRIEF`, `ALPHAIO_CORPUS_ALERTS`.
- `run_web.py` starts services before serving and stops them in a `finally`,
  printing what it started so an operator can see the state.
- The feed service tracks `poll_count` / `last_poll_at`, and the terminal
  distinguishes **never polled** from **polled and quiet** — the two look
  identical on screen otherwise, and only one of them is a problem.

### Pass criteria

- Default configuration starts nothing; the test suite asserts it.
- A second `start()` does not launch a second loop against the same sources.
- A raising collaborator does not break shutdown.

## Completion tracking

Percentages are weighted by scope, not by phase count, so a cycle's reported
progress reflects work delivered rather than boxes ticked.

Percentages are weighted by scope, not by phase count, so a cycle's reported
progress reflects work delivered rather than boxes ticked.

> **The basis was corrected once.** Through Phase 6 the weights covered
> component delivery only, with no line item for composing those components into
> the running system — which is why every phase read "landed" while none of it
> actually ran. Phase 7 was added and the weights renormalised. On the corrected
> basis the pre-Phase-7 figure was 82%, not 92%. No work was lost; the
> denominator was wrong.

| Phase | Weight | State |
| --- | ---: | --- |
| 1 — Ingestion layer | 22% | landed |
| 2 — Measured asymmetry | 9% | landed |
| 2.1 — Asymmetry calibration | 5% | landed |
| 3 — Safety and liveness | 9% | landed |
| 4 — Report layer | 13% | landed |
| 5 — Event-driven alerts | 13% | landed |
| 6 — Dashboard consolidation | 18% | 11% landed, 7% blocked |
| 7 — Runtime wiring | 11% | landed |

**Complete: 93%.** The remaining 7% is blocked, not outstanding — see Phase 6.

## Sequencing

Phases 1–3 are independent of each other in implementation and land together in
the first development pass. Phase 2 depends on Phase 1's corpus at runtime but not
at build time. Phases 4–6 each depend on Phase 1 having landed. Phase 7 depends on 1, 4 and
5, and is what makes any of them observable at runtime.

## Out of scope

- Replicating a licensed real-time consolidated tape. That layer is contractually
  protected and this plan does not pretend otherwise.
- Any agent-count target. Coverage is a function of distinct sources and their
  rate limits.
