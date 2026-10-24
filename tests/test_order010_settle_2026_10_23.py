"""Five untracked contracts reached the live account via a cancel/fill race.

Live on `bl`, entry #5, 2026-10-23:

    14:17:14  Attempt 1: LIMIT @ $0.60 for Put 7600.0   (order ...759, 7 ct)
    14:18:01  Cancelling working order ...759 (2/7 filled) before next rung
    14:18:04  ORDER-010: partial 2/7 on Put 7600.0 — escalating to fill the remainder
    14:18:05  Attempt 2: LIMIT @ $0.55                   (5 contracts)
    14:18:07  Filled Put 7600.0 7/7 @ avg $0.56          (booked 2 + 5)

POS-003, an hour later:

    conid 919909284: expected 7, broker shows 12

2 + 5 + 5. The original order filled its remaining 5 AS WELL AS the escalation.
Five untracked long puts, ~$280, carrying no stop and invisible to our P&L —
which is also a concrete instance of the BROKER-RECONCILE drift.

The existing design was already right in principle: it cancels, then re-reads
the TERMINAL fill rather than trusting the pre-cancel snapshot. What it missed
is that `_cancel_order` returning True means IBKR ACCEPTED the cancel, not that
the order is dead — and a status read in that window still returns the stale
count. The identical pattern on Call 7670 forty seconds later did NOT over-fill,
so it is a race, which is why it survived.

Fix: poll until the order reports a genuinely TERMINAL status before trusting
its count, and FAIL CLOSED if it never does — treat it exactly like a failed
cancel rather than sizing a remainder from an unsettled order.
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.base_strategy import MEICStrategy  # noqa: E402


class _Stub:
    """Just enough strategy to drive _await_terminal_fill."""
    ORDER_SETTLE_TIMEOUT_S = 1.0        # keep the suite fast
    ORDER_SETTLE_POLL_S = 0.01
    _extract_filled_quantity = staticmethod(MEICStrategy._extract_filled_quantity)
    _await_terminal_fill = MEICStrategy._await_terminal_fill

    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.reads = 0

    def _get_order_status(self, order_id):
        self.reads += 1
        if self._statuses:
            return self._statuses.pop(0)
        return self._last if hasattr(self, "_last") else {}


class TestTheIncident:
    def test_the_LATE_fill_is_seen_once_the_order_settles(self):
        """The exact shape: status reads 2 while still working, then reports
        7 when it finally goes terminal. The late 5 must be picked up."""
        s = _Stub([
            {"status": "submitted", "filled_quantity": 2},
            {"status": "submitted", "filled_quantity": 2},
            {"status": "cancelled", "filled_quantity": 7},
        ])
        filled, settled = s._await_terminal_fill("759", 2)
        assert settled is True
        assert filled == 7, "the remainder that raced the cancel was missed"

    def test_CONTROL_the_old_single_read_returned_the_stale_2(self):
        """Proves the test above is not vacuous by reproducing the defect: one
        immediate read, no terminal check, yields the pre-cancel snapshot."""
        s = _Stub([{"status": "submitted", "filled_quantity": 2}])
        assert MEICStrategy._extract_filled_quantity(
            s._get_order_status("759")) == 2

    def test_the_remainder_would_be_sized_to_ZERO_after_the_fix(self):
        """7 requested, 7 confirmed -> nothing left to place, so no second
        order and no over-fill."""
        s = _Stub([{"status": "cancelled", "filled_quantity": 7}])
        filled, settled = s._await_terminal_fill("759", 2)
        assert settled and (7 - filled) == 0


class TestItFailsClosed:
    def test_an_order_that_never_settles_reports_NOT_settled(self):
        s = _Stub([{"status": "submitted", "filled_quantity": 2}] * 400)
        filled, settled = s._await_terminal_fill("759", 2)
        assert settled is False, "an unsettled order must not be trusted"
        assert filled == 2

    def test_the_caller_treats_unsettled_as_a_FAILED_CANCEL(self):
        import inspect
        src = inspect.getsource(MEICStrategy._place_option_order_ib) \
            if hasattr(MEICStrategy, "_place_option_order_ib") else ""
        if not src:
            src = open(os.path.join(ROOT, "bots/hydra/base_strategy.py")).read()
        i = src.index("ORDER-010-SETTLE: order")
        window = src[max(0, i - 600):i + 600]
        assert "cancelled = False" in window, (
            "an unsettled order must fall into the failed-cancel path, which "
            "aborts the leg instead of re-placing against a live order")

    @pytest.mark.parametrize("state", [
        "filled", "cancelled", "api_cancelled", "rejected",
        "inactive", "expired", "presubmitted_cancelled",
    ])
    def test_every_terminal_status_is_recognised(self, state):
        s = _Stub([{"status": state, "filled_quantity": 4}])
        filled, settled = s._await_terminal_fill("759", 0)
        assert settled is True and filled == 4

    def test_a_missing_quantity_falls_back_rather_than_crashing(self):
        s = _Stub([{"status": "cancelled"}])
        filled, settled = s._await_terminal_fill("759", 3)
        assert settled is True and filled == 3

    def test_an_empty_status_does_not_count_as_terminal(self):
        """A failed status read returns {} — that is "unknown", not "dead"."""
        s = _Stub([{}] * 400)
        _f, settled = s._await_terminal_fill("759", 2)
        assert settled is False

    def test_it_reads_the_camelCase_field_too(self):
        s = _Stub([{"order_status": "Cancelled", "filledQuantity": 6}])
        filled, settled = s._await_terminal_fill("759", 0)
        assert settled is True and filled == 6


class TestTheBudgetIsBounded:
    def test_it_stops_polling_at_the_timeout(self):
        s = _Stub([{"status": "submitted", "filled_quantity": 2}] * 10000)
        import time as _t
        t0 = _t.time()
        s._await_terminal_fill("759", 2)
        assert _t.time() - t0 < 3.0, "polling is unbounded"

    def test_the_real_budget_is_long_enough_for_the_observed_race(self):
        """Fills landed ~2s after the cancel on 2026-10-23; the default budget
        must comfortably exceed that."""
        assert MEICStrategy.ORDER_SETTLE_TIMEOUT_S >= 4.0
        assert MEICStrategy.ORDER_SETTLE_POLL_S <= 1.0
