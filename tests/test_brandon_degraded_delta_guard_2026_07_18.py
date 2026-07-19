"""2026-07-18: Brandon degraded-Polygon-data guard + honest dry-run.

Root cause (2026-07-17, OPEX Friday): Polygon's greek feed degraded (80/1000
strikes hydrated). Brandon's 8δ delta-target, finding no real 8δ strikes among a
sparse chain, selected the "closest" — actually ~0.5-1δ, far-OTM, ~$0.05
premium. Every existing guard missed it: the profile was FRESH (stale guard
passed), the pick was BELOW the max-delta clamp (too-close guard passed), and the
credit was ~$0 (too-rich price-veto couldn't fire). Consequences: live C churned
protective-long leg-ins/unwinds (net-credit guard-floor saved it → 0 positions);
dry-run B booked 3 phantom $0-credit ICs → dashboard −$138 (pure commission).

Fixes under test:
  1. find_strike_at_delta(return_delta=True) → (strike, achieved_delta) so the
     caller can apply a MIN-delta floor (mirror of the max clamp).
  2. _calculate_strikes DEGRADED-DATA FLOOR: a picked short below
     target × min_delta_pct_of_target (0.5 → 4δ for 8δ) → abort_entry_reason +
     alert → clean SKIP (operator-chosen over the OTM-multiplier fallback).
  3. _skip_degraded_entry: records a SKIP (not a failed retry).
  4. base _simulate_entry HONEST-DRY-RUN: a sim credit below the net-credit
     floor a live entry would require → skip, not a phantom $0-credit IC.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon import gex_provider  # noqa: E402
from bots.hydra.brandon.gex_provider import GEXProfile, StrikeDelta, find_strike_at_delta  # noqa: E402
from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


# ============================================================ Fix 1a: return_delta
def _prof(deltas):
    """GEXProfile from (strike, side, delta) tuples."""
    return GEXProfile(
        spot=7468.0,
        expiry=date(2026, 7, 17),
        fetched_at=datetime.now(timezone.utc),
        strikes=tuple(),
        deltas=tuple(StrikeDelta(strike=s, contract_type=t, delta=d) for s, t, d in deltas),
    )


class TestReturnDeltaContract:
    def test_scalar_contract_unchanged_by_default(self):
        prof = _prof([(7325.0, "put", -0.08), (7300.0, "put", -0.04)])
        out = find_strike_at_delta(prof, side="put", target_delta_abs=0.08)
        assert isinstance(out, float) and out == 7325.0

    def test_return_delta_yields_strike_and_delta(self):
        prof = _prof([(7325.0, "put", -0.08), (7300.0, "put", -0.04)])
        strike, delta = find_strike_at_delta(
            prof, side="put", target_delta_abs=0.08, return_delta=True)
        assert strike == 7325.0
        assert abs(delta) == 0.08

    def test_return_delta_none_pair_when_no_candidates(self):
        prof = _prof([(7600.0, "call", 0.08)])  # no puts
        assert find_strike_at_delta(
            prof, side="put", target_delta_abs=0.08, return_delta=True) == (None, None)

    def test_return_delta_none_pair_when_over_max_clamp(self):
        # only a 30δ put available; max clamp 16δ → (None, None)
        prof = _prof([(7400.0, "put", -0.30)])
        assert find_strike_at_delta(
            prof, side="put", target_delta_abs=0.08, max_delta_abs=0.16,
            return_delta=True) == (None, None)

    def test_return_delta_reports_far_otm_pick(self):
        # THE 2026-07-17 case: only near-worthless ~0.5δ strikes hydrated.
        prof = _prof([(7325.0, "put", -0.005)])
        strike, delta = find_strike_at_delta(
            prof, side="put", target_delta_abs=0.08, return_delta=True)
        assert strike == 7325.0
        assert abs(delta) == 0.005  # caller's floor must reject this


# ==================================================== Fix 1b/2: delta-floor guard
def _bstrat(min_pct=0.5):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s.brandon_delta_target_enabled = True
    s.current_price = 7468.0
    s.current_vix = 18.5
    s.brandon_delta_target_pct = 0.08
    s.brandon_delta_target_min_pct_of_target = min_pct
    s.brandon_delta_target_max_credit_pct_of_width = 0.0  # disable price-veto
    s.strike_increment = 5
    s._brandon_today_date = lambda: date(2026, 7, 17)
    s._brandon_send_telegram = MagicMock()
    s._brandon_estimate_t_years_to_close = MagicMock(return_value=0.001)
    s._get_vix_adjusted_spread_width = MagicMock(return_value=5)
    s._brandon_get_gex_profile = MagicMock(return_value=SimpleNamespace(
        deltas=({"x": 1},), fetched_at=datetime.now(timezone.utc), spot=7468.0))
    return s


def _entry():
    return SimpleNamespace(entry_number=4, short_call_strike=0.0, long_call_strike=0.0,
                           short_put_strike=0.0, long_put_strike=0.0)


class TestDeltaFloorGuard:
    def test_far_otm_pick_aborts_and_alerts(self):
        s = _bstrat(min_pct=0.5)   # floor = 0.04 (4δ)
        e = _entry()
        with patch.object(HydraStrategy, "_calculate_strikes", return_value="FALLBACK") as sup, \
             patch("bots.hydra.brandon.gex_provider.find_strike_at_delta",
                   side_effect=[(7570.0, 0.008), (7325.0, -0.005)]):
            ret = s._calculate_strikes(e)
        assert ret is True                         # returns True; abort flag routes the skip
        assert getattr(e, "abort_entry_reason", None)
        assert "under-hydrated" in e.abort_entry_reason
        sup.assert_not_called()                    # SKIP, not the OTM-multiplier fallback
        s._brandon_send_telegram.assert_called_once()

    def test_put_side_far_otm_also_caught(self):
        s = _bstrat(min_pct=0.5)
        e = _entry()
        with patch("bots.hydra.brandon.gex_provider.find_strike_at_delta",
                   side_effect=[(7570.0, 0.07), (7325.0, -0.005)]):  # call fine, put garbage
            s._calculate_strikes(e)
        assert getattr(e, "abort_entry_reason", None)
        assert "put" in e.abort_entry_reason

    def test_near_target_pick_does_not_abort(self):
        s = _bstrat(min_pct=0.5)
        e = _entry()
        with patch("bots.hydra.brandon.gex_provider.find_strike_at_delta",
                   side_effect=[(7570.0, 0.07), (7325.0, -0.09)]):  # 7δ / 9δ, both > 4δ floor
            ret = s._calculate_strikes(e)
        assert ret is True
        assert not getattr(e, "abort_entry_reason", None)
        assert e.short_call_strike == 7570.0 and e.short_put_strike == 7325.0

    def test_floor_disabled_when_pct_zero(self):
        s = _bstrat(min_pct=0.0)   # 0 disables the floor
        e = _entry()
        with patch("bots.hydra.brandon.gex_provider.find_strike_at_delta",
                   side_effect=[(7570.0, 0.008), (7325.0, -0.005)]):
            s._calculate_strikes(e)
        assert not getattr(e, "abort_entry_reason", None)


# ==================================================== Fix 1c: _skip_degraded_entry
class TestSkipDegradedEntry:
    def test_records_skip_and_advances(self):
        from bots.hydra.base_strategy import MEICState
        s = HydraStrategy.__new__(HydraStrategy)
        s.daily_state = SimpleNamespace(entries_skipped=0, credit_gate_skips=0)
        s._entry_in_progress = True
        s._current_entry = object()
        s.state = None
        s._next_entry_index = 3
        s._log_safety_event = MagicMock()
        s._record_skipped_entry = MagicMock()
        msg = s._skip_degraded_entry(_entry(), 4, "chain under-hydrated, picked garbage")
        assert s.daily_state.entries_skipped == 1
        assert s.daily_state.credit_gate_skips == 1
        assert s._entry_in_progress is False
        assert s._current_entry is None
        assert s.state == MEICState.MONITORING
        assert s._next_entry_index == 4
        s._record_skipped_entry.assert_called_once()
        assert "degraded data" in msg


# ==================================================== Fix 3: honest dry-run
def _simstrat(flag=True, contracts=10):
    # HydraStrategy is the concrete subclass; _simulate_entry is the inherited
    # base method (only Brandon overrides it, via super()).
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = True
    s.contracts_per_entry = contracts
    s.min_net_credit_per_contract = 0.05          # → $5/contract floor
    s.skip_dryrun_below_net_credit_floor = flag
    s.spread_width = 5
    s._get_todays_expiry = MagicMock(return_value=date(2026, 7, 17))
    s._get_option_uic = MagicMock(return_value=12345)
    return s


def _sim_entry():
    return SimpleNamespace(entry_number=4,
                           short_call_strike=7570.0, long_call_strike=7575.0,
                           short_put_strike=7325.0, long_put_strike=7320.0,
                           call_spread_credit=0.0, put_spread_credit=0.0,
                           short_call_uic=0, long_call_uic=0,
                           short_put_uic=0, long_put_uic=0)


class TestHonestDryRun:
    def test_below_floor_skips_no_phantom(self):
        s = _simstrat(flag=True)
        s._estimate_entry_credit = MagicMock(return_value=(0.0, 0.0))  # degraded → $0
        e = _sim_entry()
        ret = s._simulate_entry(e)
        assert ret is False
        assert getattr(e, "abort_entry_reason", None)
        assert "net-credit floor" in e.abort_entry_reason

    def test_full_ic_one_viable_side_books(self):
        # 2026-07-18 review: a full IC where ONE side clears the floor must NOT
        # be skipped — live legs the viable side in (fails open). Only a
        # BOTH-sides-worthless IC is a phantom.
        s = _simstrat(flag=True)
        s._estimate_entry_credit = MagicMock(return_value=(150.0, 0.0))  # put transiently $0
        e = _sim_entry()
        assert s._simulate_entry(e) is True
        assert not getattr(e, "abort_entry_reason", None)
        assert e.call_spread_credit == 1500.0

    def test_intentional_put_only_not_skipped(self):
        # 2026-07-18 review CONFIRMED bug: a legit one-sided entry (put_only via
        # the GEX adjuster) has call_spread_credit == 0.0 BY DESIGN; the floor
        # must NOT trip on the intentionally-absent call side.
        s = _simstrat(flag=True)
        s._estimate_entry_credit = MagicMock(return_value=(0.0, 200.0))  # call skipped, put viable
        e = _sim_entry()
        e.short_call_strike = 0.0          # call zeroed by the adjuster
        e.long_call_strike = 0.0
        e.put_only = True
        e.call_side_skipped = True
        assert s._simulate_entry(e) is True
        assert not getattr(e, "abort_entry_reason", None)
        assert e.put_spread_credit == 2000.0

    def test_viable_credit_books_normally(self):
        s = _simstrat(flag=True)
        s._estimate_entry_credit = MagicMock(return_value=(150.0, 140.0))
        e = _sim_entry()
        assert s._simulate_entry(e) is True
        assert not getattr(e, "abort_entry_reason", None)
        assert e.call_spread_credit == 1500.0 and e.put_spread_credit == 1400.0

    def test_flag_off_books_phantom_old_behavior(self):
        s = _simstrat(flag=False)
        s._estimate_entry_credit = MagicMock(return_value=(0.0, 0.0))
        e = _sim_entry()
        assert s._simulate_entry(e) is True            # old behavior preserved when disabled
        assert not getattr(e, "abort_entry_reason", None)
