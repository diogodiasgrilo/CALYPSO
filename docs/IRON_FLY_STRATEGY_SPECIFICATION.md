# Iron Fly 0DTE Strategy Specification

**Version:** 1.0.0
**Last Updated:** 2026-02-02
**Strategy Sources:**
- Doc Severson (Theta Profits) - Primary strategy framework
- Jim Olson (0DTE.com) - Wing width optimization rules

---

## Executive Summary

The 0DTE Iron Fly is a short-duration, theta-decay strategy that profits from time decay on same-day expiring SPX options. The strategy enters at 10:00 AM EST after the morning volatility settles and aims to exit within 60 minutes with a profit target of 30% of credit received.

**Key Statistics (Doc Severson's Results):**
- Average hold time: 18 minutes
- Win rate: 85-95%
- Profit factor: 2.0-3.8
- Risk rating: 4/10 (Moderate)

---

## 1. Strategy Structure

### 1.1 Iron Fly Anatomy

```
           PROFIT
              ^
              |     /\
              |    /  \
              |   /    \
   Max Profit |--/------\--  <- ATM Strike (short straddle)
              | /        \
              |/          \
--------------+------------\--------------> PRICE
             /|            |\
            / |            | \
           /  |            |  \
    Lower |   |            |   | Upper
    Wing  |   |            |   | Wing
          |   |            |   |
        Long  Short      Short  Long
        Put   Put        Call   Call
```

### 1.2 Position Components

| Leg | Option | Action | Strike | Purpose |
|-----|--------|--------|--------|---------|
| 1 | Put | BUY | ATM - Wing Width | Downside protection (long wing) |
| 2 | Put | SELL | ATM | Collect premium |
| 3 | Call | SELL | ATM | Collect premium |
| 4 | Call | BUY | ATM + Wing Width | Upside protection (long wing) |

**Net Position:** Credit received (premium collected > premium paid)

---

## 2. Wing Width Calculation

### 2.1 The Problem with Pure Expected Move

Doc Severson uses expected move (EM) as wing width. However, on low VIX days:
- EM might be only 25-30 points
- Credit collected is too small (~$15-20)
- Commission ($20) eats most of the profit

### 2.2 Jim Olson's Solution

From [0DTE.com](https://0dte.com/jim-olson-iron-butterfly-0dte-trade-plan):

> "If the Implied Move is under $30, I will simply use $50 wings."

> "Keep increasing the wing size by $10 until you stop receiving at least a $1.00 extra credit."

### 2.3 Our Implementation (Hybrid Approach)

1. **Calculate Expected Move** from ATM 0DTE straddle price
2. **Enforce Minimum Wing Width** of 40 points (configurable)
3. **Target Credit** should be ~30% of wing width

| Expected Move | Wing Width Used | Rationale |
|---------------|-----------------|-----------|
| < 40 pts | 40 pts (minimum) | Jim Olson rule - ensure adequate credit |
| 40-60 pts | Expected Move | Doc Severson rule - wings at EM |
| > 60 pts | Expected Move | High vol day - wider wings natural |

### 2.4 Credit-to-Wing-Width Rule

Jim Olson's rule of thumb: **Credit should be 30-35% of wing width**

| Wing Width | Target Credit | Minimum Acceptable |
|------------|---------------|-------------------|
| 40 pts | $12-$14 | $12 |
| 50 pts | $15-$17.50 | $15 |
| 60 pts | $18-$21 | $18 |

---

## 3. Entry Rules

### 3.1 Timing

| Rule | Value | Rationale |
|------|-------|-----------|
| Entry Time | 10:00 AM EST exactly | Morning shakeout must finish |
| Opening Range | 9:30-10:00 AM | Track high/low for filters |
| No Early Entry | NEVER before 10:00 AM | Volatility too unpredictable |

### 3.2 Pre-Trade Filters

| Filter | Threshold | Action if Failed |
|--------|-----------|------------------|
| VIX Level | < 20 | Skip day |
| VIX Spike | < 5% during opening range | Skip day |
| Gap Down | < 1% overnight gap | Skip day (Trend Day) |
| FOMC Day | Check calendar | Skip day |
| Major Economic Data | CPI, PPI, Jobs Report | Skip day |
| Price in Opening Range | Must be inside range | Skip day |
| Price Near Midpoint | Within middle 70% | Skip day |

### 3.3 Strike Selection

**ATM Strike:** First strike ABOVE current SPX price (Doc Severson's bias rule)
- Compensates for put skew
- Example: SPX at 6937 -> ATM strike = 6940

**Wings:** ATM ± Wing Width (calculated per Section 2)

---

## 4. Exit Rules

### 4.1 Profit Target

**Dynamic Target:** 30% of credit received (net after commission)

| Credit Received | 30% Target | + Commission | Gross Target |
|-----------------|------------|--------------|--------------|
| $15.00 | $4.50 | $20.00 | $24.50 (capped at $15) |
| $20.00 | $6.00 | $20.00 | $26.00 (capped at $20) |
| $25.00 | $7.50 | $20.00 | $27.50 (capped at $25) |
| $30.00 | $9.00 | $20.00 | $29.00 |
| $50.00 | $15.00 | $20.00 | $35.00 |

**Minimum Target:** $25 per contract (floor)

**CRITICAL (Fix #22, 2026-02-02):** Profit target is CAPPED at credit received!
- Max possible profit on an Iron Fly = 100% of credit (if expires at ATM)
- If calculated target > credit, we cap it at credit
- Example: $15 credit → 30% = $4.50 → floor max($4.50, $25) = $25 → + $20 = $45
  - But $45 > $15 credit → IMPOSSIBLE! → Cap at $15 (100% capture)
- This ensures the target is always achievable

### 4.2 Stop Loss

**Type:** Wing Touch (software-based, not broker stops)

| Trigger | Action |
|---------|--------|
| SPX touches upper wing | MARKET ORDER close all legs immediately |
| SPX touches lower wing | MARKET ORDER close all legs immediately |

**Critical:** Use MARKET orders, not limits. Speed matters more than price on stop-outs.

**Typical Stop Loss:** ~$300-$350 per contract

### 4.3 Time Exit (The 11:00 AM Rule)

| Trigger | Action |
|---------|--------|
| 60 minutes elapsed | Close position at market |
| Profit target not hit | Accept whatever P&L |

Doc Severson: "Don't overstay. Exit by 11:00 AM if no profit target hit."

### 4.4 Exit Priority

1. **Wing Touch** - Immediate market order (highest priority)
2. **Profit Target** - Close when target reached
3. **Time Exit** - 11:00 AM hard stop
4. **Circuit Breaker** - Emergency close on system issues

---

## 5. Position Sizing

### 5.1 Contract Quantity

| Account Size | Contracts | Max Risk |
|--------------|-----------|----------|
| < $25,000 | 1 | ~$350 |
| $25,000-$50,000 | 1-2 | ~$700 |
| > $50,000 | 2-3 | ~$1,050 |

**Current Setting:** 1 contract (testing phase)

### 5.2 Commission Costs

| Component | Cost |
|-----------|------|
| Per leg open | $2.50 |
| Per leg close | $2.50 |
| Round-trip per leg | $5.00 |
| Total (4 legs) | $20.00 |

---

## 6. Expected Performance

### 6.1 Typical Trade Outcomes

| Outcome | Probability | P&L (1 contract) |
|---------|-------------|------------------|
| Profit target hit | ~70% | +$5 to +$15 net |
| Time exit (small profit) | ~15% | +$0 to +$10 net |
| Wing touch (stop loss) | ~15% | -$300 to -$350 |

### 6.2 Monthly Expectations (20 trading days)

| Metric | Conservative | Optimistic |
|--------|--------------|------------|
| Trades | 15-18 | 18-20 |
| Win Rate | 80% | 90% |
| Avg Win | $8 | $12 |
| Avg Loss | $325 | $300 |
| Monthly P&L | +$20 to +$80 | +$100 to +$200 |

**Note:** These are estimates based on 1 contract. Scale linearly with position size.

---

## 7. Risk Management

### 7.1 Circuit Breaker

| Trigger | Action |
|---------|--------|
| 5 consecutive API failures | Halt trading, emergency close |
| 5 of 10 API calls fail | Halt trading, emergency close |
| 3 circuit breaker triggers/day | Daily halt |

### 7.2 Max Loss Per Day

| Setting | Value |
|---------|-------|
| Max loss per trade | ~$400 (wing width - credit) |
| Max trades per day | 1 |
| Max daily loss | ~$400 |

### 7.3 Critical Intervention

Manual halt requiring explicit reset. Triggered by:
- Emergency close failure
- Unrecoverable system error
- Multiple circuit breaker triggers

---

## 8. Configuration Reference

```json
{
    "strategy": {
        "entry_time_est": "10:00",
        "opening_range_minutes": 30,
        "max_vix_entry": 20.0,
        "vix_spike_threshold_percent": 5.0,
        "gap_down_abort_percent": 1.0,

        "min_wing_width": 40,
        "target_credit_percent": 30,
        "min_credit_per_contract": 12.0,
        "max_credit_per_contract": 30.0,

        "profit_target_percent": 30,
        "profit_target_min": 25.0,
        "stop_loss_type": "wing_touch",
        "max_hold_minutes": 60,

        "position_size": 1,
        "commission_per_leg": 5.0
    }
}
```

---

## 9. Trading Rules Summary

1. **NEVER** enter before 10:00 AM EST
2. **NEVER** trade on Trend Days (gap down 1%+, waterfall selloff)
3. **NEVER** trade on FOMC or major economic data days
4. **ALWAYS** exit by 11:00 AM if no profit target hit
5. **ALWAYS** use Market Order for stop-loss (no limits on bail-out)
6. **ALWAYS** use minimum 40-point wing width (Jim Olson rule)
7. Target small, consistent profits and WALK AWAY

---

## 10. References

- [Doc Severson - The 0DTE Iron Fly](https://www.thetaprofits.com/the-0dte-iron-fly-a-faster-more-disciplined-way-to-trade-spx/)
- [Jim Olson - Iron Butterfly 0DTE Trade Plan](https://0dte.com/jim-olson-iron-butterfly-0dte-trade-plan)
- [Dale's $1.5M in Two Years](https://www.thetaprofits.com/0dte-iron-fly-on-spx-how-dale-made-15m-in-two-years/)

---

## Appendix: Lessons Learned

### 2026-02-02: Wing Width Fix

**Problem:** Friday 2026-01-30 trade had 35-point wings with only $23.50 credit (13% of wing width). This is too narrow per Jim Olson's guidelines.

**Root Cause:** Bot used pure expected move without minimum wing width enforcement.

**Fix:** Added `min_wing_width: 40` config and code to enforce it. Now if EM < 40, we use 40-point wings to ensure adequate credit collection.

### 2026-01-31: Profit Target Fix

**Problem:** Hardcoded $75 profit target was impossible to hit with $23.50 credit (would need 300%+ return).

**Fix:** Changed to dynamic 30% of credit target with commission accounting.

### 2026-02-01: Fill Price Sync Delay

**Problem:** P&L showed wrong values during trade because fill prices weren't fetched correctly.

**Fix:** Increased retry time for activities endpoint to allow Saxo's sync delay.

### 2026-02-02: Fill Price Source Fix

**Problem:** Bot showed -$22 P&L but actual Saxo P&L was -$150. The code was falling back to `PositionView.AverageOpenPrice` which is ALWAYS 0 for all positions.

**Fix:** Changed to use `PositionBase.OpenPrice` which contains actual fill prices for both long AND short positions.

### 2026-02-02: Profit Target Cap Fix

**Problem:** With $25 minimum floor + $20 commission = $45 target. But if credit is only $15-$30, the max possible profit is the credit (100% capture), making $45 impossible.

**Fix:** Profit target is now CAPPED at credit received. If calculated target exceeds max possible profit, bot logs a warning and uses credit as target instead.
