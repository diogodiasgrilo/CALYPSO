"""A cancelled order reported 0 filled and we believed it. Cost: $410.83.

Live on `bl`, 2026-11-10, entry #5 — the whole defect in three log lines:

    14:16:40  Cancelling working order ...702 (5/7 filled) before next rung
    14:16:46  ⚠ Put 7460.0 not filled (0/7) — retrying (ORDER-009 1.2s delay)
    14:16:49  ✓ Filled Put 7460.0 7/7 @ avg $0.65

Five contracts HAD filled — A2 independently saw the broker at `+5`. But the
post-cancel status reported 0, `_await_terminal_fill` overwrote the observed
5 with it, and the retry sized a FULL 7 instead of the remaining 2. The
surplus was never tracked, never stopped, and expired worthless.

IBKR's own reporting is the trap: on a cancel-after-partial the completed-order
report repurposes `totalQuantity` as the UNFILLED remainder, and resubmission
can reset the filled count to 0. So the post-cancel read says nothing reliable
about what already executed.

A cumulative fill cannot decrease. The clamp encodes that, and it is
deliberately asymmetric: under-counting re-buys a whole leg and leaves
untracked exposure with no stop on it; over-counting buys too little, the leg
stays short, the entry fails and the unwind cleans up.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.base_strategy import MEICStrategy  # noqa: E402


def _strat(statuses):
    """Drives the REAL `_await_terminal_fill` over a scripted status sequence."""
    class _C(MEICStrategy):
        def _calculate_strikes(self, *a, **k): ...
        def _check_stop_losses(self, *a, **k): ...
        def _initiate_entry(self, *a, **k): ...
    s = _C.__new__(_C)
    s._seq = list(statuses)
    s._get_order_status = lambda oid: s._seq.pop(0) if s._seq else {}
    s.ORDER_SETTLE_TIMEOUT_S = 2.0
    s.ORDER_SETTLE_POLL_S = 0.01
    return s


class TestTheIncident:
    def test_a_cancelled_order_reporting_zero_does_not_erase_the_partial(self):
        """The exact 2026-11-10 shape: observed 5, cancelled status says 0."""
        s = _strat([{"filled_quantity": 0, "status": "cancelled"}])
        filled, settled = MEICStrategy._await_terminal_fill(
            s, "oid", fallback=5, cancel_confirmed=True)
        assert filled == 5, (
            f"got {filled}: the retry would re-buy a FULL leg and strand the "
            f"surplus, which is what cost $410.83")
        assert settled is True

    def test_the_remainder_filling_during_cancel_IS_believed(self):
        """An INCREASE is real — the rest can fill before the cancel lands."""
        s = _strat([{"filled_quantity": 7, "status": "cancelled"}])
        filled, _ = MEICStrategy._await_terminal_fill(
            s, "oid", fallback=5, cancel_confirmed=True)
        assert filled == 7

    def test_a_mid_poll_dip_then_recovery_keeps_the_high_water_mark(self):
        s = _strat([
            {"filled_quantity": 5},
            {"filled_quantity": 0},                       # artefact
            {"filled_quantity": 6, "status": "filled"},
        ])
        filled, settled = MEICStrategy._await_terminal_fill(
            s, "oid", fallback=5, cancel_confirmed=True)
        assert filled == 6 and settled is True


class TestItDidNotBreakTheRestOfTheContract:
    def test_a_thin_status_still_fails_closed_when_the_cancel_is_unconfirmed(self):
        """No count, no terminal state, no confirmed cancel → NOT settled."""
        s = _strat([{}, {}, {}])
        filled, settled = MEICStrategy._await_terminal_fill(
            s, "oid", fallback=3, cancel_confirmed=False)
        assert filled == 3 and settled is False

    def test_a_confirmed_dead_order_whose_count_never_moved_is_settled(self):
        s = _strat([{"filled_quantity": 2}, {"filled_quantity": 2},
                    {"filled_quantity": 2}])
        filled, settled = MEICStrategy._await_terminal_fill(
            s, "oid", fallback=2, cancel_confirmed=True)
        assert filled == 2 and settled is True

    def test_a_zero_fill_rung_is_unaffected(self):
        """Nothing filled means no remainder to mis-size."""
        s = _strat([{"filled_quantity": 0, "status": "cancelled"}])
        filled, settled = MEICStrategy._await_terminal_fill(
            s, "oid", fallback=0, cancel_confirmed=True)
        assert filled == 0 and settled is True

    def test_terminal_status_still_returns_immediately(self):
        s = _strat([{"filled_quantity": 4, "status": "filled"},
                    {"filled_quantity": 99}])     # must never be reached
        filled, settled = MEICStrategy._await_terminal_fill(
            s, "oid", fallback=4, cancel_confirmed=False)
        assert filled == 4 and settled is True


class TestADipIsNotMovement:
    """A spurious low reading must not be mistaken for an order still filling.

    `settled` is computed as `cancel_confirmed and saw_any and not moved`. If a
    downward artefact counted as movement, a confirmed-dead order would be
    declared UNSETTLED, and the caller fails closed — orphaning the order and
    aborting the leg. Safe, but it throws away entries on an IBKR reporting
    quirk that we already know is meaningless.

    This is the case the first mutation-control pass could not distinguish:
    clamping the VALUE while still counting the dip as movement passed every
    other test in this file.
    """

    def test_a_dip_without_a_terminal_status_still_settles(self):
        s = _strat([{"filled_quantity": 5}, {"filled_quantity": 0},
                    {"filled_quantity": 0}])
        filled, settled = MEICStrategy._await_terminal_fill(
            s, "oid", fallback=5, cancel_confirmed=True)
        assert filled == 5
        assert settled is True, (
            "a downward artefact was treated as movement — the leg would be "
            "aborted on a reporting quirk")

    def test_a_genuine_RISE_without_terminal_status_does_NOT_settle(self):
        """Control for the above: real movement must still fail closed."""
        s = _strat([{"filled_quantity": 5}, {"filled_quantity": 6},
                    {"filled_quantity": 7}])
        filled, settled = MEICStrategy._await_terminal_fill(
            s, "oid", fallback=5, cancel_confirmed=True)
        assert filled == 7
        assert settled is False, "an order still filling must not look settled"
