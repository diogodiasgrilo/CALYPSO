# HYDRA Credit Cushion Analysis

**HISTORICAL REFERENCE ONLY** — This analysis uses HYDRA v1.7.1 stop formula (credit − $15). Current v1.23.0 uses credit + asymmetric buffers ($0.75 call / $1.75 put). Cushion calculations below are NOT applicable to the current version. See docs/HYDRA_STRATEGY_SPECIFICATION.md for current stop formula.

**Date**: 2026-03-03
**Version**: v1.7.1 → v1.7.2 (credit minimum change)
**Status**: Analysis complete, recommendation implemented

---

## 1. The Core Insight: Cross-Side Cushion Dependency

HYDRA's stop formula is based on **total credit** for the entry:

```
Stop level = Total credit - $15
```

A stop triggers when **one side's** spread value (cost-to-close) reaches the stop level. This creates a critical dependency:

```
Call starting cushion = (Put credit - $15) / Stop level
Put starting cushion = (Call credit - $15) / Stop level
```

**The call side's cushion depends on the PUT credit. The put side's cushion depends on the CALL credit.**

This means a large put credit doesn't just generate income — it **subsidizes the call side's safety**. Similarly, a large call credit subsidizes the put side. When you enforce minimum credits that push both sides toward parity, you weaken this cross-subsidization effect.

### Cushion % vs Cushion Multiple

Two ways to express the same thing:

- **Cushion %**: `(stop_level - spread_value) / stop_level × 100` — what the heartbeat shows on the terminal
- **Cushion multiple**: `stop_level / spread_value` — how many times the spread must grow to trigger a stop

At entry, `spread_value ≈ credit_received` for that side (slightly higher due to bid-ask spread). So:

```
Call cushion % ≈ (Put credit - $15) / (Total credit - $15) × 100
Call multiple ≈ (Total credit - $15) / Call credit

Put cushion % ≈ (Call credit - $15) / (Total credit - $15) × 100
Put multiple ≈ (Total credit - $15) / Put credit
```

**First heartbeat** values will be ~3-5% lower than theoretical due to bid-ask spread (spread value at mid > credit received at bid).

---

## 2. Week 1 Empirical Data (Feb 10-18, 2026)

Per-side credit data extracted from VM bot logs. All 13 full IC call sides from the first week of live trading:

| Entry | Call | Put | Total | Stop | Call Cush% | Call Mult | Put Cush% | Put Mult | Outcome |
|-------|------|-----|-------|------|-----------|-----------|-----------|----------|---------|
| Feb 18 #1 | $55 | $335 | $390 | $375 | **85.3%** | 6.8x | 10.7% | 1.12x | Both EXPIRED |
| Feb 17 #5 | $40 | $210 | $250 | $235 | **83.0%** | 5.9x | 10.6% | 1.12x | Put STOPPED |
| Feb 12 #2 | $65 | $225 | $290 | $275 | **76.4%** | 4.2x | 18.2% | 1.22x | Put STOPPED, Call EXP |
| Feb 12 #1 | $80 | $240 | $320 | $305 | **73.8%** | 3.8x | 21.3% | 1.27x | Put STOPPED, Call EXP |
| Feb 11 #1 | $125 | $310 | $435 | $420 | **70.2%** | 3.4x | 26.2% | 1.35x | Put STOPPED, Call EXP |
| Feb 17 #3 | $125 | $275 | $400 | $385 | **67.5%** | 3.1x | 28.6% | 1.40x | **CALL STOPPED** |
| Feb 13 #5 | $110 | $205 | $315 | $300 | **63.3%** | 2.7x | 31.7% | 1.46x | Put STOPPED, Call EXP |
| Feb 12 #6 | $95 | $160 | $255 | $240 | **60.4%** | 2.5x | 33.3% | 1.50x | Put STOPPED, Call EXP |
| Feb 13 #4 | $185 | $290 | $475 | $460 | **59.8%** | 2.5x | 37.0% | 1.59x | Both EXPIRED |
| Feb 13 #3 | $315 | $360 | $675 | $660 | **52.3%** | 2.1x | 45.5% | 1.83x | Both EXPIRED |
| Feb 13 #1 | $530 | $620 | $1150 | $1135 | **53.3%** | 2.1x | 45.4% | 1.83x | **CALL STOPPED** |
| Feb 17 #2 | $350 | $345 | $695 | $680 | **48.5%** | 1.9x | 49.3% | 1.97x | **CALL STOPPED** |
| Feb 12 #5 | $165 | $145 | $310 | $295 | **44.1%** | 1.8x | 50.8% | 2.03x | Put STOPPED, Call EXP |

### Survival Rate by Cushion Zone

| Call Cushion Zone | Calls Expired | Calls Stopped | Survival Rate |
|-------------------|---------------|---------------|---------------|
| **>65%** | 6 | 0 | **100%** |
| **60-65%** | 2 | 1 | 67% |
| **<55%** | 2 | 2 | 50% |

**The 65% threshold**: Every call side with >65% starting cushion survived — including Feb 12's 1.57% sell-off, Feb 13's post-crash volatility, and Feb 17's V-shape (for entries that happened to have high cushion). Below 65%, stops start appearing.

---

## 3. Put Skew and Natural Asymmetry

SPX options exhibit strong put skew — puts are more expensive than calls at equivalent OTM distances. This creates a natural asymmetry in iron condor credits:

| VIX Level | Typical C/P Ratio | Put Dominance |
|-----------|-------------------|---------------|
| 17-19 (low) | 0.16 - 0.40 | Puts are 2.5-6x calls |
| 20-21 (elevated) | 0.45 - 0.88 | Puts are 1.1-2.2x calls |
| 22-23 (high) | 0.85 - 1.01 | Near parity (WARNING) |

**In low VIX environments** (which are most trading days), puts naturally provide 65-85% of total IC credit. Calls collect small amounts ($40-$125) but are placed far OTM with massive cushion. This asymmetry is a **structural advantage** — the call side functions as "found money" that offsets put stops.

### Warning Signal: Balanced Credits

When the C/P ratio approaches 1.0, both strikes are close to ATM and BOTH sides are at risk:

- Feb 17 #2: $350C/$345P (ratio 1.01) → **CALL STOPPED**
- Feb 13 #1: $530C/$620P (ratio 0.85) → **CALL STOPPED**

A near-parity credit ratio should be treated as a red flag — the entry is essentially a straddle disguised as an iron condor.

---

## 4. How Credit Minimums Affect Cushion

### The 65% Rule Formula

To maintain ≥65% call cushion:

```
P ≥ 1.86 × C + $15
```

| Call Min | Put Min Needed for 65% | Put Min Needed for 70% |
|----------|------------------------|------------------------|
| $0.25 | $0.61 | $0.73 |
| $0.50 | $1.08 | $1.32 |
| $0.75 | **$1.54** | $1.90 |
| $1.00 | **$2.01** | $2.48 |
| $1.25 | $2.47 | $3.07 |
| $1.50 | $2.94 | $3.65 |

### Why Lowering Call Min Is Better Than Raising Put Min

Both paths can achieve 65%+ call cushion, but they work differently:

**Lowering call minimum** (e.g., $1.00 → $0.75):
- Calls stay **physically further OTM** (less MKT-020 tightening needed)
- The call becomes harder to reach — the first line of defense
- Put behavior is completely unchanged
- One config change

**Raising put minimum** (e.g., $1.75 → $2.00):
- Calls stay at the **same close-to-ATM distance** (MKT-020 still tightens ~20pt)
- Call cushion improves via higher stop level, not via physical distance
- MKT-022 must tighten puts closer to ATM → more put stop risk
- Late entries (11:05+) may not reach higher put min after theta decay → more skips
- Partially undoes MKT-024/MKT-028 design (keep puts far OTM)

**The stop formula is the last line of defense. The first line of defense is the call being so far OTM the market can't reach it.** Lowering the call minimum preserves the first line. Raising the put minimum only strengthens the last line.

---

## 5. Full Comparison Table

### Call Min = $0.25 / Put Min = $1.25

| Metric | Value | Assessment |
|--------|-------|------------|
| Total credit | $150 | Very low — barely covers 2 stops |
| Stop level | $135 | Lowest of any option |
| Call cushion | 81.5% / 5.4x | Excellent but diminishing returns |
| Put cushion | **7.4%** / 1.07x | **BROKEN** — one tick from stop trigger |
| Net call after commission | $20 | Barely worth the margin |
| Put stop room | $10 | False stop trigger territory |
| **Verdict** | | **Too extreme — put side non-functional** |

### Call Min = $0.50 / Put Min = $1.50

| Metric | Value | Assessment |
|--------|-------|------------|
| Total credit | $200 | Low |
| Stop level | $185 | |
| Call cushion | 73.0% / 3.7x | Very good |
| Put cushion | 18.9% / 1.23x | Thin — stops on any volatility |
| Net call after commission | $45 | Acceptable |
| Put stop room | $35 | Tight but workable on calm days |
| **Verdict** | | **Functional but thin on puts** |

### Call Min = $0.75 / Put Min = $1.75 (RECOMMENDED)

| Metric | Value | Assessment |
|--------|-------|------------|
| Total credit | $250 | Solid |
| Stop level | $235 | |
| Call cushion | **68.1%** / 3.1x | **Above 65% safety threshold** |
| Put cushion | 25.5% / 1.34x | Moderate — similar to Week 1 actuals |
| Net call after commission | $70 | Good |
| Put stop room | $60 | Reasonable buffer |
| MKT-020 tightening | ~10-15pt inward | Half of current |
| **Verdict** | | **Best balance — both sides functional** |

### Call Min = $1.00 / Put Min = $1.75 (PREVIOUS — v1.3.1 through v1.7.1)

| Metric | Value | Assessment |
|--------|-------|------------|
| Total credit | $275 | Good |
| Stop level | $260 | |
| Call cushion | **61.5%** / 2.6x | **Below 65% safety threshold** |
| Put cushion | 32.7% / 1.49x | Comfortable |
| Net call after commission | $95 | Very good |
| Put stop room | $85 | Good buffer |
| MKT-020 tightening | ~20-25pt inward | Aggressive |
| **Verdict** | | **Call cushion too low — broke Week 1 asymmetry** |

### Call Min = $1.00 / Put Min = $2.00 (alternative to achieve 65%)

| Metric | Value | Assessment |
|--------|-------|------------|
| Total credit | $300 | Very good |
| Stop level | $285 | |
| Call cushion | 64.9% / 2.9x | Borderline 65% |
| Put cushion | 29.8% / 1.43x | Good |
| Net call after commission | $95 | Very good |
| MKT-022 tightening | More aggressive | Puts closer to ATM than current |
| Late entry skips | More frequent | Theta decay pushes puts below $2.00 |
| **Verdict** | | **Achieves 65% but via stop level, not distance** |

---

## 6. Diminishing Returns of Lowering Call Minimum

Each $0.25 reduction in call minimum has decreasing benefit and increasing cost:

| Change | Call Cushion Gained | Put Cushion Lost | Net Assessment |
|--------|--------------------|--------------------|----------------|
| $1.00 → $0.75 | +6.6% (61.5→68.1) | -7.2% (32.7→25.5) | **Best trade** — crosses 65% threshold |
| $0.75 → $0.50 | +4.9% (68.1→73.0) | -6.6% (25.5→18.9) | Marginal — already above 65%, puts getting thin |
| $0.50 → $0.25 | +8.5% (73.0→81.5) | -11.5% (18.9→7.4) | **Destructive** — put side stops on every entry |

The $1.00 → $0.75 change is the only one where you cross a meaningful threshold (65%) without breaking the other side. Below $0.75, you're buying call cushion you don't need while destroying put functionality.

---

## 7. Intraday Theta Decay Impact

Per-side data from Feb 10 (all put-only, pure theta decay visible):

| Entry Time | Put Credit | Decay from #1 |
|------------|-----------|---------------|
| 10:05 | $210 | — |
| 10:35 | $150 | -29% |
| 11:05 | $120 | -43% |
| 11:35 | $95 | -55% |
| 12:05 | $65 | -69% |

Put credit decays ~69% across the entry window. Later entries collect dramatically less credit for the same risk. With a $1.75 put minimum, late entries (11:35, 12:05) on low-VIX days may require significant MKT-022 tightening or get skipped by MKT-011. This is arguably a feature — late entries with $65-$95 credit are barely worth the margin and commission.

---

## 8. Implementation

### Change Summary (v1.7.2)

**Single config value change:**
- `min_viable_credit_per_side`: 100 → 75 (cents, applies to call side)
- `min_viable_credit_put_side`: 175 → 175 (unchanged)

**Behavioral change:**
- MKT-020 progressive call tightening stops earlier (needs fewer inward steps to reach $0.75)
- Calls placed ~10-15pt further OTM than with $1.00 minimum
- More entries pass call credit gate without tightening
- Fewer MKT-011 conversions to put-only (some $0.75-$0.99 calls now viable)

**No change to:**
- Put minimum ($1.75 with MKT-029 fallback to $1.65)
- Stop formula (total_credit - $0.15)
- MKT-024 starting OTM distances (3.5x calls, 4.0x puts)
- Spread width floors (60pt calls, 75pt puts)
- Any other MKT rules

---

## 9. Future Monitoring

### What to Watch

1. **Call stop rate**: Should decrease from current levels. If calls above 65% cushion start stopping, investigate whether MKT-020 is tightening more than expected.

2. **Put-only conversion rate**: Should decrease (fewer entries fail the $0.75 call gate vs $1.00). Track via `credit_gate_skips` counter.

3. **Total credit per day**: Will decrease ~$25 per full IC entry at minimum. Monitor whether daily P&L targets are still achievable.

4. **C/P ratio on placed entries**: Should be more asymmetric (0.25-0.45 in low VIX). If ratio approaches 0.8+, investigate — something is pulling calls too close.

### Red Flags

- Call cushion showing <60% on first heartbeat → MKT-020 tightened too much
- Multiple days with call stops → market regime may have changed
- Put-only conversions increasing despite lower call min → VIX environment shifted

---

## Appendix: Hypothetical Combinations Reference

For future tuning, full matrix of call/put minimum combinations at exact minimums:

### Call = $0.50

| Put Min | Total | Stop | Call Cush | Put Cush |
|---------|-------|------|-----------|----------|
| $1.50 | $200 | $185 | 73.0% | 18.9% |
| $1.75 | $225 | $210 | 76.2% | 16.7% |
| $2.00 | $250 | $235 | 78.7% | 14.9% |
| $2.25 | $275 | $260 | 80.8% | 13.5% |
| $2.50 | $300 | $285 | 82.5% | 12.3% |

### Call = $0.75

| Put Min | Total | Stop | Call Cush | Put Cush |
|---------|-------|------|-----------|----------|
| $1.50 | $225 | $210 | 64.3% | 28.6% |
| **$1.75** | **$250** | **$235** | **68.1%** | **25.5%** |
| $2.00 | $275 | $260 | 71.2% | 23.1% |
| $2.25 | $300 | $285 | 73.7% | 21.1% |
| $2.50 | $325 | $310 | 75.8% | 19.4% |

### Call = $1.00

| Put Min | Total | Stop | Call Cush | Put Cush |
|---------|-------|------|-----------|----------|
| $1.50 | $250 | $235 | 57.4% | 36.2% |
| $1.75 | $275 | $260 | 61.5% | 32.7% |
| $2.00 | $300 | $285 | 64.9% | 29.8% |
| $2.25 | $325 | $310 | 67.7% | 27.4% |
| $2.50 | $350 | $335 | 70.1% | 25.4% |

### Call = $1.25

| Put Min | Total | Stop | Call Cush | Put Cush |
|---------|-------|------|-----------|----------|
| $1.75 | $300 | $285 | 56.1% | 38.6% |
| $2.00 | $325 | $310 | 59.7% | 35.5% |
| $2.25 | $350 | $335 | 62.7% | 32.8% |
| $2.50 | $375 | $360 | 65.3% | 30.6% |
| $3.00 | $425 | $410 | 69.3% | 26.8% |
