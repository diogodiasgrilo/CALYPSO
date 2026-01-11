#!/usr/bin/env python3
"""
Market Hours Module

Provides utilities for checking if US equity markets are open.
Handles weekends, holidays, and market hours (9:30 AM - 4:00 PM ET).
"""

import logging
from datetime import datetime, time
from typing import Tuple
import pytz

logger = logging.getLogger(__name__)

# US Market timezone
US_EASTERN = pytz.timezone('America/New_York')

# Regular market hours (US equity markets)
MARKET_OPEN_TIME = time(9, 30)   # 9:30 AM ET
MARKET_CLOSE_TIME = time(16, 0)  # 4:00 PM ET


def get_us_market_time() -> datetime:
    """
    Get current time in US Eastern timezone.

    Returns:
        datetime: Current time in US Eastern timezone
    """
    return datetime.now(US_EASTERN)


def is_weekend(dt: datetime = None) -> bool:
    """
    Check if the given datetime is a weekend.

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        bool: True if Saturday (5) or Sunday (6)
    """
    if dt is None:
        dt = get_us_market_time()

    # weekday() returns 0=Monday, 6=Sunday
    return dt.weekday() >= 5


def is_market_holiday(dt: datetime = None) -> bool:
    """
    Check if the given date is a US market holiday.

    Note: This is a simplified list of major holidays.
    For production, consider using a market calendar library.

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        bool: True if it's a market holiday
    """
    if dt is None:
        dt = get_us_market_time()

    # Major US market holidays (approximate - some are floating dates)
    # This is a basic implementation
    year = dt.year
    month = dt.month
    day = dt.day

    # Fixed holidays
    if (month == 1 and day == 1):  # New Year's Day
        return True
    if (month == 7 and day == 4):  # Independence Day
        return True
    if (month == 12 and day == 25):  # Christmas
        return True

    # TODO: Add floating holidays (MLK Day, Presidents Day, Good Friday,
    # Memorial Day, Labor Day, Thanksgiving)
    # For now, we'll just handle weekends and major fixed holidays

    return False


def is_within_market_hours(dt: datetime = None) -> bool:
    """
    Check if the given time is within regular market hours (9:30 AM - 4:00 PM ET).

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        bool: True if within market hours
    """
    if dt is None:
        dt = get_us_market_time()

    current_time = dt.time()
    return MARKET_OPEN_TIME <= current_time < MARKET_CLOSE_TIME


def is_market_open(dt: datetime = None) -> bool:
    """
    Check if US equity markets are currently open.

    Markets are open Monday-Friday, 9:30 AM - 4:00 PM ET,
    excluding holidays.

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        bool: True if markets are open
    """
    if dt is None:
        dt = get_us_market_time()

    # Check weekend
    if is_weekend(dt):
        return False

    # Check holiday
    if is_market_holiday(dt):
        return False

    # Check market hours
    if not is_within_market_hours(dt):
        return False

    return True


def get_next_market_open() -> Tuple[datetime, int]:
    """
    Get the next market open time and seconds until then.

    Returns:
        tuple: (next_open_datetime, seconds_until_open)
    """
    now = get_us_market_time()
    current_date = now.date()

    # If currently within market hours, market is already open
    if is_market_open(now):
        return now, 0

    # Calculate next market open
    # Start with today at market open time
    next_open = datetime.combine(current_date, MARKET_OPEN_TIME)
    next_open = US_EASTERN.localize(next_open)

    # If we're past today's market hours, start from tomorrow
    if now.time() >= MARKET_CLOSE_TIME:
        next_open = next_open.replace(day=next_open.day + 1)

    # Skip weekends and holidays
    max_attempts = 10  # Prevent infinite loop
    attempts = 0
    while (is_weekend(next_open) or is_market_holiday(next_open)) and attempts < max_attempts:
        next_open = next_open.replace(day=next_open.day + 1)
        attempts += 1

    # Calculate seconds until market open
    seconds_until_open = int((next_open - now).total_seconds())

    return next_open, seconds_until_open


def get_market_status_message() -> str:
    """
    Get a human-readable market status message.

    Returns:
        str: Status message describing market state
    """
    now = get_us_market_time()

    if is_market_open(now):
        return f"✅ Market OPEN | {now.strftime('%A, %Y-%m-%d %I:%M:%S %p %Z')}"

    # Market is closed - figure out why
    if is_weekend(now):
        next_open, seconds = get_next_market_open()
        hours = seconds // 3600
        return (
            f"🔴 Market CLOSED (Weekend) | "
            f"{now.strftime('%A, %Y-%m-%d %I:%M:%S %p %Z')} | "
            f"Opens in {hours} hours ({next_open.strftime('%A at %I:%M %p %Z')})"
        )

    if is_market_holiday(now):
        next_open, seconds = get_next_market_open()
        hours = seconds // 3600
        return (
            f"🔴 Market CLOSED (Holiday) | "
            f"{now.strftime('%A, %Y-%m-%d %I:%M:%S %p %Z')} | "
            f"Opens in {hours} hours ({next_open.strftime('%A at %I:%M %p %Z')})"
        )

    if now.time() < MARKET_OPEN_TIME:
        next_open, seconds = get_next_market_open()
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return (
            f"🔴 Market CLOSED (Pre-market) | "
            f"{now.strftime('%A, %Y-%m-%d %I:%M:%S %p %Z')} | "
            f"Opens in {hours}h {minutes}m"
        )

    # After market hours
    next_open, seconds = get_next_market_open()
    hours = seconds // 3600
    return (
        f"🔴 Market CLOSED (After-hours) | "
        f"{now.strftime('%A, %Y-%m-%d %I:%M:%S %p %Z')} | "
        f"Opens in {hours} hours ({next_open.strftime('%A at %I:%M %p %Z')})"
    )


def calculate_sleep_duration(max_sleep: int = 3600) -> int:
    """
    Calculate intelligent sleep duration based on market status.

    If market is closed, sleep until closer to market open (up to max_sleep).
    If market is open, return 0 (use normal check interval).

    Args:
        max_sleep: Maximum sleep duration in seconds (default 1 hour)

    Returns:
        int: Recommended sleep duration in seconds
    """
    if is_market_open():
        return 0  # Market is open, use normal check interval

    # Market is closed - sleep until closer to open
    next_open, seconds_until_open = get_next_market_open()

    # If market opens in less than 1 hour, wake up 5 minutes before
    if seconds_until_open < 3600:
        sleep_time = max(0, seconds_until_open - 300)  # Wake 5 min before
        return min(sleep_time, max_sleep)

    # Otherwise, sleep for up to max_sleep (1 hour by default)
    # Will recheck after waking
    return min(seconds_until_open, max_sleep)


# Test function
if __name__ == "__main__":
    print("="*70)
    print("MARKET HOURS TEST")
    print("="*70)

    now = get_us_market_time()
    print(f"\nCurrent time (ET): {now.strftime('%A, %Y-%m-%d %I:%M:%S %p %Z')}")
    print(f"Day of week: {now.strftime('%A')} (weekday {now.weekday()})")
    print(f"\nIs weekend? {is_weekend(now)}")
    print(f"Is market holiday? {is_market_holiday(now)}")
    print(f"Within market hours (9:30 AM - 4:00 PM)? {is_within_market_hours(now)}")
    print(f"\n{'✅ MARKET IS OPEN' if is_market_open(now) else '🔴 MARKET IS CLOSED'}")

    print(f"\n{get_market_status_message()}")

    next_open, seconds = get_next_market_open()
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    print(f"\nNext market open: {next_open.strftime('%A, %Y-%m-%d at %I:%M %p %Z')}")
    print(f"Time until open: {hours} hours, {minutes} minutes ({seconds} seconds)")

    sleep_duration = calculate_sleep_duration()
    print(f"\nRecommended sleep duration: {sleep_duration} seconds ({sleep_duration // 60} minutes)")

    print("\n" + "="*70)
