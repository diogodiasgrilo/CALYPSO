#!/usr/bin/env python3
"""
Event Calendar Module

Provides utilities for detecting market-moving events:
- FOMC (Federal Open Market Committee) meeting dates
- Major earnings dates for QQQ top holdings

Used by trading strategies to avoid entering positions before major events.
"""

import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Types of market-moving events."""
    FOMC = "FOMC Meeting"
    EARNINGS = "Earnings Release"
    JOBS_REPORT = "Jobs Report"
    CPI = "CPI Release"


@dataclass
class MarketEvent:
    """Represents a market-moving event."""
    event_type: EventType
    event_date: date
    description: str
    symbol: Optional[str] = None  # For earnings, the company symbol

    @property
    def days_until(self) -> int:
        """Calculate days until this event from today."""
        today = date.today()
        return (self.event_date - today).days


# ============================================================================
# FOMC Meeting Dates
# ============================================================================

# FOMC meets 8 times per year - dates are published annually
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm

FOMC_DATES_2024 = [
    date(2024, 1, 30), date(2024, 1, 31),   # Jan 30-31
    date(2024, 3, 19), date(2024, 3, 20),   # Mar 19-20
    date(2024, 4, 30), date(2024, 5, 1),    # Apr 30 - May 1
    date(2024, 6, 11), date(2024, 6, 12),   # Jun 11-12
    date(2024, 7, 30), date(2024, 7, 31),   # Jul 30-31
    date(2024, 9, 17), date(2024, 9, 18),   # Sep 17-18
    date(2024, 11, 6), date(2024, 11, 7),   # Nov 6-7
    date(2024, 12, 17), date(2024, 12, 18), # Dec 17-18
]

FOMC_DATES_2025 = [
    date(2025, 1, 28), date(2025, 1, 29),   # Jan 28-29
    date(2025, 3, 18), date(2025, 3, 19),   # Mar 18-19
    date(2025, 5, 6), date(2025, 5, 7),     # May 6-7
    date(2025, 6, 17), date(2025, 6, 18),   # Jun 17-18
    date(2025, 7, 29), date(2025, 7, 30),   # Jul 29-30
    date(2025, 9, 16), date(2025, 9, 17),   # Sep 16-17
    date(2025, 11, 5), date(2025, 11, 6),   # Nov 5-6
    date(2025, 12, 16), date(2025, 12, 17), # Dec 16-17
]

FOMC_DATES_2026 = [
    # Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
    # Announcement/press conference is on day 2 at 2:00 PM EST
    date(2026, 1, 27), date(2026, 1, 28),   # Jan 27-28
    date(2026, 3, 17), date(2026, 3, 18),   # Mar 17-18*
    date(2026, 4, 28), date(2026, 4, 29),   # Apr 28-29
    date(2026, 6, 16), date(2026, 6, 17),   # Jun 16-17*
    date(2026, 7, 28), date(2026, 7, 29),   # Jul 28-29
    date(2026, 9, 15), date(2026, 9, 16),   # Sep 15-16*
    date(2026, 10, 27), date(2026, 10, 28), # Oct 27-28
    date(2026, 12, 8), date(2026, 12, 9),   # Dec 8-9*
    # * = Summary of Economic Projections released
]

# Combined FOMC dates
FOMC_DATES = {
    2024: FOMC_DATES_2024,
    2025: FOMC_DATES_2025,
    2026: FOMC_DATES_2026,
}


def get_fomc_dates(year: int) -> List[date]:
    """
    Get all FOMC meeting dates for a given year (both days of each meeting).

    Args:
        year: Year to get FOMC dates for

    Returns:
        List of FOMC meeting dates
    """
    return FOMC_DATES.get(year, [])


def get_fomc_announcement_dates(year: int) -> List[date]:
    """
    Get only FOMC announcement days for a given year (day 2 of each meeting).

    This is the day when the Fed releases its decision and the Chair holds
    a press conference at 2:00 PM EST. This is typically the high-volatility day.

    Args:
        year: Year to get FOMC announcement dates for

    Returns:
        List of FOMC announcement dates (day 2 of each meeting)
    """
    all_dates = FOMC_DATES.get(year, [])
    # FOMC dates are stored as pairs [day1, day2, day1, day2, ...]
    # Return only day 2 (index 1, 3, 5, 7, ...)
    return [d for i, d in enumerate(all_dates) if i % 2 == 1]


def is_fomc_meeting_day(check_date: date = None) -> bool:
    """
    Check if a specific date is ANY day of an FOMC meeting (day 1 or day 2).

    Use this to block trading on ALL FOMC meeting days. Markets are volatile
    on both days - day 1 due to anticipation, day 2 due to the announcement.

    Args:
        check_date: Date to check (defaults to today)

    Returns:
        True if the date is any FOMC meeting day
    """
    if check_date is None:
        check_date = date.today()

    all_fomc_dates = get_fomc_dates(check_date.year)
    return check_date in all_fomc_dates


def is_fomc_announcement_day(check_date: date = None) -> bool:
    """
    Check if a specific date is an FOMC announcement day (day 2 only).

    Use this to block trading on FOMC days when the Fed releases its decision.

    Args:
        check_date: Date to check (defaults to today)

    Returns:
        True if the date is an FOMC announcement day
    """
    if check_date is None:
        check_date = date.today()

    announcement_dates = get_fomc_announcement_dates(check_date.year)
    return check_date in announcement_dates


def get_next_fomc_date(from_date: date = None) -> Optional[date]:
    """
    Get the next FOMC meeting date from a given date.

    Args:
        from_date: Starting date (defaults to today)

    Returns:
        Next FOMC meeting date, or None if not found
    """
    if from_date is None:
        from_date = date.today()

    # Check current and next year
    for year in [from_date.year, from_date.year + 1]:
        dates = get_fomc_dates(year)
        for fomc_date in sorted(dates):
            if fomc_date >= from_date:
                return fomc_date

    return None


def is_fomc_approaching(days_ahead: int = 1, from_date: date = None) -> bool:
    """
    Check if an FOMC meeting is within the specified number of days.

    Args:
        days_ahead: Number of days to look ahead
        from_date: Starting date (defaults to today)

    Returns:
        True if FOMC meeting is within days_ahead days
    """
    if from_date is None:
        from_date = date.today()

    next_fomc = get_next_fomc_date(from_date)
    if next_fomc is None:
        return False

    days_until = (next_fomc - from_date).days
    return 0 <= days_until <= days_ahead


def get_fomc_blackout_range(days_before: int = 2) -> Tuple[Optional[date], Optional[date]]:
    """
    Get the current FOMC blackout range (if active).

    Args:
        days_before: Number of days before FOMC to start blackout

    Returns:
        Tuple of (blackout_start, fomc_date) if in blackout, else (None, None)
    """
    today = date.today()
    next_fomc = get_next_fomc_date(today)

    if next_fomc is None:
        return None, None

    blackout_start = next_fomc - timedelta(days=days_before)

    if blackout_start <= today <= next_fomc:
        return blackout_start, next_fomc

    return None, None


# ============================================================================
# Earnings Calendar (QQQ Top Holdings)
# ============================================================================

# QQQ top holdings that could significantly move the index
# These companies typically report earnings quarterly
QQQ_TOP_HOLDINGS = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "NVDA",   # NVIDIA
    "AMZN",   # Amazon
    "GOOGL",  # Alphabet Class A
    "GOOG",   # Alphabet Class C
    "META",   # Meta Platforms
    "TSLA",   # Tesla
    "AVGO",   # Broadcom
    "COST",   # Costco
]

# Typical quarterly earnings months
# Q1: April (fiscal Q1 ends March)
# Q2: July (fiscal Q2 ends June)
# Q3: October (fiscal Q3 ends September)
# Q4: January (fiscal Q4 ends December)

# Hardcoded earnings estimates - these should be updated quarterly
# Format: (year, month, day) - approximate dates, actual dates vary
EARNINGS_CALENDAR_2026 = {
    "AAPL": [
        date(2026, 1, 29),   # Q1 FY26 (fiscal Q1)
        date(2026, 4, 30),   # Q2 FY26
        date(2026, 7, 30),   # Q3 FY26
        date(2026, 10, 29),  # Q4 FY26
    ],
    "MSFT": [
        date(2026, 1, 27),   # Q2 FY26
        date(2026, 4, 28),   # Q3 FY26
        date(2026, 7, 28),   # Q4 FY26
        date(2026, 10, 27),  # Q1 FY27
    ],
    "NVDA": [
        date(2026, 2, 25),   # Q4 FY26
        date(2026, 5, 27),   # Q1 FY27
        date(2026, 8, 26),   # Q2 FY27
        date(2026, 11, 25),  # Q3 FY27
    ],
    "AMZN": [
        date(2026, 2, 5),    # Q4 2025
        date(2026, 4, 30),   # Q1 2026
        date(2026, 7, 30),   # Q2 2026
        date(2026, 10, 29),  # Q3 2026
    ],
    "GOOGL": [
        date(2026, 2, 4),    # Q4 2025
        date(2026, 4, 28),   # Q1 2026
        date(2026, 7, 28),   # Q2 2026
        date(2026, 10, 27),  # Q3 2026
    ],
    "META": [
        date(2026, 2, 4),    # Q4 2025
        date(2026, 4, 29),   # Q1 2026
        date(2026, 7, 29),   # Q2 2026
        date(2026, 10, 28),  # Q3 2026
    ],
    "TSLA": [
        date(2026, 1, 28),   # Q4 2025
        date(2026, 4, 22),   # Q1 2026
        date(2026, 7, 22),   # Q2 2026
        date(2026, 10, 21),  # Q3 2026
    ],
}


def get_upcoming_qqq_earnings(days_ahead: int = 7, from_date: date = None) -> List[MarketEvent]:
    """
    Get upcoming earnings for QQQ top holdings.

    Args:
        days_ahead: Number of days to look ahead
        from_date: Starting date (defaults to today)

    Returns:
        List of MarketEvent objects for upcoming earnings
    """
    if from_date is None:
        from_date = date.today()

    end_date = from_date + timedelta(days=days_ahead)
    upcoming = []

    for symbol, earnings_dates in EARNINGS_CALENDAR_2026.items():
        for earnings_date in earnings_dates:
            if from_date <= earnings_date <= end_date:
                event = MarketEvent(
                    event_type=EventType.EARNINGS,
                    event_date=earnings_date,
                    description=f"{symbol} Q{((earnings_date.month - 1) // 3) + 1} Earnings",
                    symbol=symbol
                )
                upcoming.append(event)

    return sorted(upcoming, key=lambda e: e.event_date)


def is_major_earnings_approaching(days_ahead: int = 1, from_date: date = None) -> bool:
    """
    Check if any major QQQ earnings are within the specified number of days.

    Args:
        days_ahead: Number of days to look ahead
        from_date: Starting date (defaults to today)

    Returns:
        True if major earnings are approaching
    """
    upcoming = get_upcoming_qqq_earnings(days_ahead, from_date)
    return len(upcoming) > 0


def get_next_earnings_date(symbol: str, from_date: date = None) -> Optional[date]:
    """
    Get the next earnings date for a specific symbol.

    Args:
        symbol: Stock symbol (e.g., "AAPL")
        from_date: Starting date (defaults to today)

    Returns:
        Next earnings date or None
    """
    if from_date is None:
        from_date = date.today()

    earnings_dates = EARNINGS_CALENDAR_2026.get(symbol.upper(), [])

    for earnings_date in sorted(earnings_dates):
        if earnings_date >= from_date:
            return earnings_date

    return None


# ============================================================================
# Combined Event Detection
# ============================================================================

def get_all_upcoming_events(days_ahead: int = 7, from_date: date = None) -> List[MarketEvent]:
    """
    Get all upcoming market-moving events.

    Args:
        days_ahead: Number of days to look ahead
        from_date: Starting date (defaults to today)

    Returns:
        List of MarketEvent objects sorted by date
    """
    if from_date is None:
        from_date = date.today()

    events = []

    # Add FOMC events
    end_date = from_date + timedelta(days=days_ahead)
    for year in [from_date.year, from_date.year + 1]:
        for fomc_date in get_fomc_dates(year):
            if from_date <= fomc_date <= end_date:
                event = MarketEvent(
                    event_type=EventType.FOMC,
                    event_date=fomc_date,
                    description="FOMC Meeting"
                )
                events.append(event)

    # Add earnings events
    events.extend(get_upcoming_qqq_earnings(days_ahead, from_date))

    return sorted(events, key=lambda e: e.event_date)


def is_event_approaching(days_ahead: int = 1, from_date: date = None) -> Tuple[bool, Optional[MarketEvent]]:
    """
    Check if any market-moving event is approaching.

    Args:
        days_ahead: Number of days to look ahead (default 1)
        from_date: Starting date (defaults to today)

    Returns:
        Tuple of (is_approaching, next_event)
    """
    events = get_all_upcoming_events(days_ahead, from_date)

    if events:
        return True, events[0]

    return False, None


def get_event_status_message(from_date: date = None) -> str:
    """
    Get a human-readable event status message.

    Args:
        from_date: Starting date (defaults to today)

    Returns:
        Status message describing upcoming events
    """
    if from_date is None:
        from_date = date.today()

    # Check next 7 days
    events = get_all_upcoming_events(days_ahead=7, from_date=from_date)

    if not events:
        return "No major events in the next 7 days"

    # Get most imminent event
    next_event = events[0]
    days_until = next_event.days_until

    if days_until == 0:
        return f"TODAY: {next_event.description}"
    elif days_until == 1:
        return f"TOMORROW: {next_event.description}"
    else:
        return f"In {days_until} days: {next_event.description}"


def should_close_for_event(
    days_before_fomc: int = 1,
    days_before_earnings: int = 1,
    from_date: date = None
) -> Tuple[bool, Optional[str]]:
    """
    Determine if positions should be closed due to upcoming events.

    Used by strategies to decide when to exit before major market events.

    Args:
        days_before_fomc: Days before FOMC to close
        days_before_earnings: Days before major earnings to close
        from_date: Starting date (defaults to today)

    Returns:
        Tuple of (should_close, reason)
    """
    if from_date is None:
        from_date = date.today()

    # Check FOMC
    next_fomc = get_next_fomc_date(from_date)
    if next_fomc:
        days_to_fomc = (next_fomc - from_date).days
        if 0 <= days_to_fomc <= days_before_fomc:
            return True, f"FOMC meeting in {days_to_fomc} day(s)"

    # Check major earnings
    upcoming_earnings = get_upcoming_qqq_earnings(days_before_earnings, from_date)
    if upcoming_earnings:
        next_earnings = upcoming_earnings[0]
        return True, f"{next_earnings.symbol} earnings in {next_earnings.days_until} day(s)"

    return False, None


# ============================================================================
# Test
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("EVENT CALENDAR TEST")
    print("=" * 70)

    today = date.today()
    print(f"\nToday: {today}")

    # Test FOMC dates
    print("\n" + "-" * 70)
    print(f"FOMC DATES FOR {today.year}")
    print("-" * 70)

    fomc_dates = get_fomc_dates(today.year)
    for d in sorted(fomc_dates):
        marker = " <-- UPCOMING" if d >= today else ""
        print(f"  {d.strftime('%Y-%m-%d (%A)')}{marker}")

    next_fomc = get_next_fomc_date()
    if next_fomc:
        days_until = (next_fomc - today).days
        print(f"\nNext FOMC: {next_fomc} ({days_until} days away)")
        print(f"FOMC approaching (within 2 days)? {is_fomc_approaching(2)}")

    # Test earnings
    print("\n" + "-" * 70)
    print("UPCOMING QQQ EARNINGS (Next 30 days)")
    print("-" * 70)

    upcoming = get_upcoming_qqq_earnings(days_ahead=30)
    if upcoming:
        for event in upcoming:
            print(f"  {event.event_date}: {event.description} ({event.days_until} days)")
    else:
        print("  No earnings in the next 30 days")

    # Test combined events
    print("\n" + "-" * 70)
    print("ALL UPCOMING EVENTS (Next 14 days)")
    print("-" * 70)

    all_events = get_all_upcoming_events(days_ahead=14)
    if all_events:
        for event in all_events:
            print(f"  {event.event_date}: {event.event_type.value} - {event.description}")
    else:
        print("  No events in the next 14 days")

    # Test event status
    print("\n" + "-" * 70)
    print("EVENT STATUS")
    print("-" * 70)
    print(f"  {get_event_status_message()}")

    # Test should_close
    print("\n" + "-" * 70)
    print("SHOULD CLOSE CHECK")
    print("-" * 70)
    should_close, reason = should_close_for_event()
    print(f"  Should close? {should_close}")
    if reason:
        print(f"  Reason: {reason}")

    print("\n" + "=" * 70)
