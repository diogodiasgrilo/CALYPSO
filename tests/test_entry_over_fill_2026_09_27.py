"""The cancel/fill race bites when OPENING a leg too, not just closing one.

2026-09-24, entry path: the rung loop cancelled a partial at 5/7, two more
filled in the gap before the cancel took effect, the escalation then bought the
2 it believed were still missing — and the account ended up LONG 9 where 7 were
intended. Two contracts sat untracked until expiry. Proof it was the cancelled
order: the residual's avgPrice was $0.3008, the cancelled rung's $0.30 lot, not
the escalation's $0.25.

I sized that at "~$60 exposure" and deferred it. The identical race on the EXIT
path then cost ~$4,200 (2026-09-25: short 7 became long 7, flattened at a
$6.24/contract loss). Same bug, same arithmetic — so the entry path now gets
the same correction rather than waiting for a third demonstration.

The correction is shared with the close path (`_correct_over_fill`): compare
the broker's ACTUAL change at the conid against the INTENDED one and undo only
the excess. It is one-directional — it can undo trading too much, never "top
up" an under-fill, because an under-fill is the rung loop's own job and a
safety net must not be able to open a position.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.base_strategy import MEICStrategy  # noqa: E402


class _S(MEICStrategy):
    def _calculate_strikes(self, *a, **k): raise NotImplementedError
    def _check_stop_losses(self, *a, **k): raise NotImplementedError
    def _initiate_entry(self, *a, **k): raise NotImplementedError


def _rig(qty_after):
    s = _S.__new__(_S)
    s.orders = []
    s.orphans = []
    s.alert_service = MagicMock()
    s.alert_service.send_alert = MagicMock()
    s._read_open_positions = MagicMock(return_value=(
        [{"instrument_id": 111, "quantity": qty_after}] if qty_after else []))

    def _place(**kw):
        s.orders.append(kw)
        return {"filled": True, "order_id": "fix"}
    s._place_leg_order = _place
    s._add_orphaned_order = lambda x: s.orphans.append(x)
    return s


class TestTheEntryRace:
    def test_the_2026_09_24_case(self):
        """Started flat, believed 7 bought, broker shows 9 -> sell the 2."""
        s = _rig(qty_after=+9)
        s._correct_over_fill(111, "BUY", qty_before=0, intended_qty=7,
                             leg_name="long_put")
        assert len(s.orders) == 1
        assert s.orders[0]["side"] == "SELL" and s.orders[0]["quantity"] == 2

    def test_a_clean_leg_does_nothing(self):
        s = _rig(qty_after=+7)
        s._correct_over_fill(111, "BUY", qty_before=0, intended_qty=7,
                             leg_name="long_put")
        assert s.orders == []

    def test_a_SHORT_leg_over_fill_is_corrected(self):
        """Selling to open, and selling too much."""
        s = _rig(qty_after=-9)
        s._correct_over_fill(111, "SELL", qty_before=0, intended_qty=7,
                             leg_name="short_call")
        assert len(s.orders) == 1
        assert s.orders[0]["side"] == "BUY" and s.orders[0]["quantity"] == 2

    def test_a_failed_leg_that_actually_filled_is_caught(self):
        """The nastiest shape: the leg reports NOTHING filled, but a cancelled
        rung filled anyway. intended = 0, so every contract held is excess."""
        s = _rig(qty_after=+5)
        s._correct_over_fill(111, "BUY", qty_before=0, intended_qty=0,
                             leg_name="long_put")
        assert len(s.orders) == 1
        assert s.orders[0]["side"] == "SELL" and s.orders[0]["quantity"] == 5

    def test_a_sibling_entry_at_the_same_strike_is_NOT_disturbed(self):
        """CONTROL. 74% of B's days have two entries sharing a strike. Starting
        from a sibling's -7 and buying 7 correctly ends at 0 — a naive check
        would see 'flat' and do something."""
        s = _rig(qty_after=0)
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="long_put")
        assert s.orders == []

    def test_an_under_fill_is_never_topped_up(self):
        """CONTROL. Believed 7, broker shows 5 — that is the rung loop's job.
        A safety net must not be able to OPEN a position."""
        s = _rig(qty_after=+5)
        s._correct_over_fill(111, "BUY", qty_before=0, intended_qty=7,
                             leg_name="long_put")
        assert s.orders == []


class TestWiredIntoTheEntryPath:
    """The correction must actually be reachable from _place_option_order_ib —
    the helper being right is worth nothing if the entry path never calls it."""

    def test_all_three_exits_call_it(self):
        import inspect
        src = inspect.getsource(MEICStrategy._place_option_order_ib)
        assert src.count("_correct_over_fill(") == 3, (
            "entry path should correct on all three exits: leg-whole, "
            "flatten-partial, and failed-all-rungs")
        assert "_entry_qty_before = int(" in src, "no pre-placement baseline"

    def test_the_baseline_read_is_STRICT(self):
        """A cached or swallowed read would silently disable detection."""
        import inspect
        src = inspect.getsource(MEICStrategy._place_option_order_ib)
        i = src.index("_entry_qty_before = int(")
        assert "strict=True" in src[i:i + 220]
