"""
MEIC State Machine and Position States.

This module defines the state machine for the MEIC trading strategy.
Extracted from strategy.py for better code organization.
"""

from enum import Enum


class MEICState(Enum):
    """
    States of the MEIC strategy state machine.

    State transitions:
    IDLE -> WAITING_FIRST_ENTRY (9:30 AM)
    WAITING_FIRST_ENTRY -> ENTRY_IN_PROGRESS (10:00 AM)
    ENTRY_IN_PROGRESS -> MONITORING (after entry completes)
    MONITORING -> ENTRY_IN_PROGRESS (next scheduled entry time)
    MONITORING -> STOP_TRIGGERED (price hits stop level)
    MONITORING -> DAILY_COMPLETE (all entries done, end of day)
    STOP_TRIGGERED -> MONITORING (stop processed)
    Any -> CIRCUIT_BREAKER (too many failures)
    Any -> HALTED (critical intervention required)
    """
    IDLE = "Idle"                           # No position, waiting for market open
    WAITING_FIRST_ENTRY = "WaitingFirstEntry"  # Market open, waiting for 10:00 AM
    ENTRY_IN_PROGRESS = "EntryInProgress"   # Currently placing an IC entry
    MONITORING = "Monitoring"               # Active ICs, watching for stops
    STOP_TRIGGERED = "StopTriggered"        # Processing a stop loss
    DAILY_COMPLETE = "DailyComplete"        # All done for today
    CIRCUIT_BREAKER = "CircuitBreaker"      # Too many failures, cooling down
    HALTED = "Halted"                       # Critical error, manual intervention required
