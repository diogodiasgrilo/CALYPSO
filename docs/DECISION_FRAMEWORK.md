# How we decide — evidence tiers, and when each question gets answered

**Written 2026-09-29.** The governing document for
[`PREREG_SLOT_PRUNE_2026_09_29.md`](PREREG_SLOT_PRUNE_2026_09_29.md),
[`PR1_PR2_DEEP_DIVE_2026_09_28.md`](PR1_PR2_DEEP_DIVE_2026_09_28.md) and the config freeze in
[`GO_LIVE_MASTER.md`](GO_LIVE_MASTER.md) §2-quater.

---

## 1. The problem: statistical significance will not arrive in time

Measured on B's live era (47 days, 101 entries):

| question | what it would take to settle statistically |
|---|---|
| Does B have an edge at all? | t = **0.83** today; **~179 traded days (~9 months)** for t = 2 |
| Do the extra entries cover their commission? | **~771 entries ≈ 18 months** |
| Is e#4 (11:15) a losing slot? | **27 more entries ≈ 66 trading days** |
| Is any single slot better than another? | 7 slots tested → after multiple-comparison correction only **one** clears, on n = 11 |

Meanwhile the config carries **154 free parameters**. Waiting for significance on each is not a
plan; it is a decision to change nothing for years while paying the costs.

**So significance cannot be the authorising test. It has to be the VETO test.** The question is
never "has the data proven this?" — it is "what KIND of evidence is this, and does the data
contradict it?"

---

## 2. The ladder — what a change needs, by where the evidence comes from

### Tier 1 — Mechanism. Decide immediately, no waiting.

The claim is true by construction and does not depend on a P&L run.

* A defined-risk spread's loss is bounded by width — so a **$2,000 hedge defending a $1,400
  bounded loss roughly doubles exposure**. Hedges stay off. No sample size changes that.
* A flatten cushion set **below the largest final-10-minute move ever observed (18.4pt)** cannot
  cover a move we have already seen. Raised to 20pt.
* An entry cap that lets exposure **double when spread width doubles** in a regime never traded.
  Capped `[7,7,3,1]`.

**Test:** *would I make this change if the P&L had come out the other way?* If yes, it is Tier 1.

### Tier 2 — Falsification. Decide on the broken claim, not on profit.

The mechanism makes a specific, checkable claim, and the claim fails.

* The GEX adjuster says *"this strike sits in a dangerous acceleration zone."* Across **23 vetoes,
  the vetoed strike was breached 0 times** — misses up to 43.9pt. On 2026-09-28 it vetoed 7755
  twice while B sold 7720 three times and **7720 is what got breached**.
* This is not "the veto was unprofitable". It is "the veto's stated reason is false".

**Test:** can the mechanism's claim be stated crisply enough to be wrong? Then score *that*, not
P&L. Scoring on P&L would let a lucky run rescue a broken signal.

### Tier 3 — Convergence. Source and data point the same way. Act, with a non-inferiority guard.

The strongest tier available at small n, and the answer to *"inspired by the original, decided by
our data"*: **use them as two independent witnesses and act where they agree.**

Worked example — **one entry a day**:

| witness | says |
|---|---|
| **Source** | *"this is a one trade per day strategy"*, entered 9:31. B's 7-slot grid came from MEIC, not from him. |
| **Our data** | the 68 non-first entries produce **−$0.68 gross each** and cost **$41.63 each** in commission. Point estimate of their net contribution: **−$2,831**. |
| **Mechanism** | commission is **certain**; their gross edge is **measured at zero**. Paying a certain cost for a benefit whose point estimate is zero is negative-EV even when the CI is wide. |

⚠️ **And the honest limit:** the 95% CI on those entries' gross is **[−$139, +$138]**, which spans
the commission. **The data does not prove they fail to pay for themselves.** Tier 3 acts on
convergence *despite* that, which is exactly why it carries a guard:

**Non-inferiority, not superiority.** A rule that comes from outside our data needs the data only
to show it does no harm. Adopt if the out-of-sample difference is **≥ 0**; reject only if it is
**negative with |t| ≥ 2**. Requiring it to prove itself would demand the 18 months we do not have.

### Tier 4 — Data-only. Full out-of-sample significance, corrected. Expect months.

We found it in our own P&L and have no mechanism and no source for it.

* Pre-register before any out-of-sample data exists, with the cut-off date, the statistic, the
  required n, and the threshold all fixed in writing.
* **Correct for how many things were tested.** Seven slots were compared, so |t| ≳ 3.6, not 2.
* On failure the hypothesis is recorded **FAILED** — not retested with a longer window, a
  different statistic, or a neighbouring slot.
* Example: **e#4 only**, 27 out-of-sample entries, prune iff mean < 0 **and** |t| ≥ 2.
* **e#5 was removed from this tier** on inspection: t = −0.73 is noise, and it only looked bad
  because its total is large, which is an artifact of n.

---

## 3. Where each open question sits today

| question | tier | status |
|---|---|---|
| Hedges re-enabled? | 1 | **No.** Structural. Revisit only via arming gate + sizing, not by flipping. |
| Flatten cushion | 1 | **Done** — 20pt |
| Entry cap in unobserved VIX zones | 1 | **Done** — `[7,7,3,1]` |
| GEX SKIP → SHIFT | 2 | **Shadow deployed** 2026-09-29. Flip on **breach rate**, not profit. |
| One entry a day | 3 | **Registered** — non-inferiority, 40 days, first evaluable 2026-09-30 |
| e#4 prune | 4 | **Registered** — 27 entries, ~66 trading days |
| VIX 19–40 floor | — | **No.** Applying it retroactively gives **zero trades in 45 days**. |
| Any per-slot concentration | 4 | Not registered. Only e#7 survives correction, on n = 11, and its record may measure the **late credit gate's selectivity** rather than the slot. |

---

## 4. When — the calendar, and the thing that gates it

**No out-of-sample clock runs while the config changes.** Commits to B's money path have run
2,1,2,3,8,7,10,20,10,18 per week; 30 change-days out of ~45 trading days; longest clean stretch
**9 calendar days**.

```
  now ──► 15 consecutive trading days with NO economics-changing commit
              └─► the measurement clock STARTS
                    ├─ ~40 days  → Tier 3: one entry a day
                    ├─ ~66 days  → Tier 4: e#4
                    └─ ~179 days → is there an edge at all (t = 2)
```

**Achieving 15 clean days is itself the next gate**, and it is a better one than any P&L number
because it is not noisy. Defect fixes are permitted and still reset the clock — a bug fix changes
the distribution just as surely as a tune does.

---

## 5. The rules that keep this honest

1. **Register before the data.** Cut-off date, statistic, n, threshold — in writing, in the repo.
2. **Correct for the number of things tested.** Best-of-N is not a finding.
3. **A failed test is FAILED.** No longer window, no neighbouring slot, no new statistic.
4. **Never score a broken mechanism on profit.** A lucky run must not rescue a false claim.
5. **Separate the accounting from the trading.** B's reported +$4,240.60 is **−$574.40 of trading**
   plus a **+$5,995 failed-entry windfall**. Decisions read the trading number.
6. **Watch the certain costs.** Commission is **49.8% of gross**: B earns **$35.94/entry** and pays
   **$41.63/entry**. The largest lever found in this whole analysis was turnover, not any
   strategy parameter.

---

## 6. What "optimised" means here

Not "tuned until the backtest is pretty". With 154 parameters and 47 days, that direction is a
guarantee of fitting noise — B's own Sharpe fell 4.96 → 2.37 purely as a tail day arrived, while
its daily mean never moved.

Optimised means: **every knob traceable to a mechanism, a falsification, or a converged
source-and-data finding — and everything else frozen and measured.** The source supplies
hypotheses we could not generate ourselves. Our data supplies the veto. Neither is authoritative
alone, and the places where they agree are the only cheap decisions on the board.
