# PLAN: Agentic Trading Training App (Vercel) for Cardano + Pyth + Self-Directed Trading

## Goal
Build and validate an **agentic trading training application** deployed on **Vercel**, focused on **Cardano markets** using **Pyth oracle data**, with the ability for a user to make trades manually through the app.

This plan is milestone-driven. Each milestone includes:
1. Build scope
2. Automated tests
3. Playwright interactive verification
4. Explicit pass criteria

> Stop execution once the milestone test suite and Playwright interactive verification pass.

---

## Milestone 1 — Product Skeleton + Vercel Baseline

### Scope
- Create Vercel-compatible app scaffold (frontend + serverless API routes).
- Implement app shell pages:
  - Dashboard
  - Market feed (Cardano focus)
  - Training/simulation workspace
  - Manual trade ticket
- Add health endpoint (`/api/health`) and environment config validation.

### Automated Tests
- **Unit**
  - Config parser validates required env vars and fails gracefully.
  - Route guards for authenticated vs unauthenticated views.
- **API**
  - `/api/health` returns `200` + expected JSON schema.
  - Error handling shape is stable (`code`, `message`, `details?`).
- **Smoke**
  - Build command succeeds.
  - Local start command serves all core routes.

### Playwright Interactive Verification
- Launch app and navigate through all shell pages.
- Validate no fatal console errors.
- Validate route transitions and shell widgets render.
- Capture screenshot artifacts for each core page.

### Pass Criteria
- All tests green.
- Playwright scenario completes and artifacts are generated.
- App can be preview-deployed on Vercel.

---

## Milestone 2 — Cardano Market Data + Pyth Oracle Integration

### Scope
- Implement Cardano market data service abstraction.
- Integrate Pyth oracle price feed ingestion for relevant ADA pairs.
- Normalize feed payloads into internal market snapshot schema.
- Add freshness/latency metadata and stale-data handling.

### Automated Tests
- **Unit**
  - Pyth payload adapter maps source fields to internal schema.
  - Timestamp normalization and stale-window logic are correct.
- **Contract**
  - Oracle response parser tolerates missing optional fields.
  - Validation rejects malformed feed payloads.
- **Resilience**
  - Retry/backoff behavior for transient fetch failures.
  - Circuit-breaker or fallback path invoked after repeated failures.

### Playwright Interactive Verification
- Open Market page and verify live/stubbed Cardano feed tiles update.
- Simulate stale feed and verify visible stale-data warning.
- Trigger ingest error path and verify non-blocking UI degradation.

### Pass Criteria
- Pyth ingestion tests pass with deterministic fixtures.
- UI clearly indicates feed freshness and data source status.
- No blocking runtime exceptions during interactive run.

---

## Milestone 3 — Agentic Training Engine

### Scope
- Add training loop for agent decision simulation on Cardano pairs.
- Build scenario controls:
  - Risk profile
  - Time horizon
  - Position sizing mode
  - Stop-loss/take-profit presets
- Store training runs and performance metrics.

### Automated Tests
- **Unit**
  - Reward function outputs expected scores for canonical scenarios.
  - Risk constraints cap position size and drawdown.
- **Integration**
  - Training run lifecycle: create → execute → persist → retrieve.
  - Metric computations (PnL, Sharpe-like ratio, max drawdown) deterministic with seeded data.
- **Regression**
  - Golden snapshots for known strategy behavior under fixed market fixtures.

### Playwright Interactive Verification
- Start a training run from UI with fixed seed.
- Verify progress stream updates and completion status.
- Inspect results panel and ensure metric cards populate.
- Re-run with same seed and confirm deterministic output where required.

### Pass Criteria
- Training pipeline deterministic under seeded mode.
- Key metrics persist and render accurately in UI.
- Interactive run stable under repeated execution.

---

## Milestone 4 — Manual Trade Workflow (User-Initiated Trades)

### Scope
- Add manual trade ticket for user-initiated buy/sell flows.
- Include pre-trade checks:
  - Input validation
  - Risk limits
  - Balance/position checks
- Implement execution adapter abstraction (paper first, live-toggle guarded).
- Add trade confirmation and audit trail entries.

### Automated Tests
- **Unit**
  - Trade form validation rules (symbol, size, price bounds).
  - Risk engine rejects non-compliant orders.
- **Integration**
  - Paper execution path writes expected order/trade records.
  - Idempotency keys prevent duplicate submissions.
- **Security**
  - Sensitive keys are never exposed client-side.
  - Role/permission checks for live-trade toggle.

### Playwright Interactive Verification
- Submit valid manual paper trade and verify success confirmation.
- Submit invalid trade and verify inline validation + blocked submission.
- Test rapid double-click submit and verify single accepted order.
- Verify trade appears in activity/audit timeline.

### Pass Criteria
- Manual paper trading works end-to-end.
- Invalid and duplicate order protections hold.
- Audit records visible and complete.

---

## Milestone 5 — Observability, Safety Controls, and Deployment Hardening

### Scope
- Add structured logging, request tracing, and metrics dashboards.
- Implement kill-switch and safe-mode for execution components.
- Harden Vercel deployment configuration:
  - Environment segregation (dev/stage/prod)
  - Secret management
  - Build/runtime limits awareness

### Automated Tests
- **Unit/Integration**
  - Safety control toggles correctly disable trade execution paths.
  - Health and readiness endpoints reflect degraded dependencies.
- **E2E**
  - Full user journey: login → market view → training run → manual paper trade.
- **Release**
  - CI pipeline: lint, test, build, and e2e gates must pass before deploy.

### Playwright Interactive Verification
- Execute full journey script with trace recording.
- Trigger safe-mode and verify trade actions are blocked with clear UX messaging.
- Validate no high-severity console/network errors during flow.

### Pass Criteria
- Release gates all pass.
- Safety controls function reliably.
- Vercel preview + production deployment checklist complete.

---

## Cross-Cutting Test Matrix

### Functional
- Cardano feed ingestion correctness
- Pyth price mapping correctness
- Agentic training controls and outputs
- Manual trade submission lifecycle

### Non-Functional
- Determinism under fixed seeds
- Latency budgets for key views and API routes
- Error recovery and stale data handling
- Auditability and traceability of user actions

### Security & Compliance
- Secret handling verification
- AuthZ checks for trade operations
- Immutable audit log behavior

---

## Suggested Tooling
- **Unit/Integration**: Vitest or Jest + Testing Library
- **API contract**: Supertest / Pact-style checks
- **E2E/Interactive**: Playwright (`--ui` for interactive debugging)
- **CI**: GitHub Actions (required checks before Vercel promotion)

---

## Definition of Done (per milestone)
A milestone is done only when all are true:
1. Feature scope implemented.
2. Automated tests pass in CI.
3. Playwright interactive verification passes with artifacts.
4. Known issues triaged with severity and owner.
5. Documentation updated for operation and troubleshooting.

