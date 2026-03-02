# SAXO Bots Safety Measures Guideline

**The Definitive Safety Implementation Guide for CALYPSO Trading Bots**

*Based on comprehensive analysis of the Delta Neutral bot - our most robust and battle-tested implementation*

**Last Updated:** 2026-02-01
**Version:** 1.1

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Safety Architecture Overview](#2-safety-architecture-overview)
3. [Circuit Breaker System](#3-circuit-breaker-system)
4. [Emergency Position Safety](#4-emergency-position-safety)
5. [State/Position Consistency Validation](#5-stateposition-consistency-validation)
6. [Critical Intervention Flag](#6-critical-intervention-flag)
7. [Position Reconciliation](#7-position-reconciliation)
8. [Orphaned Order Detection](#8-orphaned-order-detection)
9. [Position Recovery & Restart Handling](#9-position-recovery--restart-handling)
10. [Partial Fill Handling](#10-partial-fill-handling)
11. [Quote Validation & Freshness](#11-quote-validation--freshness)
12. [WebSocket Health Monitoring](#12-websocket-health-monitoring)
13. [Token Coordination](#13-token-coordination)
14. [Market Hours & Time-Based Safety](#14-market-hours--time-based-safety)
15. [Adaptive Risk Monitoring](#15-adaptive-risk-monitoring)
16. [Flash Crash Detection](#16-flash-crash-detection)
17. [Configuration Validation](#17-configuration-validation)
18. [Logging & Audit Trails](#18-logging--audit-trails)
19. [Graceful Shutdown Handling](#19-graceful-shutdown-handling)
20. [Duplicate Bot Prevention](#20-duplicate-bot-prevention)
21. [Retry Logic & Error Recovery](#21-retry-logic--error-recovery)
22. [Main Loop Safety Patterns](#22-main-loop-safety-patterns)
23. [Alert System Integration](#23-alert-system-integration)
24. [Order Placement Safety](#24-order-placement-safety)
25. [Implementation Checklist](#25-implementation-checklist)
26. [Position Registry for Multi-Bot Isolation](#26-position-registry-for-multi-bot-isolation)

---

## 1. Introduction

### Purpose

This document provides a comprehensive guide to implementing safety measures in CALYPSO trading bots. Every safety feature documented here has been battle-tested in production and represents lessons learned from real trading scenarios.

### Target Audience

- Developers building new trading bots
- AI assistants implementing trading strategies
- Anyone maintaining or extending existing bots

### Core Philosophy

**SAFETY FIRST, ALWAYS.** When in doubt:
1. Close risky positions (especially naked shorts)
2. Halt trading until manually verified
3. Log everything for post-mortem analysis
4. Alert the user immediately

### Reference Implementation

The Delta Neutral bot (`bots/delta_neutral/`) serves as the reference implementation. All code examples and line references come from this bot unless otherwise noted.

---

## 2. Safety Architecture Overview

### Multi-Layer Defense Model

```
┌─────────────────────────────────────────────────────────────────┐
│                     LAYER 1: PREVENTION                         │
│  Config Validation │ Market Hours Check │ VIX Filters          │
│  Opening Range Delay │ Cooldowns │ Duplicate Bot Prevention    │
├─────────────────────────────────────────────────────────────────┤
│                     LAYER 2: DETECTION                          │
│  Circuit Breaker │ State Consistency │ Position Reconciliation │
│  Quote Freshness │ WebSocket Health │ Flash Crash Detection    │
├─────────────────────────────────────────────────────────────────┤
│                     LAYER 3: RESPONSE                           │
│  Emergency Position Check │ Partial Fill Fallbacks │ Retries   │
│  Adaptive Monitoring │ Critical Intervention Flag              │
├─────────────────────────────────────────────────────────────────┤
│                     LAYER 4: RECOVERY                           │
│  Position Recovery │ State Persistence │ Graceful Shutdown     │
│  Token Coordination │ Auto-Reset Circuit Breaker               │
├─────────────────────────────────────────────────────────────────┤
│                     LAYER 5: VISIBILITY                         │
│  Comprehensive Logging │ Google Sheets │ Telegram/Email Alerts │
│  Audit Trails │ Performance Metrics │ Daily Summaries          │
└─────────────────────────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|------|---------|
| `bots/delta_neutral/strategy.py` | Core strategy with safety implementations |
| `bots/delta_neutral/main.py` | Main loop with safety orchestration |
| `shared/saxo_client.py` | API client with circuit breaker |
| `shared/token_coordinator.py` | Multi-bot token management |
| `shared/alert_service.py` | Telegram/Email alerting |
| `shared/market_hours.py` | Market hours validation |
| `shared/logger_service.py` | Comprehensive logging |

---

## 3. Circuit Breaker System

### Overview

The circuit breaker is the **most critical safety mechanism**. It prevents "death loops" where repeated failures cause cascading errors that could drain an account.

### Implementation Requirements

#### 3.1 Consecutive Failure Tracking

```python
# In strategy __init__
self._consecutive_failures: int = 0
self._max_consecutive_failures: int = config.get("circuit_breaker", {}).get("max_consecutive_errors", 5)
self._circuit_breaker_open: bool = False
self._circuit_breaker_reason: str = ""
self._circuit_breaker_opened_at: Optional[datetime] = None
self._last_failure_time: Optional[datetime] = None
```

**Reference:** `strategy.py:246-256`

#### 3.2 Sliding Window Failure Tracking (CONN-002)

Consecutive failures alone miss intermittent errors (e.g., 3 failures, 1 success, 3 failures). Use a sliding window:

```python
# Track last N API calls for pattern detection
self._api_call_history: List[Tuple[datetime, bool]] = []  # (timestamp, success)
self._sliding_window_size: int = 10  # Track last 10 calls
self._sliding_window_threshold: int = 5  # Trigger if 5 of 10 fail

def _record_api_result(self, success: bool) -> None:
    """Record API call result in sliding window."""
    now = datetime.now()
    self._api_call_history.append((now, success))

    # Keep only last N entries
    if len(self._api_call_history) > self._sliding_window_size:
        self._api_call_history = self._api_call_history[-self._sliding_window_size:]

def _get_sliding_window_failures(self) -> int:
    """Count failures in sliding window."""
    return sum(1 for _, success in self._api_call_history if not success)
```

**Reference:** `strategy.py:257-423`

#### 3.3 Failure Increment Logic

```python
def _increment_failure_count(self, reason: str) -> None:
    """Increment failure count and check circuit breaker."""
    self._consecutive_failures += 1
    self._last_failure_time = datetime.now()

    # Record in sliding window
    self._record_api_result(success=False)

    logger.warning(f"Operation failed: {reason}")
    logger.warning(f"Consecutive failures: {self._consecutive_failures}/{self._max_consecutive_failures}")

    # Check consecutive failures (original logic)
    if self._consecutive_failures >= self._max_consecutive_failures:
        self._open_circuit_breaker(reason)
        return

    # Check sliding window (catches intermittent failures)
    recent_failures = self._get_sliding_window_failures()
    if recent_failures >= self._sliding_window_threshold:
        self._open_circuit_breaker(
            f"CONN-002: {recent_failures} failures in last {self._sliding_window_size} API calls"
        )
```

**Reference:** `strategy.py:360-391`

#### 3.4 Opening the Circuit Breaker

**CRITICAL:** Before halting, perform emergency position check:

```python
def _open_circuit_breaker(self, reason: str) -> None:
    """Open circuit breaker to halt all trading."""
    logger.critical("=" * 70)
    logger.critical("CIRCUIT BREAKER TRIGGERED")
    logger.critical("=" * 70)

    # CRITICAL: Check for unsafe positions BEFORE halting
    emergency_actions = self._emergency_position_check()

    self._circuit_breaker_open = True
    self._circuit_breaker_reason = reason
    self._circuit_breaker_opened_at = datetime.now()

    # Log to Google Sheets
    if self.trade_logger:
        self.trade_logger.log_safety_event({
            "event_type": "CIRCUIT_BREAKER_OPEN",
            "severity": "CRITICAL",
            "description": reason,
            "emergency_actions": emergency_actions
        })

    # Send alert
    self.alert_service.circuit_breaker(reason, self._consecutive_failures)
```

**Reference:** `strategy.py:425-493`

#### 3.5 Reset Logic

Reset after successful operation:

```python
def _reset_failure_count(self) -> None:
    """Reset consecutive failure count after success."""
    self._record_api_result(success=True)

    if self._consecutive_failures > 0:
        logger.info(f"Resetting failure count (was {self._consecutive_failures})")
        self._consecutive_failures = 0
```

**Reference:** `strategy.py:393-400`

#### 3.6 Configuration

```json
{
    "circuit_breaker": {
        "max_consecutive_errors": 5,
        "cooldown_minutes": 5,
        "sliding_window_size": 10,
        "sliding_window_failures": 5
    }
}
```

---

## 4. Emergency Position Safety

### Overview

When circuit breaker triggers, the bot MUST analyze positions and close anything unsafe before halting.

### The 6 Scenarios

```python
def _emergency_position_check(self) -> str:
    """Analyze risk exposure and close unsafe positions."""
    actions = []

    # CRITICAL: Sync with Saxo first to get accurate state
    try:
        self.recover_positions()
    except Exception as e:
        actions.append(f"WARNING: Could not sync with Saxo - {e}")

    # Determine position state
    straddle_complete = self.long_straddle and self.long_straddle.is_complete
    strangle_complete = self.short_strangle and self.short_strangle.is_complete
    has_partial_straddle = self.long_straddle and not self.long_straddle.is_complete
    has_partial_strangle = self.short_strangle and not self.short_strangle.is_complete
```

| Scenario | Condition | Action | Risk Level |
|----------|-----------|--------|------------|
| 1 | Partial strangle + Complete straddle | Close naked short only | MEDIUM |
| 2 | Partial straddle + Any shorts | **CLOSE ALL** | CRITICAL |
| 3 | Only shorts, no longs | Close all shorts | HIGH |
| 4 | Complete straddle (with or without strangle) | Keep everything | LOW |
| 5 | Partial straddle only, no shorts | Keep (limited risk) | LOW |
| 6 | No positions | No action needed | NONE |

**Reference:** `strategy.py:495-588`

### Emergency Close Methods

#### Close Naked Short (Partial Strangle)

```python
def _close_partial_strangle_emergency(self) -> bool:
    """Close single naked short leg with MARKET order."""
    naked_leg = self.short_strangle.call if self.short_strangle.call else self.short_strangle.put

    # CRITICAL: Use MARKET order for naked shorts - unlimited risk!
    order_result = self._place_protected_multi_leg_order(
        legs=[leg],
        order_description="EMERGENCY_CLOSE_NAKED_SHORT",
        emergency_mode=True,
        use_market_orders=True  # MARKET order for immediate execution
    )
```

**Reference:** `strategy.py:590-669`

#### Close All Shorts

```python
def _close_short_strangle_emergency(self) -> bool:
    """Close all short positions with MARKET orders."""
    # ... collect legs ...

    # CRITICAL: Use MARKET orders for emergency closing
    order_result = self._place_protected_multi_leg_order(
        legs=legs,
        order_description="EMERGENCY_CLOSE_ALL_SHORTS",
        emergency_mode=True,
        use_market_orders=True
    )
```

**Reference:** `strategy.py:671-821`

#### Close Everything

```python
def _emergency_close_all(self) -> bool:
    """Nuclear option - close ALL positions."""
    success = True

    # Close shorts first (higher risk)
    if self.short_strangle:
        if not self._close_short_strangle_emergency():
            success = False

    # Close longs (lower risk but still close for clean slate)
    if self.long_straddle:
        if not self._close_partial_straddle_emergency():
            success = False

    if success:
        self.state = StrategyState.IDLE
        self.alert_service.emergency_exit(reason="All positions closed")
    else:
        self.alert_service.send_alert(
            alert_type=AlertType.EMERGENCY_EXIT,
            title="EMERGENCY CLOSE FAILED",
            message="Some positions may still be open!",
            priority=AlertPriority.CRITICAL
        )

    return success
```

**Reference:** `strategy.py:823-881`

### Spread Validation Before Emergency Close

**CRITICAL:** During flash crashes or extreme volatility, bid-ask spreads can exceed 50%. Using MARKET orders blindly can cause massive slippage losses. Always check spread conditions before emergency closes.

#### Implementation

```python
# In strategy __init__
self._max_emergency_spread_percent: float = 50.0  # Maximum acceptable spread for emergency
self._spread_normalization_wait_seconds: int = 30  # Wait time for spreads to normalize
self._spread_normalization_max_attempts: int = 3  # Max wait cycles

def _check_spread_for_emergency_close(self, uic: int) -> Tuple[bool, float]:
    """
    Check if spread is acceptable for emergency close.

    During extreme volatility, spreads can be 50%+. MARKET orders
    in these conditions cause massive slippage.

    Returns:
        Tuple of (is_acceptable, spread_percent)
    """
    try:
        quote = self.client.get_quote(uic)
        if not quote:
            # No quote = assume acceptable (must close naked position)
            logger.warning(f"No quote for UIC {uic} - proceeding with emergency close")
            return (True, 0.0)

        bid = quote.get("Quote", {}).get("Bid", 0)
        ask = quote.get("Quote", {}).get("Ask", 0)

        if bid <= 0 or ask <= 0:
            logger.warning(f"Invalid bid/ask for UIC {uic} - proceeding with emergency close")
            return (True, 0.0)

        mid = (bid + ask) / 2
        spread_pct = ((ask - bid) / mid) * 100

        if spread_pct > self._max_emergency_spread_percent:
            logger.critical(
                f"EXTREME SPREAD: {spread_pct:.1f}% for UIC {uic} "
                f"(max: {self._max_emergency_spread_percent}%)"
            )
            return (False, spread_pct)

        return (True, spread_pct)

    except Exception as e:
        logger.error(f"Spread check failed for UIC {uic}: {e}")
        # On error, proceed with close (safety > slippage)
        return (True, 0.0)

def _wait_for_spread_normalization(self, uic: int) -> bool:
    """
    Wait for spread to normalize before emergency close.

    Returns:
        True if spread normalized, False if still extreme after max attempts
    """
    for attempt in range(self._spread_normalization_max_attempts):
        is_acceptable, spread_pct = self._check_spread_for_emergency_close(uic)

        if is_acceptable:
            if attempt > 0:
                logger.info(f"Spread normalized to {spread_pct:.1f}% after {attempt} wait(s)")
            return True

        logger.warning(
            f"Waiting {self._spread_normalization_wait_seconds}s for spread normalization "
            f"(attempt {attempt + 1}/{self._spread_normalization_max_attempts})"
        )

        # Alert user about the delay
        if attempt == 0:
            self.alert_service.send_alert(
                alert_type=AlertType.GAP_WARNING,
                title="EXTREME SPREAD - Delaying Emergency Close",
                message=f"Spread is {spread_pct:.0f}%. Waiting for normalization before closing.",
                priority=AlertPriority.HIGH
            )

        time.sleep(self._spread_normalization_wait_seconds)

    # Max attempts reached - log critical warning
    logger.critical(
        f"Spread still extreme after {self._spread_normalization_max_attempts} attempts. "
        f"Proceeding with emergency close anyway (naked position = unlimited risk)."
    )

    return False  # Indicates spread didn't normalize, but we proceed anyway
```

#### Integration with Emergency Close

```python
def _close_partial_strangle_emergency(self) -> bool:
    """Close single naked short leg with spread-aware MARKET order."""
    naked_leg = self.short_strangle.call if self.short_strangle.call else self.short_strangle.put

    # NEW: Check spread before emergency close
    spread_normalized = self._wait_for_spread_normalization(naked_leg.uic)

    if not spread_normalized:
        # Log that we're closing despite extreme spread
        logger.critical("Closing naked short despite extreme spread - unlimited risk override")

    # Proceed with close (naked short = unlimited risk, must close)
    order_result = self._place_protected_multi_leg_order(
        legs=[naked_leg],
        order_description="EMERGENCY_CLOSE_NAKED_SHORT",
        emergency_mode=True,
        use_market_orders=True
    )

    return order_result.get("filled", False)
```

#### Configuration

```json
{
    "emergency_close": {
        "max_spread_percent": 50.0,
        "spread_normalization_wait_seconds": 30,
        "spread_normalization_max_attempts": 3
    }
}
```

**Key Principle:** Naked short positions have UNLIMITED risk. Even with 50% slippage, closing is better than holding. The spread check adds a brief wait for normalization but will ALWAYS proceed with the close.

### Emergency Close Max Retries

Emergency close operations can fail due to network issues, broker rejects, or API errors. Implement max retries with escalating alerts to prevent infinite loops while ensuring positions get closed.

#### Implementation

```python
# In strategy __init__
self._max_emergency_close_attempts: int = 5
self._emergency_close_retry_delay_seconds: int = 5
self._emergency_close_attempts: Dict[str, int] = {}  # Track attempts per position

def _emergency_close_with_retries(
    self,
    legs: List[Dict],
    description: str,
    use_market_orders: bool = True
) -> bool:
    """
    Attempt emergency close with max retries and escalating alerts.

    Args:
        legs: Position legs to close
        description: Description for logging/alerting
        use_market_orders: Whether to use MARKET orders (default True for emergency)

    Returns:
        True if closed successfully, False if all attempts failed
    """
    position_key = f"{description}_{datetime.now().strftime('%Y%m%d')}"

    for attempt in range(1, self._max_emergency_close_attempts + 1):
        logger.info(
            f"Emergency close attempt {attempt}/{self._max_emergency_close_attempts}: {description}"
        )

        try:
            order_result = self._place_protected_multi_leg_order(
                legs=legs,
                order_description=f"{description}_attempt_{attempt}",
                emergency_mode=True,
                use_market_orders=use_market_orders
            )

            if order_result.get("filled"):
                logger.info(f"Emergency close succeeded on attempt {attempt}")

                # Clear attempt counter on success
                if position_key in self._emergency_close_attempts:
                    del self._emergency_close_attempts[position_key]

                return True

            # Log failure reason
            error = order_result.get("error", "Unknown error")
            logger.warning(f"Emergency close attempt {attempt} failed: {error}")

        except Exception as e:
            logger.error(f"Emergency close attempt {attempt} exception: {e}")

        # Track attempts
        self._emergency_close_attempts[position_key] = attempt

        # Escalating alerts based on attempt number
        if attempt == 2:
            self.alert_service.send_alert(
                alert_type=AlertType.EMERGENCY_EXIT,
                title="EMERGENCY CLOSE RETRY",
                message=f"{description}: Attempt {attempt} failed, retrying...",
                priority=AlertPriority.HIGH
            )
        elif attempt >= 3:
            self.alert_service.send_alert(
                alert_type=AlertType.CRITICAL_INTERVENTION,
                title="EMERGENCY CLOSE FAILING",
                message=f"{description}: Attempt {attempt}/{self._max_emergency_close_attempts} failed!",
                priority=AlertPriority.CRITICAL
            )

        # Wait before retry (except on last attempt)
        if attempt < self._max_emergency_close_attempts:
            time.sleep(self._emergency_close_retry_delay_seconds)

    # All attempts exhausted
    logger.critical(
        f"EMERGENCY CLOSE FAILED after {self._max_emergency_close_attempts} attempts: {description}"
    )

    self._set_critical_intervention(
        f"Emergency close failed after {self._max_emergency_close_attempts} attempts: {description}"
    )

    return False

def _close_partial_strangle_emergency(self) -> bool:
    """Close naked short with retry logic."""
    naked_leg = self.short_strangle.call if self.short_strangle.call else self.short_strangle.put

    # Wait for spread normalization first
    self._wait_for_spread_normalization(naked_leg.uic)

    # Use retry wrapper
    return self._emergency_close_with_retries(
        legs=[{
            "uic": naked_leg.uic,
            "asset_type": naked_leg.asset_type,
            "buy_sell": "Buy",  # Buy to close short
            "amount": naked_leg.quantity,
            "to_open_close": "ToClose"
        }],
        description="CLOSE_NAKED_SHORT"
    )

def _close_short_strangle_emergency(self) -> bool:
    """Close all shorts with retry logic."""
    legs = []

    if self.short_strangle.call:
        legs.append({
            "uic": self.short_strangle.call.uic,
            "asset_type": self.short_strangle.call.asset_type,
            "buy_sell": "Buy",
            "amount": self.short_strangle.call.quantity,
            "to_open_close": "ToClose"
        })

    if self.short_strangle.put:
        legs.append({
            "uic": self.short_strangle.put.uic,
            "asset_type": self.short_strangle.put.asset_type,
            "buy_sell": "Buy",
            "amount": self.short_strangle.put.quantity,
            "to_open_close": "ToClose"
        })

    # Wait for spread normalization on all legs
    for leg in legs:
        self._wait_for_spread_normalization(leg["uic"])

    # Use retry wrapper
    return self._emergency_close_with_retries(
        legs=legs,
        description="CLOSE_ALL_SHORTS"
    )
```

#### Configuration

```json
{
    "emergency_close": {
        "max_attempts": 5,
        "retry_delay_seconds": 5
    }
}
```

#### Alert Escalation Pattern

| Attempt | Alert Priority | Action |
|---------|----------------|--------|
| 1 | None | Silent retry |
| 2 | HIGH | User notified |
| 3-4 | CRITICAL | Urgent intervention requested |
| 5 (final) | CRITICAL | Critical intervention flag set |

**Key Principle:** After max attempts fail, the `_set_critical_intervention()` flag is set, which halts all bot operations and requires manual verification in SaxoTraderGO before the bot can resume.

---

## 5. State/Position Consistency Validation

### Overview (STATE-002)

Strategy state must match actual position objects. Inconsistency indicates bugs or missed fills.

### Implementation

```python
def check_state_position_consistency(self) -> Optional[str]:
    """
    Verify strategy state matches actual positions.

    Returns:
        Error description if inconsistent, None if consistent
    """
    if self.state == StrategyState.FULL_POSITION:
        # FULL_POSITION requires both straddle AND strangle
        if not self.long_straddle:
            return "STATE-002: FULL_POSITION but no long_straddle object"
        if not self.short_strangle:
            return "STATE-002: FULL_POSITION but no short_strangle object"

    elif self.state == StrategyState.LONG_STRADDLE_ACTIVE:
        # Must have straddle
        if not self.long_straddle:
            return "STATE-002: LONG_STRADDLE_ACTIVE but no long_straddle"

    elif self.state == StrategyState.IDLE:
        # Should have no positions
        if self.long_straddle:
            return "STATE-002: IDLE but has long_straddle"
        if self.short_strangle:
            return "STATE-002: IDLE but has short_strangle"

    return None  # Consistent
```

**Reference:** `strategy.py:1339-1380`

### Usage in Main Loop

```python
# Check consistency every iteration
consistency_error = strategy.check_state_position_consistency()
if consistency_error:
    logger.error(consistency_error)
    strategy._open_circuit_breaker(consistency_error)
```

---

## 6. Critical Intervention Flag

### Overview (ORDER-004)

More severe than circuit breaker. Set when MARKET orders fail during emergency close - indicates broker-level issues.

### Implementation

```python
# In __init__
self._critical_intervention_required: bool = False
self._critical_intervention_reason: str = ""
self._critical_intervention_timestamp: Optional[datetime] = None

def _set_critical_intervention(self, reason: str) -> None:
    """Set critical intervention flag when emergency closes fail."""
    self._critical_intervention_required = True
    self._critical_intervention_reason = reason
    self._critical_intervention_timestamp = datetime.now()

    logger.critical("=" * 70)
    logger.critical("CRITICAL INTERVENTION REQUIRED")
    logger.critical(f"Reason: {reason}")
    logger.critical("MARKET ORDER FAILED DURING EMERGENCY")
    logger.critical("POSITIONS MAY BE STUCK - CHECK SAXOTRADERGO")
    logger.critical("=" * 70)

    self.alert_service.send_alert(
        alert_type=AlertType.CRITICAL_INTERVENTION,
        title="CRITICAL: MARKET ORDER FAILED",
        message=f"Emergency close failed: {reason}\n\nCheck positions in SaxoTraderGO immediately!",
        priority=AlertPriority.CRITICAL
    )

def _check_critical_intervention(self) -> Optional[Tuple[bool, float]]:
    """Check if critical intervention is required."""
    if self._critical_intervention_required:
        elapsed = (datetime.now() - self._critical_intervention_timestamp).total_seconds()
        return (True, elapsed)
    return None
```

**Reference:** `strategy.py:1385-1481`

### When to Set

- MARKET order fails during emergency close
- Order rejected by broker during emergency
- Position can't be closed despite multiple attempts

### Manual Reset

Only after verifying in SaxoTraderGO that positions are safe:

```python
def reset_critical_intervention(self) -> None:
    """Manually reset after verification."""
    self._critical_intervention_required = False
    self._critical_intervention_reason = ""
    self._critical_intervention_timestamp = None

    if self.trade_logger:
        self.trade_logger.log_safety_event({
            "event_type": "CRITICAL_INTERVENTION_RESET",
            "description": "Manually reset after verification"
        })
```

---

## 7. Position Reconciliation

### Overview (POS-003)

Hourly check comparing bot memory vs actual Saxo positions. Detects:
- Early assignment
- Manual intervention
- Quantity mismatches
- Missing legs

### Implementation

```python
def check_position_reconciliation(self) -> None:
    """
    POS-003: Compare expected positions with actual Saxo positions.
    Frequency: Hourly (configured in main.py)
    """
    logger.info("POS-003: Running position reconciliation...")

    # Build expected positions from bot memory
    expected = {}
    if self.long_straddle:
        if self.long_straddle.call:
            expected[self.long_straddle.call.uic] = self.long_straddle.call.quantity
        if self.long_straddle.put:
            expected[self.long_straddle.put.uic] = self.long_straddle.put.quantity
    if self.short_strangle:
        if self.short_strangle.call:
            expected[self.short_strangle.call.uic] = -self.short_strangle.call.quantity  # Negative for short
        if self.short_strangle.put:
            expected[self.short_strangle.put.uic] = -self.short_strangle.put.quantity

    # Fetch actual positions from Saxo
    actual_positions = self.client.get_positions()
    actual = {}
    for pos in actual_positions:
        uic = pos.get("PositionBase", {}).get("Uic")
        amount = pos.get("PositionBase", {}).get("Amount", 0)
        if uic:
            actual[uic] = amount

    # Compare
    discrepancies = []

    # Check for missing or mismatched positions
    for uic, expected_qty in expected.items():
        actual_qty = actual.get(uic, 0)
        if actual_qty != expected_qty:
            discrepancies.append({
                "uic": uic,
                "expected": expected_qty,
                "actual": actual_qty,
                "type": "MISSING" if actual_qty == 0 else "QUANTITY_MISMATCH"
            })

    # Check for unexpected positions
    for uic, actual_qty in actual.items():
        if uic not in expected and actual_qty != 0:
            discrepancies.append({
                "uic": uic,
                "expected": 0,
                "actual": actual_qty,
                "type": "UNEXPECTED"
            })

    if discrepancies:
        logger.warning(f"POS-003: Found {len(discrepancies)} discrepancies!")
        for d in discrepancies:
            logger.warning(f"  UIC {d['uic']}: expected={d['expected']}, actual={d['actual']} ({d['type']})")

        # Log to Google Sheets
        if self.trade_logger:
            self.trade_logger.log_safety_event({
                "event_type": "POSITION_RECONCILIATION",
                "severity": "WARNING",
                "discrepancies": discrepancies
            })

        # Early assignment detection
        if any(d['type'] == 'MISSING' and d['expected'] < 0 for d in discrepancies):
            logger.critical("POSSIBLE EARLY ASSIGNMENT DETECTED!")
            self.alert_service.send_alert(
                alert_type=AlertType.NAKED_POSITION,
                title="POSSIBLE EARLY ASSIGNMENT",
                message="Short position missing - may have been assigned!",
                priority=AlertPriority.CRITICAL
            )
```

**Reference:** `strategy.py:1486-1610`

### Main Loop Integration

```python
# In main.py
last_reconciliation_time = datetime.now()
reconciliation_interval = 3600  # Hourly

while not shutdown_requested:
    # ... other checks ...

    # POS-003: Hourly position reconciliation
    if (now - last_reconciliation_time).total_seconds() >= reconciliation_interval:
        try:
            strategy.check_position_reconciliation()
            last_reconciliation_time = now
        except Exception as e:
            trade_logger.log_error(f"Position reconciliation error: {e}")
```

**Reference:** `main.py:763-771`

---

## 8. Orphaned Order Detection

### Overview

Orphaned orders are orders placed but not properly filled/cancelled. They can cause:
- Duplicate positions
- Unexpected fills
- Account-level issues

### Implementation

```python
# In __init__
self._orphaned_orders: List[str] = []

def _add_orphaned_order(self, order_id: str) -> None:
    """Track an order that couldn't be cancelled."""
    if order_id not in self._orphaned_orders:
        self._orphaned_orders.append(order_id)
        logger.warning(f"Added orphaned order: {order_id}")

def _check_for_orphaned_orders(self) -> bool:
    """
    Check for orphaned orders before any trading operation.

    Returns:
        True if trading can proceed, False if blocked
    """
    # Query Saxo's /orders/ endpoint
    open_orders = self.client.get_open_orders()

    if not open_orders:
        return True  # No open orders, safe to proceed

    # Check if any orders aren't part of our current operation
    current_operation_orders = []  # Track orders we expect

    unexpected_orders = [
        o for o in open_orders
        if o.get('OrderId') not in current_operation_orders
    ]

    if unexpected_orders:
        logger.error(f"Found {len(unexpected_orders)} orphaned orders!")
        for order in unexpected_orders:
            order_id = order.get('OrderId')
            self._add_orphaned_order(order_id)

            # Attempt to cancel
            try:
                self.client.cancel_order(order_id)
                logger.info(f"Cancelled orphaned order: {order_id}")
            except Exception as e:
                logger.error(f"Failed to cancel orphaned order {order_id}: {e}")

        # Block trading if orphaned orders exist
        self._open_circuit_breaker("Orphaned orders detected")
        return False

    return True
```

**Reference:** `strategy.py:1148-1189`

### Usage Before Trading

```python
def enter_long_straddle(self):
    # Check for orphaned orders FIRST
    if not self._check_for_orphaned_orders():
        return False

    # Proceed with entry...
```

---

## 9. Position Recovery & Restart Handling

### Overview

Bots must recover gracefully from:
- Systemd restarts
- VM reboots
- Crashes
- Manual stops

### Implementation

```python
def recover_positions(self) -> bool:
    """
    Recover existing positions from Saxo on startup.

    Process:
    1. Fetch all positions from Saxo
    2. Identify our strategy's positions (by underlying/expiry)
    3. Reconstruct long_straddle and short_strangle objects
    4. Detect orphaned positions
    5. Set appropriate state

    Returns:
        True if positions recovered, False if starting fresh
    """
    logger.info("Checking for existing positions to recover...")

    positions = self.client.get_positions()
    if not positions:
        logger.info("No positions found - starting fresh")
        return False

    # Filter to our underlying's options
    our_positions = [
        p for p in positions
        if self._is_our_position(p)
    ]

    if not our_positions:
        return False

    # Reconstruct positions
    recovered_straddle = self._recover_long_straddle_with_tracking(our_positions)
    recovered_strangle = self._recover_short_strangle_with_tracking(our_positions)

    # Detect orphaned positions
    orphaned = self._detect_orphaned_positions(our_positions)
    if orphaned:
        logger.warning(f"Found {len(orphaned)} orphaned positions!")
        self._handle_orphaned_positions(orphaned)

    # Set state based on recovered positions
    if recovered_straddle and recovered_strangle:
        self.state = StrategyState.FULL_POSITION
    elif recovered_straddle:
        self.state = StrategyState.LONG_STRADDLE_ACTIVE
    else:
        self.state = StrategyState.IDLE

    logger.info(f"Position recovery complete - state: {self.state.value}")
    return recovered_straddle is not None or recovered_strangle is not None
```

**Reference:** `strategy.py:3072-3301`

### Main.py Integration

```python
# After strategy initialization
trade_logger.log_event("Checking for existing positions to recover...")
positions_recovered = strategy.recover_positions()
if positions_recovered:
    trade_logger.log_event(f"Position recovery complete - state: {strategy.state.value}")
else:
    trade_logger.log_event("No existing positions found - starting fresh")

# Sync Positions sheet
strategy.sync_positions_sheet()
```

**Reference:** `main.py:394-404`

---

## 10. Partial Fill Handling

### Overview

When multi-leg orders partially fill (one leg fills, another fails):
- **Strangle partial:** Close naked short, keep straddle
- **Straddle partial:** Close everything, go FLAT

### Principle

```
STRANGLE PARTIAL FILL: Naked short = unlimited risk!
  → Close ONLY the naked short
  → Keep the complete straddle (it's safe alone)

STRADDLE PARTIAL FILL: Incomplete hedge
  → Close partial straddle
  → Close ALL shorts (they're unprotected)
  → Go to IDLE state
```

### Implementation

```python
def _handle_strangle_partial_fill_fallback(self) -> bool:
    """
    Handle partial fill on strangle - close ONLY the naked short.

    The straddle alone is safe (limited risk - you can only lose premium).
    """
    logger.critical("STRANGLE PARTIAL FILL FALLBACK TRIGGERED")
    logger.critical("Closing naked short, keeping straddle intact")

    # Sync with Saxo to know which leg filled
    self._sync_strangle_after_partial_close()

    # Close the filled (naked) short leg
    if self.short_strangle:
        if not self._close_partial_strangle_emergency():
            self._set_critical_intervention("Failed to close naked short after partial fill")
            return False

    # State: LONG_STRADDLE_ACTIVE (safe, can try strangle again later)
    self.state = StrategyState.LONG_STRADDLE_ACTIVE

    self.alert_service.send_alert(
        alert_type=AlertType.PARTIAL_FILL,
        title="Strangle Partial Fill Handled",
        message="Naked short closed, keeping straddle",
        priority=AlertPriority.MEDIUM
    )

    return True

def _handle_straddle_partial_fill_fallback(self) -> bool:
    """
    Handle partial fill on straddle - close EVERYTHING, go FLAT.

    Incomplete straddle can't protect shorts properly.
    """
    logger.critical("STRADDLE PARTIAL FILL FALLBACK TRIGGERED")
    logger.critical("Closing ALL positions to eliminate risk")

    # Sync with Saxo
    self._sync_straddle_after_partial_close()

    # Close shorts first (higher risk)
    if self.short_strangle:
        if not self._close_short_strangle_emergency():
            self._set_critical_intervention("Failed to close shorts after straddle partial fill")
            return False

    # Close partial straddle
    if self.long_straddle:
        if not self._close_partial_straddle_emergency():
            self._set_critical_intervention("Failed to close partial straddle")
            return False

    # State: IDLE (clean slate)
    self.state = StrategyState.IDLE

    self.alert_service.send_alert(
        alert_type=AlertType.PARTIAL_FILL,
        title="Straddle Partial Fill - Went FLAT",
        message="All positions closed for safety",
        priority=AlertPriority.HIGH
    )

    return True
```

**Reference:** `strategy.py:984-1146`

---

## 11. Quote Validation & Freshness

### Overview (DATA-001)

Stale quotes cause bad fill prices. Always validate before trading.

### Implementation

```python
def _validate_quote_freshness(
    self,
    quote: Dict,
    max_age_seconds: int = 60
) -> Tuple[bool, str]:
    """
    Validate that a quote is fresh enough for trading.

    Args:
        quote: Quote dictionary from Saxo
        max_age_seconds: Maximum acceptable age in seconds

    Returns:
        Tuple of (is_valid, reason)
    """
    if not quote:
        return (False, "No quote data")

    timestamp_str = quote.get("Quote", {}).get("LastUpdated") or quote.get("Timestamp")
    if not timestamp_str:
        return (False, "No timestamp in quote")

    try:
        quote_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        now = datetime.now(quote_time.tzinfo)
        age_seconds = (now - quote_time).total_seconds()

        if age_seconds > max_age_seconds:
            return (False, f"Quote is {age_seconds:.0f}s old (max: {max_age_seconds}s)")

        return (True, "Fresh")
    except Exception as e:
        return (False, f"Could not parse timestamp: {e}")
```

**Reference:** `strategy.py:2235-2284`

### Greeks Validation

```python
def _warn_missing_greeks(self, quote: Dict, context: str) -> None:
    """Warn if Greeks are missing (needed for risk calculations)."""
    greeks = quote.get("Greeks", {})

    missing = []
    for greek in ["Delta", "Gamma", "Theta", "Vega"]:
        if greek not in greeks or greeks[greek] is None:
            missing.append(greek)

    if missing:
        logger.warning(f"Missing Greeks in {context}: {', '.join(missing)}")
```

**Reference:** `strategy.py:2286-2315`

### Option Chain Validation

```python
def _validate_option_chain(self, strikes: List[Dict], min_strikes: int = 3) -> bool:
    """
    Validate option chain has sufficient strikes for trading.

    Returns False if:
    - Too few strikes available
    - Bid/Ask spreads too wide
    - Missing required data
    """
    if not strikes or len(strikes) < min_strikes:
        logger.error(f"Insufficient strikes: {len(strikes) if strikes else 0} (need {min_strikes}+)")
        return False

    # Check for valid bid/ask
    valid_strikes = [
        s for s in strikes
        if s.get("Quote", {}).get("Bid", 0) > 0
        and s.get("Quote", {}).get("Ask", 0) > 0
    ]

    if len(valid_strikes) < min_strikes:
        logger.error(f"Only {len(valid_strikes)} strikes with valid quotes")
        return False

    return True
```

**Reference:** `strategy.py:2317-2370`

---

## 12. WebSocket Health Monitoring

### Overview

WebSocket connections can die silently. Must actively monitor health.

### Implementation

```python
# In SaxoClient.__init__
self._heartbeat_count = 0
self._last_heartbeat_time: Optional[datetime] = None  # Fix #6
self._last_message_time: Optional[datetime] = None    # Fix #5
self._cache_max_age_seconds = 60  # Fix #2

def is_websocket_healthy(self) -> bool:
    """
    Check if WebSocket connection is healthy.

    Checks:
    1. Is thread alive?
    2. Last message received < 60s ago
    3. Last heartbeat < 60s ago
    4. Cache data not stale
    """
    # Check thread alive
    if not self.ws_thread or not self.ws_thread.is_alive():
        logger.warning("WebSocket thread is not alive")
        return False

    now = datetime.now()

    # Check last message freshness (Fix #5)
    if self._last_message_time:
        message_age = (now - self._last_message_time).total_seconds()
        if message_age > 60:
            logger.warning(f"WebSocket last message {message_age:.0f}s ago (stale)")
            return False

    # Check heartbeat freshness (Fix #6)
    # Saxo sends heartbeats every ~15 seconds
    if self._last_heartbeat_time:
        heartbeat_age = (now - self._last_heartbeat_time).total_seconds()
        if heartbeat_age > 60:
            logger.warning(f"WebSocket last heartbeat {heartbeat_age:.0f}s ago (zombie)")
            return False

    return True

def get_quote(self, uic: int, asset_type: str = None) -> Optional[Dict]:
    """Get quote with WebSocket health fallback."""
    # Check cache first
    with self._price_cache_lock:
        cached = self._price_cache.get(uic)
        if cached:
            cache_age = (datetime.now() - cached['timestamp']).total_seconds()

            # Fix #2: Reject stale cache
            if cache_age <= self._cache_max_age_seconds:
                if self.is_websocket_healthy():
                    return cached['data']
                else:
                    logger.debug(f"WebSocket unhealthy, falling back to REST")

    # Fall back to REST API
    return self._get_quote_rest(uic, asset_type)
```

**Reference:** `shared/saxo_client.py:3736-3800`

### Cache Invalidation on Disconnect

```python
def _on_ws_close(self, ws, close_status_code, close_msg):
    """Handle WebSocket close - invalidate cache!"""
    logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")

    # Fix #1: CRITICAL - Clear cache on disconnect
    with self._price_cache_lock:
        self._price_cache.clear()
        logger.info("Price cache cleared on WebSocket disconnect")

    self.is_streaming = False
```

**Reference:** `shared/saxo_client.py` (WebSocket handlers section)

---

## 13. Token Coordination

### Overview

Multiple bots sharing the same Saxo credentials must coordinate token refresh:
- Refresh tokens are ONE-TIME USE
- If Bot A refreshes while Bot B has stale tokens, Bot B gets 401 errors
- Saxo tokens expire every 20 minutes

### Architecture

```
┌───────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Bot A        │────▶│  Token Cache     │◀────│  Token Keeper   │
│  (trading)    │     │  (file-based)    │     │  (24/7 refresh) │
├───────────────┤     │                  │     │                 │
│  Bot B        │────▶│  + Lock File     │     │  Checks: 60s    │
│  (trading)    │     │  (fcntl locking) │     │  Threshold: 5min│
└───────────────┘     └──────────────────┘     └─────────────────┘
                              │
                              ▼
                      ┌──────────────────┐
                      │  Secret Manager  │
                      │  (persistence)   │
                      └──────────────────┘
```

### Implementation

```python
class TokenCoordinator:
    """Coordinates token refresh across multiple bot processes."""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or "/opt/calypso/data")
        self.cache_file = self.data_dir / "saxo_token_cache.json"
        self.lock_file = self.data_dir / "saxo_token.lock"

        # In-memory cache
        self._cached_tokens: Optional[Dict] = None
        self._cache_loaded_at: Optional[datetime] = None

    def _acquire_lock(self, timeout: int = 30) -> Optional[int]:
        """Acquire exclusive lock for token refresh."""
        lock_fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)

        start_time = time.time()
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock_fd
            except (IOError, OSError):
                if time.time() - start_time >= timeout:
                    os.close(lock_fd)
                    return None
                time.sleep(0.1)

    def refresh_with_lock(
        self,
        refresh_func: Callable,
        save_to_secret_manager: Callable = None
    ) -> Optional[Dict]:
        """
        Refresh tokens with exclusive lock to prevent race conditions.
        """
        lock_fd = self._acquire_lock()
        if lock_fd is None:
            logger.error("Could not acquire token lock")
            return None

        try:
            # Re-check cache (another process may have refreshed)
            cached = self._read_cache_file()
            if cached and self.is_token_valid(cached):
                logger.info("Using tokens refreshed by another process")
                return cached

            # Perform actual refresh
            new_tokens = refresh_func()
            if not new_tokens:
                return None

            # Update cache
            self._write_cache_file(new_tokens)

            # Persist to Secret Manager
            if save_to_secret_manager:
                save_to_secret_manager(new_tokens)

            return new_tokens
        finally:
            self._release_lock(lock_fd)
```

**Reference:** `shared/token_coordinator.py:65-290`

### SaxoClient Integration

```python
# In SaxoClient.__init__
self.token_coordinator = get_token_coordinator()

def authenticate(self, force_refresh: bool = False) -> bool:
    # Check coordinator cache first
    cached_tokens = self.token_coordinator.get_cached_tokens()
    if cached_tokens and not force_refresh:
        if self.token_coordinator.is_token_valid(cached_tokens):
            self._apply_tokens_from_cache(cached_tokens)
            return True

    # Use coordinated refresh
    if self.refresh_token:
        if self._coordinated_token_refresh():
            return True

    # Fall back to OAuth flow
    return self._oauth_authorization_flow()
```

**Reference:** `shared/saxo_client.py:315-377`

---

## 14. Market Hours & Time-Based Safety

### Overview

Time-based safety ensures:
- No trading outside market hours
- Opening range volatility is avoided
- Early close days are handled
- Trading cutoffs before close

### Market Hours Validation

```python
from shared.market_hours import (
    is_market_open,
    is_pre_market,
    is_after_hours,
    is_saxo_price_available,
    is_weekend,
    is_market_holiday,
    is_early_close_day,
    get_us_market_time
)

# In main loop
if not is_market_open():
    market_status = get_market_status_message()
    trade_logger.log_event(market_status)

    sleep_time = calculate_sleep_duration(max_sleep=900)
    interruptible_sleep(sleep_time)
    continue
```

**Reference:** `main.py:522-628`

### Opening Range Delay (TIME-006)

First 30 minutes after open are volatile. Wait before fresh entries:

```python
# In strategy __init__
self._fresh_entry_delay_minutes: int = config.get("fresh_entry_delay_minutes", 30)

def _should_wait_for_opening_range(self) -> bool:
    """
    Check if we should wait for opening range to end.

    Only applies to FRESH entries (0 positions).
    Does NOT apply to re-entries after ITM close.
    """
    if self.state != StrategyState.IDLE:
        return False  # Already have positions

    now = get_us_market_time()
    market_open = now.replace(hour=9, minute=30, second=0)
    delay_end = market_open + timedelta(minutes=self._fresh_entry_delay_minutes)

    if now < delay_end:
        logger.info(f"Opening range: waiting until {delay_end.strftime('%H:%M')}")
        return True

    return False
```

**Reference:** `strategy.py:306-311`

### Early Close Handling (TIME-003)

```python
def is_early_close_day(self) -> bool:
    """Check if today is a 1 PM close day."""
    early_closes = get_early_close_dates(datetime.now().year)
    today = datetime.now().date()

    for reason, close_date in early_closes.items():
        if close_date.date() == today:
            return True
    return False

# In strategy check
if self.is_early_close_day():
    now = get_us_market_time()
    if now.hour >= 12:  # After noon on early close day
        logger.warning("Early close day - limiting new entries")
        # Don't enter new shorts, only manage existing
```

**Reference:** `strategy.py:1907-1983`

### Trading Cutoffs

```python
# In strategy __init__
self.recenter_cutoff_minutes = config.get("recenter_cutoff_minutes_before_close", 15)
self.shorts_cutoff_minutes = config.get("shorts_cutoff_minutes_before_close", 10)

def _is_within_trading_cutoff(self, operation: str) -> bool:
    """Check if we're too close to market close for an operation."""
    now = get_us_market_time()

    # Determine close time (early close vs normal)
    if self.is_early_close_day():
        close_time = now.replace(hour=13, minute=0)
    else:
        close_time = now.replace(hour=16, minute=0)

    minutes_until_close = (close_time - now).total_seconds() / 60

    if operation == "recenter" and minutes_until_close < self.recenter_cutoff_minutes:
        return True
    if operation == "shorts" and minutes_until_close < self.shorts_cutoff_minutes:
        return True

    return False
```

---

## 15. Adaptive Risk Monitoring

### Overview

Risk monitoring adapts to market conditions. Uses "cushion consumption" model:
- Track distance from entry price to short strikes
- As price moves toward shorts, monitoring intensifies

### Monitoring Modes

```python
class MonitoringMode(Enum):
    """Monitoring intensity levels with associated intervals."""
    NORMAL = 10         # 10 seconds - normal trading
    VIGILANT = 2        # 2 seconds - price near shorts
    FOMC_BLACKOUT = 3600  # 1 hour - no positions, FOMC day
    OPENING_RANGE = 60  # 1 minute - waiting for open to settle
```

**Reference:** `bots/delta_neutral/models/states.py:45-85`

### Cushion Calculation

```python
def get_monitoring_mode(self) -> MonitoringMode:
    """
    Determine monitoring mode based on cushion consumption.

    Cushion = distance from entry price to short strike
    Consumption = how much of that cushion has been used

    Thresholds:
    - < 60% consumed: NORMAL (10s)
    - 60-75% consumed: VIGILANT (2s)
    - >= 75% consumed: Triggers roll logic
    - 0.1% from strike: Emergency close (absolute floor)
    """
    if self.state == StrategyState.IDLE:
        return MonitoringMode.NORMAL

    if not self.short_strangle:
        return MonitoringMode.NORMAL

    underlying_price = self.current_underlying_price
    entry_price = self.short_strangle.entry_underlying_price or underlying_price

    # Calculate cushion for each side
    call_cushion_pct = self._calculate_cushion_consumed(
        entry_price, underlying_price, self.short_strangle.call.strike, is_call=True
    )
    put_cushion_pct = self._calculate_cushion_consumed(
        entry_price, underlying_price, self.short_strangle.put.strike, is_call=False
    )

    max_consumed = max(call_cushion_pct, put_cushion_pct)

    # Check absolute danger zone first (0.1% = emergency)
    if self._is_in_danger_zone():
        return MonitoringMode.VIGILANT  # Will trigger emergency close

    # Adaptive thresholds
    if max_consumed >= 60:  # 60% cushion consumed
        logger.info(f"VIGILANT mode: {max_consumed:.0f}% cushion consumed")
        return MonitoringMode.VIGILANT

    return MonitoringMode.NORMAL

def _calculate_cushion_consumed(
    self,
    entry_price: float,
    current_price: float,
    strike: float,
    is_call: bool
) -> float:
    """Calculate percentage of original cushion that's been consumed."""
    # Original cushion = distance from entry to strike
    original_cushion = abs(strike - entry_price)

    if original_cushion == 0:
        return 100.0  # At strike = 100% consumed

    # Current distance
    current_distance = abs(strike - current_price)

    # Consumed = 1 - (current/original)
    consumed_pct = (1.0 - current_distance / original_cushion) * 100

    return max(0, min(100, consumed_pct))
```

**Reference:** `strategy.py:5258-5425`

### ITM Risk Check (0.1% Danger Zone)

```python
def check_shorts_itm_risk(self) -> Optional[str]:
    """
    Check if shorts are dangerously close to ITM.

    0.1% threshold = absolute safety floor
    At this point, MARKET order to close immediately.
    """
    if not self.short_strangle:
        return None

    price = self.current_underlying_price

    # Check call side
    if self.short_strangle.call:
        call_strike = self.short_strangle.call.strike
        distance_pct = (call_strike - price) / price * 100

        if distance_pct <= 0.1:  # 0.1% or ITM
            return f"CALL ITM RISK: ${price:.2f} is {distance_pct:.2f}% from ${call_strike:.0f}"

    # Check put side
    if self.short_strangle.put:
        put_strike = self.short_strangle.put.strike
        distance_pct = (price - put_strike) / price * 100

        if distance_pct <= 0.1:  # 0.1% or ITM
            return f"PUT ITM RISK: ${price:.2f} is {distance_pct:.2f}% from ${put_strike:.0f}"

    return None  # Safe
```

**Reference:** `strategy.py:5013-5058`

---

## 16. Flash Crash Detection

### Overview (MKT-002)

Detect rapid market moves that could threaten positions.

### Implementation

```python
# In __init__
self._price_history: List[Tuple[datetime, float]] = []
self._price_history_window_minutes: int = 5
self._flash_crash_threshold_percent: float = 2.0  # 2% in 5 min = flash crash

def _record_price_for_velocity(self, price: float) -> None:
    """Record price for velocity calculation."""
    now = datetime.now()
    self._price_history.append((now, price))

    # Keep only last N minutes
    cutoff = now - timedelta(minutes=self._price_history_window_minutes)
    self._price_history = [(t, p) for t, p in self._price_history if t > cutoff]

def check_flash_crash_velocity(self) -> Optional[Tuple[float, str]]:
    """
    Check for flash crash (rapid price movement).

    Returns:
        Tuple of (percent_change, description) if flash crash detected, None otherwise
    """
    if len(self._price_history) < 2:
        return None

    oldest_time, oldest_price = self._price_history[0]
    newest_time, newest_price = self._price_history[-1]

    # Calculate percentage change
    pct_change = abs(newest_price - oldest_price) / oldest_price * 100

    if pct_change >= self._flash_crash_threshold_percent:
        direction = "UP" if newest_price > oldest_price else "DOWN"
        description = (
            f"FLASH {direction}: {pct_change:.1f}% move in "
            f"{(newest_time - oldest_time).total_seconds():.0f}s"
        )

        logger.critical(f"MKT-002: {description}")

        return (pct_change, description)

    return None
```

**Reference:** `strategy.py:1749-1907`

### Response to Flash Crash

```python
# In strategy check
flash_crash = self.check_flash_crash_velocity()
if flash_crash:
    pct_change, description = flash_crash

    self.alert_service.send_alert(
        alert_type=AlertType.GAP_WARNING,
        title="FLASH CRASH DETECTED",
        message=description,
        priority=AlertPriority.CRITICAL
    )

    # Check if shorts are threatened
    if self.short_strangle:
        itm_risk = self.check_shorts_itm_risk()
        if itm_risk:
            logger.critical(f"Flash crash threatening shorts: {itm_risk}")
            self._close_short_strangle_emergency()
```

---

## 17. Configuration Validation

### Overview

Validate all required configuration on startup before any trading.

### Implementation

```python
def validate_config(config: dict) -> bool:
    """
    Validate required configuration values.

    Raises:
        ValueError: If required config is missing
    """
    # Check account section
    if "account" not in config:
        raise ValueError("Missing config section: account")

    # Get environment
    environment = config.get("saxo_api", {}).get("environment", "sim")
    account_config = config["account"]

    # Check for environment-specific account keys
    if environment in account_config and isinstance(account_config[environment], dict):
        env_account = account_config[environment]
        if "account_key" not in env_account:
            raise ValueError(f"Missing: account.{environment}.account_key")
        if "client_key" not in env_account:
            raise ValueError(f"Missing: account.{environment}.client_key")
    else:
        # Legacy structure
        if "account_key" not in account_config:
            raise ValueError("Missing: account.account_key")

    # Check saxo_api section
    if "saxo_api" not in config:
        raise ValueError("Missing config section: saxo_api")

    # Check environment credentials
    env_config = config["saxo_api"].get(environment, {})
    if "app_key" not in env_config or not env_config["app_key"]:
        raise ValueError(f"Missing app_key for {environment}")
    if "app_secret" not in env_config or not env_config["app_secret"]:
        raise ValueError(f"Missing app_secret for {environment}")

    return True
```

**Reference:** `main.py:215-263`

---

## 18. Logging & Audit Trails

### Overview

Comprehensive logging is essential for:
- Debugging issues
- Post-mortem analysis
- Regulatory compliance
- Performance tracking

### Logging Levels

```python
# logger.critical() - Circuit breaker, emergencies, intervention required
logger.critical("CIRCUIT BREAKER TRIGGERED - Trading halted!")

# logger.warning() - Failures, edge cases, risks
logger.warning(f"Order timeout - attempting retry {attempt}/3")

# logger.info() - Normal operations, state changes
logger.info(f"State changed: {old_state} -> {new_state}")

# logger.debug() - Detailed execution flow
logger.debug(f"Quote received: bid={bid}, ask={ask}")
```

### Google Sheets Integration

```python
# Trade logging
trade_logger.log_trade(
    action="CLOSE_SHORT_CALL",
    strike=position.strike,
    price=fill_price,
    delta=position.delta,
    pnl=calculated_pnl,
    underlying_price=spy_price,
    vix=current_vix
)

# Safety events
trade_logger.log_safety_event({
    "timestamp": datetime.now().isoformat(),
    "event_type": "CIRCUIT_BREAKER_OPEN",
    "severity": "CRITICAL",
    "spy_price": self.current_underlying_price,
    "vix": self.current_vix,
    "action_taken": "TRADING HALTED",
    "description": reason
})

# Position snapshots
trade_logger.log_position_snapshot(positions)

# Performance metrics
trade_logger.log_performance_metrics(
    period="End of Day",
    metrics=dashboard_metrics,
    saxo_client=client
)

# Bot activity (hourly)
trade_logger.log_bot_activity(
    level="INFO",
    component="Strategy",
    message=f"Hourly update: Delta={delta:.4f}, P&L=${pnl:.2f}",
    spy_price=spy_price,
    vix=vix
)
```

**Reference:** `shared/logger_service.py`, `main.py:696-761`

---

## 19. Graceful Shutdown Handling

### Overview

Bots must shut down gracefully to:
- Preserve position state for recovery
- Complete pending operations
- Flush logs to Google Sheets
- Avoid leaving orphaned orders

### Signal Handlers

```python
import signal

shutdown_requested = False

def signal_handler(signum, frame):
    """Handle shutdown signals (CTRL+C, SIGTERM)."""
    global shutdown_requested
    logger.info(f"Shutdown signal received ({signum}). Initiating graceful shutdown...")
    shutdown_requested = True

# Register handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)  # For systemd
```

**Reference:** `main.py:110-118`

### Interruptible Sleep

```python
def interruptible_sleep(seconds: int, check_interval: int = 5) -> bool:
    """
    Sleep with periodic shutdown check.

    Returns:
        True if sleep completed, False if interrupted
    """
    remaining = seconds
    while remaining > 0 and not shutdown_requested:
        time.sleep(min(check_interval, remaining))
        remaining -= check_interval
    return not shutdown_requested
```

**Reference:** `main.py:121-137`

### Shutdown Sequence

```python
finally:
    # Graceful shutdown
    trade_logger.log_event("INITIATING GRACEFUL SHUTDOWN")

    # Stop WebSocket streaming
    if USE_WEBSOCKET_STREAMING:
        client.stop_price_streaming()

    # Log final status
    status = strategy.get_status_summary()
    trade_logger.log_status(status)

    # Note about position preservation
    if strategy.state != StrategyState.IDLE:
        trade_logger.log_event(
            "Bot shutting down with active positions. "
            "Positions will remain open on Saxo. "
            "On next startup, the bot will automatically recover."
        )

    # Flush logs to Google Sheets
    trade_logger.shutdown()

    trade_logger.log_event("Shutdown complete.")
```

**Reference:** `main.py:895-923`

---

## 20. Duplicate Bot Prevention

### Overview

Multiple bot instances can cause:
- Duplicate orders
- Position conflicts
- Circuit breaker confusion
- Race conditions

### Implementation

```python
def kill_existing_bot_instances() -> int:
    """
    Find and kill any existing bot instances before starting.

    Returns:
        Number of processes killed
    """
    current_pid = os.getpid()
    killed_count = 0

    try:
        # Find all Python processes running main.py
        result = subprocess.run(
            ["pgrep", "-f", "main.py"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')

            for pid_str in pids:
                try:
                    pid = int(pid_str.strip())
                    if pid != current_pid:
                        logger.info(f"Terminating existing instance (PID: {pid})")
                        os.kill(pid, signal.SIGTERM)
                        killed_count += 1
                        time.sleep(1)  # Allow graceful shutdown
                except (ValueError, ProcessLookupError):
                    pass

        if killed_count > 0:
            logger.info(f"Terminated {killed_count} existing instance(s)")
            time.sleep(2)  # Extra wait for cleanup

    except FileNotFoundError:
        logger.warning("pgrep not available - cannot check for existing instances")

    return killed_count
```

**Reference:** `main.py:139-188`

### Usage in main()

```python
def main():
    # ... banner ...

    # Kill existing instances BEFORE doing anything else
    killed = kill_existing_bot_instances()
    if killed > 0:
        print(f"Terminated {killed} existing bot instance(s)")

    # ... continue with startup ...
```

---

## 21. Retry Logic & Error Recovery

### Overview

Failed operations should be retried with progressive tolerance:
1. First retry: Same price
2. Second retry: +5% tolerance
3. Third retry: +10% tolerance
4. Final fallback: MARKET order (for emergencies only)

### Progressive Retry Pattern

```python
def _place_order_with_retries(
    self,
    legs: List[Dict],
    description: str,
    max_attempts: int = 3
) -> Dict:
    """
    Place order with progressive retry on failure.

    Sequence:
    1. 0% tolerance (exact price) - 2 attempts
    2. 5% tolerance - 2 attempts
    3. 10% tolerance - 2 attempts
    4. MARKET order - only for emergency/closing shorts
    """
    tolerances = [0, 0, 5, 5, 10, 10]  # Percentages

    for attempt, tolerance in enumerate(tolerances[:max_attempts * 2], 1):
        logger.info(f"Order attempt {attempt}/{max_attempts * 2} (+{tolerance}% tolerance)")

        # Adjust prices based on tolerance
        adjusted_legs = self._apply_tolerance(legs, tolerance)

        result = self._place_protected_multi_leg_order(
            legs=adjusted_legs,
            order_description=f"{description}_attempt_{attempt}"
        )

        if result["filled"]:
            if attempt > 1:
                logger.info(f"Order filled on attempt {attempt} with {tolerance}% tolerance")
            return result

        # Check if we should continue or abort
        if result.get("rejected"):
            logger.warning(f"Order rejected: {result.get('rejection_reason')}")
            break

        # Wait before retry
        time.sleep(5)

    return {"filled": False, "error": "Max retries exceeded"}
```

### Action Cooldowns

Prevent rapid retry of same failed action:

```python
# In __init__
self._action_cooldowns: Dict[str, datetime] = {}
self._cooldown_seconds: int = 300  # 5 minutes

def _is_action_on_cooldown(self, action_type: str) -> bool:
    """Check if an action is on cooldown."""
    if action_type not in self._action_cooldowns:
        return False

    last_attempt = self._action_cooldowns[action_type]
    elapsed = (datetime.now() - last_attempt).total_seconds()

    return elapsed < self._cooldown_seconds

def _set_action_cooldown(self, action_type: str) -> None:
    """Start cooldown for an action."""
    self._action_cooldowns[action_type] = datetime.now()
    logger.info(f"Action '{action_type}' on cooldown for {self._cooldown_seconds}s")

def _clear_action_cooldown(self, action_type: str) -> None:
    """Clear cooldown for an action."""
    if action_type in self._action_cooldowns:
        del self._action_cooldowns[action_type]
```

**Reference:** `strategy.py:1191-1232`

---

## 22. Main Loop Safety Patterns

### Overview

The main loop is the orchestration layer. It must handle all safety checks in the correct order.

### Complete Main Loop Pattern

```python
try:
    while not shutdown_requested:
        try:
            # ==========================================
            # PHASE 1: MARKET STATUS CHECKS
            # ==========================================

            # Market status monitor (countdown/open/close alerts)
            if market_monitor:
                market_monitor.check_and_alert()

            # Check if market is open
            if not is_market_open():
                # Handle closed market (sleep, daily summaries, etc.)
                continue

            # ==========================================
            # PHASE 2: SAFETY GATE CHECKS
            # ==========================================

            # Circuit breaker check (FIRST!)
            if client.is_circuit_open():
                logger.info("Circuit breaker OPEN - waiting for cooldown")
                interruptible_sleep(check_interval)
                continue

            # Connection timeout check
            client.circuit_breaker.last_successful_connection = datetime.now()
            if client.check_connection_timeout():
                logger.error("Connection timeout - circuit breaker activated")
                continue

            # Critical intervention check
            intervention = strategy._check_critical_intervention()
            if intervention:
                logger.critical("Critical intervention required - halted")
                interruptible_sleep(check_interval)
                continue

            # ==========================================
            # PHASE 3: DAILY INITIALIZATION
            # ==========================================

            if not trading_day_started:
                strategy.start_new_trading_day()
                trading_day_started = True

            strategy.update_intraday_tracking()

            # ==========================================
            # PHASE 4: STRATEGY EXECUTION
            # ==========================================

            action, monitoring_mode = strategy.run_strategy_check()

            if action != "No action":
                trade_logger.log_event(f"ACTION: {action}")

            # ==========================================
            # PHASE 5: PERIODIC OPERATIONS
            # ==========================================

            now = datetime.now()

            # Status logging (60s)
            if (now - last_status_time).total_seconds() >= 60:
                status = strategy.get_status_summary()
                trade_logger.log_status(status)
                last_status_time = now

            # Dashboard logging (5 min)
            if (now - last_dashboard_log_time).total_seconds() >= 300:
                strategy.refresh_position_prices()
                dashboard_metrics = strategy.get_dashboard_metrics_safe()
                trade_logger.log_performance_metrics("5-min", dashboard_metrics)
                last_dashboard_log_time = now

            # Position sync (10 min)
            if (now - last_position_sync_time).total_seconds() >= 600:
                strategy.recover_positions()
                last_position_sync_time = now

            # Position reconciliation (hourly)
            if (now - last_reconciliation_time).total_seconds() >= 3600:
                strategy.check_position_reconciliation()
                last_reconciliation_time = now

            # ==========================================
            # PHASE 6: ADAPTIVE SLEEP
            # ==========================================

            # Determine sleep interval based on monitoring mode
            if monitoring_mode == MonitoringMode.VIGILANT:
                sleep_interval = 2  # Fast monitoring
            elif monitoring_mode == MonitoringMode.FOMC_BLACKOUT:
                sleep_interval = 3600  # Hourly heartbeat
            elif monitoring_mode == MonitoringMode.OPENING_RANGE:
                sleep_interval = 60  # Waiting for market to settle
            else:
                sleep_interval = 10  # Normal

            if not interruptible_sleep(sleep_interval):
                break  # Shutdown requested

        except Exception as e:
            trade_logger.log_error(f"Error in main loop: {e}", exception=e)
            if not interruptible_sleep(check_interval):
                break

finally:
    # Graceful shutdown (see section 19)
    ...
```

**Reference:** `main.py:511-893`

---

## 23. Alert System Integration

### Overview

Alerts provide real-time visibility into bot actions. All alerts go through Pub/Sub for non-blocking delivery.

### Alert Priorities

```python
class AlertPriority(Enum):
    CRITICAL = "critical"  # Telegram + Email - immediate attention
    HIGH = "high"          # Telegram + Email - significant event
    MEDIUM = "medium"      # Telegram + Email - important but not urgent
    LOW = "low"            # Telegram + Email - informational
```

### Alert Types

```python
class AlertType(Enum):
    # Safety Events
    CIRCUIT_BREAKER = "circuit_breaker"
    CRITICAL_INTERVENTION = "critical_intervention"
    EMERGENCY_EXIT = "emergency_exit"

    # Position Events
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    PARTIAL_FILL = "partial_fill"

    # Risk Events
    NAKED_POSITION = "naked_position"
    VIGILANT_ENTERED = "vigilant_entered"
    VIGILANT_EXITED = "vigilant_exited"
    ITM_RISK_CLOSE = "itm_risk_close"

    # ... see full list in shared/alert_service.py
```

### Usage Examples

```python
# Circuit breaker
self.alert_service.circuit_breaker(
    reason="5 consecutive failures",
    consecutive_failures=5,
    details={"spy_price": 593.25, "vix": 18.5}
)

# Position opened
self.alert_service.send_alert(
    alert_type=AlertType.POSITION_OPENED,
    title="Long Straddle Opened",
    message=f"Strike: ${strike} | Premium: ${premium:.2f}",
    priority=AlertPriority.MEDIUM,
    details={"dte": 90, "vix": 17.5}
)

# Emergency exit
self.alert_service.emergency_exit(
    reason="Shorts breached 0.1% threshold",
    pnl=-350.00,
    details={"spy_price": 595.50, "call_strike": 596}
)

# Vigilant mode
self.alert_service.send_alert(
    alert_type=AlertType.VIGILANT_ENTERED,
    title="VIGILANT: Price Near Short Strikes",
    message=f"Call cushion: 70% consumed | Put cushion: 45% consumed",
    priority=AlertPriority.HIGH
)
```

**Reference:** `shared/alert_service.py:80-175`

---

## 24. Order Placement Safety

### Overview

Orders are the most critical operations. Every order must be protected against:
- Stale quotes
- Wide spreads
- Partial fills
- Timeouts
- Rejections
- Excessive order sizes (bug protection)
- Fill price slippage

### Order Size Validation

**CRITICAL:** A bug in quantity calculation could cause the bot to place orders for 100 or 1000 contracts instead of 1. This safety check prevents catastrophic losses from coding errors.

#### Implementation

```python
# In strategy __init__
self._max_contracts_per_order: int = config.get("max_contracts_per_order", 10)
self._max_contracts_per_underlying: int = config.get("max_contracts_per_underlying", 20)

def _validate_order_size(self, legs: List[Dict], order_description: str) -> Tuple[bool, str]:
    """
    Validate order sizes are within acceptable limits.

    Protections:
    1. Per-leg maximum (catches single-leg bugs)
    2. Total order maximum (catches multi-leg bugs)
    3. Underlying position limit (prevents over-concentration)

    Args:
        legs: List of order legs with 'amount' field
        order_description: Description for logging

    Returns:
        Tuple of (is_valid, error_message)
    """
    total_contracts = 0

    for i, leg in enumerate(legs):
        amount = abs(leg.get("amount", 0))

        # Check 1: Per-leg maximum
        if amount > self._max_contracts_per_order:
            error = (
                f"ORDER SIZE REJECTED: Leg {i+1} has {amount} contracts "
                f"(max: {self._max_contracts_per_order}). "
                f"Order: {order_description}"
            )
            logger.critical(error)
            self.alert_service.send_alert(
                alert_type=AlertType.CIRCUIT_BREAKER,
                title="ORDER SIZE LIMIT EXCEEDED",
                message=error,
                priority=AlertPriority.CRITICAL
            )
            return (False, error)

        total_contracts += amount

    # Check 2: Total order maximum
    max_total = self._max_contracts_per_order * len(legs)
    if total_contracts > max_total:
        error = (
            f"ORDER SIZE REJECTED: Total {total_contracts} contracts "
            f"exceeds limit ({max_total}). Order: {order_description}"
        )
        logger.critical(error)
        return (False, error)

    # Check 3: Would this exceed underlying position limit?
    current_position_size = self._get_current_position_size()
    projected_size = current_position_size + total_contracts

    if projected_size > self._max_contracts_per_underlying:
        error = (
            f"POSITION LIMIT EXCEEDED: Current={current_position_size}, "
            f"Adding={total_contracts}, Projected={projected_size} "
            f"(max: {self._max_contracts_per_underlying})"
        )
        logger.critical(error)
        return (False, error)

    # All checks passed
    logger.debug(
        f"Order size validated: {total_contracts} contracts for {order_description}"
    )
    return (True, "")

def _get_current_position_size(self) -> int:
    """Calculate total contracts currently held across all positions."""
    total = 0

    if self.long_straddle:
        if self.long_straddle.call:
            total += abs(self.long_straddle.call.quantity)
        if self.long_straddle.put:
            total += abs(self.long_straddle.put.quantity)

    if self.short_strangle:
        if self.short_strangle.call:
            total += abs(self.short_strangle.call.quantity)
        if self.short_strangle.put:
            total += abs(self.short_strangle.put.quantity)

    return total
```

#### Integration with Protected Order Placement

```python
def _place_protected_multi_leg_order(self, legs: List[Dict], ...) -> Dict:
    # FIRST CHECK: Validate order size before anything else
    is_valid, error = self._validate_order_size(legs, order_description)
    if not is_valid:
        return {"filled": False, "error": error, "rejected_reason": "SIZE_LIMIT"}

    # Continue with quote validation, spread checks, etc.
    ...
```

#### Configuration

```json
{
    "order_limits": {
        "max_contracts_per_order": 10,
        "max_contracts_per_underlying": 20
    }
}
```

#### Common Scenarios

| Scenario | Config Setting | Reasoning |
|----------|----------------|-----------|
| Paper trading | `max_contracts_per_order: 5` | Conservative while testing |
| Small account ($25K) | `max_contracts_per_order: 2` | Limited capital |
| Medium account ($100K) | `max_contracts_per_order: 10` | Reasonable scaling |
| Large account ($500K+) | `max_contracts_per_order: 50` | Higher limits with care |

**Key Principle:** This check runs BEFORE any API calls. A rejected order never touches the broker, preventing expensive mistakes.

### Protected Order Placement

```python
def _place_protected_multi_leg_order(
    self,
    legs: List[Dict],
    total_limit_price: float,
    order_description: str,
    emergency_mode: bool = False,
    use_market_orders: bool = False
) -> Dict:
    """
    Place multi-leg order with full protection.

    Protections:
    1. Quote freshness validation
    2. Spread validation
    3. Timeout protection
    4. Fill verification
    5. Partial fill detection
    """
    result = {
        "filled": False,
        "partial_fill": False,
        "orders": [],
        "error": None
    }

    # 1. Validate quotes are fresh
    for leg in legs:
        quote = self.client.get_quote(leg["uic"], leg["asset_type"])
        is_fresh, reason = self._validate_quote_freshness(quote)
        if not is_fresh:
            result["error"] = f"Stale quote for leg: {reason}"
            return result

    # 2. Validate spreads (unless emergency)
    if not emergency_mode:
        for leg in legs:
            quote = self.client.get_quote(leg["uic"])
            bid = quote["Quote"].get("Bid", 0)
            ask = quote["Quote"].get("Ask", 0)
            spread_pct = (ask - bid) / ((ask + bid) / 2) * 100

            if spread_pct > self.max_spread_percent:
                result["error"] = f"Spread too wide: {spread_pct:.1f}% > {self.max_spread_percent}%"
                return result

    # 3. Determine order type
    order_type = OrderType.MARKET if use_market_orders else OrderType.LIMIT

    # 4. Place orders with timeout
    timeout = self.order_timeout_seconds
    filled_legs = []

    for i, leg in enumerate(legs):
        try:
            order_result = self.client.place_order(
                uic=leg["uic"],
                asset_type=leg["asset_type"],
                buy_sell=leg["buy_sell"],
                amount=leg["amount"],
                order_type=order_type,
                limit_price=leg.get("price") if order_type == OrderType.LIMIT else None,
                to_open_close=leg.get("to_open_close", "ToOpen")
            )

            if order_result and order_result.get("OrderId"):
                # 5. Verify fill
                filled = self._verify_order_fill(
                    order_result["OrderId"],
                    timeout=timeout
                )

                if filled:
                    filled_legs.append(leg)
                    result["orders"].append(order_result)
                else:
                    # Timeout - cancel and handle partial
                    self.client.cancel_order(order_result["OrderId"])
                    result["partial_fill"] = len(filled_legs) > 0
                    result["error"] = f"Leg {i+1} timed out"
                    break
            else:
                result["error"] = f"No order ID returned for leg {i+1}"
                result["partial_fill"] = len(filled_legs) > 0
                break

        except Exception as e:
            result["error"] = str(e)
            result["partial_fill"] = len(filled_legs) > 0
            break

    # Check if all legs filled
    if len(filled_legs) == len(legs):
        result["filled"] = True
        self._reset_failure_count()
    else:
        self._increment_failure_count(order_description + ": " + (result["error"] or "Unknown"))

    return result
```

**Reference:** `strategy.py:1287-1600`

### Fill Verification with Activities Retry

**CRITICAL (Fix #18):** The Saxo activities endpoint may have a sync delay of 1-3 seconds after order fill. Previous implementations checked activities once and gave up, falling back to quoted prices for P&L. This caused significant P&L tracking errors.

#### Implementation

```python
def _verify_order_fill(
    self,
    order_id: str,
    expected_price: float,
    timeout: int = 10  # Reduced from 30s - market orders fill fast
) -> Tuple[bool, Optional[float]]:
    """
    Verify order filled and extract actual fill price.

    Market orders typically fill in ~3 seconds. Reduced timeout from 30s
    to 10s to avoid wasting time polling for filled orders.

    Args:
        order_id: Saxo order ID to verify
        expected_price: Expected fill price (for slippage check)
        timeout: Maximum wait time in seconds

    Returns:
        Tuple of (is_filled, actual_fill_price)
        - (True, price) if filled with known price
        - (True, None) if filled but price unknown (edge case)
        - (False, None) if not filled
    """
    start_time = time.time()
    not_found_count = 0

    while (time.time() - start_time) < timeout:
        # Check order status first
        order = self.client.get_order_status(order_id)

        if not order:
            # Order not found = likely filled and removed from /orders/
            not_found_count += 1

            if not_found_count >= 3:
                # Use activities endpoint with RETRY LOGIC
                fill_result = self._get_fill_from_activities_with_retry(order_id)

                if fill_result:
                    fill_price = fill_result.get("FilledPrice", 0)

                    # Check slippage
                    self._check_fill_slippage(expected_price, fill_price, order_id)

                    return (True, fill_price)
        else:
            status = order.get("Status")

            if status == "Filled":
                # Extract fill price from order response
                fill_price = order.get("FilledPrice") or order.get("Price", 0)
                self._check_fill_slippage(expected_price, fill_price, order_id)
                return (True, fill_price)

            elif status in ["Rejected", "Cancelled", "Expired"]:
                logger.warning(f"Order {order_id} status: {status}")
                return (False, None)

            # else: "Working", "PartiallyFilled" - keep waiting

        time.sleep(1)

    # Timeout reached
    logger.warning(f"Order {order_id} verification timed out after {timeout}s")
    return (False, None)
```

#### Activities Retry Logic (Fix #18)

```python
# In strategy __init__
self._activities_retry_attempts: int = 3
self._activities_retry_delay_seconds: float = 1.0

def _get_fill_from_activities_with_retry(self, order_id: str) -> Optional[Dict]:
    """
    Get fill details from activities endpoint with retry logic.

    The activities endpoint may have a sync delay of 1-3 seconds after
    order fill. This method retries to ensure we capture the fill data.

    Args:
        order_id: The order ID to look up

    Returns:
        Activity dict with FilledPrice if found, None otherwise
    """
    for attempt in range(1, self._activities_retry_attempts + 1):
        try:
            activity = self.client.check_order_filled_by_activity(order_id)

            if activity:
                fill_price = activity.get("FilledPrice", 0)

                if fill_price > 0:
                    logger.info(
                        f"Fill found in activities (attempt {attempt}): "
                        f"OrderId={order_id}, FilledPrice=${fill_price:.2f}"
                    )
                    return activity
                else:
                    # Activity exists but no price yet - keep retrying
                    logger.debug(
                        f"Activity found but no FilledPrice yet (attempt {attempt})"
                    )

        except Exception as e:
            logger.warning(f"Activities lookup error (attempt {attempt}): {e}")

        # Wait before retry (except on last attempt)
        if attempt < self._activities_retry_attempts:
            time.sleep(self._activities_retry_delay_seconds)

    # All retries exhausted
    logger.warning(
        f"Could not get fill price from activities after "
        f"{self._activities_retry_attempts} attempts for order {order_id}"
    )

    # Return None - caller should handle fallback
    return None
```

#### SaxoClient Integration

```python
# In shared/saxo_client.py
def check_order_filled_by_activity(self, order_id: str) -> Optional[Dict]:
    """
    Check if order was filled by querying the activities endpoint.

    CRITICAL: Use "FilledPrice" not "Price" - Fix #14 from lessons learned.

    Returns:
        Activity dict with FilledPrice if filled, None otherwise
    """
    try:
        response = self._make_request(
            "GET",
            f"/cs/v1/activities?OrderId={order_id}",
            auth_required=True
        )

        activities = response.get("Data", [])
        for activity in activities:
            activity_type = activity.get("ActivityType", "")

            if activity_type in ["Fill", "PartialFill"]:
                # CRITICAL: Use FilledPrice, not Price!
                fill_price = activity.get("FilledPrice", 0)

                if fill_price > 0:
                    return activity

        return None

    except Exception as e:
        logger.error(f"Activities endpoint error: {e}")
        return None
```

#### Configuration

```json
{
    "fill_verification": {
        "timeout_seconds": 10,
        "activities_retry_attempts": 3,
        "activities_retry_delay_seconds": 1.0
    }
}
```

### Fill Price Slippage Check

**CRITICAL:** Orders can fill at significantly different prices than expected, especially during volatility. Large slippage indicates market conditions have changed or there's a pricing issue.

#### Implementation

```python
# In strategy __init__
self._slippage_warning_threshold_percent: float = 5.0   # Warn at 5% slippage
self._slippage_critical_threshold_percent: float = 15.0  # Critical at 15%
self._slippage_abort_threshold_percent: float = 25.0    # Consider aborting at 25%

def _check_fill_slippage(
    self,
    expected_price: float,
    actual_price: float,
    order_id: str
) -> Optional[str]:
    """
    Check for excessive slippage between expected and actual fill prices.

    Args:
        expected_price: Price we expected to fill at
        actual_price: Actual fill price from broker
        order_id: Order ID for logging

    Returns:
        Slippage description if significant, None if acceptable
    """
    if expected_price <= 0 or actual_price <= 0:
        logger.warning(
            f"Cannot calculate slippage: expected=${expected_price:.2f}, "
            f"actual=${actual_price:.2f}"
        )
        return None

    # Calculate slippage percentage
    slippage_pct = abs(actual_price - expected_price) / expected_price * 100
    slippage_direction = "FAVORABLE" if actual_price < expected_price else "UNFAVORABLE"

    # Log slippage for tracking
    if slippage_pct > 0.5:  # Log any slippage > 0.5%
        logger.info(
            f"Fill slippage: {slippage_pct:.2f}% {slippage_direction} "
            f"(expected=${expected_price:.2f}, actual=${actual_price:.2f})"
        )

    # Check thresholds
    if slippage_pct >= self._slippage_critical_threshold_percent:
        message = (
            f"CRITICAL SLIPPAGE: {slippage_pct:.1f}% {slippage_direction}\n"
            f"Expected: ${expected_price:.2f}\n"
            f"Actual: ${actual_price:.2f}\n"
            f"Order: {order_id}"
        )
        logger.critical(message)

        self.alert_service.send_alert(
            alert_type=AlertType.GAP_WARNING,
            title="CRITICAL FILL SLIPPAGE",
            message=message,
            priority=AlertPriority.CRITICAL
        )

        # Log to safety events
        if self.trade_logger:
            self.trade_logger.log_safety_event({
                "event_type": "CRITICAL_SLIPPAGE",
                "severity": "CRITICAL",
                "expected_price": expected_price,
                "actual_price": actual_price,
                "slippage_percent": slippage_pct,
                "direction": slippage_direction,
                "order_id": order_id
            })

        return message

    elif slippage_pct >= self._slippage_warning_threshold_percent:
        message = (
            f"High slippage: {slippage_pct:.1f}% {slippage_direction} "
            f"(expected=${expected_price:.2f}, actual=${actual_price:.2f})"
        )
        logger.warning(message)

        self.alert_service.send_alert(
            alert_type=AlertType.GAP_WARNING,
            title="HIGH FILL SLIPPAGE",
            message=message,
            priority=AlertPriority.HIGH
        )

        return message

    return None  # Slippage within acceptable range

def _track_slippage_metrics(self, slippage_pct: float, direction: str) -> None:
    """Track slippage metrics for analysis."""
    if not hasattr(self, '_slippage_history'):
        self._slippage_history: List[Dict] = []

    self._slippage_history.append({
        "timestamp": datetime.now().isoformat(),
        "slippage_percent": slippage_pct,
        "direction": direction
    })

    # Keep last 100 entries
    if len(self._slippage_history) > 100:
        self._slippage_history = self._slippage_history[-100:]

def get_average_slippage(self) -> Tuple[float, int]:
    """Calculate average slippage from history."""
    if not hasattr(self, '_slippage_history') or not self._slippage_history:
        return (0.0, 0)

    total = sum(entry["slippage_percent"] for entry in self._slippage_history)
    count = len(self._slippage_history)

    return (total / count, count)
```

#### Configuration

```json
{
    "slippage_monitoring": {
        "warning_threshold_percent": 5.0,
        "critical_threshold_percent": 15.0,
        "abort_threshold_percent": 25.0
    }
}
```

#### Slippage Response Matrix

| Slippage | Priority | Action |
|----------|----------|--------|
| < 5% | None | Log only, acceptable |
| 5-15% | HIGH | Alert user, continue trading |
| 15-25% | CRITICAL | Alert user, log safety event, review market conditions |
| > 25% | CRITICAL | Consider pausing new orders, investigate |

**Key Principle:** Slippage tracking helps identify:
1. Market volatility periods to avoid
2. Broker execution quality issues
3. Quote staleness problems (if slippage is consistently high)

---

## 25. Implementation Checklist

### New Bot Implementation Checklist

Use this checklist when implementing a new trading bot:

#### Phase 1: Core Safety Infrastructure

- [ ] **Circuit Breaker**
  - [ ] Consecutive failure tracking
  - [ ] Sliding window failure tracking (CONN-002)
  - [ ] Circuit breaker open/close logic
  - [ ] Auto-reset conditions
  - [ ] Manual reset method

- [ ] **Emergency Position Safety**
  - [ ] 6-scenario position analysis
  - [ ] Emergency close naked short
  - [ ] Emergency close all shorts
  - [ ] Emergency close all positions
  - [ ] MARKET orders for emergency closes
  - [ ] Spread validation before emergency close
  - [ ] Spread normalization wait logic
  - [ ] Emergency close max retries (5 attempts)
  - [ ] Escalating alerts on retry failures

- [ ] **State Consistency**
  - [ ] State enum definition
  - [ ] State/position consistency check
  - [ ] State persistence for recovery

#### Phase 2: Position Management

- [ ] **Position Recovery**
  - [ ] Recover on startup
  - [ ] Reconstruct position objects from Saxo
  - [ ] Detect orphaned positions
  - [ ] Handle orphaned orders

- [ ] **Position Reconciliation**
  - [ ] Hourly reconciliation check
  - [ ] Early assignment detection
  - [ ] Quantity mismatch detection

- [ ] **Partial Fill Handling**
  - [ ] Strangle partial fill fallback
  - [ ] Straddle partial fill fallback
  - [ ] Sync after partial close

#### Phase 3: API Safety

- [ ] **Quote Validation**
  - [ ] Freshness validation (60s max)
  - [ ] Greeks validation
  - [ ] Option chain validation
  - [ ] Spread validation

- [ ] **Token Coordination**
  - [ ] Use TokenCoordinator
  - [ ] Check cache before refresh
  - [ ] Coordinated refresh with lock

- [ ] **WebSocket Health** (if using)
  - [ ] Thread alive check
  - [ ] Message freshness check
  - [ ] Heartbeat timeout detection
  - [ ] Cache invalidation on disconnect
  - [ ] REST fallback

#### Phase 4: Time-Based Safety

- [ ] **Market Hours**
  - [ ] Market open check
  - [ ] Pre-market handling
  - [ ] After-hours handling
  - [ ] Weekend/holiday detection

- [ ] **Opening Range**
  - [ ] Delay for fresh entries
  - [ ] Monitoring mode for waiting

- [ ] **Early Close**
  - [ ] Early close day detection
  - [ ] Trading cutoff adjustments

#### Phase 5: Risk Monitoring

- [ ] **Monitoring Modes**
  - [ ] NORMAL (10s)
  - [ ] VIGILANT (2s)
  - [ ] FOMC_BLACKOUT (3600s)
  - [ ] OPENING_RANGE (60s)

- [ ] **ITM Risk**
  - [ ] Cushion calculation
  - [ ] Adaptive thresholds
  - [ ] 0.1% danger zone

- [ ] **Flash Crash**
  - [ ] Price velocity tracking
  - [ ] Threshold detection
  - [ ] Emergency response

#### Phase 6: Order Safety

- [ ] **Protected Order Placement**
  - [ ] Quote freshness check
  - [ ] Spread validation
  - [ ] Timeout protection
  - [ ] Fill verification
  - [ ] Partial fill detection
  - [ ] Order size validation (max contracts per order)
  - [ ] Position size limits (max contracts per underlying)

- [ ] **Fill Verification**
  - [ ] Activities endpoint retry logic (3 attempts, 1s delay)
  - [ ] Fill price slippage check
  - [ ] Slippage alerting (5% warning, 15% critical)
  - [ ] Slippage tracking for analysis

- [ ] **Retry Logic**
  - [ ] Progressive tolerance
  - [ ] Action cooldowns
  - [ ] Max attempts

#### Phase 7: Visibility

- [ ] **Logging**
  - [ ] Structured logging levels
  - [ ] Google Sheets integration
  - [ ] Trade logging
  - [ ] Safety event logging
  - [ ] Performance metrics

- [ ] **Alerting**
  - [ ] AlertService integration
  - [ ] All alert types covered
  - [ ] Correct priorities

- [ ] **Main Loop**
  - [ ] Signal handlers
  - [ ] Interruptible sleep
  - [ ] Graceful shutdown
  - [ ] Duplicate bot prevention

#### Phase 8: Configuration

- [ ] **Config Validation**
  - [ ] Required fields check
  - [ ] Environment detection
  - [ ] Credentials validation

- [ ] **Circuit Breaker Config**
  ```json
  {
      "circuit_breaker": {
          "max_consecutive_errors": 5,
          "cooldown_minutes": 5,
          "sliding_window_size": 10,
          "sliding_window_failures": 5
      }
  }
  ```

#### Phase 9: Multi-Bot Isolation (if sharing underlying)

- [ ] **Position Registry** (required if multiple bots trade same underlying)
  - [ ] Register positions on open
  - [ ] Unregister positions on close
  - [ ] Ownership verification before close
  - [ ] Filtered position recovery
  - [ ] Registry-aware reconciliation
  - [ ] Expired position cleanup
  - [ ] fcntl file locking for concurrent access

- [ ] **Owner ID Configuration**
  ```json
  {
      "position_registry": {
          "enabled": true,
          "owner_id": "iron_fly",
          "cleanup_on_startup": true
      }
  }
  ```

---

## 26. Position Registry for Multi-Bot Isolation

### Overview

When multiple bots trade the **same underlying** (e.g., MEIC + Iron Fly both trade SPX options), position management becomes complex. Without isolation:
- Bot A might try to close Bot B's positions
- Position reconciliation sees "unexpected" positions
- Recovery after restart can't determine which bot owns which position
- Circuit breaker in one bot might close another bot's positions

The **Position Registry** solves this by tracking which bot owns which position.

### Architecture

```
┌─────────────────┐     ┌─────────────────────────────────────┐
│   Iron Fly Bot  │────▶│                                     │
│   (SPX 0DTE)    │     │     Position Registry               │
├─────────────────┤     │     /opt/calypso/data/              │
│   MEIC Bot      │────▶│     position_registry.json          │
│   (SPX 0DTE)    │     │                                     │
├─────────────────┤     │  ┌─────────────────────────────┐    │
│  Delta Neutral  │     │  │ UIC 12345 → "iron_fly"      │    │
│   (SPY)         │     │  │ UIC 12346 → "iron_fly"      │    │
└─────────────────┘     │  │ UIC 12350 → "meic_entry_1"  │    │
        │               │  │ UIC 12351 → "meic_entry_1"  │    │
        │               │  │ UIC 12355 → "meic_entry_2"  │    │
        │               │  └─────────────────────────────┘    │
        │               │                                     │
        └──────────────▶│  + fcntl file locking for          │
                        │    concurrent access                │
                        └─────────────────────────────────────┘
```

### File Structure

```json
{
  "positions": {
    "12345": {
      "owner": "iron_fly",
      "registered_at": "2026-02-01T10:15:30.123456",
      "underlying": "SPX",
      "position_type": "long_call",
      "strike": 6050,
      "expiry": "2026-02-01",
      "quantity": 1
    },
    "12350": {
      "owner": "meic_entry_1",
      "registered_at": "2026-02-01T10:30:00.000000",
      "underlying": "SPX",
      "position_type": "short_call",
      "strike": 6100,
      "expiry": "2026-02-01",
      "quantity": 1
    }
  },
  "last_updated": "2026-02-01T10:30:00.000000",
  "version": "1.0"
}
```

### Implementation

```python
# shared/position_registry.py

import fcntl
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class PositionRegistry:
    """
    Thread-safe registry for tracking position ownership across multiple bots.

    Uses file-based locking (fcntl) to prevent race conditions when multiple
    bots access the registry simultaneously.
    """

    def __init__(self, data_dir: str = "/opt/calypso/data"):
        self.data_dir = Path(data_dir)
        self.registry_file = self.data_dir / "position_registry.json"
        self.lock_file = self.data_dir / "position_registry.lock"

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _acquire_lock(self, timeout: int = 10) -> int:
        """Acquire exclusive lock for registry access."""
        import time

        lock_fd = open(str(self.lock_file), "w")

        start_time = time.time()
        while True:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock_fd
            except (IOError, OSError):
                if time.time() - start_time >= timeout:
                    lock_fd.close()
                    raise TimeoutError("Could not acquire position registry lock")
                time.sleep(0.1)

    def _release_lock(self, lock_fd) -> None:
        """Release lock."""
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
        except Exception as e:
            logger.warning(f"Error releasing lock: {e}")

    def _read_registry(self) -> Dict:
        """Read registry from file."""
        if not self.registry_file.exists():
            return {"positions": {}, "last_updated": None, "version": "1.0"}

        try:
            with open(self.registry_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error reading registry: {e}")
            return {"positions": {}, "last_updated": None, "version": "1.0"}

    def _write_registry(self, registry: Dict) -> None:
        """Write registry to file."""
        registry["last_updated"] = datetime.now().isoformat()
        with open(self.registry_file, "w") as f:
            json.dump(registry, f, indent=2)

    def register_position(
        self,
        uic: int,
        owner: str,
        underlying: str,
        position_type: str,
        strike: float,
        expiry: str,
        quantity: int
    ) -> bool:
        """
        Register a position as owned by a specific bot.

        Args:
            uic: Saxo UIC (unique identifier for the option)
            owner: Bot identifier (e.g., "iron_fly", "meic_entry_1")
            underlying: Underlying symbol (e.g., "SPX", "SPY")
            position_type: Type (e.g., "long_call", "short_put")
            strike: Strike price
            expiry: Expiration date (YYYY-MM-DD)
            quantity: Number of contracts

        Returns:
            True if registered successfully, False if already owned by another bot
        """
        lock_fd = self._acquire_lock()
        try:
            registry = self._read_registry()

            uic_str = str(uic)

            # Check if already registered to another owner
            if uic_str in registry["positions"]:
                existing_owner = registry["positions"][uic_str]["owner"]
                if existing_owner != owner:
                    logger.error(
                        f"Position UIC {uic} already owned by {existing_owner}, "
                        f"cannot register for {owner}"
                    )
                    return False

            # Register the position
            registry["positions"][uic_str] = {
                "owner": owner,
                "registered_at": datetime.now().isoformat(),
                "underlying": underlying,
                "position_type": position_type,
                "strike": strike,
                "expiry": expiry,
                "quantity": quantity
            }

            self._write_registry(registry)
            logger.info(f"Registered position UIC {uic} for {owner}")
            return True

        finally:
            self._release_lock(lock_fd)

    def unregister_position(self, uic: int, owner: str) -> bool:
        """
        Unregister a position (when closed).

        Args:
            uic: Saxo UIC
            owner: Bot identifier (must match registered owner)

        Returns:
            True if unregistered, False if not found or wrong owner
        """
        lock_fd = self._acquire_lock()
        try:
            registry = self._read_registry()

            uic_str = str(uic)

            if uic_str not in registry["positions"]:
                logger.warning(f"Position UIC {uic} not in registry")
                return False

            existing_owner = registry["positions"][uic_str]["owner"]
            if existing_owner != owner:
                logger.error(
                    f"Position UIC {uic} owned by {existing_owner}, "
                    f"cannot unregister for {owner}"
                )
                return False

            del registry["positions"][uic_str]
            self._write_registry(registry)
            logger.info(f"Unregistered position UIC {uic} for {owner}")
            return True

        finally:
            self._release_lock(lock_fd)

    def get_owner(self, uic: int) -> Optional[str]:
        """Get the owner of a position."""
        lock_fd = self._acquire_lock()
        try:
            registry = self._read_registry()
            uic_str = str(uic)

            if uic_str in registry["positions"]:
                return registry["positions"][uic_str]["owner"]
            return None

        finally:
            self._release_lock(lock_fd)

    def get_positions_for_owner(self, owner: str) -> List[Dict]:
        """Get all positions registered to a specific owner."""
        lock_fd = self._acquire_lock()
        try:
            registry = self._read_registry()

            positions = []
            for uic_str, data in registry["positions"].items():
                if data["owner"] == owner:
                    positions.append({
                        "uic": int(uic_str),
                        **data
                    })

            return positions

        finally:
            self._release_lock(lock_fd)

    def is_my_position(self, uic: int, my_owner_id: str) -> bool:
        """Check if a position belongs to me."""
        owner = self.get_owner(uic)
        return owner == my_owner_id or owner is None  # None = not registered, claim it

    def cleanup_expired_positions(self) -> int:
        """Remove positions with past expiry dates."""
        lock_fd = self._acquire_lock()
        try:
            registry = self._read_registry()
            today = datetime.now().date().isoformat()

            to_remove = []
            for uic_str, data in registry["positions"].items():
                if data.get("expiry", "9999-12-31") < today:
                    to_remove.append(uic_str)

            for uic_str in to_remove:
                del registry["positions"][uic_str]

            if to_remove:
                self._write_registry(registry)
                logger.info(f"Cleaned up {len(to_remove)} expired positions")

            return len(to_remove)

        finally:
            self._release_lock(lock_fd)


# Singleton pattern for easy import
_registry_instance: Optional[PositionRegistry] = None


def get_position_registry() -> PositionRegistry:
    """Get or create the singleton PositionRegistry instance."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = PositionRegistry()
    return _registry_instance
```

### Strategy Integration

```python
# In strategy __init__
from shared.position_registry import get_position_registry

self.position_registry = get_position_registry()
self.owner_id = "iron_fly"  # or "meic_entry_1", "delta_neutral", etc.

# After opening a position
def _on_position_opened(self, position: Position) -> None:
    self.position_registry.register_position(
        uic=position.uic,
        owner=self.owner_id,
        underlying=self.underlying_symbol,
        position_type=position.position_type,
        strike=position.strike,
        expiry=position.expiry.isoformat(),
        quantity=position.quantity
    )

# Before closing a position
def _on_position_closing(self, position: Position) -> bool:
    # Verify we own this position
    if not self.position_registry.is_my_position(position.uic, self.owner_id):
        logger.error(f"Cannot close UIC {position.uic} - not owned by {self.owner_id}")
        return False
    return True

# After closing a position
def _on_position_closed(self, position: Position) -> None:
    self.position_registry.unregister_position(
        uic=position.uic,
        owner=self.owner_id
    )

# During position recovery
def recover_positions(self) -> bool:
    positions = self.client.get_positions()

    for pos in positions:
        uic = pos.get("PositionBase", {}).get("Uic")

        # Only recover positions that belong to me
        if self.position_registry.is_my_position(uic, self.owner_id):
            self._recover_single_position(pos)
        else:
            owner = self.position_registry.get_owner(uic)
            logger.info(f"Skipping UIC {uic} - owned by {owner}")
```

### Position Reconciliation Integration

```python
def check_position_reconciliation(self) -> None:
    """POS-003: Compare expected vs actual, respecting ownership."""
    # Get only MY expected positions
    expected = {}
    for pos in self.position_registry.get_positions_for_owner(self.owner_id):
        expected[pos["uic"]] = pos["quantity"]

    # Get actual positions from Saxo
    actual_positions = self.client.get_positions()

    for pos in actual_positions:
        uic = pos.get("PositionBase", {}).get("Uic")
        amount = pos.get("PositionBase", {}).get("Amount", 0)

        # Only check positions that belong to me
        if not self.position_registry.is_my_position(uic, self.owner_id):
            continue  # Not my position, skip

        if uic in expected:
            if expected[uic] != amount:
                logger.warning(f"Quantity mismatch for UIC {uic}")
        else:
            # I don't expect this position but registry says it's mine
            logger.warning(f"Unexpected position UIC {uic} in my registry")
```

### MEIC-Specific Owner IDs

For MEIC bot with 6 scheduled entries per day:

```python
# In MEIC strategy
def _get_entry_owner_id(self, entry_index: int) -> str:
    """Generate unique owner ID for each MEIC entry."""
    return f"meic_entry_{entry_index}"  # meic_entry_1, meic_entry_2, etc.
```

This allows each Iron Condor to be tracked independently.

### Cleanup

```python
# In main.py - run at end of day or on startup
def cleanup_registry():
    registry = get_position_registry()

    # Remove expired positions
    expired_count = registry.cleanup_expired_positions()
    logger.info(f"Cleaned up {expired_count} expired registry entries")

    # Verify all my registered positions still exist in Saxo
    my_positions = registry.get_positions_for_owner(my_owner_id)
    actual_uics = {
        pos.get("PositionBase", {}).get("Uic")
        for pos in client.get_positions()
    }

    for pos in my_positions:
        if pos["uic"] not in actual_uics:
            logger.warning(
                f"Registry has UIC {pos['uic']} but not in Saxo - removing"
            )
            registry.unregister_position(pos["uic"], my_owner_id)
```

### Configuration

```json
{
    "position_registry": {
        "enabled": true,
        "data_dir": "/opt/calypso/data",
        "owner_id": "iron_fly",
        "cleanup_on_startup": true
    }
}
```

### When to Use Position Registry

| Scenario | Use Registry? |
|----------|---------------|
| Single bot trading SPX | Optional (no conflicts) |
| MEIC + Iron Fly both trading SPX | **Required** |
| Delta Neutral (SPY) + Iron Fly (SPX) | Not required (different underlyings) |
| Multiple instances of same bot | **Required** (prevents duplicate trades) |

**Key Principle:** If two or more bots can see the same positions in the Saxo account, use the Position Registry to prevent conflicts.

---

## Appendix: Quick Reference

### Critical Methods

| Method | Purpose | Location |
|--------|---------|----------|
| `_open_circuit_breaker()` | Halt all trading | strategy.py:425 |
| `_emergency_position_check()` | Analyze risk before halt | strategy.py:495 |
| `_close_partial_strangle_emergency()` | Close naked short | strategy.py:590 |
| `_emergency_close_all()` | Nuclear option | strategy.py:823 |
| `check_position_reconciliation()` | Hourly position check | strategy.py:1486 |
| `recover_positions()` | Startup recovery | strategy.py:3072 |
| `check_shorts_itm_risk()` | 0.1% danger check | strategy.py:5013 |
| `get_monitoring_mode()` | Adaptive monitoring | strategy.py:5060 |
| `_validate_order_size()` | Order size validation | strategy.py (Section 24) |
| `_check_fill_slippage()` | Fill price slippage check | strategy.py (Section 24) |
| `_get_fill_from_activities_with_retry()` | Activities retry logic | strategy.py (Section 24) |
| `_check_spread_for_emergency_close()` | Spread check before emergency | strategy.py (Section 4) |
| `_emergency_close_with_retries()` | Emergency close max retries | strategy.py (Section 4) |
| `get_position_registry()` | Multi-bot position isolation | shared/position_registry.py |

### Key Configuration Values

| Config Key | Default | Purpose |
|------------|---------|---------|
| `circuit_breaker.max_consecutive_errors` | 5 | Failures before halt |
| `circuit_breaker.cooldown_minutes` | 5 | Cooldown between retries |
| `circuit_breaker.sliding_window_size` | 10 | Window for intermittent detection |
| `order_timeout_seconds` | 60 | Limit order timeout |
| `max_bid_ask_spread_percent` | 10 | Max acceptable spread |
| `fresh_entry_delay_minutes` | 30 | Opening range delay |
| `flash_crash_threshold_percent` | 2.0 | Flash crash detection |
| `order_limits.max_contracts_per_order` | 10 | Max contracts per single order |
| `order_limits.max_contracts_per_underlying` | 20 | Max total position size |
| `slippage_monitoring.warning_threshold_percent` | 5.0 | Slippage warning level |
| `slippage_monitoring.critical_threshold_percent` | 15.0 | Slippage critical level |
| `emergency_close.max_attempts` | 5 | Emergency close retries |
| `emergency_close.max_spread_percent` | 50.0 | Max spread for emergency close |
| `fill_verification.activities_retry_attempts` | 3 | Activities endpoint retries |
| `position_registry.enabled` | true | Enable multi-bot isolation |

### Alert Priority Mapping

| Event | Priority | Channels |
|-------|----------|----------|
| Circuit breaker | CRITICAL | Telegram + Email |
| Emergency close failed | CRITICAL | Telegram + Email |
| Naked position detected | CRITICAL | Telegram + Email |
| ITM risk close | CRITICAL | Telegram + Email |
| Critical slippage (>15%) | CRITICAL | Telegram + Email |
| Order size limit exceeded | CRITICAL | Telegram + Email |
| Stop loss | HIGH | Telegram + Email |
| Vigilant mode entered | HIGH | Telegram + Email |
| High slippage (5-15%) | HIGH | Telegram + Email |
| Emergency close retry | HIGH | Telegram + Email |
| Extreme spread delay | HIGH | Telegram + Email |
| Position opened | MEDIUM | Telegram + Email |
| Roll completed | MEDIUM | Telegram + Email |
| Bot started | LOW | Telegram + Email |
| Daily summary | LOW | Telegram + Email |

---

*Document generated from comprehensive analysis of the Delta Neutral bot codebase.*
*Version 1.1 - Updated 2026-02-01 with Order Size Validation, Spread Check on Emergency Close, Fill Price Slippage Check, Activities Retry Logic, Emergency Close Max Retries, and Position Registry sections.*
*For questions or updates, refer to the source files or contact the development team.*
