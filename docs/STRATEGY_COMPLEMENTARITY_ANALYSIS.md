# Strategy Complementarity — pre-registered analysis

**Status: PRE-REGISTRATION, written 2026-09-15 BEFORE running anything.**
Hypotheses, method, bucket boundaries and pass/fail criteria are fixed here so results cannot be
retrofitted to them. Results go in §6, appended, with this section unedited.

---

## 1. The question

*Do HYDRA's strategies have genuinely different conditional performance, such that running several
together improves risk-adjusted return — and if not, what shape would a real complement have?*

## 2. Why this needs pre-registering

This project has already made the exact error this analysis invites. On 2026-09-02 the 11:15 entry
slot was cut for being the worst of seven; a later permutation test put the **entire** per-slot
effect at **p=0.569**, and the cut was reversed. The method was "rank N things, keep the best" with
no test against chance.

Searching 7 variants for "the combination that works together" is the same procedure one level up,
over ~26 overlapping days. **Any pairing will look good.** So:

- every hypothesis is stated below, before results exist;
- every bucket boundary is fixed below, before results exist;
- any claimed effect must survive a permutation test at **p < 0.05**, reported even when it fails;
- "no effect found" is a publishable outcome here, and the most likely one.

## 3. What is already established (not under test)

Measured 2026-09-15 on overlapping traded days, live era:

```
A↔G +0.84 · A↔C +0.78 · C↔G +0.77 · B↔C +0.73 · A↔B +0.64 · B↔G +0.59
direction agreement vs B: A 88% · C 89% · G 82%
```

**A, B, C, F, G are one trade in five costumes** — all short-premium 0DTE SPX. This is not under
test; it is the premise. D and E are structurally different (multi-day net-debit calendars) but have
**5 and 1** traded days, which is not enough to measure anything.

## 4. Hypotheses

| | Hypothesis | Falsified by |
|---|---|---|
| **H1** | B's daily P&L falls as realised SPX movement rises — i.e. its losses are a *trend-day* phenomenon, not random. | No monotonic relationship across the pre-set movement buckets, or a permutation test that fails to reject chance. |
| **H2** | No existing variant has a materially different conditional profile from B once normalised per contract. | Any variant with a **positive** mean in the high-movement bucket where B is **negative**, surviving permutation at p<0.05. |
| **H3** | The missing complement is *specifiable*: there exists a quantifiable P&L profile on high-movement days that would offset B's losses there without costing more than it saves. | The required offset exceeds what any realistic instrument could earn — i.e. the complement would have to be a better strategy than B itself. |

**H3 has a known precedent that must be honoured.** The Brandon overlay hedges were switched off on
2026-09-04 because hedge debits ($1,925–$2,240) *exceeded* the IC-side loss they defended (~$1,400,
already capped by the A2 stop). That is this exact idea, already tried, already failed. Any answer to
H3 that does not clear that bar is not an answer.

## 5. Method — fixed in advance

**Population.** Traded days only (`entries_placed > 0`). Per-variant era floors: B from its live-seat
date (`analysis_eras.LIVE_ERA_SINCE`, 2026-07-24); others from the same date for comparability, with
their full history reported separately and clearly labelled.

**Normalisation.** All P&L divided by `contracts_per_entry` — variants run 1c to 10c and raw dollars
are not comparable. Correlation is scale-invariant and reported raw.

**Regime features** (all from `daily_summaries`, no new data collection):
- `move_pct` = |spx_close − spx_open| / spx_open — the trend magnitude that hurts a short IC
- `range_pct` = (spx_high − spx_low) / spx_open — the whipsaw magnitude
- `vix_open`
- `overnight_gap`

**Buckets — FIXED NOW, not tuned to results.** Terciles of the *pooled* distribution across all
traded days, so boundaries do not depend on which variant is being examined:
- `move_pct`: Q1 (quiet) / Q2 (moderate) / Q3 (trending)
- `range_pct`: same

**Statistics.**
- Per bucket: n, mean, median, and a **bootstrap 95% CI** (10,000 resamples). A CI spanning zero is
  reported as "no signal", never as a direction.
- **Permutation test** for every claimed effect: shuffle the day→bucket assignment 10,000 times,
  recompute the statistic, report the fraction of shuffles at least as extreme. This is the guard the
  slot analysis lacked.
- **Multiple comparisons acknowledged**: 7 variants × 3 buckets × 2 features = 42 cells. At p<0.05
  roughly **2 cells will look significant by chance alone**. A single significant cell is therefore
  NOT a finding; it must be part of a monotonic pattern to count.

**Sample-size honesty.** B has ~26 traded live-era days ⇒ ~8–9 per bucket. This analysis can
plausibly detect only a large effect. Where it cannot conclude, it says so, and says what n would be
needed.

## 6. Pass / fail, decided in advance

- **H1 supported** ⇒ B's failure mode is characterised, and the complement can be specified by it.
- **H2 supported** (expected) ⇒ **do not add another premium seller.** The existing variants are not a
  portfolio and must stop being counted as one.
- **H2 falsified** ⇒ name the variant and the regime; that is a real finding worth sizing.
- **H3 unanswerable at this n** ⇒ state the required data and stop. **Do not pick a complement on
  26 days.**

**The recommendation this analysis is allowed to produce** is a *specification* for a complement
(what it must earn, in which regime, at what cost), never a instruction to deploy one. Deployment
needs its own validation, and the account is not funded.

---

## 7. Results

*(appended after the run — this section did not exist at pre-registration time)*
