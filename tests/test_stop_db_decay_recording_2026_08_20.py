"""2026-08-20 (execution audit finding, variant A 2026-08-19 Entry #2 put):
_execute_stop_loss's non-short-only-stop branch recorded entry.call_side_stop/
put_side_stop (the STATIC base stop level, set once at entry time) into
trade_stops.trigger_level — but the live trigger check a few lines below (and
MKT-036 confirmation) fires against _get_effective_stop_level()'s dynamically
MKT-042-decayed value, which is never written back onto the entry. Confirmed
on real production data: DB showed trigger_level=405.0 while the log's
STOP-DETAIL lines showed the stop actually fired against an effective decayed
trigger of ~$530. Any analysis reading "how close was the stop to firing"
(buffer calibration, slot_edge, HERMES) was off by the decay multiplier for
any stop firing inside the ~4h decay window — most 0DTE stops.

Round-1 adversarial review found the initial version of this fix leaked into
_record_stop_to_db's net_pnl fallback (used only when actual_close_cost is
None) — that fallback recomputes -(stop_level - credit), and it must stay
consistent with daily_state.total_realized_pnl (booked inside
super()._execute_stop_loss() using its own separate, still-static local
stop_level), not drift to the decayed value. Fixed by decoupling: stop_level
(positional, unchanged meaning) still feeds the net_pnl fallback;
effective_trigger_level (new, keyword-only) is the ONLY thing that changed —
it feeds just the trigger_level DB column.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from bots.hydra.base_strategy import MEICStrategy  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402
from shared.market_hours import get_us_market_time  # noqa: E402


def _strat():
    s = HydraStrategy.__new__(HydraStrategy)
    s.short_only_stop = False
    s.stop_confirmation_enabled = False
    s.narrow_spread_stop_enabled = False  # A2 off — this is the credit+buffer path
    s.buffer_decay_start_mult = 2.5
    s.buffer_decay_hours = 4.0
    s.call_stop_buffer = 0.75
    s.put_stop_buffer = 1.75
    s._record_stop_to_db = MagicMock()
    return s


def _entry(*, side_stop=1575.0, contracts=7, entry_time=None, actual_debit=550.0):
    return SimpleNamespace(
        call_side_stop=side_stop, put_side_stop=side_stop,
        contracts=contracts,
        entry_time=entry_time or (get_us_market_time() - timedelta(hours=1)),
        long_call_strike=7695, short_call_strike=7690,
        long_put_strike=7690, short_put_strike=7695,
        actual_call_stop_debit=actual_debit, actual_put_stop_debit=actual_debit,
    )


class TestStopDbRecordsEffectiveDecayedLevel:
    def test_effective_trigger_level_is_decayed_not_static(self):
        s = _strat()
        e = _entry()
        # 1h elapsed of a 4h decay window, start_mult 2.5, put_buffer 1.75, 7c:
        # decay_factor = 1 - 1/4 = 0.75; extra = 1.75*7*(2.5-1)*0.75 = 13.78125
        # effective = 1575.0 + 1378.125 = 2953.125 -- material and != static 1575.0
        with patch.object(MEICStrategy, "_execute_stop_loss", return_value="closed"):
            s._execute_stop_loss(e, "put")

        s._record_stop_to_db.assert_called_once()
        kwargs = s._record_stop_to_db.call_args.kwargs
        effective = s._get_effective_stop_level(e, "put")
        assert kwargs["effective_trigger_level"] == pytest.approx(effective)
        assert kwargs["effective_trigger_level"] != pytest.approx(e.put_side_stop), (
            "regressed: not recording the MKT-042-decayed effective level "
            "actually used to trigger"
        )

    def test_positional_stop_level_stays_static_for_net_pnl_consistency(self):
        """Round-1 review finding: the positional stop_level arg (which
        _record_stop_to_db's net_pnl fallback reads) must NOT be the decayed
        value — it has to stay consistent with daily_state.total_realized_pnl,
        which super()._execute_stop_loss() books using the static level."""
        s = _strat()
        e = _entry()
        with patch.object(MEICStrategy, "_execute_stop_loss", return_value="closed"):
            s._execute_stop_loss(e, "put")

        positional_stop_level = s._record_stop_to_db.call_args.args[2]
        assert positional_stop_level == pytest.approx(e.put_side_stop)

    def test_no_decay_configured_effective_equals_static(self):
        """When decay is off entirely, effective == static — both parameters
        end up equal, but this pins the fallback path stays consistent with
        _get_effective_stop_level (not a hardcoded static read)."""
        s = _strat()
        s.buffer_decay_start_mult = None
        e = _entry()
        with patch.object(MEICStrategy, "_execute_stop_loss", return_value="closed"):
            s._execute_stop_loss(e, "call")

        kwargs = s._record_stop_to_db.call_args.kwargs
        assert kwargs["effective_trigger_level"] == pytest.approx(e.call_side_stop)
        assert s._record_stop_to_db.call_args.args[2] == pytest.approx(e.call_side_stop)

    def test_a2_mode_bypasses_decay_matching_effective_level_helper(self):
        """A2 (%-of-width) mode already stores the % trigger directly on the
        entry and _get_effective_stop_level correctly skips decay for it —
        confirm the DB recording follows the same helper, not a raw read."""
        s = _strat()
        s.narrow_spread_stop_enabled = True
        e = _entry(side_stop=1400.0)
        with patch.object(MEICStrategy, "_execute_stop_loss", return_value="closed"):
            s._execute_stop_loss(e, "call")

        kwargs = s._record_stop_to_db.call_args.kwargs
        assert kwargs["effective_trigger_level"] == pytest.approx(1400.0)


class TestRecordStopToDbNetPnlFallbackUnaffectedByDecay:
    """Direct test of _record_stop_to_db itself: effective_trigger_level must
    change ONLY the trigger_level DB field, never net_pnl's fallback."""

    def _strat_with_recorder(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s._data_recorder = MagicMock()
        s.current_price = 7690.0
        s._last_stop_time = None
        return s

    def _entry_for_db(self, *, credit=175.0):
        return SimpleNamespace(
            entry_number=2, entry_time=None, _spx_at_entry=None,
            call_spread_value=None, call_spread_credit=credit,
            put_spread_value=None, put_spread_credit=credit,
            contracts=7,
        )

    def test_net_pnl_fallback_uses_static_stop_level_not_effective(self):
        s = self._strat_with_recorder()
        e = self._entry_for_db(credit=175.0)

        # actual_close_cost=None -> net_pnl fallback fires: -(stop_level - credit).
        # Static stop_level=1575 -> net_pnl = -(1575-175) = -1400.
        # If the decayed effective_trigger_level (2953.125) leaked in instead,
        # net_pnl would wrongly be -(2953.125-175) = -2778.125.
        s._record_stop_to_db(
            e, "call", stop_level=1575.0, actual_close_cost=None,
            effective_trigger_level=2953.125,
        )

        recorded = s._data_recorder.record_stop.call_args[0][0]
        assert recorded["trigger_level"] == pytest.approx(2953.125)
        assert recorded["net_pnl"] == pytest.approx(-1400.0)

    def test_effective_trigger_level_defaults_to_stop_level_when_omitted(self):
        """Existing call sites that don't pass effective_trigger_level (e.g.
        one-sided/A2 paths untouched by this fix) must be unaffected."""
        s = self._strat_with_recorder()
        e = self._entry_for_db(credit=175.0)

        s._record_stop_to_db(e, "call", stop_level=1575.0, actual_close_cost=None)

        recorded = s._data_recorder.record_stop.call_args[0][0]
        assert recorded["trigger_level"] == pytest.approx(1575.0)
        assert recorded["net_pnl"] == pytest.approx(-1400.0)

    def test_known_close_cost_ignores_both_stop_level_and_effective(self):
        """I-M2: when actual_close_cost is known, net_pnl = credit - cost
        regardless of either stop_level parameter."""
        s = self._strat_with_recorder()
        e = self._entry_for_db(credit=175.0)

        s._record_stop_to_db(
            e, "call", stop_level=1575.0, actual_close_cost=70.0,
            effective_trigger_level=2953.125,
        )

        recorded = s._data_recorder.record_stop.call_args[0][0]
        assert recorded["net_pnl"] == pytest.approx(105.0)  # 175 - 70
