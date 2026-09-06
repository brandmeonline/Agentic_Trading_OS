# Owner action checklist

Everything in this repository that a person has to do, because software
cannot do it for them — sign something, hold a credential, decide a risk, or
put a file somewhere a build container cannot reach.

Nothing here is a code task. All 33 ULTRAPLAN issues, the documentation truth
pass (§28), credential hardening (§29) and the final adversarial review (§37)
are complete and on `claude/trading-terminal-review-k1lqqv`.

**Legend** — ☐ outstanding · ☑ done · ⛔ blocks live capital · ⚠️ blocks
unattended operation only.

---

## A. Before any real capital moves

### ☑ A1 — Grant a capital tier · *decided 2026-09-05*

L1 granted, $10.00 ceiling. Decision and evidence in
`docs/decisions/2026-09-05-capital-tier-L1.md`.

### ⛔ ☐ A2 — Apply the L1 grant **on the deployment host**

The grant is deployment state, not source. A grant written in a build
container is discarded with the container and authorises nothing. Run this
from the repository root on the host that will trade — `data/` is the mounted
volume in `docker-compose.yml`:

```
python "Alpha IO/tools/grant_capital_tier.py" \
    --tier L1 \
    --approved-by "Andrew Bohri" \
    --ladder data/capital_ladder.json \
    --i-am-authorizing-real-money \
    --risk-acknowledgement 'I UNDERSTAND THIS TRADES REAL MONEY' \
    --attest reconciliation_clean="reviewed in the Alpaca console on <date>"
```

Add `--executed-by "<who ran it>"` if the approver is not the person at the
keyboard. Use `--dry-run` first to see the evidence without granting anything.

If broker credentials are present in that environment, the tool runs a real
reconciliation and records it as *verified*, refusing the attestation — a
check that ran outranks somebody's word about it.

**Verify afterwards:** `data/capital_ladder.json` exists and reads `L1`, and
its sidecar `data/capital_ladder.json.attestation.json` names you.

### ⛔ ☐ A3 — Pin the expected broker account

Set `expected_account_fingerprint` on the orchestrator config for the
deployment. It is compared for equality against what the broker reports, and
it only ever satisfies once a reconciliation has actually matched — an unset
value fails the `account_fingerprint_matches` condition rather than passing
vacuously. This is the check that catches a live key behind a paper label.

Reconcile once by hand in the Alpaca console before the first live session,
so the attestation in A2 is something you saw rather than something you
assumed.

### ⛔ ☐ A4 — Decide M-002, the ledger's money type

`TradingLedger` stores cash, quantity, price and P&L as SQLite `REAL`. The
execution path is exact Decimal; the ledger is not. Its own escalation
condition — *any real capital traded* — is what A2 makes reachable, so this
is now due. Three options, in `docs/MAINTENANCE.md`; the recommendation is to
start a new ledger generation and archive the old one. **This is a decision
to make, then a change to implement — say which and it gets built.**

---

## B. Deployment configuration

### ⛔ ☐ B1 — Set the required environment variables

None of these have defaults, deliberately. The process refuses to start
rather than inventing one.

| Variable | Needed for | Note |
|---|---|---|
| `WEB_SECRET_KEY` | Dashboard, whenever debug is off | 32+ random bytes; a fresh one per restart logs everyone out |
| `ADMIN_PASSWORD_HASH` *or* `ADMIN_PASSWORD_HASH_FILE` | Dashboard login | Prefer the file form; `ADMIN_PASSWORD` is accepted but is plaintext in the environment |
| `CREDENTIALS_PASSWORD` | The encrypted credential store | Without it the store derives a machine-local key and cannot be restored from backup on another host |
| `ALPACA_API_KEY`, `ALPACA_API_SECRET` | Broker | Environment only, never a file — the committed-credential CI scan will fail the build on a file |
| `FRED_API_KEY`, `OPENAI_API_KEY`, `SMTP_*` | Optional integrations | Features degrade visibly rather than faking data |

### ☐ B2 — Keep the REST control plane off the network

`APIConfig` binds `127.0.0.1` and requires auth by default. Both are correct;
if you change either, put a real reverse proxy in front of it first.

### ☐ B3 — Confirm the dashboard's trust bar reads what you expect

DEMO outranks UNAVAILABLE by design, so one generated panel colours the whole
bar. If the bar says DEMO on a deployment holding capital, that is the system
working, not a display bug — find the panel.

---

## C. Maintenance items awaiting an owner decision

Full detail, including what escalates each back to a blocker, in
`docs/MAINTENANCE.md`.

| Item | What it is | Status |
|---|---|---|
| **M-001** | Exposed Alpaca key, revocation deferred | ☑ Decided 2026-09-05: continue as-is. Escalates if the key becomes live-funded, an unexplained order/balance appears, **a tier above L1 is sought**, or the repo is shared |
| **M-002** | Ledger stores money as SQLite `REAL` | ⛔ ☐ Due now — see A4 |
| **M-003** | No external alerting channel configured | ⚠️ ☐ Wire a webhook or pager. `AlertManager` takes any callable; which service is an operational choice |
| **M-004** | Governance registries not routed into the running path | ☐ Not due until a learned policy or swarm output first proposes a trade |
| **M-005** | `core/exchange_connectors.py` returns fabricated data | ☐ Not due unless Binance/Coinbase leave local development |

### ☐ C1 — If and when you revoke the Alpaca key (closes M-001)

Revoke and reissue at https://app.alpaca.markets/, put the new value in the
environment, confirm the old key returns 401, then date-stamp M-001 closed.
Rewriting git history is optional and is not a substitute — copies exist.

---

## D. Repository

### ☐ D1 — Review and merge PR #8

Open as a draft on `claude/trading-terminal-review-k1lqqv`, CI green. Mark it
ready and merge when you have read it. It is large because the plan was.

### ☐ D2 — Note what stays NO-GO

**Unattended** live trading remains NO-GO on M-003 alone: an operator-action
condition at 02:00 currently reaches a log file and nobody else. **Supervised
L1** is a different question and the ladder is designed to separate them.
Nothing above L1 should be sought while M-001, M-002 and M-003 stand.
