# Pre-registration — is `bl`'s clock worse at getting filled than B-A's?

**Registered 2026-10-14, before the answer is known.** Written because on
2026-10-14 I raised `bl`'s low fill rate as a decision for the operator, on the
strength of a comparison between `bl`'s 3 days and B-A's 12 — windows that do
not match. The same-window numbers are:

| variant | placed | slots | fill | window |
|---|---|---|---|---|
| `bl` | 1 | 20 | 5% | 10-09, 10-12, 10-13 |
| B-A (`b`) | 2 | 21 | 10% | same three days |

Two-proportion test: **z = 0.56, p = 0.58** — indistinguishable from identical.
The three days were dry for the *whole* Brandon fleet (A skipped on credit, C
skipped both slots, B-A skipped 7 of 7 on 10-13), not for `bl` specifically.

So there is no finding yet, and this file exists to stop the same
mismatched-window mistake producing one next week.

## The question

Does the 12:15–14:45 grid get filled materially less often than the
09:45–12:45 grid, given identical code, identical GEX data and the same
trading days?

## Decision rule — fixed now

- **Window:** the next **20 trading days** on which BOTH `bl` and `b` run.
- **Metric:** fill rate = `trade_entries` rows ÷ (`trade_entries` +
  `skipped_entries`) per variant, over those days only.
- **Pairing:** the SAME dates for both variants. A date missing for either is
  excluded from both. This is the error that produced the false finding — do
  not compare different windows.
- **Threshold:** at ~130 slots each, a two-proportion test detects ~**8.7 pp**
  at p < 0.05. So:
  - `bl` lower by **≥ 9 pp** at p < 0.05 → the clock genuinely fills worse.
    Then, and only then, it is an operator decision whether to hand the live
    seat back to the morning grid (`scripts/flip_bc_rollback.sh`).
  - otherwise → **no change**, and the result is recorded as "no detectable
    difference", not "needs more data".

## What this does NOT license

- **Changing the GEX veto.** Its own pre-registration
  (`docs/PREREG_GEX_GATE_2026_09_29.md`) returned **KEEP** (n=29, p=0.4634).
  A low fill rate is not new evidence about that filter's EV.
- **Enabling one-sided entries** to raise the fill rate. ~14 of `bl`'s 19 skips
  are require-both-sides, so this would "work" — and one-sided entries are
  measured deeply net negative on B/C despite a 69–77% win rate. It would look
  like a fix and lose money.
- **Reading fill rate as edge.** Filling more is not earning more. This metric
  answers an operational question (does the live seat exercise the order path
  often enough to surface defects — yesterday's ORDER-DBL bug surfaced only
  because `bl` traded), NOT whether the later slots are profitable. That
  question is separately unresolvable: the in-sample per-slot permutation gives
  p = 0.597, and B's own noise-to-edge ratio implies ~24 years to reach |t|=2.

## Verdict

_Unfilled. Due after 20 paired trading days from 2026-10-14._
