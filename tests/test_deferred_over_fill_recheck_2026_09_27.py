"""The over-fill correction must re-check AFTER the broker's book settles.

THE DEFECT THIS PINS. `_correct_over_fill` read positions the instant a leg
returned. IBKR's position endpoint LAGS a fill by up to ~20s — measured
2026-09-25, when it still reported qty 7 after a confirmed 7-lot sale and only
cleared ~20s later.

During that lag the book looks like it moved LESS than we asked. That is
precisely the shape the lag-safe guard declines to act on. So the immediate
check answered "incomplete fill, not my job" in the exact scenario it was
written for, and the over-fill survived to be found ~27 minutes later by
POS-003 — which only ALERTS, it does not flatten. On 2026-09-25 the resulting
orphan sat from 12:06 until a human flattened it at 13:36, by which time the
call had decayed $4.00 -> $2.55.

So the correction shipped on 2026-09-27 was very likely a no-op in production.
The fix is to park the expectation and re-check it on a later monitoring tick.

NEGATIVE CONTROL. `test_immediate_check_alone_would_have_MISSED_the_race`
drives the lagging read and asserts NOTHING is traded on the immediate pass —
that is the defect, reproduced. The very next test lets the book settle and
asserts the correction now fires. Delete the deferral and the second test fails
while the first still passes; that pairing is what makes this suite sighted
rather than a source-grep.
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


def _rig(fill_ok=True):
    """A strategy whose position read is swappable mid-test.

    `s.book` is the net quantity the broker currently admits to. Setting it is
    how a test says "the lag has now resolved".
    """
    s = _S.__new__(_S)
    s.orders = []
    s.orphans = []
    s.alerts = []
    s.book = 0
    s.strategy_config = {}
    s._read_open_positions = lambda **kw: (
        [{"instrument_id": 111, "quantity": s.book}] if s.book else [])

    def _place(**kw):
        s.orders.append(kw)
        return {"filled": fill_ok, "order_id": "fix1"}
    s._place_leg_order = _place
    s._add_orphaned_order = lambda x: s.orphans.append(x)
    s.alert_service = MagicMock()
    s.alert_service.send_alert = lambda **kw: s.alerts.append(kw)
    return s


def _age(s, seconds=None):
    """Backdate every parked check so its settle delay has elapsed.

    `seconds=None` means "just past the window, whatever the window is". The
    call sites used to hardcode 60, which silently stopped being 'due' when the
    settle window was raised 45 -> 120 on 2026-10-02: the sweeps would no-op and
    every assertion that something WAS corrected would fail for the wrong
    reason. Deriving it from the live window keeps these tests about behaviour
    (due after the window elapses) rather than about a constant.
    """
    import time
    if seconds is None:
        seconds = s._deferred_fill_settle_s() + 15
    for rec in s._pending_fill_checks().values():
        rec["at"] = time.monotonic() - seconds


class TestTheLagDefeatedTheImmediateCheck:
    def test_immediate_check_alone_would_have_MISSED_the_race(self):
        """THE DEFECT. Short 7, BUY 7 to close; 14 actually bought.

        But the position endpoint has not caught up — it still reports the
        pre-close -7. The immediate check sees "nothing moved", which is
        indistinguishable from an unfilled order, so it must not trade.
        """
        s = _rig()
        s.book = -7                      # lagging: still the pre-close state
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        assert s.orders == [], (
            "the immediate pass must NOT trade on a lagging read")

    def test_but_it_is_parked_not_dropped(self):
        s = _rig()
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        assert "111" in s._pending_fill_checks(), (
            "a lag-shaped decline must park the expectation for re-check")

    def test_the_sweep_corrects_it_once_the_book_settles(self):
        """THE FIX. Same race, re-checked on settled data -> corrected."""
        s = _rig()
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        assert s.orders == []

        s.book = +7                      # the lag resolves: 14 were bought
        _age(s)
        s._sweep_deferred_fill_checks()

        assert len(s.orders) == 1, "settled re-check did not correct the over-fill"
        assert s.orders[0]["side"] == "SELL"
        assert s.orders[0]["quantity"] == 7
        assert s.orders[0]["is_exit"] is True


class TestTheSweepIsDisciplined:
    def test_it_waits_for_the_settle_delay(self):
        """A sweep that fires immediately is the bug again, just relocated."""
        s = _rig()
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        s.book = +7
        s._sweep_deferred_fill_checks()          # no ageing: not due yet
        assert s.orders == [], "swept before the book could settle"
        assert "111" in s._pending_fill_checks(), "must stay parked until due"

    def test_a_settled_recheck_does_not_re_park(self):
        """Otherwise a conid re-parks itself every tick, forever."""
        s = _rig()
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        _age(s)
        s._sweep_deferred_fill_checks()          # still lagging -> declines
        assert s._pending_fill_checks() == {}, (
            "the deferred re-check must be final, not self-renewing")

    def test_a_stale_park_is_dropped_not_traded(self):
        """Past the max age the baseline is untrustworthy: drop, don't trade."""
        s = _rig()
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        s.book = +7
        _age(s, s._DEFERRED_FILL_MAX_AGE_S + 60)
        s._sweep_deferred_fill_checks()
        assert s.orders == [], "acted on a baseline too old to trust"
        assert s._pending_fill_checks() == {}

    def test_an_idle_sweep_never_touches_the_broker(self):
        """It runs on every tick, so idle cost must be zero."""
        s = _rig()
        s._read_open_positions = MagicMock(side_effect=AssertionError(
            "idle sweep read positions"))
        s._sweep_deferred_fill_checks()

    def test_the_sweep_never_raises(self):
        """It runs inside the trading loop; it must not be able to break it."""
        s = _rig()
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        _age(s)
        s._correct_over_fill = MagicMock(side_effect=RuntimeError("boom"))
        s._sweep_deferred_fill_checks()          # must not propagate
        assert s._pending_fill_checks() == {}, (
            "a throwing correction must not leave a poison entry")


class TestSupersedingAndSafety:
    def test_a_later_leg_at_the_same_conid_supersedes_the_park(self):
        """IBKR merges at (conid, side) — a sibling leg can retrade a strike.

        The newest call has the freshest baseline, so only it may survive.
        """
        s = _rig()
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="first")
        assert s._pending_fill_checks()["111"]["leg_name"] == "first"

        # A second, also-lag-shaped call at the same conid (the book still
        # reports -7, i.e. "nothing moved"): it declines, and its newer
        # baseline must replace the parked one.
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=4,
                             leg_name="second")
        pending = s._pending_fill_checks()
        assert s.orders == [], "neither call should have traded"
        assert len(pending) == 1, "two parked checks for one conid"
        assert pending["111"]["leg_name"] == "second"
        assert pending["111"]["intended_qty"] == 4

    def test_a_failed_read_parks_rather_than_retiring_the_check(self):
        """A broker blip is not evidence. Try again, don't give up."""
        s = _rig()
        s._read_open_positions = MagicMock(side_effect=RuntimeError("503"))
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        assert s.orders == []
        assert "111" in s._pending_fill_checks()

    def test_reduce_only_still_holds_on_the_deferred_path(self):
        """An UNDER-fill on settled data is the rung loop's job, not ours.

        The deferred path reuses the same arithmetic, so it must inherit the
        invariant: a correction may only ever REDUCE net exposure.
        """
        s = _rig()
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        s.book = -3                      # settled: only 4 of 7 bought back
        _age(s)
        s._sweep_deferred_fill_checks()
        assert s.orders == [], "traded on an under-fill"

    def test_a_settled_flat_read_must_not_RESTORE_a_short(self):
        """The case the reduce-only invariant exists for.

        A leg reports nothing filled, so we expect the conid to still hold the
        -7 it held before. The settled book says flat. Naively "correcting" to
        the expectation means SELLING 7 — opening a naked short on the strength
        of a read that told us nothing. A safety net must never be able to
        OPEN a position.

        This is the control that catches removal of the `abs(expected) >=
        abs(qty_after)` half; the two tests below it are caught by the earlier
        guards and never reach it.
        """
        s = _rig()
        # Short 7, buying back 4 of them. Immediate read still says -7
        # ("nothing moved") -> lag-shaped, so it parks expecting -3.
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=4,
                             leg_name="short_call")
        assert s._pending_fill_checks(), "precondition: must have parked"

        # Settled book reads FLAT — the close did more than we tracked (a
        # merge at the conid, say). Correcting toward the -3 expectation means
        # SELLING 3 from flat: opening a short to satisfy bookkeeping.
        s.book = 0
        _age(s)
        s._sweep_deferred_fill_checks()
        assert s.orders == [], (
            "a safety correction SOLD from a flat book — it opened a short")

    def test_a_settled_empty_read_never_opens_a_position(self):
        """The nastiest failure mode: acting on "no positions" by buying."""
        s = _rig()
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        s.book = 0                       # flat: the close simply worked
        _age(s)
        s._sweep_deferred_fill_checks()
        assert s.orders == [], "a flat book must never trigger a trade"


class TestUncorrectedIsEscalated:
    def test_a_failed_deferred_correction_alerts_and_orphans(self):
        s = _rig(fill_ok=False)
        s.book = -7
        s._correct_over_fill(111, "BUY", qty_before=-7, intended_qty=7,
                             leg_name="short_call")
        s.book = +7
        _age(s)
        s._sweep_deferred_fill_checks()
        assert len(s.orders) == 1, "no correction attempted"
        assert s.orphans, "an uncorrected over-fill must be orphan-flagged"
        assert s.alerts and s.alerts[0]["priority"].value == "critical"


class TestTheTickActuallyCallsIt:
    """Without this, every test above passes while the sweep never runs.

    The suite drives `_sweep_deferred_fill_checks` directly, so deleting the
    one line that hooks it into the monitoring loop would leave the whole file
    green and the fix dead in production. This is the test that notices.
    """

    class _Stop(Exception):
        pass

    def _tick_rig(self):
        s = _rig()
        s._critical_intervention_required = False
        s._circuit_breaker_open = False
        s._has_orphaned_orders = lambda: False
        s.swept = []
        s._sweep_deferred_fill_checks = lambda: s.swept.append(1)
        # Stop the tick immediately after the sweep point so the rest of the
        # loop needs no scaffolding.
        def _boom():
            raise TestTheTickActuallyCallsIt._Stop()
        s._update_market_data = _boom
        return s

    def test_the_monitoring_tick_sweeps(self):
        s = self._tick_rig()
        try:
            s._run_strategy_check_internal()
        except TestTheTickActuallyCallsIt._Stop:
            pass
        assert s.swept, (
            "_run_strategy_check_internal did not call the deferred sweep")

    def test_it_sweeps_BEFORE_the_market_data_read(self):
        """So a stale-SPX or data-freshness bail cannot skip it.

        `_update_market_data` raising stands in for every early return below
        it: the sweep must already have happened.
        """
        s = self._tick_rig()
        try:
            s._run_strategy_check_internal()
        except TestTheTickActuallyCallsIt._Stop:
            pass
        assert s.swept, "the sweep sits behind the market-data read"

    def test_no_variant_overrides_the_tick_past_the_hook(self):
        """All 8 variants must inherit it — the B4 coverage bug, avoided.

        B4 (the placement budget) was written into `_initiate_entry`, which
        D/E/F/G/H each fully override, so it reached only three variants. Pin
        that this hook does not have the same hole.
        """
        import importlib
        from bots.hydra.base_strategy import MEICStrategy
        base = MEICStrategy._run_strategy_check_internal
        mods = [
            "bots.hydra.strategy",
            "bots.hydra.brandon.strategy",
            "bots.hydra.calendar_strategy_base",
            "bots.hydra.double_calendar_strategy",
            "bots.hydra.spy_double_calendar_strategy",
            "bots.hydra.ghauri_strategy",
            "bots.hydra.strangle_strategy",
        ]
        offenders = []
        for name in mods:
            mod = importlib.import_module(name)
            for attr in vars(mod).values():
                if (isinstance(attr, type) and issubclass(attr, MEICStrategy)
                        and attr is not MEICStrategy
                        and "_run_strategy_check_internal" in vars(attr)):
                    offenders.append(f"{name}.{attr.__name__}")
        assert not offenders, (
            "these classes override the tick and so bypass the deferred "
            f"over-fill sweep: {offenders}")
        assert base is MEICStrategy._run_strategy_check_internal
