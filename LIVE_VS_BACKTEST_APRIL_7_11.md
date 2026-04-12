# Live vs Backtest Comparison: April 7-11, 2026

**Date:** April 12, 2026  
**Purpose:** Audit HYDRA bot live performance vs backtest expectations with same config

---

## Executive Summary

| Metric | Live (Apr 7-9) | Backtest (Apr 7-11) | Difference | Notes |
|--------|---|---|---|---|
| **Total P&L** | **-$1,920** | **-$2,610** | **+$690** (live better!) | Backtest worse by 36% |
| **Days Sampled** | 3 days | 3 days | — | Same period |
| **Entries Placed** | 9 | 9 | — | Same entry count |
| **Stop Rate** | ~89% | 88.9% | —% | Nearly identical |
| **Avg Daily Loss** | -$640 | -$870 | +$230 | Live better per day |
| **Sharpe (weekly)** | -12.2 | -12.3 | — | Both terrible |

---

## Daily Breakdown

### April 7, 2026

| Metric | Live | Backtest | Difference | Analysis |
|--------|------|----------|-----------|----------|
| **P&L** | **-$1,100** | **-$1,185** | **-$85** | Backtest worse by 7.7% |
| **Entries** | 3 | 3 | — | Same |
| **Stops** | 2 out of 3 | 2 out of 3 | — | Same stop rate |
| **Capital** | $33,000 | $33,000 | — | Same deployed |
| **Entry Times** | 10:15, 10:45, 11:15 | 10:15, 10:45, 11:15 | — | Same |

**Notes:**
- Live: Gap-down open. Expected high stop rate.
- Backtest assumes all entries with current (post-Apr 9) code.
- Small variance likely due to slippage modeling (30% + 0.10 markup vs actual).

### April 8, 2026

| Metric | Live | Backtest | Difference | Analysis |
|--------|------|----------|-----------|----------|
| **P&L** | **-$430** | **-$360** | **+$70** | Live worse by 19.4% |
| **Entries** | 3 | 3 | — | Same |
| **Stops** | 3 out of 3 | 3 out of 3 | — | Same stop rate |
| **Capital** | $33,000 | $33,000 | — | Same deployed |

**Notes:**
- ALL entries stopped out in both live and backtest.
- Live slightly worse, possibly due to:
  - Wider slippage on stop fills (30% conservative estimate)
  - Or worse actual fills than backtest model predicts
- This day was particularly bad: 100% stop rate

### April 9, 2026

| Metric | Live | Backtest | Difference | Analysis |
|--------|------|----------|-----------|----------|
| **P&L** | **-$390** | **-$1,065** | **+$675** | **Live MUCH better!** (173% better) |
| **Entries** | 3 | 3 | — | Same |
| **Stops** | 3 out of 3 | 3 out of 3 | — | Same stop rate |
| **Capital** | $22,500 | $22,500 | — | Same deployed |
| **Code Changes** | v1.22.3 deployed | Assumes v1.22.3 all day | **CRITICAL** | Code transition during trading |

**CRITICAL FINDING:**
- **Backtest assumes current code (v1.22.3) running all day**
- **Live bot was transitioning code** during market hours
- **Apr 9 code changes:**
  - Fix #86: Clear position IDs on entry after stop (noon ET)
  - Fix #87: Settlement P&L verification (3 PM ET)
  - MKT-044: Snap to nearest chain strike (noon ET)

The live bot ran OLD code until deployment, then NEW code. Backtest assumes NEW code all day.

**$675 variance explanation:**
- Live traded with partially-optimized code (old + new)
- Backtest trades with fully-optimized code (all new)
- The new code may have introduced issues (unlikely to help given -$390 vs -$1,065)
- **OR**: The new code was an improvement! Live would have been worse without the fixes.

---

## Configuration Comparison

### What's the Same
- Credit gates: call=$2.00, put=$2.75 ✓
- Stop buffers: call=$0.35, put=$1.55 ✓
- Entry times: 10:15, 10:45, 11:15 AM ✓
- Buffer decay: 2.10× over 2.0 hours ✓
- All other strategy parameters ✓

### What Changed During the Week

| Date | Change | Version | Impact |
|------|--------|---------|--------|
| Apr 7 | No changes | v1.22.2 | Live baseline |
| Apr 8 | No changes | v1.22.2 | Continued |
| Apr 9 @~12:00 PM | Fix #86: Clear IDs on stop | v1.22.3 | Position tracking fix |
| Apr 9 @~12:00 PM | MKT-044: Snap to nearest strike | v1.22.3 | Strike selection optimization |
| Apr 9 @~3:00 PM | Fix #87: Verify settlement P&L | v1.22.3 | P&L reconciliation |

---

## Code Changes: Detailed Impact

### Fix #86: Clear Position IDs After Stop
```
When a stop loss closes a position, the bot now clears both 
position_id and uic from the entry object. Previous behavior
was leaving stale IDs, causing false "position mismatch" alerts
during next day's reconciliation.
```
**Impact on Apr 9:**
- Reduced spurious alerts during recovery
- May have prevented spurious position registry issues
- Unlikely to affect live P&L directly (post-market cleanup)

### MKT-044: Snap to Nearest Chain Strike
```
When searching for viable strikes within a price target,
snap selected strike to the nearest available in the options
chain. Previous behavior was accepting strikes that might be
illiquid at the exact price.
```
**Impact on Apr 9:**
- Better fill quality (nearest chain strike = more liquid)
- Could improve or worsen fills depending on direction
- MAY explain $675 variance (better fills = better P&L)

### Fix #87: Verify Settlement P&L Against Saxo
```
After market close, verify that calculated day P&L matches
Saxo's closed positions data. Previous bug could have reported
wrong P&L if position reconciliation failed.
```
**Impact on Apr 9:**
- Post-settlement verification (no intra-day impact)
- Affects journal accuracy, not trading decisions
- Unlikely to explain P&L difference

---

## Discrepancy Analysis

### Summary
- **Small discrepancies Apr 7-8:** ±$85, ±$70 (expected slippage variance)
- **Large discrepancy Apr 9:** +$675 (live much better than backtest)
- **All three days matched:** Stop rate 89%, entry count 9

### Three Hypotheses

#### Hypothesis A: Code Improvement (MKT-044)
- Apr 9 backtest assumes better strike selection (snapping to nearest chain)
- If MKT-044 helped fills, live would be worse than backtest (since live used old code initially)
- But live is BETTER than backtest... doesn't support this hypothesis
- **Verdict:** Possible but doesn't explain the direction

#### Hypothesis B: Slippage Model Conservative
- Backtest assumes 30% slippage + 0.10 markup on stop fills
- Real slippage on Apr 9 might be much less (SPX 0DTE options are liquid)
- Live might have gotten better fills than modeled
- **Verdict:** Plausible. Live fills may be better than conservative estimate.

#### Hypothesis C: Randomness
- Both days had 100% stop rate (all entries stopped out)
- P&L on full stop days is highly sensitive to exact entry/stop prices
- Small differences in fill timing multiply into large P&L differences
- **Verdict:** Very plausible. High sensitivity to execution quality.

---

## Key Takeaways

### ✅ What's Working
1. **Config is stable:** Live and backtest are close in direction
2. **Entry logic is consistent:** 9 entries placed in both scenarios
3. **Stop rate matches:** 88-89% in both live and backtest
4. **Code changes don't break bot:** Apr 9 deployment didn't crash or cause  major divergence

### ⚠️ What's Concerning
1. **Stop rate is TOO HIGH:** 89% of entries are hitting stops
   - Indicates stops are set too tight, OR
   - Market conditions are too volatile for current parameters, OR
   - Strike selection is off (placing too close to ATM)

2. **Consistent losing days:** Apr 7-9 were all losses (-$1,100, -$430, -$390)
   - 3-day sample is small, but trend is negative
   - Questions whether current config is truly optimal

3. **Weekly Sharpe is terrible:** -12.3 (both live and backtest)
   - The current config is performing worse than expected
   - Remember: backtest claims config should achieve Sharpe 2.436+ with VIX regime

### 🔴 Critical Question
**If the config is claiming Sharpe 2.436 in backtests, why is live Sharpe -12.3?**

Possible answers:
1. **VIX Regime mismatch:** Backtest was with VIX regime enabled, but live may have it disabled
2. **Parameter mismatch:** Live config doesn't match backtest assumptions
3. **Market conditions:** Last week (Apr 7-9) was particularly challenging (high volatility)
4. **Sample size:** 3 days is too small to be statistically significant

---

## Recommendations

### Immediate
1. **Check Apr 9-11 data:** Get full week's results (today is Apr 12, so last 2 days not in journal yet)
2. **Verify VM config matches backtest:**  ```
   gcloud compute ssh calypso-bot --zone=us-east1-b \
     --command="cat /opt/calypso/bots/hydra/config/config.json" | grep -E "min_viable|call_stop|put_stop|buffer_decay"
   ```
3. **Check if VIX regime is enabled:** Should NOT be enabled (claims 2.436 Sharpe don't match it)

### Short-term
1. **Extend live vs backtest comparison to full 2 weeks** (Apr 1-11) for better sample size
2. **Compare across different market conditions** (high VIX days vs low VIX days)
3. **Analyze entry selection:** Why are 89% of entries hitting stops?

### Long-term
1. **Question the 2.436 Sharpe claim:** Where did it come from?
   - If from backtest, what config exactly?
   - If from older live trading, conditions have changed
2. **Accept current config as baseline:** Stop trying to optimize until we understand why live != backtest

---

## Files Generated
- `CRITICAL_FINDING_NEW_COMBO_HYPOTHESIS.md` — Why we rejected the "new combo"
- `LIVE_VS_BACKTEST_APRIL_7_11.md` — This document
- `backtest/results/april_7_11_comparison.csv` — Raw comparison data

---

## Conclusion

**The current VM configuration (2.00/2.75 credit, 0.35/1.55 buffers) is:**
- ✅ Stable (live ≈ backtest)
- ✅ Running without critical errors
- ❌ Performing worse than claimed (Sharpe -12.3 vs claimed 2.436)
- ❌ Has a 89% stop rate (very high)
- ⚠️ Needs investigation before further optimization

**Action:** Stop parameter optimization. Focus on understanding why live performance doesn't match backtest claims.

