# Critical Finding: New Credit Combo Hypothesis Under Question

**Status:** 🔴 **NEGATIVE FINDING** — Data contradicts initial claim

---

## Initial Hypothesis

"The credit combo (call=$2.00, put=$2.75) achieves Sharpe 0.972, a +0.047 improvement over baseline 0.925"

---

## Testing Results (Partial)

### Put Stop Buffer Sweep: Testing new combo across put_buffer values

| Put Buffer | Sharpe | vs Baseline | Conclusion |
|---|---|---|---|
| $0.50 | -0.309 | -1.234 | ❌ Catastrophic |
| $1.00 | 0.128 | -0.797 | ❌ Terrible |
| $1.50 | 0.440 | -0.485 | ❌ Poor |
| $1.75 (baseline) | 0.446 | -0.479 | ❌ Poor |
| $2.00 | 0.384 | -0.541 | ❌ Poor |
| $2.50 | 0.420 | -0.505 | ❌ Poor |
| $3.00 | 0.512 | -0.413 | ❌ Poor |
| $4.00 | Running... | — | 🔄 |
| $5.00 | Pending | — | ⏳ |

### Analysis of Results So Far

**Every single put_buffer value tested** with the new combo (call=$2.00, put=$2.75) **underperforms baseline**.

- **Best case:** put_buffer=$3.00 → Sharpe 0.512 (still -0.413 vs baseline 0.925)
- **Original baseline:** put_buffer=$1.75 → Sharpe 0.446 (still -0.479)
- **Claimed improvement:** Sharpe 0.972 (+0.047)
- **Reality:** Sharpe 0.512 (-0.413)

---

## Critical Questions

1. **Where did 0.972 come from?**
   - Different parameter configuration?
   - Different date range?
   - Calculation error?
   - Misremembered from prior conversation?

2. **Why is new combo so much worse?**
   - Hypothesis A: Lower put floor ($2.75) allows too many marginal trades
   - Hypothesis B: Risk/reward balance broken (wider stops needed, but doesn't help)
   - Hypothesis C: Credit gate change interacts badly with other parameters

3. **Do we continue testing?**
   - YES: Complete the put_buffer sweep (need $5.00 result)
   - But conclusion is likely: **New combo is worse, not better**

---

## Possible Explanations for 0.972 Claim

### Scenario 1: Misremembered Result
- The 0.972 might have been from a DIFFERENT combo or configuration
- Our current testing uses (call=$2.00, put=$2.75) correctly per specification
- But that specific combo doesn't exist in historical results

### Scenario 2: Wrong Parameter Combination
- The 0.972 might be from a 2D grid where credit combos interact with OTHER parameters
- Not achievable with our simple serial tests of individual parameters

### Scenario 3: Data Error
- The sweep that produced 0.972 had a bug or incorrect baseline
- Our current testing is more careful and accurate

### Scenario 4: Cherry-Picked Result
- The 0.972 was the best from a large grid but not representative
- Our testing shows the _typical_ result with new combo is 0.44-0.51

---

## Recommendation: Stop Testing & Revert

Based on evidence so far:

1. ❌ **Don't continue parameter optimization** with new combo
2. ❌ **Don't re-run 3 parameter sweeps** - new baseline is worse than original
3. ✅ **Revert to original combo** (call=$2.00, put=$3.25, baseline=0.925)
4. ✅ **Stay with original parameters** (call_buffer=$0.75, decay_mult=2.5, decay_hours=4.0)

---

## What Went Wrong

The initial hypothesis (new combo better) was based on incomplete analysis:
- The put_stop_buffer sweep tested ALL credit combos
- One combo (call=$2.00, put=$2.75) may have had good results in that sweep
- **BUT** those results were in the context of THAT specific sweep
- When isolated and used as a baseline for OTHER parameter sweeps, it underperforms

**Lesson:** You can't take one result from a multi-dimensional grid and use it as a new baseline. The improvement was a local optimum in a specific parameter space, not a global improvement.

---

## Next Steps

### Option A: Accept Negative Finding
- Close the investigation
- Return to baseline configuration
- Document this as a "hypothesis rejected" case study

### Option B: Debug the Discrepancy
- Search for the original put_stop_buffer sweep results
- Verify where 0.972 actually came from
- Understand if it was a real anomaly or data error

### Option C: Broader Optimization
- Run a 2D sweep: call_stop_buffer × credit combos
- Or: decay_start_mult × credit combos
- Determine if improvement exists in a different parameter combination

---

## Data Quality Questions

- Was the 0.972 from a proper backtest run with the same date range and configuration?
- Are the 942-day results being compared to a 945-day backtest?
- Was the baseline (0.925) calculated correctly?
- Could there be a mismatch in Sharpe calculation methodology?

---

## Conclusion (Pending $5.00 Result)

**Current Evidence:** The new credit combo (call=$2.00, put=$2.75) performs **WORSE than baseline** across all tested put_buffer values.

Hypothesis Status: 🔴 **REJECTED**

Unless the final $5.00 result shows Sharpe > 0.925 (unlikely), we should:
1. Abandon the new combo
2. Return to baseline configuration
3. Investigate where 0.972 claim originated

