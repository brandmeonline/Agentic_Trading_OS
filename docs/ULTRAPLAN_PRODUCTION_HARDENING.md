# Agentic_Trading_OS Production Hardening ULTRAPLAN

**Repository:** `brandmeonline/Agentic_Trading_OS`  
**Primary application:** `Alpha IO/`  
**Purpose:** Exhaustive remediation specification for Claude Code / coding agents to harden Agentic_Trading_OS from an ambitious agentic trading prototype into a safely gated, auditable, reconciled, testable trading operating system.  
**Baseline audit:** Adversarial static review performed against `main` at commit `3d5f7b46b24fae1e03f715da9555d3950d396ef6`.  
**Authority:** On safety questions, this document overrides stale README claims, comments, legacy defaults, convenience fallbacks, and tests that preserve unsafe behavior. Update those artifacts as part of the same remediation.

---

# 0. MISSION

The governing invariant is:

> **Agentic_Trading_OS must never add or retain real-money risk when execution state, broker state, portfolio state, market-data state, persistence state, risk state, credential state, model authority, or operator authorization is materially uncertain.**

The system must prefer **safe unavailability over unsafe continuity**.

Production interpretation:

- unknown broker/order state => no new risk;
- stale/invalid market data => no new risk;
- persistence uncertainty => no new risk;
- reconciliation mismatch => no new risk;
- model/agent disagreement => deterministic risk policy wins;
- credentials prove capability, not permission;
- live execution requires explicit authorization and current evidence;
- broker truth is authoritative over local bookkeeping;
- every live order is durable before submission, idempotent across retries, lifecycle-tracked, and reconciled;
- risk-reducing actions are privileged only when mathematically proven to reduce absolute exposure;
- public/demo/dashboard surfaces cannot alter trading authority without authenticated, audited control-plane policy;
- autonomous learning/refinement cannot silently promote itself to live capital.

---

# 1. IMMEDIATE SECURITY HOLD — DO THIS BEFORE ALL OTHER WORK

## ATOS-P0-SEC-001 — Revoke and remove committed exchange credentials

### Finding

A credential file is committed in the public repository under:

`Alpha IO/config/alpaca_credentials.json`

Treat any credential ever committed to public Git history as **compromised**, regardless of whether it is paper-only, expired, currently unused, or later deleted.

### OWNER ACTION REQUIRED

Claude cannot safely prove/revoke credentials merely by editing source. The owner must:

1. revoke/delete the exposed Alpaca API key in the Alpaca account;
2. generate a new key only if still needed;
3. store the replacement outside Git using GitHub Actions secrets, environment variables, OS keyring, or a production secret manager;
4. never paste replacement credentials into Claude chat, issues, commits, PRs, logs, screenshots, or fixtures.

### Claude repository actions

Claude MUST:

- delete `Alpha IO/config/alpaca_credentials.json` from the current tree;
- ensure `.gitignore` excludes all real credential files and encrypted/local credential artifacts;
- replace the committed file, if documentation needs an example, with a clearly fake `alpaca_credentials.example.json` containing impossible/non-secret placeholders;
- scan the repository for credential-like literals, API secrets, passphrases, private keys, bearer tokens, JWTs, webhook secrets, database passwords, and `.env` files;
- add secret scanning to CI (e.g. gitleaks/trufflehog or equivalent deterministic scanner);
- add tests that sample/example credential files contain no plausible secrets;
- update docs to state that committed credentials must be rotated, not merely deleted.

### Git history

Do not assume deletion from `main` removes exposure from Git history. After revocation, the owner may elect to rewrite public Git history if desired. Rotation is mandatory; history rewriting alone is insufficient.

### Gate

**No live-capital work proceeds until credential revocation is confirmed by the owner and the current tree is clean.**

---

# 2. CLAUDE EXECUTION CONTRACT

Claude Code must execute this plan as a repeated evidence loop.

## 2.1 Mandatory issue loop

For every issue:

1. **READ** all relevant source, tests, config, call sites, docs, and broker adapter behavior before editing.
2. Identify the capital/safety invariant being protected.
3. Build or extend an adversarial test that demonstrates the failure whenever practical.
4. Implement a coherent fix at the correct architectural boundary, not a superficial patch.
5. Run targeted tests.
6. Run formatting/lint/type/security checks relevant to the change.
7. Run the full regression suite.
8. Re-read the diff adversarially for ways actual broker exposure can exceed tracked/allowed exposure.
9. Update stale docs/config/UI claims.
10. Commit a checkpoint with the issue ID.
11. Advance only if acceptance criteria are objectively satisfied.

## 2.2 Claude MUST NOT

- weaken/delete a safety assertion to preserve old behavior;
- increase risk limits/capital limits;
- enable live execution by default;
- convert an UNKNOWN state into FILLED, CANCELED, FLAT, HEALTHY, or SAFE;
- retry a timed-out order with a new client order ID before broker reconciliation;
- treat a broker create-order response as proof of final fill;
- treat cancel-request acknowledgement as terminal cancellation;
- start live trading with empty/flat local state after failed recovery;
- continue adding risk after critical persistence failure;
- let an RL/LLM/agent change hard risk caps or activation state;
- allow UI/API requests to mutate live trading without authenticated and audited authorization;
- store real exchange credentials in repository files;
- claim production readiness based on module import success, backtests, or paper-only smoke tests;
- silently use float rounding as financial reconciliation;
- improvise exchange semantics without verifying official broker behavior.

## 2.3 Stop and report instead of improvising when

- broker documentation is ambiguous on lifecycle/idempotency semantics;
- a schema migration is required to preserve live-order durability;
- current abstractions cannot distinguish paper from real side effects safely;
- external account state is required to validate a change and no deterministic test double can substitute;
- an agentic/RL feature has direct order authority and safe separation requires an architectural decision.

Report: blocker, violated invariant, options, safest recommendation, owner decision required.

---

# 3. SYSTEM-WIDE DEFINITION OF DONE

The ULTRAPLAN is complete only when all are true:

- [ ] No real credential remains committed in the repository.
- [ ] Secret scanning runs in CI.
- [ ] Live execution is explicit and fail-closed.
- [ ] A durable order intent exists before any broker side effect.
- [ ] Broker client-order IDs are stable idempotency keys across timeout/restart.
- [ ] Partial fills retain reservation for still-live remainder.
- [ ] Cancellation is terminal only after broker confirmation.
- [ ] Broker positions/open orders/fills/cash reconcile against local state before live activation.
- [ ] Reconciliation runs continuously during live operation.
- [ ] Restart cannot assume flat state after recovery failure.
- [ ] Critical database/ledger failure freezes new risk.
- [ ] Risk calculations use broker-authoritative exposure plus outstanding orders.
- [ ] Daily and total drawdown state survives restart.
- [ ] SELL/reduce privilege cannot create/increase a short unintentionally.
- [ ] Market-data integrity includes sequence/timestamp/gap/duplicate/invalid-value controls.
- [ ] RL/LLM/swarm/auto-tuning output cannot bypass deterministic pre-trade risk.
- [ ] Adaptive models use explicit candidate/shadow/promoted governance.
- [ ] Backtests avoid common leakage/lookahead/data-snooping errors and include execution costs.
- [ ] Financial execution/ledger quantities use Decimal/fixed-point boundaries.
- [ ] API/control plane is authenticated, authorized, rate-limited, auditable, and network-hardened.
- [ ] Dashboard always identifies PAPER/LIVE, source trust, reconciliation, broker connectivity, and capital at risk.
- [ ] Production UI never silently substitutes demo data for live telemetry.
- [ ] CI runs full tests rather than a hand-picked subset only.
- [ ] CI adds lint, type checking, dependency audit, static security scan, secret scan, frontend checks, and adversarial tests.
- [ ] Deployment readiness differs from process liveness.
- [ ] Supervised capital is introduced only through a persisted promotion ladder.
- [ ] Final adversarial review answers all closing questions with “no” or evidence-backed safe behavior.

---

# 4. PRIORITY MAP

## P0 — BLOCK LIVE CAPITAL

- ATOS-P0-SEC-001 exposed credential remediation.
- ATOS-P0-EXEC-001 live order lifecycle state machine.
- ATOS-P0-EXEC-002 durable pre-submit order-intent WAL.
- ATOS-P0-EXEC-003 idempotency / timeout-after-accept protection.
- ATOS-P0-EXEC-004 cancel/fill race terminal confirmation.
- ATOS-P0-REC-001 fail-closed restart/recovery.
- ATOS-P0-REC-002 broker reconciliation before live activation.
- ATOS-P0-AUTH-001 explicit live activation authority.

## P1 — REQUIRED BEFORE SUPERVISED REAL-MONEY CANARY

- ATOS-P1-RISK-001 broker-authoritative exposure and reservations.
- ATOS-P1-RISK-002 durable daily/total drawdown controls.
- ATOS-P1-RISK-003 prove risk-reducing orders.
- ATOS-P1-PERSIST-001 live persistence failure policy.
- ATOS-P1-DATA-001 market-data integrity.
- ATOS-P1-NUM-001 Decimal/fixed-point execution ledger.
- ATOS-P1-AGENT-001 deterministic authority boundary for agents/RL/LLM.
- ATOS-P1-CONFIG-001 one source of truth for mode/risk/broker config.

## P2 — REQUIRED BEFORE UNATTENDED LIVE

- ATOS-P2-REC-001 recurring broker reconciliation.
- ATOS-P2-FAULT-001 adversarial fake broker.
- ATOS-P2-FAULT-002 crash/replay testing.
- ATOS-P2-OPS-001 external alerts/escalation.
- ATOS-P2-API-001 control-plane authentication/RBAC.
- ATOS-P2-UI-001 dashboard trust hierarchy.
- ATOS-P2-DEPLOY-001 readiness/runtime health.
- ATOS-P2-CI-001 institutional CI gates.

## P3 — STRATEGY / AGENTIC RESEARCH HARDENING

- ATOS-P3-BT-001 backtest correctness/leakage audit.
- ATOS-P3-ML-001 RL training/evaluation separation.
- ATOS-P3-AGENT-001 swarm arbitration governance.
- ATOS-P3-TUNE-001 auto-tuner champion/challenger promotion.
- ATOS-P3-EXEC-001 execution realism and market impact.
- ATOS-P3-CAP-001 capital promotion ladder.

---

# 5. PHASE A — LIVE ORDER LIFECYCLE

## ATOS-P0-EXEC-001 — Replace response mirroring with a real order state machine

### Audit target

Start with:

- `Alpha IO/core/execution.py`
- `Alpha IO/core/alpaca_connector.py`
- `Alpha IO/core/exchange_connectors.py`
- `Alpha IO/core/ledger.py`
- `Alpha IO/core/orchestrator.py`

### Current concern

The execution layer normalizes broker responses into local statuses and can mark exceptions during submission as rejected. A network exception does **not** prove the broker rejected the order; the broker may have accepted it before the response was lost.

### Required order states

At minimum:

- INTENT_CREATED
- INTENT_PERSISTED
- SUBMITTING
- ACKNOWLEDGED
- OPEN
- PARTIALLY_FILLED
- CANCEL_REQUESTED
- FILLED
- CANCELED
- REJECTED
- EXPIRED
- UNKNOWN
- RECONCILIATION_REQUIRED

### Core invariant

> Every economically live remainder remains represented as exposure/reservation until broker truth proves terminal state.

### Implementation requirements

- distinguish transport failure from broker rejection;
- never map generic exception to REJECTED if acceptance is uncertain;
- retain broker order ID + client order ID;
- track cumulative filled quantity, average price, fees, remaining quantity;
- use broker status polling/stream updates to progress lifecycle;
- preserve UNKNOWN when broker state cannot be established;
- make order status transitions monotonic/validated; reject impossible backward transitions except reconciliation correction with explicit event.

### Tests

- immediate full fill;
- accepted/open;
- partial then full;
- partial then canceled;
- timeout before broker acceptance;
- timeout after broker acceptance;
- malformed broker response;
- unknown broker status;
- duplicate lifecycle event;
- out-of-order lifecycle event;
- broker reports fill greater than requested quantity => mismatch/freeze;
- broker says rejected after local partial fill => reconciliation error unless broker semantics justify it.

---

## ATOS-P0-EXEC-002 — Durable pre-submit WAL

### Invariant

> The ledger knows a real order may exist before the network call can create one.

Before broker submission, atomically/durably persist:

- internal order ID;
- stable client order ID;
- session ID;
- strategy/agent/signal provenance;
- instrument;
- side;
- quantity/notional;
- order type/prices;
- expected max exposure delta;
- risk approval snapshot/hash;
- timestamp;
- status `INTENT_PERSISTED`.

If persistence fails in live mode, do not submit.

Persist every lifecycle transition.

### Crash boundaries to test

1. crash before intent persist;
2. crash after intent persist before network call;
3. crash after broker accepts but before response;
4. crash after ack before ledger transition;
5. crash after partial fill;
6. crash during cancel race.

Recovery must reconcile unresolved intents by client order ID.

---

## ATOS-P0-EXEC-003 — Idempotency and timeout-after-accept

### Invariant

> Network retry cannot create duplicate economic exposure.

Requirements:

- generate client order ID before WAL;
- preserve same ID across retries and restart;
- on timeout, query broker by client order ID before any re-submit;
- if broker already has matching order, attach to it;
- if same client ID has conflicting economics, freeze/reconciliation mismatch;
- persist retry attempts and result;
- do not use short IDs if collision probability is not negligible; prefer UUID/ULID/full stable identifier.

### Tests

- response lost after broker accept;
- restart then lookup existing order;
- duplicate retry;
- conflicting duplicate client ID;
- concurrent submissions cannot reuse ID.

---

## ATOS-P0-EXEC-004 — Cancel/fill race

### Invariant

> cancel requested != canceled.

After cancel request:

- broker terminal state must be confirmed;
- fills occurring after cancel request must still be applied;
- reservation remains until terminal state;
- timeout => UNKNOWN/RECONCILIATION_REQUIRED;
- risk engine blocks new acquisition while material ambiguity remains.

Tests: fill-after-cancel, partial-after-cancel, cancel reject, cancel timeout, duplicate cancel.

---

# 6. PHASE B — RECOVERY AND BROKER RECONCILIATION

## ATOS-P0-REC-001 — Fail-closed live restart

### Invariant

> A restart cannot manufacture a clean/flat portfolio from missing state.

Introduce explicit runtime states, e.g.:

- PAPER
- PAPER_LIVE_DATA
- LIVE_ARMED
- LIVE_RECONCILING
- LIVE_ACTIVE
- FROZEN
- RECOVERY_REQUIRED
- HALTED

### Live startup sequence

1. load validated config;
2. verify explicit live authorization;
3. initialize durable ledger/database;
4. replay local state;
5. enumerate unresolved order intents;
6. authenticate broker;
7. fetch account/cash/buying power;
8. fetch positions;
9. fetch open orders;
10. fetch recent fills/orders necessary to close unresolved intents;
11. reconcile local vs broker;
12. restore durable risk anchors;
13. validate market-data health;
14. validate capital tier;
15. only then transition LIVE_RECONCILING -> LIVE_ACTIVE.

Any uncertainty => remain FROZEN/RECOVERY_REQUIRED.

---

## ATOS-P0-REC-002 — Reconciliation engine

### Recommended module

`Alpha IO/core/reconciliation.py`

### Normalize broker snapshot

Include:

- account ID fingerprint (non-secret);
- timestamp;
- cash/equity/buying power;
- positions by instrument;
- open orders;
- recent fills;
- broker order IDs;
- client order IDs;
- fees where available;
- completeness/freshness flags.

### Local snapshot

Include:

- positions;
- open/unknown orders;
- reserved quantity/notional;
- cash/equity expectation;
- fills;
- fees;
- strategy attribution;
- risk state.

### Mismatch classes

- UNKNOWN_BROKER_ORDER
- MISSING_BROKER_ORDER
- POSITION_MISMATCH
- CASH_MISMATCH
- FILL_MISMATCH
- QUANTITY_MISMATCH
- DUPLICATE_CLIENT_ID
- CAPITAL_LIMIT_BREACH
- ACCOUNT_ID_MISMATCH
- INCOMPLETE_BROKER_SNAPSHOT

Mismatch/unknown => no new risk.

Never silently overwrite local truth with broker truth without persisting a reconciliation/correction event.

---

# 7. PHASE C — EXPLICIT LIVE AUTHORITY

## ATOS-P0-AUTH-001

### Concern

`activate.py` exposes interactive live setup/start behavior and the orchestrator can choose live broker endpoints based on mode. “Production-ready” claims are not authorization controls.

### Invariant

> A caller cannot enter LIVE by passing `mode="live"` alone.

### Required authorization gate

Live requires all of:

- explicit live CLI flag or distinct command;
- explicit human risk acknowledgement;
- a production environment designation;
- non-expired valid live credential reference;
- credential source approved (never repository file);
- durable database healthy;
- successful replay;
- broker reconciliation MATCHED;
- market data healthy;
- current strategy/config hash has valid promotion evidence;
- hard capital tier present and >0;
- no active risk trip;
- no unresolved order state;
- account fingerprint matches expected broker account;
- session ID persisted.

### Prohibit

- implicit live because `mode` string is live;
- live activation because credentials exist;
- live fallback from paper/testnet connection failure;
- environment auto-detection that can point production accidentally.

Update `activate.py`, orchestrator config, README, tests, and UI.

---

# 8. PHASE D — RISK ENGINE AS HARD BOUNDARY

## ATOS-P1-RISK-001 — Broker-authoritative exposure

### Concern

`RiskManager` maintains in-memory `asset_exposure`, capital, positions, daily P&L and uses floats. Local reservations are useful but cannot be authoritative in live mode.

### Invariant

> Pre-trade risk uses conservative reconciled broker exposure plus outstanding order exposure.

For spot/equities:

`effective_exposure = marked_broker_position_exposure + outstanding_acquisition_order_exposure`

For derivatives/options/futures, extend with product-aware delta/notional/margin risk; do not reuse simplistic cash equity rules.

### Requirements

- distinguish reserved order exposure from filled position exposure;
- track per-asset and portfolio gross/net exposure;
- include open orders;
- detect broker exposure above local configured cap;
- do not clamp/hide cap breaches;
- freeze acquisition on mismatch/breach;
- use actual broker equity for percentage limits in live mode, not only configured initial capital.

### Tests

- manual broker-side position appears;
- open order pushes effective exposure to cap;
- local says $X but broker says greater;
- price move causes concentration breach;
- concurrent orders race for same remaining capacity;
- position/buying-power data stale => no new risk.

---

## ATOS-P1-RISK-002 — Durable loss/drawdown controls

### Concern

Daily reset and P&L/risk state are in-memory. Restart can alter risk context if not durably restored.

Persist:

- UTC/session trading date;
- day opening broker equity;
- high-water equity;
- daily realized/unrealized loss policy state;
- total drawdown anchor;
- loss-streak state if it affects permission;
- active risk trips.

Restart during same day must preserve original anchor/trip.

Prefer broker equity snapshots for live truth.

---

## ATOS-P1-RISK-003 — Prove “risk reducing” orders

SELL/CLOSE privilege only if deterministic policy proves:

`abs(post_trade_exposure) <= abs(pre_trade_exposure)`

Requirements:

- account for outstanding close orders to prevent overselling;
- equities/spot: sell quantity cannot exceed available owned quantity unless shorting is explicitly enabled and separately risk-governed;
- options/futures: closing/reduce-only semantics must be product-aware;
- use broker reduce-only flags where supported;
- stale position state cannot grant risk-reducing privilege.

---

# 9. PHASE E — PERSISTENCE FAILURE SEMANTICS

## ATOS-P1-PERSIST-001

Classify writes:

### Critical

- order intent;
- lifecycle transition;
- fill;
- reservation change;
- reconciliation report/state;
- live session activation/deactivation;
- capital tier;
- risk trip;
- daily-loss anchor.

### Non-critical

- optional analytics;
- dashboard cache;
- research telemetry;
- cosmetic history.

Live critical write failure => FROZEN/RECOVERY_REQUIRED, no new risk.

Paper may degrade with warning.

Do not use broad exception swallowing around critical live writes.

---

# 10. PHASE F — MARKET DATA INTEGRITY

## ATOS-P1-DATA-001

Audit:

- `Alpha IO/core/live_data.py`
- `Alpha IO/core/market_data.py`
- exchange websocket/REST clients;
- feature engine assumptions;
- strategy consumers.

Validate:

- timestamp monotonicity;
- sequence monotonicity where provided;
- duplicate updates;
- missing sequence/gap;
- stale repeated payload;
- zero/negative price;
- OHLC consistency;
- negative/invalid volume;
- bid >= ask/crossed book;
- excessive spread;
- venue/local clock skew;
- stale cache despite receiving heartbeats;
- reconnect resubscription correctness;
- REST-vs-stream divergence where feasible;
- corporate actions for equities where relevant;
- market session/open/close/halts for equities;
- symbol normalization (`BTC/USD` vs `BTC/USDT`, equities, options/futures identifiers).

Per-symbol state:

- HEALTHY
- DEGRADED
- STALE
- GAP_DETECTED
- INVALID
- HALTED
- UNKNOWN

Only HEALTHY can initiate new exposure.

---

# 11. PHASE G — FINANCIAL NUMERICS

## ATOS-P1-NUM-001

Use Decimal or fixed-point at capital-affecting boundaries:

- cash;
- notional;
- order quantity where venue permits decimal quantity;
- price in ledger;
- fees;
- realized P&L;
- cost basis;
- reservations;
- exposure;
- reconciliation comparisons.

Analytics, numpy, ML features can remain float.

Rules:

- never construct Decimal directly from uncontrolled binary float;
- quantize to broker/instrument increments;
- serialize deterministic strings/integers;
- define tolerance by venue grid, not arbitrary epsilon.

---

# 12. PHASE H — AGENT / RL / LLM AUTHORITY BOUNDARY

## ATOS-P1-AGENT-001

### High-level concern

The repository explicitly advertises autonomous/self-learning behavior, swarm decision arbitration, RL, auto-tuning, AI assistance, and dynamic confidence/risk tuning. These are research/decision components, not safe execution authorities.

### Architectural invariant

> All probabilistic/agentic output must pass through one deterministic pre-trade policy boundary that cannot be bypassed by any agent.

### Required flow

`data -> features -> models/agents -> normalized TradeProposal -> deterministic validation -> portfolio/risk -> execution intent -> broker`

No agent may call broker connector directly.

### TradeProposal schema

Include:

- proposal ID;
- model/agent ID and version;
- strategy ID;
- timestamp;
- instrument;
- direction;
- desired size or target exposure;
- confidence;
- rationale/features provenance;
- expiry/time horizon;
- training/evaluation version;
- whether proposal is shadow/paper/live eligible.

### Hard policy

Risk engine can reduce/zero a proposal. Agent cannot override.

### Search requirement

Claude must statically search all code paths for direct calls to:

- `place_order`
- `submit_order`
- exchange connector order endpoints
- broker client order methods

and ensure live order side effects are reachable only through the hardened execution boundary.

---

# 13. PHASE I — CONFIGURATION AUTHORITY

## ATOS-P1-CONFIG-001

The codebase has multiple config surfaces (`config.py`, config manager, orchestrator config, credential environments, runtime args). Consolidate safety-critical values into a typed validated source of truth.

Safety-critical config hash must include:

- live/paper mode policy;
- broker/account fingerprint reference;
- allowed instruments/asset classes;
- max capital tier;
- per-trade risk;
- concentration limits;
- portfolio exposure limits;
- leverage/short/options/futures permissions;
- execution policy;
- model/strategy promotion versions;
- stop/drawdown policy;
- market-data health thresholds.

Any safety-relevant change invalidates prior live-promotion evidence until revalidated.

Reject unknown/invalid config fields rather than silently accepting typos.

---

# 14. PHASE J — CONTINUOUS RECONCILIATION

## ATOS-P2-REC-001

Run broker reconciliation:

- before live activation;
- periodically during live session;
- after any order ambiguity;
- after reconnect;
- after persistence recovery;
- after risk trip;
- before capital promotion;
- before shutdown if possible and on next startup regardless.

Metrics:

- last reconciliation age;
- duration;
- mismatch count;
- unknown orders;
- broker-vs-local exposure delta;
- broker-vs-local cash delta;
- unresolved intents.

Any material mismatch freezes acquisition.

---

# 15. PHASE K — ADVERSARIAL BROKER HARNESS

## ATOS-P2-FAULT-001

Build deterministic fake Alpaca/exchange adapter supporting scripted failure modes:

- full fill;
- open order;
- partial fill;
- fill after cancel request;
- canceled with partial fill;
- timeout before accept;
- timeout after accept;
- duplicate response;
- stale status;
- out-of-order event;
- 429;
- 500/502/503;
- malformed JSON/object;
- missing order ID;
- unknown status;
- insufficient buying power;
- market closed;
- halt;
- quantity precision rejection;
- notional/price invalidation;
- auth failure;
- account mismatch;
- position changed externally;
- broker stream disconnect/reconnect.

Every scenario asserts:

1. no untracked exposure;
2. no duplicate order;
3. reservation conservative while unknown;
4. acquisition frozen on material uncertainty;
5. recovery/reconciliation can resolve or surface blocker.

Mark suite `adversarial` for CI.

---

# 16. PHASE L — CRASH / EVENT REPLAY

## ATOS-P2-FAULT-002

Deterministic replay of durable events must reconstruct:

- positions;
- cash/equity expectation;
- open/unknown orders;
- reservations;
- fills;
- capital tier;
- daily/high-water anchors;
- risk trips;
- live session state;
- idempotency/client IDs;
- strategy/model provenance.

Test:

- duplicate events;
- truncated tail;
- corrupt event;
- DB locked;
- crash between WAL and submission;
- crash after submission before acknowledgement persistence.

Uncertainty => RECOVERY_REQUIRED.

---

# 17. PHASE M — ALERTING / ESCALATION

## ATOS-P2-OPS-001

Logging to stdout is insufficient for unattended capital.

External alerts for:

- live activation/deactivation;
- broker reconciliation mismatch;
- unknown order;
- timeout-after-accept;
- persistence failure;
- risk limit trip;
- daily/total drawdown trip;
- stale/invalid feed;
- account/broker auth failure;
- capital breach;
- repeated rejected orders;
- system recovery requiring operator action.

Alert payloads must redact credentials, auth headers, signatures, tokens, raw secret config.

---

# 18. PHASE N — API / CONTROL PLANE SECURITY

## ATOS-P2-API-001

Audit `core/rest_api.py`, web server, dashboard routes and any mutation endpoint.

Requirements for remote production access:

- authentication always enabled for production control plane;
- MFA/SSO upstream where practical;
- RBAC: read telemetry vs mutate trading state;
- CSRF protection for browser mutations;
- request rate limiting;
- secure session/cookie flags;
- origin restrictions/CORS allowlist;
- audit log containing actor/action/time/result but no secrets;
- network restriction (VPN/Tailscale/Cloudflare Access/private network);
- no credentials returned by any endpoint;
- no endpoint logs raw credentials;
- live-arm/flatten/unfreeze actions require elevated authorization and explicit confirmation semantics;
- production API should not default to `0.0.0.0` without explicit network protection.

Add adversarial auth tests.

---

# 19. PHASE O — DASHBOARD TRUST MODEL

## ATOS-P2-UI-001

Every dashboard page should show a global operator status bar:

- MODE: BACKTEST / RESEARCH / PAPER / LIVE-ARMED / LIVE / FROZEN / RECOVERY REQUIRED;
- BROKER: PAPER / LIVE and account fingerprint;
- DATA: LIVE / STALE / INVALID / DEMO / UNAVAILABLE;
- EXECUTION: ENABLED/DISABLED;
- RECONCILIATION: MATCHED/MISMATCH/UNKNOWN;
- broker connection freshness;
- market-data freshness;
- effective capital at risk vs tier limit;
- outstanding reserved capital;
- open/unknown order count;
- active risk trips;
- last successful persistence health check.

Production dashboard must never silently render demo/fake data as healthy live state.

Dangerous controls must not be mixed with unauthenticated/read-only analytics.

---

# 20. PHASE P — DEPLOYMENT READINESS

## ATOS-P2-DEPLOY-001

Separate:

- **liveness**: process exists/responds;
- **readiness**: system is safe to add new risk.

Live readiness requires:

- persistence healthy;
- broker auth healthy;
- expected account confirmed;
- reconciliation fresh + MATCHED;
- data healthy;
- no unresolved order intents;
- no risk trip;
- capital tier valid;
- strategy/model promotion valid;
- event loops/threads healthy;
- execution adapter healthy.

Process manager may restart crashes, but restart must enter RECONCILING, never ACTIVE directly.

Container/runtime secrets are injected externally, not baked into image.

---

# 21. PHASE Q — CI HARDENING

## ATOS-P2-CI-001

Current CI runs compile + a hand-selected pytest list and optional Alpaca paper smoke. Harden it.

### Required Python gates

- install from authoritative dependency manifests, not only ad-hoc package list;
- run **full** Alpha IO pytest suite;
- explicit adversarial marker suite;
- Ruff;
- mypy or pyright (strict first for execution/risk/reconciliation/credentials);
- Bandit;
- pip-audit;
- secret scan;
- compileall;
- coverage report with minimum threshold for execution/risk/security modules;
- no real-network dependency for core safety tests.

### Supply-chain

- pin/lock dependencies where practical;
- Dependabot/Renovate optional;
- hash/lock high-risk production dependencies if deployment supports it;
- fail on known high-severity vulnerabilities unless explicit documented exception.

### CI secret policy

Optional broker smoke should use dedicated paper-only credentials with minimal permissions. Never use live credentials in CI.

---

# 22. PHASE R — BACKTEST CORRECTNESS

## ATOS-P3-BT-001

Audit:

- `core/backtest_engine.py`
- `core/advanced_backtest.py`
- feature generation;
- strategy scoring;
- data alignment;
- RL evaluation paths.

Test/prevent:

- lookahead bias;
- future-bar access;
- survivorship bias where universe data matters;
- data snooping;
- leakage via normalization over full dataset;
- label leakage into features;
- same-sample parameter tuning and reporting;
- incorrect corporate-action handling for equities;
- unrealistic fills;
- missing commissions/spread/slippage;
- no liquidity constraint;
- using close price for signal and same close for impossible fill;
- overlapping train/test windows without purging where necessary.

Add deterministic “leak trap” tests that fail if future values influence current decisions.

---

# 23. PHASE S — RL GOVERNANCE

## ATOS-P3-ML-001

Audit `core/advanced_rl.py`, `core/agent.py`, `core/deep_learning.py` and callers.

Requirements:

- immutable train/validation/test boundaries;
- no online learning directly changing live execution policy without promotion;
- reproducible seeds/config/model artifact hash;
- observation schema/version;
- action-space constraints;
- reward includes realistic transaction costs and risk penalties;
- evaluation across multiple market regimes;
- catastrophic-action guard independent of policy network;
- distribution-shift detector/shadow fallback;
- model version associated with every proposal/order.

RL action is advisory TradeProposal; deterministic risk engine remains authority.

---

# 24. PHASE T — SWARM / AGENT ARBITRATION

## ATOS-P3-AGENT-001

The README advertises swarm-based decision arbitration and rare-alpha detection. Formalize governance.

Requirements:

- deterministic arbitration interface;
- agent identities/versions;
- no hidden side effects during voting/scoring;
- conflicting agents cannot cause duplicate independent broker orders;
- aggregate proposal is deduplicated by instrument/intent/time horizon;
- confidence calibration by agent;
- minimum evidence before live eligibility;
- quorum does not override hard risk;
- provenance persisted from agent votes -> final proposal -> order.

Test malicious/buggy agent outputs:

- NaN confidence;
- >1 confidence;
- negative size;
- enormous quantity;
- unsupported symbol;
- contradictory buy/sell proposals;
- stale proposal;
- prompt injection/untrusted text attempting to alter rules.

Normalize/reject before risk stage.

---

# 25. PHASE U — AUTO-TUNER / SELF-MODIFICATION GOVERNANCE

## ATOS-P3-TUNE-001

Audit `core/auto_tuner.py` and any recursive/self-learning path.

Production weights/config cannot mutate in place solely because recent performance improved.

Use lifecycle:

- CANDIDATE
- SHADOW
- OOS_VALIDATED
- PROMOTED
- ROLLED_BACK
- RETIRED

Promotion requires untouched evaluation evidence and produces a durable model/config artifact hash.

Any material tuning change invalidates live strategy promotion until re-approved.

Never permit auto-tuner to increase hard risk/capital/leverage ceilings.

---

# 26. PHASE V — EXECUTION REALISM

## ATOS-P3-EXEC-001

Backtest/paper/promotion model must account for asset-class-specific execution:

### Equities

- market hours;
- halts;
- spreads;
- fractional share rules;
- extended-hours policy;
- corporate actions;
- borrow/short restrictions if enabled.

### Crypto

- 24/7 sessions;
- venue-specific precision;
- spread/slippage;
- outages;
- min notional.

### Futures/options if retained

Do not claim support until execution/risk semantics include:

- contract multipliers;
- expirations;
- margin;
- assignment/exercise for options;
- Greeks/delta exposure;
- liquidation/margin risk;
- reduce-only/position-side semantics;
- liquidity/open interest.

If not implemented, explicitly disable these from live mode and correct README claims.

---

# 27. CAPITAL PROMOTION LADDER

## ATOS-P3-CAP-001

Capital is a governed persisted tier, not a user-entered `initial_capital` number.

Recommended ladder:

| Tier | Max real capital at risk | Evidence |
|---|---:|---|
| L0 | $0 | Research/backtest/paper only |
| L1 | $10 | all P0/P1 safety gates + broker reconciliation |
| L2 | $25 | clean supervised lifecycle sample |
| L3 | $50 | timeout/cancel/crash drills pass |
| L4 | $100 | extended supervised period; zero unexplained reconciliation mismatches |
| L5 | $250 | positive OOS net-cost strategy evidence |
| L6 | $500 | stable execution shortfall + external alerting |
| L7 | $1,000+ | independent review + operational maturity |

Rules:

- config cannot skip tier;
- owner authorization required to promote;
- capital breach freezes and may demote;
- strategy/config hash change can invalidate tier eligibility;
- `initial_capital` does not grant spend authority.

---

# 28. DOCUMENTATION TRUTH PASS

Search repo for claims such as:

- `production-ready`
- `live trading`
- `autonomous`
- `self-learning`
- `futures`
- `options`
- `spreads`
- `outperform institutional`
- `secure credentials`
- `automatic rotation`
- `risk tuning`

Every claim must match tested implementation.

Examples:

- If options/futures live execution is not safely supported, mark it research/not-live.
- If credential rotation is not automated against providers, do not claim automatic provider rotation.
- If encryption derives a local key from machine/process attributes rather than an externally managed secret, document its threat model accurately.
- Remove blanket “Production-Ready” banners until readiness gates are actually satisfied.

---

# 29. CREDENTIAL MANAGER HARDENING

Beyond deleting exposed repo credentials, review `core/credentials.py`.

Requirements:

- production secrets preferably come from environment/secret manager/keyring, not application-managed plaintext files;
- plaintext storage must be development-only, loudly gated, never default for live;
- encryption master key must not be deterministically reconstructible from weak/local process metadata for production security;
- do not invent “rotation” unless provider API rotation actually occurs;
- encrypted credential files and salts are gitignored;
- access logs never contain secret values;
- exception messages redact secrets;
- credential list/view UI exposes metadata only;
- live credential environment is explicit and account fingerprint is checked.

Add tests for redaction and refusal of insecure production storage.

---

# 30. INITIAL FILE MAP FOR CLAUDE

Claude must verify call graph before edits, but begin with:

### Security / credentials

- `Alpha IO/config/alpaca_credentials.json`
- `.gitignore`
- `Alpha IO/core/credentials.py`
- `Alpha IO/activate.py`
- `.github/workflows/alpha-io-ci.yml`

### Execution / broker

- `Alpha IO/core/execution.py`
- `Alpha IO/core/alpaca_connector.py`
- `Alpha IO/core/exchange_connectors.py`
- `Alpha IO/core/ledger.py`
- `Alpha IO/core/orchestrator.py`
- `Alpha IO/core/unified_system.py`

### Risk

- `Alpha IO/core/risk.py`
- portfolio/position modules discovered in call graph.

### Data

- `Alpha IO/core/live_data.py`
- `Alpha IO/core/market_data.py`
- `Alpha IO/core/feature_engine.py`

### Agents/ML

- `Alpha IO/core/agent.py`
- `Alpha IO/core/advanced_rl.py`
- `Alpha IO/core/deep_learning.py`
- `Alpha IO/core/ai_assistant.py`
- `Alpha IO/core/auto_tuner.py`
- signal arbitration/scoring modules.

### API/web

- `Alpha IO/core/rest_api.py`
- `Alpha IO/web/*`
- dashboard routes/templates/scripts.

### Backtest

- `Alpha IO/core/backtest_engine.py`
- `Alpha IO/core/advanced_backtest.py`

---

# 31. SUGGESTED TEST ORGANIZATION

Adapt to repo conventions, but ensure clear invariant-focused modules:

- `Alpha IO/tests/test_secret_hygiene.py`
- `Alpha IO/tests/test_live_activation_fail_closed.py`
- `Alpha IO/tests/test_order_lifecycle_adversarial.py`
- `Alpha IO/tests/test_order_idempotency.py`
- `Alpha IO/tests/test_order_intent_wal.py`
- `Alpha IO/tests/test_broker_reconciliation.py`
- `Alpha IO/tests/test_live_recovery.py`
- `Alpha IO/tests/test_live_persistence_failure.py`
- `Alpha IO/tests/test_risk_reservations.py`
- `Alpha IO/tests/test_daily_drawdown_recovery.py`
- `Alpha IO/tests/test_market_data_integrity.py`
- `Alpha IO/tests/test_agent_authority_boundary.py`
- `Alpha IO/tests/test_backtest_leakage.py`
- `Alpha IO/tests/test_control_plane_authorization.py`
- `Alpha IO/tests/test_dashboard_trust_state.py`

---

# 32. GLOBAL EXECUTABLE INVARIANTS

Encode these as tests/properties where possible.

### INV-ATOS-001

`effective_exposure <= capital_tier_limit`, or system is FROZEN with explicit breach.

### INV-ATOS-002

Unknown order state retains conservative reservation.

### INV-ATOS-003

No retry may create a second order before idempotent broker lookup.

### INV-ATOS-004

Live startup requires reconciliation == MATCHED.

### INV-ATOS-005

Critical persistence failure => no new live acquisition.

### INV-ATOS-006

Credentials alone cannot activate live trading.

### INV-ATOS-007

Model/RL/LLM/agent output cannot bypass deterministic risk.

### INV-ATOS-008

Any order reaching broker has a persisted approved intent.

### INV-ATOS-009

Daily/total drawdown trip survives restart.

### INV-ATOS-010

Risk-reducing privilege implies non-increasing absolute exposure.

### INV-ATOS-011

Invalid/stale/unknown data cannot initiate new exposure.

### INV-ATOS-012

Dashboard DEMO cannot appear LIVE/HEALTHY.

### INV-ATOS-013

Safety-relevant config/model change invalidates stale promotion evidence.

### INV-ATOS-014

No real secret appears in tracked repository content.

### INV-ATOS-015

Unresolved broker/local mismatch cannot be silently auto-declared safe.

---

# 33. IMPLEMENTATION ORDER

Execute sequentially unless isolated branches/worktrees are used and integration tests gate merge:

1. ATOS-P0-SEC-001 credential revocation/removal support + secret scan.
2. ATOS-P0-EXEC-001 lifecycle state machine.
3. ATOS-P0-EXEC-002 order-intent WAL.
4. ATOS-P0-EXEC-003 idempotency timeout-after-accept.
5. ATOS-P0-EXEC-004 cancel/fill race.
6. ATOS-P0-REC-001 fail-closed recovery.
7. ATOS-P0-REC-002 startup broker reconciliation.
8. ATOS-P0-AUTH-001 explicit live activation.
9. ATOS-P1-PERSIST-001 persistence failure policy.
10. ATOS-P1-RISK-001 broker-authoritative exposure.
11. ATOS-P1-RISK-002 durable drawdown.
12. ATOS-P1-RISK-003 risk-reducing orders.
13. ATOS-P1-NUM-001 Decimal boundary.
14. ATOS-P1-DATA-001 market-data integrity.
15. ATOS-P1-AGENT-001 agent authority boundary.
16. ATOS-P1-CONFIG-001 config authority/hash.
17. ATOS-P2-REC-001 recurring reconciliation.
18. ATOS-P2-FAULT-001 adversarial fake broker.
19. ATOS-P2-FAULT-002 crash/replay.
20. ATOS-P2-OPS-001 external alerts.
21. ATOS-P2-API-001 control-plane security.
22. ATOS-P2-UI-001 dashboard trust hierarchy.
23. ATOS-P2-DEPLOY-001 runtime readiness.
24. ATOS-P2-CI-001 CI hardening.
25. ATOS-P3-BT-001 backtest correctness.
26. ATOS-P3-ML-001 RL governance.
27. ATOS-P3-AGENT-001 swarm arbitration.
28. ATOS-P3-TUNE-001 auto-tuner governance.
29. ATOS-P3-EXEC-001 execution realism.
30. ATOS-P3-CAP-001 capital promotion.
31. credential manager threat-model pass.
32. documentation truth pass.
33. complete final adversarial re-audit.

---

# 34. PHASE GATES

## Gate 0 — repository security

Requires:

- exposed key revoked by owner;
- tracked credential file removed;
- secret scan green.

## Gate A — broker paper/sandbox lifecycle validation

Requires:

- P0 lifecycle/WAL/idempotency/cancel fixes;
- adversarial tests green.

## Gate B — supervised real-money L1 ($10)

Requires:

- all P0/P1 complete;
- broker reconciliation MATCHED;
- explicit live authorization;
- full CI green;
- operator runbook;
- owner present.

## Gate C — higher supervised tiers

Requires P2 reconciliation/fault/crash/alert readiness and clean observed operation.

## Gate D — unattended live

Requires all P2 plus P3 strategy governance, hardened deployment/control plane, fresh independent adversarial review.

---

# 35. REQUIRED RUNBOOKS

Create/update:

### Live startup

- environment validation;
- credential source validation;
- account fingerprint;
- reconciliation;
- risk/capital tier;
- data health;
- explicit arm.

### Unknown order

- freeze acquisition;
- lookup by client ID;
- inspect broker open orders/fills;
- reconcile;
- terminal resolution.

### Persistence outage

- freeze;
- broker state capture;
- restore DB;
- replay;
- reconcile before resume.

### Feed outage

- block new risk;
- determine safe treatment of existing positions;
- recover sequence continuity.

### Kill/flatten

- distinguish freeze from flatten;
- account for open orders;
- verify broker terminal state;
- reconcile after flatten.

### Credential incident

- revoke;
- rotate;
- remove from repository/logs;
- audit exposure;
- update secret manager.

---

# 36. CLAUDE CHECKPOINT FORMAT

Use in commit/PR descriptions:

```text
ISSUE: ATOS-Px-...
INVARIANT: <guarantee>
ROOT CAUSE: <failure>
FILES CHANGED: <paths>
TESTS ADDED: <scenarios>
TARGETED TESTS: PASS/FAIL
FULL SUITE: PASS/FAIL
LINT/TYPE/SECURITY/SECRET SCAN: PASS/FAIL
EXTERNAL OWNER ACTION: <none or exact action>
REMAINING RISK: <explicit>
NEXT ISSUE: <ID>
```

Never mark a check PASS unless actually executed.

---

# 37. FINAL ADVERSARIAL REVIEW PROMPT

After all implementation, Claude performs a final read-only adversarial pass using this prompt:

> Assume the broker, network, database, filesystem, process, clock, market feed, API, dashboard, credential provider, RL policy, LLM, swarm agents, and operator inputs can each fail independently or together. Search for any path where Agentic_Trading_OS can create, retain, duplicate, mis-size, or fail to observe real exposure while believing state is safer than broker reality. Treat broad exception handlers, retries, defaults, fallbacks, auto-learning, direct broker calls, UI controls, mutable in-memory risk state, and stale cached data as capital-loss boundaries. Unknown must never become safe by default.

Explicitly answer:

1. Can a network timeout duplicate an order?
2. Can a partial fill release too much reserved exposure?
3. Can a fill occur after cancel and be missed?
4. Can restart forget a broker position/open order?
5. Can database failure permit new live risk?
6. Can manual broker activity evade local risk limits?
7. Can credentials or `mode="live"` alone activate real trading?
8. Can a model/RL/LLM/agent bypass hard risk?
9. Can two agents create duplicate/conflicting orders?
10. Can auto-tuning raise risk without promotion?
11. Can stale but frequently repeated data appear fresh?
12. Can a SELL/close increase absolute exposure?
13. Can daily drawdown reset on restart?
14. Can a dashboard/API mutation bypass strong auth/audit?
15. Can demo data look live?
16. Can real secrets be committed or logged?
17. Can backtest leakage make a strategy appear promotable?
18. Can unsupported futures/options semantics reach real execution?
19. Can a safety-relevant config/model change reuse old approval evidence?
20. Can any UNKNOWN condition become RUNNING/SAFE without reconciliation?

If any answer is yes or uncertain, unattended live trading remains NO-GO.

---

# 38. OWNER SUCCESS CRITERION

The target is not a marketing label such as “production-ready.” The target is durable evidence that the system remains conservative under ambiguity.

After hardening, Agentic_Trading_OS should survive:

- exposed/rotated credentials;
- timeout after broker acceptance;
- partial fill;
- cancel/fill race;
- broker stream disconnect;
- market-data sequence gap;
- database outage;
- process crash;
- restart;
- manual broker-side trade;
- buggy agent proposal;
- RL distribution shift;
- dashboard/backend outage;

and still answer correctly:

**Which account is connected? What does the broker actually own? Which orders are still economically live? What capital is reserved? What is the actual exposure? Is data trustworthy? Is another order allowed? Which deterministic rule granted or denied it? Can the full decision be replayed and reconciled?**

Until those answers are provable under adversarial tests and controlled canaries, keep the system in research/paper/sandbox or tightly supervised minimal-capital mode.
