#!/usr/bin/env python3
"""
Market Hours Module

Provides utilities for checking US equity market status including:
- Regular market hours (9:30 AM - 4:00 PM ET)
- Pre-market session (7:00 AM - 9:30 AM ET)
- After-hours session (4:00 PM - 5:00 PM ET)
- Weekends and holidays

Extended hours are based on Saxo Bank's extended trading hours:
https://www.help.saxo/hc/en-us/articles/7574076258589-Extended-trading-hours

Key functions:
- is_market_open(): Check if regular market is open
- is_pre_market(): Check if in pre-market session (7:00-9:30 AM)
- is_after_hours(): Check if in after-hours session (4:00-5:00 PM)
- is_saxo_price_available(): Check if Saxo can provide price data (7:00 AM - 5:00 PM)
- get_trading_session(): Get current session name ("pre_market", "regular", "after_hours", "closed")
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
EARLY_CLOSE_TIME = time(13, 0)   # 1:00 PM ET for early close days

# Extended trading hours (Saxo Bank specific)
# Pre-market: 7:00 AM - 9:30 AM ET (limit orders only)
# After-hours: 4:00 PM - 5:00 PM ET (limit orders only)
# See: https://www.help.saxo/hc/en-us/articles/7574076258589-Extended-trading-hours
PRE_MARKET_OPEN_TIME = time(7, 0)   # 7:00 AM ET - Pre-market starts
AFTER_HOURS_CLOSE_TIME = time(17, 0)  # 5:00 PM ET - After-hours ends


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


def get_early_close_dates(year: int) -> dict:
    """
    Get all early close dates for US stock markets.

    NYSE/NASDAQ close at 1:00 PM ET on these days:
    - Day after Thanksgiving (Black Friday)
    - Christmas Eve (Dec 24, or Dec 23 if Dec 24 is Sunday)
    - July 3rd (if July 4th is a weekday, or July 3rd if July 4th is Saturday)

    Note: New Year's Eve is NOT an early close day.

    Args:
        year: Year to get early close dates for

    Returns:
        dict: Reason -> date mapping for early close days
    """
    early_closes = {}

    # Day after Thanksgiving (4th Thursday of November + 1 day = Friday)
    thanksgiving = _get_nth_weekday_of_month(year, 11, 3, 4)  # 3=Thursday
    black_friday = thanksgiving + timedelta(days=1)
    early_closes["Day After Thanksgiving"] = black_friday

    # Christmas Eve - Dec 24, unless it falls on weekend
    christmas_eve = datetime(year, 12, 24)
    if christmas_eve.weekday() == 5:  # Saturday - early close on Friday Dec 23
        early_closes["Christmas Eve (observed)"] = christmas_eve - timedelta(days=1)
    elif christmas_eve.weekday() == 6:  # Sunday - no early close (market closed Monday)
        pass
    else:
        early_closes["Christmas Eve"] = christmas_eve

    # July 3rd early close (if July 4th is a weekday)
    july_4 = datetime(year, 7, 4)
    if july_4.weekday() == 0:  # Monday - market closed, July 3 is Sunday, no early close
        pass
    elif july_4.weekday() == 5:  # Saturday - July 3 is Friday, early close
        early_closes["Day Before Independence Day"] = datetime(year, 7, 3)
    elif july_4.weekday() == 6:  # Sunday - July 3 is Saturday, no early close
        pass
    else:  # July 4 is Tue-Fri, July 3 is a weekday, early close
        early_closes["Day Before Independence Day"] = datetime(year, 7, 3)

    return early_closes


def is_early_close_day(dt: datetime = None) -> bool:
    """
    Check if the given date is an early close day (1:00 PM ET close).

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        bool: True if it's an early close day
    """
    if dt is None:
        dt = get_us_market_time()

    return get_early_close_reason(dt) is not None


def get_early_close_reason(dt: datetime = None) -> Optional[str]:
    """
    Get the reason for early close if the given date is an early close day.

    Args:
        dt: Date to check (defaults to now in ET)

    Returns:
        str or None: Reason for early close, or None if not an early close day
    """
    if dt is None:
        dt = get_us_market_time()

    early_closes = get_early_close_dates(dt.year)
    check_date = dt.date() if hasattr(dt, 'date') else dt

    for reason, close_dt in early_closes.items():
        if close_dt.date() == check_date:
            return reason

    return None


def get_market_close_time(dt: datetime = None) -> time:
    """
    Get the market close time for a given date.

    Returns 1:00 PM for early close days, 4:00 PM for regular days.

    Args:
        dt: Date to check (defaults to now in ET)

    Returns:
        time: Market close time (either EARLY_CLOSE_TIME or MARKET_CLOSE_TIME)
    """
    if dt is None:
        dt = get_us_market_time()

    if is_early_close_day(dt):
        return EARLY_CLOSE_TIME

    return MARKET_CLOSE_TIME


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


# =========================================================================
# EXTENDED HOURS FUNCTIONS (Saxo Bank Extended Trading Hours)
# =========================================================================
# Saxo Bank offers extended hours trading on US exchanges:
# - Pre-market: 7:00 AM - 9:30 AM ET (limit orders only)
# - After-hours: 4:00 PM - 5:00 PM ET (limit orders only)
# Price data is available during these sessions but liquidity is lower.
# Source: https://www.help.saxo/hc/en-us/articles/7574076258589-Extended-trading-hours
# =========================================================================


def is_pre_market(dt: datetime = None) -> bool:
    """
    Check if we're in the pre-market trading session (7:00 AM - 9:30 AM ET).

    Pre-market is when Saxo Bank provides price data but regular market is not yet open.
    Only limit orders are supported during pre-market.

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        bool: True if within pre-market hours on a trading day
    """
    if dt is None:
        dt = get_us_market_time()

    # Must be a trading day (not weekend or holiday)
    if is_weekend(dt) or is_market_holiday(dt):
        return False

    current_time = dt.time()
    return PRE_MARKET_OPEN_TIME <= current_time < MARKET_OPEN_TIME


def is_after_hours(dt: datetime = None) -> bool:
    """
    Check if we're in the after-hours trading session (4:00 PM - 5:00 PM ET).

    After-hours is when Saxo Bank provides price data but regular market has closed.
    Only limit orders are supported during after-hours.

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        bool: True if within after-hours on a trading day
    """
    if dt is None:
        dt = get_us_market_time()

    # Must be a trading day (not weekend or holiday)
    if is_weekend(dt) or is_market_holiday(dt):
        return False

    current_time = dt.time()
    return MARKET_CLOSE_TIME <= current_time < AFTER_HOURS_CLOSE_TIME


def is_extended_hours(dt: datetime = None) -> bool:
    """
    Check if we're in any extended trading session (pre-market or after-hours).

    Extended hours = Pre-market (7:00-9:30 AM) OR After-hours (4:00-5:00 PM)

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        bool: True if within any extended trading session
    """
    if dt is None:
        dt = get_us_market_time()

    return is_pre_market(dt) or is_after_hours(dt)


def is_saxo_price_available(dt: datetime = None) -> bool:
    """
    Check if Saxo Bank price data is available (regular + extended hours).

    Price data is available during:
    - Pre-market: 7:00 AM - 9:30 AM ET
    - Regular hours: 9:30 AM - 4:00 PM ET
    - After-hours: 4:00 PM - 5:00 PM ET

    Total: 7:00 AM - 5:00 PM ET on trading days.

    IMPORTANT: This is when Saxo CAN provide prices. Outside these hours,
    price fetching will likely return stale data or fail.

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        bool: True if Saxo should have live price data available
    """
    if dt is None:
        dt = get_us_market_time()

    # Must be a trading day (not weekend or holiday)
    if is_weekend(dt) or is_market_holiday(dt):
        return False

    current_time = dt.time()
    return PRE_MARKET_OPEN_TIME <= current_time < AFTER_HOURS_CLOSE_TIME


def get_trading_session(dt: datetime = None) -> str:
    """
    Get the current trading session name.

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        str: One of "pre_market", "regular", "after_hours", "closed"
    """
    if dt is None:
        dt = get_us_market_time()

    # Weekend or holiday = closed
    if is_weekend(dt) or is_market_holiday(dt):
        return "closed"

    current_time = dt.time()

    # Before pre-market opens
    if current_time < PRE_MARKET_OPEN_TIME:
        return "closed"

    # Pre-market: 7:00 AM - 9:30 AM
    if current_time < MARKET_OPEN_TIME:
        return "pre_market"

    # Regular hours: 9:30 AM - 4:00 PM
    if current_time < MARKET_CLOSE_TIME:
        return "regular"

    # After-hours: 4:00 PM - 5:00 PM
    if current_time < AFTER_HOURS_CLOSE_TIME:
        return "after_hours"

    # After extended hours close
    return "closed"


def get_extended_hours_status_message(dt: datetime = None) -> str:
    """
    Get a detailed market status message including extended hours info.

    Args:
        dt: Datetime to check (defaults to now in ET)

    Returns:
        str: Human-readable status including session type
    """
    if dt is None:
        dt = get_us_market_time()

    session = get_trading_session(dt)
    time_str = dt.strftime('%I:%M:%S %p %Z')

    if session == "regular":
        return f"🟢 REGULAR SESSION | {time_str} | Saxo prices available"
    elif session == "pre_market":
        mins_to_open = int((datetime.combine(dt.date(), MARKET_OPEN_TIME) -
                           datetime.combine(dt.date(), dt.time())).total_seconds() // 60)
        return f"🟡 PRE-MARKET | {time_str} | {mins_to_open} min until 9:30 AM | Saxo prices available"
    elif session == "after_hours":
        mins_to_close = int((datetime.combine(dt.date(), AFTER_HOURS_CLOSE_TIME) -
                            datetime.combine(dt.date(), dt.time())).total_seconds() // 60)
        return f"🟡 AFTER-HOURS | {time_str} | {mins_to_close} min until close | Saxo prices available"
    else:
        # Closed
        next_open, seconds = get_next_market_open()
        hours = seconds // 3600

        # Check if it's before pre-market on a trading day
        if not is_weekend(dt) and not is_market_holiday(dt):
            current_time = dt.time()
            if current_time < PRE_MARKET_OPEN_TIME:
                # Before 7 AM on a trading day
                pre_market_dt = dt.replace(hour=7, minute=0, second=0, microsecond=0)
                mins_to_premarket = int((pre_market_dt - dt).total_seconds() // 60)
                return f"🔴 CLOSED | {time_str} | Pre-market opens in {mins_to_premarket} min (7:00 AM)"
            else:
                # After 5 PM
                return f"🔴 CLOSED | {time_str} | After-hours ended | Opens in {hours}h"

        return f"🔴 CLOSED | {time_str} | Opens in {hours} hours"


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

    # Extended hours info
    print("\n" + "="*70)
    print("EXTENDED HOURS STATUS")
    print("="*70)
    print(f"\nSaxo Extended Hours Schedule:")
    print(f"  Pre-market:   7:00 AM - 9:30 AM ET")
    print(f"  Regular:      9:30 AM - 4:00 PM ET")
    print(f"  After-hours:  4:00 PM - 5:00 PM ET")
    print(f"\nCurrent session: {get_trading_session(now)}")
    print(f"Is pre-market? {is_pre_market(now)}")
    print(f"Is after-hours? {is_after_hours(now)}")
    print(f"Is extended hours? {is_extended_hours(now)}")
    print(f"Is Saxo price available? {is_saxo_price_available(now)}")
    print(f"\n{get_extended_hours_status_message(now)}")

    # Show all holidays for current year
    print("\n" + "="*70)
    print(f"US MARKET HOLIDAYS FOR {now.year}")
    print("="*70)
    holidays = get_us_market_holidays(now.year)
    for name, dt in sorted(holidays.items(), key=lambda x: x[1]):
        print(f"  {dt.strftime('%Y-%m-%d (%A)')}: {name}")

    print("\n" + "="*70)
