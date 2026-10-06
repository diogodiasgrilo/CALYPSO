# Re-scoring the strategy backlog on MEASURABILITY

**Date:** 2026-10-06
**Supplements** (does not replace) [`STRATEGY_CANDIDATES.md`](STRATEGY_CANDIDATES.md),
which scores candidates on ease, differentiation, uncorrelatedness, credibility
and overhead. It does **not** score whether you could ever tell if one worked.

## Why this exists

Variant B has traded 37 live days. Its edge is **t = 0.24** and proving it would
take about **2,486 trading days — ten years**. That is not a B problem. It is
what happens when a strategy's daily swing is **25x** its daily edge.

Every candidate in the backlog was scored on plausibility and novelty. None was
scored on the one property that decides whether building it can ever produce an
answer. This fixes that.

## The metric

Detection time at the usual bar is

```
trades_needed = (2 * SD_per_trade / edge_per_trade)^2
years         = trades_needed / trades_per_year
```

So two things matter, and **frequency is often decisive on its own**:

| per-trade ratio | trades needed | at 250/yr | at 100/yr | at 50/yr | at 18/yr |
|---|---|---|---|---|---|
| 0.05 | 1,600 | 6.4y | 16y | 32y | 89y |
| 0.10 | 400 | 1.6y | 4.0y | 8.0y | 22y |
| 0.15 | 178 | 0.7y | 1.8y | 3.6y | 9.9y |
| 0.20 | 100 | 0.4y | 1.0y | 2.0y | 5.6y |
| 0.30 | 44 | 0.2y | 0.4y | 0.9y | 2.5y |

## Calibration — what the running fleet actually achieves

Per-TRADE, on each variant's own epoch-scoped record. Per-DAY figures flatter a
strategy that rarely trades, because its zero days shrink the standard deviation
while carrying no information; per-trade is the honest unit.

| variant | trades | trades/day | edge/trade | SD/trade | **ratio** |
|---|---|---|---|---|---|
| A | 114 | 0.79 | +$29.47 | $259.70 | **+0.114** |
| B (live) | 113 | 3.05 | −$14.03 | $687.07 | **−0.020** |
| C | 57 | 1.19 | −$123.13 | $720.57 | **−0.171** |
| F | 8 | 0.89 | +$15.62 | **$47.95** | **+0.326** |
| G | 50 | 2.00 | −$8.73 | $225.34 | **−0.039** |

⚠️ F's n = 8. That is a structural observation, not evidence: what matters is
that its SD per trade is **14x smaller than B's**, because it trades one
contract on a fixed 10pt width instead of seven contracts across a 7-slot grid.

⚠️ A's per-trade figure covers only trades since 2026-07-01 (when
`realized_pnl` began being recorded) while its per-day figure covers all 145
days. Do not compare the two directly.

## The re-scored backlog

`trades/yr` is derived from each candidate's own stated holding period.
`ratio needed` is what the strategy must achieve per trade to resolve inside
**two years**.

| candidate | hold | trades/yr | ratio needed for 2y | verdict |
|---|---|---|---|---|
| **0DTE Breakeven IC** (Sandvand) | 0DTE | ~1,800 | **0.047** | most measurable — but it **IS** A/B/C already |
| **0DTE Mean Reversion** (Ghauri) | 0DTE, event | ~200 | **0.141** | already built as **F**, the only positive ratio in the fleet |
| METF (Yaklin) | 0DTE, 6/day | ~1,500 nominal | ~0.05 nominal | ⚠️ entries are same-day and correlated — effective count is nearer 250, so **~0.13**. HYDRA ran this mechanic and reverted it |
| Boomer Dan Levitation | 0DTE | ~250 | 0.126 | creator states on record he has "absolutely no edge" in the directional call the payoff depends on |
| Flyagonal (Ganz) | 3–5 days | ~100 | **0.200** | 95% win rate with a $4,500 tail — the tail IS the measurement, so it needs far more than 100 |
| Skyline (Khurana) | weekly | ~50 | **0.283** | −54.2% max drawdown; backtest sums two separate 6-leg sims |
| Time Flies (Black) | weekly | ~50 | **0.283** | explicitly **discretionary**, "almost artistic" — cannot be automated, so cannot be measured |
| SPX Put Credit Spread | ~4 days | ~60 | **0.258** | no numeric stop rule exists to encode |
| Ratio Diagonal (Woods) | weeks | ~20 | **0.447** | creator on camera: "just a lottery ticket" |
| **21 DTE Put BWB** (Allen) | ~14 days | **~18** | **0.471** | 🔴 **the backlog's TOP pick is the WORST here** |
| VIX Spike (Kwan) | event | ~10 | **0.632** | no performance data of any kind exists |

## The inversion

**The highest-scored candidate in the existing backlog — 21 DTE Put Broken Wing
Butterfly, the only one to reach "worth considering" — is the least measurable
thing on the list.** At ~18 trades a year it would need a per-trade edge-to-noise
ratio of **0.471** to be confirmed in two years. The best ratio anywhere in the
live fleet is F's **0.326**, on eight trades. The independent Monte Carlo of the
BWB found EV of **+$0.92/trade** and called it "on the edge with a very low EV" —
nowhere near 0.471. **Building it would produce something that cannot be
evaluated within a decade.**

Conversely the structural profile that IS measurable — high frequency, small
defined risk, truncated losses — describes **F**, which the backlog rated a
*weak candidate* (4.6) and which was built anyway.

## What to use this for

1. **Frequency first.** Below ~50 trades/year, no plausible edge resolves in a
   useful time. That rules out five candidates on structure alone, before any
   argument about whether the edge is real.
2. **Judge a backtest's Sharpe per TRADE, not per day.** A sparse strategy's
   daily Sharpe is inflated by its zero days.
3. **Treat a high win rate with a fat tail as expensive to measure**, not as
   safe — Flyagonal's 95% win rate means the rare $4,500 loss carries all the
   information, so you must wait for tail events to learn anything.
4. **This does not say any candidate is unprofitable.** It says how long you
   would wait to find out. A strategy you cannot evaluate is not a strategy you
   can responsibly fund.

## What it does not do

It assumes trades are independent. B's seven daily entries are **not** —
measured, 7 of 7 multi-stop days stopped on a single side and 0 of 11 ever
stopped both, so its effective trade count is far below its nominal one. Any
candidate placing multiple same-day entries on one underlying should be assumed
to have the same problem until shown otherwise, which pushes METF and Boomer Dan
meaningfully worse than their nominal counts suggest.
