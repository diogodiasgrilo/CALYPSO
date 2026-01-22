"""
safety - Safety mechanisms documentation for Delta Neutral strategy

This package documents the safety architecture of the Delta Neutral bot.
The actual implementation remains in strategy.py due to tight coupling,
but this documentation helps future developers understand the safety systems.

=============================================================================
SAFETY ARCHITECTURE OVERVIEW
=============================================================================

1. CIRCUIT BREAKER (strategy.py methods starting with _increment_failure)
   - _increment_failure_count(): Track consecutive failures
   - _reset_failure_count(): Reset after success
   - _open_circuit_breaker(): Halt trading after MAX failures
   - _check_circuit_breaker(): Check if trading is halted
   - reset_circuit_breaker(): Manual reset

   Behavior:
   - Opens after 3 consecutive failures (configurable)
   - Auto-resets after cooldown IF positions are safe
   - Requires manual intervention for unsafe states

2. EMERGENCY HANDLERS (strategy.py methods starting with _emergency or _close_*_emergency)
   - _emergency_position_check(): Analyze risk before halting
   - _close_partial_strangle_emergency(): Close naked short
   - _close_short_strangle_emergency(): Close all shorts
   - _emergency_close_all(): Nuclear option - close everything
   - _close_partial_straddle_emergency(): Close partial longs

   Scenarios handled:
   - SCENARIO 1: Partial strangle + complete straddle → close naked short
   - SCENARIO 2: Partial straddle + any shorts → close ALL (dangerous!)
   - SCENARIO 3: Only shorts, no longs → close all shorts
   - SCENARIO 4: Complete straddle → safe, keep everything
   - SCENARIO 5: Partial straddle only → keep (limited risk)
   - SCENARIO 6: No positions → nothing to do

3. PARTIAL FILL FALLBACKS (strategy.py methods _handle_*_partial_fill_fallback)
   - _handle_strangle_partial_fill_fallback(): Close naked short, keep straddle
   - _handle_straddle_partial_fill_fallback(): Go FLAT (close all)

   Progressive retry sequence (before fallback):
   - 0% slippage x2 attempts
   - 5% slippage x2 attempts
   - 10% slippage x2 attempts
   - MARKET order (final attempt)
   - If MARKET fails → trigger fallback

   Principle:
   - Strangle is expendable (close it, keep straddle safe)
   - Straddle is critical (if compromised with shorts → go FLAT)

4. COOLDOWN SYSTEM (strategy.py methods _is_action_on_cooldown, _set_action_cooldown)
   - _is_action_on_cooldown(): Check if action should be skipped
   - _set_action_cooldown(): Set cooldown after failure
   - _clear_action_cooldown(): Clear after success

   Prevents rapid retry loops after failures.

5. ORPHANED ORDER TRACKING (strategy.py methods _add_orphaned_order, _check_for_orphaned_orders)
   - _add_orphaned_order(): Track orders that couldn't be cancelled
   - _check_for_orphaned_orders(): Check before new operations

   Blocks trading until orphaned orders are resolved.

6. ITM RISK DETECTION (strategy.py method check_shorts_itm_risk)
   - Monitors short strikes vs SPY price
   - Triggers emergency roll if price approaches strike
   - Uses percentage-based threshold (default 0.5%)

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

=============================================================================
"""

# This package is primarily for documentation.
# Safety methods are implemented in strategy.py due to tight coupling.

__all__ = []
