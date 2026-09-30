# day_type / realized_volatility — the column, and its first read

**2026-09-30.** Both columns had been NULL on every row since the schema gained
them. HOMER derived `day_type` from a Google Sheets "Notes" column and Sheets
was retired on 2026-07-17, so the writer had been reading a source that no
longer existed for ten weeks. Fixed forward (`fb3e4ec`) and backfilled over the
recorded history (`6ebc214`, `63d95c8`): **539 rows across all eight variants**,
computed with the live classifier over `market_ticks`, audited in
`data_corrections`, tickless days left NULL rather than guessed.

## Is the measure real?

Yes. Over B's 96 usable days, `corr(realized_volatility, VIX close) = 0.669`,
with realized running at 0.45× implied — realized below implied is the ordinary
vol risk premium, and an intraday-only realized measure against 30-day implied
should sit below it. The number tracks the thing it claims to measure.

## Is there a day_type edge? No — and the first look would have said otherwise.

Asked of the whole history, the answer looks emphatic:

| slice | chop | trend |
|---|---|---|
| full history (n=78) | n=51, mean **+$338.27** | n=27, mean **+$63.98** |

A 5× difference in mean daily P&L. It is not real.

Split at the live-seat swap, the sign **flips**:

| era | chop | trend | Welch t |
|---|---|---|---|
| dry-run shadow (< 2026-07-24) | n=30, **+$605.25** | n=16, **−$107.94** | 0.93 |
| **live seat (≥ 2026-07-24)** | n=21, **−$43.13** | n=11, **+$314.05** | **−0.64** |
| full history | n=51, +$338.27 | n=27, +$63.98 | 0.54 |

Daily standard deviations are $1,300–$3,100 against mean differences of a few
hundred dollars. No slice is distinguishable from zero, and the two eras
disagree about which day type is even the profitable one. The full-history
figure is dominated by dry-run shadow days, whose fills are simulated and
optimistic — which is the same contamination that makes every dry-run-vs-live
comparison in this project untrustworthy.

The RV buckets are worse: the best bucket (7–10) is n=10 and the worst (≥10) is
n=3. At n=3 there is nothing to read.

## What this column is, and what it is not

It is an **explanatory** variable, not a **predictive** one. `day_type` is
computed from the session's own close: at 10:45, when the entry is placed,
nobody knows whether the day will end as trend or chop. It cannot be traded on.
Its job is to let a post-hoc question — "did slot e#4 lose because of the slot,
or because those happened to be trend days?" — be asked at all. Until now every
per-slot and per-entry result this month pooled the two together with no way to
tell.

## Registered, not acted on

**Hypothesis:** realized volatility measured EARLY in the session (through
11:00 ET, before the last entries) predicts the day's eventual type, and
therefore is tradeable in a way `day_type` itself is not.

This is deliberately **not implemented**. It is the shape of thing that fits
noise easily — a threshold on a continuous variable, chosen after seeing the
data, against 32 live days. Pre-register a threshold and a decision rule before
computing anything, per `docs/DECISION_FRAMEWORK.md`, and let the sample grow.

**Decision rule when revisited:** require the live-era chop/trend difference to
reach |t| > 2.0 on n ≥ 60 traded live days before any entry logic conditions on
volatility state. Today |t| = 0.64 on n = 32.
