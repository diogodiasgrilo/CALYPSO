"""
states.py - Strategy state and position type enums

This module defines the enums used throughout the Delta Neutral strategy
for tracking position types and strategy states.
"""

from enum import Enum


class PositionType(Enum):
    """Types of positions in the strategy."""
    LONG_CALL = "LongCall"
    LONG_PUT = "LongPut"
    SHORT_CALL = "ShortCall"
    SHORT_PUT = "ShortPut"


class StrategyState(Enum):
    """
    States of the trading strategy.

    State Machine Flow:
    - IDLE: No positions, waiting for entry conditions
    - WAITING_VIX: VIX too high, waiting for VIX < threshold
    - LONG_STRADDLE_ACTIVE: Long straddle entered, no short strangle yet
    - FULL_POSITION: Both long straddle and short strangle active
    - RECENTERING: In process of 5-point recentering
    - ROLLING_SHORTS: Rolling weekly short strangle
    - EXITING: Closing all positions (end of trade or emergency)
    """
    IDLE = "Idle"
    WAITING_VIX = "WaitingForVIX"
    LONG_STRADDLE_ACTIVE = "LongStraddleActive"
    FULL_POSITION = "FullPosition"
    RECENTERING = "Recentering"
    ROLLING_SHORTS = "RollingShorts"
    EXITING = "Exiting"
