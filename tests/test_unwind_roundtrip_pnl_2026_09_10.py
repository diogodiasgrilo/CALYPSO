"""
ORDER-010: book the round-trip PRICE P&L of a failed entry's unwound legs
(2026-09-10).

THE GAP. When an entry fails part-way, `_unwind_partial_entry` closes the legs
that did fill. Since 2026-08-20 it booked the round-trip COMMISSION — but the
code's own note conceded that "market-order slippage on this round trip is NOT
separately tracked". That slippage is real money: each leg was a genuine broker
fill on the way IN and again on the way OUT, at a market order, so the round
trip almost always loses the spread. Leaving it unbooked understated the true
cost of a failed entry in BOTH the day aggregate and the per-entry number.

NOT HYPOTHETICAL: this path fired TWICE on 2026-09-09 on the live seat (failures
at leg 3 and leg 4), unwinding 2 and 3 filled legs respectively.

SIGN CONVENTION — the thing most likely to be got backwards:
  short leg: SOLD to open, BOUGHT to close  ->  P&L = open - close
  long  leg: BOUGHT to open, SOLD to close  ->  P&L = close - open
Prices are option points; x100 x contracts for dollars.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _entry(contracts=7, open_px=1.00):
    legs = {n: SimpleNamespace(fill_price=open_px)
            for n in ("short_call", "long_call", "short_put", "long_put")}
    return SimpleNamespace(entry_number=3, contracts=contracts, legs=legs,
                           realized_pnl=0.0)


def _strat(close_px, filled=True):
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = False
    s.commission_per_leg = 1.15
    s.daily_state = SimpleNamespace(total_commission=0.0, total_realized_pnl=0.0)
    s.registry = MagicMock()
    s._cancel_order = MagicMock()
    s._close_leg_order = MagicMock(return_value={
        "filled": filled, "order_id": "X1", "fill_price": close_px})
    s.booked = []
    def _book(amount, entry=None):
        s.booked.append(amount)
        s.daily_state.total_realized_pnl += amount
        if entry is not None:
            entry.realized_pnl = getattr(entry, "realized_pnl", 0.0) + amount
    s._book_realized_pnl = _book
    return s


class TestRoundTripPricePnl:
    def test_short_leg_bought_back_CHEAPER_is_a_gain(self):
        """Sold at 1.00, bought back at 0.60 -> +0.40 x 100 x 7 = +$280."""
        s = _strat(close_px=0.60)
        e = _entry(contracts=7, open_px=1.00)
        HydraStrategy._unwind_partial_entry(s, [("short_call", "p1", 111)], e)
        assert s.booked == [pytest.approx(280.0)]

    def test_short_leg_bought_back_DEARER_is_a_loss(self):
        """The realistic market-order case: cross the spread both ways."""
        s = _strat(close_px=1.40)
        HydraStrategy._unwind_partial_entry(
            s, [("short_call", "p1", 111)], _entry(contracts=7, open_px=1.00))
        assert s.booked == [pytest.approx(-280.0)]

    def test_long_leg_sign_is_INVERTED_relative_to_short(self):
        """A long bought at 1.00 and sold at 0.60 LOSES — the opposite sign to
        the short case above with identical prices. Getting this backwards
        would silently flip the P&L of every unwound long."""
        s = _strat(close_px=0.60)
        HydraStrategy._unwind_partial_entry(
            s, [("long_call", "p1", 111)], _entry(contracts=7, open_px=1.00))
        assert s.booked == [pytest.approx(-280.0)]

    def test_scales_with_contracts(self):
        s = _strat(close_px=0.60)
        HydraStrategy._unwind_partial_entry(
            s, [("short_put", "p1", 111)], _entry(contracts=10, open_px=1.00))
        assert s.booked == [pytest.approx(400.0)]

    def test_each_unwound_leg_books_separately(self):
        """The 2026-09-09 failures unwound 2 and 3 legs."""
        s = _strat(close_px=0.60)
        e = _entry(contracts=7, open_px=1.00)
        HydraStrategy._unwind_partial_entry(
            s, [("long_call", "p1", 111), ("long_put", "p2", 222)], e)
        assert len(s.booked) == 2
        assert e.realized_pnl == pytest.approx(-560.0)   # two longs, both losing

    def test_commission_is_STILL_booked(self):
        """Regression guard for the 2026-08-20 fix this builds on."""
        s = _strat(close_px=0.60)
        HydraStrategy._unwind_partial_entry(
            s, [("short_call", "p1", 111)], _entry(contracts=7))
        assert s.daily_state.total_commission == pytest.approx(2 * 1.15 * 7)


class TestItNeverBreaksTheUnwind:
    def test_a_missing_close_fill_price_books_commission_but_not_pnl(self):
        """Closing the leg matters more than measuring it. A close that filled
        without reporting a price must NOT lose the commission booking or raise."""
        s = _strat(close_px=None)
        HydraStrategy._unwind_partial_entry(
            s, [("short_call", "p1", 111)], _entry(contracts=7))
        assert s.booked == []
        assert s.daily_state.total_commission == pytest.approx(2 * 1.15 * 7)

    def test_a_missing_open_fill_price_is_tolerated(self):
        s = _strat(close_px=0.60)
        e = _entry(contracts=7, open_px=0.0)
        HydraStrategy._unwind_partial_entry(s, [("short_call", "p1", 111)], e)
        assert s.booked == []

    def test_an_unfilled_close_books_nothing_and_cancels_the_leftover(self):
        """No close, no round trip — and the working order must be cancelled."""
        s = _strat(close_px=0.60, filled=False)
        HydraStrategy._unwind_partial_entry(
            s, [("short_call", "p1", 111)], _entry(contracts=7))
        assert s.booked == []
        assert s.daily_state.total_commission == 0.0
        assert s._cancel_order.call_count == 1

    def test_a_raising_book_call_does_not_abort_the_unwind(self):
        """Accounting must never cost us the close."""
        s = _strat(close_px=0.60)
        s._book_realized_pnl = MagicMock(side_effect=RuntimeError("db down"))
        HydraStrategy._unwind_partial_entry(
            s, [("short_call", "p1", 111), ("long_put", "p2", 222)],
            _entry(contracts=7))
        assert s._close_leg_order.call_count == 2   # both legs still closed


# ---------------------------------------------------------------------------
# The naked-short emergency close (2026-09-10)
# ---------------------------------------------------------------------------

class TestNakedShortBooksPnl:
    """This path closes a REAL position and booked nothing at all. It was
    invisible to the usual consistency check because it skews the day aggregate
    and the per-entry number EQUALLY, so the two still agreed.

    The gap was specifically on SUCCESS: on failure the leg stays in
    filled_legs and the unwind closes and books it; on success the position was
    already flat, the unwind's close could not fill, and the P&L vanished."""

    def _s(self, close_px, filled=True):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = False
        s.contracts_per_entry = 7
        s.requires_protective_wings = True
        s.registry = MagicMock()
        s._cancel_order = MagicMock()
        s._log_safety_event = MagicMock()
        s._trigger_critical_intervention = MagicMock()
        s.alert_service = MagicMock()
        s._close_leg_order = MagicMock(return_value={
            "filled": filled, "order_id": "N1", "fill_price": close_px})
        s.booked = []
        s._book_realized_pnl = lambda amt, entry=None: s.booked.append(amt)
        return s

    def test_a_successful_close_books_the_short_round_trip(self):
        """Sold at 2.00, bought back at 1.20 -> +0.80 x 100 x 7 = +$560."""
        s = self._s(close_px=1.20)
        e = _entry(contracts=7, open_px=2.00)
        assert HydraStrategy._handle_naked_short(s, ("short_call", "p1", 111), e) is True
        assert s.booked == [pytest.approx(560.0)]

    def test_buying_back_DEARER_is_a_loss(self):
        s = self._s(close_px=2.80)
        assert HydraStrategy._handle_naked_short(
            s, ("short_put", "p1", 111), _entry(contracts=7, open_px=2.00)) is True
        assert s.booked == [pytest.approx(-560.0)]

    def test_a_FAILED_close_books_nothing_and_returns_False(self):
        """Returning False keeps the leg in filled_legs so the unwind still
        closes and books it — the two paths must not both skip it."""
        s = self._s(close_px=1.20, filled=False)
        assert HydraStrategy._handle_naked_short(
            s, ("short_call", "p1", 111), _entry()) is False
        assert s.booked == []
        assert s._trigger_critical_intervention.call_count == 1

    def test_no_entry_still_closes_the_position(self):
        """The emergency close must NEVER be blocked by missing accounting."""
        s = self._s(close_px=1.20)
        assert HydraStrategy._handle_naked_short(s, ("short_call", "p1", 111)) is True
        assert s._close_leg_order.call_count == 1
        assert s.booked == []

    def test_a_raising_book_call_does_not_undo_the_close(self):
        s = self._s(close_px=1.20)
        s._book_realized_pnl = MagicMock(side_effect=RuntimeError("db down"))
        assert HydraStrategy._handle_naked_short(
            s, ("short_call", "p1", 111), _entry()) is True

    def test_undefined_risk_strategies_are_still_skipped(self):
        """Variant G holds naked shorts BY DESIGN — this must stay a no-op for
        requires_protective_wings=False, and must not report a close."""
        s = self._s(close_px=1.20)
        s.requires_protective_wings = False
        assert HydraStrategy._handle_naked_short(
            s, ("short_call", "p1", 111), _entry()) in (False, None)
        assert s._close_leg_order.call_count == 0
