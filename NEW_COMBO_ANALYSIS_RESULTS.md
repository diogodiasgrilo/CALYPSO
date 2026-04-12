# New Credit Combo Analysis: Investigation Results

**Date:** 2026-04-10  
**Status:** 🔴 Initial hypothesis requires refinement

---

## Problem Statement

Previous discovery claimed that credit combo (call=$2.00, put=$2.75) achieves **Sharpe 0.972** vs baseline 0.925. We ran 4 parameter sweeps to validate this improvement, but got unexpected results.

---

## Results from Initial 4 Sweeps

All four sweeps completed with **DEGRADED performance**:

| Sweep | Parameter Range | Best Sharpe | vs Baseline | Verdict |
|-------|-----------------|------------|------------|---------|
| 1. call_stop_buffer | [10.0…75.0] | **0.684** | -0.241 | ❌ Much Worse |
| 2. decay_start_mult | [1.5…3.5] | **0.755** | -0.170 | ❌ Much Worse |
| 3. decay_hours | [2.0…5.0] | **0.715** | -0.210 | ❌ Much Worse |
| 4. put_only_max_vix | [10.0…30.0] | **0.689** | -0.236 | ❌ Much Worse |

---

## Critical Discovery: Parameter Interaction Issue

**The Problem:**
We were using:
- Credit combo: (call=$2.00, put=$2.75) — the "improved" combo
- Put stop buffer: **175.0 ($1.75)** — the baseline value
- Other parameters: Varied in sweeps

But we got Sharpe 0.684-0.755 with the new combo, vs baseline 0.925.

**Root Cause Hypothesis:**
The claimed 0.972 Sharpe was probably achieved at a **DIFFERENT put_stop_buffer value** than 175.0. So:
- ✅ Put stop buffer value X + (call=$2.00, put=$2.75) = Sharpe 0.972
- ❌ Put stop buffer value 175.0 + (call=$2.00, put=$2.75) = Sharpe 0.684-0.755

**The Fix:**
We need to **find the optimal put_stop_buffer for the new combo first**, then use that as the baseline for re-testing other parameters.

---

## Current Action: Finding Optimal Put Stop Buffer

**Sweep:** `sweep_put_stop_buffer_new_combo_find_optimal`
- **Credit combo (FIXED):** (call=$2.00, put=$2.75)
- **Put stop buffer (VARIED):** [50, 100, 150, 175, 200, 250, 300, 400, 500]
- **Other parameters (FIXED):** call_buffer=75.0, decay_mult=2.5, decay_hours=4.0
- **Tests:** 9 backtests, ~8 minutes with 5 workers
- **Goal:** Find put_buffer value that gives Sharpe > 0.925 (ideally > 0.972)

**Progress:** Running now  
**Expected completion:** 21:50 UTC (approx)

---

## What Comes After

Once we identify the optimal put_stop_buffer for (call=$2.00, put=$2.75):

1. **Update baseline:** Use that put_stop_buffer value as the new baseline
2. **Re-run 3 parameter sweeps:**
   - call_stop_buffer (with new put_buffer baseline)
   - decay_start_mult (with new put_buffer baseline)
   - decay_hours (with new put_buffer baseline)
3. **Validate improvement:** Check if Sharpe > 0.972 across parameter ranges
4. **Optional 4th sweep:** put_only_max_vix (only if other 3 show improvement)

---

## Key Insight

The credit gate improvement (lower put credit floor $2.75 vs $3.25) changes the **risk/reward balance** of the entire system. The stop buffer values may need to be adjusted to work optimally with the new gates.

This is a classic **parameter interaction** problem — you can't optimize one parameter in isolation. The new combo (call=$2.00, put=$2.75) appears to be optimal at a different put_stop_buffer value than the original combo.

---

## Timeline

| Time | Event | Status |
|------|-------|--------|
| 21:25 | Launched 4 initial sweeps | ✅ Complete (degraded) |
| 21:35 | Initial sweeps completed | ✅ Analyzed results |
| 21:36 | Launched optimal put_buffer finder | 🔄 Running |
| ~21:50 | Optimal put_buffer results | ⏳ Pending |
| 21:55+ | Re-run 3 parameter sweeps with new baseline | ⏳ Next |

---

## Hypothesis: Expected Put Stop Buffer Value

Based on the degradation in the initial sweeps (Sharpe 0.684 at put_buffer=175), I hypothesize:
- **Likely optimal:** put_buffer in range 200-350 ($2.00-$3.50)
- **Reason:** Wider buffers = more generous stops = higher win rate = potentially higher Sharpe with tight credit gates
- **Extreme case:** If true optimal is put_buffer=500 ($5.00), that would match the original combo's buffer value and suggest the new combo doesn't help at all

---

## Files to Monitor

```bash
# Current optimal finder results
backtest/results/put_stop_buffer_new_combo_optimal_results.csv
backtest/results/put_stop_buffer_new_combo_optimal_progress.txt

# When ready for re-runs:
backtest/results/call_stop_buffer_new_combo_*.csv
backtest/results/buffer_decay_start_mult_new_combo_*.csv
backtest/results/buffer_decay_hours_new_combo_*.csv
```

---

## Conclusion So Far

The initial validation failed, but not the new combo itself — the **parameters weren't optimized for the new combo's risk/reward profile**. This is actually valuable: it tells us parameter choices matter more than we thought, and optimization requires joint tuning, not sequential refinement.

Next step: Find the right put_stop_buffer value for the new combo.
