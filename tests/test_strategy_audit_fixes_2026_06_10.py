"""2026-06-10 strategy-audit fixes (three clear code bugs found by the audit).

A (S-HIGH-1): find_strike_at_delta max-delta clamp — a sparse/ATM-biased chain
  can return a 20-35delta short as "closest to 8delta" even off a FRESH profile;
  reject it so the caller falls back to the OTM-multiplier.
B (L-C2c): MKT-042 buffer decay must not push the effective stop ABOVE the
  spread_value clamp ceiling (width*100*contracts), or the credit+buffer stop is
  physically unfirable on narrow spreads.
C (S-HIGH-3): never book an ITM-settled short as full-credit PROFIT when the
  post-close SPX read fails — defer + retry instead.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.gex_provider import find_strike_at_delta, GEXProfile, StrikeDelta  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402
from bots.hydra.base_strategy import IronCondorEntry  # noqa: E402
from shared.market_hours import get_us_market_time  # noqa: E402


def _profile(deltas, spot=7300.0):
    return GEXProfile(spot=spot, expiry=date(2026, 6, 10),
                      fetched_at=datetime.now(timezone.utc), deltas=tuple(deltas))


class TestFindStrikeMaxDeltaClamp:
    def test_sparse_chain_too_close_rejected(self):
        # Only near-ATM puts carry delta; the "closest to 0.08" is really 0.30.
        prof = _profile([StrikeDelta(7250.0, "put", -0.30, 0.20),
                         StrikeDelta(7260.0, "put", -0.35, 0.20)])
        assert find_strike_at_delta(prof, side="put", target_delta_abs=0.08,
                                    max_delta_abs=0.16) is None

    def test_good_match_accepted(self):
        prof = _profile([StrikeDelta(7200.0, "put", -0.08, 0.20),
                         StrikeDelta(7250.0, "put", -0.30, 0.20)])
        assert find_strike_at_delta(prof, side="put", target_delta_abs=0.08,
                                    max_delta_abs=0.16) == 7200.0

    def test_no_clamp_is_backward_compatible(self):
        prof = _profile([StrikeDelta(7250.0, "put", -0.30, 0.20)])
        # No max_delta_abs -> existing behavior: closest returned, no rejection.
        assert find_strike_at_delta(prof, side="put", target_delta_abs=0.08) == 7250.0


def _strat_with_buffers():
    s = HydraStrategy.__new__(HydraStrategy)
    s.buffer_decay_start_mult = 2.5
    s.buffer_decay_hours = 4.0
    s.call_stop_buffer = 75.0     # 0.75 * 100
    s.put_stop_buffer = 250.0     # 2.50 * 100 (stored x100, confirmed strategy.py:360)
    return s


class TestDecayClampCap:
    def test_narrow_spread_decay_stays_firable(self):
        s = _strat_with_buffers()
        e = IronCondorEntry(entry_number=1)
        e.short_put_strike = 7290
        e.long_put_strike = 7285          # 5pt width
        e.put_side_stop = 3130.0
        e.contracts = 7
        e.entry_time = get_us_market_time()   # t~=0 -> decay_factor~=1 (max extra)
        eff = s._get_effective_stop_level(e, "put")
        ceiling = 5 * 100 * 7              # 3500 = spread_value clamp ceiling
        # Without the cap this would be ~5755 (> ceiling) and UNFIRABLE.
        assert eff < ceiling, f"effective stop {eff} must stay below the clamp ceiling {ceiling}"
        assert eff <= 0.9 * ceiling + 1e-6

    def test_wide_spread_cap_does_not_bite(self):
        s = _strat_with_buffers()
        e = IronCondorEntry(entry_number=1)
        e.short_put_strike = 7290
        e.long_put_strike = 7190          # 100pt width (wide HYDRA spread)
        e.put_side_stop = 3130.0
        e.contracts = 1
        e.entry_time = get_us_market_time()
        eff = s._get_effective_stop_level(e, "put")
        # 100pt ceiling = 10000; decayed effective (~3130 + 250*1*1.5 = 3505) < 0.9*10000
        assert eff > 3130.0   # decay still widens the stop on wide spreads
        assert eff < 0.9 * 100 * 100 * 1


class TestSettlementDeferOnUnreadableSpx:
    def _bare(self, dry_run):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = object()               # non-None
        s.dry_run = dry_run
        s.requires_protective_wings = True
        s.daily_state = SimpleNamespace(entries=[], total_realized_pnl=0.0)
        s._settlement_spx_level = lambda: None    # the post-close read FAILS
        s._read_open_positions = lambda: []
        s._alert_settlement_deferred = MagicMock()
        return s

    def test_live_path_defers_and_alerts(self):
        s = self._bare(dry_run=False)
        out = s._process_expired_credits()
        assert out == 0.0
        assert s._settlement_deferred is True   # do NOT book — retry next heartbeat
        s._alert_settlement_deferred.assert_called_once()

    def test_dry_run_keeps_legacy_no_defer(self):
        s = self._bare(dry_run=True)
        s._process_expired_credits()
        assert s._settlement_deferred is False   # None-by-design -> legacy worthless
        s._alert_settlement_deferred.assert_not_called()


class TestB2NakedShortOnPartialClose:
    """B2: in _close_entry_early, if the SHORT buy-back fails the long close must
    be ABORTED so we keep the full defined-risk spread instead of a naked short,
    and the side must NOT be marked expired (which would drop it from monitoring)."""

    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.commission_per_leg = 0.65
        s.daily_state = SimpleNamespace(total_commission=0.0, entries=[])
        s._alert_short_close_failed = MagicMock()
        s._book_early_close_side_pnl = MagicMock()
        s._record_stop_to_db = MagicMock()
        s._read_option_quote = lambda uic: {"bid": 5.0}   # non-worthless long
        return s

    def _put_entry(self):
        e = IronCondorEntry(entry_number=1)
        e.contracts = 7
        e.call_side_stopped = True            # exclude the call side from closing
        e.short_put_strike = 7290.0
        e.long_put_strike = 7285.0
        e.short_put_uic = "SP"
        e.long_put_uic = "LP"
        e.put_spread_credit = 1190.0
        return e

    def test_short_fail_aborts_long_keeps_spread(self):
        s = self._strat()
        attempted = []

        def fake_close(pos_id, leg_name, uic=None, entry_number=None, contracts=None):
            attempted.append(leg_name)
            if leg_name == "short_put":
                return (False, 0.0, None)     # short buy-back FAILS
            return (True, 1.0, "oid")          # long would have succeeded

        s._close_position_with_retry = fake_close
        e = self._put_entry()
        s._close_entry_early(e)

        assert "short_put" in attempted
        assert "long_put" not in attempted, "long must NOT be closed after the short failed"
        assert e.put_side_expired is False, "naked short avoided — side stays alive + monitored"
        s._alert_short_close_failed.assert_called_once()

    def test_short_success_closes_normally(self):
        s = self._strat()
        attempted = []

        def fake_close(pos_id, leg_name, uic=None, entry_number=None, contracts=None):
            attempted.append(leg_name)
            return (True, 1.0, "oid")          # both legs close

        s._close_position_with_retry = fake_close
        e = self._put_entry()
        s._close_entry_early(e)

        assert "short_put" in attempted and "long_put" in attempted
        assert e.put_side_expired is True       # normal full close
        s._alert_short_close_failed.assert_not_called()


# ============================ A2 / A1 stop redesign ============================

def _nss_strat(pct=0.40, enabled=True):
    s = HydraStrategy.__new__(HydraStrategy)
    s.contracts_per_entry = 7
    s.call_stop_buffer = 75.0
    s.put_stop_buffer = 250.0
    s.downday_theoretical_put_credit = 260.0
    s.buffer_decay_start_mult = None       # skip MKT-042 init log
    s.buffer_decay_hours = None
    s.narrow_spread_stop_enabled = enabled
    s.narrow_spread_stop_pct = pct
    return s


class TestPctOfWidthStop:
    def _ic(self):
        e = IronCondorEntry(entry_number=1)
        e.contracts = 7
        e.call_only = False
        e.put_only = False
        e.short_call_strike = 7425.0
        e.long_call_strike = 7430.0      # 5pt
        e.short_put_strike = 7290.0
        e.long_put_strike = 7285.0       # 5pt
        e.call_spread_credit = 190.0
        e.put_spread_credit = 1190.0
        return e

    def test_full_ic_stops_become_pct_of_width(self):
        s = _nss_strat(0.40)
        e = self._ic()
        s._calculate_stop_levels_hydra(e)
        expected = 0.40 * 5 * 100 * 7   # 1400 — NOT the 1905/3130 credit+buffer
        assert e.call_side_stop == expected
        assert e.put_side_stop == expected

    def test_disabled_keeps_credit_buffer(self):
        s = _nss_strat(0.40, enabled=False)
        e = self._ic()
        s._calculate_stop_levels_hydra(e)
        # credit+buffer: total_credit 1380 + put_buf 250*7=1750 -> 3130
        assert e.put_side_stop == 1380.0 + 250.0 * 7

    def test_decay_bypassed_in_pct_mode(self):
        # Even with decay configured, the % stop is returned as-is (no widening).
        s = _nss_strat(0.40)
        s.buffer_decay_start_mult = 2.5
        s.buffer_decay_hours = 4.0
        e = self._ic()
        e.put_side_stop = 1400.0
        e.entry_time = get_us_market_time()
        assert s._get_effective_stop_level(e, "put") == 1400.0


class TestSettlementHold:
    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.settlement_hold_enabled = True
        s.narrow_spread_stop_enabled = True
        s.settlement_hold_itm_pct = 0.70
        s.settlement_hold_minutes = 20.0
        return s

    def _put(self):
        e = IronCondorEntry(entry_number=1)
        e.contracts = 7                  # max = 5 * 100 * 7 = 3500; 70% = 2450
        e.short_put_strike = 7290.0
        e.long_put_strike = 7285.0
        return e

    def test_holds_deep_itm_near_close(self):
        s, e = self._strat(), self._put()
        with patch("bots.hydra.strategy.get_us_market_time",
                   return_value=datetime(2026, 6, 10, 15, 50)):   # 10 min to close
            assert s._settlement_hold_active(e, "put", 3000.0) is True
            assert s._settlement_hold_active(e, "put", 1000.0) is False   # not deep ITM

    def test_no_hold_when_far_from_close(self):
        s, e = self._strat(), self._put()
        with patch("bots.hydra.strategy.get_us_market_time",
                   return_value=datetime(2026, 6, 10, 13, 0)):    # 3h to close
            assert s._settlement_hold_active(e, "put", 3000.0) is False

    def test_no_hold_when_mode_disabled(self):
        s, e = self._strat(), self._put()
        s.narrow_spread_stop_enabled = False
        with patch("bots.hydra.strategy.get_us_market_time",
                   return_value=datetime(2026, 6, 10, 15, 50)):
            assert s._settlement_hold_active(e, "put", 3000.0) is False


class TestBreachAdvisory:
    def test_advisory_would_close_does_not_act(self):
        from bots.hydra.brandon.strategy import BrandonHydraStrategy
        from bots.hydra.brandon import gex_breach_exit
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.brandon_breach_exit_advisory = True
        s.brandon_breach_confirmation_seconds = 90
        s.brandon_decel_min_pct = 0.05
        s.current_price = 7270.0
        s._brandon_today_date = lambda: date(2026, 6, 10)
        s._brandon_now_et = lambda: datetime(2026, 6, 10, 15, 50)
        s._brandon_breach_states = {}
        s._close_entry_early = MagicMock()    # must NOT be called in advisory mode
        wall = SimpleNamespace(strike_low=6950.0, strike_high=7395.0)
        s._brandon_get_gex_profile = lambda d: SimpleNamespace(
            positive_clusters=lambda min_strength_pct: (wall,))
        s._brandon_side_alive = lambda e, side: side == "put"
        e = SimpleNamespace(entry_number=1, short_call_strike=7425.0, short_put_strike=7290.0)
        decision = SimpleNamespace(is_first_breach=False, would_close=True, reason="breach")
        with patch.object(gex_breach_exit, "evaluate_breach",
                          return_value=(decision, SimpleNamespace())):
            out = s._brandon_check_breach_exit(e)
        assert out is None                    # advisory: returns no action
        s._close_entry_early.assert_not_called()


class TestDeltaUsesCachedMarketNotRecompute:
    """2026-06-11 root-cause fix: find_strike_at_delta must use the cached MARKET
    delta (matches option prices), NOT a calendar-BS re-level that under-deltas 0DTE
    ~2x and picked a 33δ put as '8δ'. recompute_t_years is now a drift adjustment
    (BS(live) - BS(profile.spot)), not a re-level."""

    def _prof(self, spot):
        ds = [StrikeDelta(7185.0, "put", -0.087, 0.34),
              StrikeDelta(7200.0, "put", -0.110, 0.333),
              StrikeDelta(7220.0, "put", -0.157, 0.314),
              StrikeDelta(7250.0, "put", -0.281, 0.308)]
        return GEXProfile(spot=spot, expiry=date(2026, 6, 11),
                          fetched_at=datetime.now(timezone.utc), deltas=tuple(ds))

    def test_picks_8delta_by_cached_even_with_recompute(self):
        # profile.spot == live spot -> drift adj == 0 -> uses the cached delta ->
        # picks 7185 (~9δ, the real 8δ band), NOT the too-close strike the old
        # calendar-BS re-level would have landed on.
        p = self._prof(7288.0)
        out = find_strike_at_delta(p, side="put", target_delta_abs=0.08,
                                   spot_fallback=7288.0, recompute_t_years=0.000542,
                                   max_delta_abs=0.16)
        assert out == 7185.0

    def test_recompute_drift_zero_equals_no_recompute(self):
        # passing recompute_t_years with profile.spot == live spot must equal the
        # no-recompute (pure cached) result — proves it no longer re-levels.
        p = self._prof(7288.0)
        a = find_strike_at_delta(p, side="put", target_delta_abs=0.08, spot_fallback=7288.0,
                                 recompute_t_years=0.000542, max_delta_abs=0.16)
        b = find_strike_at_delta(p, side="put", target_delta_abs=0.08, spot_fallback=7288.0,
                                 max_delta_abs=0.16)
        assert a == b == 7185.0

    def test_skips_strikes_without_cached_delta(self):
        # a strike with delta=None is not a candidate; the only cached one (7250,
        # 0.28) exceeds the 0.16 clamp -> None -> caller falls back to OTM-multiplier.
        ds = [StrikeDelta(7185.0, "put", None, 0.34),
              StrikeDelta(7250.0, "put", -0.281, 0.308)]
        p = GEXProfile(spot=7288.0, expiry=date(2026, 6, 11),
                       fetched_at=datetime.now(timezone.utc), deltas=tuple(ds))
        out = find_strike_at_delta(p, side="put", target_delta_abs=0.08,
                                   spot_fallback=7288.0, recompute_t_years=0.000542,
                                   max_delta_abs=0.16)
        assert out is None
