# What would actually soften B's P&L curve

**Question asked (2026-09-24):** *"Research strategies that could help B out — something that wins
on days B really loses, but wouldn't lose money all the time."*

I started on that and the data redirected it. **The biggest lever is not a hedge, and the premise
that B loses on big-move days is not what its own record shows.**

---

## 1. B does NOT lose on big-move days

The intuitive story — short premium gets hurt by large moves, so buy convexity — does not survive
contact with B's 95 live days.

| day type | n | B mean/day | losing days |
|---|---|---|---|
| SPX range **> 1.0%** | 21 | **−$169** | 7 / 21 |
| SPX range **≤ 1.0%** | 74 | **+$342** | 16 / 74 |

B wins **two-thirds** of large-range days. And its worst days are utterly ordinary:

| date | B net | SPX range | |
|---|---|---|---|
| 2026-07-16 | **−$7,074** | 0.88% | an average day |
| 2026-07-13 | **−$6,809** | 0.76% | below average |
| 2026-07-24 | −$1,793 | 0.86% | close-to-open move of **0.02%** |

**A tail hedge cannot help here.** Convexity pays on the days B is already fine, and stays silent
on the days that hurt. That is the same mechanism that made [H fail as a
hedge](H_AS_A_HEDGE_FOR_B_2026_09_24.md) — and it is worse than that analysis suggested, because
the days needing cover are not large-move days at all.

---

## 2. B's bad days are STOP days 🔴

Decomposing the worst sessions by what actually closed:

| date | daily net | composition |
|---|---|---|
| 2026-07-16 | −$7,074 | **4 stop-losses = −$7,650** (2 early closes +$775) |
| 2026-07-13 | −$6,809 | **3 stop-losses = −$5,825** |
| 2026-09-21 | −$3,088 | **3 stop-losses = −$3,260** |
| 2026-09-01 | −$1,343 | **2 stop-losses = −$2,800** |
| 2026-07-24 | −$1,793 | **1 stop-loss = −$1,295** on a 0.02% day |

Lifetime: `trade_stops` sum to **−$137,340** against a net of **+$21,742**. The stops are the
entire loss side of the business.

This follows from the structure. B runs **5pt-wide spreads at 7 contracts** with the A2
%-of-width stop at 40%: `0.40 × 5 × 100 × 7 = $1,400` **per side**. Four sides stopping costs
$5,600 — and on a narrow spread, "price reached my short strike" is an ordinary 0.5–1% excursion,
not a tail event. B is not being hurt by volatility; it is being hurt by **routine moves reaching
close strikes, converted into realised losses by its own stop.**

---

## 3. And the stop looks net-negative

For every `stop_loss` side with a matching entry row, comparing what the stop cost against what
that side would have cost at settlement (capped at the spread width):

| | count | amount |
|---|---|---|
| stop **saved** money | 6 | $14,169 avoided |
| stop **cost** money | 15 | $26,550 given up |
| **net effect of stopping vs holding** | | **−$12,381** |

**15 of 21 stops fired on sides that finished cheaper than the stop.** A 29% hit rate on a
mechanism that produces the entire loss side.

⚠️ **Caveats, because this is the load-bearing number.** n=21 (only stops with matching entry
rows). "Hold to expiry" is a *counterfactual*, not a free alternative: the external literature is
emphatic that 0DTE gamma explodes in the final 60–90 minutes and that holding narrow spreads near
the strikes into the close carries real risk — so the honest reading is **"this stop is
mis-calibrated," not "remove the stop."**

This is already a known open question in the repo: *"OPEN: does credit+buffer stop fit narrow 5pt
spreads?"* (`brandon_first_live_days_postmortem`), and the A2 %-of-width stop runs in **shadow on
C** awaiting exactly this decision (`brandon_strike_and_stop_followups`). **The data now says the
shadow has something to say.**

---

## 4. So what to do — in order of expected value

### First: re-calibrate the stop (no new strategy, no new capital)
The highest-leverage change available, because it acts on the entire loss side rather than
offsetting a fraction of it. Options worth measuring against the existing shadow data:
* **A wider stop** — 40% of a 5pt width is tight when the short strike is ~8δ away.
* **A time-conditional stop** — loose early, tightening into the gamma hour, instead of one
  constant threshold. MKT-042 buffer decay already does this for the credit+buffer path and is
  explicitly bypassed in A2 mode.
* **Stop on the UNDERLYING, not the mark** — several of these fired on spread marks during moves
  that reverted, which is the same failure mode D hit at the open.

### Second: entry-side selectivity
If routine moves toward the strikes are the damage, the cheapest defence is fewer/further strikes
on days that look directional. B already skips FOMC days; the GEX veto work was aimed at this and
is data-blocked rather than disproven.

### Third — and only then — an external hedge
If you still want a separate leg, the profile it must satisfy is now precise, and it is **not** a
tail hedge:
* pays on **0.5–1% directional excursions** reaching the short strikes
* within the **same session** (B's risk is intraday)
* cheap enough to carry on the ~75% of days nothing happens

That combination is genuinely hard, and it is expensive precisely because such moves are common —
you are buying insurance against an ordinary event. Anything far enough OTM to be cheap will not
pay. **This is why I would exhaust items 1 and 2 first:** they cost nothing to test and act on the
dominant term.

---

## 5. What I would NOT do

* **Do not size H as the hedge.** Measured separately: correlation inverts to **+0.54** once H's
  filters are applied, because "enter when IV is cheap" selects quiet days — B's best days.
* **Do not buy tail convexity.** B wins on 14 of 21 large-range days. Convexity pays there.
* **Do not simply remove the stop.** The counterfactual assumes a holding regime whose gamma risk
  this analysis does not model, and 6 of the 21 stops genuinely saved money.

---

## Reproducing this

```
python -m scripts.analyze_h_hedges_b       # the H-as-hedge model
```
The B decomposition above came from ad-hoc queries over `daily_summaries`, `trade_entries` and
`trade_stops` in `data/variant_b/backtesting.db`; the stop counterfactual joins `trade_stops` to
`trade_entries` and prices each side against that day's `spx_close`, capped at the spread width.
