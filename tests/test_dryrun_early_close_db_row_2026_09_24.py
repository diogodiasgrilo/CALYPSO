"""A dry-run early close must not record the full credit as the result.

FOUND 2026-09-24 auditing every variant for the accounting class that bit B.
Variant F took a take-profit at 12:24:17. Its state was right; its DB row was
not:

    F state realized_pnl        =  $92.50      (credit 167.50 - close 75.00)
    F trade_stops.net_pnl       = $167.50      <-- the CREDIT, not the P&L
    F trade_stops.quoted_mid_at_stop = $75.00  <-- the cost, on the SAME row

In dry-run there is no fill, so ``side_close_cost`` reaches
``_record_stop_to_db`` as 0 and it books ``net_pnl = -(0 - credit)`` = the full
credit, as though the position closed for free. The callers (Ghauri's TP,
Brandon's TP/breach) already correct the in-memory total by subtracting the
pre-close mark — but they run AFTER the write. That ordering is the KNOWN GAP
the ``_eod_flatten_dry_run_correct`` docstring records.

Dry-run only by construction, so the live seat was never affected — but HOMER,
HERMES, CLIO and the dashboard's Analytics -> Stops tab all read that table, so
every dry-run variant's recorded history overstated its take-profits.

CONTROLS: ``test_live_path_is_untouched`` (a fix that fired on real fills would
corrupt the live seat's record) and ``test_booking_is_NOT_double_corrected``
(the callers subtract the mark themselves; correcting the booking here too
would double-subtract).
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _rig(dry_run: bool, mark: float = 75.0):
    """Drives the REAL ``_close_entry_early``.

    Deliberately not a replay of the block under test: an earlier draft of this
    file re-implemented the logic and asserted on the copy, which would have
    stayed green against a gutted method. Only ``_record_stop_to_db`` and
    ``_book_early_close_side_pnl`` are stubbed — they are the two recorded
    effects this test is about — plus the broker-touching close/quote calls.
    """
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = dry_run
    s.commission_per_leg = 1.15
    s.daily_state = SimpleNamespace(total_commission=0.0)
    s.recorded = []
    s.booked = []
    s._record_stop_to_db = lambda e, side, lvl, cost, exit_reason=None: s.recorded.append(
        {"side": side, "cost": cost, "reason": exit_reason})
    s._book_early_close_side_pnl = lambda e, side, credit, cost: s.booked.append(
        {"side": side, "credit": credit, "cost": cost})
    s._read_option_quote = MagicMock(return_value={"bid": 0.75, "ask": 0.80})
    s._alert_short_close_failed = MagicMock()
    entry = SimpleNamespace(
        entry_number=1, close_reason="TP", contracts=1, close_commission=0.0,
        call_side_stop=1400.0, call_spread_value=mark, call_spread_credit=167.50,
        put_spread_credit=0.0, put_spread_value=0.0, put_side_stop=0.0,
        call_side_stopped=False, put_side_stopped=True,
        call_side_expired=False, put_side_expired=True,
        call_side_skipped=False, put_side_skipped=True,
        short_call_uic=101, long_call_uic=102,
        short_put_uic=None, long_put_uic=None,
        short_call_position_id=None, long_call_position_id=None,
        close_time="", is_complete=False, call_only=False, put_only=False,
    )
    return s, entry


def _close(s, entry, fills=None):
    """Run the real close.

    ``fills`` is the per-leg fill PRICE sequence in close order (short first,
    then long). ``None`` reproduces dry-run's "no simulated fill", which is the
    condition that makes side_close_cost land on 0.

    Note the sign convention this exercises: side_close_cost ACCUMULATES the
    short buy-back and SUBTRACTS the long sale (strategy.py ~3823), so equal
    fills on both legs net to exactly 0 — which is how an earlier draft of this
    test accidentally drove the dry-run branch while believing it was live.
    """
    if fills is None:
        s._close_position_with_retry = MagicMock(return_value=(True, None, "oid"))
    else:
        seq = list(fills)
        s._close_position_with_retry = MagicMock(
            side_effect=[(True, p, f"oid{i}") for i, p in enumerate(seq)])
    return HydraStrategy._close_entry_early(s, entry)


class TestDryRunRow:
    def test_reproduces_and_fixes_the_F_row(self):
        """F on 2026-09-24: credit 167.50, mark 75.00 -> the row must carry 75."""
        s, e = _rig(dry_run=True, mark=75.0)
        _close(s, e, fills=None)
        assert s.recorded and s.recorded[0]["cost"] == 75.0

    def test_booking_is_NOT_double_corrected(self):
        """CONTROL. Ghauri/Brandon subtract the mark themselves afterwards;
        correcting the booking here would double-subtract."""
        s, e = _rig(dry_run=True, mark=75.0)
        _close(s, e, fills=None)
        assert s.booked and s.booked[0]["cost"] == 0.0, "the booking must keep the raw cost"

    def test_no_mark_available_leaves_it_alone(self):
        """Nothing to substitute -> behave exactly as before, not guess."""
        s, e = _rig(dry_run=True, mark=0.0)
        _close(s, e, fills=None)
        assert s.recorded and s.recorded[0]["cost"] == 0.0

    def test_reason_tag_is_preserved(self):
        s, e = _rig(dry_run=True, mark=75.0)
        _close(s, e, fills=None)
        assert s.recorded and s.recorded[0]["reason"] == "take_profit"


class TestLiveSeatUntouched:
    def test_live_path_is_untouched(self):
        """CONTROL. A real fill must record the REAL cost, never the mark."""
        s, e = _rig(dry_run=False, mark=75.0)
        _close(s, e, fills=[2.15, 0.75])   # (2.15-0.75)*100*1 = $140
        assert s.recorded and s.recorded[0]["cost"] == 140.0
        assert s.booked and s.booked[0]["cost"] == 140.0

    def test_dry_run_with_a_real_cost_also_untouched(self):
        """A dry-run path that DID produce a cost keeps it."""
        s, e = _rig(dry_run=True, mark=75.0)
        _close(s, e, fills=[2.15, 0.75])   # (2.15-0.75)*100*1 = $140
        assert s.recorded and s.recorded[0]["cost"] == 140.0
