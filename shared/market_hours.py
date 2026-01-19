#!/usr/bin/env python3
"""
Market Hours Module

Provides utilities for checking if US equity markets are open.
Handles weekends, holidays, and market hours (9:30 AM - 4:00 PM ET).
"""

import logging
from datetime import datetime, time, timedelta
from typing import Tuple, Optional
import pytz

logger = logging.getLogger(__name__)

# US Market timezone
US_EASTERN = pytz.timezone('America/New_York')

# Regular market hours (US equity markets)
MARKET_OPEN_TIME = time(9, 30)   # 9:30 AM ET
MARKET_CLOSE_TIME = time(16, 0)  # 4:00 PM ET


def _get_nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> datetime:
    """
    Get the nth occurrence of a weekday in a given month.

    Args:
        year: Year
        month: Month (1-12)
        weekday: Day of week (0=Monday, 6=Sunday)
        n: Which occurrence (1=first, 2=second, etc.)

    Returns:
        datetime: The date of the nth weekday
    """
    # Start at the first day of the month
    first_day = datetime(year, month, 1)

    # Find the first occurrence of the weekday
    days_until_weekday = (weekday - first_day.weekday()) % 7
    first_occurrence = first_day + timedelta(days=days_until_weekday)

    # Add (n-1) weeks to get the nth occurrence
    return first_occurrence + timedelta(weeks=n - 1)


def _get_last_weekday_of_month(year: int, month: int, weekday: int) -> datetime:
    """
    Get the last occurrence of a weekday in a given month.

    Args:
        year: Year
        month: Month (1-12)
        weekday: Day of week (0=Monday, 6=Sunday)

    Returns:
        datetime: The date of the last weekday
    """
    # Start at the last day of the month
    if month == 12:
        last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1) - timedelta(days=1)

    # Find the last occurrence of the weekday
    days_since_weekday = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=days_since_weekday)


def _get_good_friday(year: int) -> datetime:
    """
    Calculate Good Friday for a given year using the Anonymous Gregorian algorithm.

    Good Friday is the Friday before Easter Sunday.

    Args:
        year: Year to calculate for

    Returns:
        datetime: Good Friday date
    """
    # Anonymous Gregorian algorithm for Easter
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1

    easter_sunday = datetime(year, month, day)
    good_friday = easter_sunday - timedelta(days=2)
    return good_friday


def _adjust_for_weekend(dt: datetime) -> datetime:
    """
    Adjust a holiday date if it falls on a weekend.
    - If Saturday, observed on Friday
    - If Sunday, observed on Monday

    Args:
        dt: Holiday date

    Returns:
        datetime: Adjusted date for market closure
    """
    if dt.weekday() == 5:  # Saturday
        return dt - timedelta(days=1)  # Observe Friday
    elif dt.weekday() == 6:  # Sunday
        return dt + timedelta(days=1)  # Observe Monday
    return dt


def get_us_market_holidays(year: int) -> dict:
    """
    Get all US stock market holidays for a given year.

    NYSE/NASDAQ holidays:
    - New Year's Day (Jan 1, or observed)
    - Martin Luther King Jr. Day (3rd Monday of January)
    - Presidents' Day (3rd Monday of February)
    - Good Friday (Friday before Easter)
    - Memorial Day (Last Monday of May)
    - Juneteenth National Independence Day (June 19, or observed) - since 2021
    - Independence Day (July 4, or observed)
    - Labor Day (1st Monday of September)
    - Thanksgiving Day (4th Thursday of November)
    - Christmas Day (December 25, or observed)

    Args:
        year: Year to get holidays for

    Returns:
        dict: Holiday name -> date mapping
    """
    holidays = {}

    # New Year's Day - January 1 (adjusted for weekend)
    new_years = _adjust_for_weekend(datetime(year, 1, 1))
    holidays["New Year's Day"] = new_years

    # Martin Luther King Jr. Day - 3rd Monday of January
    mlk_day = _get_nth_weekday_of_month(year, 1, 0, 3)  # 0=Monday, 3rd occurrence
    holidays["Martin Luther King Jr. Day"] = mlk_day

    # Presidents' Day - 3rd Monday of February
    presidents_day = _get_nth_weekday_of_month(year, 2, 0, 3)
    holidays["Presidents' Day"] = presidents_day

    # Good Friday - Friday before Easter (market closed)
    good_friday = _get_good_friday(year)
    holidays["Good Friday"] = good_friday

    # Memorial Day - Last Monday of May
    memorial_day = _get_last_weekday_of_month(year, 5, 0)
    holidays["Memorial Day"] = memorial_day

    # Juneteenth - June 19 (observed since 2021)
    if year >= 2021:
        juneteenth = _adjust_for_weekend(datetime(year, 6, 19))
        holidays["Juneteenth"] = juneteenth

    # Independence Day - July 4 (adjusted for weekend)
    independence_day = _adjust_for_weekend(datetime(year, 7, 4))
    holidays["Independence Day"] = independence_day

    # Labor Day - 1st Monday of September
    labor_day = _get_nth_weekday_of_month(year, 9, 0, 1)
    holidays["Labor Day"] = labor_day

    # Thanksgiving Day - 4th Thursday of November
    thanksgiving = _get_nth_weekday_of_month(year, 11, 3, 4)  # 3=Thursday
    holidays["Thanksgiving Day"] = thanksgiving

    # Christmas Day - December 25 (adjusted for weekend)
    christmas = _adjust_for_weekend(datetime(year, 12, 25))
    holidays["Christmas Day"] = christmas

    return holidays


def get_holiday_name(dt: datetime = None) -> Optional[str]:
    """
    Get the name of the holiday if the given date is a market holiday.

    Args:
        dt: Date to check (defaults to now in ET)

    Returns:
        str or None: Holiday name if it's a holiday, None otherwise
    """
    if dt is None:
        dt = get_us_market_time()

    holidays = get_us_market_holidays(dt.year)
    check_date = dt.date() if hasattr(dt, 'date') else dt

    for name, holiday_dt in holidays.items():
        if holiday_dt.date() == check_date:
            return name

    return None


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

    Checks all NYSE/NASDAQ holidays including floating holidays like
    MLK Day, Presidents Day, Memorial Day, Labor Day, Thanksgiving, etc.

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        bool: True if it's a market holiday
    """
    if dt is None:
        dt = get_us_market_time()

    return get_holiday_name(dt) is not None


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

    holiday_name = get_holiday_name(now)
    if holiday_name:
        next_open, seconds = get_next_market_open()
        hours = seconds // 3600
        return (
            f"🔴 Market CLOSED ({holiday_name}) | "
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
    holiday = get_holiday_name(now)
    if holiday:
        print(f"Holiday name: {holiday}")
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

    # Show all holidays for current year
    print("\n" + "="*70)
    print(f"US MARKET HOLIDAYS FOR {now.year}")
    print("="*70)
    holidays = get_us_market_holidays(now.year)
    for name, dt in sorted(holidays.items(), key=lambda x: x[1]):
        print(f"  {dt.strftime('%Y-%m-%d (%A)')}: {name}")

    print("\n" + "="*70)
