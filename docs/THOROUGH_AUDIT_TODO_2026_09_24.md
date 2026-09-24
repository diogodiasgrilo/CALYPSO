# Thorough audit — working to-do (2026-09-24)

Everything the operator asked for in one place, so none of it is lost. Updated as
each item lands.

## A. Online research — can the "known open" items actually be closed?

I declared these closed-by-impossibility **without researching them**. That was
an assumption, not a finding.

| # | Item | Status |
|---|---|---|
| A1 | H's IV percentile | ✅ **CLOSED — and my claim was wrong twice over.** (a) Polygon DOES expose per-contract IV and the repo already fetches it (`fetch_polygon_contract_snapshot`). (b) More importantly, the industry-standard IV percentile is **30-day ATM IV ranked over 252 days** — and for an S&P underlying that *is* VIX. H is computing the correct metric, not an approximation. Residual: VIX is variance-swap constructed rather than pure ATM, which a **percentile** is largely insensitive to (rank is invariant under monotone transformation). Polygon has no HISTORICAL IV endpoint, so nothing better is retroactively available anyway. |
| A2 | E's withheld rules | ✅ **CLOSED as far as it can be.** No public source gives the creator's specific double-calendar rules. But the practitioner consensus independently corroborates E's two invented parameters: strikes at the **1-SD expected move** (= `em_fraction: 1.0`) and **exiting 2–3 days before the short expiry** (= `time_exit_days_before: 2`), with profit-taking from **15–20% return on risk** (E's ladder starts at 20%). Reasoned, not arbitrary. |
| A3 | H's +100% target rule | ✅ **CLOSED — and CONFIRMED FAITHFUL.** The article: *"If implied volatility starts low and is expanding, he may aim toward the 100% target. If volatility is already somewhat elevated… he is more likely to take profits around 50%."* That is exactly H's low-AND-rising implementation. It was never an open gap. |
| A4 | D's written source | ✅ Full transcript already audited line by line; every parameter matched. Nothing further found. |
| A5 | F's article | ✅ Rules already recorded in `STRATEGY_CANDIDATES.md` from the article + full transcript; F verified against them and the delta band tightened to the stated 10–25Δ. |

## B. Dashboard line-by-line audits

Only H's page has ever had one. The others have only been touched where something
forced it.

| # | Page | Status |
|---|---|---|
| B1 | **H** | ✅ Re-verified: symbol-aware chart, widest-breach verdict, settlement vs mid-session copy, dry-run LOCKED label. Plus the new range-expansion reading is recorded on the entry. |
| B2 | **D** | ✅ Audited. Copy is D-native (transform credit, risk-free, wings) and correct for D. |
| B3 | **E** | 🔴 **FOUND + FIXED.** The shared calendar page rendered `Transformed: 0` / `Risk-Free: 0` at E permanently — D's concepts, which E has no code for. A zero reads as FAILING at something rather than not attempting it. Now capability-gated, with E's actual model named in the slot. Also fixed the D-vs-E comparison caption (different windows). |
| B4 | **F** | 🔴 **FOUND + FIXED** (FOMC banner read the primary seat's policy). Event-triggered schedule renders correctly; one-sided rendering previously addressed. |
| B5 | **G** | 🔴 **FOUND + FIXED** (same FOMC banner defect — worst here, as G is the undefined-risk naked strangle that trades announcement days). `UndefinedRiskCard` correctly wired via `capital_basis.boundedLoss`. |

## C. Re-verify what I claimed closed

| # | Item | Status |
|---|---|---|
| C1 | VM state | ✅ Verified after each deploy: configs read back from `/opt/calypso`, services active, zero errors, `git status` clean. |
| C2 | The six items | ✅ Re-checked; all closed. A3 turned out to have been faithful all along. |
| C3 | Collateral damage | ⚠️ **Two found and fixed, both mine.** A test file that mutated `CalendarEntry` globally (broke four unrelated tests by import order), and a `_metrics_epoch_date` attribute read that silently disabled the entire metrics self-heal. Full suite 4,940 green. |


---

## Findings from this pass

### 🔴 A3 revealed a rule H did not have — now implemented
The article lists a **third entry condition** beside the IV filter and the cost cap:
*"Seeks range expansion — a period of narrow daily candle ranges that begins to widen."*
For a long strangle that is the thesis stated on the chart. Implemented as
`range_expansion_signal` + `_range_expansion_gate`: the last 10 completed days' median range at
or below 90% of the 60-day median (compression, measured relative so it means the same at any
price), AND the most recent day exceeding that narrow median by 10% (the widening has started).
**Fails OPEN**, unlike the IV gate — the source gives IV a number to clear and gives this none,
so every threshold is ours and must not veto on its own ignorance.

### 🔴 B4/B5 found a cross-wiring bug in the FOMC banner
`FOMCBanner` read the FOMC flags from the **primary seat's** state for every selection. The live
seat is B, which **skips** announcement days; **D, E, F, G and H all trade through them**. So on
an FOMC day, selecting any of those five claimed *"All entries skipped"* while the strategy was
taking positions — reporting a strategy as flat on the day it was most exposed, and **G is the
undefined-risk naked strangle**. Same class as H's band chart plotting SPX against SPY strikes.
Fixed: per-variant `fomc_policy` published in the snapshot, resolved by a pure
`resolveFomcPolicy` helper that tests EXECUTE.

### ⚠️ My own first fix was under-tested
The banner fix was initially covered only by string-presence assertions — and a build with the
bug restored (`const policy = null`) kept two of three green. Extracting the logic to
`lib/fomcPolicy.ts` and running it under node made the control fire properly.
