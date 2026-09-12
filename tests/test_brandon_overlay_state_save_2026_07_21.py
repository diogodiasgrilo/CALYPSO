"""2026-07-21: Brandon log_daily_summary must persist state AFTER settling overlays.

Bug: the last state save of the day was POS-004's IC-only settlement, so the on-disk
total_realized_pnl / entry.realized_pnl / brandon_overlay_booked stayed PRE-overlay
while the DB + metrics captured the full total. The dashboard "today" card reads the
state file, so it showed the IC-only P&L (+$1,795) while the cumulative jumped by the
overlay-inclusive total (+$11,852) — the same screen contradicting itself. Fix: save
state right after _brandon_settle_hedges so the disk state matches the DB.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _stub(close):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    calls = []
    s._resolve_spx_close = lambda: close
    s._brandon_settle_hedges = lambda spx: calls.append("settle")
    s._save_state_to_disk = lambda: calls.append("save")
    return s, calls


class TestOverlayStateSave:
    def test_state_saved_after_settle_before_super(self):
        s, calls = _stub(7500.0)
        with patch.object(HydraStrategy, "log_daily_summary", lambda self: calls.append("super")):
            BrandonHydraStrategy.log_daily_summary(s)
        # The save must land AFTER the overlay settlement (so it captures the overlay
        # booking) and the day summary still runs.
        assert calls == ["settle", "save", "super"]
        assert calls.index("save") > calls.index("settle")

    def test_no_settle_or_save_when_close_invalid(self):
        s, calls = _stub(0.0)  # _resolve_spx_close returned an unusable value
        with patch.object(HydraStrategy, "log_daily_summary", lambda self: calls.append("super")):
            BrandonHydraStrategy.log_daily_summary(s)
        assert calls == ["super"]  # no overlay settlement, no premature save

    def test_save_skipped_if_settle_raises_but_summary_still_runs(self):
        s, calls = _stub(7500.0)
        def boom(spx):
            raise RuntimeError("settle blew up")
        s._brandon_settle_hedges = boom
        with patch.object(HydraStrategy, "log_daily_summary", lambda self: calls.append("super")):
            BrandonHydraStrategy.log_daily_summary(s)
        # save is inside the try AFTER settle → not reached on raise; summary still logs.
        assert "save" not in calls
        assert calls == ["super"]
