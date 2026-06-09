"""2026-06-09 (10-agent verification): the Brandon early-close (TP / GEX-breach /
MKT-018) per-side P&L booking must book a NEGATIVE-credit side too. The old
`credit > 0` gate dropped a spread sold at a net DEBIT (e.g. E#1's down-day
legged call spread, credit −$385) from total_realized_pnl, overstating the day
by the omitted loss. Reproduces the exact E#1/E#2 reconciliation."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _strat():
    s = HydraStrategy.__new__(HydraStrategy)
    s.daily_state = SimpleNamespace(total_realized_pnl=0.0)
    return s


class TestEarlyCloseSidePnlSigns:
    def test_positive_credit_side_books_net(self):
        s = _strat()
        s._book_early_close_side_pnl(SimpleNamespace(entry_number=1),
                                     "put", credit=1050.0, side_close_cost=70.0)
        assert s.daily_state.total_realized_pnl == 980.0

    def test_negative_credit_side_is_booked_not_dropped(self):
        # E#1 call spread sold at a NET DEBIT (credit −385); the old gate dropped
        # this −$525 loss entirely.
        s = _strat()
        s._book_early_close_side_pnl(SimpleNamespace(entry_number=1),
                                     "call", credit=-385.0, side_close_cost=140.0)
        assert s.daily_state.total_realized_pnl == -525.0

    def test_full_day_reconciles_to_broker(self):
        # Reproduce the full 2026-06-09 day: E#1 (call −525, put +980) +
        # E#2 (call −70, put +525) → +910 gross (matches broker +909.02),
        # NOT the buggy +1435 (which dropped E#1's call −525).
        s = _strat()
        e1 = SimpleNamespace(entry_number=1)
        e2 = SimpleNamespace(entry_number=2)
        s._book_early_close_side_pnl(e1, "put", 1050.0, 70.0)    # +980
        s._book_early_close_side_pnl(e1, "call", -385.0, 140.0)  # -525 (was dropped!)
        s._book_early_close_side_pnl(e2, "call", 105.0, 175.0)   # -70
        s._book_early_close_side_pnl(e2, "put", 560.0, 35.0)     # +525
        assert s.daily_state.total_realized_pnl == 910.0
        # The bug would have produced 1435.0 (omitting the -525 call side).
        assert s.daily_state.total_realized_pnl != 1435.0

    def test_negative_credit_deferred_no_close_cost(self):
        # No close fill yet (cost 0) → book the (negative) credit; deferred
        # lookup corrects later. Old gate dropped it.
        s = _strat()
        s._book_early_close_side_pnl(SimpleNamespace(entry_number=3),
                                     "call", credit=-200.0, side_close_cost=0.0)
        assert s.daily_state.total_realized_pnl == -200.0
