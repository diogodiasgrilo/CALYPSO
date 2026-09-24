"""Bound the window in which the monitoring loop is blind.

`_handle_monitoring` runs ONE stop check and then calls `_initiate_entry`,
which blocks the single-threaded loop until every leg places or is abandoned.
No stop check runs in between. Measured on B over 42 placements:

    median 65s | p90 405s (6.8 min) | max 558s (9.3 min)

On 2026-09-24 Entry #6 spent 3.7 minutes on a SINGLE leg across five rungs that
filled nothing (GUARD-FLOOR correctly refusing to cross a price that would have
inverted the vertical). The entry failed anyway and was unwound, so the whole
8.2-minute blind window bought nothing — and it produced that day's stranded
contracts and its +$5,995 unattributed booking.

WHY A DEADLINE RATHER THAN INTERLEAVING STOP CHECKS INTO PLACEMENT: a stop
close fired mid-placement could land on a conid another leg is actively
working, and **74% of B's trading days have two entries sharing a strike** (84
occurrences over 31 days; on 09-24 E#5 and E#6 both used conid 920688820).
Those net at the broker. A deadline bounds the same exposure with zero
re-entrancy.

CONTROLS worth naming:
* ``test_first_rung_of_a_leg_always_runs`` — an over-eager guard that also
  blocked the first attempt would leave a leg unattempted while its siblings
  are filled, i.e. manufacture the naked-leg state the unwind exists to avoid.
* ``test_stale_deadline_cannot_abort_the_NEXT_entry`` — the deadline is gated
  on ``_entry_in_progress``; without that, one slow entry poisons the next.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.base_strategy import MEICStrategy  # noqa: E402


class _S(MEICStrategy):
    def _calculate_strikes(self, *a, **k): raise NotImplementedError
    def _check_stop_losses(self, *a, **k): raise NotImplementedError
    def _initiate_entry(self, *a, **k): raise NotImplementedError


def _s(cfg=None):
    s = _S.__new__(_S)
    s.strategy_config = cfg if cfg is not None else {}
    s._entry_in_progress = False
    s._placement_deadline = None
    return s


class TestBudgetResolution:
    def test_default_is_150s(self):
        assert _s()._entry_placement_budget_s() == 150.0

    def test_configurable(self):
        assert _s({"entry_placement_budget_s": 90})._entry_placement_budget_s() == 90.0

    def test_garbage_falls_back(self):
        assert _s({"entry_placement_budget_s": "soon"})._entry_placement_budget_s() == 150.0


class TestDeadlineSemantics:
    def test_zero_disables_it(self, monkeypatch):
        s = _s({"entry_placement_budget_s": 0})
        s._begin_placement_window()
        s._entry_in_progress = True
        assert s._placement_deadline is None
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: 1e9)
        assert s._placement_deadline_passed() is False

    def test_not_passed_inside_the_budget(self, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: clock["t"])
        s = _s({"entry_placement_budget_s": 150})
        s._begin_placement_window()
        s._entry_in_progress = True
        clock["t"] += 149
        assert s._placement_deadline_passed() is False

    def test_passed_beyond_the_budget(self, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: clock["t"])
        s = _s({"entry_placement_budget_s": 150})
        s._begin_placement_window()
        s._entry_in_progress = True
        clock["t"] += 151
        assert s._placement_deadline_passed() is True

    def test_stale_deadline_cannot_abort_the_NEXT_entry(self, monkeypatch):
        """CONTROL for the _entry_in_progress gate. Without it, a deadline left
        over from a finished entry is always in the past, so the next entry
        would abort at its second rung."""
        clock = {"t": 1000.0}
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: clock["t"])
        s = _s({"entry_placement_budget_s": 150})
        s._begin_placement_window()
        clock["t"] += 10_000               # that entry finished long ago
        s._entry_in_progress = False       # ...and is over
        assert s._placement_deadline_passed() is False

    def test_restarting_the_window_resets_it(self, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: clock["t"])
        s = _s({"entry_placement_budget_s": 150})
        s._begin_placement_window()
        s._entry_in_progress = True
        clock["t"] += 200
        assert s._placement_deadline_passed() is True
        s._begin_placement_window()        # next entry
        assert s._placement_deadline_passed() is False


class TestRungLoopHonoursIt:
    """Exercises the REAL rung loop in _place_option_order_ib."""

    def _rig(self, monkeypatch, budget=150, elapse_after_first=999):
        from bots.hydra import base_strategy as B
        clock = {"t": 1000.0}
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: clock["t"])
        monkeypatch.setattr("bots.hydra.base_strategy.time.sleep", lambda *_a: None)
        s = _s({"entry_placement_budget_s": budget})
        s.dry_run = False
        s._entry_in_progress = True
        s._begin_placement_window()
        s.attempts = []

        s._read_option_quote = lambda conid: {"bid": 1.00, "ask": 1.05}
        s.contracts_per_entry = 7
        s.max_contracts_per_order = 15
        s.max_contracts_per_underlying = 600
        s.commission_per_leg = 1.15
        s._max_absolute_slippage = 0.50
        from bots.hydra.base_strategy import MEICDailyState
        s.daily_state = MEICDailyState(date="2026-09-24")
        s._read_option_chain = lambda expiry, strikes: ({}, {7630.0: 111})
        s._sell_credit_floor_price = lambda *a, **k: None
        s._monitor_fill_slippage = lambda **k: None
        s._flatten_accumulated_partial = lambda *a, **k: None
        s._cancel_order = lambda *a, **k: True
        s._get_order_status = lambda *a, **k: {}
        s._extract_filled_quantity = staticmethod(lambda *a, **k: 0)
        s._add_orphaned_order = lambda *a, **k: None

        def _place(**kw):
            s.attempts.append(kw)
            # Burn the budget once the first attempt has actually been made.
            # The deadline is checked at the TOP of each rung, before the quote
            # fetch, so the clock must move here rather than in the quote stub.
            clock["t"] += elapse_after_first
            return {"filled": False, "order_id": None, "filled_quantity": 0}

        s._place_leg_order = _place
        return s

    def test_budget_stops_the_escalation(self, monkeypatch):
        from bots.hydra.base_strategy import BuySell
        s = self._rig(monkeypatch)
        s._place_option_order_ib(
            7630.0, "Put", BuySell.BUY, "2026-09-24", "ref",
        )
        assert len(s.attempts) == 1, f"escalated past the budget: {len(s.attempts)} rungs"

    def test_first_rung_of_a_leg_always_runs(self, monkeypatch):
        """CONTROL. Even with the budget ALREADY spent before the leg starts,
        attempt 1 must still be made — refusing it would leave a leg
        unattempted while its siblings are filled."""
        from bots.hydra.base_strategy import BuySell
        s = self._rig(monkeypatch, budget=1)
        s._placement_deadline = 0.0   # already long past
        s._place_option_order_ib(
            7630.0, "Put", BuySell.BUY, "2026-09-24", "ref",
        )
        assert len(s.attempts) == 1

    def test_without_the_budget_it_still_walks_every_rung(self, monkeypatch):
        """CONTROL. Proves the test can tell the two states apart — with the
        budget disabled the loop exhausts all five rungs as it always has."""
        from bots.hydra.base_strategy import BuySell, PROGRESSIVE_RETRY_SEQUENCE
        s = self._rig(monkeypatch, budget=0)
        s._place_option_order_ib(
            7630.0, "Put", BuySell.BUY, "2026-09-24", "ref",
        )
        assert len(s.attempts) == len(PROGRESSIVE_RETRY_SEQUENCE)
