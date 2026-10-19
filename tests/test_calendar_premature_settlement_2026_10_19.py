"""Calendars settled at MIDNIGHT ON their expiry day, against the prior close.

`_dc_settle_due`'s condition was `short_expiry <= today`, an ISO date compare.
That becomes true at **00:00 on the expiry date** — so a calendar settled the
instant the calendar date rolled over, SIXTEEN HOURS before its short leg
expired, and necessarily against the only price available at midnight: the
PREVIOUS day's close. The expiry session it was settling was never observed.

Variant D, entry 2026-10-07, short_expiry 2026-10-16:

    2026-10-16 00:00:48 [DCTM-SETTLE] E#1 (CALENDAR-leftover) settled at
                        SPX 7743.37: P&L $96.25

10-16 went on to close at **7659.96** — an **83.41pt** error.

Measured across D's full history (`dc_outcomes` joined to `dc_calendar_entries`):
**3 of 35 outcomes reached expiry**, and all three settled a day early —

    entry       expiry      SPX used   true close    error     realized
    2026-08-18  2026-08-28   7731.48     7714.83    +16.65     +$511.50
    2026-09-01  2026-09-11   7591.74     7658.26    -66.52     +$663.40
    2026-10-07  2026-10-16   7743.37     7659.96    +83.41      +$96.25

— carrying $1,271.15 of realized P&L. The other 32 closed EARLY (transform /
stop / EOD) at a live intraday price and are unaffected. Variant E has never had
an at-expiry settlement at all (all 6 of its outcomes closed early), so this is
a D-only defect in practice.

⚠️ This is NOT the "misdated row" problem described on 2026-10-19 — it is worse.
The row's DATE was a symptom; the P&L itself was computed against a price up to
83 points wrong.

TWO fixes, because the audit found two separate wrong-day paths:

1. `_dc_expiry_session_closed(expiry, today, now)` — settle only once the expiry
   date's REGULAR SESSION has ended. `not is_market_open()` is unusable (also
   true at 00:00, the very moment being excluded) and `is_after_hours` is a
   narrow 16:00–17:00 window while settlement routinely runs at 21:45+, so this
   compares against `get_market_close_time`, which handles early closes too.
2. `_dc_settlement_spx(expiry)` — mark against the EXPIRY DAY's own recorded
   close. Identical to "today's" on the normal path, but divergent on recovery:
   a bot down across the expiry session and waking a day later would otherwise
   mark against the wrong day entirely.

⚠️ Two bugs in the FIRST version of this fix were caught by pre-existing tests
in `test_double_calendar_strategy.py`, and both are pinned below: the predicate
ignored the caller's `today` (so a deliberately-future expiry became due), and
the loop returned True unconditionally (so an all-deferred call claimed success).
"""

from __future__ import annotations

import datetime as _dt
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.calendar_strategy_base import CalendarStrategyBase  # noqa: E402

CLOSED = CalendarStrategyBase._dc_expiry_session_closed


def _at(h, m, day="2026-10-16"):
    y, mo, d = (int(x) for x in day.split("-"))
    return _dt.datetime(y, mo, d, h, m)


class TestTheIncident:
    def test_midnight_on_the_expiry_day_is_NOT_due(self):
        """The exact moment D settled: 00:00:48 on 2026-10-16, its expiry day."""
        assert CLOSED("2026-10-16", "2026-10-16", _at(0, 0)) is False

    @pytest.mark.parametrize("h,m", [(0, 1), (6, 30), (9, 29), (12, 0), (15, 59)])
    def test_not_due_at_any_point_before_the_expiry_days_close(self, h, m):
        assert CLOSED("2026-10-16", "2026-10-16", _at(h, m)) is False

    @pytest.mark.parametrize("h,m", [(16, 0), (16, 1), (21, 45), (23, 59)])
    def test_due_once_the_expiry_days_close_has_passed(self, h, m):
        """16:00 inclusive — the close itself settles."""
        assert CLOSED("2026-10-16", "2026-10-16", _at(h, m)) is True

    def test_a_past_expiry_is_always_due(self):
        """Recovery path: the bot was down across the expiry session."""
        assert CLOSED("2026-10-15", "2026-10-16", _at(0, 0)) is True
        assert CLOSED("2026-06-19", "2026-10-16", _at(3, 0)) is True

    def test_a_future_expiry_is_never_due(self):
        assert CLOSED("2026-10-23", "2026-10-16", _at(23, 59)) is False

    def test_negative_control_the_old_condition_would_settle_at_midnight(self):
        """Proves the tests above are not vacuous — the replaced logic was a
        bare `expiry <= today`, which is True at 00:00 on the expiry date."""
        expiry, today = "2026-10-16", "2026-10-16"
        assert (expiry <= today) is True, "the old condition fired at midnight"
        assert CLOSED(expiry, today, _at(0, 0)) is False, "the new one does not"


class TestEarlyCloseDaysAreHandled:
    """`get_market_close_time` returns 13:00 on a half day. Using a hardcoded
    16:00 would defer a half-day expiry by three hours into the evening, past
    the point its own ticks stop."""

    def test_an_early_close_day_settles_at_1300(self):
        from shared.market_hours import get_early_close_dates
        # {reason_name: datetime} — the VALUES are the dates. An earlier version
        # of this test sorted the keys and tried to parse "Christmas Eve".
        early = get_early_close_dates(2026)
        assert early, "no early-close dates for 2026 — fixture assumption broken"
        for day in sorted(early.values()):
            iso = day.strftime("%Y-%m-%d")
            assert CLOSED(iso, iso, _at(13, 0, iso)) is True, f"{iso} at 13:00"
            assert CLOSED(iso, iso, _at(12, 59, iso)) is False, f"{iso} at 12:59"

    def test_a_regular_day_does_NOT_settle_at_1300(self):
        """The control for the test above: if the close time were hardcoded to
        13:00 the early-close test would pass for the wrong reason."""
        assert CLOSED("2026-10-16", "2026-10-16", _at(13, 0)) is False


class TestTheTwoBugsPreExistingTestsCaught:
    """Both were in the FIRST version of this fix. Pinned so they cannot return."""

    def test_the_predicate_honours_the_CALLERS_today(self):
        """v1 derived `today` from the real clock and so ignored the argument —
        which made `_dc_settle_due(today)` disregard its own parameter. A
        deliberately-future expiry became due, and
        test_settle_due_settles_past_skips_future caught it."""
        import inspect
        params = list(inspect.signature(CLOSED).parameters)
        assert params[:2] == ["expiry", "today"], (
            "today must be an explicit parameter, not derived from the clock")
        # future relative to the CALLER's today, even though it is past the real one
        assert CLOSED("2026-07-10", "2026-06-26", _at(23, 0)) is False

    def test_settle_due_reports_what_actually_settled(self):
        """v1 returned True unconditionally after the loop, so a call where every
        entry deferred on an unreadable price claimed success.
        test_settle_due_defers_when_spx_unreadable caught it."""
        src = open(os.path.join(ROOT, "bots/hydra/calendar_strategy_base.py")).read()
        i = src.index("def _dc_settle_due")
        body = src[i:i + 3000]
        assert "settled = 0" in body and "settled += 1" in body
        assert "if not settled:" in body and "return False" in body


class TestSettlementPriceIsTheExpiryDays:
    def test_it_prefers_the_expiry_dates_recorded_close(self):
        from types import SimpleNamespace
        inst = CalendarStrategyBase.__new__(CalendarStrategyBase)
        inst.current_price = 9999.0          # must NOT be used
        inst._data_recorder = SimpleNamespace(
            get_last_spx_for_date=lambda d: 7659.96 if d == "2026-10-16" else None)
        assert inst._dc_settlement_spx("2026-10-16") == pytest.approx(7659.96)

    def test_it_falls_back_when_that_date_has_no_ticks(self):
        from types import SimpleNamespace
        inst = CalendarStrategyBase.__new__(CalendarStrategyBase)
        inst.current_price = 7700.0
        inst._data_recorder = SimpleNamespace(get_last_spx_for_date=lambda d: None)
        assert inst._dc_settlement_spx("2026-10-16") == pytest.approx(7700.0)

    def test_no_expiry_argument_keeps_the_old_behaviour(self):
        """Backward compatibility — the parameter is optional."""
        inst = CalendarStrategyBase.__new__(CalendarStrategyBase)
        inst.current_price = 7700.0
        assert inst._dc_settlement_spx() == pytest.approx(7700.0)

    def test_a_raising_recorder_does_not_break_settlement(self):
        from types import SimpleNamespace
        def boom(_d):
            raise RuntimeError("db locked")
        inst = CalendarStrategyBase.__new__(CalendarStrategyBase)
        inst.current_price = 7700.0
        inst._data_recorder = SimpleNamespace(get_last_spx_for_date=boom)
        assert inst._dc_settlement_spx("2026-10-16") == pytest.approx(7700.0)
