# Theta Profits Strategy Analysis

**Last Updated:** 2026-01-27
**Purpose:** Comprehensive analysis of 20 Theta Profits trading strategies for potential bot implementation
**Excludes:** Delta Neutral, Iron Fly 0DTE, Rolling Put Diagonal (already implemented)

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Strategy Comparison Table](#strategy-comparison-table)
3. [Tier 1: Highest Recommendation](#tier-1-highest-recommendation)
4. [Tier 2: Strong Candidates](#tier-2-strong-candidates)
5. [Tier 3: More Complex Strategies](#tier-3-more-complex-strategies)
6. [Tier 4: Difficult to Automate](#tier-4-difficult-to-automate)
7. [Codebase Reusability Analysis](#codebase-reusability-analysis)
8. [Top 3 Recommendations](#top-3-recommendations)
9. [Implementation Roadmap](#implementation-roadmap)
10. [Strategies to Avoid](#strategies-to-avoid)
11. [Sources](#sources)

---

## Executive Summary

This document analyzes 20 options trading strategies from [Theta Profits](https://www.thetaprofits.com/) to determine the best candidates for automated bot implementation within our CALYPSO infrastructure.

### Key Findings

- **Code Reuse:** Our shared modules provide ~90-95% code reuse for most strategies
- **Best Candidates:** MEIC, METF, and SPX Put Credit Spreads offer the best risk-adjusted returns with minimal implementation effort
- **Implementation Time:** Top 3 strategies can be implemented in ~3 weeks total
- **Infrastructure Ready:** `SaxoClient`, `AlertService`, `MarketHours`, and `TechnicalIndicators` modules cover almost all requirements

### Evaluation Criteria

| Criterion | Weight | Description |
|-----------|--------|-------------|
| Risk-Adjusted Return | High | Annual return vs max drawdown |
| Win Rate | Medium | Consistency of profits |
| Automation Feasibility | High | How mechanical/rules-based is the strategy |
| Code Reuse | High | Percentage of existing infrastructure usable |
| Capital Requirements | Medium | Minimum account size needed |
| Complexity | Medium | Number of legs, adjustments, edge cases |

---

## Strategy Comparison Table

| # | Strategy | Annual Return | Win Rate | Max Drawdown | Capital | Risk (1-10) | Complexity | Automation | Reuse % |
|---|----------|---------------|----------|--------------|---------|-------------|------------|------------|---------|
| 1 | **MEIC** | 20.7% | ~70% | 4.3% | $25K+ | 3 | Medium | Excellent | 95% |
| 2 | **METF** | 50% | ~65% | 8.5% | $25K+ | 5 | Medium | Excellent | 95% |
| 3 | **SPX Put Credit Spreads** | 5x (4yr) | ~85% | Moderate | $15K+ | 5 | Low | Excellent | 95% |
| 4 | **Double Calendar** | 100%+ | 85% | Low | $40K+ | 3 | Medium | Good | 80% |
| 5 | **21 DTE BWB** | ~40% | 80% | Moderate | $15K+ | 4 | Medium | Good | 90% |
| 6 | **VIX Spike Trading** | Variable | ~80% | Moderate | $25K+ | 5 | Medium | Good | 90% |
| 7 | **Flyagonal** | ~120% | 96% | Low | $20K+ | 4 | High | Good | 85% |
| 8 | **0DTE Breakeven IC** | ~40%+ | 39.1% | ~15% | $25K+ | 6 | High | Good | 90% |
| 9 | **0DTE Levitation** | ~60%+ | 70% | Very Low | $10K+ | 2 | High | Moderate | 75% |
| 10 | **Time Flies Spread** | 45% (4mo) | 92% | Low | $15K+ | 4 | High | Moderate | 70% |
| 11 | **1-1-1 Strategy** | 60% | ~85% | Moderate | $50K+ | 5 | Medium | Moderate | 80% |
| 12 | **Jade Lizard** | ~30% | 90% | High downside | $20K+ | 6 | Medium | Good | 85% |
| 13 | **Poor Man's Covered Call** | ~25% | ~70% | Moderate | $10K+ | 5 | Medium | Moderate | 70% |
| 14 | **The Wheel** | 20% | ~80% | High | $50K+ | 5 | Low | Moderate | 65% |
| 15 | **Covered Strangle** | 75% | ~80% | High | $50K+ | 6 | Medium | Moderate | 65% |
| 16 | **112 Strategy** | 21% | ~75% | **BLOWUP** | $50K+ | 8 | High | Difficult | 75% |
| 17 | **Ratio Diagonal** | ~40% | ~75% | Moderate | $20K+ | 5 | Medium | Moderate | 75% |
| 18 | **Earnings IV Crush** | Variable | 75%+ | Moderate | $20K+ | 6 | High | Difficult | 60% |
| 19 | **Post-Earnings Spreads** | Variable | High | Moderate | $20K+ | 5 | Medium | Difficult | 60% |
| 20 | **Options on Futures** | 77% | 91% | Unknown | $100K+ | 7 | High | Difficult | 50% |

---

## Tier 1: Highest Recommendation

### 1. MEIC (Multiple Entry Iron Condors)

**Source:** [Tammy Chambless Interview](https://www.thetaprofits.com/tammy-chambless-explains-her-meic-strategy-for-trading-0dte-options/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Tammy Chambless ("Queen of 0DTE") |
| **Annual Return** | 20.7% CAR |
| **Win Rate** | ~70% |
| **Max Drawdown** | 4.3% (excellent!) |
| **Underlying** | SPX 0DTE |

#### Strategy Structure

Multiple Iron Condor positions entered throughout the trading day rather than a single entry.

```
Entry 1 (10:00 AM): Iron Condor #1
Entry 2 (10:30 AM): Iron Condor #2
Entry 3 (11:00 AM): Iron Condor #3
Entry 4 (11:30 AM): Iron Condor #4
Entry 5 (12:00 PM): Iron Condor #5
Entry 6 (12:30 PM): Iron Condor #6
```

#### Entry Rules

- **Frequency:** 6 trades/day, spaced 30-60 minutes apart
- **Credit Target:** $1.00-$1.75 per side
- **Spread Width:** 50-60 points average (max 100)
- **Timing:** Pre-scheduled entries reduce emotional decisions

#### Exit Rules

- **Stop Loss:** Set separately on each side, equal to total credit received
- **Example:** If collecting $1.50/side ($3.00 total), stop on each side = $3.00
- **Hold Period:** Until stop hit or expires worthless
- **MEIC+ Variation:** Stop at $0.10 below 1x net loss to convert breakeven days to small wins

#### Why Excellent for Automation

1. **Mechanical rules** - no discretion needed
2. **Same infrastructure** as our Iron Fly bot (SPX 0DTE)
3. **Multiple entries smooth volatility** - reduces single-entry risk
4. **Lowest drawdown** of all profitable strategies analyzed

#### Code Reuse (95%)

| Existing Module | Usage |
|-----------------|-------|
| `SaxoClient.find_iron_fly_options()` | Adapt for iron condor strikes |
| `SaxoClient.place_multi_leg_order()` | Direct reuse (4-leg orders) |
| `AlertService` | Direct reuse |
| `MarketHours` | Direct reuse |
| `EventCalendar` | Direct reuse (FOMC filter) |
| `TradeLoggerService` | Direct reuse |

#### New Code Needed

1. Multiple entry scheduling (6 times/day)
2. Iron Condor strike selection logic (OTM calls + OTM puts)
3. Per-side stop loss monitoring
4. Position aggregation across multiple entries

#### Estimated Implementation Time: 1 week

---

### 2. METF (Multiple Entry Trend Following)

**Source:** [METF 0DTE Strategy](https://www.thetaprofits.com/how-to-trade-the-metf-0dte-options-strategy/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Dan Yaklin |
| **Annual Return** | 50% CAR |
| **Win Rate** | ~65% |
| **Max Drawdown** | 8.5% |
| **Calmar Ratio** | 5.98 (vs 2.15 for SPX buy-and-hold) |
| **Underlying** | SPX 0DTE |

#### Strategy Structure

Directional credit spreads based on EMA crossover signals.

```
If 20 EMA > 40 EMA (bullish): Sell PUT credit spreads
If 20 EMA < 40 EMA (bearish): Sell CALL credit spreads
```

#### Entry Rules

- **Timing:** 6 entries at 12:30, 1:00, 1:30, 2:00, 2:30, 2:45 ET
- **Signal:** 20-period EMA vs 40-period EMA on 1-minute chart
- **Spread Width:** 25-35 points
- **Credit Target:** $1.25-$2.50

#### Exit Rules

- **Stop Loss:** 2x premium received (1:1 risk-reward)
- **Hold Period:** To expiration if stops not triggered
- **Afternoon Adjustment:** Stops migrate to short-strike-only protection

#### Why Excellent for Automation

1. **Uses our existing `TechnicalIndicators` module** - EMA already implemented!
2. **Directional bias** provides diversification from MEIC (market-neutral)
3. **Mechanical signals** - no discretion
4. **Same SPX 0DTE infrastructure**

#### Code Reuse (95%)

| Existing Module | Usage |
|-----------------|-------|
| `TechnicalIndicators.calculate_ema()` | **Direct use!** |
| `SaxoClient.get_chart_data()` | For 1-minute bars |
| `SaxoClient.place_order()` | Credit spread orders |
| All shared modules | Direct reuse |

#### New Code Needed

1. 1-minute chart data fetching and caching
2. EMA crossover signal detection (20 vs 40)
3. Directional spread selection logic
4. Entry time scheduling (afternoon focus)

#### Estimated Implementation Time: 1 week

#### Complementary Strategy

METF pairs well with MEIC for a "smoother equity curve with smaller drawdowns than if trading each individually."

---

### 3. SPX Put Credit Spreads

**Source:** [Put Credit Spread](https://www.thetaprofits.com/put-credit-spread/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Robert McIntosh |
| **Return** | 5x account growth in 4 years |
| **Win Rate** | ~85% |
| **Underlying** | SPX |

#### Strategy Structure

The simplest vertical spread strategy - only 2 legs.

```
Sell: 1 OTM Put (higher strike)
Buy:  1 Further OTM Put (lower strike, protection)
```

#### Entry Rules

- **Strike Selection:** Sell 10-20 delta puts
- **Width:** 20-30 points typically
- **DTE:** Can be 0DTE or weekly/monthly
- **Premium:** Target sufficient credit for risk/reward

#### Exit Rules

- **Profit Target:** 50% of max profit
- **Stop Loss:** Varies by trader preference
- **Time Exit:** Close before expiration if profitable

#### Why Excellent for Automation

1. **Simplest strategy** - only 2 legs
2. **High win rate** - 85%
3. **Defined risk** - max loss known at entry
4. **Fastest implementation** - ~3 days

#### Code Reuse (95%)

| Existing Module | Usage |
|-----------------|-------|
| `SaxoClient.place_multi_leg_order()` | Just 2 legs |
| `SaxoClient.find_atm_options()` | For strike discovery |
| All shared modules | Direct reuse |

#### New Code Needed

1. Basic vertical spread placement
2. 50% profit target monitoring
3. Simple stop loss logic

#### Estimated Implementation Time: 3 days

---

## Tier 2: Strong Candidates

### 4. Double Calendar Spread

**Source:** [Double Calendar - 85% Win Rate](https://www.thetaprofits.com/double-calendar-the-low-risk-trade-behind-an-85-win-rate/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Ravish Ahuja |
| **Annual Return** | 100%+ on allocated capital |
| **Win Rate** | 85% |
| **Risk Rating** | 3/10 |
| **Underlying** | SPX, QQQ, SPY |

#### Strategy Structure

Combines a call calendar and put calendar at different strikes.

```
Call Calendar (above current price):
  - Sell short-term call (10-15 DTE)
  - Buy longer-term call (same strike)

Put Calendar (below current price):
  - Sell short-term put (10-15 DTE)
  - Buy longer-term put (same strike)
```

#### Entry Rules

- **Timing:** Tuesday/Wednesday entry
- **Short Leg DTE:** 10-15 days (expire Friday next week)
- **Strike Placement:** Near expected move boundaries
- **Capital Allocation:** 20% of account maximum

#### Exit Rules

- **Profit Target:** 15-20% return on risk
- **Time Exit:** 2-3 days before short expiration (avoid "sag")
- **Loss Management:** Stay in until price reaches short strike

#### Challenge for Automation

- Requires managing 2 different expirations
- More complex Greeks management
- IV sensitivity

#### Code Reuse: 80%

---

### 5. 21 DTE Put Broken Wing Butterfly

**Source:** [Broken Wing Butterfly](https://www.thetaprofits.com/broken-wing-butterfly-a-high-probability-options-strategy/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Carl Allen |
| **Annual Return** | ~40% |
| **Win Rate** | 80% (70% with recommended stops) |
| **Underlying** | SPX |

#### Strategy Structure

Asymmetric put butterfly with unequal wing widths.

```
Buy:  1 Put at 32 delta (highest strike)
Sell: 2 Puts at 28 delta (middle strike)
Buy:  1 Put at 21 delta (lowest strike, wider wing)
```

#### Entry Rules

- **DTE:** 21 days to expiration
- **Strike Selection:** Delta-based (32/28/21)
- **Width Rule:** Widest spread = 2x narrowest spread
- **Premium Target:** 12-15% of narrowest spread

#### Exit Rules

- **Profit Target:** 2% of narrowest spread (take immediately)
- **Stop Loss:** 2x premium collected
- **Alternative:** Rolling management for losing positions

#### Code Reuse: 90%

Similar multi-leg structure to Iron Fly.

---

### 6. VIX Spike Trading

**Source:** [VIX Spike Strategy](https://www.thetaprofits.com/vix-spikes-here-is-an-easy-options-trading-strategy-to-profit/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Kevin Kwan |
| **Return** | Variable (supplemental strategy) |
| **Win Rate** | ~80% |
| **Underlying** | VIX options |

#### Strategy Structure

Short VIX when elevated, profit from mean reversion.

```
VIX Level    | Position Size | Strike Range
-------------|---------------|-------------
20-30        | 5% capital    | Minimal
40-50        | Increase      | 70-90 strikes
60+          | Maximum       | Defined-risk spreads
```

#### Entry Rules

- **Trigger:** VIX > 30 to start
- **Scaling:** Increase position as VIX rises
- **Structure:** Defined-risk spreads at extreme levels

#### Exit Rules

- **DTE:** 30-70 days to expiration (monthly)
- **No traditional stop losses**
- **Hold through mean reversion**

#### Why Good for Us

**We already track VIX!** Uses existing `get_vix_level()` function.

#### Code Reuse: 90%

VIX infrastructure already exists in `SaxoClient`.

---

### 7. Flyagonal

**Source:** [Flyagonal Strategy](https://www.thetaprofits.com/flyagonal-how-a-hybrid-options-strategy-hit-a-96-win-rate/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Steve Ganz |
| **Return** | ~$24,000 from 60 trades |
| **Win Rate** | 96% (58 wins, 2 losses) |
| **Underlying** | SPX, RUT, QQQ, TSLA, NVDA |

#### Strategy Structure

Hybrid combining two components:

```
Above Current Price: Call Broken Wing Butterfly
  - Benefits from moderate upward movement
  - Profits from time decay

Below Current Price: Put Diagonal
  - Profits from price drops
  - Benefits from volatility spikes
```

#### Entry Rules

- **Short Leg DTE:** 8-10 days
- **Long Leg DTE:** 2x short (16-20 days)
- **Strike Distance:** ~3% from current price
- **Profit Target:** ~10% of max loss

#### Exit Rules

- **Hold Duration:** 3-5 days typically
- **Never hold to expiration**
- **Adjustments:** Rare, simple rolls of short leg

#### Challenge for Automation

- Complex multi-leg, multi-expiry management
- 6+ legs total
- Requires sophisticated position tracking

#### Code Reuse: 85%

---

## Tier 3: More Complex Strategies

### 8. 0DTE Breakeven Iron Condor

**Source:** [0DTE Breakeven Iron Condor](https://www.thetaprofits.com/my-most-profitable-options-trading-strategy-0dte-breakeven-iron-condor/)

| Attribute | Value |
|-----------|-------|
| **Annual Return** | ~40%+ |
| **Win Rate** | 39.1% (but wins 2x losses) |
| **Max Drawdown** | ~15% |
| **Underlying** | SPX 0DTE |

#### Key Difference from Standard IC

Equal premium on both sides, tight stop-losses on each side separately.

#### Risk Warning

**Double stop-loss events** occur in 6.2% of trades (rising to 10.5% in 2024).

---

### 9. 0DTE Levitation Trades

**Source:** [0DTE Levitation](https://www.thetaprofits.com/0dte-levitation-trades-how-boomer-dan-creates-risk-free-spx-profits-by-the-end-of-the-day/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Boomer Dan |
| **Win Rate** | 70% achieve "levitation" |
| **Risk After Levitation** | 0 (mathematically impossible to lose) |
| **Underlying** | SPX 0DTE |

#### Strategy Concept

Start with credit spread, convert to butterfly as profit builds, creating "risk-free" profit zone.

#### Challenge for Automation

Requires real-time P&L monitoring and dynamic leg addition throughout the day.

---

### 10. Time Flies Spread

**Source:** [Time Flies Spread](https://www.thetaprofits.com/the-time-flies-spread-a-smarter-way-to-trade-theta/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Simon Black |
| **Return** | 45% in <4 months |
| **Win Rate** | 92% (11/12 in 2025) |
| **Capital/Trade** | $3,000 buying power |
| **Underlying** | RUT |

#### Strategy Structure

```
Put Diagonal (below market):
  - Sell near-term put
  - Buy longer-term put at different strike

Call Broken Wing Butterfly (above market):
  - Short 2 calls + long calls at extended strikes
```

#### Code Reuse: 70%

Requires RUT options (not currently implemented in our Saxo setup).

---

### 11. 1-1-1 Strategy

**Source:** [1-1-1 Strategy](https://www.thetaprofits.com/inside-the-1-1-1-options-strategy-rules-mechanics-and-results/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Zoheb Noormohamed |
| **Annual Return** | ~60% |
| **Win Rate** | ~85% |
| **Risk** | Undefined (naked short put) |

#### Strategy Structure

```
Long Put:  ~30 delta
Short Put: 5-10 points lower (debit spread)
Short Put: ~20 delta (financing put - NAKED)
```

#### Warning

**Undefined risk** on the naked short put. Requires active management at 21 DTE.

---

## Tier 4: Difficult to Automate

### 12. 112 Strategy

**Source:** [112 Strategy](https://www.thetaprofits.com/112-options-trading-strategy-profit-potential-and-risks/)

| Attribute | Value |
|-----------|-------|
| **Trader** | Murray Lindhoet |
| **Return** | $50K to $500K in 4 years |
| **Risk** | **EXTREME - Lost 4 years of profits in August 2024** |

#### DO NOT IMPLEMENT

Murray experienced catastrophic losses during August 2024 volatility spike. Not suitable for automation.

---

### 13-16. Stock-Based Strategies

| Strategy | Issue |
|----------|-------|
| **The Wheel** | Requires stock ownership, assignment handling |
| **Covered Strangle** | Requires 100+ shares, complex assignment |
| **Poor Man's Covered Call** | LEAPS management, rolling complexity |
| **Jade Lizard** | Undefined downside risk |

---

### 17-20. Specialized Strategies

| Strategy | Issue |
|----------|-------|
| **Earnings IV Crush** | Requires earnings calendar, sporadic |
| **Post-Earnings Spreads** | Same as above |
| **Options on Futures (NQ/ES)** | Different API, $100K+ capital |
| **Ratio Diagonal** | Complex adjustment logic |

---

## Codebase Reusability Analysis

### Existing Shared Modules

| Module | Lines | Reusable For |
|--------|-------|--------------|
| `SaxoClient` | 4,282 | ALL strategies - orders, positions, quotes, options chain |
| `TradeLoggerService` | 3,660 | ALL strategies - Google Sheets logging |
| `AlertService` | 1,008 | ALL strategies - SMS/Email alerts |
| `MarketHours` | 797 | ALL strategies - market session detection |
| `TechnicalIndicators` | ~300 | METF, any EMA/MACD-based strategies |
| `EventCalendar` | ~200 | ALL 0DTE strategies - FOMC filter |
| `TokenCoordinator` | ~150 | ALL strategies - multi-bot token sync |
| `ExternalPriceFeed` | ~100 | ALL strategies - Yahoo Finance fallback |

### Key Existing Functions

```python
# Option Discovery
find_atm_options()           # ATM strike discovery
find_strangle_options()      # Strangle legs by delta
find_iron_fly_options()      # Multi-leg discovery
get_option_chain()           # Full chain with Greeks

# Order Execution
place_multi_leg_order()      # 4-leg order execution
place_order_with_retry()     # Automatic retry
place_market_order_immediate() # Quick fills

# Price Data
start_price_streaming()      # Real-time WebSocket
get_quote()                  # Cached quotes
get_vix_level()              # VIX monitoring

# Technical Analysis
calculate_ema()              # For METF strategy
calculate_macd()             # Available if needed
calculate_cci()              # Available if needed
```

### What New Strategies Need

| Strategy | New Code Required |
|----------|-------------------|
| **MEIC** | Multi-entry scheduling, IC strike selection, per-side stops |
| **METF** | 1-min chart fetching, EMA crossover signal, directional logic |
| **SPX Put Credit** | Basic vertical placement, 50% profit target |
| **Double Calendar** | Multi-expiry management |
| **21 DTE BWB** | Delta-based strike selection |
| **VIX Spike** | VIX level triggers, scaling logic |

---

## Top 3 Recommendations

### Recommendation Summary

| Rank | Strategy | Return | Drawdown | Implementation | Why |
|------|----------|--------|----------|----------------|-----|
| 1 | **MEIC** | 20.7% | 4.3% | 1 week | Lowest risk, mechanical, 95% reuse |
| 2 | **METF** | 50% | 8.5% | 1 week | Uses existing EMA, directional diversification |
| 3 | **SPX Put Credit** | High | Moderate | 3 days | Simplest to implement |

### Why This Order?

1. **MEIC First:** Lowest drawdown (4.3%) means safest to deploy. Same SPX 0DTE infrastructure as Iron Fly. Mechanical rules perfect for automation.

2. **METF Second:** Higher return (50%) with acceptable drawdown (8.5%). Uses our existing `TechnicalIndicators` module. Provides directional diversification vs MEIC's market-neutral approach.

3. **SPX Put Credit Third:** Simplest implementation (~3 days). High win rate (85%). Good baseline income strategy while other bots run.

### Diversification Benefits

| Strategy | Market Bias | Volatility Preference |
|----------|-------------|----------------------|
| MEIC | Neutral | Low-Medium |
| METF | Directional (trend) | Any |
| SPX Put Credit | Bullish | Low-Medium |
| Iron Fly (existing) | Neutral | Low |
| Delta Neutral (existing) | Neutral | Any |

---

## Implementation Roadmap

### Phase 1: MEIC Bot (Week 1)

```
Day 1-2: Strategy logic
  - Iron Condor strike selection (OTM calls + OTM puts)
  - Entry scheduling (6 times/day)
  - Per-side stop loss monitoring

Day 3-4: Integration
  - Config file setup
  - Alert integration
  - Google Sheets logging

Day 5: Testing
  - Dry-run mode validation
  - Edge case handling
  - Circuit breaker setup

Day 6-7: Deployment
  - VM deployment
  - Live monitoring
  - Documentation
```

### Phase 2: METF Bot (Week 2)

```
Day 1-2: Technical Analysis
  - 1-minute chart data fetching
  - EMA crossover signal (20 vs 40 period)
  - Signal caching

Day 3-4: Strategy Logic
  - Directional spread selection
  - Entry time scheduling (afternoon)
  - Stop loss at 2x premium

Day 5-7: Integration & Deployment
  - Same process as MEIC
```

### Phase 3: SPX Put Credit Bot (Week 3, Days 1-3)

```
Day 1: Strategy Logic
  - Simple 2-leg vertical spread
  - Delta-based strike selection

Day 2: Exit Logic
  - 50% profit target
  - Stop loss

Day 3: Deployment
  - Quick deployment (simplest bot)
```

### Post-Implementation (Week 3, Days 4-7)

- Monitor all new bots
- Fine-tune parameters
- Document lessons learned
- Plan next strategies (Double Calendar, VIX Spike)

---

## Strategies to Avoid

### High Risk of Catastrophic Loss

| Strategy | Reason |
|----------|--------|
| **112 Strategy** | Murray lost 4 years of profits in one day (Aug 2024) |
| **1-1-1 Strategy** | Undefined risk on naked short put |
| **Covered Strangle** | 2x share assignment risk |

### Too Complex for Current Infrastructure

| Strategy | Reason |
|----------|--------|
| **Options on Futures** | Requires different API calls, $100K+ capital |
| **Earnings Strategies** | Sporadic, requires earnings calendar integration |
| **Stock-Based Strategies** | Requires share ownership, assignment handling |

### Questionable Risk/Reward

| Strategy | Reason |
|----------|--------|
| **0DTE Breakeven IC** | 39% win rate, double stop-out risk increasing |
| **Jade Lizard** | Unlimited downside risk |

---

## Sources

### Primary Strategy Sources (Theta Profits)

- [Home - Theta Profits](https://www.thetaprofits.com/)
- [MEIC Strategy - Tammy Chambless](https://www.thetaprofits.com/tammy-chambless-explains-her-meic-strategy-for-trading-0dte-options/)
- [METF 0DTE Strategy](https://www.thetaprofits.com/how-to-trade-the-metf-0dte-options-strategy/)
- [0DTE Breakeven Iron Condor](https://www.thetaprofits.com/my-most-profitable-options-trading-strategy-0dte-breakeven-iron-condor/)
- [Flyagonal Strategy](https://www.thetaprofits.com/flyagonal-how-a-hybrid-options-strategy-hit-a-96-win-rate/)
- [Double Calendar Spread](https://www.thetaprofits.com/double-calendar-the-low-risk-trade-behind-an-85-win-rate/)
- [Jade Lizard Strategy](https://www.thetaprofits.com/jade-lizard-options-strategy-explained-no-upside-risk/)
- [1-1-1 Strategy](https://www.thetaprofits.com/inside-the-1-1-1-options-strategy-rules-mechanics-and-results/)
- [112 Strategy](https://www.thetaprofits.com/112-options-trading-strategy-profit-potential-and-risks/)
- [0DTE Levitation Trades](https://www.thetaprofits.com/0dte-levitation-trades-how-boomer-dan-creates-risk-free-spx-profits-by-the-end-of-the-day/)
- [Time Flies Spread](https://www.thetaprofits.com/the-time-flies-spread-a-smarter-way-to-trade-theta/)
- [Poor Man's Covered Call](https://www.thetaprofits.com/how-to-trade-poor-mans-covered-call-with-dale-perryman/)
- [Covered Strangle](https://www.thetaprofits.com/covered-strangle-how-to-profit-a-beginners-guide/)
- [VIX Spike Strategy](https://www.thetaprofits.com/vix-spikes-here-is-an-easy-options-trading-strategy-to-profit/)
- [Broken Wing Butterfly](https://www.thetaprofits.com/broken-wing-butterfly-a-high-probability-options-strategy/)
- [The Wheel Strategy](https://www.thetaprofits.com/the-wheel-options-strategy-with-a-twist-simple-steps-for-consistent-income/)
- [Options on Futures](https://www.thetaprofits.com/options-on-futures-explained-income-strategies-using-nq-and-es/)
- [Earnings Trades](https://www.thetaprofits.com/how-to-succeed-with-earnings-trades-using-options/)
- [Put Credit Spread](https://www.thetaprofits.com/put-credit-spread/)

### Internal Documentation

- [CLAUDE.md](../CLAUDE.md) - Project overview and bot details
- [SAXO_API_PATTERNS.md](SAXO_API_PATTERNS.md) - Saxo API integration patterns
- [IRON_FLY_CODE_AUDIT.md](IRON_FLY_CODE_AUDIT.md) - Iron Fly implementation reference
- [DELTA_NEUTRAL_EDGE_CASES.md](DELTA_NEUTRAL_EDGE_CASES.md) - Delta Neutral reference

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-01-27 | Claude | Initial comprehensive analysis |
