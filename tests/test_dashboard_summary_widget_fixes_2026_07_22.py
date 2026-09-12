"""Dashboard audit fixes (2026-07-22):
1. /api/widget entry dots distinguish SKIPPED from expired (a no-trade skip is
   not a kept-credit win).
2. /api/hydra/summary delegates to the canonical _summary_from_state, so
   active_entries is gated on the per-side live flags (NOT is_complete, which
   goes True at placement) and net_pnl is LIVE (realized + unrealized − comm).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

pytest.importorskip("fastapi")

from dashboard.backend.routers.widget import _entry_dot  # noqa: E402
from dashboard.backend.routers.variants import _summary_from_state  # noqa: E402


class TestWidgetEntryDot:
    def test_skipped_entry_is_skipped_not_expired(self):
        e = {"is_complete": True, "call_side_skipped": True, "put_side_skipped": True}
        assert _entry_dot(e) == "skipped"

    def test_stopped_entry(self):
        assert _entry_dot({"is_complete": True, "call_side_stopped": True}) == "stopped"

    def test_kept_credit_expiry_still_expired(self):
        # a genuine full-credit expiry (complete, not stopped, not skipped)
        assert _entry_dot({"is_complete": True}) == "expired"

    def test_monitoring_and_pending(self):
        assert _entry_dot({"is_complete": False, "entry_time": "2026-07-22T10:00"}) == "active"
        assert _entry_dot({"is_complete": False}) == "pending"


class TestHydraSummaryActiveAndNet:
    def _monitoring_state(self):
        # An entry that is is_complete=True (set at placement) but whose put side
        # is still live — must count as 1 active, and its unrealized must land in
        # net_pnl.
        return {
            "date": "2026-07-22",
            "total_realized_pnl": -345.0,
            "total_commission": 6.9,
            "entries": [{
                "entry_number": 1,
                "is_complete": True,
                "call_side_stopped": True,          # call done
                "put_side_stopped": False,          # put still live
                "put_spread_credit": 145.0,
                "put_spread_value": 15.0,           # cost-to-close now
            }],
        }

    def test_active_entries_counts_live_side_despite_is_complete(self):
        s = _summary_from_state(self._monitoring_state())
        assert s["active_entries"] == 1   # is_complete=True but put side live

    def test_net_pnl_includes_unrealized(self):
        s = _summary_from_state(self._monitoring_state())
        # unrealized = put credit 145 - value 15 = 130
        assert s["unrealized_pnl"] == pytest.approx(130.0)
        # net = realized -345 + unrealized 130 - commission 6.9 = -221.9
        assert s["net_pnl"] == pytest.approx(-221.9)

    def test_flat_skip_day_nets_zero(self):
        state = {
            "date": "2026-07-22", "total_realized_pnl": 0.0, "total_commission": 0.0,
            "entries": [{"entry_number": 1, "is_complete": True,
                         "call_side_skipped": True, "put_side_skipped": True}],
        }
        s = _summary_from_state(state)
        assert s["net_pnl"] == 0.0
        assert s["active_entries"] == 0   # skipped side is not active
