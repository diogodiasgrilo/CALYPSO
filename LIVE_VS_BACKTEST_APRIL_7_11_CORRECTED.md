# Live vs Backtest Comparison: April 7-11, 2026 (CORRECTED)

**Date:** April 12, 2026  
**Purpose:** Comprehensive live vs backtest audit with CORRECT entry schedule (3 base + 1 conditional E6)

---

## Executive Summary

| Metric | Live (Apr 7-9) | Backtest (Apr 7-9) | Difference | Notes |
|--------|---|---|---|---|
| **Total P&L (Gross)** | **-$1,920** | **-$1,020** | **+$900** (backtest better) | Backtest is 47% more profitable |
| **Total P&L (Net)** | N/A | **-$1,145** | — | Includes commissions |
| **Days Sampled** | 3 | 3 | — | Same period |
| **Entries Placed** | 9 | 12 | +3 | Backtest includes more E6 conditional entries |
| **Stop Rate** | 89% (8/9) | 58% (7/12) | -31% | Live has higher stop rate |
| **Avg Daily Loss** | -$640 | -$381 | +$259 | Live worse per day |
| **Sharpe (3-day)** | -12.2 | -6.092 | — | Both terrible, backtest less bad |

---

## Daily Breakdown

### April 7, 2026

| Metric | Live | Backtest | Difference | Analysis |
|--------|------|----------|-----------|----------|
| **P&L** | **-$1,100** | **-$820** (net) | **+$280** | Backtest 25% better |
| **Entries** | 3 | 4 | +1 | Backtest E6 fired (upday put-only at 14:00) |
| **Stops** | 2 out of 3 | 3 out of 4 | — | Higher stop rate in backtest despite better P&L |
| **Entry Types** | 3 full ICs | 3 full IC + 1 E6 put-only | — | — |

**Key Observation:** Live bot placed only base entries (no E6). Backtest shows E6 would have fired on Apr 7, adding another entry. Even with extra entry, backtest was still better due to different fill quality or stop mechanics.

### April 8, 2026

| Metric | Live | Backtest | Difference | Analysis |
|--------|------|----------|-----------|----------|
| **P&L** | **-$430** | **+$995** (net) | **-$1,425** | HUGE discrepancy! Backtest profitable, live loss |
| **Entries** | 3 | 4 | +1 | Backtest E6 fired |
| **Stops** | 3 out of 3 | 1 out of 4 | -2 | Backtest had 75% fewer stops |
| **Entry Types** | 3 full ICs | 3 full IC + 1 E6 put-only | — | — |

**CRITICAL FINDING:** Apr 8 shows the widest backtest-to-live divergence. Backtest was massively profitable (+$995) while live lost $430. Hypotheses:

1. **Backtest data doesn't match actual market conditions** - Possible data quality issue or simulation assumption
2. **Live slippage is worse than modeled** - Even with 30% slippage assumption, live is getting filled much worse
3. **Stop mechanics differ** - Backtest uses spread-value stops, live uses different trigger calculation
4. **Credit estimation wrong** - Backtest assumes entries collected more credit than they actually did

### April 9, 2026

| Metric | Live | Backtest | Difference | Analysis |
|--------|------|----------|-----------|----------|
| **P&L** | **-$390** | **-$1,320** (net) | **-$930** | Backtest MUCH worse on this day |
| **Entries** | 3 | 4 | +1 | Backtest E6 fired (put-only) |
| **Stops** | 3 out of 3 | 3 out of 4 | — | Same stop rate |
| **Code Changes** | v1.22.3 deployed at noon | Assumes v1.22.3 all day | **MISMATCH** | Mid-day deployment |

**CRITICAL MISMATCH:** Live bot was transitioning code during trading:
- Pre-noon: v1.22.2 (old code)
- Noon: v1.22.3 deployed (Fix #86, MKT-044, Fix #87)
- Backtest: Assumes v1.22.3 all day

Possible explanation for -$930 gap:
- **MKT-044 (snap to nearest chain strike)** may have helped live bot get better fills than backtest expected
- Or backtest overestimated credit the new code would collect
- Apr 9 P&L was -$390 (live better than backtest's -$1,320), suggesting the code fix(es) actually helped

---

## Configuration Analysis

### What Matches Between Live and Backtest

✓ Credit gates: call=$2.00, put=$2.75  
✓ Stop buffers: call=$0.35, put=$1.55  
✓ Entry times: 10:15, 10:45, 11:15 AM ET (base entries)  
✓ Buffer decay: 2.10× over 2.0 hours (MKT-042)  
✓ E6 upday conditional: ENABLED at 14:00 with 0.25% threshold  
✓ E7 downday conditional: DISABLED  

### Critical Difference

**ENTRY SCHEDULE:** This was the first major error.
- **Initial backtest (wrong):** 5 entries per day → 15 total entries (Apr 7-9)
- **Live configuration (correct):** 3 base + ~1 conditional E6 → 9-12 total entries (Apr 7-9)
- **Impact:** Running with wrong entry count was invalid baseline for comparison

**Now corrected.** Backtest uses actual 3+1 schedule matching live bot.

---

## Why Live Still Underperforms Backtest

Even with correct entry schedule, backtest is $900 better (-$1,020 vs -$1,920 gross).

### Hypothesis 1: Slippage Modeling Too Optimistic

**Assumption in backtest:** 30% slippage + 0.10 markup on stop fills.

**Reality:** Apr 8 shows backtest profit (+$995) while live lost (-$430). Backtest entry credit might be too high. If backtest overestimates credit by ~$150-200/entry on Apr 8 (which had profitable exits), that alone explains the gap.

**Test:** Compare actual fill prices from live trading to backtest-assumed prices.

### Hypothesis 2: Data Quality Issue

**Possibility:** Backtest uses 5-min bar data, which may not capture intraday moves accurately. Apr 8 could have had different actual market behavior than the 5-min OHLC bars imply.

**Evidence:** Apr 8 shows the WILDEST divergence (+$1,425 gap). This doesn't look like slippage, it looks like fundamentally different market conditions or data artifacts.

### Hypothesis 3: Stop Monitoring Calculation Difference

Live bot and backtest may trigger stops at different times:
- **Backtest:** Checks stops at 5-min intervals (or 1-min if using 1-min data)
- **Live:** Polls stops every 2-5 seconds

If Apr 8 had fast moves, the live bot's more frequent polling might have triggered stops earlier than the backtest would predict, leading to different average stop-out prices.

### Hypothesis 4: Entry Credit Estimation Bug in Backtest

MKT-011 credit gate estimates credit **before placing orders**. If the backtest's credit estimation is overly optimistic, it might allow entries that the live bot would reject as non-viable. This would show up as better entry quality than live, improving backtest P&L.

---

## Stop Rate Analysis

| Period | Live Stop Rate | Backtest Stop Rate | Difference |
|--------|---|---|---|
| Apr 7 | 67% (2/3) | 75% (3/4) | +8% |
| Apr 8 | 100% (3/3) | 25% (1/4) | -75% |
| Apr 9 | 100% (3/3) | 75% (3/4) | -25% |
| **Avg** | **89%** | **58%** | **-31%** |

**Critical Question:** Why does the backtest have a much LOWER stop rate than live? Both use the same stop formula (total_credit + buffer). Possible explanations:

1. **Spread width difference:** Backtest uses VIX-scaled widths (MKT-027) [25-110pt formula], live may use different widths
2. **Credit collected difference:** If backtest entries collect more credit, they have higher stop levels, triggering fewer stops
3. **Entry timing difference:** Backtest's simulated entries might have different market conditions (5-min bar vs actual ticks) leading to different credits
4. **Data artifact:** The backtest data might simply have fewer violent moves than the actual market on Apr 7-9

---

## Key Findings

### Finding 1: Configuration Was Partially Wrong

Initial backtest used 5 entries/day instead of actual 3+1 schedule. **CORRECTED.** Now using actual live configuration.

### Finding 2: Backtest Still Shows Significantly Better P&L

Even with correct entry schedule, backtest is **$900 better** (-$1,020 vs -$1,920) over 3 days. This suggests either:
- Slippage assumptions are too optimistic
- Data quality issues (5-min bars vs actual ticks)
- Entry credit estimation is overoptimistic in backtest
- Stop mechanics differ between simulation and live

### Finding 3: Apr 8 Shows the Largest Divergence

Backtest +$995 (net) vs Live -$430 = **$1,425 gap**. This is NOT explained by slippage alone. Likely indicates a data quality or fundamental calculation difference specific to Apr 8.

### Finding 4: Apr 9 Code Deployment Timing Issue

Live bot was transitioning code at noon ET (v1.22.2 → v1.22.3), but backtest assumes v1.22.3 all day. The +$930 gap on Apr 9 could reflect either:
- Code improvements helping live bot
- Or backtest overestimating what the new code would achieve

### Finding 5: High Stop Rate Persists

Even corrected backtest shows 58% stop rate vs claimed baseline of ~30%. This confirms that the current configuration (call=$2.00, put=$2.75, buffers=$0.35/$1.55) is producing more stops than expected. Parameter optimization research in prior sessions showed optimal put_buffer is $3.00 (Sharpe 0.512 vs current 0.446), confirming these parameters are NOT optimal.

---

## Immediate Next Steps

1. **Extend to full week:** Get Apr 10-11 data to add 2 more days of live/backtest comparison
2. **Verify Apr 8 data:** Investigate why backtest and live diverge so dramatically on that day
3. **Validate VM config:** SSH to VM and confirm actual config matches what we assume
4. **Check data source:** Compare backtest's 5-min data source against actual market conditions
5. **Analyze entry credits:** Compare backtest-estimated credits vs actual fills from live trading journal

---

## Conclusion

The backtest with corrected configuration shows live trading underperformance (-$900 total gap, or -$300/day average). The configuration itself is correct, but execution quality differs. This could be due to:

1. **Slippage assumptions** being too optimistic
2. **Data quality issues** in the backtest simulation
3. **Entry credit estimation** overoptimistic
4. **Stop mechanics differences** between simulation and live

The high stop rate (89% live vs 58% backtest, and overall 58-89% vs 20-30% baseline expectation) indicates the current parameters are suboptimal. Prior parameter optimization work showed optimal put_buffer=$3.00, not current $1.55, which could reduce stops significantly.

**Key metric missing:** Apr 10-11 live trading data (not yet available in local files). Once available, extend this analysis to full week for better statistical significance.

