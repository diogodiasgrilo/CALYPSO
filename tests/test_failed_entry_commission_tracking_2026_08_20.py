"""2026-08-20 (execution audit finding, variant B entry #6 on 2026-08-19):
a failed entry leg-in that gets safely unwound (zero naked-leg risk — that
part was always correct) previously left its round-trip commission cost
completely invisible in daily_state.total_commission. The reported day P&L
and commission understated the true economic cost of a failed attempt.

Covers both unwind paths:
- _unwind_partial_entry (base_strategy.py): fully-filled legs closed after an
  exception mid-leg-in.
- _flatten_accumulated_partial (base_strategy.py): an ORDER-010 accumulated
  partial (fewer than entry.contracts) market-closed after the fill-the-
  remainder loop exhausts its rungs.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import MEICDailyState  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _strat(commission_per_leg=1.15):
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = False
    s.commission_per_leg = commission_per_leg
    s.daily_state = MEICDailyState()
    s.registry = MagicMock()
    return s


class TestUnwindPartialEntryCommission:
    def test_successful_unwind_books_round_trip_commission(self):
        s = _strat()
        s._close_leg_order = MagicMock(return_value={"filled": True, "order_id": "O1"})
        entry = MagicMock(entry_number=6, contracts=7)
        filled_legs = [("short_call", None, 111), ("long_call", None, 222)]

        s._unwind_partial_entry(filled_legs, entry)

        # 2 legs x 2 (open never booked + this close) x $1.15 x 7c = $32.20
        assert s.daily_state.total_commission == pytest.approx(32.20)

    def test_failed_unwind_books_nothing(self):
        s = _strat()
        s._close_leg_order = MagicMock(return_value={"filled": False, "order_id": None})
        s._cancel_order = MagicMock()
        entry = MagicMock(entry_number=6, contracts=7)
        filled_legs = [("short_call", None, 111)]

        s._unwind_partial_entry(filled_legs, entry)

        assert s.daily_state.total_commission == 0.0

    def test_leg_with_no_conid_is_skipped_no_commission(self):
        s = _strat()
        s._close_leg_order = MagicMock()
        entry = MagicMock(entry_number=6, contracts=7)
        filled_legs = [("short_call", None, None)]  # uic=None — P7-audit H1 case

        s._unwind_partial_entry(filled_legs, entry)

        s._close_leg_order.assert_not_called()
        assert s.daily_state.total_commission == 0.0

    def test_dry_run_never_books_commission(self):
        s = _strat()
        s.dry_run = True
        s._close_leg_order = MagicMock(return_value={"filled": True, "order_id": "O1"})
        entry = MagicMock(entry_number=6, contracts=7)
        filled_legs = [("short_call", None, 111)]

        s._unwind_partial_entry(filled_legs, entry)

        s._close_leg_order.assert_not_called()
        assert s.daily_state.total_commission == 0.0


class TestFlattenAccumulatedPartialCommission:
    def test_successful_flatten_books_round_trip_commission_at_actual_filled_qty(self):
        s = _strat()
        s._place_leg_order = MagicMock(return_value={"filled": True, "order_id": "O2"})
        s._add_orphaned_order = MagicMock()

        s._flatten_accumulated_partial(
            conid=333, side="BUY", filled_qty=4, external_ref="ref1",
            coid_suffix="flat", leg_description="short_put",
        )

        # 1 leg x 2 (open never booked + this close) x $1.15 x 4 (ACTUAL partial
        # qty, not entry.contracts) = $9.20
        assert s.daily_state.total_commission == 9.20
        s._add_orphaned_order.assert_not_called()

    def test_failed_flatten_books_nothing_and_orphans(self):
        s = _strat()
        s._place_leg_order = MagicMock(return_value={"filled": False})
        s._add_orphaned_order = MagicMock()

        s._flatten_accumulated_partial(
            conid=333, side="BUY", filled_qty=4, external_ref="ref1",
            coid_suffix="flat", leg_description="short_put",
        )

        assert s.daily_state.total_commission == 0.0
        s._add_orphaned_order.assert_called_once()

    def test_zero_filled_qty_is_a_noop(self):
        s = _strat()
        s._place_leg_order = MagicMock()

        s._flatten_accumulated_partial(
            conid=333, side="BUY", filled_qty=0, external_ref="ref1",
            coid_suffix="flat", leg_description="short_put",
        )

        s._place_leg_order.assert_not_called()
        assert s.daily_state.total_commission == 0.0
