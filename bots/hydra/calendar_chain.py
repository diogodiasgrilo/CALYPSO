"""Two-expiration selection for Strategy D (DC Time Machine) — Phase 2.

Pure, broker-free helpers (stdlib + market_hours only) for choosing the two
SPXW expirations a double calendar needs:
  - the SHORT (near) expiry: the option sold, ``short_dte_min..short_dte_max``
    calendar days out;
  - the LONG (far) expiry: the option bought, ``long_extra_min..long_extra_max``
    calendar days FURTHER out than the short.

SPXW lists daily expirations on trading days, so the candidate universe is the
trading days inside the window; the strategy then resolves real conids per
expiry (broker side). Keeping selection pure makes it unit-testable without a
broker or a live clock — the caller passes ``today_iso``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, List, Optional, Tuple

from shared.market_hours import get_holiday_name

FRIDAY = 4  # date.weekday(): Mon=0 .. Sun=6


def _is_trading_day(d: date, is_holiday: Optional[Callable[[date], bool]] = None) -> bool:
    """A weekday that is not an exchange holiday."""
    if d.weekday() >= 5:  # Sat/Sun
        return False
    if is_holiday is not None:
        return not is_holiday(d)
    # Default: reuse the project's NYSE holiday calendar (get_holiday_name takes a
    # datetime-like with .date(); a date has no .date(), so wrap via a shim).
    return get_holiday_name(_DateAsDatetime(d)) is None


class _DateAsDatetime:
    """Tiny shim so a plain ``date`` satisfies get_holiday_name's ``.date()`` +
    ``.year`` access without importing datetime/tz machinery here."""

    def __init__(self, d: date):
        self._d = d
        self.year = d.year

    def date(self) -> date:
        return self._d


def generate_candidate_expiries(
    today_iso: str,
    horizon_days: int,
    is_holiday: Optional[Callable[[date], bool]] = None,
) -> List[str]:
    """Trading-day ISO dates in ``(today, today+horizon_days]`` — the SPXW expiry
    candidates. Excludes today (a calendar never uses a 0DTE short here)."""
    today = date.fromisoformat(today_iso)
    out: List[str] = []
    for n in range(1, horizon_days + 1):
        d = today + timedelta(days=n)
        if _is_trading_day(d, is_holiday):
            out.append(d.isoformat())
    return out


def _dte(today: date, iso: str) -> int:
    return (date.fromisoformat(iso) - today).days


def pick_calendar_expiries(
    candidates: List[str],
    today_iso: str,
    short_dte_min: int,
    short_dte_max: int,
    long_extra_min: int,
    long_extra_max: int,
    prefer_friday: bool = True,
) -> Optional[Tuple[str, str]]:
    """Choose (short_expiry, long_expiry) from sorted ISO ``candidates``.

    SHORT: a candidate with calendar DTE in ``[short_dte_min, short_dte_max]``.
    With ``prefer_friday`` (default, matches Burnich's "following-week Friday"),
    the earliest Friday in the window is chosen; otherwise the earliest candidate
    in the window (smallest DTE → largest front-vs-back vega differential).

    LONG: the candidate after the short whose extra DTE over the short is in
    ``[long_extra_min, long_extra_max]``, smallest such gap. Returns None if
    either leg can't be satisfied.
    """
    today = date.fromisoformat(today_iso)
    in_window = [c for c in candidates if short_dte_min <= _dte(today, c) <= short_dte_max]
    if not in_window:
        return None
    in_window.sort(key=lambda c: _dte(today, c))

    short_iso: Optional[str] = None
    if prefer_friday:
        fridays = [c for c in in_window if date.fromisoformat(c).weekday() == FRIDAY]
        if fridays:
            short_iso = fridays[0]
    if short_iso is None:
        short_iso = in_window[0]

    short_dte = _dte(today, short_iso)
    longs = [
        c for c in candidates
        if long_extra_min <= (_dte(today, c) - short_dte) <= long_extra_max
    ]
    if not longs:
        return None
    longs.sort(key=lambda c: _dte(today, c))  # smallest gap first
    return short_iso, longs[0]
