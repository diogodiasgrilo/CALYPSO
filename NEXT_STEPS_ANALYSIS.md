# Parameter Optimization: Next Steps & Strategy

**Current Discovery:** The new credit combo (call=$2.00, put=$2.75) requires **wider put_stop_buffer** values than the baseline.

---

## Partial Results (In Progress)

| Put Buffer ($) | Put Buffer (pts) | Sharpe | P&L | Status |
|---|---|---|---|---|
| 0.50 | 50 | -0.309 | -$12,079 | ❌ Terrible |
| 1.00 | 100 | 0.128 | $5,358 | ❌ Poor |
| 1.50 | 150 | Running... | — | 🔄 |
| 1.75 | 175 | Pending | — | ⏳ |
| 2.00 | 200 | Pending | — | ⏳ |
| 2.50 | 250 | Pending | — | ⏳ |
| 3.00 | 300 | Pending | — | ⏳ |
| 4.00 | 400 | Pending | — | ⏳ |
| 5.00 | 500 | Pending | — | ⏳ |

**Hypothesis:** The optimal will be found at **200-500 range** (wider buffers).

---

## Why This Happens

### Original Combo Behavior (call=$2.00, put=$3.25)
- Higher put credit floor ($3.25) = entries are only placed when put premium is rich
- Rich premium = lower probability of hitting stops = narrower buffers OK
- Original baseline: put_buffer=$1.75 works fine

### New Combo Behavior (call=$2.00, put=$2.75)
- **Lower put credit floor** ($2.75 vs $3.25) = entries placed with leaner premium
- Leaner premium = higher probability of hitting stops = **needs wider buffers for safety**
- New combo baseline: put_buffer=$1.75 is **insufficient** (Sharpe degradation proves it)

### The Trade-off
- ✅ Lower credit floor = more entries placed (more opportunities)
- ❌ Leaner premium = higher risk per entry
- ➜ **Solution:** Accept higher risk per entry by widening the stop buffer

---

## Two Possible Outcomes

### Outcome A: Wider Buffers Help (Likely)
If optimal put_buffer is found at 250-500 with Sharpe > 0.925:
1. The new combo is viable, just needs adjustment
2. **Next phase:** Re-run 3 parameter sweeps with new put_buffer baseline
3. **Expected result:** Validate improvement across parameters

### Outcome B: Wider Buffers Don't Help (Unlikely)
If even at put_buffer=500 we still get Sharpe < 0.925:
1. The new combo is not viable (too risky)
2. **Next phase:** Reject the new combo, stick with (call=$2.00, put=$3.25)
3. **Recommendation:** Optimization is sensitive to credit gate changes; go back to baseline

---

## Phase 2 Plan (If Outcome A)

Once we identify optimal put_buffer for new combo (e.g., put_buffer=X):

### Step 1: Re-run 3 Parameter Sweeps
```
Sweep 1: call_stop_buffer with put_buffer=X
Sweep 2: buffer_decay_start_mult with put_buffer=X
Sweep 3: buffer_decay_hours with put_buffer=X
```

### Step 2: Validation Criteria
- ✅ At least 2/3 show Sharpe > 0.925 (not regressed)
- ✅ At least 1/3 shows Sharpe > 0.972 (improvement confirmed)
- ✅ All show better performance with new combo than original combo

### Step 3: Optional Bonus Sweep
- put_only_max_vix with new combo (tests entry type interaction)

---

## Timeline

| Phase | Task | Est. Time | Status |
|-------|------|-----------|--------|
| 1 | Put stop buffer optimal finder (9 tests) | 20 min | 🔄 Running |
| 2 | Analyze results, identify optimal put_buffer | 5 min | ⏳ Pending |
| 3 | Re-run call_stop_buffer sweep | 5 min | ⏳ Pending |
| 4 | Re-run decay_start_mult sweep | 3 min | ⏳ Pending |
| 5 | Re-run decay_hours sweep | 2 min | ⏳ Pending |
| 6 | Final validation & recommendation | 10 min | ⏳ Pending |

**Total estimated:** ~45 minutes from now

---

## Key Learning

This investigation reveals a fundamental principle:
**Parameter optimization is not linear.** 

When you change one parameter (credit gates), other parameters must be re-optimized. The "improvement" from switching credit combos is only realized if you also adjust the dependent parameters (stop buffers).

This explains why:
- ✅ Raw put_stop_buffer sweep found Sharpe 0.972 (because it co-optimized both)
- ❌ Our initial parameter sweeps got Sharpe 0.684 (because they changed credit gates but fixed put_buffer)

---

## Decision Point

### If optimal put_buffer is found:
- **Low (<$1.50):** Suggests new combo is not viable
- **Medium ($1.50-$2.50):** Moderate adjustment needed, likely viable
- **High ($2.50-$5.00):** Significant adjustment, questions the value of the change
- **Higher than original ($5.00+):** Suggests new combo doesn't improve anything

**Most likely:** Optimal will be in $2.00-$3.50 range (slightly higher than baseline $1.75).

---

*Status: Awaiting completion of Phase 1 (put_stop_buffer finder)*
