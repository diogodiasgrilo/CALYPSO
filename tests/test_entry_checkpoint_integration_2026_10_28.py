"""Does the ENTRY-CHECKPOINT actually fire inside the REAL rung loop?

`tests/test_entry_checkpoint_2026_10_25.py` covers `_entry_checkpoint` itself
well — throttling, re-entry, state restoration, not harming the placement. But
the claim that matters is an INTEGRATION one: that a checkpoint runs at the top
of every rung of `_place_option_order_ib`, so the monitor's blind interval is
one rung (~53s measured) rather than a whole entry (~318s measured).

That claim was pinned only by a SOURCE GREP:

    assert "for attempt, (slippage_percent, is_market) in enumerate(" in window

which passes against a gutted method — the exact failure this project has
recorded before (21 such tests passing against a hollowed-out method,
2026-09-23). Production could not settle it either: the coincidence needed is
a long placement WITH an open position, and `bl` placed nothing at all on
2026-10-27, so waiting is not a plan.

So drive the real 590-line method. `_read_option_quote` returning None makes
each rung `continue` immediately after its checkpoint, which walks the whole
PROGRESSIVE_RETRY_SEQUENCE without needing the order path at all.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.base_strategy import (  # noqa: E402
    PROGRESSIVE_RETRY_SEQUENCE, BuySell, MEICStrategy,
)

CONID = 987654321
STRIKE = 7750.0


class _DailyState:
    def __init__(self, open_entries):
        self.active_entries = open_entries


def _strategy(open_entries, quote=None):
    """A real MEICStrategy with only what `_place_option_order_ib` touches."""

    class _Concrete(MEICStrategy):
        def _calculate_strikes(self, *a, **k): ...
        def _check_stop_losses(self, *a, **k):
            self.stop_checks += 1
            return None
        def _initiate_entry(self, *a, **k): ...

    s = _Concrete.__new__(_Concrete)
    s.stop_checks = 0
    s.state = "ENTRY_IN_PROGRESS"
    s.dry_run = False
    s.contracts_per_entry = 7
    s.min_net_credit_per_contract = 0.0
    s.daily_state = _DailyState(open_entries)
    s.ENTRY_CHECKPOINT_MIN_GAP_S = 0.0          # throttle is covered elsewhere
    s._checkpoint_time_in_leg = 0.0
    s._read_option_chain = lambda expiry, strikes: ({STRIKE: CONID}, {STRIKE: CONID})
    s._validate_order_size = lambda qty, desc: (True, None)
    s._read_open_positions = lambda **k: []
    s._net_qty_at_conid = lambda **k: 0
    s._read_option_quote = lambda conid: quote      # None → every rung continues
    s._leg_budget_spent = lambda *a, **k: 0.0
    s._entry_leg_budget_s = lambda *a, **k: 10 ** 9
    s._max_absolute_slippage = 10.0
    return s


def _place(s):
    return MEICStrategy._place_option_order_ib(
        s, strike=STRIKE, put_call="call", buy_sell=BuySell.SELL,
        expiry="20261028", external_ref="TEST")


class TestTheCheckpointRunsOnEveryRung:
    def test_one_stop_check_per_rung_with_a_position_open(self):
        s = _strategy(open_entries=[{"entry": 1}])
        _place(s)
        assert s.stop_checks == len(PROGRESSIVE_RETRY_SEQUENCE), (
            f"expected one checkpoint per rung "
            f"({len(PROGRESSIVE_RETRY_SEQUENCE)}), got {s.stop_checks}")

    def test_the_counter_the_starvation_log_reads_agrees(self):
        """MONITOR-STARVED derives its coverage clause from this counter."""
        s = _strategy(open_entries=[{"entry": 1}])
        _place(s)
        assert getattr(s, "entry_checkpoints_run", 0) == s.stop_checks

    def test_nothing_open_means_no_broker_work_in_the_loop(self):
        """Control: the count must come from the OPEN POSITION, not the loop.

        Without this, `stop_checks == 5` could be satisfied by a checkpoint
        that ignores its own guard and runs unconditionally.
        """
        s = _strategy(open_entries=[])
        _place(s)
        assert s.stop_checks == 0

    def test_the_blind_interval_is_a_rung_not_the_whole_leg(self):
        """The actual safety claim, stated as a ratio.

        Measured 2026-10-22/23: starvation spells of 162-318s against a median
        stop warning time of 54s. With a checkpoint per rung the longest
        unobserved interval is one rung wait (p90 49s, max 53s measured).
        """
        s = _strategy(open_entries=[{"entry": 1}])
        _place(s)
        rungs = len(PROGRESSIVE_RETRY_SEQUENCE)
        assert s.stop_checks >= rungs - 1, (
            "a leg must not be able to run several rungs unobserved")
