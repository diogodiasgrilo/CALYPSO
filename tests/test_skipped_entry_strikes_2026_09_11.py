"""
Record the strikes a skipped entry WOULD have used (2026-09-11).

THE GAP. `skipped_entries` has carried `theoretical_short_call` /
`theoretical_long_call` / `theoretical_short_put` / `theoretical_long_put` since
schema v8, and **nothing has ever populated them**. Measured on variant B:

    live-era skipped entries          154
      ...GEX accel-zone vetoes         95
      ...vetoes WITH strikes recorded   0

Without the strikes, the counterfactual the table exists for — *"would the
entries the GEX adjuster vetoed have won?"* — is not computable at all. That is
why the GEX-veto EV question has stayed open through three audits: it was never
a question of analysis effort, it was that the inputs were never written down.

The companion half is `DataRecorder.update_skipped_entry_backtest`, which writes
`would_have_stopped` / `theoretical_pnl` and has zero callers repo-wide (198 rows,
0 populated). Recording the strikes is the PREREQUISITE for wiring that; without
them there is nothing to compute an outcome from.

A VETOED SIDE MAY BE ABSENT, and the analysis must not assume otherwise. The GEX
adjuster zeroes the side it drops, so on a GEX skip typically only the SURVIVING
side carries a strike. That is still the useful half — it is the side that WOULD
have been placed — so this records what is present and leaves filtering to the
reader rather than inventing a value.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _proposed(sc=7650, lc=7655, sp=7550, lp=7545):
    return SimpleNamespace(
        short_call_strike=sc, long_call_strike=lc,
        short_put_strike=sp, long_put_strike=lp,
    )


def _strat():
    s = HydraStrategy.__new__(HydraStrategy)
    s.current_price = 7600.0
    s.current_vix = 15.0
    s.contracts_per_entry = 7
    s._data_recorder = MagicMock()
    s._record_shadow_entry = MagicMock()
    s.daily_state = SimpleNamespace(entries=[], entries_skipped=0)
    s.alert_service = MagicMock()
    s._log_safety_event = MagicMock()
    return s


def _recorded(strat):
    """The dict handed to record_skipped_entry."""
    assert strat._data_recorder.record_skipped_entry.called, "nothing was recorded"
    return strat._data_recorder.record_skipped_entry.call_args.args[0]


def _call(strat, **kw):
    try:
        HydraStrategy._record_skipped_entry(strat, 1, "test skip", **kw)
    except Exception:
        # The alert/shadow tail is not under test; the DB write happens first.
        pass
    return _recorded(strat)


class TestTheStrikesAreRecorded:
    def test_a_proposed_entry_supplies_all_four_strikes(self):
        d = _call(_strat(), proposed_entry=_proposed())
        assert d["theoretical_short_call"] == 7650
        assert d["theoretical_long_call"] == 7655
        assert d["theoretical_short_put"] == 7550
        assert d["theoretical_long_put"] == 7545

    def test_without_a_proposed_entry_they_are_None_not_zero(self):
        """None means 'not applicable'. Zero would look like a real strike of 0
        and would pass a `> 0` filter in exactly the wrong direction."""
        d = _call(_strat())
        for k in ("theoretical_short_call", "theoretical_long_call",
                  "theoretical_short_put", "theoretical_long_put"):
            assert d[k] is None

    def test_a_ZEROED_side_records_None_rather_than_zero(self):
        """The GEX adjuster zeroes the side it drops. 0 is not a strike — it must
        not be stored as one, or the measurable-set query (`strike > 0`) would
        count a dropped side as present."""
        d = _call(_strat(), proposed_entry=_proposed(sc=0, lc=0))
        assert d["theoretical_short_call"] is None
        assert d["theoretical_long_call"] is None
        assert d["theoretical_short_put"] == 7550   # surviving side intact

    def test_the_surviving_half_is_still_recorded(self):
        """The whole point of tolerating a half-populated row: the surviving side
        IS the side that would have been placed."""
        d = _call(_strat(), proposed_entry=_proposed(sp=0, lp=0))
        assert d["theoretical_short_call"] == 7650
        assert d["theoretical_short_put"] is None

    def test_the_existing_fields_are_untouched(self):
        """Additive only — a regression here would corrupt the whole table."""
        d = _call(_strat(), proposed_entry=_proposed())
        assert d["spx_at_skip"] == 7600.0
        assert d["vix_at_skip"] == 15.0
        assert d["contracts"] == 0
        assert d["skip_reason"] == "test skip"


class TestTheVetoSitesActuallyPassTheEntry:
    """A parameter nothing passes records nothing. These are the two sites that
    already hold a proposed entry — the GEX/require-both-sides veto (95 of B's
    live-era skips) and the degraded-data abort."""

    def test_require_both_sides_forwards_the_entry(self):
        import inspect
        src = inspect.getsource(HydraStrategy._skip_require_both_sides)
        assert "proposed_entry=entry" in src

    def test_the_degraded_data_skip_forwards_the_entry(self):
        import inspect
        src = inspect.getsource(HydraStrategy._skip_degraded_entry)
        assert "proposed_entry=entry" in src

    def test_the_credit_gate_skip_forwards_the_entry(self):
        """Found live: 2026-09-11 entry #3 was a credit-gate skip (call $0.07 <
        $0.10 minimum) and recorded SC=None/SP=None, because the morning's change
        wired only the GEX/require-both-sides and degraded-data paths. The credit
        gate is a SEPARATE site and is ~15 of the 64 skips on B's zero-entry
        days."""
        import inspect
        src = inspect.getsource(HydraStrategy)
        i = src.index("skipped - credit gate (MKT-011/MKT-032)")
        assert "proposed_entry=entry" in src[i-700:i], (
            "the credit-gate skip must record the strikes it would have used"
        )

    def test_pre_strike_skips_are_deliberately_NOT_wired(self):
        """Margin / whipsaw / FOMC / pivot skips fire BEFORE strikes are chosen,
        so there is nothing to record. Not an oversight — wiring them would
        record None and imply the data was attempted."""
        import inspect
        for fn, marker in ((HydraStrategy._initiate_entry, "Insufficient margin"),
                           (HydraStrategy._initiate_entry, "FOMC T+1 blackout")):
            src = inspect.getsource(fn)
            i = src.index(marker)
            assert "proposed_entry" not in src[i:i+400]

    def test_the_recorder_accepts_the_parameter(self):
        import inspect
        sig = inspect.signature(HydraStrategy._record_skipped_entry)
        assert "proposed_entry" in sig.parameters
        assert sig.parameters["proposed_entry"].default is None


class TestItNeverBreaksTheSkipItself:
    """Recording telemetry must never cost us a clean skip — a skip that raises
    would leave _entry_in_progress set and wedge the entry loop."""

    def test_a_malformed_proposed_entry_does_not_raise(self):
        d = _call(_strat(), proposed_entry=object())
        assert d["theoretical_short_call"] is None

    def test_a_None_proposed_entry_does_not_raise(self):
        assert _call(_strat(), proposed_entry=None)["theoretical_short_put"] is None

    @pytest.mark.parametrize("bad", ["", 0, False, []])
    def test_falsey_non_entries_are_tolerated(self, bad):
        assert _call(_strat(), proposed_entry=bad)["theoretical_long_put"] is None
