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
    - FOMC_BLACKOUT: FOMC day with no positions - trading blocked all day
    - LONG_STRADDLE_ACTIVE: Long straddle entered, no short strangle yet
    - FULL_POSITION: Both long straddle and short strangle active
    - RECENTERING: In process of 5-point recentering
    - ROLLING_SHORTS: Rolling weekly short strangle
    - EXITING: Closing all positions (end of trade or emergency)
    """
    IDLE = "Idle"
    WAITING_VIX = "WaitingForVIX"
    FOMC_BLACKOUT = "FOMCBlackout"
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
    - VIGILANT: 2-second monitoring (60-75% cushion consumed)
    - FOMC_BLACKOUT: 3600-second (1 hour) heartbeat when in FOMC blackout with no positions

    Thresholds are adaptive — they scale with the original distance from entry
    price to short strikes. Falls back to static 0.5% from strike if
    entry_underlying_price is unavailable.

    When price enters VIGILANT zone, we watch closely. The challenged roll
    trigger (75% cushion consumed) fires from should_roll_shorts().
    Emergency close at 0.1% from strike (absolute safety floor, stays static).

    Rate Limit Consideration (2026-01-28):
    - Switched from WebSocket to REST-only for price fetching (more reliable)
    - VIGILANT uses 2s interval (30 calls/min) instead of 1s (60 calls/min)
    - With 3 bots max: worst case 90 calls/min (75% of 120/min limit)
    - REST API provides guaranteed fresh prices for order placement

    FOMC_BLACKOUT mode is used when:
    - It's an FOMC meeting day (within blackout period)
    - The bot has no open positions
    - Trading is blocked for the entire day
    This saves resources by only checking hourly for heartbeat/state changes.
    """
    NORMAL = 10      # 10 seconds between checks (6 calls/min per bot)
    VIGILANT = 2     # 2 seconds between checks (30 calls/min per bot, REST API)
    FOMC_BLACKOUT = 3600  # 1 hour between checks when in FOMC blackout with no positions
