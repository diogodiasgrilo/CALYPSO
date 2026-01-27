# Portfolio Allocation Analysis: $50,000 Capital

**Last Updated:** 2026-01-27
**Purpose:** Comprehensive analysis of optimal capital allocation across trading bots
**Account Size:** $50,000

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current Bot Profiles](#current-bot-profiles)
3. [Proposed New Bot Profiles](#proposed-new-bot-profiles)
4. [Why NOT 6 Bots](#why-not-6-bots)
5. [Portfolio Scenarios Analyzed](#portfolio-scenarios-analyzed)
6. [Recommendation](#recommendation)
7. [Why NOT Keep Iron Fly](#why-not-keep-iron-fly)
8. [Implementation Path](#implementation-path)
9. [Capital Scaling Guide](#capital-scaling-guide)

---

## Executive Summary

### Key Findings

| Question | Answer |
|----------|--------|
| **Should you run 6 bots?** | ❌ NO - insufficient capital |
| **Best risk-adjusted portfolio?** | MEIC ($30K) + Delta Neutral ($20K) |
| **Should you keep Iron Fly?** | ❌ Replace with MEIC (superior risk-adjusted) |
| **Should you keep Rolling Put Diagonal?** | ❌ Not yet - keep in dry-run |
| **Expected return?** | ~28-32% annually |
| **Expected max drawdown?** | ~8-10% |
| **Calmar Ratio** | 3.0-3.5 (excellent) |
| **Next bot to add when capital grows?** | METF (50% return) |

### Recommended Allocation

| Bot | Allocation | % of Capital | Status |
|-----|------------|--------------|--------|
| **MEIC** (NEW) | $30,000 | 60% | To Build |
| **Delta Neutral** (KEEP) | $20,000 | 40% | LIVE |
| Iron Fly | $0 | 0% | Deprecated (MEIC superior) |
| Rolling Put Diagonal | $0 | 0% | Keep in dry-run |

---

## Current Bot Profiles

### Bot 1: Iron Fly 0DTE

| Metric | Value |
|--------|-------|
| **Underlying** | SPX (cash-settled) |
| **Buying Power per Contract** | ~$3,000-5,000 |
| **Typical Profit** | +$75/contract (profit target) |
| **Typical Loss** | -$300-350/contract (wing touch) |
| **Max Loss Circuit Breaker** | -$400/contract |
| **Win Rate** | ~55-60% (estimated from Doc Severson data) |
| **Frequency** | 1 trade/day (when filters pass) |
| **Annual Expectancy** | ~40-50% on allocated capital |
| **Max Drawdown** | ~15-20% |
| **Calmar Ratio** | ~2.5 |
| **Status** | LIVE |

### Bot 2: Delta Neutral (Brian Terry)

| Metric | Value |
|--------|-------|
| **Underlying** | SPY |
| **Capital Required** | Long straddle cost (~$4,000-8,000 for 1 contract at 120 DTE) |
| **Weekly Target** | 1% NET of long straddle cost |
| **Annual Return** | ~52% (1% weekly × 52 weeks, but ~40% after losses) |
| **Win Rate** | ~75-80% on weekly rolls |
| **Frequency** | Weekly premium collection |
| **Max Drawdown** | ~10-15% |
| **Calmar Ratio** | ~3.0 |
| **Risk Profile** | Lower risk - shorts are covered by longs |
| **Status** | LIVE |

### Bot 3: Rolling Put Diagonal (Bill Belt)

| Metric | Value |
|--------|-------|
| **Underlying** | QQQ |
| **Buying Power per Contract** | ~$1,000-1,500 |
| **Income** | Daily short put premium |
| **Annual Return** | ~30-40% |
| **Win Rate** | ~80% |
| **Frequency** | Daily rolls |
| **Max Drawdown** | ~20% (tech-heavy, higher volatility) |
| **Calmar Ratio** | ~1.75 |
| **Risk Profile** | Medium - long put provides protection |
| **Status** | DRY-RUN |

---

## Proposed New Bot Profiles

### Bot 4: MEIC (Multiple Entry Iron Condors)

| Metric | Value |
|--------|-------|
| **Underlying** | SPX 0DTE |
| **Buying Power per Trade** | ~$2,500-4,000 (per IC, 6 trades/day) |
| **Total Daily Capital** | ~$15,000-24,000 (for 6 ICs) |
| **Annual Return** | 20.7% |
| **Max Drawdown** | **4.3%** (best of all strategies!) |
| **Calmar Ratio** | **4.8** (excellent risk-adjusted) |
| **Win Rate** | ~70% |
| **Frequency** | 6 entries/day |
| **Risk Profile** | Lowest risk, market-neutral |

### Bot 5: METF (Multiple Entry Trend Following)

| Metric | Value |
|--------|-------|
| **Underlying** | SPX 0DTE |
| **Buying Power per Trade** | ~$2,000-3,500 (per spread, 6 trades/day) |
| **Total Daily Capital** | ~$12,000-21,000 |
| **Annual Return** | 50% |
| **Max Drawdown** | 8.5% |
| **Calmar Ratio** | **5.98** (excellent) |
| **Win Rate** | ~65% |
| **Frequency** | 6 entries/day (afternoon) |
| **Risk Profile** | Directional (follows trend) |

### Bot 6: SPX Put Credit Spreads

| Metric | Value |
|--------|-------|
| **Underlying** | SPX |
| **Buying Power per Spread** | ~$1,500-3,000 |
| **Annual Return** | ~40-60% |
| **Max Drawdown** | ~15-20% |
| **Calmar Ratio** | ~2.5 |
| **Win Rate** | 85% |
| **Frequency** | Daily/Weekly entries |
| **Risk Profile** | Bullish bias (loses in crashes) |

---

## Why NOT 6 Bots

### The Math Problem with Spreading $50K Across 6 Bots

| Scenario | Capital/Bot | Issue |
|----------|-------------|-------|
| Equal split (6 bots) | $8,333 each | **Most bots can only run 1-2 contracts** |
| Iron Fly needs | $3,000-5,000/contract | Only 1-2 contracts possible |
| Delta Neutral needs | $5,000-8,000/straddle | Only 1 straddle possible |
| MEIC needs (for proper 6-entry) | $15,000-24,000 | **Cannot properly run at $8K!** |
| METF needs (for proper 6-entry) | $12,000-21,000 | **Cannot properly run at $8K!** |

### Key Insight: Position Size Matters

**With $50K spread across 6 bots:**
- Each bot runs at minimum position sizes
- Transaction costs eat more of profits
- Can't properly implement multi-entry strategies (MEIC/METF need 6 positions daily!)
- No buffer for drawdowns

**The fundamental problem:** MEIC and METF are designed for **multiple entries per day**. With only $8K allocated, you can only do 1-2 entries, which defeats the purpose of the "Multiple Entry" methodology that smooths returns.

---

## Portfolio Scenarios Analyzed

### Scenario A: All 6 Bots (Bad Idea)

| Bot | Allocation | Contracts | Expected Return | Expected P&L |
|-----|------------|-----------|-----------------|--------------|
| Iron Fly | $8,333 | 1-2 | 45% | $3,750 |
| Delta Neutral | $8,333 | 1 | 40% | $3,333 |
| Rolling Put Diagonal | $8,333 | 5-6 | 35% | $2,917 |
| MEIC | $8,333 | **Broken** | **N/A** | **N/A** |
| METF | $8,333 | **Broken** | **N/A** | **N/A** |
| SPX Put Credit | $8,333 | 2-3 | 50% | $4,167 |

**Total Expected Return: ~28-30%** (but MEIC/METF won't work properly!)

**Problem:** You can't run MEIC or METF properly with only $8K each. The whole point is 6 diversified entries per day.

---

### Scenario B: Focus on 4 Bots (Better)

| Bot | Allocation | Contracts | Expected Return | Expected P&L | Max Loss |
|-----|------------|-----------|-----------------|--------------|----------|
| Iron Fly | $10,000 | 2-3 | 45% | $4,500 | -$4,000 |
| Delta Neutral | $15,000 | 2 | 40% | $6,000 | -$3,000 |
| Rolling Put Diagonal | $10,000 | 6-8 | 35% | $3,500 | -$2,500 |
| SPX Put Credit | $15,000 | 5-6 | 50% | $7,500 | -$4,500 |

**Total Expected Return: ~43%** ($21,500 on $50K)
**Max Portfolio Drawdown: ~28%** ($14,000)
**Calmar Ratio: ~1.5**

---

### Scenario C: Focus on 3 Bots (Balanced)

| Bot | Allocation | Contracts | Expected Return | Expected P&L | Max Loss |
|-----|------------|-----------|-----------------|--------------|----------|
| Delta Neutral | $20,000 | 3-4 | 40% | $8,000 | -$4,000 |
| MEIC | $20,000 | Full 6-entry | 20.7% | $4,140 | -$860 |
| Iron Fly | $10,000 | 2-3 | 45% | $4,500 | -$2,500 |

**Total Expected Return: ~33%** ($16,640 on $50K)
**Max Portfolio Drawdown: ~15%** ($7,360)
**Calmar Ratio: 2.3**

---

### Scenario D: Maximum Return (Higher Risk)

| Bot | Allocation | Contracts | Expected Return | Expected P&L | Max Loss |
|-----|------------|-----------|-----------------|--------------|----------|
| METF | $25,000 | Full 6-entry | 50% | $12,500 | -$2,125 |
| Iron Fly | $15,000 | 3-5 | 45% | $6,750 | -$6,000 |
| SPX Put Credit | $10,000 | 3-4 | 50% | $5,000 | -$2,000 |

**Total Expected Return: ~49%** ($24,250 on $50K)
**Max Portfolio Drawdown: ~20%** ($10,125)
**Calmar Ratio: 2.4**

---

### Scenario E: Conservative Portfolio (Lowest Risk) ⭐ RECOMMENDED

| Bot | Allocation | Contracts | Expected Return | Expected P&L | Max Loss |
|-----|------------|-----------|-----------------|--------------|----------|
| MEIC | $30,000 | Full 6-entry | 20.7% | $6,210 | -$1,290 |
| Delta Neutral | $20,000 | 3-4 | 40% | $8,000 | -$3,000 |

**Total Expected Return: ~28%** ($14,210 on $50K)
**Max Portfolio Drawdown: ~8.6%** ($4,290)
**Calmar Ratio: 3.3** (Excellent!)

---

## Recommendation

### Why Conservative (Scenario E)?

1. **You only have $50K** - This is not "play money." At this capital level, preservation matters.

2. **MEIC has the best risk-adjusted returns** - 4.3% max drawdown vs 20.7% annual return = 4.8 Calmar ratio. That's exceptional.

3. **Delta Neutral is already LIVE and working** - You've debugged it, it's stable. Keep the proven performer.

4. **Simplicity** - Managing 2 bots vs 6 bots is dramatically easier for monitoring and maintenance.

### Final Recommended Allocation

| Bot | Allocation | % of Capital | Why |
|-----|------------|--------------|-----|
| **MEIC** (NEW) | $30,000 | 60% | Best risk-adjusted (4.8 Calmar), market-neutral, 6-entry smoothing |
| **Delta Neutral** (KEEP) | $20,000 | 40% | Proven, stable, good returns, different underlying (SPY vs SPX) |
| Iron Fly | **$0** | 0% | **Deprecated** - MEIC is superior |
| Rolling Put Diagonal | **$0** | 0% | Still in dry-run, insufficient capital |
| METF | **$0** | 0% | Too aggressive for current capital |
| SPX Put Credit | **$0** | 0% | Wait until capital grows |

### Expected Outcomes

| Metric | Value |
|--------|-------|
| **Expected Annual Return** | ~28-32% ($14K-16K) |
| **Max Portfolio Drawdown** | ~8-10% ($4K-5K) |
| **Calmar Ratio** | 3.0-3.5 |
| **Bots to Maintain** | 2 (much simpler!) |
| **Position Sizes** | Meaningful (3-4 contracts each) |

---

## Why NOT Keep Iron Fly

This is counterintuitive since it's already built, but consider:

| Metric | Iron Fly | MEIC |
|--------|----------|------|
| Annual Return | ~45% | 20.7% |
| Max Drawdown | ~15-20% | **4.3%** |
| Calmar Ratio | ~2.5 | **4.8** |
| Entries/Day | 1 | 6 |
| Single-Entry Risk | HIGH | LOW |

**The Iron Fly's weakness:** It's a single-entry strategy. If that one entry hits your stop, you lose the whole day. With MEIC, you spread entries across the day. One loss is offset by 5 other positions.

**MEIC is essentially "Iron Fly 2.0"** - same SPX 0DTE infrastructure, but better risk management through multiple entries.

**However**, if you prefer higher returns and can stomach more volatility, keep Iron Fly at $10K and reduce MEIC to $20K (Scenario C).

---

## Implementation Path

### Phase 1 (Week 1)
1. Continue running Delta Neutral (already live)
2. Stop Iron Fly bot
3. Begin building MEIC bot (uses 95% of Iron Fly code)

### Phase 2 (Week 2)
1. MEIC dry-run testing
2. Verify 6-entry scheduling works
3. Test per-side stop loss logic

### Phase 3 (Week 3)
1. Deploy MEIC to LIVE with $30K allocated
2. Monitor first week closely
3. Keep Iron Fly code for potential re-activation if MEIC underperforms

---

## Capital Scaling Guide

### When to Add More Bots

| Capital Level | Recommended Bots | Notes |
|---------------|------------------|-------|
| **$50K** | 2 bots (MEIC + Delta Neutral) | Current recommendation |
| **$75K** | 3 bots (+ Iron Fly or METF) | Can run Iron Fly alongside MEIC |
| **$100K** | 4 bots (+ SPX Put Credit) | Full SPX diversification |
| **$150K+** | 5-6 bots (+ Rolling Put Diagonal) | Full portfolio diversification |

### Scaling Rules

1. **Never allocate less than $15K to a multi-entry strategy** (MEIC, METF)
2. **Never allocate less than $10K to Delta Neutral** (need meaningful position size)
3. **Keep at least 10% cash reserve** at $100K+ for margin calls
4. **Diversify underlyings** - Mix SPX, SPY, and QQQ bots when possible

---

## Risk Comparison Summary

| Bot | Return | Drawdown | Calmar | Risk Level |
|-----|--------|----------|--------|------------|
| MEIC | 20.7% | 4.3% | **4.8** | ⭐ Lowest |
| METF | 50% | 8.5% | **5.98** | Low |
| Delta Neutral | 40% | 12% | 3.3 | Low-Medium |
| SPX Put Credit | 50% | 17% | 2.9 | Medium |
| Iron Fly | 45% | 18% | 2.5 | Medium-High |
| Rolling Put Diagonal | 35% | 20% | 1.75 | Higher |

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-01-27 | Claude | Initial comprehensive analysis |
