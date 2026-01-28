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


class MonitoringMode(Enum):
    """
    Monitoring frequency modes for ITM risk detection.

    Adaptive vigilant monitoring system (Updated 2026-01-28):
    - NORMAL: 10-second check interval (< 60% of original cushion consumed)
    - VIGILANT: 1-second monitoring (60-75% cushion consumed)

    Thresholds are adaptive — they scale with the original distance from entry
    price to short strikes. Falls back to static 0.5% from strike if
    entry_underlying_price is unavailable.

    When price enters VIGILANT zone, we watch closely. The challenged roll
    trigger (75% cushion consumed) fires from should_roll_shorts().
    Emergency close at 0.1% from strike (absolute safety floor, stays static).

    Note: Both intervals are safe because price data comes from WebSocket cache
    (no API calls), eliminating rate limit concerns. Reduced NORMAL from 30s to 10s
    for faster detection of price movements toward short strikes.
    """
    NORMAL = 10      # 10 seconds between checks (was 30s, reduced with WebSocket fix)
    VIGILANT = 1     # 1 second between checks (watching closely, uses cached price)
