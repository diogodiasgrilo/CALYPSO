# PRE-REGISTRATION — slot pruning on variant B

**Registered 2026-09-29, before any out-of-sample data exists. Nothing is pruned today.**

The point of writing this now is that the decision rule cannot be adjusted later to fit whatever
the data does. If a rule below is changed, that change is itself a new registration with a new
cut-off date, and the old one must be recorded as failed rather than quietly replaced.

---

## The hypothesis, and where it came from

B runs a 7-slot grid (09:45 → 12:45) inherited from HYDRA's MEIC lineage. The source B is named
after trades **one** entry a day. Per-slot realized P&L over the live era (2026-07-24 → 09-28,
101 entries) suggested some slots carry the losses.

**That observation is IN-SAMPLE. It generated the hypothesis and cannot also test it.**

---

## What the in-sample evidence actually supports — much less than it first appears

Per-slot gross realized P&L per entry:

| slot | time | n | mean | sd | **t** | verdict |
|---|---|---|---|---|---|---|
| e#1 | 09:45 | 9 | −$15.56 | 766.54 | **−0.06** | noise |
| e#2 | 10:15 | 17 | +$240.06 | 797.63 | +1.24 | suggestive |
| e#3 | 10:45 | 18 | +$38.22 | 473.29 | +0.34 | noise |
| e#4 | 11:15 | 19 | −$291.32 | 750.82 | **−1.69** | suggestive |
| e#5 | 11:45 | 12 | −$146.25 | 696.42 | **−0.73** | **noise** |
| e#6 | 12:15 | 15 | +$188.20 | 320.38 | +2.28 | significant |
| e#7 | 12:45 | 11 | +$315.27 | 118.16 | **+8.85** | significant |

**Seven slots were tested, so the threshold must be corrected for multiple comparisons.**
Bonferroni at α = 0.05 over 7 tests gives α = 0.0071, i.e. |t| ≳ 3.6 at these degrees of freedom.

> **Only e#7 clears that bar. Nothing else does — including both slots I had informally called
> "the bad ones".**

Three consequences, all of which narrow the original idea:

1. **e#5 is NOT a candidate.** t = −0.73 is indistinguishable from zero. Pruning it would be
   fitting to noise. It appeared in the first pass only because its total looked large, which is
   an artifact of n, not of edge. **Dropping e#5 is removed from this registration.**
2. **e#4 is the only defensible candidate**, and even it is merely suggestive at t = −1.69.
3. There is **no structural mechanism**. Credit per hour at risk rises monotonically across the
   grid (e#1 $59.77 → e#7 $100.00) exactly as time decay predicts, with **no anomaly at e#4**
   ($53.06, between e#3's $52.94 and e#5's $79.08). The only thing separating e#4 is its 42% stop
   rate — an *outcome*, not a structural property. Without a mechanism, this rule gets no
   discount on the evidence it needs.

---

## The registered test

**Cut-off: 2026-09-29.** Every entry placed on or before 2026-09-28 is in-sample and is
**excluded from the test**. Only entries from 2026-09-30 onward count.

**Candidate: e#4 (11:15) only.**

| | |
|---|---|
| **Statistic** | mean gross realized P&L per e#4 entry, out-of-sample |
| **Required n** | **27 e#4 entries** (≈ 66 trading days at its observed rate of 0.40/day) |
| **Prune if** | out-of-sample mean < 0 **and** \|t\| ≥ 2.0 |
| **Keep if** | out-of-sample mean ≥ 0, **or** \|t\| < 2.0 at n = 27 |
| **On "keep"** | the hypothesis is recorded as **FAILED**. It is not re-tested with a longer window, a different statistic, or a different slot. |

`27` is not chosen for convenience — it is `(2·sd/|mean|)² = (2·750.82/291.32)²`, the sample size
at which the *observed* in-sample effect would reach |t| = 2 if it is real.

**Not permitted under this registration:** pruning e#5 · pruning on cumulative P&L rather than
per-entry mean · adding slots to the candidate list after seeing data · stopping the test early
because the number looks good · re-running with commission-adjusted P&L instead of gross (gross
is specified because commission apportionment is an estimate, not a measurement).

---

## A separate, stronger test — and why it needs less evidence

**Reducing the grid toward one entry a day is justified by the SOURCE, not by our P&L.** Brandon
Jones trades once, at 9:31, and B's 7-slot grid came from MEIC, not from him. A rule that comes
from outside the data does not need the data to establish it — it needs the data only to show it
does no harm.

So this is registered as a **non-inferiority** test, which is both more honest and better powered:

| | |
|---|---|
| **Rule** | keep only the first entry of each day |
| **In-sample, for reference only** | all entries −$574.40 vs first-only +$2,277.93 over 33 days |
| **Statistic** | daily net P&L difference (first-only minus all-entries), out-of-sample |
| **Required n** | 40 trading days |
| **Adopt if** | the mean difference is **≥ 0** (i.e. it does not hurt). A positive point estimate is not required to be significant — the source, not the data, is the reason to prefer it. |
| **Reject if** | the mean difference is < 0 with \|t\| ≥ 2 — the data actively contradicts the source |

---

## ⚠️ A caveat on e#7 that must not be forgotten if it is ever acted on

e#7's t = 8.85 is the strongest per-slot number here, but **n = 11 while e#4 has 19** — e#7 fires
*less often*. The plausible reason is that the late-day credit gate is more selective: by 12:45
premium is thin, so e#7 only fires when conditions are unusually good. If so, its record measures
**the selectivity of the gate, not the merit of the slot**, and concentrating size into e#7 would
not reproduce it.

**Nothing in this registration proposes acting on e#7.** It is recorded here so that a future
reader who notices t = 8.85 encounters this paragraph at the same time.

---

## Status

| | |
|---|---|
| Pruned today | **nothing** |
| Live grid | unchanged, all 7 slots |
| Config freeze | holds — see [`GO_LIVE_MASTER.md`](GO_LIVE_MASTER.md) §2-quater |
| First evaluable date | 2026-09-30 |
