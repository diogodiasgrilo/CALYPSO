# Delta Neutral Strategy Specification

**Strategy Source:** Brian Terry (Theta Profits)
**Bot Version:** 2.0.4
**Last Updated:** 2026-02-01
**Research Date:** 2026-01-27

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 2.0.4 | 2026-02-01 | Enhanced safety features: ORDER-006 order size validation (max 10 contracts/order, 20 per underlying), ORDER-007 fill price slippage monitoring (5% warning, 15% critical), ORDER-008 emergency close retries with spread normalization wait. Activities endpoint retry logic for sync delays. |
| 2.0.3 | 2026-01-29 | Opening range delay: When bot has 0 positions, waits until 10:00 AM ET before entering (configurable via `fresh_entry_delay_minutes`). Prevents entering on volatile/misleading opening VIX readings. New `WAITING_OPENING_RANGE` state with 1-minute heartbeats. Does NOT apply to re-entries when we already have longs. |
| 2.0.2 | 2026-01-29 | Safety extension: If 1.33x floor gives zero/negative return, scan extends to 1.0x. Target return updated from 1.0% to 1.5% (optimal EV based on IV>RV premium analysis). Strike selection now has 3-tier priority system. |
| 2.0.1 | 2026-01-28 | REST-only mode for reliability. Disabled WebSocket streaming (`USE_WEBSOCKET_STREAMING=False`). VIGILANT monitoring: 2-second intervals (30 REST API calls/min, within rate limits). WebSocket code preserved for future use if 4+ bots need rate limit relief. |
| 2.0.0 | 2026-01-28 | Adaptive cushion-based roll trigger (75% consumed = roll, 60% = vigilant). Immediate next-week shorts entry after scheduled debit skip. Added `entry_underlying_price` tracking to StranglePosition for cushion calculations. 10 WebSocket reliability fixes (CONN-007 through CONN-016). |
| 1.0.0 | 2026-01-23 | Initial production release with proactive restart check |

## Overview

The Delta Neutral strategy consists of a long-term ATM straddle against which you sell short-term OTM strangles for weekly income. The strategy aims to collect premium from theta decay while maintaining delta neutrality to avoid directional risk.

---

## Strategy Components

### Long Position (Longs)
- **Structure:** ATM Straddle (1 Call + 1 Put at same strike)
- **DTE on Entry:** 90-120 DTE (targeting ~120 DTE)
- **DTE Exit Threshold:** 60 DTE (close entire position when longs reach this)
- **Strike Selection:** ATM (at-the-money) based on current SPY price
- **Purpose:** Delta hedge and profit from large moves

### Short Position (Shorts)
- **Structure:** OTM Strangle (1 Call + 1 Put at different strikes)
- **DTE on Entry:** 5-12 DTE (typically target next Friday)
- **Strike Selection:** 1.33x - 2.0x expected move from ATM (Updated 2026-01-29)
  - **Maximum:** 2.0x expected move (scan starting point - widest/safest strikes)
  - **Minimum Floor:** 1.33x expected move (ensures roll trigger lands at 1.0x EM when 75% consumed)
  - **Safety Extension:** If 1.33x gives zero/negative return, scan extends to 1.0x
  - **Symmetry:** Both legs within 0.3x of each other (for delta neutrality)
  - **Expected Move:** Calculated from ATM straddle price (Call mid + Put mid)
- **Purpose:** Collect weekly premium to offset long theta cost and generate 1.5% NET return

### Strike Selection Priority (2026-01-29)

The bot uses a 3-tier fallback system to balance profit target vs safety:

1. **Optimal:** Find highest symmetric multiplier (2.0x→1.33x) achieving 1.5% NET return
   - Result: Widest strikes = safest, still hitting target return
2. **Fallback:** If target can't be met, use 1.33x floor strikes with whatever positive return
   - Result: Safe roll trigger (75% cushion consumed → 1.0x EM boundary), lower return
3. **Safety Extension:** If floor strikes give zero/negative return, scan 1.33x→1.0x for first positive
   - Result: Extends closer to ATM if needed, ensures positive expectancy
   - Note: Roll trigger will be closer than 1.0x EM, less safe but still profitable
4. **Abort:** If no positive return found even at 1.0x, skip entry for that week

**Why 1.33x Floor?**
- Formula: `roll_trigger = multiplier × 0.75` (roll triggers at 75% cushion consumed)
- At 1.33x multiplier: `1.33 × 0.75 = 1.0x` (roll triggers exactly at expected move boundary)
- This is the mathematical minimum to ensure rolls happen before statistically likely breaches

---

## Entry Conditions

### VIX Filter
- **Rule:** Only open new Delta Neutral trades when **VIX < 18**
- **Rationale:** Low VIX = cheaper long straddle. If VIX then spikes, straddle gains value
- **Implementation:** Check VIX before entering long straddle

### Opening Range Delay (Added 2026-01-29)
- **Rule:** When bot has 0 positions and wants to enter fresh, wait until **10:00 AM ET**
- **Rationale:** First 30 minutes after market open are volatile - VIX can spike/drop misleadingly
- **Implementation:** `_is_within_opening_range()` checks current time and position status
- **State:** Bot enters `WAITING_OPENING_RANGE` state during delay period
- **Terminal Output:** Shows "0 positions - waiting for market to settle | Entry after 10:00 AM"
- **Config:** `fresh_entry_delay_minutes: 30` (default) - adjustable
- **Does NOT apply to:** Re-entries after ITM close (when we already have longs)

### Market Conditions
- **No FOMC days:** Avoid high volatility events
- **Regular trading hours:** Enter during normal market hours (after opening range, 10:00 AM - 4:00 PM ET)
- **No early close days:** Avoid half-days before holidays

---

## Position Management

### 1. Rolling Weekly Shorts (The Core Income Mechanism)

#### When to Roll
- **Scheduled Roll:** Every Friday (or Thursday if Friday is holiday)
- **Challenged Roll:** When price consumes >= 75% of the original cushion to a short strike (Updated 2026-01-28, adaptive)
- **Emergency Close:** When price is within 0.1% of ITM (close shorts, keep longs — absolute safety floor, stays static)

#### Roll Mechanics: BOTH LEGS, NOT SINGLE LEG

**CRITICAL FINDING:** Based on options trading best practices and Brian Terry's approach:

**The bot rolls BOTH legs of the strangle simultaneously**, not just the challenged leg. Here's why:

1. **Recentering Logic:** When price moves, you recenter the strangle around the CURRENT price
   - Old strangle: Put $678 / Call $697 (centered at ~$687)
   - SPY moves to $695
   - New strangle: Put $677 / Call $712 (centered at ~$695)
   - This moves the challenged call FURTHER away ($697 → $712 = +$15)
   - This moves the unchallenged put CLOSER ($678 → $677 = -$1)

2. **Credit Collection:** Moving the unchallenged leg closer allows collecting more premium
   - Far OTM put at $678: $0.25 credit
   - Closer put at $677: $1.68 credit
   - This extra premium offsets the cost to close the challenged call

3. **Maintains Symmetry:** Both legs equidistant from current price maintains delta neutrality

**Single Leg Roll Would:**
- ❌ Leave strangle off-center (asymmetric risk)
- ❌ Collect less premium (other leg stays far OTM)
- ❌ Increase debit risk (expensive to close challenged leg with no offsetting credit)

#### The "Never Roll for a Debit" Rule

**Definition:**
- **Net Credit** = (New Shorts Premium) - (Cost to Close Old Shorts)
- **Never roll if Net Credit ≤ 0** (would be paying to extend the trade)

**Calculation:**
```
Cost to Close Old Shorts:
  = (Old Call Ask Price + Old Put Ask Price) × 100 × Position Size

New Shorts Premium:
  = (New Call Bid Price + New Put Bid Price) × 100 × Position Size

Net Credit = New Shorts Premium - Cost to Close Old Shorts
```

**Example from 2026-01-27:**
```
Old Shorts:
  - Call $697: Ask = $2.74
  - Put $678: Ask = $0.64
  - Cost to close = ($2.74 + $0.64) × 100 = $338

New Shorts (recentered at $695):
  - Call $712: Bid = $0.45
  - Put $677: Bid = $1.68
  - Premium collected = ($0.45 + $1.68) × 100 = $213

Net Credit = $213 - $338 = -$125 (DEBIT!)

RESULT: Roll REJECTED → Close entire position
```

#### What Happens When Roll is Rejected

**Per Brian Terry:** If you can't roll for credit, close the entire position and start fresh.

**Rationale:**
1. The trade setup is broken (can't collect income anymore)
2. Better to take profits on longs and exit than bleed theta
3. Longs alone cost $159/week in theta - not sustainable without short premium
4. Market conditions likely changed (price moved too fast, vol collapsed, etc.)

**Actions:**
1. Close short strangle (take the loss on challenged leg)
2. Close long straddle (take profit if price moved favorably)
3. Calculate net P&L for the cycle
4. Return to IDLE state
5. Wait for next good entry (VIX < 18, favorable conditions)

---

### 2. Recentering the Long Straddle (5-Point Rule)

#### When to Recenter
- **Trigger:** SPY moves ±$5 from initial long straddle strike
- **Example:**
  - Initial strike: $690
  - SPY moves to $695
  - Recenter triggered

#### Recenter Mechanics

**CRITICAL:** Keep the SAME expiration date when recentering.

**Process:**
1. **Close old long straddle:**
   - Sell long call at old strike
   - Sell long put at old strike
   - Calculate P&L (likely slightly negative due to theta)

2. **Open new long straddle:**
   - Buy new long call at NEW ATM strike ($695)
   - Buy new long put at NEW ATM strike ($695)
   - **Same expiration as old longs!**

3. **Update tracking:**
   - New initial strike: $695
   - Reset 5-point recenter threshold (next trigger at $690 or $700)

**Why Same Expiration:**
- Maintains DTE countdown to 60 DTE exit threshold
- If you reset to 120 DTE, you'd never reach the 60 DTE exit
- Example: If old longs had 85 DTE, new longs also have 85 DTE

**Recenter P&L Impact:**
- Usually slightly negative due to theta decay
- Example from 2026-01-22: -$32 on recenter
- Offset by short premium collected over the cycle

#### Shorts Behavior During Recenter

**Do NOT roll shorts during recenter** unless they're also challenged:
- Recenter only affects LONGS (straddle)
- Shorts (strangle) remain in place until:
  - Friday roll day
  - OR challenged (>= 75% of original cushion consumed)
  - OR emergency (price within 0.1% of ITM)

**Exception:** If recenter happens on Friday roll day, handle both:
1. Recenter longs first
2. Then roll shorts (centered on new price)

---

### 3. ITM Risk Monitoring (Vigilant Mode)

#### Monitoring Thresholds (Updated 2026-01-28 — Adaptive Cushion-Based)

The monitoring system now uses **adaptive thresholds** based on the percentage of the original entry cushion that has been consumed, rather than fixed percentage distances from the strike. This matches the adaptive entry logic (1% NET symmetric scanning) which places shorts closer in low-vol and further in high-vol environments.

| Threshold | Cushion Consumed | Action | Alert Priority |
|-----------|------------------|--------|----------------|
| **Normal Zone** | < 60% consumed | Normal monitoring (10s intervals) | None |
| **Vigilant Zone** | 60% - 75% consumed | Heightened monitoring (1s intervals) | HIGH |
| **Challenged Roll** | >= 75% consumed | Immediate roll attempt | HIGH |
| **Danger Zone** | 0.1% from strike (static) | Emergency close shorts | CRITICAL |

**How Cushion Is Calculated:**
- `entry_underlying_price`: SPY price when shorts were placed (stored on `StranglePosition`)
- `original_distance`: Strike - entry price (for calls) or entry price - strike (for puts)
- `current_distance`: Strike - current price (for calls) or current price - strike (for puts)
- `cushion_consumed`: `1 - (current_distance / original_distance)`

**Example (Low Vol vs High Vol):**
- Low vol: Shorts at 1.2x EM, $7 from price → triggers at $1.75 remaining ($5.25 consumed = 75%)
- High vol: Shorts at 2.0x EM, $20 from price → triggers at $5.00 remaining ($15 consumed = 75%)
- The trigger naturally scales with the original placement distance

**Why 75% for Roll Trigger:**
- Roll = 4 legs of fees, Hard exit (Path A) = 8 legs of fees
- At 75%, the short is still OTM with decent extrinsic value → credit roll is likely to succeed
- Waiting longer (80-85%) risks credit failure → triggers the expensive 8-leg hard exit
- Falls back to static 0.5% threshold for legacy positions without `entry_underlying_price`

**Key Changes History:**
- (2026-01-28): Replaced static 0.5% with adaptive cushion-based trigger (75% consumed = roll, 60% consumed = vigilant entry). Added immediate next-week shorts entry after scheduled debit skip.
- (2026-01-27): Challenged roll threshold widened from 0.3% to 0.5%

#### Vigilant Mode Entry/Exit

**Entry (HIGH Alert):**
```
Cushion Consumed = 1 - (Current Distance / Original Distance)
If Cushion Consumed >= 60%:
  - Enter VIGILANT mode (1-second monitoring)
  - Alert: "X% cushion consumed ($Y remaining). Roll triggers at 75%."
```

**Exit (LOW Alert):**
```
If Cushion Consumed < 60%:
  - Exit VIGILANT mode
  - Resume normal 10-second intervals
  - Alert: "Moved away from strike - returned to safe zone"
```

#### Emergency Close (0.1% Threshold)

When price breaches 0.1% from strike:
1. **Immediately close threatened shorts** (use aggressive pricing)
2. Send CRITICAL alert: "ITM_RISK_CLOSE"
3. Assess if roll is possible
4. If roll rejected (debit), close entire position

---

## Exit Conditions

### 1. Longs Reach 60 DTE (Normal Exit)

When long straddle DTE ≤ 60:
1. Close short strangle
2. Close long straddle
3. Calculate total cycle P&L
4. Send alert with metrics
5. Return to IDLE
6. Wait for next entry opportunity

### 2. Challenged Roll Failed (Hard Exit)

When shorts challenged AND roll would be debit:
1. Close short strangle (accept loss on challenged leg)
2. Close long straddle (collect any profits from directional move)
3. Calculate total cycle P&L
4. Send CRITICAL alert: "HARD_EXIT_CHALLENGED_ROLL_FAILED"
5. Return to IDLE

**Why this happens:**
- Price moved too fast (challenged leg expensive to close)
- New strikes too far OTM (not enough premium)
- Vol collapsed (options premium too low)
- Net result: Can't collect credit to justify roll

### 3. Emergency Exit (5%+ Move)

If SPY moves ±5% from initial strike in a single day:
1. Emergency close all positions (use aggressive pricing)
2. Send CRITICAL alert
3. Market conditions abnormal - protect capital

### 4. VIX Defensive Mode (VIX ≥ 25)

If VIX spikes ≥ 25 while positions open:
- **Stop selling new shorts** (defensive mode)
- Keep existing longs (benefit from vol expansion)
- Allow existing shorts to expire or close when profitable
- Don't enter new cycle until VIX < 18

---

## P&L Expectations

### Target Returns
- **Weekly NET return:** 1% of long straddle cost
- **Example:** Long straddle cost $4,757 → Target $47.57 NET per week
- **Calculation:** NET = Gross Premium - Weekly Theta - Fees

### Typical Cycle P&L Breakdown

**Example Cycle (2026-01-22 to 2026-01-27):**

| Component | P&L | Notes |
|-----------|-----|-------|
| **Long Call** | +$262 | SPY moved up (+5 points) |
| **Long Put** | -$222 | SPY moved up |
| **Longs Net** | +$40 | Directional offset |
| **Short Call** | -$110 | Challenged (took loss) |
| **Short Put** | +$209 | Expired far OTM |
| **Shorts Net** | +$99 | Income generation |
| **Recenter** | -$32 | One 5-point recenter |
| **TOTAL** | **+$107** | ~2.2% of straddle cost |

**Key Observations:**
- Longs nearly break even (as designed)
- Shorts provide income despite challenged call
- One bad leg doesn't doom the cycle
- Even with challenged roll exit, profit captured

---

## Risk Management

### Circuit Breaker Rules

**Failure Tracking:**
- Track failures by type: entry, exit, roll, recenter
- 5 consecutive failures OR 5-of-10 sliding window → HALT
- Manual intervention required to reset

**Cooldown Periods:**
- After partial fill: 30-minute cooldown before retry
- After roll failure: 60-minute cooldown
- After emergency exit: 120-minute cooldown

### Position Limits
- **Max position size:** 1 contract per leg (for $50K account)
- **Max margin used:** ~$15K for long straddle + $3K for shorts = $18K
- **Remaining capital:** $32K buffer for safety
- **Order size limits (ORDER-006):** Max 10 contracts per order, 20 per underlying

### Slippage Monitoring (ORDER-007)
- **Warning threshold:** 5% slippage from expected price (configurable)
- **Critical threshold:** 15% slippage from expected price (configurable)
- **Behavior:** Logs HIGH/CRITICAL alerts when fills deviate significantly

### Emergency Close Safety (ORDER-008)
- **Max retry attempts:** 5 attempts with escalating alerts
- **Retry delay:** 5 seconds between attempts
- **Spread normalization:** Wait up to 30 seconds for spread < 50%
- **Max normalization attempts:** 3 attempts before proceeding

### Key Safety Rules

1. **Never naked shorts:** Always have long straddle as hedge
2. **Never let shorts expire ITM:** Close or roll before expiration
3. **Never roll for debit:** Exit if can't collect credit
4. **Never skip VIX filter:** Only enter when VIX < 18
5. **Never hold longs past 60 DTE:** Exit to avoid gamma risk
6. **Never place oversized orders:** ORDER-006 validates before placement

---

## Technical Implementation Notes

### Order Execution
- **Longs:** Place as individual limit orders (2 legs)
- **Shorts:** Place as individual limit orders (2 legs)
- **Saxo requirement:** No multi-leg orders in LIVE (use combo orders in SIM only)

### Progressive Slippage on Close
When closing shorts, use progressive slippage:
1. Try at 0% slippage (bid/ask)
2. If timeout, try at 5% slippage
3. If timeout, try at 10% slippage
4. If still timeout, use MARKET order (emergency only)

### Greeks Tracking
- **Delta:** Track total portfolio delta (should be near zero)
- **Theta:** Track daily theta cost (longs lose value daily)
- **Vega:** Track vol exposure (longs benefit from vol spike)

---

## Common Scenarios & Actions

### Scenario 1: Scheduled Friday Roll (No Challenge)

**Conditions:**
- It's Friday (or Thursday if Friday is holiday)
- Shorts are 0-3 DTE
- No strikes challenged

**Actions:**
1. Calculate cost to close current shorts
2. Get quotes for next week's shorts (recentered at current price)
3. Verify net credit > 0
4. Close old shorts
5. Open new shorts
6. Log roll metrics

---

### Scenario 2: Challenged Call (Price Moved Up)

**Conditions:**
- SPY moved up significantly
- Short call cushion >= 75% consumed (adaptive trigger)
- Shorts still have 5+ DTE remaining

**Actions:**
1. Enter VIGILANT mode (1s monitoring)
2. Send HIGH alert: "Short call CHALLENGED"
3. Attempt to roll shorts:
   - Calculate close cost (call will be expensive!)
   - Get quotes for new shorts (recentered higher)
   - Check net credit calculation
4. **If net credit > 0:** Execute roll (recenter strangle higher)
5. **If net credit ≤ 0:** Reject roll, close everything (hard exit)

**Why challenged rolls often fail:**
- Challenged leg is expensive to close (near ATM)
- New strikes far OTM (low premium collected)
- Net = Low premium - High cost = Debit
- Can't justify paying to extend a losing trade

---

### Scenario 3: Price Moved 5 Points (Recenter)

**Conditions:**
- SPY moved from $690 → $695
- Long straddle strikes at $690 (now OTM)

**Actions:**
1. Close long call $690 (sell for profit - now ITM)
2. Close long put $690 (sell for loss - now OTM)
3. Buy new long call $695 (ATM at new price)
4. Buy new long put $695 (ATM at new price)
5. Update initial strike to $695
6. Keep shorts in place (unless also challenged)
7. Log recenter event

**P&L Impact:**
- Usually small loss due to theta decay
- Example: -$32 on recenter
- Offset by income from shorts over time

---

### Scenario 4: Longs Reach 60 DTE (Normal Exit)

**Conditions:**
- Long straddle now has 60 DTE remaining
- Time to exit entire cycle

**Actions:**
1. Check if shorts are near expiration:
   - If shorts have 0-2 DTE, let them expire worthless (if OTM)
   - If shorts have 3+ DTE, close for profit
2. Close long straddle (sell both legs)
3. Calculate total cycle P&L:
   - All short premium collected
   - Long straddle exit value vs. entry cost
   - Any recenter losses
4. Send MEDIUM alert with cycle summary
5. Return to IDLE
6. Wait for next VIX < 18 entry

---

## Metrics & Reporting

### Per-Cycle Tracking
- Total premium collected (all shorts)
- Total straddle cost (all longs, including recenters)
- Realized P&L (closed trades)
- Number of recenters
- Number of rolls
- Win/loss ratio

### Lifetime Statistics (Persist Across Cycles)
- Total trade count
- Winning trades / Losing trades
- Best trade P&L / Worst trade P&L
- Peak P&L / Max drawdown
- Overall win rate

### Daily Tracking (Reset Each Day)
- SPY open/high/low
- VIX high / VIX average
- Daily P&L change
- Daily recenter/roll count

---

## Sources & References

### Strategy Source
- **Brian Terry's Delta Neutral Strategy:** [Theta Profits Article](https://www.thetaprofits.com/the-delta-neutral-options-strategy-for-income-in-any-market/)
- Video interview with detailed management rules (embedded in article)

### Rolling Options Best Practices
- **Rolling for Credit:** [Options Trading IQ - Rolling Options for Credit](https://optionstradingiq.com/rolling-options-for-a-credit/)
- **Roll vs. Close:** [Options Trading IQ - Rolling Options Complete Guide](https://optionstradingiq.com/rolling-options/)
- **Option Alpha - Rolling Options:** [Beginner's Guide to Rolling Options](https://optionalpha.com/learn/rolling-options)

### Strangle Management
- **Short Strangle Guide:** [Option Alpha - Short Strangle](https://optionalpha.com/strategies/short-strangle)
- **Delta Neutral SPX Trading:** [Environmental Trading Edge](https://www.environmentaltradingedge.com/trading-education/delta-neutral-spx-options-trading-a-comprehensive-guide)

### Premium Collection & Closing Rules
- **50% Rule:** [Best Stock Strategy - Closing Options Trades](https://beststockstrategy.com/closing-options-trades/)
- **Data Driven Options - Rolling Losing Positions:** [Rolling Losing Option Positions](https://datadrivenoptions.com/rolling-losing-option-positions/)

---

## Key Lessons Learned (2026-01-27)

### Issue 1: Metrics Not Resetting Between Cycles

**Problem:**
- Cumulative metrics (`total_premium_collected`, `total_straddle_cost`, `realized_pnl`) persisted across cycles
- Example: Bot showed $257 P&L but Saxo showed $138 actual
- Caused by metrics accumulating from previous cycles without reset

**Solution:**
- Added `reset_cycle_metrics()` method to StrategyMetrics
- Called after `exit_all_positions()` completes
- Resets cycle-specific metrics to zero
- Preserves lifetime statistics

**Impact:**
- Future cycles will show accurate per-cycle P&L
- Matches Saxo Trader actual P&L
- Lifetime stats still tracked separately

### Issue 2: Import Bug Crashed Bot After Exit

**Problem:**
- `TypeError: 'module' object is not callable` at line 1948
- Tried to call `time(9, 30)` but `time` was the module, not the class
- Crashed after exiting all positions when checking market open delay

**Solution:**
- Changed: `from datetime import time as dt_time`
- Updated: `market_open = dt_time(9, 30)`
- Prevents collision between `time` module and `time` class

**Impact:**
- Bot can now transition from exit back to checking for new entry
- No more crashes when returning to IDLE state

### Issue 3: Strike Selection Violated Config Constraints

**Problem:**
- Config specified `weekly_strangle_multiplier_min: 1.5`
- Code used `MIN_MULT_ATTEMPTS = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5]`
- Bot selected call at 1.4x expected move (below 1.5x minimum!)
- Resulted in challenged roll at only 0.29% price move
- Roll failed for debit (-$125), position exited prematurely

**Example from 2026-01-27:**
- Expected move: $5.00 (0.72% of SPY $690)
- Call strike: $697 (1.4x EM) - TOO CLOSE
- Put strike: $678 (2.4x EM) - TOO FAR
- Asymmetric: 1.4x vs 2.4x violates delta neutrality
- Price moved $5 to $695 → Call challenged at 0.29%

**Solution:**
- Changed `MIN_MULT_ATTEMPTS` to use config value: `[1.5]`
- Added symmetry constraint: Both legs within 0.3x of each other
- No fallback to lower multipliers - if can't hit 1.5x minimum, skip entry

**Impact:**
- Future shorts will be properly placed at 1.5x-2.0x expected move
- Symmetric strikes maintain delta neutrality
- Adaptive roll trigger (75% cushion consumed) more likely to succeed for credit

### Issue 4: Challenged Roll Threshold Too Tight

**Problem:**
- Challenged roll triggered at 0.3% from strike
- With 1.4x strikes, this was too late to roll for credit
- Challenged leg expensive to close, new leg cheap to open = debit

**Analysis:**
- At 0.3% away (with 1.5x strikes): Roll costs -$80 debit
- At 0.5% away (with 1.5x strikes): Roll yields +$30 credit
- At 0.5% away (with 2.0x strikes): Roll yields +$110 credit

**Solution (2026-01-27):**
- Updated challenged roll threshold from 0.3% to 0.5%
- Updated vigilant monitoring threshold from 0.3% to 0.5%
- Kept emergency close threshold at 0.1% (already correct)

**Further Improvement (2026-01-28) — Adaptive Cushion Trigger:**
- Replaced static 0.5% with adaptive 75% cushion-consumed trigger
- The entry is adaptive (1% NET symmetric scanning places shorts closer in low-vol, further in high-vol)
- The roll trigger now adapts the same way: triggers when 75% of original cushion is consumed
- Low vol ($7 cushion): triggers at $1.75 remaining. High vol ($20 cushion): triggers at $5.00 remaining
- 60% consumed → vigilant mode (1s checks), 75% consumed → challenged roll trigger
- Falls back to static 0.5% for legacy positions without entry_underlying_price

**Impact:**
- Roll trigger naturally scales with market conditions (matches adaptive entry logic)
- Credit rolls more likely to succeed because triggered at optimal distance
- Avoids 8-leg hard exit (2x fees) by triggering early enough for credit
- Emergency at 0.1% stays as absolute safety floor (about execution speed, not market conditions)

---

**Last Research Date:** 2026-01-27
**Last Updated:** 2026-02-01 (Enhanced safety features: ORDER-006, ORDER-007, ORDER-008)
**Researcher:** AI Assistant via Web Search + Trade Analysis
**Implementation Status:** Active in LIVE trading (CALYPSO Delta Neutral bot v2.0.4)
