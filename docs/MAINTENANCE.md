# Maintenance log

Open items that are known, accepted, and not blocking — deliberately, by a
named decision. This file exists so that "we decided to live with it" and "we
forgot" stop looking the same in six months.

Each item records what the risk actually is, who accepted it, and what would
turn it back into a blocker. An item with no escalation condition is not an
accepted risk, it is an unresolved one with better manners.

---

## M-001 — Exposed Alpaca credential, revocation deferred

| | |
|---|---|
| **Opened** | 2026-09-05 |
| **Status** | Open, accepted |
| **Accepted by** | Repository owner (Andrew Bohri), 2026-09-05 |
| **Origin** | ATOS-P0-SEC-001 |
| **Runbook** | `docs/runbooks/credential-incident.md` |

### What happened

`Alpha IO/config/alpaca_credentials.json` was committed with a real Alpaca API
key (26 characters) and secret (44 characters). Neither carried placeholder
wording. The file was deleted from the working tree during ATOS-P0-SEC-001,
and a blocking CI secret scan now prevents a recurrence.

**Deleting the file did not undo publication.** The values remain in git
history at `3d5f7b4` and are readable by anyone who has cloned the repository
or can browse its history.

### The decision

The owner has elected to continue using the Alpaca integration as-is and to
defer revocation until further notice. This item tracks that deferral.

### Residual risk, unchanged by the deferral

Anyone holding the key can act on the account within whatever permissions it
carries — read balances and positions, and place or cancel orders if it is a
trading key. The capital ladder bounds what *this system* will risk; it does
not bound what a third party can do with a working credential. The two are
independent, and only one of them is under this repository's control.

### What escalates this back to a blocker

- The key is, or becomes, a **live-funded** account credential rather than
  paper.
- Any unexplained order, position or balance change appears at the broker.
- A capital tier above **L1** is sought.
- The repository becomes public, or is shared with anyone outside the current
  holders.

### Closing this item

Revoke and reissue in the Alpaca console at https://app.alpaca.markets/, put
the new value in the environment (never a file), and confirm the old key
returns 401. Then mark this item closed with the date. Rewriting git history
is optional and does not substitute for revocation — copies already exist.

---

## M-002 — Ledger stores money as SQLite REAL

| | |
|---|---|
| **Opened** | 2026-09-05 |
| **Status** | Open, awaiting a migration decision |
| **Origin** | ATOS-P1-NUM-001 (the remaining half) |

`TradingLedger` stores cash, quantity, price and P&L as `REAL`. The execution
path is exact (`core/money.py`, Decimal throughout); the ledger it writes to
is not, so stored history accumulates binary-float error.

Per ULTRAPLAN §2.3 this is a stop-and-report rather than something to
improvise against existing trade history. Options: convert existing ledger
files one-off; start a new ledger generation and archive the old (recommended
— it leaves the old records readable and untouched); or keep `REAL` for
historical rows and write `TEXT` for new ones.

**Escalates when:** any real capital is traded, because from that point the
ledger is a financial record rather than a paper-trading artifact.

---

## M-003 — No external alerting channel configured

| | |
|---|---|
| **Opened** | 2026-09-05 |
| **Status** | Open |
| **Origin** | ATOS-P2-OPS-001 |

`core/alerting.py` routes and redacts correctly and is tested, but the only
configured channel is structured logging. An operator-action condition at
02:00 reaches a log file and nobody else.

**Escalates when:** the system runs unattended with any real capital.
`AlertManager` accepts any callable, so wiring a webhook or paging service is
small; which service, and where its endpoint comes from, is an operational
choice.

---

## M-004 — Governance registries not routed into the running path

| | |
|---|---|
| **Opened** | 2026-09-05 |
| **Status** | Open |
| **Origin** | ATOS-P3-ML-001, ATOS-P3-AGENT-001, ATOS-P3-TUNE-001 |

The model registry, swarm arbiter and tuning registry are implemented and
tested, but nothing currently calls them from the execution path — because
nothing currently sends a model or a swarm to execution. That is why the gap
is survivable today.

**Escalates when:** any learned policy or multi-agent output is wired to
propose trades. At that point the boundary has to run through
`DeterministicTradeBoundary` and `apply_hard_risk`, not around them.

---

## M-005 — `core/exchange_connectors.py` returns fabricated data

| | |
|---|---|
| **Opened** | 2026-09-05 |
| **Status** | Open |
| **Origin** | ATOS-P2-CI-001 (surfaced), ATOS-P2-UI-001 (trust hierarchy) |

Every Binance and Coinbase request path returns `_mock_request()` output. The
signing helpers are real; the connectivity is not. The module docstring now
says so, but the data is not marked DEMO in the operator status bar the way
the DeFi panels are.

**Escalates when:** either venue is used for anything other than local
development, or that data reaches a dashboard panel an operator reads.
