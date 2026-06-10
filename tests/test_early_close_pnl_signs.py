"""2026-06-09 (10-agent verification): the Brandon early-close (TP / GEX-breach /
MKT-018) per-side P&L booking must book a NEGATIVE-credit side too. The old
`credit > 0` gate dropped a spread sold at a net DEBIT (e.g. E#1's down-day
legged call spread, credit −$385) from total_realized_pnl, overstating the day
by the omitted loss. Reproduces the exact E#1/E#2 reconciliation."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
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


class TestCloseEntryEarlyWritesRealCost:
    """2026-06-10: _close_entry_early must write the REAL fill-based net close
    cost into actual_*_stop_debit (the dashboard's P&L input) — the Brandon TP
    path used to overwrite it with the pre-close spread MARK, so live cards
    overstated a take-profit's P&L (mark $87.50 vs real $140)."""

    def _entry(self):
        return SimpleNamespace(
            entry_number=1, contracts=7,
            short_call_uic=1, long_call_uic=2, short_put_uic=3, long_put_uic=4,
            short_call_position_id=None, long_call_position_id=None,
            short_put_position_id=None, long_put_position_id=None,
            call_side_stopped=False, put_side_stopped=False,
            call_side_expired=False, put_side_expired=False,
            call_side_skipped=False, put_side_skipped=False,
            call_only=False, put_only=False,
            call_spread_credit=-385.0, put_spread_credit=1050.0,
            call_spread_value=87.5, put_spread_value=17.5,   # the pre-close MARK
            actual_call_stop_debit=0.0, actual_put_stop_debit=0.0,
            close_commission=0.0, close_time="", early_closed=False,
            is_complete=False,
        )

    def test_actual_debit_is_real_cost_not_mark(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.commission_per_leg = 0.65
        s.daily_state = SimpleNamespace(total_commission=0.0, total_realized_pnl=0.0)
        s._read_option_quote = MagicMock(return_value={"bid": 1.0, "ask": 1.1})
        s._book_early_close_side_pnl = MagicMock()
        s._record_stop_to_db = MagicMock()
        # real fills → call: buy short 0.55, sell long 0.35 → 0.20*700 = $140
        #               put : buy short 0.40, sell long 0.30 → 0.10*700 = $70
        fills = {"short_call": 0.55, "long_call": 0.35, "short_put": 0.40, "long_put": 0.30}

        def fake_close(pos_id, leg_name, uic=None, entry_number=None, contracts=None):
            return (True, fills[leg_name], "O")

        s._close_position_with_retry = fake_close
        entry = self._entry()
        with patch("bots.hydra.strategy.get_us_market_time",
                   return_value=datetime(2026, 6, 9, 15, 11)):
            s._close_entry_early(entry)

        # REAL fill-based costs, NOT the marks (87.5 / 17.5).
        assert entry.actual_call_stop_debit == pytest.approx(140.0)
        assert entry.actual_put_stop_debit == pytest.approx(70.0)
