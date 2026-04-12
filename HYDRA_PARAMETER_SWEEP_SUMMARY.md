# HYDRA Parameter Sweep: New Combo Validation (2026-04-10)

## Objective

Validate whether the credit gate improvement (call=$2.00, put=$2.75) discovered in the put_stop_buffer sweep carries across other parameter values and interacts positively with parameter tuning.

## Background: The Discovery

**Put Stop Buffer Sweep Results:**
- Tested all 42 credit gate combinations with put_stop_buffer range [50.0, 1000.0]
- **Found:** Combo (call=$2.00, put=$2.75) achieves **Sharpe 0.972** vs baseline 0.925
- **Improvement:** +0.047 Sharpe (+5.1%)
- The lower put_credit gate ($2.75 vs $3.25) allows more put-only entries while still maintaining profitability

## Current Testing Strategy

### Why These Sweeps?

Prior sweeps tested call_stop_buffer, decay_start_mult, and decay_hours with the **original** combo (call=$2.00, put=$3.25) and showed **degradation** relative to baseline. Now we re-test with the **improved** combo to see if:

1. The Sharpe improvement carries across different parameter values
2. Parameters can be re-optimized for the new baseline
3. Some parameter values that were suboptimal before might become optimal with the new combo

### The Four Sweeps

#### 1. Call Stop Buffer Sweep (NEW COMBO)
- **Parameter:** call_stop_buffer = [10.0, 20.0, 30.0, 35.0, 40.0, 50.0, 75.0]
- **Fixed:** All other parameters at baseline + new combo (call=$2.00, put=$2.75)
- **Tests:** 7 backtests, ~5 minutes
- **Hypothesis:** Varying the call-side stop buffer with the new combo's tighter credit floors
- **Expected:** Call buffer (stop cushion) may need adjustment given different credit dynamics

#### 2. Buffer Decay Start Multiplier Sweep (NEW COMBO)
- **Parameter:** buffer_decay_start_mult = [1.5, 2.0, 2.5, 3.0, 3.5]
- **Fixed:** All other parameters at baseline + new combo (call=$2.00, put=$2.75)
- **Tests:** 5 backtests, ~3 minutes
- **Hypothesis:** Initial width of decay buffer affects early-day stop cushion. The new combo might benefit from wider or narrower initial buffers.
- **Expected:** Decay multiplier interaction with tighter credit gates

#### 3. Buffer Decay Hours Sweep (NEW COMBO)
- **Parameter:** buffer_decay_hours = [2.0, 3.0, 4.0, 5.0]
- **Fixed:** All other parameters at baseline + new combo (call=$2.00, put=$2.75)
- **Tests:** 4 backtests, ~2 minutes
- **Hypothesis:** Decay duration affects how long stops are widened. The new combo might benefit from faster or slower decay.
- **Expected:** Decay duration tradeoff between early-day cushion and late-day tightness

#### 4. Put-Only Max VIX Sweep (NEW COMBO) — **NOVEL**
- **Parameter:** put_only_max_vix = [10.0, 15.0, 18.0, 20.0, 25.0, 30.0]
- **Fixed:** All other parameters at baseline + new combo (call=$2.00, put=$2.75)
- **Tests:** 6 backtests, ~4 minutes
- **Hypothesis:** With **tighter credit floors** in the new combo, put-only entries become more viable and profitable. We can allow them at higher VIX levels without excessive risk. This is a **parameter interaction test** — something not previously discovered.
- **Expected:** Optimal put_only_max_vix shifts HIGHER with new combo (enabling more aggressive one-sided entry use)

## Key Metrics to Track

For each backtest:
- **Sharpe:** Primary metric (annualized, 252 trading days)
- **Total P&L:** Dollar profitability over period
- **Max Drawdown:** Largest peak-to-trough loss
- **Calmar Ratio:** Sharpe × (std / |MaxDD|) — risk-adjusted return
- **Win Rate:** % of winning days
- **Stop Rate:** % of entries that hit stop loss
- **Days:** Number of trading days in backtest period

## Baseline Comparison

| Metric | Baseline (Original Combo) | Target (New Combo) |
|--------|--------------------------|-------------------|
| Credit Gates | call=$2.00, put=$3.25 | call=$2.00, put=$2.75 |
| Sharpe | 0.925 | 0.972 ✓ (+0.047) |
| P&L | Varies | Should improve ~5% |
| Max DD | Varies | Should stay stable or improve |
| Sample Size | 942 days | 942 days |

## Expected Outcomes

### Sweep 1: Call Stop Buffer
- **If improving:** Call buffer optimal value shifts (may be higher or lower)
- **If degrading:** Call-side risk management is sensitive to credit gate changes
- **Verdict:** Validates whether call-side tuning applies to new combo

### Sweep 2: Decay Start Multiplier
- **If improving:** Early-day cushion strategy needs adjustment for new combo
- **If degrading:** Current 2.5× multiplier is robust across credit gate changes
- **Verdict:** Validates whether buffer decay strategy is combo-agnostic

### Sweep 3: Decay Hours
- **If improving:** Decay duration optimal value shifts with new combo
- **If degrading:** 4.0h decay is stable across credit gate changes
- **Verdict:** Validates whether decay duration tuning applies to new combo

### Sweep 4: Put-Only Max VIX (NOVEL)
- **If higher VIX optimal:** Confirms hypothesis that tighter put floors enable more aggressive one-sided entries
- **If same VIX optimal:** Put-only entry viability unchanged by credit gate tightening
- **If lower VIX optimal:** New combo makes put-only entries riskier (unlikely but possible)
- **Verdict:** Discovers new parameter interaction between credit gates and entry type selection

## Timeline

- **Sweep 1 (call_stop_buffer):** Starts immediately, completes ~10:00 AM (est.)
- **Sweep 2 (decay_start_mult):** Starts ~10:05 AM (est.), completes ~10:08 AM (est.)
- **Sweep 3 (decay_hours):** Starts ~10:10 AM (est.), completes ~10:12 AM (est.)
- **Sweep 4 (put_only_max_vix):** Starts ~10:14 AM (est.), completes ~10:18 AM (est.)
- **Total Elapsed:** ~18 minutes from start to final result

## Results Location

```
backtest/results/
├── call_stop_buffer_new_combo_results.csv       # [7 rows]
├── call_stop_buffer_new_combo_progress.txt      # Live progress
├── buffer_decay_start_mult_new_combo_results.csv # [5 rows]
├── buffer_decay_start_mult_new_combo_progress.txt
├── buffer_decay_hours_new_combo_results.csv     # [4 rows]
├── buffer_decay_hours_new_combo_progress.txt
├── put_only_max_vix_new_combo_results.csv       # [6 rows]
└── put_only_max_vix_new_combo_progress.txt
```

## How to Monitor

```bash
# Watch all progress files
tail -f backtest/results/*_new_combo_progress.txt

# Or individually
tail -f backtest/results/call_stop_buffer_new_combo_progress.txt
tail -f backtest/results/buffer_decay_start_mult_new_combo_progress.txt
tail -f backtest/results/buffer_decay_hours_new_combo_progress.txt
tail -f backtest/results/put_only_max_vix_new_combo_progress.txt

# Check results CSV (updated in real-time)
watch -n 2 'wc -l backtest/results/*_new_combo_results.csv'
```

## Success Criteria

✅ **SUCCESS:** At least 2 out of 4 sweeps show Sharpe > 0.925 (baseline improvement confirmed)
✅ **BETTER:** At least 1 sweep achieves Sharpe > 0.972 (new combo improvement preserved)
✅ **NOVEL:** Sweep 4 (put_only_max_vix) discovers new optimal VIX gate value

## Notes

- All sweeps use **1-minute data resolution** (honest backtest)
- **Real Greeks mode** enabled (actual per-strike delta from ThetaData)
- **Slippage calibration:** 30% base + 0.10 markup (conservative live estimate)
- **Period:** 2022-05-16 to 2026-04-08 (942 trading days)
- **Workers:** 2 per sweep (leave 2 CPU cores free for system)

---

Generated: 2026-04-10 ~10:05 AM ET
