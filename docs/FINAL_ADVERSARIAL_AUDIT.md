# Final adversarial review

ULTRAPLAN section 37. Twenty questions, each asking whether the system can
create, retain, duplicate, mis-size or fail to observe real exposure while
believing state is safer than broker reality.

Every answer below is backed by an assertion in
`Alpha IO/tests/test_final_adversarial_audit.py`, named `test_qNN_...`. A
read-only pass would be an opinion; these are checked, and an answer that
stops being "no" fails a build rather than ageing quietly in this document.

**One question was YES when this pass ran.** Question 11. It is fixed, and
the fix is pinned rather than recorded as a caveat.

---

### 1. Can a network timeout duplicate an order?

**No.** The client order id is a full UUID generated before the network is
touched, and it survives a retry and a restart, so a resend arrives at the
broker as the same id rather than as a second order. A transport failure
transitions the order to UNKNOWN, never to REJECTED: "the request failed" is
not "the broker refused", and treating it as one is how a filled order becomes
an untracked position.

*Caveat, stated because it is outside our control:* this relies on the broker
honouring client order ids for deduplication. Alpaca does.

### 2. Can a partial fill release too much reserved exposure?

**No.** Effective exposure is `position + outstanding_acquisitions`, so a
40-of-100 fill leaves 60 still counted. The remainder is released only as it
fills.

### 3. Can a fill occur after cancel and be missed?

**No.** CANCEL_REQUESTED and CANCELLED are different states. The request does
not close the order, so a fill arriving afterwards has a legal transition to
land on and is recorded.

### 4. Can restart forget a broker position or open order?

**No.** An empty local book is not evidence of an empty broker book:
reconciliation compares against the broker and refuses acquisition on any
mismatch. An *incomplete* broker snapshot also refuses — not knowing is not
the same as nothing being there.

### 5. Can database failure permit new live risk?

**No.** Nine write kinds are critical; a failure inside the persistence guard
raises `CriticalWriteFailed` rather than continuing, and readiness reports
503 while persistence is unhealthy.

### 6. Can manual broker activity evade local risk limits?

**No.** Exposure is taken from the broker, not from local intent, so a
position opened by hand in the broker's own UI consumes the same limit. A
reconciliation older than the freshness window stops acquisition on its own —
an old check is not evidence about now.

### 7. Can credentials or `mode="live"` alone activate real trading?

**No.** Fifteen conditions must be satisfied, and credentials-present plus a
live flag satisfies two of them. Separately, spend authority comes from the
persisted capital ladder: with no grant, `initial_capital` authorises zero
however large it is.

### 8. Can a model, RL policy, LLM or agent bypass hard risk?

**No.** The catastrophic-action guard sits outside the policy network and
raises rather than penalising — there is no number for the network to trade
off. An unpromoted model cannot serve. An agent proposal passes through a
deterministic boundary that may only shrink it, and then through the risk
engine.

### 9. Can two agents create duplicate or conflicting orders?

**No.** Arbitration produces at most one proposal per instrument and horizon
bucket. Two agents proposing opposite directions produce none, because a
disagreement routed independently becomes two positions.

### 10. Can auto-tuning raise risk without promotion?

**No.** The tuner is one-directional: swept across win rate, average PnL and
loss streak, it never produces a looser parameter set. A looser set cannot be
promoted even with an out-of-sample Sharpe of 9.9, because loosening is
checked before results are.

### 11. Can stale but frequently repeated data appear fresh?

**This was YES.** Freshness was measured as "when did we last receive
something", which a disconnected socket replaying its last tick satisfies
perfectly — as does a cached upstream or a proxy answering from memory. The
arrival clock stayed current while the number stopped meaning anything, and
the operator bar read DATA LIVE.

**Now no.** `FeedHealth` tracks two clocks: when a value last arrived and when
it last *changed*. A feed arriving every second with an unchanged value for
longer than the freeze window reads STALE, and the dashboard names the frozen
symbols. A moving feed still reads LIVE, so the check discriminates rather
than simply pessimising everything.

### 12. Can a SELL or close increase absolute exposure?

**No.** Selling 250 against a 100 long closes 100 and opens a 150 short; the
reduce-only gate compares pre- and post-trade absolute exposure and refuses,
reporting the maximum genuinely reducible quantity.

### 13. Can daily drawdown reset on restart?

**No.** Anchors are persisted and UTC-day-anchored. A trip established before
a restart is still active after it, and still blocks trading.

### 14. Can a dashboard or API mutation bypass strong auth and audit?

**No.** Authentication for mutations is unconditional — not tied to the mode,
which is asserted against the orchestrator source so the old
`enable_auth = (mode == LIVE)` cannot return through another path. Seven
dangerous actions require an ELEVATED permission plus a confirmation string;
`trade` is not sufficient for any of them. Every decision, including every
denial, is audited.

### 15. Can demo data look live?

**No.** DEMO outranks every other trust level including UNAVAILABLE, so a
single generated panel sets the whole bar. A demo panel rendered while real
capital is committed is reported as its own problem. Every trust default is
the pessimistic one.

### 16. Can real secrets be committed or logged?

**No, for the paths we control.** Alert payloads are redacted on egress — key
names and value shapes both. Credential paths are in `.gitignore` and
`.dockerignore`, and a blocking CI job fails the build on a tracked
credential. Access logs and credential listings carry metadata only.

*Outstanding, and not a code issue:* the key exposed in
ATOS-P0-SEC-001 has still not been revoked by the owner. Until it is, Gate 0
is open regardless of what the code now does.

### 17. Can backtest leakage make a strategy appear promotable?

**No.** Prefix invariance catches full-series normalisation, centred windows,
back-filling and wrap-around shifts. A backtest cannot fill at a price the
decision was derived from: same-bar-close timing raises at construction, and
passing the decision bar as the fill bar raises at the call.

### 18. Can unsupported futures or options semantics reach real execution?

**No.** Refused at three layers: `SafetyConfig` rejects a live configuration
with `allow_options` or `allow_futures`; `venue_rules` refuses the asset class
and names what is missing; and the planner stamps every structure
`executable: False` while its execution entry point raises.

### 19. Can a safety-relevant config or model change reuse old approval evidence?

**No.** A changed `SafetyConfig` has a different hash and invalidates the
promotion of the previous one. A capital tier is bound to the config and
strategy hashes it was granted against and stops authorising when either
changes. A material tuning change invalidates the running strategy's
promotion — including a *tightening* one, because a strategy approved at 1%
risk per trade was not approved at 0.2% either.

### 20. Can any UNKNOWN condition become RUNNING or SAFE without reconciliation?

**No.** LIVE_ACTIVE is reachable from exactly one state, LIVE_RECONCILING.
Leaving FROZEN or RECOVERY_REQUIRED means re-reconciling, never resuming. A
restarted process never comes back into a trading state. And with no evidence
at all, readiness reports 503.

---

## Verdict

Nineteen of twenty were "no" on this pass; the twentieth is now "no" and
pinned. That is the code's answer.

**Unattended live trading remains NO-GO,** for reasons outside the code:

1. **Gate 0 is open.** The Alpaca key committed in this repository has not
   been revoked. No amount of hardening addresses a key an unknown number of
   people can read.
2. **No capital tier has been granted.** The ladder is implemented and the
   grant is L0, which authorises nothing. Reaching L1 needs the P0 and P1
   gates plus clean broker reconciliation, approved by name.
3. **No external alerting channel is configured.** The manager accepts any
   callable and the redaction is tested, but nothing is wired to a pager, so
   an operator-action condition at 02:00 reaches a log file.
4. **The governance objects are not all wired into the running path.** The
   model registry, the swarm arbiter and the tuning registry are implemented
   and tested; nothing in this repository currently routes a model or a swarm
   to execution, which is why that gap was survivable, and it has to be
   closed by whatever first does.

Items 1 and 2 are owner decisions. Items 3 and 4 are work.
