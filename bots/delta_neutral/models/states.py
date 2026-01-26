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

    Vigilant monitoring system (2026-01-26: optimized for WebSocket streaming):
    - NORMAL: 10-second check interval (> 0.3% from strike)
    - VIGILANT: 1-second monitoring (0.1% - 0.3% from strike)

    When price enters VIGILANT zone, we watch closely but don't act.
    This avoids unnecessary closes when price bounces back.
    Only close when price actually reaches 0.1% (DANGER zone).

    Note: Both intervals are safe because price data comes from WebSocket cache
    (no API calls), eliminating rate limit concerns. Reduced NORMAL from 30s to 10s
    for faster detection of price movements toward short strikes.
    """
    NORMAL = 10      # 10 seconds between checks (was 30s, reduced with WebSocket fix)
    VIGILANT = 1     # 1 second between checks (watching closely, uses cached price)
