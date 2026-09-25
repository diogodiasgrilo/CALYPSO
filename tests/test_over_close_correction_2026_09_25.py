"""Detect and undo a close that traded MORE than it asked for.

THE INCIDENT, live seat, 2026-09-25 12:05. E#3's stop had to buy back 7 short
calls:

    12:05:54  BUY 7 @ $9.60   attempt 1
    12:06:14  "did not fill"  -> cancelled after 20s
    12:06:22  BUY 7 @ $8.80   attempt 2 -> filled $8.79

It was short 7. It ended LONG 7 — so 14 were bought. The cancelled order filled
in the gap between the cancel being sent and the exchange acting on it.

The strategy believed the leg had closed and cleared its conids, so nothing
managed the result. It surfaced only as a CRITICAL orphan alert, and by the
time it was flattened the call had decayed $4.00 -> $2.55.

Same race hit the ENTRY side on 09-24 (bought 9 puts, intended 7, 2 stranded).
It was deferred then as "~$60 exposure". That was wrong.

WHY A DELTA, NOT "DID WE END UP FLAT". A conid is not one entry: IBKR merges at
(conid, side), and **74% of B's trading days have two entries sharing a
strike**. Ending long after closing a short is perfectly correct when a sibling
entry holds the long — `test_sibling_entry_at_the_same_conid_is_NOT_touched`
is the control for that, and a naive flat-check fails it.

Sound because the bot is single-threaded: no other order of ours can move the
conid while a close runs.
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


def _rig(qty_after, fill_ok=True):
    s = _S.__new__(_S)
    s.orders = []
    s.orphans = []
    s.alerts = []
    s._read_open_positions = MagicMock(return_value=(
        [{"instrument_id": 111, "quantity": qty_after}] if qty_after else []))

    def _place(**kw):
        s.orders.append(kw)
        return {"filled": fill_ok, "order_id": "fix1"}
    s._place_leg_order = _place
    s._add_orphaned_order = lambda x: s.orphans.append(x)
    s.alert_service = MagicMock()
    s.alert_service.send_alert = lambda **kw: s.alerts.append(kw)
    return s


class TestTheIncident:
    def test_the_real_case_is_detected_and_corrected(self):
        """Short 7, BUY 7 to close, ended LONG 7 -> 7 too many bought."""
        s = _rig(qty_after=+7)
        s._correct_over_close(111, "BUY", qty_before=-7, intended_qty=7,
                              leg_name="short_call")
        assert len(s.orders) == 1, "over-close not corrected"
        assert s.orders[0]["side"] == "SELL"
        assert s.orders[0]["quantity"] == 7
        assert s.orders[0]["is_exit"] is True

    def test_a_clean_close_does_nothing(self):
        """Short 7, BUY 7, ended flat — the normal path must not trade."""
        s = _rig(qty_after=0)
        s._correct_over_close(111, "BUY", qty_before=-7, intended_qty=7,
                              leg_name="short_call")
        assert s.orders == []

    def test_sibling_entry_at_the_same_conid_is_NOT_touched(self):
        """CONTROL, and the reason this is a delta check.

        Two entries share the strike: one long 7, one short 7, so the conid nets
        to 0. Closing the short (BUY 7) correctly leaves the sibling's LONG 7
        behind. A naive 'we ended long after closing a short, so flatten it'
        would sell a position that belongs to another entry.
        """
        s = _rig(qty_after=+7)
        s._correct_over_close(111, "BUY", qty_before=0, intended_qty=7,
                              leg_name="short_call")
        assert s.orders == [], "flattened a sibling entry's position"

    def test_over_sell_on_a_long_close_is_corrected(self):
        """Mirror case: closing a LONG by selling, and selling too much."""
        s = _rig(qty_after=-5)
        s._correct_over_close(111, "SELL", qty_before=+7, intended_qty=7,
                              leg_name="long_put")
        assert len(s.orders) == 1
        assert s.orders[0]["side"] == "BUY" and s.orders[0]["quantity"] == 5


class TestItNeverBreaksTheClose:
    def test_a_failed_position_read_does_not_assume_an_over_close(self):
        """Fails SAFE: an unreadable book must not trigger a corrective trade.

        The scenario deliberately has a NON-ZERO expected result (a sibling
        entry holds +7, so closing a short by buying 7 should end at +14). An
        earlier version of this test used an expectation of 0, which happens to
        equal the 'treat a failed read as flat' fallback — so it passed whether
        the code returned early or not, and proved nothing. With +14 expected, a
        swallowed read would compute excess = -14 and BUY 14 more contracts on
        data it never actually had.
        """
        s = _rig(qty_after=0)
        s._read_open_positions = MagicMock(side_effect=RuntimeError("broker down"))
        s._correct_over_close(111, "BUY", qty_before=+7, intended_qty=7,
                              leg_name="short_call")
        assert s.orders == [], "traded on an unreadable position book"

    def test_a_failed_correction_alerts_and_orphans(self):
        """If the fix itself will not fill, a human must be told."""
        s = _rig(qty_after=+7, fill_ok=False)
        s._correct_over_close(111, "BUY", qty_before=-7, intended_qty=7,
                              leg_name="short_call")
        assert s.orphans == ["A2_111"]
        assert s.alerts and "MANUAL FLATTEN" in s.alerts[0]["message"]

    def test_a_raising_correction_does_not_propagate(self):
        s = _rig(qty_after=+7)

        def _boom(**kw):
            raise RuntimeError("order rejected")
        s._place_leg_order = _boom
        s._correct_over_close(111, "BUY", qty_before=-7, intended_qty=7,
                              leg_name="short_call")
        assert s.orphans == ["A2_111"]


class TestItOnlyEverUndoesOverTrading:
    """It must never be able to OPEN a position.

    Found by an existing close test, not by design: its rig yields an empty
    position read, giving qty_before 0 / expected +7 / actual 0 — excess -7.
    The first version of this code would have BOUGHT 7 more contracts on the
    strength of a read that told it nothing. An under-close is the retry loop's
    job; this check exists solely to undo trading too much.
    """

    def test_an_under_close_is_NOT_finished_here(self):
        s = _rig(qty_after=0)
        s._correct_over_close(111, "BUY", qty_before=0, intended_qty=7,
                              leg_name="short_call")
        assert s.orders == [], "tried to complete an incomplete close"

    def test_an_under_close_on_a_SELL_is_also_ignored(self):
        s = _rig(qty_after=+7)
        s._correct_over_close(111, "SELL", qty_before=+7, intended_qty=7,
                              leg_name="long_put")
        assert s.orders == []


class TestNetQtyHelper:
    def test_sums_rather_than_picks(self):
        """One conid is one NET number — IBKR merges at (conid, side)."""
        s = _rig(qty_after=0)
        rows = [{"instrument_id": 111, "quantity": -7},
                {"instrument_id": 111, "quantity": 7},
                {"instrument_id": 222, "quantity": 5}]
        assert s._net_qty_at_conid(111, rows) == 0
        assert s._net_qty_at_conid(222, rows) == 5

    def test_absent_conid_is_flat(self):
        s = _rig(qty_after=0)
        assert s._net_qty_at_conid(999, []) == 0
