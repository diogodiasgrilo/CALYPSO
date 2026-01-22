"""
safety - Safety mechanisms documentation for Delta Neutral strategy

This package documents the safety architecture of the Delta Neutral bot.
The actual implementation remains in strategy.py due to tight coupling,
but this documentation helps future developers understand the safety systems.

For detailed edge case analysis, see: docs/DELTA_NEUTRAL_EDGE_CASES.md

=============================================================================
SAFETY ARCHITECTURE OVERVIEW (42 Edge Cases - 100% Coverage)
=============================================================================

1. CIRCUIT BREAKER (strategy.py ~1053-1127)
   - _increment_failure_count(): Track consecutive failures
   - _reset_failure_count(): Reset after success
   - _open_circuit_breaker(): Halt trading after MAX failures
   - _check_circuit_breaker(): Check if halted (consecutive OR sliding window)
   - reset_circuit_breaker(): Manual reset

   Behavior:
   - Opens after 5 consecutive failures (configurable via max_consecutive_errors)
   - ALSO opens if 5 of last 10 operations failed (CONN-002 sliding window)
   - Auto-resets after cooldown IF positions are safe
   - Requires manual intervention for unsafe states

2. CRITICAL INTERVENTION (strategy.py ~1285-1384) [ORDER-004]
   - _set_critical_intervention(): Set flag after MARKET order failure
   - _check_critical_intervention(): Block all operations until manual reset
   - _clear_critical_intervention(): Manual reset after human review

   This is MORE SEVERE than circuit breaker - when a MARKET order fails
   during an emergency, trading is completely halted until human reviews.

3. EMERGENCY HANDLERS (strategy.py ~334-796)
   - _emergency_position_check(): Analyze risk before halting
   - _close_partial_strangle_emergency(): Close naked short
   - _close_short_strangle_emergency(): Close all shorts
   - _emergency_close_all(): Nuclear option - close everything
   - _close_partial_straddle_emergency(): Close partial longs

   Scenarios handled:
   - SCENARIO 1: Partial strangle + complete straddle -> close naked short
   - SCENARIO 2: Partial straddle + any shorts -> close ALL (dangerous!)
   - SCENARIO 3: Only shorts, no longs -> close all shorts
   - SCENARIO 4: Complete straddle -> safe, keep everything
   - SCENARIO 5: Partial straddle only -> keep (limited risk)
   - SCENARIO 6: No positions -> nothing to do

4. PARTIAL FILL FALLBACKS (strategy.py ~797-1052)
   - _handle_strangle_partial_fill_fallback(): Close naked short, keep straddle
   - _handle_straddle_partial_fill_fallback(): Go FLAT (close all)

   Progressive retry sequence (before fallback):
   - 0% slippage x2 attempts
   - 5% slippage x2 attempts
   - 10% slippage x2 attempts
   - MARKET order (final attempt)
   - If MARKET fails -> trigger fallback

   Principle:
   - Strangle is expendable (close it, keep straddle safe)
   - Straddle is critical (if compromised with shorts -> go FLAT)

5. COOLDOWN SYSTEM (strategy.py ~1191-1235)
   - _is_action_on_cooldown(): Check if action should be skipped
   - _set_action_cooldown(): Set cooldown after failure
   - _clear_action_cooldown(): Clear after success

   Prevents rapid retry loops after failures.

6. ORPHANED ORDER TRACKING (strategy.py ~1148-1188)
   - _add_orphaned_order(): Track orders that couldn't be cancelled
   - _check_for_orphaned_orders(): Check before new operations

   Blocks trading until orphaned orders are resolved.

7. ITM RISK DETECTION (strategy.py ~3656-3700)
   - check_shorts_itm_risk(): Monitors short strikes vs SPY price
   - Triggers emergency roll if price approaches strike
   - Uses percentage-based threshold (default 0.5%)

=============================================================================
EDGE CASE HANDLERS (Added 2026-01-22)
=============================================================================

CONNECTION/API (CONN-*)
-----------------------
CONN-002: Intermittent API Errors (strategy.py ~1053)
   - Sliding window counter: 5 failures in last 10 operations -> circuit breaker
   - Instance variables: _recent_operations (deque of last 10 bool results)

CONN-004: Token Expires Mid-Operation (saxo_client.py ~860)
   - 401 detection in _make_request()
   - Automatic token refresh via authenticate(force_refresh=True)
   - Retries original request after refresh

CONN-005: Network Timeout Confirmation (strategy.py ~2827-2900)
   - _verify_position_exists(): Verify single position after order
   - _verify_positions_after_order(): Verify all legs after multi-leg order
   - Can be called to confirm fills after timeout scenarios

CONN-006: Rate Limiting (saxo_client.py ~860)
   - 429 detection with exponential backoff (1s, 2s, 4s, 8s, 16s)
   - Respects Retry-After header if present
   - Max 5 retries before failing

ORDER EXECUTION (ORDER-*)
-------------------------
ORDER-004: MARKET Order Failure (strategy.py ~1285-1384)
   - Sets _critical_intervention_required flag
   - Logs safety event with position state
   - Blocks ALL trading until manual reset

ORDER-005: Wide Bid-Ask Spread (strategy.py ~2628-2660)
   - Max absolute slippage check before MARKET orders
   - Default $0.50 max spread
   - Aborts MARKET order if spread too wide, falls back to emergency handler

ORDER-007: Order Rejection Handling (strategy.py ~2721-2760)
   - Explicit rejection detection (vs timeout)
   - Partial fill tracking for each leg
   - Returns detailed rejection status to calling code

POSITION STATE (POS-*)
----------------------
POS-002: Manual Intervention Detection (strategy.py ~2056-2140)
   - verify_positions_before_operation(): Query Saxo before any modification
   - Compares expected positions vs actual
   - Triggers recover_positions() if discrepancy found

POS-003: Early Assignment Detection (strategy.py ~1386-1500)
   - check_position_reconciliation(): Hourly comparison vs Saxo reality
   - Tracks _expected_positions dict
   - Sets critical intervention on unexpected position changes

POS-004: Expiration Handling (strategy.py ~1670-1755)
   - check_expired_positions(): Proactive expiry check at start of day
   - Clears position objects when expiry passed
   - Transitions state FULL_POSITION -> LONG_STRADDLE_ACTIVE

POS-006: Multiple Straddles Warning (strategy.py ~3811-3830)
   - Check in _recover_long_straddle_with_tracking()
   - Logs warning if multiple straddle candidates found
   - Lists all candidates so operator knows which are orphaned

MARKET CONDITIONS (MKT-*)
-------------------------
MKT-001: Pre-Market Gap Detection (strategy.py ~1503-1625)
   - check_pre_market_gap(): Uses Yahoo Finance for pre-market price
   - Compares against previous close
   - Warns if gap > 2% (configurable)
   - update_previous_close(): Call at market close to store reference

MKT-002: Flash Crash Velocity (strategy.py ~1756-1840)
   - _record_price_for_velocity(): Add price to sliding window
   - check_flash_crash_velocity(): Detect 2%+ moves in 5 minutes
   - Returns move direction and threatened strikes
   - Triggers urgent ITM check when detected

MKT-003: VIX Spike Mid-Trade
   - BY DESIGN: VIX check only blocks NEW entries
   - Long straddle benefits from high VIX
   - Documented as intentional per strategy design

MKT-004: Market Halt Detection (strategy.py ~2142-2188)
   - _check_market_halt_pattern(): Error message pattern matching
   - Checks for "trading halt", "market closed", "suspended", "circuit breaker"
   - Triggered after 3 consecutive failures
   - Logs critical warning if halt suspected

MKT-005: No Liquidity Handling (strategy.py ~2190-2228)
   - _log_no_valid_strikes_error(): Explicit error logging
   - Includes operation, reason, price context
   - Logs safety event for tracking

TIMING/RACE CONDITIONS (TIME-*)
-------------------------------
TIME-001: Operation Lock (strategy.py ~213, ~8329-8350)
   - _operation_lock_time: Prevents concurrent strategy checks
   - Logs warning if operation already in progress
   - 60-second timeout

TIME-003: Half-Day Closures (strategy.py ~1844-1975)
   - is_early_close_day(): Detect 1pm close days
   - get_market_close_time_today(): Returns actual close time
   - _is_past_early_close(): Block operations after 12:45pm
   - check_early_close_warning(): Log warning at market open

TIME-004: Roll + Recenter Same Day (strategy.py ~1978-2055)
   - _handle_recenter_failure_on_roll_day(): Skip roll, let shorts expire
   - _mark_recenter_failed_on_roll_day(): Set flag for later handling
   - Prevents compounding failures by not attempting roll with misaligned positions

STATE MACHINE (STATE-*)
-----------------------
STATE-002: State/Position Consistency (strategy.py ~1239-1284)
   - check_state_position_consistency(): Verify state matches position objects
   - Run at start of _run_strategy_check_impl()
   - Triggers recover_positions() if mismatch found

DATA INTEGRITY (DATA-*)
-----------------------
DATA-001: Stale Quote Detection (strategy.py ~2230-2280)
   - _validate_quote_freshness(): Check quote timestamps
   - Default max age: 60 seconds
   - Returns warning if quote older than threshold

DATA-002: Missing Greeks Warning (strategy.py ~2281-2310)
   - _warn_missing_greeks(): Check for Delta, Theta, Gamma, Vega
   - Logs warning with position type and strike
   - Still uses defaults but alerts operator

DATA-003: Option Chain Validation (strategy.py ~2312-2365)
   - _validate_option_chain(): Comprehensive chain validation
   - Checks: not empty, min options, valid bid/ask, strike range
   - Returns (is_valid, reason) tuple

=============================================================================
KEY SAFETY PRINCIPLES
=============================================================================

1. ALWAYS sync with Saxo before emergency actions (positions may be stale)
2. Close shorts FIRST in emergencies (unlimited risk)
3. Complete straddle = safe hedge, can keep running
4. Partial positions = dangerous, may need intervention
5. Circuit breaker protects against cascading failures
6. Progressive retry gives orders best chance to fill
7. MARKET orders used only for emergencies (unlimited risk positions)
8. Critical intervention halts EVERYTHING until human reviews
9. Multiple layers of protection - circuit breaker + intervention + cooldowns
10. Proactive checks (expiry, gap, flash crash) catch issues before they escalate

=============================================================================
SAFETY CHECK ORDER IN run_strategy_check() (~8350+)
=============================================================================

1. TIME-001: Operation lock check (prevent concurrent runs)
2. ORDER-004: Critical intervention check (most severe)
3. POS-004: Expired positions check (proactive cleanup)
4. TIME-003: Early close warning (once per day)
5. STATE-002: State/position consistency check
6. Circuit breaker check (auto-reset if safe)
7. Market data update (SPY, VIX)
8. MKT-002: Flash crash velocity check
9. TIME-003: Early close cutoff check (block operations)
10. Normal strategy logic proceeds...

=============================================================================
Last Updated: 2026-01-22
=============================================================================
"""

# This package is primarily for documentation.
# Safety methods are implemented in strategy.py due to tight coupling.

__all__ = []
