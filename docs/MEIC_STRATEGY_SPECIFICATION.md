# MEIC (Multiple Entry Iron Condors) Strategy Specification

**Last Updated:** 2026-01-27
**Purpose:** Complete implementation specification for the MEIC 0DTE trading bot
**Strategy Creator:** Tammy Chambless (the "Queen of 0DTE")
**Status:** Research Complete - Ready for Implementation

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Strategy Overview](#strategy-overview)
3. [Entry Rules](#entry-rules)
4. [Strike Selection](#strike-selection)
5. [Stop Loss Rules](#stop-loss-rules)
6. [Exit Rules](#exit-rules)
7. [Risk Management](#risk-management)
8. [MEIC+ Modification](#meic-modification)
9. [Implementation Details](#implementation-details)
10. [Position Registry Integration](#position-registry-integration)
11. [Config Specification](#config-specification)
12. [Testing Strategy](#testing-strategy)
13. [Sources](#sources)

---

## Executive Summary

### What is MEIC?

MEIC (Multiple Entry Iron Condors) is a 0DTE SPX options strategy developed by Tammy Chambless. Unlike single-entry iron condors, MEIC involves **entering multiple condor positions throughout the trading day**, which:

- Smooths results by averaging entry prices
- Reduces volatility exposure
- Avoids the danger of relying on a single entry point

### Key Performance Metrics

| Metric | Value | Source |
|--------|-------|--------|
| Compound Annual Return | **20.7%** | Tammy Chambless (Jan 2023 - present) |
| Max Drawdown | **4.31%** | Tammy Chambless |
| Calmar Ratio | **4.8** | Calculated (20.7% / 4.31%) |
| Win Rate | ~70% | Community backtests |
| Risk Rating | 3.5/10 | Theta Profits |

### Why MEIC is Ideal for Automation

1. **Mechanical rules** - Pre-defined entry times, no discretion needed
2. **Multiple entries** - Smooths the impact of timing mistakes
3. **Clear stop losses** - Per-side stops equal to total credit received
4. **0DTE expiration** - All positions close same day (no overnight risk)
5. **SPX cash-settled** - No assignment risk, European-style options

---

## Strategy Overview

### Core Concept

Enter **6 iron condors throughout the trading day**, each with:
- OTM call spread (sell call, buy higher call)
- OTM put spread (sell put, buy lower put)
- Equal credit collected on both sides
- Per-side stop losses to protect against directional moves

### Iron Condor Structure

```
                 Call Spread Side
    ┌─────────────────────────────────────────┐
    │  Buy Long Call (protection)             │
    │      ▲                                  │
    │      │ 50-60 points width               │
    │      ▼                                  │
    │  Sell Short Call (credit)               │
    └─────────────────────────────────────────┘
                      │
                      │ Out of the Money
                      │ (~5-15 delta)
                      ▼
    ═══════════════ SPX PRICE ════════════════
                      ▲
                      │ Out of the Money
                      │ (~5-15 delta)
                      │
    ┌─────────────────────────────────────────┐
    │  Sell Short Put (credit)                │
    │      ▲                                  │
    │      │ 50-60 points width               │
    │      ▼                                  │
    │  Buy Long Put (protection)              │
    └─────────────────────────────────────────┘
                  Put Spread Side
```

### Daily Structure

| Entry # | Time (ET) | Notes |
|---------|-----------|-------|
| Entry 1 | 10:00 AM | After opening volatility settles |
| Entry 2 | 10:30 AM | 30-minute spacing |
| Entry 3 | 11:00 AM | Mid-morning |
| Entry 4 | 11:30 AM | Before lunch |
| Entry 5 | 12:00 PM | Noon entry |
| Entry 6 | 12:30 PM | Final entry |

**Note:** Entry times are spaced 30-60 minutes apart. The exact schedule above is based on industry best practices (wait 30-90 minutes after market open, complete entries by early afternoon). Tammy's specific times may differ but are only available in her paid course.

---

## Entry Rules

### Pre-Entry Conditions

Before each scheduled entry, verify:

| Condition | Requirement | Why |
|-----------|-------------|-----|
| Market Hours | 9:30 AM - 4:00 PM ET | SPX trading hours |
| Not FOMC Day | Skip FOMC announcement days | High volatility risk |
| SPX Price Available | Valid quote from Saxo | Can't calculate strikes |
| No Circuit Breaker | Bot not in error state | Safety check |

### Entry Timing Rules

1. **Wait 30 minutes after market open** (first entry at 10:00 AM)
   - Avoid opening gap volatility
   - Let overnight order flow settle
   - More stable strike selection

2. **Space entries 30-60 minutes apart**
   - Diversifies entry prices
   - Smooths daily P&L curve
   - Reduces single-entry risk

3. **Complete all entries by 12:30 PM**
   - Sufficient time for theta decay
   - Avoids late-day gamma risk
   - Positions have time to hit stops if needed

### Entry Execution Order

For each iron condor entry:

1. **Calculate strikes** based on current SPX price
2. **Place call spread** (sell short call, buy long call)
3. **Place put spread** (sell short put, buy long put)
4. **Register all 4 positions** in Position Registry
5. **Set stop losses** on each side (call side + put side)
6. **Log entry** to Google Sheets

**Order Placement:** Place legs individually (safer) or as multi-leg order if supported.

---

## Strike Selection

### Delta-Based Selection

| Leg | Delta Target | Notes |
|-----|--------------|-------|
| Short Call | 5-15 delta | OTM, high probability |
| Long Call | Further OTM | Protection wing |
| Short Put | 5-15 delta | OTM, high probability |
| Long Put | Further OTM | Protection wing |

### Spread Width Rules

| Parameter | Value | Notes |
|-----------|-------|-------|
| Standard Width | 50-60 points | Average spread width |
| Maximum Width | 100 points | Never exceed |
| Minimum Width | 25 points | Some traders use tighter |

### Credit Targets

| Parameter | Value | Notes |
|-----------|-------|-------|
| Credit per Side | $1.00 - $1.75 | Per call spread AND per put spread |
| Total Credit | $2.00 - $3.50 | Combined for full iron condor |
| Balance Rule | Equal credits | Call side ≈ Put side credit |

### Strike Selection Algorithm

```python
def select_strikes(spx_price: float, spread_width: int = 50) -> dict:
    """
    Select iron condor strikes based on current SPX price.

    Args:
        spx_price: Current SPX index level
        spread_width: Distance between short and long strikes (default 50)

    Returns:
        dict with all 4 strikes
    """
    # Round SPX price to nearest 5 (SPX strikes are in 5-point increments)
    rounded_price = round(spx_price / 5) * 5

    # Find strikes that give ~5-15 delta (typically 30-60 points OTM)
    # This should be calibrated based on current VIX/IV
    otm_distance = 40  # Start with 40 points OTM

    # Call side (above current price)
    short_call = rounded_price + otm_distance
    long_call = short_call + spread_width

    # Put side (below current price)
    short_put = rounded_price - otm_distance
    long_put = short_put - spread_width

    return {
        "short_call": short_call,
        "long_call": long_call,
        "short_put": short_put,
        "long_put": long_put,
        "spread_width": spread_width
    }
```

### Practical Example

SPX at 6000:

| Leg | Strike | Delta | Position |
|-----|--------|-------|----------|
| Long Call | 6090 | ~2 | Buy 1 |
| Short Call | 6040 | ~8 | Sell 1 |
| Short Put | 5960 | ~8 | Sell 1 |
| Long Put | 5910 | ~2 | Buy 1 |

Credit received: ~$1.25 per side = $2.50 total

---

## Stop Loss Rules

### Core Stop Loss Principle

**Stop loss on each side = Total credit received for the FULL iron condor**

This is the key insight that makes MEIC a "breakeven" strategy:
- If one side gets stopped and the other expires worthless, you break even
- The losing side costs exactly what the winning side gains

### Stop Loss Calculation

| Scenario | Credit Collected | Stop Loss Per Side |
|----------|------------------|-------------------|
| Example 1 | $1.00 call + $1.00 put = $2.00 | $2.00 per side |
| Example 2 | $1.50 call + $1.50 put = $3.00 | $3.00 per side |
| Example 3 | $1.25 call + $1.50 put = $2.75 | $2.75 per side |

### Stop Loss Implementation

```python
def calculate_stop_loss(credit_call_side: float, credit_put_side: float,
                         meic_plus: bool = True) -> dict:
    """
    Calculate stop loss prices for each side.

    Standard MEIC: Stop = Total credit
    MEIC+: Stop = Total credit - $0.10 (turns breakeven days into small wins)
    """
    total_credit = credit_call_side + credit_put_side

    if meic_plus:
        # MEIC+ modification: reduce stop by $0.10
        stop_loss = total_credit - 0.10
    else:
        stop_loss = total_credit

    return {
        "call_side_stop": stop_loss,
        "put_side_stop": stop_loss,
        "total_credit": total_credit
    }
```

### Stop Order Types

| Order Type | Pros | Cons |
|------------|------|------|
| **Stop Market** (Recommended) | Guaranteed fill | May have slippage |
| Stop Limit | Price protection | May not fill |
| Mental Stop | Flexible | Human error risk |

**MEIC uses Stop Market orders** - guaranteed fills are more important than slippage protection.

### What Triggers a Stop

**Call Side Stop:** Triggered when the value of the call spread rises to the stop level
- This happens when SPX moves UP toward the short call strike
- The call spread becomes more expensive (losing money)

**Put Side Stop:** Triggered when the value of the put spread rises to the stop level
- This happens when SPX moves DOWN toward the short put strike
- The put spread becomes more expensive (losing money)

---

## Exit Rules

### Normal Exit Scenarios

| Exit Trigger | Action | Expected Outcome |
|--------------|--------|------------------|
| Position expires worthless | No action needed | Collect full credit |
| One side stopped | Stop triggers market close | Breakeven or small loss |
| Both sides stopped | Both stops trigger | Max loss (rare, ~6% of days) |

### Take Profit Rule (Optional)

Some traders add a take profit at 50% of max profit:
- Close position when spread value drops to $0.05
- Locks in profit before potential reversal
- **Not part of standard MEIC** - Tammy holds until expiration or stop

### End of Day Rules

| Time | Action |
|------|--------|
| 3:55 PM ET | Monitor all open positions closely |
| 4:00 PM ET | SPX 0DTE options expire (cash-settled) |

**Cash Settlement:** Unlike equity options, SPX options are cash-settled. No assignment risk.

---

## Risk Management

### Position Sizing

| Account Size | Recommended Allocation | Max Daily Risk |
|--------------|------------------------|----------------|
| $30,000 | Minimum for MEIC | 1-2% = $300-600 |
| $50,000 | Our allocation | 1-2% = $500-1000 |
| $100,000+ | Full implementation | 1-2% = $1000-2000 |

### Daily Risk Limits

| Rule | Value | Implementation |
|------|-------|----------------|
| Max Daily Loss | 2% of account | Circuit breaker at $1,000 (for $50K) |
| Max Positions at Risk | 3-4 ICs | Limit simultaneous double-stop exposure |
| Max Buying Power Usage | 50% | Never use more than half of BP |

### Worst Case Scenario Analysis

For 1 iron condor with $50 wide spreads and $2.50 credit:

| Scenario | Probability | P&L |
|----------|-------------|-----|
| Both sides expire worthless | ~60% | +$250 |
| One side stopped, one expires | ~34% | ~$0 (breakeven) |
| Both sides stopped | ~6% | -$250 to -$750 |

### Double Stop Loss Days

Historical occurrence: **6-10% of trading days**

When this happens:
1. Accept the loss (it's defined risk)
2. Do NOT double down or revenge trade
3. Review if market conditions warranted trading
4. Continue with normal strategy next day

---

## MEIC+ Modification

### What is MEIC+?

A small but important modification to standard MEIC:
- **Turns ~30% of breakeven days into small winners**
- Reduces stop loss by $0.10 from the standard calculation

### MEIC+ Stop Loss Calculation

| Standard MEIC | MEIC+ |
|---------------|-------|
| Stop = Total Credit | Stop = Total Credit - $0.10 |
| $2.00 credit → $2.00 stop | $2.00 credit → $1.90 stop |
| $3.00 credit → $3.00 stop | $3.00 credit → $2.90 stop |

### Why MEIC+ Works

When one side gets stopped and the other expires worthless:
- **Standard MEIC:** Loss exactly equals win = $0 (breakeven)
- **MEIC+:** Loss is $0.10 less than win = +$10 (small profit)

### Implementation Decision

**Recommendation:** Start with Standard MEIC, then switch to MEIC+ after validation.

```python
# Config option
MEIC_PLUS_ENABLED = True  # Set to True for MEIC+ modification
MEIC_PLUS_REDUCTION = 0.10  # $0.10 reduction from standard stop
```

---

## Implementation Details

### Code Reuse from Iron Fly

| Component | Reuse Level | Notes |
|-----------|-------------|-------|
| SaxoClient | 100% | Same API client |
| Position Registry | 100% | New shared module |
| AlertService | 100% | Same alert system |
| TradeLoggerService | 100% | Same logging |
| market_hours.py | 100% | Same market hours |
| Strike calculation | ~50% | Different logic for IC vs IF |
| Stop loss logic | ~30% | Per-side vs wing-based |
| Entry scheduling | NEW | 6 scheduled entries |
| State machine | ~60% | More states for multi-entry |

### New Components Required

1. **Entry Scheduler** - Execute 6 entries at specific times
2. **Multi-Position Tracker** - Track 6 ICs (24 positions) per day
3. **Per-Side Stop Loss Monitor** - Separate stops for call/put sides
4. **Aggregate P&L Tracking** - Sum of all 6 ICs

### State Machine

```
┌──────────────────────────────────────────────────────────────┐
│                       MEIC States                            │
└──────────────────────────────────────────────────────────────┘

IDLE → WAITING_FIRST_ENTRY → ENTRY_1_ACTIVE → WAITING_ENTRY_2 →
ENTRY_2_ACTIVE → WAITING_ENTRY_3 → ENTRY_3_ACTIVE →
WAITING_ENTRY_4 → ENTRY_4_ACTIVE → WAITING_ENTRY_5 →
ENTRY_5_ACTIVE → WAITING_ENTRY_6 → ALL_ENTRIES_ACTIVE →
MONITORING → CLOSING → DAILY_COMPLETE

Special States:
- CIRCUIT_BREAKER: Too many failures
- HALTED: Unrecoverable error
```

---

## Position Registry Integration

### Why Position Registry is Required

MEIC trades SPX 0DTE - the same underlying as Iron Fly. Without Position Registry:
- MEIC would see Iron Fly's positions and vice versa
- Stop losses could trigger on wrong positions
- Position counts would be incorrect

### Registration Flow

```python
# After each leg fills
def on_order_filled(order_id: str, position_id: str, entry_number: int, leg_type: str):
    strategy_id = f"meic_{date.today().strftime('%Y%m%d')}_entry{entry_number}"

    registry.register(
        position_id=position_id,
        bot_name="MEIC",
        strategy_id=strategy_id,
        metadata={
            "entry_number": entry_number,
            "leg_type": leg_type,  # "short_call", "long_call", "short_put", "long_put"
            "strike": strike,
            "credit": credit_received
        }
    )
```

### Position Filtering

```python
# Get only MEIC positions
def get_my_positions(self):
    all_positions = self.client.get_positions()
    my_position_ids = self.registry.get_positions("MEIC")

    return [p for p in all_positions
            if p["PositionBase"]["PositionId"] in my_position_ids]
```

---

## Config Specification

### MEIC Config Template

```json
{
    "bot_name": "MEIC",
    "saxo_api": {
        "environment": "live",
        "app_key": "FROM_SECRET_MANAGER",
        "app_secret": "FROM_SECRET_MANAGER",
        "redirect_uri": "http://localhost:8000/callback"
    },
    "strategy": {
        "underlying_symbol": "SPX",
        "underlying_uic": 4913,
        "option_root_uic": 128,
        "vix_spot_uic": 10606,

        "entry_times": ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30"],
        "entry_window_minutes": 5,

        "spread_width": 50,
        "min_spread_width": 25,
        "max_spread_width": 100,

        "min_credit_per_side": 1.00,
        "max_credit_per_side": 1.75,
        "credit_balance_tolerance": 0.25,

        "target_delta": 8,
        "min_delta": 5,
        "max_delta": 15,

        "meic_plus_enabled": true,
        "meic_plus_reduction": 0.10,

        "max_daily_loss_percent": 2.0,
        "max_positions_at_risk": 4,
        "contracts_per_entry": 1
    },
    "alerts": {
        "enabled": true,
        "phone_number": "+1XXXXXXXXXX",
        "email": "your@email.com"
    },
    "logging": {
        "log_level": "INFO",
        "google_sheets_enabled": true
    }
}
```

---

## Testing Strategy

### Phase 1: Dry Run Testing (Week 1)

1. Run bot in dry-run mode
2. Verify entry scheduling works (6 entries at correct times)
3. Verify strike selection produces reasonable values
4. Verify stop loss calculations are correct
5. Verify position registry integration

### Phase 2: Paper Trading (Week 2)

1. Run with real market data, simulated fills
2. Verify P&L calculations
3. Test stop loss triggers
4. Test end-of-day expiration handling
5. Monitor for edge cases

### Phase 3: Live Trading (Week 3+)

1. Start with 1 contract per entry
2. Monitor first week closely
3. Verify actual fills vs expected
4. Track slippage on entries and stops
5. Compare to backtested expectations

### Test Cases

| Test | Expected Result |
|------|-----------------|
| Entry at 10:00 AM | First IC opened with correct strikes |
| All 6 entries complete | 6 ICs by 12:30 PM (24 positions total) |
| Stop loss trigger | Single side closes, other side remains |
| Double stop | Both sides close, loss limited |
| Market close | All positions expire/settle |
| FOMC day | No entries (skip day) |
| Early close day | Adjusted entry schedule |

---

## Sources

### Primary Sources

- [Tammy Chambless explains her MEIC strategy for trading 0DTE options](https://www.thetaprofits.com/tammy-chambless-explains-her-meic-strategy-for-trading-0dte-options/) - Theta Profits
- [How 0DTE Breakeven Iron Condor is my most profitable options trading strategy](https://www.thetaprofits.com/my-most-profitable-options-trading-strategy-0dte-breakeven-iron-condor/) - Theta Profits (John Einar Sandvand)
- [What I learned from 16 months with my most profitable options trading strategy](https://www.sandvand.net/2022/08/21/learnings-from-0dte-breakeven-iron-condor/) - John Einar Sandvand
- [Here is a consistently profitable 0DTE Iron Condor strategy](https://www.thetaprofits.com/0dte-iron-condor-a-consistently-profitable-stratey/) - Theta Profits

### Additional Resources

- [M.E.I.C Course](https://academy.optionomega.com/course/meic) - Option Omega Academy (Tammy's paid course)
- [Henry Schwartz's Zero-Day SPX Iron Condor Strategy](https://www.cboe.com/insights/posts/henry-schwartzs-zero-day-spx-iron-condor-strategy-a-deep-dive/) - CBOE
- [Best Time to Get Into a SPX 0-DTE Iron Condor](https://tradersfly.com/blog/best-time-to-get-into-a-spx-0-dte-iron-condor/) - Tradersfly
- [Easy Peasy Iron Condors (EPIC)](https://aeromir.com/001376781/easy-peasy-iron-condors) - Aeromir

### Community Resources

- Quantum Options (Facebook/Discord) - Tammy Chambless's community
- Option Alpha - Backtesting data (25,000+ trades analyzed)
- Trade Automation Toolbox / Trade Steward - Automation tools mentioned by traders

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-01-27 | Claude | Initial strategy specification from web research |
