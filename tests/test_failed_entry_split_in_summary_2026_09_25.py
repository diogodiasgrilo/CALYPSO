"""The day's headline must not silently mix failed-entry P&L with the strategy's.

WHY. On 2026-09-24 the live seat's card read **+$3,565** while the strategy
itself had **LOST $2,430**. A broken entry — legs filled, entry aborted, legs
unwound — happened to unwind into a headline-driven melt-up for +$5,995.

Answering "why is this positive?" took a full day of forensics. The next day
the identical broken entry lost $468. One opaque number cannot tell those two
apart, and only one of them is the strategy's result or repeatable at all.

So the summary now carries the split. Shown only when non-zero, so an ordinary
day is unchanged.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dashboard", "backend")))

from dashboard.backend.routers.variants import _summary_from_state  # noqa: E402


def _state(realized, commission, unattributed, entries=None):
    return {
        "date": "2026-09-24",
        "state": "Monitoring",
        "entries": entries or [],
        "total_realized_pnl": realized,
        "total_commission": commission,
        "failed_entry_unattributed_pnl": unattributed,
    }


class TestTheSplit:
    def test_reproduces_2026_09_24(self):
        """gross 3565, commission 233.45, of which 5995 was a failed entry."""
        out = _summary_from_state(_state(3565.0, 233.45, 5995.0))
        assert round(out["net_pnl"], 2) == 3331.55
        assert round(out["unattributed_pnl"], 2) == 5995.00
        assert round(out["trading_pnl"], 2) == -2663.45

    def test_the_parts_reconcile_to_the_headline(self):
        out = _summary_from_state(_state(3565.0, 233.45, 5995.0))
        assert round(out["trading_pnl"] + out["unattributed_pnl"], 2) == round(out["net_pnl"], 2)

    def test_an_ordinary_day_has_a_zero_split(self):
        """No failed entry -> trading == net, and the card shows nothing extra."""
        out = _summary_from_state(_state(750.0, 138.0, 0.0))
        assert out["unattributed_pnl"] == 0.0
        assert round(out["trading_pnl"], 2) == round(out["net_pnl"], 2) == 612.00

    def test_a_LOSING_failed_entry_is_split_too(self):
        """2026-09-25's failed entry lost $468 — the split must work both ways."""
        out = _summary_from_state(_state(-1000.0, 50.0, -468.0))
        assert round(out["unattributed_pnl"], 2) == -468.00
        assert round(out["trading_pnl"], 2) == -582.00


class TestBackCompat:
    def test_a_state_file_predating_the_field(self):
        s = _state(100.0, 10.0, 0.0)
        del s["failed_entry_unattributed_pnl"]
        out = _summary_from_state(s)
        assert out["unattributed_pnl"] == 0.0
        assert round(out["trading_pnl"], 2) == 90.00

    def test_a_null_value_is_treated_as_zero(self):
        out = _summary_from_state(_state(100.0, 10.0, None))
        assert out["unattributed_pnl"] == 0.0

    def test_net_pnl_itself_is_UNCHANGED(self):
        """CONTROL. The headline must keep its existing meaning — this adds a
        breakdown, it does not redefine the number people already read."""
        out = _summary_from_state(_state(3565.0, 233.45, 5995.0))
        assert round(out["net_pnl"], 2) == round(3565.0 - 233.45, 2)
