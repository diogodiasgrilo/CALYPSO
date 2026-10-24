"""The stop monitor was blind for as long as an entry took to leg in.

`run_strategy_check()` does entries AND stop monitoring on one thread, and the
per-rung wait happens server-side in calypso-broker inside a single blocking
HTTP call, so the strategy sits in a socket read for ~45s per rung.

Measured on the live seat 2026-10-23: five blind spells of 191/173/271/260/318
seconds — 20.2 minutes in one session, with up to 4 open entries.

Why that is worse than it first looks. The WARNING TIME before a stop fires is
SHORTER than the blind window. Over 68 stops on bl+b since 2026-09-01 (seconds
between a side's cost-to-close last being under 70% of its trigger level, and
the stop actually firing):

    median warning   bl 54s    b 159s
    whole warning window fits inside 318s:  53/68  (78%)
    whole warning window fits inside  50s:  16/68  (24%)

So a blind spell coinciding with a developing breach does not DELAY the stop,
it MISSES it: the position goes from safe to past-stop with no check between.

Fix: run a stop check at the top of every rung. One insertion point covers both
boundaries — attempt 0 is the start of a leg, later attempts are rung
boundaries — so the maximum blind interval becomes one rung wait instead of a
whole entry. 78% -> 24%.

NOT a thread. The entry under construction is not yet in
`daily_state.active_entries`, so the checkpoint only ever evaluates
ALREADY-OPEN entries: independent positions, independent orders, and nothing
can race the placement because the strategy stays single-threaded.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.base_strategy import MEICStrategy  # noqa: E402

SRC = open(os.path.join(ROOT, "bots/hydra/base_strategy.py")).read()


class _Stub:
    ENTRY_CHECKPOINT_MIN_GAP_S = MEICStrategy.ENTRY_CHECKPOINT_MIN_GAP_S
    _entry_checkpoint = MEICStrategy._entry_checkpoint

    def __init__(self, open_entries=1, raises=False, action=None):
        self.daily_state = SimpleNamespace(
            active_entries=[object()] * open_entries)
        self.state = "ENTRY_IN_PROGRESS"
        self.calls = 0
        self._raises = raises
        self._action = action

    def _check_stop_losses(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("broker exploded")
        if self._action:
            self.state = "STOP_TRIGGERED"   # the stop path transitions state
        return self._action


class TestItRunsWhereItMatters:
    def test_a_checkpoint_runs_when_positions_are_open(self):
        s = _Stub(open_entries=2)
        s._entry_checkpoint("leg rung 1")
        assert s.calls == 1

    def test_it_is_called_at_the_top_of_EVERY_rung(self):
        """One insertion point covering leg starts (attempt 0) and rung
        boundaries (attempt > 0)."""
        i = SRC.index("self._entry_checkpoint(f\"{leg_description} rung")
        window = SRC[max(0, i - 900):i]
        assert "for attempt, (slippage_percent, is_market) in enumerate(" in window
        assert "if attempt > 0" not in SRC[i - 200:i], (
            "checkpoint is gated to later rungs — leg starts would be skipped")

    def test_nothing_open_means_no_broker_work(self):
        s = _Stub(open_entries=0)
        s._entry_checkpoint("leg rung 1")
        assert s.calls == 0


class TestItCannotHarmThePlacement:
    def test_a_raising_stop_check_does_not_propagate(self):
        """The entry is holding real money; a checkpoint must never break it."""
        s = _Stub(raises=True)
        s._entry_checkpoint("leg rung 2")     # must not raise
        assert s.calls == 1

    def test_the_entry_state_is_restored(self):
        """The stop path transitions state; the entry IS still in progress, so
        leaving STOP_TRIGGERED behind would strand the state machine."""
        s = _Stub(action="Stopped call side")
        s._entry_checkpoint("leg rung 2")
        assert s.state == "ENTRY_IN_PROGRESS"

    def test_state_is_restored_even_when_the_check_raises(self):
        s = _Stub(raises=True)
        s.state = "ENTRY_IN_PROGRESS"
        s._entry_checkpoint("leg rung 2")
        assert s.state == "ENTRY_IN_PROGRESS"

    def test_it_never_re_enters_itself(self):
        s = _Stub()
        s._in_entry_checkpoint = True
        s._entry_checkpoint("leg rung 1")
        assert s.calls == 0

    def test_the_reentry_flag_is_cleared_after_a_raise(self):
        s = _Stub(raises=True)
        s._entry_checkpoint("leg rung 1")
        assert s._in_entry_checkpoint is False, "a raise left the gate latched"


class TestItIsThrottled:
    def test_a_second_checkpoint_inside_the_gap_is_skipped(self):
        """The IBKR rate gate was measured ~83% saturated; 4 legs x 5 rungs of
        unthrottled stop checks would add real load."""
        s = _Stub()
        s._entry_checkpoint("rung 1")
        s._entry_checkpoint("rung 2")
        assert s.calls == 1

    def test_it_runs_again_once_the_gap_has_passed(self):
        s = _Stub()
        s._entry_checkpoint("rung 1")
        s._last_entry_checkpoint_at -= (s.ENTRY_CHECKPOINT_MIN_GAP_S + 1)
        s._entry_checkpoint("rung 2")
        assert s.calls == 2

    def test_the_throttle_is_well_under_a_rung_wait(self):
        """Rungs wait ~45s. A throttle near that would nullify the fix; one
        well under it keeps the worst-case blind interval ~one rung."""
        assert MEICStrategy.ENTRY_CHECKPOINT_MIN_GAP_S <= 20.0


class TestTheMeasurementThatJustifiesIt:
    def test_the_warning_time_data_is_recorded_in_the_source(self):
        """So nobody removes this as 'defensive overhead' without seeing that
        78% of stops had their whole warning window inside one blind spell."""
        i = SRC.index("ENTRY_CHECKPOINT_MIN_GAP_S")
        block = SRC[max(0, i - 2200):i]
        assert "78%" in block and "warning" in block.lower()
        assert "not yet in daily_state.active_entries" in block.lower() or \
               "NOT yet in daily_state.active_entries" in block

class TestItCannotCrashAStrategyThatLacksState:
    """The checkpoint's contract is that it can never harm the entry it
    interleaves. The first version reached through `self.daily_state`
    unguarded and raised AttributeError on any object that lacks it — eight
    pre-existing tests caught it, and in production a half-constructed
    strategy would have taken down a live placement."""

    def test_no_daily_state_at_all(self):
        class Bare:
            ENTRY_CHECKPOINT_MIN_GAP_S = 15.0
            _entry_checkpoint = MEICStrategy._entry_checkpoint
            state = "X"
        Bare()._entry_checkpoint("leg rung 1")      # must not raise

    def test_daily_state_present_but_empty(self):
        s = _Stub(open_entries=0)
        s._entry_checkpoint("leg rung 1")
        assert s.calls == 0

    def test_it_reaches_through_getattr_on_SELF(self):
        i = SRC.index("def _entry_checkpoint")
        body = SRC[i:i + 1400]
        assert 'getattr(self, "daily_state", None)' in body, (
            "daily_state is reached directly — a strategy without it raises")
