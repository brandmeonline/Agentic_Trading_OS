# ULTRA PLAN — Closing the Terminal Gap

**Status:** 60% complete. Phases 1–4 landed; Phase 2.1, 5 and 6 open.

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

## Phase 2.1 — Asymmetry calibration (P1b) — OPEN

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

> Deliberately not fixed in-cycle: changing either the score shape or the
> thresholds changes what the system would trade. That is the owner's call.

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

## Phase 5 — Event-driven alerts (P4) — OPEN

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

## Phase 6 — Dashboard consolidation (P5) — OPEN

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

---

## Completion tracking

Percentages are weighted by scope, not by phase count, so a cycle's reported
progress reflects work delivered rather than boxes ticked.

| Phase | Weight | State |
| --- | ---: | --- |
| 1 — Ingestion layer | 25% | landed |
| 2 — Measured asymmetry | 10% | landed |
| 2.1 — Asymmetry calibration | 5% | open |
| 3 — Safety and liveness | 10% | landed |
| 4 — Report layer | 15% | landed |
| 5 — Event-driven alerts | 15% | open |
| 6 — Dashboard consolidation | 20% | open |

**Complete: 60%.**

## Sequencing

Phases 1–3 are independent of each other in implementation and land together in
the first development pass. Phase 2 depends on Phase 1's corpus at runtime but not
at build time. Phases 4–6 each depend on Phase 1 having landed.

## Out of scope

- Replicating a licensed real-time consolidated tape. That layer is contractually
  protected and this plan does not pretend otherwise.
- Any agent-count target. Coverage is a function of distinct sources and their
  rate limits.
