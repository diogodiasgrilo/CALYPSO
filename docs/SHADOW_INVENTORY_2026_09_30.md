# What is actually being shadow-tested, and is the waiting warranted?

**2026-09-30.** Asked what we are waiting on. The answer splits cleanly in two,
and only one half is really "waiting".

## Nothing is built and waiting to deploy

The queue is empty — 0 unpushed commits, 0 uncommitted files. Nothing sits
finished-but-withheld. What accrues is **evidence**, not code.

## Half of it is genuinely waiting, and the clocks started yesterday

| registered test | needs | has | ready |
|---|---|---|---|
| GEX gate | **25 out-of-sample vetoes** | **0** | ~12–13 trading days |
| slot prune (e#4) | 27 out-of-sample entries | **0** | ~5 weeks |
| one-entry-a-day | 40 out-of-sample days | **1** | ~8 weeks |

⚠️ **Correction.** On 2026-09-30 I reported the GEX gate test as "ready — 28
vetoes against the 25 needed." That was wrong, and wrong in the exact way the
preregistration exists to prevent: **those 28 are IN-SAMPLE.** `PREREG_GEX_GATE`
fixes a cut-off of 2026-09-29 and counts only decisions from 09-30 onward,
because the earlier ones formed the hypothesis. Out-of-sample n is **0**. The
same applies to e#4: 60 entries all-time, **0** since the cut-off.

**Is that waiting warranted? Yes, and it is nearly free.** B has 154 free
parameters against 32 live days and t = 0.83; fitting one more decision to the
same history is the overfitting error itself. Nothing is being withheld from
production while the evidence accrues — the gate keeps running either way, and
the in-sample read says it is *helping* (vetoed strikes breached 0/23 vs 26/131
kept, p = 0.0062). Waiting costs approximately nothing and buys the one thing
that cannot be bought later: a result that was not chosen after seeing it.

## The other half was not waiting. It was unattended.

### `hydra_stop_shadow` — data since July, nobody looked

Resolved 2026-07-14 by full replay (%-of-width net-negative at every threshold →
keep credit+buffer), with an explicit instruction: *"re-run
`stop_shadow.analyze()` periodically; revisit only if the fired-set grows and
the sign flips."* **It had not been re-run in 2.5 months.**

Re-run today. **Both revisit conditions are met — the fired set grew and the
sign flipped:**

| C (acts on credit+buffer) | fired | net | tail-capped | premature |
|---|---|---|---|---|
| 25% | 11 | **+$2,555** | +$5,880 (7) | −$1,680 (2) |
| **40%** | 6 | **+$1,890** | +$1,995 (5) | **$0 (0)** |
| 50% | 1 | +$700 | +$700 (1) | $0 (0) |

July said C would *lose* **−$2,340** at 25%. It now reads **+$2,555**.

**And it is still not actionable — for two reasons that matter more than the
numbers.**

1. **C has been dry-run since the 2026-07-24 swap.** Its "actual credit+buffer
   outcome" is simulated fills, so this is sim-vs-sim. Per
   `OPEN_ITEMS_REVIEW_2026_09_30.md` §G5, dry-run/live parity is unmeasured and
   its *direction is unknown* — the two natural experiments disagree. A sign
   flip measured entirely inside simulation cannot move a live decision.
2. **B already runs 40% %-of-width.** There is nothing to flip on the live seat.
   The analyzer answers "should a credit+buffer variant switch?", and the only
   variant it can ask that of is now a shadow.

So the trigger fired, the answer was examined, and the verdict is **no action** —
recorded here rather than left as a re-run that never happened again.

### `shadow_entries` — 96 days, and no analyzer exists at all

**569 rows across 96 days** (2026-05-05 → 09-29), recording the OTM-based shadow
strike selection against the live delta-target choice. `grep -rl shadow_entries
scripts/` returns only two **migration tests**. Nothing has ever analysed it.

This is the same failure as `audit_fleet_correctness.py` being scheduled by no
timer: the collection was built, the looking never was. Three months of a
controlled A/B on strike selection is sitting unread.

**Recommend: write the analyzer.** It is the largest body of unexamined
evidence in the repo, it needs no waiting, and the comparison it encodes — OTM
multiplier vs 8δ delta-target — is a live open question in
`brandon_strike_and_stop_followups`.

## The distinction worth keeping

**Waiting for evidence that does not exist yet is cheap and correct.**
**Not looking at evidence that already exists is neither.** Two of the three
shadows were in the second category, and the only reason it surfaced is that
someone asked what we were waiting for.
