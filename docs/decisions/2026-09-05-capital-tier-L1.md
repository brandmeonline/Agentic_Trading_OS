# Capital tier L1 granted

| | |
|---|---|
| **Date** | 2026-09-05 |
| **Approved by** | Repository owner (Andrew Bohri) |
| **Tier** | L1 — $10.00 maximum real capital at risk |
| **Previous** | L0 — $0.00 |
| **Issue** | ATOS-P3-CAP-001 |

## The decision

The owner has granted capital tier **L1**: a ceiling of $10.00 of real capital
at risk. This is the first rung above research-and-paper-only.

## Evidence

Section 27 requires, for L1: *all P0/P1 safety gates + broker reconciliation*.

**Verified** — checked by running it, on 2026-09-05:

| Item | Result |
|---|---|
| P0/P1 safety gates | 1,165 adversarial tests passed, 419 deselected |
| Safety config hash | `cfg-6b545428f33f1ee9` (computed, not supplied) |
| Strategy hash | `strat-aec9f69fc04539a7` (computed over the strategy modules and promoted versions) |

**Attested** — a person's word, because this host cannot check it:

| Item | Attestation |
|---|---|
| Broker reconciliation clean | Owner attests. No broker credentials are present in the build environment, so no reconciliation was attempted here. |

The distinction is recorded in the grant's sidecar file and is deliberate. A
grant that cannot say which of its evidence was checked and which was somebody's
word is a grant nobody can audit after a loss.

## What this does and does not authorise

**Does:** the system may risk up to $10.00 of real capital, provided
everything else also permits it — the fifteen live-authorization conditions,
the fourteen startup checks, a fresh matched reconciliation, healthy
persistence, no active risk trip, and a runtime state that reached
`LIVE_ACTIVE` through `LIVE_RECONCILING`. The tier is a ceiling, not a
licence; it is one of several independent gates and the most permissive of
them does not win.

**Does not:** authorise anything above $10.00, survive a change to the safety
config or the strategy (either invalidates the grant until it is rebound by a
named person), or survive a breach — exceeding the ceiling freezes the ladder
and demotes it to L0.

## Applying the grant

The grant is deployment state, not source. It is written to a gitignored path
by the tool, and must be created **in the deployment**, not in a build
container — the file this repository's CI produces is discarded with the
container.

```
python "Alpha IO/tools/grant_capital_tier.py" \
    --tier L1 \
    --approved-by "Andrew Bohri" \
    --ladder data/capital_ladder.json \
    --i-am-authorizing-real-money \
    --risk-acknowledgement 'I UNDERSTAND THIS TRADES REAL MONEY' \
    --attest reconciliation_clean="reviewed in the Alpaca console on <date>"
```

The acknowledgement phrase is typed by the person granting the tier. That is
the point of it, so it is not typed on their behalf by anything automated,
including me.

If broker credentials are present in the environment when this runs, the tool
attempts a real reconciliation and records the result as *verified* rather
than accepting the attestation — the attestation is refused in that case,
because a check that actually ran outranks somebody's word about it.

## Related open items

- **M-001** — the exposed Alpaca credential. Revocation is deferred by owner
  decision. That item's escalation conditions include *"a capital tier above
  L1 is sought"*, so L1 and the deferral coexist by design; L2 does not.
- **M-002** — the ledger stores money as SQLite `REAL`. Its escalation
  condition is *"any real capital is traded"*, which this grant makes
  reachable.
