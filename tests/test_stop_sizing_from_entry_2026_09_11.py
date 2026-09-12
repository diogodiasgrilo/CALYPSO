"""
Stop sizing must come from the ENTRY's contract count, not from config
(2026-09-11).

HONEST SCOPE: this is DEFENSIVE, not a live bug fix, and the distinction is
worth stating so nobody later "discovers" it was already safe and rips the guard
out. Today `_calculate_stop_levels_hydra` is called exactly once, at entry time,
immediately after `entry.contracts = self.contracts_per_entry` — so the two are
identical at that moment, and restored entries read their stops back from the
state file rather than recalculating.

It stops being equivalent the moment either becomes true:

  * anything recalculates stops LATER — after a mid-day config change, or on a
    restore carrying a different contract count than the running config; or
  * the combo/BAG path lands. IBKR can partially fill a whole 4-leg structure,
    and `entry.contracts` becomes the only truth. Note the leg ladder CANNOT
    produce a surviving partial today: ORDER-010 flattens an incompletable leg
    rather than keeping it, so a leg is all-or-nothing by construction. A BAG
    order has no equivalent guard, which is exactly why the audit's "6-of-10
    fill stops at 67% of max loss" scenario is a COMBO-path problem and not a
    present one.

Sizing a stop off config while holding a different quantity gets the trigger
wrong in exact proportion, and silently — the log line would print a plausible
dollar figure computed from the wrong multiplier.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _entry(contracts=7, width=5):
    return SimpleNamespace(
        entry_number=1,
        contracts=contracts,
        short_call_strike=7600, long_call_strike=7600 + width,
        short_put_strike=7500, long_put_strike=7500 - width,
        call_spread_credit=100.0, put_spread_credit=100.0,
        total_credit=200.0,
        call_side_stop=0.0, put_side_stop=0.0,
        call_side_skipped=False, put_side_skipped=False,
        call_only=False, put_only=False,
        spread_width=width,
    )


def _strat(cpe=7, pct=0.40):
    """Attribute set derived by introspecting the real method rather than
    guessed — an under-specified fixture fails on the wrong line and teaches
    nothing."""
    s = HydraStrategy.__new__(HydraStrategy)
    s.contracts_per_entry = cpe
    s.call_stop_buffer = 0.75
    s.put_stop_buffer = 1.75
    s.downday_theoretical_put_credit = 2.60
    s.buffer_decay_hours = 4.0
    s.buffer_decay_start_mult = 2.50
    s.narrow_spread_stop_enabled = True
    s.narrow_spread_stop_pct = pct
    s.narrow_spread_stop_shadow = False
    s._get_effective_stop_level = lambda entry, side: getattr(
        entry, f"{side}_side_stop", 0.0)
    s.logger = MagicMock()
    return s


class TestTheA2StopSizesFromTheEntry:
    """A2 = the %-of-width stop that is B's ACTING stop
    (narrow_spread_stop.enabled=true, pct_of_width=0.4)."""

    def _apply(self, strat, entry):
        """The A2 %-of-width override runs at the END of the stop calculator."""
        return HydraStrategy._calculate_stop_levels_hydra(strat, entry)

    def test_it_uses_the_entrys_count_when_config_disagrees(self):
        """The whole point: entry holds 6, config says 10 -> size on 6."""
        s = _strat(cpe=10, pct=0.40)
        e = _entry(contracts=6, width=5)
        self._apply(s, e)
        assert e.call_side_stop == pytest.approx(0.40 * 5 * 100 * 6)   # 1200, not 2000

    def test_the_equal_case_is_unchanged(self):
        """Today's reality — config and entry agree — must be bit-identical."""
        s = _strat(cpe=7, pct=0.40)
        e = _entry(contracts=7, width=5)
        self._apply(s, e)
        assert e.call_side_stop == pytest.approx(0.40 * 5 * 100 * 7)   # 1400

    def test_the_audits_scenario_is_sized_correctly(self):
        """10c config, 6 actually filled, 5pt, 40%: the trigger must be 40% of
        the REAL max loss (6 x 5 x 100 = $3,000), i.e. $1,200 — not $2,000,
        which would be 67% of it."""
        s = _strat(cpe=10, pct=0.40)
        e = _entry(contracts=6, width=5)
        self._apply(s, e)
        real_max_loss = 5 * 100 * 6
        assert e.call_side_stop == pytest.approx(0.40 * real_max_loss)
        assert e.call_side_stop / real_max_loss == pytest.approx(0.40)

    def test_a_missing_contract_count_falls_back_to_config(self):
        """A partially-constructed entry must not size a stop at zero."""
        s = _strat(cpe=7, pct=0.40)
        e = _entry(contracts=7, width=5)
        e.contracts = None
        self._apply(s, e)
        assert e.call_side_stop == pytest.approx(0.40 * 5 * 100 * 7)

    def test_zero_contracts_falls_back_rather_than_producing_a_zero_stop(self):
        """A 0 trigger would fire instantly on any move."""
        s = _strat(cpe=7, pct=0.40)
        e = _entry(contracts=0, width=5)
        self._apply(s, e)
        assert e.call_side_stop > 0


class TestTheCreditBufferPathAlsoSizesFromTheEntry:
    """A2 OVERWRITES the stop at the end, so with it enabled the credit+buffer
    path's contract count is invisible in the result. A mutation reverting THAT
    site to config survived the first version of this file for exactly that
    reason. These tests disable A2 so the credit+buffer stop is what survives —
    which is also variant A's and un-migrated C's real acting stop.
    """

    def _apply(self, strat, entry):
        strat.narrow_spread_stop_enabled = False
        return HydraStrategy._calculate_stop_levels_hydra(strat, entry)

    def test_the_min_stop_floor_scales_with_the_ENTRYs_count(self):
        """Floor is $0.50/contract (50.0 * n). Entry holds 6, config says 10:
        the floor must be $300, not $500."""
        s = _strat(cpe=10)
        e = _entry(contracts=6, width=5)
        e.call_spread_credit = e.put_spread_credit = 1.0   # far below any floor
        e.total_credit = 2.0
        self._apply(s, e)
        # both buffers are also n-scaled, so the whole stop tracks n
        assert e.call_side_stop == pytest.approx(
            50.0 * 6 + s.call_stop_buffer * 6)

    def test_the_buffers_scale_with_the_ENTRYs_count(self):
        s6 = _strat(cpe=10); e6 = _entry(contracts=6, width=5)
        e6.call_spread_credit = e6.put_spread_credit = 1.0; e6.total_credit = 2.0
        self._apply(s6, e6)
        s7 = _strat(cpe=10); e7 = _entry(contracts=7, width=5)
        e7.call_spread_credit = e7.put_spread_credit = 1.0; e7.total_credit = 2.0
        self._apply(s7, e7)
        assert e7.call_side_stop > e6.call_side_stop
        assert e6.call_side_stop / 6 == pytest.approx(e7.call_side_stop / 7)

    def test_config_disagreeing_does_not_change_the_result(self):
        """The defining property: identical entry, different config -> same stop."""
        a = _entry(contracts=6, width=5)
        a.call_spread_credit = a.put_spread_credit = 1.0; a.total_credit = 2.0
        b = _entry(contracts=6, width=5)
        b.call_spread_credit = b.put_spread_credit = 1.0; b.total_credit = 2.0
        self._apply(_strat(cpe=7), a)
        self._apply(_strat(cpe=20), b)
        assert a.call_side_stop == pytest.approx(b.call_side_stop)
        assert a.put_side_stop == pytest.approx(b.put_side_stop)


class TestBothSidesAndTheSourceItself:
    def test_the_put_side_is_sized_the_same_way(self):
        s = _strat(cpe=10, pct=0.40)
        e = _entry(contracts=6, width=5)
        TestTheA2StopSizesFromTheEntry()._apply(s, e)
        assert e.put_side_stop == pytest.approx(0.40 * 5 * 100 * 6)

    def test_neither_sizing_site_reads_config_directly_any_more(self):
        """Regression guard. Both sites take an `entry`; reading
        self.contracts_per_entry there is the defect."""
        import inspect
        src = inspect.getsource(HydraStrategy._calculate_stop_levels_hydra)
        assert "n = getattr(entry, \"contracts\", None) or self.contracts_per_entry" in src, (
            "_calculate_stop_levels_hydra must size from the entry"
        )

    def test_the_log_line_reports_the_count_it_actually_used(self):
        """A log printing the config count beside a stop computed from the
        entry count would send an operator chasing the wrong number."""
        import inspect
        src = inspect.getsource(HydraStrategy._calculate_stop_levels_hydra)
        assert "A2: Entry #" in src
        assert "{self.contracts_per_entry}c" not in src
        assert "{n}c" in src
