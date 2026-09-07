# Market Data Provider Evaluation

**Purpose:** resolve the blocker recorded in `docs/ULTRA_PLAN.md` Phase 6 — the
sector heatmap, market breadth panel, and cross-asset strip have no data source
in this repo.
**Date:** 2026-08-28. Pricing and uptime move; re-verify before committing spend.

---

## 1. What is actually required

The three blocked panels are not one requirement. Separating them changes the
answer:

| Panel | Real requirement | Symbols needed | Update cadence that matters |
| --- | --- | ---: | --- |
| Sector heatmap | 11 GICS sector performances | 11 (sector ETFs) | minutes |
| Market breadth | advance/decline, new highs/lows over a universe | 500+ | minutes |
| Cross-asset strip | DXY, TNX, VIX, OIL | 4–6 | minutes |

Two observations that do most of the work here:

**Sector membership is not needed.** The eleven SPDR sector ETFs (XLK, XLF, XLE,
XLV, XLI, XLY, XLP, XLU, XLRE, XLB, XLC) *are* the heatmap. They are ordinary
equities. No GICS licence, no constituent mapping.

**Indices can be proxied or sourced free.** DXY→UUP, VIX→VIXY, OIL→USO, and
10-year yield from FRED's `DGS10` directly rather than a proxy. Only if the desk
wants the *actual* index print does an indices subscription become necessary.

**The terminal refreshes every 30 seconds.** Microsecond latency is irrelevant to
this system. The binding variable is *staleness class* — real-time versus
15-minute delayed — not microseconds.

## 2. Candidates

### Alpaca — already integrated

`core/alpaca_connector.py` and `core/execution.py` already speak Alpaca. Adding
nothing is worth real money in reliability terms.

| Plan | Price | Data |
| --- | ---: | --- |
| Basic | **free** | IEX real-time; SIP historical when `end` is ≥15 min old; 200 req/min; 30 WebSocket symbols |
| Algo Trader Plus | **$99/mo** | Full SIP real-time, OPRA options, 10,000 req/min, unlimited WebSocket symbols |

- **Uptime:** status page reports **100.0% over 90 days** across market data
  streaming and REST.
- **No index products.** VIX/DXY/TNX are not available at any tier — proxies or
  another vendor.
- **Multi-symbol snapshots**: comma-separated symbols, `limit` up to 10,000,
  cursor pagination. Breadth across 500 names is one or two REST calls, not 500 —
  which is what makes the free tier viable for this.

### Massive (formerly Polygon.io; both domains live in parallel)

| Product | Plan | Price | Data |
| --- | --- | ---: | --- |
| Stocks | Basic | free | 5 calls/min, end-of-day only, 2y history |
| Stocks | Starter | $29/mo | unlimited calls, **15-min delayed** |
| Stocks | Developer | $79/mo | unlimited calls, **15-min delayed** |
| Stocks | Advanced | $199/mo | unlimited calls, **real-time**, 20+y history |
| Indices | Starter | $49/mo | all index tickers, 15-min delayed, WebSockets |
| Indices | **Advanced** | **$99/mo** | all index tickers, **real-time**, WebSockets |

- **Uptime (published, 90 days):** Stocks REST 99.86%, Stocks WebSocket 99.96%,
  Indices REST 99.95%, Indices WebSocket 99.99%, Forex 99.99%.
- Recent incidents are disclosed openly (stale quotes 20–21 Aug, REST latency
  19 Aug, slowness 25 Aug). Transparency is a point in its favour, but 99.86% on
  the stocks REST path is roughly **60 minutes of monthly downtime**.
- **The one thing nobody else here sells cheaply: real index values.** Indices
  Advanced at $99/mo is the only clean way to put a true VIX/DXY print on the
  strip.

### Databento

- Usage-based, plus subscription plans (CME Standard from $179/mo).
- **p90 latency 42µs cross-connect, 590µs over the internet.** Colocated capture
  in exchange facilities.
- **Wrong tool.** This is HFT-grade infrastructure priced accordingly. A
  dashboard on a 30-second refresh cannot use six orders of magnitude of latency
  advantage. Rejected on fit, not on quality.

### Second-tier API vendors

| Vendor | Free tier | Paid entry |
| --- | --- | ---: |
| Finnhub | 60 calls/min | $49.99/mo |
| Twelve Data | 8 credits/min, 800/day | $29/mo |
| Tiingo | 1,000 req/day | — |
| EODHD | — | ~$20/mo (EOD, 60+ exchanges) |

None publishes component-level 90-day uptime the way Alpaca and Massive do.
**For a selection criterion of "highest uptime", an unpublished uptime figure is
itself disqualifying** — it cannot be verified before purchase or monitored after.

### Free, no-vendor sources

- **FRED** (St. Louis Fed) — free key, 120 req/min, `DGS10` for the 10-year yield
  and the full dollar-index series. Daily, authoritative, no redistribution
  ambiguity. This is a *better* source for rates than any equity proxy.
- **CBOE VIX history CSV** — public, no key, daily.

## 3. Recommendation

### Adopt now — Tier 1, $0/month

**Alpaca Basic + FRED + CBOE.** No new vendor, no spend, no procurement.

| Panel | Source | Staleness |
| --- | --- | --- |
| Sector heatmap | Alpaca snapshots, 11 sector ETFs | IEX real-time / 15-min SIP |
| Market breadth | Alpaca snapshots, `dailyBar` vs `prevDailyBar` across the universe | same |
| Cross-asset strip | UUP / VIXY / USO via Alpaca; `DGS10` via FRED | ETFs as above; rates daily |

Breadth is a slow indicator. A 15-minute-delayed advance/decline line is
genuinely fit for purpose on a solo desk, and pretending otherwise is how people
talk themselves into a $200/month subscription they use to read a heatmap.

**Caveat requiring a spike, not a decision:** whether Basic's snapshot response
populates `dailyBar`/`prevDailyBar` for non-IEX-traded names needs verifying
against a live key. It cannot be verified from documentation, and it decides
whether breadth needs Tier 2. Budget half a day.

### Upgrade trigger — Tier 2, $99/month

**Alpaca Algo Trader Plus**, if and only if one of these becomes true:

- the 15-minute delay materially changes a decision the desk makes, or
- the watchlist needs more than 30 streamed symbols, or
- the Tier 1 spike shows breadth cannot be built on Basic.

Single vendor, already integrated, full SIP, unlimited streaming. At $99 it is
half the price of Massive Stocks Advanced for the same job on this stack.

### Only if genuinely needed — Tier 3, +$99/month

**Massive Indices Advanced**, only if the desk wants true index prints rather
than ETF proxies. UUP is not DXY and VIXY is not VIX; they track, they are not
equal. If someone will make a call on the actual level, buy the actual level.
Otherwise this is $1,188/year for cosmetic accuracy.

### Rejected

- **Databento** — excellent, and wrong for a 30-second dashboard.
- **Massive Stocks Advanced ($199)** — strictly worse value than Alpaca Algo
  Trader Plus here, given Alpaca is already integrated.
- **Finnhub / Twelve Data / Tiingo / EODHD** — no published component uptime,
  and no capability the above lacks.

## 4. Cost summary

| Path | Monthly | Annual | Unblocks |
| --- | ---: | ---: | --- |
| Tier 1 | **$0** | **$0** | All three panels, 15-min staleness class |
| Tier 1 + 2 | $99 | $1,188 | Same, real-time, unlimited streaming |
| Tier 1 + 2 + 3 | $198 | $2,376 | Same, plus true index prints |

For reference, the terminal blueprint this work was reviewed against assumed
roughly $3,000/year of infrastructure. Tier 1 delivers the blocked panels at zero
marginal cost, because the expensive part of that blueprint was inference, not
market data.

## 5. Verification notes

- Uptime figures are the vendors' own published 90-day numbers. Alpaca's status
  page exposes fewer components than Massive's; a page reporting 100% across four
  coarse components is **not strictly comparable** to one reporting 99.86% across
  twenty granular ones. Treat both as directional.
- Massive's stocks REST 99.86% implies roughly an hour of monthly downtime. Any
  integration should degrade to "unavailable" rather than to a stale number —
  the pattern the terminal panels already follow.
- Re-verify pricing before spend. Polygon.io rebranded to Massive and both
  domains currently run in parallel; third-party pricing summaries for it are
  already inconsistent with the vendor's own page.
