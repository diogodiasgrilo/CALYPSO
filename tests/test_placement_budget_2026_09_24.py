"""Bound the blind window PER LEG — the scope the data actually supports.

`_handle_monitoring` runs ONE stop check and then calls `_initiate_entry`, which
blocks the single-threaded loop until every leg places or is abandoned. No stop
check runs in between. On B over 42 placements: median 65s, p90 405s, max 558s.

THE FIRST VERSION OF THIS GOT THE SCOPE WRONG, and measuring caught it.
Across all retained logs — 60 placements with a known outcome, 59 leg placements:

    per-ENTRY 150s -> aborts 7 placements that SUCCEEDED (64% of the 11 that ran
                      long): ~12% of entries turned into needless unwinds
    per-LEG   150s -> aborts ZERO legs that would have filled, catches BOTH that
                      were doomed

because the populations separate cleanly:

    legs that eventually FILLED : median 16s, p99 148s, MAX 148s
    legs that FAILED all rungs  : median 222s, MAX 223s

A slow ENTRY is usually several legs each filling normally. The pathology is a
SINGLE leg grinding rungs that fill nothing — E#6 on 2026-09-24: Put 7630, five
rungs, 223s, zero filled, because GUARD-FLOOR correctly refused a price that
would have inverted the vertical. The entry failed anyway, so its 8.2-minute
blind window bought nothing and produced that day's stranded contracts and its
+$5,995 unattributed booking.

Deliberately NOT capping the whole entry: four legs at budget is a worse
theoretical bound, but a per-entry cap has a CERTAIN cost (aborting good
entries) against a benefit never once observed to pay — no stop has ever been
found late because of a blind window.

WHY NOT INTERLEAVE STOP CHECKS INTO PLACEMENT: a stop close fired mid-placement
could land on a conid another leg is actively working, and 74% of B's trading
days have two entries sharing a strike (84 occurrences over 31 days). Those net
at the broker.
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
    return s


class TestBudgetResolution:
    def test_default_is_150s(self):
        assert _s()._entry_leg_budget_s() == 150.0

    def test_configurable(self):
        assert _s({"entry_leg_budget_s": 90})._entry_leg_budget_s() == 90.0

    def test_pre_rescope_alias_still_honoured(self):
        """Anyone who already set the old per-entry key keeps their value."""
        assert _s({"entry_placement_budget_s": 90})._entry_leg_budget_s() == 90.0

    def test_garbage_falls_back(self):
        assert _s({"entry_leg_budget_s": "soon"})._entry_leg_budget_s() == 150.0


class TestLegClock:
    def test_zero_disables_it(self, monkeypatch):
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: 1e9)
        assert _s({"entry_leg_budget_s": 0})._leg_budget_spent(0.0) is False

    def test_not_spent_inside_the_budget(self, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: clock["t"])
        s = _s({"entry_leg_budget_s": 150})
        started = clock["t"]
        clock["t"] += 149
        assert s._leg_budget_spent(started) is False

    def test_spent_beyond_the_budget(self, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: clock["t"])
        s = _s({"entry_leg_budget_s": 150})
        started = clock["t"]
        clock["t"] += 151
        assert s._leg_budget_spent(started) is True

    def test_each_leg_gets_a_FRESH_budget(self, monkeypatch):
        """The scope fix in one assertion: a long leg must not penalise the next.

        Under the old per-ENTRY deadline this was exactly backwards — one slow
        leg spent the whole entry's budget and every later leg aborted at its
        second rung.
        """
        clock = {"t": 1000.0}
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: clock["t"])
        s = _s({"entry_leg_budget_s": 150})
        leg1 = clock["t"]
        clock["t"] += 200                      # leg 1 blew its budget
        assert s._leg_budget_spent(leg1) is True
        leg2 = clock["t"]                      # leg 2 starts its own clock
        assert s._leg_budget_spent(leg2) is False


class TestRungLoopHonoursIt:
    """Exercises the REAL rung loop in _place_option_order_ib."""

    def _rig(self, monkeypatch, budget=150, elapse_after_first=999,
             elapse_before_first=0):
        from bots.hydra import base_strategy as B
        clock = {"t": 1000.0}
        monkeypatch.setattr("bots.hydra.base_strategy.time.monotonic", lambda: clock["t"])
        monkeypatch.setattr("bots.hydra.base_strategy.time.sleep", lambda *_a: None)
        s = _s({"entry_leg_budget_s": budget})
        s.dry_run = False
        s.attempts = []

        def _quote(conid):
            # burn time BEFORE the first order, so a leg can start already over
            if not s.attempts and elapse_before_first:
                clock["t"] += elapse_before_first
            return {"bid": 1.00, "ask": 1.05}
        s._read_option_quote = _quote
        s._clock = clock
        s._burn = elapse_after_first
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
            # `s._burn` is settable per leg so a test can make ONE leg slow.
            clock["t"] += s._burn
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
        """The first rung of a leg always runs, even on a slow-quoting leg.

        HONEST SCOPE: this is NOT a control for the ``attempt > 0`` guard.
        Under the per-LEG clock that guard is structurally unreachable — the
        clock starts microseconds before the first check, so the budget cannot
        already be spent at rung 0, and removing the guard leaves every test
        green (verified). The guard is kept as defence in depth against a future
        refactor that starts the clock earlier; this test pins the PROPERTY
        (a leg is never left unattempted while its siblings are filled), which
        is what actually matters, rather than the mechanism."""
        from bots.hydra.base_strategy import BuySell
        s = self._rig(monkeypatch, budget=10, elapse_before_first=500,
                      elapse_after_first=500)
        s._place_option_order_ib(
            7630.0, "Put", BuySell.BUY, "2026-09-24", "ref",
        )
        assert len(s.attempts) == 1, "the first rung of the leg was refused"

    def test_a_SECOND_leg_gets_its_own_budget(self, monkeypatch):
        """CONTROL for the scope fix, driven through the real rung loop.

        Leg 1 blows its budget. Leg 2 must still get all five rungs. Under the
        old per-ENTRY deadline it got two — which is exactly the defect that
        made the per-entry design abort 7 successful placements out of 60.
        """
        from bots.hydra.base_strategy import BuySell, PROGRESSIVE_RETRY_SEQUENCE
        s = self._rig(monkeypatch, budget=150, elapse_after_first=999)
        s._place_option_order_ib(7630.0, "Put", BuySell.BUY, "2026-09-24", "ref")
        first_leg_attempts = len(s.attempts)
        assert first_leg_attempts == 1, "leg 1 should have been cut short"
        s.attempts.clear()
        s._burn = 0          # leg 2 is a NORMAL, fast leg
        s._place_option_order_ib(7630.0, "Put", BuySell.BUY, "2026-09-24", "ref2")
        assert len(s.attempts) == len(PROGRESSIVE_RETRY_SEQUENCE), (
            f"leg 2 only got {len(s.attempts)} rungs — the clock is shared "
            f"across legs, which is the per-entry bug this rescope removed")

    def test_without_the_budget_it_still_walks_every_rung(self, monkeypatch):
        """CONTROL. Proves the test can tell the two states apart — with the
        budget disabled the loop exhausts all five rungs as it always has."""
        from bots.hydra.base_strategy import BuySell, PROGRESSIVE_RETRY_SEQUENCE
        s = self._rig(monkeypatch, budget=0)
        s._place_option_order_ib(
            7630.0, "Put", BuySell.BUY, "2026-09-24", "ref",
        )
        assert len(s.attempts) == len(PROGRESSIVE_RETRY_SEQUENCE)


class TestItReachesEveryVariantAutomatically:
    """The scope fix removed the need for per-variant wiring entirely.

    D, E, F, G and H each fully override ``_initiate_entry`` and never call
    ``super()``. Under the per-ENTRY design they inherited nothing, and the
    follow-up had to add a ``_begin_placement_window()`` call to all five — five
    chances for a sixth variant to be forgotten. Per-LEG, the clock starts
    inside ``_place_option_order_ib`` itself, which every one of them reaches
    via ``_place_option_order``. There is no cross-entry state to wire up and
    nothing to forget.

    This asserts that directly: a strategy with NO entry-lifecycle attributes at
    all still honours the budget.
    """

    def test_budget_applies_with_no_entry_lifecycle_state(self, monkeypatch):
        from bots.hydra.base_strategy import BuySell
        s = TestRungLoopHonoursIt()._rig(monkeypatch)
        # the per-entry design depended on these; the per-leg one must not
        assert not hasattr(s, "_entry_in_progress")
        assert not hasattr(s, "_placement_deadline")
        s._place_option_order_ib(7630.0, "Put", BuySell.BUY, "2026-09-24", "ref")
        assert len(s.attempts) == 1, "budget ignored without entry-lifecycle state"
