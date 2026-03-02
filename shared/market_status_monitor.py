#!/usr/bin/env python3
"""
Market Status Monitor (2026-01-27)

Monitors market status and sends Telegram/Email alerts for:
- Market opening countdown (1h, 30m, 15m before open)
- Market open notification (at exactly 9:30 AM ET, not before)
- Market close notification (at 4:00 PM ET or 1:00 PM on early close days)
- Holiday notifications (weekday closures)
- Early close day warnings

IMPORTANT: This should run on ONLY ONE BOT (Delta Neutral) to avoid duplicate alerts.
Other bots (Iron Fly, Rolling Put Diagonal, MEIC) should NOT initialize MarketStatusMonitor.

Bug Fixes:
- 2026-01-26: Fixed premature market open alert at 9:29 AM caused by int() truncation
  (int(-0.5) = 0 in Python, which passed the "0 <= 0 <= 5" check)
- 2026-01-27: Widened countdown alert window from ±2 to ±8 minutes to ensure alerts
  are sent even with 15-minute sleep cycles during pre-market

Usage:
    from shared.market_status_monitor import MarketStatusMonitor

    # Initialize ONCE per system (on Delta Neutral only)
    monitor = MarketStatusMonitor(alert_service)

    # Call periodically from main loop (every 1-5 minutes)
    monitor.check_and_alert()
"""

import logging
from datetime import datetime, date, time, timedelta
from typing import Optional, Set, Dict, Any

from shared.market_hours import (
    get_us_market_time,
    is_market_open,
    is_weekend,
    is_market_holiday,
    get_holiday_name,
    is_early_close_day,
    get_early_close_reason,
    get_market_close_time,
    get_next_market_open,
    MARKET_OPEN_TIME,
    MARKET_CLOSE_TIME,
    EARLY_CLOSE_TIME,
    US_EASTERN,
)
from shared.alert_service import AlertService

logger = logging.getLogger(__name__)


class MarketStatusMonitor:
    """
    Monitors market status and sends alerts at key times.

    Alerts are sent once per day/event and tracked to avoid duplicates.
    """

    # Alert timing thresholds (minutes before market open)
    COUNTDOWN_THRESHOLDS = [60, 30, 15]  # 1 hour, 30 min, 15 min

    def __init__(self, alert_service: AlertService):
        """
        Initialize the market status monitor.

        Args:
            alert_service: AlertService instance for sending alerts
        """
        self.alert_service = alert_service
        self._sent_alerts: Set[str] = set()  # Track sent alerts
        self._last_check_date: Optional[date] = None

    def _get_alert_key(self, alert_type: str, date_str: str) -> str:
        """Generate a unique key for tracking sent alerts."""
        return f"{alert_type}:{date_str}"

    def _mark_sent(self, alert_type: str, dt: datetime = None) -> None:
        """Mark an alert as sent for the given date."""
        if dt is None:
            dt = get_us_market_time()
        key = self._get_alert_key(alert_type, dt.strftime("%Y-%m-%d"))
        self._sent_alerts.add(key)

    def _was_sent(self, alert_type: str, dt: datetime = None) -> bool:
        """Check if an alert was already sent for the given date."""
        if dt is None:
            dt = get_us_market_time()
        key = self._get_alert_key(alert_type, dt.strftime("%Y-%m-%d"))
        return key in self._sent_alerts

    def _reset_daily(self, dt: datetime) -> None:
        """Reset sent alerts tracking on new day."""
        current_date = dt.date()
        if self._last_check_date != current_date:
            # Keep alerts from previous day for a bit, then clear
            self._sent_alerts.clear()
            self._last_check_date = current_date
            logger.debug(f"Market monitor: Reset alerts for new day {current_date}")

    def check_and_alert(self) -> Dict[str, bool]:
        """
        Check market status and send appropriate alerts.

        This should be called periodically (every 1-5 minutes) from the bot's main loop.

        Returns:
            Dict of alert types and whether they were sent
        """
        now = get_us_market_time()
        self._reset_daily(now)

        results = {}

        # Check each alert type
        results["holiday"] = self._check_holiday(now)
        results["early_close"] = self._check_early_close(now)
        results["opening_countdown"] = self._check_opening_countdown(now)
        results["market_open"] = self._check_market_open(now)
        results["market_closed"] = self._check_market_closed(now)

        return results

    def _check_holiday(self, now: datetime) -> bool:
        """Check and alert if today is a market holiday."""
        # Only check on weekdays
        if is_weekend(now):
            return False

        # Already sent?
        if self._was_sent("holiday", now):
            return False

        # Is it a holiday?
        holiday_name = get_holiday_name(now)
        if not holiday_name:
            return False

        # Get next market open
        next_open, _ = get_next_market_open()
        next_open_str = next_open.strftime("%A, %B %d")

        # Send alert
        logger.info(f"Market monitor: Sending holiday alert - {holiday_name}")
        self.alert_service.market_holiday(
            holiday_name=holiday_name,
            next_open_date=next_open_str,
            details={"today": now.strftime("%Y-%m-%d")}
        )
        self._mark_sent("holiday", now)
        return True

    def _check_early_close(self, now: datetime) -> bool:
        """Check and alert if today is an early close day."""
        # Only alert in the morning before market opens
        if now.time() >= MARKET_OPEN_TIME:
            return False

        # Already sent?
        if self._was_sent("early_close", now):
            return False

        # Is it an early close day?
        reason = get_early_close_reason(now)
        if not reason:
            return False

        # Send alert
        logger.info(f"Market monitor: Sending early close alert - {reason}")
        self.alert_service.market_early_close(
            reason=reason,
            close_time="1:00 PM ET",
            details={"regular_close": "4:00 PM ET"}
        )
        self._mark_sent("early_close", now)
        return True

    def _check_opening_countdown(self, now: datetime) -> bool:
        """Check and send countdown alerts (1h, 30m, 15m before open)."""
        # Weekend or holiday - no countdown
        if is_weekend(now) or is_market_holiday(now):
            return False

        # Already past market open
        if now.time() >= MARKET_OPEN_TIME:
            return False

        # Calculate minutes until market open
        market_open_dt = datetime.combine(now.date(), MARKET_OPEN_TIME)
        market_open_dt = US_EASTERN.localize(market_open_dt)
        minutes_until_open = int((market_open_dt - now).total_seconds() / 60)

        # Check each threshold
        for threshold in self.COUNTDOWN_THRESHOLDS:
            alert_key = f"countdown_{threshold}"

            # Already sent this countdown?
            if self._was_sent(alert_key, now):
                continue

            # Within threshold window? (threshold +/- 8 minutes)
            # Window is wide enough to catch alerts even with 15-minute sleep cycles
            if threshold - 8 <= minutes_until_open <= threshold + 8:
                logger.info(f"Market monitor: Sending {threshold}min countdown alert")
                self.alert_service.market_opening_soon(
                    minutes_until_open=threshold,
                    current_time=now.strftime("%I:%M %p ET"),
                    details={
                        "market_open_time": "9:30 AM ET",
                        "is_early_close": is_early_close_day(now)
                    }
                )
                self._mark_sent(alert_key, now)
                return True

        return False

    def _check_market_open(self, now: datetime) -> bool:
        """Check and alert when market opens."""
        # Weekend or holiday
        if is_weekend(now) or is_market_holiday(now):
            return False

        # Already sent?
        if self._was_sent("market_open", now):
            return False

        # Check if we just passed market open (within 5 minutes)
        market_open_dt = datetime.combine(now.date(), MARKET_OPEN_TIME)
        market_open_dt = US_EASTERN.localize(market_open_dt)

        # IMPORTANT: Must check now >= market_open_dt FIRST before using int()
        # because int() truncates toward zero: int(-0.5) = 0, not -1
        # This caused premature alerts at 9:29:30 AM (bug fixed 2026-01-26)
        if now < market_open_dt:
            return False

        seconds_since_open = (now - market_open_dt).total_seconds()
        minutes_since_open = int(seconds_since_open / 60)

        if minutes_since_open <= 5:
            logger.info("Market monitor: Sending market open alert")
            self.alert_service.market_open(
                current_time=now.strftime("%I:%M %p ET"),
                details={
                    "is_early_close": is_early_close_day(now),
                    "close_time": get_market_close_time(now).strftime("%I:%M %p") + " ET"
                }
            )
            self._mark_sent("market_open", now)
            return True

        return False

    def _check_market_closed(self, now: datetime) -> bool:
        """Check and alert when market closes."""
        # Weekend or holiday
        if is_weekend(now) or is_market_holiday(now):
            return False

        # Already sent?
        if self._was_sent("market_closed", now):
            return False

        # Get today's close time (may be early close)
        close_time = get_market_close_time(now)
        market_close_dt = datetime.combine(now.date(), close_time)
        market_close_dt = US_EASTERN.localize(market_close_dt)

        # Check if we just passed market close (within 5 minutes)
        # IMPORTANT: Must check now >= market_close_dt FIRST before using int()
        # because int() truncates toward zero: int(-0.5) = 0, not -1
        if now < market_close_dt:
            return False

        seconds_since_close = (now - market_close_dt).total_seconds()
        minutes_since_close = int(seconds_since_close / 60)

        if minutes_since_close <= 5:
            logger.info("Market monitor: Sending market closed alert")

            # Get next market open
            next_open, _ = get_next_market_open()

            self.alert_service.market_closed(
                current_time=now.strftime("%I:%M %p ET"),
                details={
                    "close_type": "Early close" if is_early_close_day(now) else "Regular close",
                    "next_open": next_open.strftime("%A %I:%M %p ET")
                }
            )
            self._mark_sent("market_closed", now)
            return True

        return False


# Test function
if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("MARKET STATUS MONITOR TEST")
    print("=" * 70)

    # Create mock alert service for testing
    from unittest.mock import Mock

    mock_alert = Mock()

    # Create monitor
    monitor = MarketStatusMonitor(mock_alert)

    # Run check
    print("\nRunning market status check...")
    results = monitor.check_and_alert()

    print(f"\nResults: {results}")

    # Show current status
    now = get_us_market_time()
    print(f"\nCurrent time: {now.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")
    print(f"Is weekend: {is_weekend(now)}")
    print(f"Is holiday: {is_market_holiday(now)}")
    holiday = get_holiday_name(now)
    if holiday:
        print(f"Holiday name: {holiday}")
    print(f"Is early close: {is_early_close_day(now)}")
    early_reason = get_early_close_reason(now)
    if early_reason:
        print(f"Early close reason: {early_reason}")
    print(f"Is market open: {is_market_open(now)}")
    print(f"Close time today: {get_market_close_time(now)}")

    # Check if any alerts were "sent"
    print(f"\nMock alert calls: {mock_alert.method_calls}")

    print("\n" + "=" * 70)
