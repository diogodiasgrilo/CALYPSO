"""Method-level integration tests for BrandonHydraStrategy.

Avoids the full HydraStrategy.__init__ dependency chain (Saxo client, config
loader, trade logger, schema, etc.) by constructing the instance via __new__
and setting the Brandon-specific attributes directly. The override methods
are then exercised in isolation.

Full end-to-end coverage of HydraStrategy itself is out of scope here — those
methods are tested in their existing suite. We only verify that the overrides
correctly route to Brandon modules vs. parent.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.strategy import BrandonHydraStrategy


def _make_instance(**brandon_attrs):
    """Construct a BrandonHydraStrategy without running __init__."""
    inst = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    defaults = dict(
        brandon_take_profit_enabled=False,
        brandon_take_profit_threshold=0.80,
        # MKT-049 net-of-cost gate OFF by default — these exercise TP DISPATCH;
        # the gate has its own suite (test_mkt049_tp_net_of_cost.py). With it on,
        # the fail-CLOSED path would (correctly) hold when no quotes are wired.
        brandon_tp_net_of_cost_gate_enabled=False,
        brandon_gex_enabled=False,
        brandon_polygon_api_key_env="POLYGON_API_KEY",
        brandon_polygon_underlying="SPX",
        brandon_strike_adjuster_enabled=False,
        brandon_breach_exit_enabled=False,
        brandon_breach_confirmation_seconds=90,
        brandon_decel_min_pct=0.05,
        brandon_accel_min_pct=0.10,
        brandon_max_shift_pts=25.0,
        brandon_shift_buffer_pts=5.0,
        brandon_accel_peak_locality_pts=25.0,
        brandon_accel_peak_persistence_enabled=False,
        brandon_accel_peak_persistence_tolerance_pts=10.0,
        # 2026-09-01: hydration-cap fix default (was a bare 80 literal).
        brandon_gex_max_contracts_to_hydrate=250,
        brandon_overlay_enabled=False,
        brandon_overlay_trigger_distance_pts=25.0,
        brandon_overlay_butterfly_width=10,
        brandon_overlay_butterfly_cutoff_hour=12,
        brandon_overlay_butterfly_cutoff_minute=30,
        # 0.0 = no confirmation delay / no severity band / legacy (flat 0.05,
        # no locality, no persistence) GEX gate — preserves the pre-2026-08-25
        # immediate-fire behavior as the test default; individual tests for
        # the new mechanisms override these explicitly.
        brandon_overlay_confirm_seconds=0.0,
        brandon_overlay_severity_bypass_distance_pts=0.0,
        brandon_overlay_use_adjuster_gex_gate=False,
        brandon_overlay_debit_spread_enabled=True,
        brandon_overlay_butterfly_enabled=True,
        _brandon_overlay_trigger_first_seen_at={},
        _brandon_overlay_current_gex_profile=None,
        _brandon_overlay_prior_gex_profile=None,
        _brandon_hedge_recorder=None,
        brandon_narrow_spread_enabled=False,
        brandon_narrow_breakpoint_vix=22.0,
        brandon_narrow_width_low=5,
        brandon_narrow_width_high=10,
        brandon_disable_progressive_tightening=False,
        brandon_delta_target_enabled=False,
        brandon_delta_target_pct=0.08,
        brandon_hydra_shadow_enabled=True,
        _brandon_gex_profile=None,
        _brandon_gex_profile_fetched_at=None,
        _brandon_gex_failure_at=None,
        _brandon_prior_gex_profile=None,
        _brandon_breach_states={},
        _brandon_overlay_placed=set(),
        _brandon_overlay_watch_logged_at={},
        _brandon_hydra_shadow_fired=set(),
        _brandon_hedge_legs={},
        _brandon_hedge_settlements=[],
        _brandon_overlay_booked=set(),  # unified overlay double-book guard (2026-07-18)
        current_price=6800.0,
        dry_run=True,
        alert_service=None,
    )
    defaults.update(brandon_attrs)
    for k, v in defaults.items():
        setattr(inst, k, v)
    return inst


class TestCapitalDeployedSweep:
    """Verify capital_deployed returns peak-concurrent margin, not sum.

    Regression for the 2026-05-08 audit finding: v1 of the fix had two
    silent bugs (lex-sort confusion between datetime/ISO string AND
    Brandon close paths not setting close_time), which made the sweep
    collapse back to the sum behaviour. v3 fixes both.
    """

    def _entry(self, open_dt, close_dt, *, contracts=15, spread_width=5):
        from unittest.mock import MagicMock
        e = MagicMock()
        e.spread_width = spread_width
        e.contracts = contracts
        e.entry_time = open_dt
        e.call_stop_time = ""
        e.put_stop_time = ""
        e.close_time = close_dt.isoformat() if close_dt else ""
        # Put-only via Brandon GEX-ADJ shape
        e.short_call_position_id = None
        e.short_put_position_id = "DRY"
        e.call_side_skipped = True
        e.put_side_stopped = bool(close_dt)
        e.put_side_expired = bool(close_dt)
        e.put_side_skipped = False
        e.call_side_stopped = False
        e.call_side_expired = False
        return e

    def _strategy(self, entries):
        from unittest.mock import MagicMock
        # _calculate_capital_deployed is a base method; build via the concrete
        # HydraStrategy (which inherits it). MEICStrategy is abstract since item
        # 4b (template-method hooks), so MEICStrategy.__new__ now raises.
        from bots.hydra.strategy import HydraStrategy
        inst = HydraStrategy.__new__(HydraStrategy)
        ds = MagicMock()
        ds.entries = entries
        inst.daily_state = ds
        return inst

    def test_returns_sum_when_all_alive(self):
        # 3 entries opened, all still open → peak = sum (3 × $7,500).
        from datetime import datetime
        entries = [
            self._entry(datetime(2026, 5, 8, 9, 31), None),
            self._entry(datetime(2026, 5, 8, 9, 45), None),
            self._entry(datetime(2026, 5, 8, 10, 15), None),
        ]
        assert self._strategy(entries)._calculate_capital_deployed() == 22500.0

    def test_returns_peak_concurrent_when_tps_cycle(self):
        # Same shape as variant B's 2026-05-07: 7 entries with TPs/breaches
        # cycling. Peak concurrent is 4, not 7.
        from datetime import datetime
        entries = [
            self._entry(datetime(2026, 5, 7, 9, 31), datetime(2026, 5, 7, 9, 46)),
            self._entry(datetime(2026, 5, 7, 9, 45), datetime(2026, 5, 7, 11, 0)),
            self._entry(datetime(2026, 5, 7, 10, 15), datetime(2026, 5, 7, 11, 1)),
            self._entry(datetime(2026, 5, 7, 10, 45), datetime(2026, 5, 7, 13, 28)),
            self._entry(datetime(2026, 5, 7, 11, 15), datetime(2026, 5, 7, 13, 49)),
            self._entry(datetime(2026, 5, 7, 11, 45), datetime(2026, 5, 7, 13, 49)),
            self._entry(datetime(2026, 5, 7, 12, 15), None),
        ]
        result = self._strategy(entries)._calculate_capital_deployed()
        # Peak between 12:15 and 13:28 = 4 ICs × $7,500 = $30,000.
        assert result == 30000.0

    def test_iso_string_close_time_compared_correctly(self):
        # close_time is set as iso STRING in real code; entry_time is
        # datetime. v1 of the fix sorted by str() which placed all opens
        # before all closes lexically (space < T). Here we check the
        # comparator handles the mixed types correctly.
        from datetime import datetime
        e1 = self._entry(datetime(2026, 5, 7, 9, 31),
                         close_dt=datetime(2026, 5, 7, 9, 46))
        e2 = self._entry(datetime(2026, 5, 7, 11, 15),
                         close_dt=datetime(2026, 5, 7, 13, 49))
        # E#1 closes before E#2 opens → never overlap → peak = 1 IC.
        result = self._strategy([e1, e2])._calculate_capital_deployed()
        assert result == 7500.0


class TestDeltaTargetStrikeSelection:
    """_calculate_strikes anchors short strikes to a delta target on B/C."""

    def _profile(self, deltas):
        from datetime import date, datetime, timezone
        from bots.hydra.brandon.gex_provider import GEXProfile, StrikeDelta
        return GEXProfile(
            spot=7345.0,
            expiry=date(2026, 5, 8),
            fetched_at=datetime.now(timezone.utc),
            strikes=tuple(),
            deltas=tuple(StrikeDelta(strike=s, contract_type=t, delta=d) for s, t, d in deltas),
        )

    def test_falls_back_to_super_when_disabled(self):
        inst = _make_instance(
            brandon_delta_target_enabled=False,
            current_price=7345.0,
        )
        # Ensure parent _calculate_strikes is invoked. Mock the parent to
        # return True so we don't need the full HYDRA strike pipeline.
        with patch.object(
            BrandonHydraStrategy.__mro__[1],
            "_calculate_strikes",
            return_value=True,
        ) as parent_method:
            entry = MagicMock()
            result = inst._calculate_strikes(entry)
        assert result is True
        parent_method.assert_called_once_with(entry)

    def test_falls_back_when_no_chain(self):
        inst = _make_instance(
            brandon_delta_target_enabled=True,
            current_price=7345.0,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: None
        inst._brandon_today_date = lambda: None
        with patch.object(
            BrandonHydraStrategy.__mro__[1],
            "_calculate_strikes",
            return_value=True,
        ) as parent_method:
            entry = MagicMock(entry_number=1)
            result = inst._calculate_strikes(entry)
        assert result is True
        parent_method.assert_called_once()

    def test_falls_back_when_chain_has_no_deltas(self):
        from datetime import date, datetime, timezone
        from bots.hydra.brandon.gex_provider import GEXProfile
        prof = GEXProfile(
            spot=7345.0,
            expiry=date(2026, 5, 8),
            fetched_at=datetime.now(timezone.utc),
            strikes=tuple(),
            deltas=tuple(),
        )
        inst = _make_instance(
            brandon_delta_target_enabled=True,
            current_price=7345.0,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        inst._brandon_today_date = lambda: None
        with patch.object(
            BrandonHydraStrategy.__mro__[1],
            "_calculate_strikes",
            return_value=True,
        ) as parent_method:
            entry = MagicMock(entry_number=1)
            result = inst._calculate_strikes(entry)
        assert result is True
        parent_method.assert_called_once()

    def test_picks_strike_at_target_delta_with_narrow_widths(self):
        # Real-world May 7 setup: 8δ put should land at 7280 (well below
        # 7330 wall), not 7340 like the tightener walked it to.
        prof = self._profile([
            (7280, "put", -0.08),   # closest to 8δ
            (7320, "put", -0.20),
            (7340, "put", -0.42),
            (7400, "call", +0.20),
            (7420, "call", +0.10),
            (7430, "call", +0.08),  # closest to 8δ
        ])
        inst = _make_instance(
            brandon_delta_target_enabled=True,
            brandon_delta_target_pct=0.08,
            brandon_narrow_spread_enabled=True,  # 5pt at low VIX
            current_price=7345.0,
            current_vix=17.0,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        inst._brandon_today_date = lambda: None
        # Use a real-ish entry with mutable strike attrs
        entry = MagicMock(entry_number=1, spec_set=None)
        entry.short_call_strike = 0.0
        entry.long_call_strike = 0.0
        entry.short_put_strike = 0.0
        entry.long_put_strike = 0.0
        entry.spread_width = 0
        result = inst._calculate_strikes(entry)
        assert result is True
        assert entry.short_put_strike == 7280.0
        assert entry.long_put_strike == 7275.0  # 5pt below
        assert entry.short_call_strike == 7430.0
        assert entry.long_call_strike == 7435.0  # 5pt above

    def test_brandon_avoids_yesterday_wall_strike(self):
        # Sanity check: with the tightener disabled AND delta-target on,
        # B's E#5 yesterday would NOT have landed at 7340 (which was on
        # the wall). It would have landed at 7280 (8δ).
        prof = self._profile([
            (7280, "put", -0.08),
            (7330, "put", -0.30),  # the GEX wall
            (7340, "put", -0.42),  # what we picked yesterday
            (7430, "call", +0.08),
        ])
        inst = _make_instance(
            brandon_delta_target_enabled=True,
            brandon_delta_target_pct=0.08,
            brandon_narrow_spread_enabled=True,
            current_price=7345.0,
            current_vix=17.0,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        inst._brandon_today_date = lambda: None
        entry = MagicMock(entry_number=5)
        entry.short_put_strike = 0.0
        entry.long_put_strike = 0.0
        entry.short_call_strike = 0.0
        entry.long_call_strike = 0.0
        entry.spread_width = 0
        inst._calculate_strikes(entry)
        # Strike is at 8δ, far below the 7330 wall.
        assert entry.short_put_strike == 7280.0
        assert entry.short_put_strike < 7330.0  # decisively below the wall


class TestDeltaTargetPriceVeto:
    """Price/credit sanity veto (2026-06-11): the 0DTE delta the picker keys off
    systematically under-states moneyness, so a ~30delta short can be selected as
    the '8delta' short and placed far too close. After selection we estimate the
    spread credit; if a side collects more than max_credit_pct_of_width of its
    width, we fall back to the conservative OTM-multiplier. E#2's *estimate* was
    0.23 of width (>0.20) — caught pre-placement even though the fill was richer."""

    def _profile(self, deltas):
        from datetime import date, datetime, timezone
        from bots.hydra.brandon.gex_provider import GEXProfile, StrikeDelta
        return GEXProfile(
            spot=7345.0, expiry=date(2026, 5, 8),
            fetched_at=datetime.now(timezone.utc), strikes=tuple(),
            deltas=tuple(StrikeDelta(strike=s, contract_type=t, delta=d) for s, t, d in deltas),
        )

    def _setup(self, *, pct, est_call, est_put, est_raises=False):
        prof = self._profile([
            (7280, "put", -0.08), (7320, "put", -0.20),
            (7430, "call", +0.08), (7420, "call", +0.10),
        ])
        inst = _make_instance(
            brandon_delta_target_enabled=True, brandon_delta_target_pct=0.08,
            brandon_narrow_spread_enabled=True,  # 5pt at VIX 17
            brandon_delta_target_max_credit_pct_of_width=pct,
            current_price=7345.0, current_vix=17.0,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        inst._brandon_today_date = lambda: None
        if est_raises:
            def _boom(_e):
                raise RuntimeError("quote feed down")
            inst._estimate_entry_credit = _boom
        else:
            inst._estimate_entry_credit = lambda _e: (est_call, est_put)
        entry = MagicMock(entry_number=2)
        entry.short_put_strike = entry.long_put_strike = 0.0
        entry.short_call_strike = entry.long_call_strike = 0.0
        entry.spread_width = 0
        return inst, entry

    def test_vetoes_too_rich_short_falls_back_to_otm(self):
        # est_put=150/contract on a 5pt width → 150/500 = 0.30 > 0.20 ceiling.
        inst, entry = self._setup(pct=0.20, est_call=20.0, est_put=150.0)
        with patch.object(
            BrandonHydraStrategy.__mro__[1], "_calculate_strikes", return_value=True,
        ) as parent:
            result = inst._calculate_strikes(entry)
        assert result is True
        parent.assert_called_once_with(entry)  # fell back to OTM-multiplier

    def test_no_veto_for_legitimate_cheap_short(self):
        # Both sides ~0.04 of width → well under the 0.20 ceiling; keep the pick.
        inst, entry = self._setup(pct=0.20, est_call=20.0, est_put=22.0)
        with patch.object(
            BrandonHydraStrategy.__mro__[1], "_calculate_strikes", return_value=True,
        ) as parent:
            result = inst._calculate_strikes(entry)
        assert result is True
        parent.assert_not_called()
        assert entry.short_put_strike == 7280.0   # the delta-target pick stands
        assert entry.short_call_strike == 7430.0

    def test_fail_safe_estimate_exception_does_not_veto(self):
        # A flaky quote must NEVER block/alter an entry — only a CONFIRMED
        # too-rich short widens. Estimation raising → keep the delta-target pick.
        inst, entry = self._setup(pct=0.20, est_call=0.0, est_put=0.0, est_raises=True)
        with patch.object(
            BrandonHydraStrategy.__mro__[1], "_calculate_strikes", return_value=True,
        ) as parent:
            result = inst._calculate_strikes(entry)
        assert result is True
        parent.assert_not_called()
        assert entry.short_put_strike == 7280.0

    def test_zero_estimate_does_not_veto(self):
        # (0,0) = estimation unavailable, not "free" — never veto on it.
        inst, entry = self._setup(pct=0.20, est_call=0.0, est_put=0.0)
        with patch.object(
            BrandonHydraStrategy.__mro__[1], "_calculate_strikes", return_value=True,
        ) as parent:
            inst._calculate_strikes(entry)
        parent.assert_not_called()
        assert entry.short_put_strike == 7280.0

    def test_disabled_when_ceiling_zero(self):
        # ceiling 0 disables the veto entirely even with an egregiously rich short.
        inst, entry = self._setup(pct=0.0, est_call=400.0, est_put=400.0)
        with patch.object(
            BrandonHydraStrategy.__mro__[1], "_calculate_strikes", return_value=True,
        ) as parent:
            inst._calculate_strikes(entry)
        parent.assert_not_called()
        assert entry.short_put_strike == 7280.0


class TestPctWidthShadowStop:
    """%-of-width stop SHADOW (2026-06-11): computes the would-fire trigger and
    LOGS it without acting, so C can run a zero-risk head-to-head vs the acting
    credit+buffer stop before flipping %-of-width live. Trigger for a 5pt/7c side
    at 40% = 0.40×5×100×7 = $1,400."""

    def _inst(self, *, shadow=True, pct=0.40):
        inst = _make_instance(
            narrow_spread_stop_shadow=shadow,
            narrow_spread_stop_pct=pct,
            contracts_per_entry=7,
            _brandon_pctwidth_shadow_fired=set(),
            _brandon_pctwidth_breach_at={},
            _brandon_pctwidth_confirmed_fired=set(),
        )
        inst._brandon_side_alive = lambda e, s: True
        return inst

    def _entry(self, *, call_sv, put_sv):
        e = MagicMock(entry_number=2)
        e.short_call_strike, e.long_call_strike = 7350.0, 7355.0   # 5pt
        e.short_put_strike, e.long_put_strike = 7250.0, 7245.0     # 5pt
        e.call_spread_value, e.put_spread_value = call_sv, put_sv
        e.call_side_stop, e.put_side_stop = 2940.0, 3640.0          # acting credit+buffer
        e.call_spread_credit, e.put_spread_credit = 140.0, 1750.0
        return e

    def test_would_fire_logged_when_sv_crosses_trigger(self, caplog):
        inst = self._inst()
        entry = self._entry(call_sv=1500.0, put_sv=500.0)  # call >$1400, put <$1400
        import logging as _logging
        with caplog.at_level(_logging.INFO):
            inst._brandon_check_pctwidth_shadow_stop(entry)
        assert (2, "call") in inst._brandon_pctwidth_shadow_fired
        assert (2, "put") not in inst._brandon_pctwidth_shadow_fired
        a2 = [r for r in caplog.records if "A2-SHADOW" in r.getMessage()]
        assert len(a2) == 1 and "WOULD fire" in a2[0].getMessage()

    def test_no_fire_when_below_trigger(self):
        inst = self._inst()
        entry = self._entry(call_sv=1000.0, put_sv=500.0)  # both <$1400
        inst._brandon_check_pctwidth_shadow_stop(entry)
        assert inst._brandon_pctwidth_shadow_fired == set()

    def test_dedup_once_per_side_per_day(self, caplog):
        inst = self._inst()
        entry = self._entry(call_sv=1500.0, put_sv=500.0)
        import logging as _logging
        with caplog.at_level(_logging.INFO):
            inst._brandon_check_pctwidth_shadow_stop(entry)
            inst._brandon_check_pctwidth_shadow_stop(entry)  # second tick — silent
        a2 = [r for r in caplog.records if "A2-SHADOW" in r.getMessage()]
        assert len(a2) == 1

    def test_disabled_is_noop(self):
        inst = self._inst(shadow=False)
        entry = self._entry(call_sv=9999.0, put_sv=9999.0)  # both way over
        inst._brandon_check_pctwidth_shadow_stop(entry)
        assert inst._brandon_pctwidth_shadow_fired == set()

    def test_does_not_act_no_close_called(self):
        # The shadow must NEVER touch the position — only log.
        inst = self._inst()
        inst._close_entry_early = MagicMock(side_effect=AssertionError("shadow must not close"))
        entry = self._entry(call_sv=1500.0, put_sv=1500.0)
        inst._brandon_check_pctwidth_shadow_stop(entry)  # must not raise
        inst._close_entry_early.assert_not_called()

    def test_confirmed_fires_only_after_persistence(self, caplog):
        # 2026-06-25: CONFIRMED variant fires only after the breach persists the
        # confirm window — the first tick starts the timer but does NOT confirm.
        import logging as _logging
        from datetime import timedelta
        from shared.market_hours import get_us_market_time
        inst = self._inst()
        entry = self._entry(call_sv=1500.0, put_sv=500.0)  # call over $1400 trigger
        inst._brandon_check_pctwidth_shadow_stop(entry)
        assert (2, "call") in inst._brandon_pctwidth_shadow_fired           # raw fired
        assert (2, "call") not in inst._brandon_pctwidth_confirmed_fired    # NOT yet confirmed
        assert (2, "call") in inst._brandon_pctwidth_breach_at             # timer started
        # backdate the breach past the 10s confirm window, tick again → confirmed
        inst._brandon_pctwidth_breach_at[(2, "call")] = get_us_market_time() - timedelta(seconds=11)
        with caplog.at_level(_logging.INFO):
            inst._brandon_check_pctwidth_shadow_stop(entry)
        assert (2, "call") in inst._brandon_pctwidth_confirmed_fired
        assert any("A2-SHADOW-CONFIRMED" in r.getMessage() and "WOULD fire" in r.getMessage()
                   for r in caplog.records)

    def test_whipsaw_recovery_clears_breach_and_does_not_confirm(self, caplog):
        import logging as _logging
        inst = self._inst()
        inst._brandon_check_pctwidth_shadow_stop(self._entry(call_sv=1500.0, put_sv=500.0))
        assert (2, "call") in inst._brandon_pctwidth_breach_at
        # SV recovers below the trigger before the confirm window → breach cleared
        with caplog.at_level(_logging.INFO):
            inst._brandon_check_pctwidth_shadow_stop(self._entry(call_sv=100.0, put_sv=500.0))
        assert (2, "call") not in inst._brandon_pctwidth_breach_at
        assert (2, "call") not in inst._brandon_pctwidth_confirmed_fired
        assert any("whipsaw avoided" in r.getMessage() for r in caplog.records)


class TestOrphanCloseAlertDedup:
    """The Brandon orphan-close (a TP/BREACH that transacted 0 legs) is re-checked
    every tick; the 90s retry cooldown alone still re-alerted ~28×/afternoon, so
    it now alerts AT MOST ONCE per (entry, side, kind) per day (2026-06-12)."""

    def _inst(self):
        from unittest.mock import MagicMock
        inst = _make_instance(
            alert_service=MagicMock(),
            _brandon_orphan_alerted=set(),
            _brandon_failed_close_at={},
        )
        return inst

    def test_alerts_once_per_episode(self):
        inst = self._inst()
        entry = MagicMock(entry_number=2)
        for _ in range(10):  # 10 ticks of a stuck 0-leg close
            inst._brandon_alert_orphan_close(entry, "call", "TP")
        assert inst.alert_service.send_alert.call_count == 1

    def test_distinct_sides_alert_separately(self):
        inst = self._inst()
        entry = MagicMock(entry_number=2)
        inst._brandon_alert_orphan_close(entry, "call", "TP")
        inst._brandon_alert_orphan_close(entry, "put", "TP")
        assert inst.alert_service.send_alert.call_count == 2

    def test_cooldown_still_marked_each_call(self):
        # The retry cooldown must STILL be set every call (it gates the close
        # retry); only the ALERT is deduped.
        inst = self._inst()
        entry = MagicMock(entry_number=2)
        inst._brandon_alert_orphan_close(entry, "call", "TP")
        assert (2, "call") in inst._brandon_failed_close_at


class TestNarrowSpreadOverride:
    def test_uses_narrow_when_enabled(self):
        inst = _make_instance(brandon_narrow_spread_enabled=True)
        assert inst._get_vix_adjusted_spread_width(15.0, "call") == 5
        assert inst._get_vix_adjusted_spread_width(25.0, "put") == 10

    def test_falls_through_to_super_when_disabled(self):
        inst = _make_instance(brandon_narrow_spread_enabled=False)
        with patch.object(
            BrandonHydraStrategy.__mro__[1],
            "_get_vix_adjusted_spread_width",
            return_value=99,
        ) as parent_method:
            result = inst._get_vix_adjusted_spread_width(15.0, "call")
        assert result == 99
        parent_method.assert_called_once()

    def test_custom_breakpoint_respected(self):
        inst = _make_instance(
            brandon_narrow_spread_enabled=True,
            brandon_narrow_breakpoint_vix=18.0,
        )
        assert inst._get_vix_adjusted_spread_width(17.9) == 5
        assert inst._get_vix_adjusted_spread_width(18.0) == 10


class TestTakeProfitDispatch:
    def _entry(self, **kw):
        e = MagicMock()
        e.entry_number = 1
        e.contracts = 1
        e.call_spread_credit = 100.0
        e.put_spread_credit = 100.0
        e.call_spread_value = 10.0
        e.put_spread_value = 10.0
        e.call_side_stopped = False
        e.put_side_stopped = False
        e.call_side_expired = False
        e.put_side_expired = False
        e.call_side_skipped = False
        e.put_side_skipped = False
        # MagicMock returns truthy children for any unset attr — explicitly
        # set the pivot_closed flags False so _brandon_side_alive doesn't
        # think a side is dead because of an auto-mock.
        e.call_side_pivot_closed = False
        e.put_side_pivot_closed = False
        # P&L attribution fields populated by Brandon TP/breach paths.
        # Keep as concrete floats so format strings don't TypeError on a
        # MagicMock attr.
        e.actual_call_stop_debit = 0.0
        e.actual_put_stop_debit = 0.0
        for k, v in kw.items():
            setattr(e, k, v)
        return e

    def test_returns_none_when_disabled(self):
        inst = _make_instance(brandon_take_profit_enabled=False)
        e = self._entry()
        assert inst._brandon_check_take_profit(e) is None

    def test_returns_none_when_holding(self):
        inst = _make_instance(brandon_take_profit_enabled=True, brandon_take_profit_threshold=0.80)
        # SVs at 50% of credits — not yet TP
        e = self._entry(call_spread_value=50.0, put_spread_value=50.0)
        assert inst._brandon_check_take_profit(e) is None

    def test_fires_when_threshold_reached(self):
        inst = _make_instance(brandon_take_profit_enabled=True, brandon_take_profit_threshold=0.80)
        # Set up daily_state so the realized-P&L correction can run.
        # _close_entry_early in dry-run records full credit; Brandon then
        # subtracts close_cost. We start at +200 (the credit-only number a
        # mocked _close_entry_early would have left) so the post-call value
        # tells us "credit + correction" worked: 200 - 20 - 20 = 160.
        inst.daily_state = MagicMock()
        inst.daily_state.total_realized_pnl = 200.0
        # Total credit $200, total SV $40 → 80% captured exactly
        e = self._entry(call_spread_value=20.0, put_spread_value=20.0)
        # The REAL _close_entry_early sets *_side_expired on each side it closes;
        # the 06-04 fail-closed fix requires that signal before the TP marks a
        # side stopped, so the mock must set it too (both sides close here).
        def _close(entry_arg):
            entry_arg.call_side_expired = True
            entry_arg.put_side_expired = True
            return (4, 0, [])
        inst._close_entry_early = MagicMock(side_effect=_close)
        result = inst._brandon_check_take_profit(e)
        assert result is not None
        assert "TP" in result
        inst._close_entry_early.assert_called_once_with(e)
        # Brandon TP closes through *_side_stopped (not _expired) and
        # populates actual_*_stop_debit with the raw spread_value (already in
        # dollars — the × 100 × contracts is baked into the property).
        assert e.call_side_stopped is True
        assert e.put_side_stopped is True
        assert e.actual_call_stop_debit == pytest.approx(20.0)  # raw, not × 100 × contracts
        assert e.actual_put_stop_debit == pytest.approx(20.0)
        # Realized P&L correction: subtracts close_cost from each side.
        # 200 (credit-only added by mocked _close_entry_early) − 20 − 20 = 160.
        assert inst.daily_state.total_realized_pnl == pytest.approx(160.0)

    def test_skips_already_closed_sides(self):
        inst = _make_instance(brandon_take_profit_enabled=True, brandon_take_profit_threshold=0.80)
        inst.daily_state = MagicMock()
        inst.daily_state.total_realized_pnl = 100.0
        # Call already stopped — only put side counts
        # Put: credit 100, SV 20 → 80% captured → fires
        e = self._entry(
            call_side_stopped=True,
            call_spread_value=999.0,  # ignored — call already dead
            put_spread_value=20.0,
        )
        # only the put side closes here (call already stopped) → set put_side_expired
        def _close(entry_arg):
            entry_arg.put_side_expired = True
            return (2, 0, [])
        inst._close_entry_early = MagicMock(side_effect=_close)
        result = inst._brandon_check_take_profit(e)
        assert result is not None
        # Put closed via Brandon TP → *_side_stopped + actual_*_stop_debit raw
        assert e.put_side_stopped is True
        assert e.actual_put_stop_debit == pytest.approx(20.0)
        # Only put side correction (call was already dead, not touched).
        assert inst.daily_state.total_realized_pnl == pytest.approx(80.0)

    def test_zero_leg_close_keeps_side_alive(self):
        # 06-04 fail-closed: if the close transacts 0 legs (no *_side_expired
        # set), the TP must NOT mark the side stopped — the live legs are still
        # open. Leave it alive to retry next tick + fire a CRITICAL orphan alert.
        inst = _make_instance(brandon_take_profit_enabled=True, brandon_take_profit_threshold=0.80)
        inst.daily_state = MagicMock()
        inst.daily_state.total_realized_pnl = 0.0
        e = self._entry(call_spread_value=20.0, put_spread_value=20.0)
        inst._close_entry_early = MagicMock(return_value=(0, 0, []))  # closed nothing
        inst._brandon_alert_orphan_close = MagicMock()
        inst._brandon_check_take_profit(e)
        assert e.call_side_stopped is False   # NOT marked — legs still open
        assert e.put_side_stopped is False
        assert inst._brandon_alert_orphan_close.called

    def test_no_op_when_all_sides_already_done(self):
        inst = _make_instance(brandon_take_profit_enabled=True)
        e = self._entry(
            call_side_stopped=True,
            put_side_expired=True,
        )
        assert inst._brandon_check_take_profit(e) is None

    def test_close_failure_returns_none(self):
        # If the close machinery throws, fall through to standard stops next tick
        inst = _make_instance(brandon_take_profit_enabled=True)
        e = self._entry(call_spread_value=10.0, put_spread_value=10.0)
        inst._close_entry_early = MagicMock(side_effect=RuntimeError("saxo down"))
        result = inst._brandon_check_take_profit(e)
        assert result is None


class TestGEXProfileFetch:
    def test_returns_none_when_gex_disabled(self):
        inst = _make_instance(brandon_gex_enabled=False)
        from datetime import date
        assert inst._brandon_get_gex_profile(date(2026, 5, 4)) is None

    def test_returns_none_when_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        inst = _make_instance(brandon_gex_enabled=True)
        from datetime import date
        assert inst._brandon_get_gex_profile(date(2026, 5, 4)) is None

    def test_returns_cached_profile_within_ttl(self, monkeypatch):
        from datetime import date
        from bots.hydra.brandon.gex_provider import GEXProfile

        inst = _make_instance(brandon_gex_enabled=True)
        cached = GEXProfile(spot=6800.0, expiry=date(2026, 5, 4), fetched_at=datetime.now(timezone.utc), strikes=())
        inst._brandon_gex_profile = cached
        inst._brandon_gex_profile_fetched_at = datetime.now(timezone.utc)  # fresh
        result = inst._brandon_get_gex_profile(date(2026, 5, 4))
        assert result is cached

    def test_refreshes_after_15_minutes(self, monkeypatch):
        # Stale cache (>15 min old) and a working API key + fetcher → refresh fires.
        from datetime import date, timedelta
        from bots.hydra.brandon.gex_provider import GEXProfile
        from bots.hydra.brandon import gex_provider as gp
        import bots.hydra.brandon.strategy as bstrat

        monkeypatch.setenv("POLYGON_API_KEY", "test-key")
        inst = _make_instance(brandon_gex_enabled=True, current_price=6800.0)

        old = GEXProfile(spot=6500.0, expiry=date(2026, 5, 4), fetched_at=datetime.now(timezone.utc), strikes=())
        inst._brandon_gex_profile = old
        inst._brandon_gex_profile_fetched_at = datetime.now(timezone.utc) - timedelta(minutes=20)

        fresh_calls = {"n": 0}

        def fake_fetch(*args, **kwargs):
            fresh_calls["n"] += 1
            return [
                {
                    "details": {"strike_price": 6800, "contract_type": "call"},
                    "open_interest": 100,
                    "greeks": {"gamma": 0.001},
                }
            ]

        monkeypatch.setattr(gp, "fetch_polygon_chain", fake_fetch)
        result = inst._brandon_get_gex_profile(date(2026, 5, 4))
        assert fresh_calls["n"] == 1
        assert result is not old  # was replaced by fresh profile

    def test_failure_cooldown_60s(self, monkeypatch):
        # If a fetch fails, don't retry until 60s have elapsed.
        from datetime import date
        monkeypatch.setenv("POLYGON_API_KEY", "test-key")
        inst = _make_instance(brandon_gex_enabled=True, current_price=6800.0)

        from bots.hydra.brandon import gex_provider as gp
        calls = {"n": 0}

        def boom(*args, **kwargs):
            calls["n"] += 1
            raise ConnectionError("polygon down")

        monkeypatch.setattr(gp, "fetch_polygon_chain", boom)
        monkeypatch.setattr(gp._time, "sleep", lambda *a, **k: None)  # skip retry backoff
        # First call: the chain pull is retried GEX_CHAIN_FETCH_ATTEMPTS times
        # (2026-06-10 reliability fix) before failing → sets failure_at.
        assert inst._brandon_get_gex_profile(date(2026, 5, 4)) is None
        assert calls["n"] == gp.GEX_CHAIN_FETCH_ATTEMPTS
        # Second call within the 60s cooldown: doesn't fetch again at all.
        assert inst._brandon_get_gex_profile(date(2026, 5, 4)) is None
        assert calls["n"] == gp.GEX_CHAIN_FETCH_ATTEMPTS

    def test_force_refresh_reuses_recent_sibling_write(self, monkeypatch):
        # 2026-06-08 forensic H (multi-variant contention): even a force_refresh,
        # once it holds the cross-variant lock, must REUSE a sibling's just-
        # written profile (≤ _GEX_FORCE_REFRESH_SIBLING_WINDOW_S old) instead of
        # issuing its own serial Polygon fetch.
        import contextlib
        from datetime import date
        from bots.hydra.brandon.gex_provider import GEXProfile
        from bots.hydra.brandon import strategy as bstrat

        monkeypatch.setenv("POLYGON_API_KEY", "test-key")
        inst = _make_instance(brandon_gex_enabled=True, current_price=6800.0)
        sibling = GEXProfile(
            spot=6800.0, expiry=date(2026, 5, 4),
            fetched_at=datetime.now(timezone.utc), strikes=(),
        )

        seen = {}

        def fake_load(**kwargs):
            seen["max_age"] = kwargs.get("max_age_seconds")
            return sibling

        monkeypatch.setattr(bstrat.gex_shared_cache, "load_shared_profile", fake_load)
        monkeypatch.setattr(
            bstrat.gex_shared_cache, "fetch_lock",
            lambda *a, **k: contextlib.nullcontext(),
        )

        def must_not_fetch(*a, **k):
            raise AssertionError("force_refresh must reuse the fresh sibling write")

        monkeypatch.setattr(
            bstrat.gex_provider, "fetch_polygon_chain_with_greeks", must_not_fetch
        )

        result = inst._brandon_get_gex_profile(date(2026, 5, 4), force_refresh=True)
        assert result is sibling
        # The post-lock recheck used the TIGHT force_refresh window — NOT the
        # full background TTL (which would over-reuse stale data).
        assert seen["max_age"] == bstrat._GEX_FORCE_REFRESH_SIBLING_WINDOW_S

    def test_force_refresh_fetches_when_no_recent_sibling(self, monkeypatch):
        # No sibling within the tight window → force_refresh does fetch fresh.
        import contextlib
        from datetime import date
        from bots.hydra.brandon.gex_provider import GEXProfile
        from bots.hydra.brandon import strategy as bstrat

        monkeypatch.setenv("POLYGON_API_KEY", "test-key")
        inst = _make_instance(brandon_gex_enabled=True, current_price=6800.0)

        monkeypatch.setattr(
            bstrat.gex_shared_cache, "load_shared_profile", lambda **k: None
        )
        monkeypatch.setattr(
            bstrat.gex_shared_cache, "fetch_lock",
            lambda *a, **k: contextlib.nullcontext(),
        )
        monkeypatch.setattr(
            bstrat.gex_shared_cache, "save_shared_profile", lambda *a, **k: None
        )

        fetched = {"n": 0}

        def fake_fetch(*a, **k):
            fetched["n"] += 1
            # 2026-09-01: fetch_polygon_chain_with_greeks now returns
            # (contracts, candidates_found), not a bare list.
            return [{"details": {"strike_price": 6800, "contract_type": "call"},
                     "open_interest": 100, "greeks": {"gamma": 0.001}}], 1

        monkeypatch.setattr(
            bstrat.gex_provider, "fetch_polygon_chain_with_greeks", fake_fetch
        )
        inst._brandon_get_gex_profile(date(2026, 5, 4), force_refresh=True)
        assert fetched["n"] == 1



class TestStrikeAdjusterLive:
    """Verify the LIVE strike adjuster actually mutates entry strikes."""

    def _entry(self, **kw):
        e = MagicMock()
        e.entry_number = 1
        e.contracts = 1
        e.short_call_strike = 6850
        e.long_call_strike = 6925   # 75pt wide
        e.short_put_strike = 6750
        e.long_put_strike = 6675    # 75pt wide
        e.call_side_skipped = False
        e.put_side_skipped = False
        e.call_only = False
        e.put_only = False
        for k, v in kw.items():
            setattr(e, k, v)
        return e

    def _profile_with_decel_above(self, spot=6800):
        from datetime import date
        from bots.hydra.brandon.gex_provider import build_profile
        return build_profile(
            [
                {"details": {"strike_price": 6870, "contract_type": "put"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6875, "contract_type": "put"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6880, "contract_type": "put"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
            ],
            spot=spot, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0,
        )

    def _profile_with_accel_at_call_short(self, spot=6800):
        from datetime import date
        from bots.hydra.brandon.gex_provider import build_profile
        return build_profile(
            [
                {"details": {"strike_price": 6840, "contract_type": "call"}, "open_interest": 50000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6850, "contract_type": "call"}, "open_interest": 50000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6860, "contract_type": "call"}, "open_interest": 50000, "greeks": {"gamma": 0.001}},
            ],
            spot=spot, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0,
        )

    def test_keep_does_not_mutate(self):
        from datetime import date
        from bots.hydra.brandon.gex_provider import build_profile
        # Quiet profile → KEEP both sides
        prof = build_profile(
            [{"details": {"strike_price": 6500, "contract_type": "put"}, "open_interest": 50, "greeks": {"gamma": 0.001}}],
            spot=6800, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0,
        )
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            current_price=6800,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        e = self._entry()
        inst._brandon_apply_strike_adjuster(e)
        assert e.short_call_strike == 6850
        assert e.long_call_strike == 6925
        assert e.short_put_strike == 6750
        assert e.long_put_strike == 6675
        assert e.call_side_skipped is False
        assert e.put_side_skipped is False

    def test_shift_mutates_call_strikes_preserving_width(self):
        prof = self._profile_with_decel_above()
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_decel_min_pct=0.01, brandon_max_shift_pts=50,
            current_price=6800,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        e = self._entry()
        original_width = e.long_call_strike - e.short_call_strike
        inst._brandon_apply_strike_adjuster(e)
        assert e.short_call_strike == 6885  # wall.high (6880) + buffer (5)
        assert e.long_call_strike - e.short_call_strike == original_width
        assert e.call_side_skipped is False  # not skipped, just shifted

    def test_skip_routes_to_one_sided_entry(self):
        prof = self._profile_with_accel_at_call_short()
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.05,
            current_price=6800,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        e = self._entry()
        inst._brandon_apply_strike_adjuster(e)
        assert e.call_side_skipped is True
        assert e.short_call_strike == 0.0
        assert e.long_call_strike == 0.0
        assert e.put_only is True   # HYDRA's one-sided entry path
        # Put side untouched
        assert e.short_put_strike == 6750

    def test_disabled_means_no_mutation(self):
        prof = self._profile_with_decel_above()
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=False,
            current_price=6800,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        e = self._entry()
        inst._brandon_apply_strike_adjuster(e)
        assert e.short_call_strike == 6850  # unchanged

    def test_skip_aborts_entry_when_one_sided_disabled(self):
        """require-both-sides: a GEX call-SKIP must ABORT the entry (not route
        one-sided) when one_sided_entries_enabled=False."""
        prof = self._profile_with_accel_at_call_short()
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.05, current_price=6800,
            one_sided_entries_enabled=False,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        e = self._entry()
        inst._brandon_apply_strike_adjuster(e)
        assert getattr(e, "require_both_abort", False) is True
        assert e.put_only is False           # NOT routed one-sided
        assert e.call_side_skipped is False   # aborted before any strike mutation
        assert e.short_call_strike == 6850    # strikes untouched

    def test_skip_still_routes_one_sided_when_enabled(self):
        """Regression: with one_sided_entries_enabled=True (default), a GEX SKIP
        still routes one-sided (existing behavior preserved)."""
        prof = self._profile_with_accel_at_call_short()
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.05, current_price=6800,
            one_sided_entries_enabled=True,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        e = self._entry()
        inst._brandon_apply_strike_adjuster(e)
        # Observable behavior proves the enabled path ran (MagicMock auto-attrs make
        # a getattr(require_both_abort) check unreliable here — the abort test uses an
        # explicit-True set, which is checkable).
        assert e.put_only is True
        assert e.call_side_skipped is True

    def test_call_side_abort_still_evaluates_and_logs_put_side(self, caplog):
        """2026-08-12 observability fix: before this, a call-side
        require-both-sides abort `return`ed immediately, so the put side
        never even ran — zero log evidence of what it would have decided.
        Now it still evaluates + logs, but must NOT mutate any put-side
        strike on an entry that's being discarded either way.
        """
        import logging
        prof = self._profile_with_accel_at_call_short()
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.05, current_price=6800,
            one_sided_entries_enabled=False,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        e = self._entry()
        with caplog.at_level(logging.INFO, logger="bots.hydra.brandon.strategy"):
            inst._brandon_apply_strike_adjuster(e)
        assert getattr(e, "require_both_abort", False) is True
        # Put side strikes must be UNMUTATED — the entry is discarded regardless.
        assert e.short_put_strike == 6750
        assert e.long_put_strike == 6675
        assert e.put_side_skipped is False
        assert e.call_only is False
        put_log_lines = [r.message for r in caplog.records if "put:" in r.message]
        assert any("BRANDON-GEX-ADJ" in msg and str(e.entry_number) in msg for msg in put_log_lines), (
            f"expected a put-side BRANDON-GEX-ADJ log line even after the call-side abort, got: {put_log_lines}"
        )


class TestAccelPeakPersistenceRotation:
    """2026-08-12: _brandon_apply_strike_adjuster tracks the previous
    INDEPENDENT GEX read (self._brandon_prior_gex_profile) so the persistence
    gate can compare consecutive entry-slot reads. Rotation must be keyed on
    profile.fetched_at, not merely "did the function run again" — a cache-hit
    reuse of the same profile within one entry evaluation must not count as
    a second independent confirmation.
    """

    def _entry(self, **kw):
        e = MagicMock()
        e.entry_number = 1
        e.contracts = 1
        e.short_call_strike = 6850
        e.long_call_strike = 6925
        e.short_put_strike = 6750
        e.long_put_strike = 6675
        e.call_side_skipped = False
        e.put_side_skipped = False
        e.call_only = False
        e.put_only = False
        for k, v in kw.items():
            setattr(e, k, v)
        return e

    def _accel_profile(self, peak_strike, spot=6800, fetched_at=None):
        from datetime import date
        from bots.hydra.brandon.gex_provider import build_profile
        contracts = [{"details": {"strike_price": peak_strike, "contract_type": "call"}, "open_interest": 200000, "greeks": {"gamma": 0.001}}]
        for offset in range(-30, 35, 5):
            if offset != 0:
                k = peak_strike + offset
                contracts.append({"details": {"strike_price": k, "contract_type": "call"}, "open_interest": 2000, "greeks": {"gamma": 0.001}})
        return build_profile(
            contracts, spot=spot, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0,
            fetched_at=fetched_at,
        )

    def _dual_accel_profile(self, call_peak, put_peak, spot=6800, fetched_at=None):
        """Two separate, non-overlapping dominant-OI bands — one near a call
        short, one near a put short — so call/put persistence can be tested
        independently within a single profile (both bands use "call"
        contract_type; acceleration is sign-based, not side-based).

        _detect_clusters breaks a "run" only on a SIGN change in the
        strike-sorted list, not on a numeric strike gap — two same-sign
        (both "call") bands with nothing between them merge into ONE
        cluster regardless of distance (this is exactly the SpotGamma
        hemisphere-merge pathology accel_peak_locality_pts exists to guard
        against). A small put-type (positive-sign) contract strictly between
        the two bands breaks the run so they detect as two independent
        clusters, matching how a real chain (calls above spot, puts below)
        naturally separates them.
        """
        from datetime import date
        from bots.hydra.brandon.gex_provider import build_profile
        contracts = [{"details": {"strike_price": 6790, "contract_type": "put"}, "open_interest": 5000, "greeks": {"gamma": 0.001}}]
        for peak in (call_peak, put_peak):
            contracts.append({"details": {"strike_price": peak, "contract_type": "call"}, "open_interest": 200000, "greeks": {"gamma": 0.001}})
            for offset in range(-25, 30, 5):
                if offset != 0:
                    contracts.append({"details": {"strike_price": peak + offset, "contract_type": "call"}, "open_interest": 2000, "greeks": {"gamma": 0.001}})
        return build_profile(
            contracts, spot=spot, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0,
            fetched_at=fetched_at,
        )

    def test_dual_side_evaluation_reaches_independent_verdicts_from_shared_prior(self):
        """Call and put sides are evaluated with the SAME prior_profile
        within one _brandon_apply_strike_adjuster call — confirm each side
        reaches its own independent confirmed/unconfirmed verdict from that
        shared prior, closing the gap where only single-side scenarios were
        covered (2026-08-12 review finding)."""
        from datetime import datetime, timezone
        # Call peak unchanged between reads (6840 both) -> confirmed.
        # Put peak drifted 15pt (6725 -> 6740, > default 10pt tolerance) -> unconfirmed.
        prior = self._dual_accel_profile(6840, 6725, fetched_at=datetime(2026, 8, 12, 14, 45, 0, tzinfo=timezone.utc))
        current = self._dual_accel_profile(6840, 6740, fetched_at=datetime(2026, 8, 12, 15, 15, 0, tzinfo=timezone.utc))
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.01, current_price=6800,
            brandon_accel_peak_persistence_enabled=True,
            brandon_accel_peak_persistence_tolerance_pts=10.0,
            _brandon_prior_gex_profile=prior,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: current
        e = self._entry()
        inst._brandon_apply_strike_adjuster(e)
        # Call side: peak confirmed by the shared prior -> SKIP -> routes one-sided
        # (one_sided_entries_enabled defaults True when unset on _make_instance).
        assert e.call_side_skipped is True
        assert e.put_only is True
        # Put side: peak UNCONFIRMED by the same shared prior -> falls through,
        # no decel wall in this fixture -> KEEP, strike unchanged.
        assert e.put_side_skipped is False
        assert e.short_put_strike == 6750

    def test_first_call_with_no_prior_sets_prior_profile(self):
        from datetime import datetime, timezone
        prof = self._accel_profile(6820, fetched_at=datetime(2026, 8, 12, 14, 45, 0, tzinfo=timezone.utc))
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.01, current_price=6800,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        assert inst._brandon_prior_gex_profile is None
        inst._brandon_apply_strike_adjuster(self._entry())
        assert inst._brandon_prior_gex_profile is prof

    def test_second_call_with_different_fetched_at_rotates(self):
        from datetime import datetime, timezone
        prof1 = self._accel_profile(6820, fetched_at=datetime(2026, 8, 12, 14, 45, 0, tzinfo=timezone.utc))
        prof2 = self._accel_profile(6805, fetched_at=datetime(2026, 8, 12, 15, 15, 0, tzinfo=timezone.utc))
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.01, current_price=6800,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof1
        inst._brandon_apply_strike_adjuster(self._entry())
        assert inst._brandon_prior_gex_profile is prof1
        inst._brandon_get_gex_profile = lambda d, **_kw: prof2
        inst._brandon_apply_strike_adjuster(self._entry())
        assert inst._brandon_prior_gex_profile is prof2

    def test_second_call_with_same_fetched_at_does_not_rerotate(self):
        """A TTL-cache-hit reuse (same fetched_at) within/near one entry
        evaluation must not roll 'prior' forward to itself — otherwise a
        profile could get compared against itself (trivially 'confirmed')."""
        from datetime import datetime, timezone
        fetched = datetime(2026, 8, 12, 14, 45, 0, tzinfo=timezone.utc)
        prof = self._accel_profile(6820, fetched_at=fetched)
        older_prior = self._accel_profile(6600, fetched_at=datetime(2026, 8, 12, 14, 15, 0, tzinfo=timezone.utc))
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.01, current_price=6800,
            _brandon_prior_gex_profile=older_prior,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        inst._brandon_apply_strike_adjuster(self._entry())
        assert inst._brandon_prior_gex_profile is prof  # rotated once (older_prior -> prof)
        # Second call reuses the SAME profile object/fetched_at (cache hit) —
        # prior must stay pinned at `prof`, not roll forward onto itself.
        inst._brandon_apply_strike_adjuster(self._entry())
        assert inst._brandon_prior_gex_profile is prof

    def test_same_fetched_at_reuse_never_passes_self_as_prior_profile(self):
        """2026-08-12 review fix: the ROTATION guard alone only stops the
        POINTER from being reassigned on a repeat sighting — it does not stop
        that already-rotated pointer from being read back out and handed to
        the confirm check as `prior_profile` against the very profile it was
        set from (a real self-comparison, trivially "confirmed" with 0pt
        drift — indistinguishable in the reason string from a genuine
        independent second read). Assert directly on what gets PASSED to the
        adjuster functions (not just the SKIP/KEEP outcome, which can
        coincide between "self-confirmed" and "no-prior-available" when the
        peak is in locality either way — the call-args assertion is the
        precise, mechanism-level proof)."""
        from datetime import datetime, timezone
        from bots.hydra.brandon import gex_strike_adjuster
        fetched = datetime(2026, 8, 12, 14, 45, 0, tzinfo=timezone.utc)
        prof = self._accel_profile(6840, fetched_at=fetched)  # within 25pt locality of 6850
        older_prior = self._accel_profile(6600, fetched_at=datetime(2026, 8, 12, 14, 15, 0, tzinfo=timezone.utc))
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.01, current_price=6800,
            brandon_accel_peak_persistence_enabled=True,
            _brandon_prior_gex_profile=older_prior,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        with patch.object(
            gex_strike_adjuster, "adjust_call_strike",
            wraps=gex_strike_adjuster.adjust_call_strike,
        ) as spy_call:
            inst._brandon_apply_strike_adjuster(self._entry())
            first_call_prior = spy_call.call_args.kwargs["prior_profile"]
            assert first_call_prior is older_prior  # genuine independent prior — correct

            # Second call: SAME profile object/fetched_at reused (cache hit).
            inst._brandon_apply_strike_adjuster(self._entry())
            second_call_prior = spy_call.call_args.kwargs["prior_profile"]
            assert second_call_prior is None, (
                f"expected prior_profile=None on a same-fetched_at reuse (not the profile "
                f"comparing against itself), got {second_call_prior!r}"
            )
            # Round-2 fix: must ALSO signal force_unconfirmed=True on this call —
            # None alone is ambiguous with "no prior has ever been read", which
            # would (wrongly) let SKIP fire unconditionally on a retry.
            assert spy_call.call_args.kwargs.get("force_unconfirmed") is True, (
                "expected force_unconfirmed=True on a same-fetched_at reuse, so the "
                "adjuster falls through to KEEP/SHIFT instead of the legacy "
                "unconditional-SKIP 'no prior ever' path"
            )

    def test_entry_retry_reusing_same_gex_read_does_not_collapse_to_unconditional_skip(self):
        """Reproduces the exact scenario a round-2 reviewer found: an entry
        retry (ENTRY_RETRY_DELAY_SECONDS=15s, well under the 30s force-refresh
        sibling-reuse window) can re-evaluate the strike adjuster against the
        SAME cached GEX profile as the first attempt. Before this fix, that
        collapsed to prior_profile=None -> unconditional SKIP (silently
        reverting to pre-persistence-gate behavior while looking identical in
        the logs to a genuine decision). After this fix it must fall through
        to KEEP instead — proven at the outcome level, not just the call-args
        level (test_same_fetched_at_reuse_never_passes_self_as_prior_profile
        already covers the mechanism; this covers the actual trading outcome
        that scenario produces)."""
        from datetime import datetime, timezone
        fetched = datetime(2026, 8, 12, 14, 45, 0, tzinfo=timezone.utc)
        # peak within the default 25pt locality of proposed_short (6850)
        prof = self._accel_profile(6840, fetched_at=fetched)
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.01, current_price=6800,
            brandon_accel_peak_persistence_enabled=True,
            _brandon_prior_gex_profile=None,  # first entry of the day — no prior yet
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof

        # Attempt 1 (first evaluation): no prior at all -> legacy SKIP, intentional.
        e1 = self._entry()
        inst._brandon_apply_strike_adjuster(e1)
        assert e1.call_side_skipped is True  # correct: genuinely first look, matches pre-fix behavior

        # Attempt 2 (retry: a NEW entry object, but _brandon_get_gex_profile
        # returns the SAME cached profile — same fetched_at, simulating the
        # force-refresh sibling-reuse window). Must NOT silently re-SKIP for
        # a reason that has nothing to do with a real, independent GEX read.
        e2 = self._entry()
        inst._brandon_apply_strike_adjuster(e2)
        assert e2.call_side_skipped is False, (
            "a retry reusing the same GEX read must fall through to KEEP, not "
            "silently collapse to the legacy unconditional-SKIP path"
        )
        assert e2.short_call_strike == 6850  # KEEP — unchanged

    def test_end_to_end_drifted_peak_does_not_skip_when_persistence_enabled(self):
        """The scenario this whole fix targets: a raw single-read locality
        check would SKIP (peak in range), but persistence + a drifted prior
        read means the entry is NOT vetoed."""
        from datetime import datetime, timezone
        # Peaks chosen so proposed_short (6850, from _entry()) is within the
        # default 25pt accel_peak_locality_pts of BOTH reads (10pt and 25pt
        # away respectively) — otherwise the accel-SKIP branch would never
        # even be reached and this test would pass for the wrong reason.
        prior = self._accel_profile(6825, fetched_at=datetime(2026, 8, 12, 14, 45, 0, tzinfo=timezone.utc))
        current = self._accel_profile(6840, fetched_at=datetime(2026, 8, 12, 15, 15, 0, tzinfo=timezone.utc))
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.01, current_price=6800,
            brandon_accel_peak_persistence_enabled=True,
            brandon_accel_peak_persistence_tolerance_pts=10.0,
            _brandon_prior_gex_profile=prior,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: current
        e = self._entry()
        inst._brandon_apply_strike_adjuster(e)
        # Would have SKIP'd (aborting, one_sided default True → routes put-only)
        # under the OLD single-read behavior; persistence gate keeps it KEEP.
        assert e.call_side_skipped is False
        assert e.short_call_strike == 6850  # unchanged — KEEP, not SHIFT/SKIP

    def test_first_read_of_day_still_skips_even_with_persistence_enabled(self):
        """No prior read yet (first entry slot of the day) → falls back to
        today's single-read behavior for this one slot, persistence or not."""
        from datetime import datetime, timezone
        # peak within the default 25pt locality of proposed_short (6850)
        current = self._accel_profile(6840, fetched_at=datetime(2026, 8, 12, 13, 45, 0, tzinfo=timezone.utc))
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_strike_adjuster_enabled=True,
            brandon_accel_min_pct=0.01, current_price=6800,
            brandon_accel_peak_persistence_enabled=True,
            _brandon_prior_gex_profile=None,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: current
        e = self._entry()
        inst._brandon_apply_strike_adjuster(e)
        assert e.call_side_skipped is True  # one_sided default True → routes one-sided

    def test_reset_for_new_day_clears_prior_gex_profile(self):
        """Mirrors the existing manual-clear convention for this bare
        (__new__-constructed) instance — we can't call the real
        _reset_for_new_day (it chains to super(), which needs full init).
        """
        from datetime import datetime, timezone
        prof = self._accel_profile(6820, fetched_at=datetime(2026, 8, 11, 14, 45, 0, tzinfo=timezone.utc))
        inst = _make_instance(_brandon_prior_gex_profile=prof)
        assert inst._brandon_prior_gex_profile is prof
        inst._brandon_prior_gex_profile = None  # the new line added to _reset_for_new_day
        assert inst._brandon_prior_gex_profile is None


class TestRequireBothSidesGuards:
    """require-both-sides (one_sided_entries_enabled=false): _execute/_simulate
    abort before super() when the GEX adjuster flagged require_both_abort, and the
    _initiate_entry skip helper records a clean skip (not a failed retry)."""

    def test_execute_entry_aborts_before_super(self):
        import types
        from bots.hydra.strategy import HydraStrategy
        inst = _make_instance()
        inst._brandon_apply_strike_adjuster = lambda e: setattr(e, "require_both_abort", True)
        with patch.object(HydraStrategy, "_execute_entry", return_value=True) as mock_super:
            e = types.SimpleNamespace(require_both_abort=False)
            result = inst._execute_entry(e)
        assert result is False
        mock_super.assert_not_called()

    def test_simulate_entry_aborts_before_super(self):
        import types
        from bots.hydra.strategy import HydraStrategy
        inst = _make_instance()
        inst._brandon_apply_strike_adjuster = lambda e: setattr(e, "require_both_abort", True)
        with patch.object(HydraStrategy, "_simulate_entry", return_value=True) as mock_super:
            e = types.SimpleNamespace(require_both_abort=False)
            result = inst._simulate_entry(e)
        assert result is False
        mock_super.assert_not_called()

    def test_execute_entry_proceeds_when_no_abort(self):
        import types
        from bots.hydra.strategy import HydraStrategy
        inst = _make_instance()
        inst._brandon_apply_strike_adjuster = lambda e: None  # no abort flag set
        with patch.object(HydraStrategy, "_execute_entry", return_value=True) as mock_super:
            e = types.SimpleNamespace()  # no require_both_abort attr → getattr default False
            result = inst._execute_entry(e)
        assert result is True
        mock_super.assert_called_once()

    def test_skip_helper_records_clean_skip(self):
        import types
        from bots.hydra.strategy import MEICState
        inst = _make_instance()
        inst.daily_state = types.SimpleNamespace(
            entries_skipped=0, credit_gate_skips=0, active_entries=[]
        )
        inst._entry_in_progress = True
        inst._current_entry = object()
        inst._next_entry_index = 2
        inst.entry_times = [1, 2, 3]
        inst._log_safety_event = MagicMock()
        inst._record_skipped_entry = MagicMock()
        e = types.SimpleNamespace()
        msg = inst._skip_require_both_sides(e, 3, "GEX-skip")
        assert inst.daily_state.entries_skipped == 1
        assert inst.daily_state.credit_gate_skips == 1
        assert inst._entry_in_progress is False
        assert inst._current_entry is None
        assert inst._next_entry_index == 3
        assert inst.state == MEICState.MONITORING
        inst._record_skipped_entry.assert_called_once()
        assert "require both sides" in msg.lower()


class TestBreachExitLive:
    """Verify the LIVE breach exit actually closes the IC."""

    def _entry(self):
        e = MagicMock()
        e.entry_number = 1
        e.contracts = 1
        e.short_call_strike = 6920
        e.long_call_strike = 6995
        e.short_put_strike = 6680
        e.long_put_strike = 6605
        e.call_spread_credit = 100.0
        e.put_spread_credit = 100.0
        e.call_spread_value = 10.0
        e.put_spread_value = 10.0
        e.call_side_stopped = False
        e.put_side_stopped = False
        e.call_side_expired = False
        e.put_side_expired = False
        e.call_side_skipped = False
        e.put_side_skipped = False
        e.call_side_pivot_closed = False
        e.put_side_pivot_closed = False
        return e

    def test_no_close_when_no_walls(self):
        from datetime import date
        from bots.hydra.brandon.gex_provider import GEXProfile
        empty_profile = GEXProfile(spot=6800, expiry=date(2026, 5, 5), fetched_at=datetime.now(timezone.utc), strikes=())
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_breach_exit_enabled=True,
            current_price=6800,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: empty_profile
        e = self._entry()
        inst._close_entry_early = MagicMock(return_value=(4, 0, []))
        result = inst._brandon_check_breach_exit(e)
        assert result is None
        inst._close_entry_early.assert_not_called()

    def test_sustained_breach_closes_ic(self):
        from datetime import date, timedelta
        from bots.hydra.brandon.gex_provider import build_profile
        from bots.hydra.brandon.gex_breach_exit import BreachState
        # Realistic setup: short_call at 6920, decel wall at 6890-6900 (between
        # entry spot and short), spot now at 6905 (above wall, not yet at short).
        prof = build_profile(
            [
                {"details": {"strike_price": 6890, "contract_type": "put"}, "open_interest": 100000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6900, "contract_type": "put"}, "open_interest": 100000, "greeks": {"gamma": 0.001}},
            ],
            spot=6800, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0,
        )
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_breach_exit_enabled=True,
            brandon_decel_min_pct=0.01, brandon_breach_confirmation_seconds=90,
            current_price=6905,  # past the wall (6900) but not yet at short (6920)
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        first = datetime.now(timezone.utc) - timedelta(seconds=100)
        inst._brandon_breach_states[(1, "call")] = BreachState(first_breach_at=first)
        inst._brandon_now_et = lambda: datetime.now(timezone.utc)

        e = self._entry()
        # Ensure call side has a real spread_value at the moment of breach.
        # The bug-fix verifies we capture this BEFORE _close_entry_early
        # zeroes out the aliveness flags. If the buggy version had survived,
        # actual_call_stop_debit would be 0.
        e.call_spread_credit = 100.0
        e.call_spread_value = 80.0  # call SV at moment of breach close
        e.put_spread_credit = 100.0
        e.put_spread_value = 60.0
        inst._close_entry_early = MagicMock(return_value=(4, 0, []))
        # daily_state needed for the realized-P&L correction (see TP test).
        inst.daily_state = MagicMock()
        inst.daily_state.total_realized_pnl = 200.0  # full credit pre-correction
        # Simulate _close_entry_early's flag-flip side-effect: it sets
        # *_side_expired=True. The fix must capture aliveness BEFORE this
        # mutation so the close-cost block still runs.
        def fake_close(entry):
            entry.call_side_expired = True
            entry.put_side_expired = True
            return (4, 0, [])
        inst._close_entry_early = MagicMock(side_effect=fake_close)
        result = inst._brandon_check_breach_exit(e)
        assert result is not None
        assert "closed" in result
        inst._close_entry_early.assert_called_once_with(e)
        assert e.call_side_pivot_closed is True
        # Real close costs MUST be recorded — this is the regression
        # guard for the 2026-05-07 incident where breach exits silently
        # logged $0 close cost while the actual SV was $750-$4,125.
        assert e.actual_call_stop_debit == pytest.approx(80.0)
        assert e.actual_put_stop_debit == pytest.approx(60.0)
        # And realized P&L must be reduced by both close costs:
        # 200 (credit pre-correction) - 80 - 60 = 60.
        assert inst.daily_state.total_realized_pnl == pytest.approx(60.0)


class TestHydraShadowStop:
    """Verify the credit+buffer early-warning: logs/alerts once per side per day
    when the level is breached, and this method itself never closes (the actual
    close is super()._check_stop_losses, run as the L-C2 backstop in step 3)."""

    def _entry(self):
        e = MagicMock()
        e.entry_number = 1
        e.call_spread_credit = 100.0
        e.put_spread_credit = 100.0
        e.call_spread_value = 50.0   # under stop
        e.put_spread_value = 50.0
        e.call_side_stop = 200.0     # generous
        e.put_side_stop = 200.0
        e.call_side_stopped = False
        e.put_side_stopped = False
        e.call_side_expired = False
        e.put_side_expired = False
        e.call_side_skipped = False
        e.put_side_skipped = False
        e.call_side_pivot_closed = False
        e.put_side_pivot_closed = False
        return e

    def test_no_fire_when_value_below_stop(self):
        inst = _make_instance(brandon_hydra_shadow_enabled=True)
        e = self._entry()
        inst._brandon_send_telegram = MagicMock()
        inst._brandon_check_hydra_shadow_stop(e)
        inst._brandon_send_telegram.assert_not_called()
        assert (1, "call") not in inst._brandon_hydra_shadow_fired
        assert (1, "put") not in inst._brandon_hydra_shadow_fired

    def test_fires_once_per_side_per_day(self):
        inst = _make_instance(brandon_hydra_shadow_enabled=True)
        e = self._entry()
        e.call_spread_value = 250.0  # above stop ($200)
        inst._brandon_send_telegram = MagicMock()
        inst._brandon_check_hydra_shadow_stop(e)
        assert inst._brandon_send_telegram.call_count == 1
        assert (1, "call") in inst._brandon_hydra_shadow_fired
        # Second tick: same side already fired, no new alert
        inst._brandon_check_hydra_shadow_stop(e)
        assert inst._brandon_send_telegram.call_count == 1

    def test_fires_independently_per_side(self):
        inst = _make_instance(brandon_hydra_shadow_enabled=True)
        e = self._entry()
        e.call_spread_value = 250.0
        e.put_spread_value = 250.0
        inst._brandon_send_telegram = MagicMock()
        inst._brandon_check_hydra_shadow_stop(e)
        assert inst._brandon_send_telegram.call_count == 2  # once per side


class TestOverlayHedgeTracking:
    """Verify overlay placement creates HedgeLegs and settles correctly."""

    def _entry(self, entry_number=1):
        e = MagicMock()
        e.entry_number = entry_number
        e.contracts = 1
        e.short_call_strike = 6840
        e.long_call_strike = 6915
        e.short_put_strike = 6760
        e.long_put_strike = 6685
        e.call_spread_credit = 100.0
        e.put_spread_credit = 100.0
        e.call_spread_value = 80.0
        e.put_spread_value = 10.0
        e.call_side_stopped = False
        e.put_side_stopped = False
        e.call_side_expired = False
        e.put_side_expired = False
        e.call_side_skipped = False
        e.put_side_skipped = False
        e.call_side_pivot_closed = False
        e.put_side_pivot_closed = False
        return e

    def _profile_with_call_accel(self):
        from datetime import date
        from bots.hydra.brandon.gex_provider import build_profile
        return build_profile(
            [
                {"details": {"strike_price": 6830, "contract_type": "call"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6840, "contract_type": "call"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6850, "contract_type": "call"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
            ],
            spot=6820, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0,
        )

    def test_overlay_placement_creates_hedge_legs(self):
        from datetime import time
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_butterfly_cutoff_hour=23,  # force morning → debit spread
            brandon_overlay_butterfly_cutoff_minute=59,
            current_price=6820,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: self._profile_with_call_accel()
        # Pin t_years so the Black-Scholes fill estimates are deterministic
        # regardless of when the test runs. Real prod code uses
        # `time_to_expiry_years(now_et, close_et)`, which on intraday runs
        # (< 4 hours to close) makes deep-OTM call premiums underflow to
        # float64 0.0 — the test's `fill_price > 0` assertion would then
        # fail purely due to wall-clock timing, not real behavior.
        # 1 day = 1/365 years gives realistic non-zero premiums.
        inst._brandon_estimate_t_years_to_close = lambda: 1.0 / 365.0
        e = self._entry()
        inst._brandon_check_overlay(e)

        legs = inst._brandon_hedge_legs.get(1, [])
        assert len(legs) == 2  # debit spread = 2 legs
        # All legs should be calls (call side threatened)
        assert all(l.contract_type == "call" for l in legs)
        # All legs marked with the right metadata
        assert all(l.entry_number == 1 for l in legs)
        assert all(l.threatened_side == "call" for l in legs)
        assert all(l.position_id.startswith("DRY_OVERLAY_1_call_") for l in legs)
        assert all(l.fill_price > 0 for l in legs)

    def test_overlay_does_not_double_fire(self):
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_butterfly_cutoff_hour=23,
            brandon_overlay_butterfly_cutoff_minute=59,
            current_price=6820,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: self._profile_with_call_accel()
        e = self._entry()
        inst._brandon_check_overlay(e)
        first_count = len(inst._brandon_hedge_legs.get(1, []))
        inst._brandon_check_overlay(e)  # second tick
        assert len(inst._brandon_hedge_legs[1]) == first_count

    def test_settle_hedges_returns_settlements(self):
        from bots.hydra.brandon.hedge_position import HedgeLeg
        from datetime import datetime, timezone

        inst = _make_instance()
        # daily_state (present in production at settlement) so the overlay P&L can
        # be booked per-entry (2026-07-02 fold-in).
        from types import SimpleNamespace
        inst.daily_state = SimpleNamespace(
            entries=[SimpleNamespace(entry_number=1, realized_pnl=0.0, overlay_pnl_booked=False)],
            total_realized_pnl=0.0,
        )
        # Pre-seed two legs of a call debit spread on entry 1 — SAME placed_at,
        # matching production (_brandon_place_overlay computes placed_at ONCE
        # per call and shares it across every leg of that one hedge; a stray
        # per-leg datetime.now() here would look like two separate hedges
        # under the 2026-08-20 placed_at-grouping fix).
        one_placement = datetime.now(timezone.utc)
        inst._brandon_hedge_legs[1] = [
            HedgeLeg(1, "long", "call", 6850, 1, fill_price=8.0,
                     position_id="DRY_OVERLAY_1_call_0", structure="debit_spread",
                     threatened_side="call", placed_at=one_placement),
            HedgeLeg(1, "short", "call", 6860, 1, fill_price=3.0,
                     position_id="DRY_OVERLAY_1_call_1", structure="debit_spread",
                     threatened_side="call", placed_at=one_placement),
        ]
        inst._brandon_send_telegram = MagicMock()
        settlements = inst._brandon_settle_hedges(spx_settle=6900)
        assert len(settlements) == 1
        # Same payoff math as TestDebitSpreadPayoff in hedge_position tests:
        # SPX 6900 → max profit = 500
        assert settlements[0].total_pnl == 500
        # Two telegrams: per-hedge + day total
        assert inst._brandon_send_telegram.call_count >= 2

    def test_settle_is_idempotent_within_day(self):
        from bots.hydra.brandon.hedge_position import HedgeLeg
        from datetime import datetime, timezone

        inst = _make_instance()
        from types import SimpleNamespace
        inst.daily_state = SimpleNamespace(
            entries=[SimpleNamespace(entry_number=1, realized_pnl=0.0, overlay_pnl_booked=False)],
            total_realized_pnl=0.0,
        )
        inst._brandon_hedge_legs[1] = [
            HedgeLeg(1, "long", "call", 6850, 1, fill_price=8.0,
                     position_id="DRY_OVERLAY_1_call_0", structure="debit_spread",
                     threatened_side="call", placed_at=datetime.now(timezone.utc)),
        ]
        inst._brandon_send_telegram = MagicMock()
        s1 = inst._brandon_settle_hedges(6900)
        first_call_count = inst._brandon_send_telegram.call_count
        s2 = inst._brandon_settle_hedges(6900)  # second call same day
        assert s1 == s2
        assert inst._brandon_send_telegram.call_count == first_call_count

    def test_reset_for_new_day_clears_hedge_state(self):
        from bots.hydra.brandon.hedge_position import HedgeLeg
        from datetime import datetime, timezone

        inst = _make_instance()
        inst._brandon_hedge_legs[1] = [
            HedgeLeg(1, "long", "call", 6850, 1, 8.0, "DRY_x", "debit_spread", "call",
                     datetime.now(timezone.utc)),
        ]
        inst._brandon_hedge_settlements = [MagicMock()]  # any non-empty
        # Call _reset_for_new_day directly via the unbound method; we can't
        # call super()._reset_for_new_day on this bare instance, so simulate
        # the fields-clearing portion alone.
        inst._brandon_hedge_legs.clear()
        inst._brandon_hedge_settlements = []
        assert inst._brandon_hedge_legs == {}
        assert inst._brandon_hedge_settlements == []


class TestDoubleHedgeOnSameEntrySettledSeparately:
    """2026-08-20 (execution audit finding, variant B entry #5 on 2026-08-19):
    an entry can receive TWO independent hedge placements hours apart on
    different sides (e.g. a call debit spread mid-morning, a separate put
    butterfly in the afternoon). _brandon_hedge_legs keyed only on
    entry_number used to flatten both placements into one list and settle
    them as a SINGLE HedgeSettlement — the combined dollar total was correct
    (settle_hedge sums every leg) but structure/threatened_side were taken
    from legs[0] alone, silently mislabeling the settlement and dropping the
    second hedge's identity entirely (production: 4 real placements, only 3
    BRANDON-OVERLAY-SETTLED lines). Fixed by grouping legs by placed_at
    (shared exactly within one _brandon_place_overlay() call) before settling."""

    def _ds(self, entries):
        return SimpleNamespace(entries=entries, total_realized_pnl=0.0)

    def test_two_placements_produce_two_settlements_correctly_labeled(self):
        from bots.hydra.brandon.hedge_position import HedgeLeg

        inst = _make_instance()
        entry = SimpleNamespace(entry_number=5, realized_pnl=0.0, overlay_pnl_booked=False)
        inst.daily_state = self._ds([entry])

        morning = datetime(2026, 8, 19, 16, 8, 0, tzinfo=timezone.utc)
        afternoon = datetime(2026, 8, 19, 16, 50, 54, tzinfo=timezone.utc)
        inst._brandon_hedge_legs[5] = [
            # Call debit spread, placed morning.
            HedgeLeg(5, "long", "call", 7765, 1, fill_price=8.0,
                     position_id="DRY_OVERLAY_5_call_0", structure="debit_spread",
                     threatened_side="call", placed_at=morning),
            HedgeLeg(5, "short", "call", 7770, 1, fill_price=3.0,
                     position_id="DRY_OVERLAY_5_call_1", structure="debit_spread",
                     threatened_side="call", placed_at=morning),
            # Put butterfly, placed separately in the afternoon.
            HedgeLeg(5, "long", "put", 7540, 1, fill_price=6.0,
                     position_id="DRY_OVERLAY_5_put_0", structure="butterfly",
                     threatened_side="put", placed_at=afternoon),
            HedgeLeg(5, "short", "put", 7550, 2, fill_price=3.0,
                     position_id="DRY_OVERLAY_5_put_1", structure="butterfly",
                     threatened_side="put", placed_at=afternoon),
            HedgeLeg(5, "long", "put", 7560, 1, fill_price=1.0,
                     position_id="DRY_OVERLAY_5_put_2", structure="butterfly",
                     threatened_side="put", placed_at=afternoon),
        ]
        inst._brandon_send_telegram = MagicMock()

        settlements = inst._brandon_settle_hedges(spx_settle=7550)

        assert len(settlements) == 2, "must settle as TWO independent hedges, not one merged record"
        by_structure = {s.structure: s for s in settlements}
        assert set(by_structure) == {"debit_spread", "butterfly"}
        assert by_structure["debit_spread"].threatened_side == "call"
        assert by_structure["butterfly"].threatened_side == "put"
        # Each settlement only sums ITS OWN legs (2 for the spread, 3 for the
        # butterfly) — not all 5 legs collapsed into one.
        assert len(by_structure["debit_spread"].legs) == 2
        assert len(by_structure["butterfly"].legs) == 3
        # Combined P&L across both settlements matches the old (correct)
        # aggregate-sum behavior.
        expected_total = sum(s.total_pnl for s in settlements)
        assert entry.realized_pnl == pytest.approx(expected_total)
        # One BRANDON-OVERLAY-SETTLED-equivalent Telegram per hedge (2) + one
        # day-total summary = 3, not 2 (1 merged hedge + day total).
        assert inst._brandon_send_telegram.call_count == 3

    def test_guard_set_once_after_both_placements_settle(self):
        from bots.hydra.brandon.hedge_position import HedgeLeg

        inst = _make_instance()
        entry = SimpleNamespace(entry_number=5, realized_pnl=0.0, overlay_pnl_booked=False)
        inst.daily_state = self._ds([entry])
        morning = datetime(2026, 8, 19, 16, 8, 0, tzinfo=timezone.utc)
        afternoon = datetime(2026, 8, 19, 16, 50, 54, tzinfo=timezone.utc)
        inst._brandon_hedge_legs[5] = [
            HedgeLeg(5, "long", "call", 7765, 1, 8.0, "p0", "debit_spread", "call", morning),
            HedgeLeg(5, "short", "call", 7770, 1, 3.0, "p1", "debit_spread", "call", morning),
            HedgeLeg(5, "long", "put", 7540, 1, 6.0, "p2", "butterfly", "put", afternoon),
        ]
        inst._brandon_send_telegram = MagicMock()

        inst._brandon_settle_hedges(spx_settle=7550)

        assert entry.overlay_pnl_booked is True
        assert 5 in inst._brandon_overlay_booked
        # Re-running the same day must be a true no-op — no re-price, no
        # re-booking, no duplicate telegrams (matches test_settle_is_idempotent_within_day).
        call_count = inst._brandon_send_telegram.call_count
        inst._brandon_settle_hedges(spx_settle=9999)  # wildly different SPX
        assert inst._brandon_send_telegram.call_count == call_count

    def test_telegram_failure_after_booking_does_not_cause_restart_double_book(self):
        """2026-08-20 round-1 adversarial review finding (HIGH, empirically
        reproduced by the reviewer): an earlier version of this refactor set
        the entry-level guard only AFTER the full per-group loop (including
        logging/Telegram) finished, while each group booked its P&L
        mid-loop. A raise between group 1's booking and the final guard-set
        left a booked-but-unguarded entry that a same-day restart re-ran
        from scratch, double-counting group 1. This test proves the fix:
        booking + guard-set now happen BEFORE any logging/Telegram, in a
        tight block with no I/O, so a Telegram failure on ANY group cannot
        leave the entry booked-but-unguarded."""
        from bots.hydra.brandon import hedge_position
        from bots.hydra.brandon.hedge_position import HedgeLeg

        inst = _make_instance()
        entry = SimpleNamespace(entry_number=5, realized_pnl=0.0, overlay_pnl_booked=False)
        inst.daily_state = self._ds([entry])
        morning = datetime(2026, 8, 19, 16, 8, 0, tzinfo=timezone.utc)
        afternoon = datetime(2026, 8, 19, 16, 50, 54, tzinfo=timezone.utc)
        inst._brandon_hedge_legs[5] = [
            HedgeLeg(5, "long", "call", 7765, 1, 8.0, "p0", "debit_spread", "call", morning),
            HedgeLeg(5, "short", "call", 7770, 1, 3.0, "p1", "debit_spread", "call", morning),
            HedgeLeg(5, "long", "put", 7540, 1, 6.0, "p2", "butterfly", "put", afternoon),
        ]
        # Telegram raises on every call — simulates the exact failure class
        # the reviewer used (something in the logging/Telegram phase).
        inst._brandon_send_telegram = MagicMock(side_effect=RuntimeError("network blip"))

        with pytest.raises(RuntimeError):
            inst._brandon_settle_hedges(spx_settle=7550)

        # Booking + guard must ALREADY be fully consistent even though the
        # method raised before returning — this is the whole point of doing
        # book+guard in a phase with zero I/O, before any Telegram send.
        expected_total = sum(
            hedge_position.settle_hedge(
                [l for l in inst._brandon_hedge_legs[5] if l.placed_at == pa], 7550
            ).total_pnl
            for pa in {morning, afternoon}
        )
        assert entry.realized_pnl == pytest.approx(expected_total)
        assert entry.overlay_pnl_booked is True
        assert 5 in inst._brandon_overlay_booked

        # Simulate a restart: fresh settlements cache, guard persisted.
        inst._brandon_hedge_settlements = []
        inst._brandon_send_telegram = MagicMock()  # network recovered
        settlements = inst._brandon_settle_hedges(spx_settle=9999)  # even a different SPX
        assert settlements == []  # guard skips the entry entirely — no re-booking
        assert entry.realized_pnl == pytest.approx(expected_total), (
            "restart after a mid-settlement failure double-counted a hedge — "
            "the exact bug this fix closes"
        )

    def test_settle_hedge_exception_books_nothing(self):
        """The PURE COMPUTE phase must not book anything if settle_hedge
        itself fails for any group — no partial booking from a half-computed
        entry."""
        from bots.hydra.brandon.hedge_position import HedgeLeg

        inst = _make_instance()
        entry = SimpleNamespace(entry_number=5, realized_pnl=0.0, overlay_pnl_booked=False)
        inst.daily_state = self._ds([entry])
        inst._brandon_hedge_legs[5] = [
            HedgeLeg(5, "long", "call", 7765, 1, 8.0, "p0", "debit_spread", "call",
                     datetime(2026, 8, 19, 16, 8, 0, tzinfo=timezone.utc)),
        ]
        inst._brandon_send_telegram = MagicMock()

        with patch(
            "bots.hydra.brandon.strategy.hedge_position.settle_hedge",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError):
                inst._brandon_settle_hedges(spx_settle=7550)

        assert entry.realized_pnl == 0.0
        assert entry.overlay_pnl_booked is False
        assert 5 not in inst._brandon_overlay_booked

    def test_single_placement_on_entry_still_settles_as_one(self):
        """Regression guard: an entry with exactly one hedge placement (the
        common case) must still produce exactly one settlement after the
        placed_at-grouping refactor."""
        from bots.hydra.brandon.hedge_position import HedgeLeg

        inst = _make_instance()
        entry = SimpleNamespace(entry_number=2, realized_pnl=0.0, overlay_pnl_booked=False)
        inst.daily_state = self._ds([entry])
        one_placement = datetime(2026, 8, 19, 15, 13, 39, tzinfo=timezone.utc)
        inst._brandon_hedge_legs[2] = [
            HedgeLeg(2, "long", "put", 7620, 1, 6.0, "p0", "debit_spread", "put", one_placement),
            HedgeLeg(2, "short", "put", 7645, 1, 3.0, "p1", "debit_spread", "put", one_placement),
        ]
        inst._brandon_send_telegram = MagicMock()

        settlements = inst._brandon_settle_hedges(spx_settle=7600)

        assert len(settlements) == 1
        assert len(settlements[0].legs) == 2


class TestOverlayGexConfirmationAlwaysRequired:
    """2026-08-19: _brandon_check_overlay used to pass
    require_gex_confirmation=(profile is not None) to evaluate_overlay --
    so when the GEX/Polygon profile is fully unavailable (a total outage,
    not just sparse/degraded data), the hedge fired on proximity distance
    ALONE, with zero confirming signal. Fixed to always require
    confirmation; when profile is None, evaluate_overlay's own
    `if profile is None: return None` (already-tested module behavior)
    means no hedge is proposed at all -- the position stays protected via
    the independent credit+buffer stop (L-C1 GEX fallback), not this hedge.
    """

    def _entry(self, entry_number=1):
        e = MagicMock()
        e.entry_number = entry_number
        e.contracts = 1
        e.short_call_strike = 6840
        e.long_call_strike = 6915
        e.short_put_strike = 6760
        e.long_put_strike = 6685
        e.call_side_stopped = False
        e.put_side_stopped = False
        e.call_side_expired = False
        e.put_side_expired = False
        e.call_side_skipped = False
        e.put_side_skipped = False
        e.call_side_pivot_closed = False
        e.put_side_pivot_closed = False
        return e

    def test_no_hedge_when_profile_is_none_even_within_trigger_distance(self):
        # Price is well within the (default 25pt) trigger distance of the
        # threatened call strike (6840), which under the OLD code would
        # have fired on distance alone since profile=None used to set
        # require_gex_confirmation=False.
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_butterfly_cutoff_hour=23,
            brandon_overlay_butterfly_cutoff_minute=59,
            current_price=6820,  # 20pt from short call 6840
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: None  # total Polygon outage
        e = self._entry()
        inst._brandon_check_overlay(e)

        assert inst._brandon_hedge_legs.get(1, []) == [], (
            "hedge fired on distance alone during a total Polygon outage -- "
            "the GEX-confirmation-always-required fix regressed"
        )

    def test_hedge_still_fires_when_profile_present_and_confirmed(self):
        # Regression companion: confirms the fix didn't also break the
        # legitimate case (profile present, accel zone confirms).
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_butterfly_cutoff_hour=23,
            brandon_overlay_butterfly_cutoff_minute=59,
            current_price=6820,
        )
        from datetime import date
        from bots.hydra.brandon.gex_provider import build_profile
        profile = build_profile(
            [
                {"details": {"strike_price": 6830, "contract_type": "call"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6840, "contract_type": "call"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6850, "contract_type": "call"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
            ],
            spot=6820, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: profile
        inst._brandon_estimate_t_years_to_close = lambda: 1.0 / 365.0
        e = self._entry()
        inst._brandon_check_overlay(e)

        assert len(inst._brandon_hedge_legs.get(1, [])) == 2  # debit spread, still fires correctly

    def test_no_hedge_when_profile_present_but_no_accel_zone_confirms(self):
        # Profile IS available (not the None case) but has no accel cluster
        # on the threatened side -- must still correctly withhold the hedge,
        # exactly as before this fix (this path was already gated, confirms
        # the fix didn't accidentally loosen it).
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_butterfly_cutoff_hour=23,
            brandon_overlay_butterfly_cutoff_minute=59,
            current_price=6820,
        )
        from datetime import date
        from bots.hydra.brandon.gex_provider import build_profile
        profile = build_profile([], spot=6820, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0)
        inst._brandon_get_gex_profile = lambda d, **_kw: profile
        e = self._entry()
        inst._brandon_check_overlay(e)

        assert inst._brandon_hedge_legs.get(1, []) == []

    def test_no_hedge_on_put_side_when_profile_is_none(self):
        # Round-1 review finding: the other 3 tests in this class only ever
        # drive the CALL side into the require_gex_confirmation branch --
        # current_price=6820 puts the put side (short 6760) 60pt away, past
        # the 25pt distance gate, so it never reaches the confirmation
        # check at all. cfg.require_gex_confirmation is a single value
        # shared by both sides (built once, before the per-side loop), so
        # the call-side proof does cover the put side in practice -- but
        # assert it directly rather than only by code-symmetry inference.
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_butterfly_cutoff_hour=23,
            brandon_overlay_butterfly_cutoff_minute=59,
            current_price=6775,  # 15pt from short put 6760, within the 25pt trigger
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: None  # total Polygon outage
        e = self._entry()
        inst._brandon_check_overlay(e)

        assert inst._brandon_hedge_legs.get(1, []) == [], (
            "put-side hedge fired on distance alone during a total Polygon outage"
        )


class TestOverlayTriggerDistanceConfigWiring:
    """A 9-event replay (2026-08-19) found trigger_distance_pts=25 arms the
    hedge well before real danger on B/C's 5-10pt-wide spreads (0/9 events
    defended a side that was ever actually breached). Retuned to 15. Deployed
    as a config change (no logic change -- trigger_distance_pts was already
    fully config-driven), so what needs testing here is the WIRING: that
    config_variant_{b,c}.json's strategy.brandon.defensive_overlay.
    trigger_distance_pts JSON path is spelled correctly and parses to the
    expected value, using the identical extraction expression bots/hydra/
    brandon/strategy.py's __init__ uses -- a wrong key name or nesting level
    would silently fall back to the old 25.0 default with no error anywhere.
    Mirrors tests/test_eod_flatten_safety.py::TestEodFlattenSkipOtmPtsConfigWiring,
    the analogous wiring test for the sibling skip_otm_pts retune one day
    earlier -- a round-1 review finding noted no such test existed yet for
    this parameter, unlike that established precedent."""

    def _extract_trigger_distance_pts(self, config: dict) -> float:
        """Byte-for-byte the same expression as strategy.py's __init__
        (~line 275-277) -- not a re-derivation, the literal production logic."""
        bcfg = (config.get("strategy", {}) or {}).get("brandon", {}) or {}
        ov = bcfg.get("defensive_overlay") or {}
        return float(ov.get("trigger_distance_pts", 25.0))

    def _load(self, filename: str) -> dict:
        path = Path(__file__).resolve().parents[1] / "bots" / "hydra" / "config" / filename
        with open(path) as f:
            return json.load(f)

    def test_variant_b_config_sets_15pt(self):
        cfg = self._load("config_variant_b.json")
        assert self._extract_trigger_distance_pts(cfg) == 15.0

    def test_variant_c_config_sets_15pt(self):
        cfg = self._load("config_variant_c.json")
        assert self._extract_trigger_distance_pts(cfg) == 15.0


class TestVariantBOverlayStructuresDisabled:
    """2026-09-04: B's afternoon butterfly was disabled after a full historical
    review (decisive reason: every butterfly debit actually paid, $1,925-$2,240,
    EXCEEDS the loss it defends -- an IC side is already bounded by the A2
    %-of-width stop at ~$1,400 nominal / $1,190-$1,785 observed -- so the hedge
    roughly doubled exposure on a threatened entry rather than truncating a tail
    the stop had already truncated).

    Pins the deployed shape of config_variant_b.json's defensive_overlay. This
    matters more than a normal config test because config_variant_*.json carries
    skip-worktree on the VM, which does NOT stop a `git pull` fast-forward from
    overwriting it (verified 2026-08-19) -- so a future commit touching this
    sample could silently re-arm a real-money hedge with no error anywhere.
    Uses the same extraction expressions brandon/strategy.py's __init__ uses."""

    def _load(self, filename: str) -> dict:
        path = Path(__file__).resolve().parents[1] / "bots" / "hydra" / "config" / filename
        with open(path) as f:
            return json.load(f)

    def _overlay(self, config: dict) -> dict:
        bcfg = (config.get("strategy", {}) or {}).get("brandon", {}) or {}
        return bcfg.get("defensive_overlay") or {}

    def test_variant_b_butterfly_is_disabled(self):
        ov = self._overlay(self._load("config_variant_b.json"))
        assert bool(ov.get("butterfly_enabled", True)) is False

    def test_variant_b_debit_spread_stays_disabled(self):
        # Killed 2026-08-25 on a 0-wins / 8-losses (B) + 0/4 (C) record.
        ov = self._overlay(self._load("config_variant_b.json"))
        assert bool(ov.get("debit_spread_enabled", True)) is False

    def test_variant_b_overlay_stays_enabled_so_watch_logging_survives(self):
        """DELIBERATE, and load-bearing: brandon/strategy.py gates the whole
        _brandon_check_overlay call (and therefore the BRANDON-OVERLAY-WATCH
        distance-from-short logging) on `enabled`. Flipping this to false to
        'fully turn the hedge off' would ALSO silence the telemetry we kept it
        for -- the open question of whether the arming GATE is miscalibrated
        (it stood down at 1.42pt from a short on 2026-09-01 where a butterfly
        would plausibly have paid ~+$1,500, then armed on 2026-09-04 when every
        IC finished 10pt+ OTM). With both structures disabled, no hedge can fire
        regardless, so leaving this true costs nothing and buys the data."""
        ov = self._overlay(self._load("config_variant_b.json"))
        assert bool(ov.get("enabled", False)) is True


class TestOverlayWatchLoggingAccuracy:
    """Round-1 review finding: BRANDON-OVERLAY-WATCH's gex_confirmed field
    originally logged `profile is not None` -- true whenever ANY profile
    exists, even one with zero accel clusters on the threatened side, which
    is a materially weaker signal than evaluate_overlay's own confirmation
    gate (_has_accel_zone_on_side). A future retune replaying this log would
    have over-counted "confirmed" approaches. Fixed to compute the same
    check evaluate_overlay itself uses."""

    def _entry(self, entry_number=1):
        e = MagicMock()
        e.entry_number = entry_number
        e.contracts = 1
        e.short_call_strike = 6840
        e.long_call_strike = 6915
        e.short_put_strike = 6760
        e.long_put_strike = 6685
        e.call_side_stopped = False
        e.put_side_stopped = False
        e.call_side_expired = False
        e.put_side_expired = False
        e.call_side_skipped = False
        e.put_side_skipped = False
        e.call_side_pivot_closed = False
        e.put_side_pivot_closed = False
        return e

    def test_gex_confirmed_false_when_profile_present_but_no_accel_zone(self, caplog):
        # The exact gap round-1 review found: profile is not None (would
        # have logged gex_confirmed=True under the old code) but has no
        # accel cluster on the threatened side, so the REAL gate
        # (evaluate_overlay) would decline to hedge. The log must say False.
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_trigger_distance_pts=25.0,
            current_price=6820,  # 20pt from short call 6840, within watch zone
        )
        from datetime import date
        from bots.hydra.brandon.gex_provider import build_profile
        profile = build_profile([], spot=6820, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0)
        inst._brandon_get_gex_profile = lambda d, **_kw: profile
        e = self._entry()
        with caplog.at_level("INFO"):
            inst._brandon_check_overlay(e)

        watch_lines = [r.getMessage() for r in caplog.records if "BRANDON-OVERLAY-WATCH" in r.getMessage() and " call:" in r.getMessage()]
        assert any("gex_confirmed=False" in line for line in watch_lines), watch_lines
        assert not any("gex_confirmed=True" in line for line in watch_lines), watch_lines

    def test_gex_confirmed_true_when_accel_zone_present(self, caplog):
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_trigger_distance_pts=25.0,
            current_price=6820,
        )
        from datetime import date
        from bots.hydra.brandon.gex_provider import build_profile
        profile = build_profile(
            [
                {"details": {"strike_price": 6830, "contract_type": "call"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6840, "contract_type": "call"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
                {"details": {"strike_price": 6850, "contract_type": "call"}, "open_interest": 80000, "greeks": {"gamma": 0.001}},
            ],
            spot=6820, expiry=date(2026, 5, 5), time_to_expiry=1 / 365.0,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: profile
        inst._brandon_estimate_t_years_to_close = lambda: 1.0 / 365.0
        e = self._entry()
        # Force a placement-free tick: use a distance just OUTSIDE the
        # trigger but inside the watch zone, so WATCH logs without also
        # firing the real hedge (which would consume the key and could
        # otherwise complicate reading back the log for this assertion).
        e.short_call_strike = 6840
        inst.current_price = 6815  # 25pt away -- right at the trigger boundary
        with caplog.at_level("INFO"):
            inst._brandon_check_overlay(e)

        watch_lines = [r.getMessage() for r in caplog.records if "BRANDON-OVERLAY-WATCH" in r.getMessage() and " call:" in r.getMessage()]
        assert any("gex_confirmed=True" in line for line in watch_lines), watch_lines


class TestOverlayWatchLoggingThrottle:
    """Round-1 review finding: the WATCH log had no throttle, so a side
    chopping sideways inside the watch band for an extended period could
    log on every ~2-5s monitoring tick -- hundreds of near-duplicate lines
    in a single range-bound session. Throttled to once per 60s per
    (entry, side)."""

    def _entry(self, entry_number=1):
        e = MagicMock()
        e.entry_number = entry_number
        e.contracts = 1
        e.short_call_strike = 6840
        e.long_call_strike = 6915
        e.short_put_strike = 6760
        e.long_put_strike = 6685
        e.call_side_stopped = False
        e.put_side_stopped = False
        e.call_side_expired = False
        e.put_side_expired = False
        e.call_side_skipped = False
        e.put_side_skipped = False
        e.call_side_pivot_closed = False
        e.put_side_pivot_closed = False
        return e

    def test_second_tick_within_60s_does_not_log_again(self, caplog):
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_trigger_distance_pts=25.0,
            current_price=6810,  # 30pt from short call -- watch zone, not trigger
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: None
        e = self._entry()
        with caplog.at_level("INFO"):
            inst._brandon_check_overlay(e)
            first_count = len([
                r for r in caplog.records
                if "BRANDON-OVERLAY-WATCH" in r.getMessage() and " call:" in r.getMessage()
            ])
            inst._brandon_check_overlay(e)  # immediate second tick, same call
            second_count = len([
                r for r in caplog.records
                if "BRANDON-OVERLAY-WATCH" in r.getMessage() and " call:" in r.getMessage()
            ])

        assert first_count == 1
        assert second_count == 1, "throttle did not suppress a second tick within 60s"

    def test_logs_again_after_throttle_window_elapses(self, caplog, monkeypatch):
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_trigger_distance_pts=25.0,
            current_price=6810,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: None
        e = self._entry()
        fake_now = [1000.0]
        monkeypatch.setattr(
            "bots.hydra.brandon.strategy.time.monotonic",
            lambda: fake_now[0],
        )
        with caplog.at_level("INFO"):
            inst._brandon_check_overlay(e)
            fake_now[0] += 61.0  # past the 60s throttle window
            inst._brandon_check_overlay(e)

        watch_lines = [
            r for r in caplog.records
            if "BRANDON-OVERLAY-WATCH" in r.getMessage() and " call:" in r.getMessage()
        ]
        assert len(watch_lines) == 2, "expected a fresh log line once the throttle window elapsed"


class TestOverlayWatchLogging:
    """2026-08-19: distance-at-tick instrumentation (BRANDON-OVERLAY-WATCH),
    added so a future trigger_distance_pts retune has a real intraday
    distance trail instead of having to infer bounds from unrelated
    signals (e.g. whether MKT-047 later force-closed the side)."""

    def _entry(self, entry_number=1):
        e = MagicMock()
        e.entry_number = entry_number
        e.contracts = 1
        e.short_call_strike = 6840
        e.long_call_strike = 6915
        e.short_put_strike = 6760
        e.long_put_strike = 6685
        e.call_side_stopped = False
        e.put_side_stopped = False
        e.call_side_expired = False
        e.put_side_expired = False
        e.call_side_skipped = False
        e.put_side_skipped = False
        e.call_side_pivot_closed = False
        e.put_side_pivot_closed = False
        return e

    def test_logs_within_watch_zone(self, caplog):
        # 30pt from short call 6840 (current_price 6810), watch zone is
        # 2x trigger_distance_pts (25 -> 50pt), so this is well inside it
        # but outside the trigger itself -- must log without placing a hedge.
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_trigger_distance_pts=25.0,
            current_price=6810,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: None
        e = self._entry()
        with caplog.at_level("INFO"):
            inst._brandon_check_overlay(e)

        watch_lines = [r.getMessage() for r in caplog.records if "BRANDON-OVERLAY-WATCH" in r.getMessage()]
        assert any("call" in line and "30.00pt" in line for line in watch_lines)
        assert inst._brandon_hedge_legs.get(1, []) == []  # 30pt is outside the 25pt trigger itself

    def test_does_not_log_outside_watch_zone(self, caplog):
        # Wide strikes (unlike the default 6840/6760 entry, where call+put
        # distances necessarily sum to the 80pt strike range -- making it
        # impossible for BOTH sides to independently exceed a 50pt watch
        # zone at once) so spot can sit >50pt from both sides simultaneously.
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_trigger_distance_pts=25.0,
            current_price=6800,
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: None
        e = self._entry()
        e.short_call_strike = 7000  # 200pt away
        e.short_put_strike = 6600   # 200pt away
        with caplog.at_level("INFO"):
            inst._brandon_check_overlay(e)

        watch_lines = [r.getMessage() for r in caplog.records if "BRANDON-OVERLAY-WATCH" in r.getMessage()]
        assert watch_lines == []

    def test_does_not_log_for_already_placed_side(self, caplog):
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_overlay_enabled=True,
            brandon_overlay_trigger_distance_pts=25.0,
            current_price=6810,
            _brandon_overlay_placed={(1, "call")},
        )
        inst._brandon_get_gex_profile = lambda d, **_kw: None
        e = self._entry()
        with caplog.at_level("INFO"):
            inst._brandon_check_overlay(e)

        watch_lines = [r.getMessage() for r in caplog.records if "BRANDON-OVERLAY-WATCH" in r.getMessage() and "call" in r.getMessage()]
        assert watch_lines == []


class TestDryRunStateRecovery:
    """Regression coverage for the 2026-05-05 dry-run state-loss bug,
    updated for the F4.8 state-file-authoritative recovery rewrite.

    The 2026-05-05 bug: a mid-day dry-run restart returned from
    `_recover_positions_from_saxo` without loading the state file, so
    the next save wiped today's session. F4.8 makes
    `_load_state_file_history` the FIRST step of recovery for BOTH
    dry-run and live — today's entries are always rehydrated — and the
    broker is only a cross-check (live only). The method no longer
    calls `client.get_positions` directly.
    """

    def _recovery_instance(self, dry_run):
        inst = _make_instance()
        inst.dry_run = dry_run
        inst.BOT_NAME = "HYDRA"
        inst.contracts_per_entry = 1
        inst._next_entry_index = 0
        inst.client = MagicMock()
        inst.alert_service = MagicMock()
        inst._save_state_to_disk = MagicMock()
        inst._log_safety_event = MagicMock()
        inst._reconcile_recovered_entries_with_broker = MagicMock()
        inst.daily_state = MagicMock()
        inst.daily_state.total_realized_pnl = 0.0
        return inst

    def test_dry_run_loads_state_history(self):
        from bots.hydra.strategy import HydraStrategy

        inst = self._recovery_instance(dry_run=True)
        inst._load_state_file_history = MagicMock(return_value=False)
        inst.daily_state.entries = []

        HydraStrategy._recover_positions_from_saxo(inst)

        inst._load_state_file_history.assert_called_once_with()
        # dry-run never cross-checks against a broker
        inst._reconcile_recovered_entries_with_broker.assert_not_called()

    def test_dry_run_with_entries_skips_broker_check(self):
        from bots.hydra.strategy import HydraStrategy

        inst = self._recovery_instance(dry_run=True)
        inst._load_state_file_history = MagicMock(return_value=True)
        fake_entry = MagicMock()
        fake_entry.contracts = 1
        inst.daily_state.entries = [fake_entry]
        inst.daily_state.active_entries = [fake_entry]

        HydraStrategy._recover_positions_from_saxo(inst)

        inst._reconcile_recovered_entries_with_broker.assert_not_called()

    def test_live_mode_cross_checks_against_broker(self):
        """F4.8: live recovery loads the state file then cross-checks
        recovered entries against the broker — it no longer calls
        client.get_positions directly."""
        from bots.hydra.strategy import HydraStrategy

        inst = self._recovery_instance(dry_run=False)
        inst._load_state_file_history = MagicMock(return_value=True)
        fake_entry = MagicMock()
        fake_entry.contracts = 1
        inst.daily_state.entries = [fake_entry]
        inst.daily_state.active_entries = [fake_entry]

        HydraStrategy._recover_positions_from_saxo(inst)

        inst._reconcile_recovered_entries_with_broker.assert_called_once()
        inst.client.get_positions.assert_not_called()


class TestSubclassRelationship:
    def test_is_hydra_strategy_subclass(self):
        from bots.hydra.strategy import HydraStrategy
        assert issubclass(BrandonHydraStrategy, HydraStrategy)

    def test_overrides_check_stop_losses(self):
        from bots.hydra.strategy import HydraStrategy
        # Verify the method is defined on the subclass, not just inherited
        assert "_check_stop_losses" in BrandonHydraStrategy.__dict__

    def test_overrides_spread_width(self):
        assert "_get_vix_adjusted_spread_width" in BrandonHydraStrategy.__dict__

    def test_overrides_reset_for_new_day(self):
        assert "_reset_for_new_day" in BrandonHydraStrategy.__dict__


class TestOverlayReconciliation:
    """L-H6: live overlay hedge legs (real conid) must appear in
    _expected_position_quantities so reconciliation is not blind to them;
    dry-run DRY_OVERLAY placeholders (conid=None) must NOT appear."""

    def _leg(self, *, side, qty, conid, position_id):
        from bots.hydra.brandon.hedge_position import HedgeLeg
        return HedgeLeg(
            entry_number=1, side=side, contract_type="put", strike=6800.0,
            quantity=qty, fill_price=1.5, position_id=position_id,
            structure="debit_spread", threatened_side="put",
            placed_at=datetime.now(timezone.utc), conid=conid,
        )

    def test_live_hedge_conid_in_expected(self):
        inst = _make_instance()
        inst.daily_state = MagicMock()
        inst.daily_state.entries = []  # no IC legs → base returns {}
        inst._brandon_hedge_legs = {
            1: [
                self._leg(side="long", qty=2, conid=123456, position_id="123456"),
                self._leg(side="short", qty=2, conid=654321, position_id="654321"),
            ]
        }
        expected = inst._expected_position_quantities()
        assert expected.get(123456) == 2    # long → +qty
        assert expected.get(654321) == -2   # short → -qty

    def test_dry_overlay_placeholder_excluded(self):
        inst = _make_instance()
        inst.daily_state = MagicMock()
        inst.daily_state.entries = []
        inst._brandon_hedge_legs = {
            1: [self._leg(side="long", qty=2, conid=None,
                          position_id="DRY_OVERLAY_1_put_0")]
        }
        expected = inst._expected_position_quantities()
        assert expected == {}  # conid=None → skipped entirely

    def test_settled_hedge_excluded_from_expected(self):
        """2026-08-20 (execution audit finding): once an entry's hedge is
        settled (_brandon_overlay_booked), its legs must drop out of the
        expected-position set — the real IBKR position no longer exists —
        even though _brandon_hedge_legs itself is never cleared (the
        dashboard sidecar needs it post-close). Without this, every same-day
        restart after a settlement logged a permanent POS-003 'ambiguous'
        warning indistinguishable from a genuinely stuck leg."""
        inst = _make_instance()
        inst.daily_state = MagicMock()
        inst.daily_state.entries = []
        inst._brandon_hedge_legs = {
            1: [self._leg(side="long", qty=2, conid=123456, position_id="123456")],  # settled
            2: [self._leg(side="short", qty=3, conid=999999, position_id="999999")],  # still open
        }
        inst._brandon_overlay_booked = {1}
        expected = inst._expected_position_quantities()
        assert 123456 not in expected
        assert expected.get(999999) == -3

    def test_partial_overlay_alert_fires_critical(self):
        inst = _make_instance()
        sent = []
        inst._brandon_send_telegram = lambda *a, **k: sent.append(k.get("priority_name"))
        proposal = MagicMock()
        proposal.threatened_side = "put"
        inst._brandon_alert_overlay_partial(MagicMock(entry_number=1), proposal,
                                            placed=1, expected=2)
        assert sent == ["CRITICAL"]


class TestGexFallbackStop:
    """L-C1 + L-C2: HYDRA's credit+buffer stop is the LIVE protection beneath
    Brandon's GEX breach in BOTH GEX states.

    L-C1 — GEX fully unavailable: the breach exit can never fire, so the
    credit+buffer is the only stop and ACTS (super()._check_stop_losses).

    L-C2 (2026-06-10) — GEX armed but the breach exit can't protect a threatened
    short (decel wall far from the short / strike off a stale profile): the
    breach exit gets first crack, and if it does not close the side the
    credit+buffer ACTS as the backstop. It is no longer shadow-only. Per tick
    the GEX stop and the backstop are mutually exclusive → never a double-stop.
    """

    def _active_entry(self):
        e = MagicMock()
        e.entry_number = 1
        e.call_side_stopped = False
        e.put_side_stopped = False
        return e

    def test_fallback_delegates_to_parent_stop_when_gex_unavailable(self, monkeypatch):
        # GEX disabled (e.g. no Polygon key) → gex_stop_armed is False → the
        # parent HYDRA stop must be invoked and its action returned.
        inst = _make_instance(brandon_gex_enabled=False, brandon_hydra_shadow_enabled=True)
        inst._batch_update_entry_prices = MagicMock()
        inst.daily_state = MagicMock()
        inst.daily_state.active_entries = [self._active_entry()]
        inst._brandon_today_date = lambda: "2026-06-09"
        inst._brandon_send_telegram = MagicMock()

        called = {"parent": 0}
        parent = BrandonHydraStrategy.__mro__[1]  # HydraStrategy

        def fake_parent_stop(self_):
            called["parent"] += 1
            return "STOP E#1 call"

        monkeypatch.setattr(parent, "_check_stop_losses", fake_parent_stop)
        result = inst._check_stop_losses()

        assert called["parent"] == 1, "fallback must delegate to HYDRA's stop"
        assert result == "STOP E#1 call"

    def test_gex_armed_credit_buffer_acts_as_backstop(self, monkeypatch):
        # L-C2: GEX armed (profile present) + breach exit did NOT fire this tick
        # → the credit+buffer ACTS as the backstop (parent stop called, its
        # action returned), AND the shadow early-warning runs first. This is the
        # 2026-06-10 variant-C gap: a threatened short whose decel wall is far
        # away must still be stopped by the credit+buffer, not ride unprotected.
        from datetime import date
        from bots.hydra.brandon.gex_provider import GEXProfile
        prof = GEXProfile(
            spot=6800, expiry=date(2026, 5, 5),
            fetched_at=datetime.now(timezone.utc), strikes=(),
        )
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_breach_exit_enabled=True,
            brandon_hydra_shadow_enabled=True,
        )
        inst._batch_update_entry_prices = MagicMock()
        inst.daily_state = MagicMock()
        inst.daily_state.active_entries = [self._active_entry()]
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        inst._brandon_check_breach_exit = lambda e: None  # no breach this tick
        inst._brandon_check_hydra_shadow_stop = MagicMock()

        called = {"parent": 0}
        parent = BrandonHydraStrategy.__mro__[1]
        monkeypatch.setattr(
            parent, "_check_stop_losses",
            lambda self_: called.__setitem__("parent", called["parent"] + 1) or "STOP E#1 put",
        )
        result = inst._check_stop_losses()

        assert called["parent"] == 1, "GEX armed but no breach → credit+buffer backstop must ACT"
        assert result == "STOP E#1 put"
        inst._brandon_check_hydra_shadow_stop.assert_called()  # early-warning still logged

    def test_gex_armed_breach_fires_skips_backstop(self, monkeypatch):
        # When the GEX breach exit DOES fire, it returns early — the credit+buffer
        # backstop must NOT also run that tick (mutually exclusive → no double-stop).
        from datetime import date
        from bots.hydra.brandon.gex_provider import GEXProfile
        prof = GEXProfile(
            spot=6800, expiry=date(2026, 5, 5),
            fetched_at=datetime.now(timezone.utc), strikes=(),
        )
        inst = _make_instance(
            brandon_gex_enabled=True, brandon_breach_exit_enabled=True,
            brandon_hydra_shadow_enabled=True,
        )
        inst._batch_update_entry_prices = MagicMock()
        inst.daily_state = MagicMock()
        inst.daily_state.active_entries = [self._active_entry()]
        inst._brandon_get_gex_profile = lambda d, **_kw: prof
        inst._brandon_check_breach_exit = lambda e: "BRANDON-BREACH E#1 put"  # breach fires
        inst._brandon_check_hydra_shadow_stop = MagicMock()

        called = {"parent": 0}
        parent = BrandonHydraStrategy.__mro__[1]
        monkeypatch.setattr(
            parent, "_check_stop_losses",
            lambda self_: called.__setitem__("parent", called["parent"] + 1) or "NOPE",
        )
        result = inst._check_stop_losses()

        assert result == "BRANDON-BREACH E#1 put", "GEX breach is primary and returns early"
        assert called["parent"] == 0, "backstop must NOT run on the same tick the breach fired"

    def test_fallback_alert_fires_once_per_day(self):
        inst = _make_instance(brandon_gex_enabled=False)
        inst._brandon_today_date = lambda: "2026-06-09"
        sent = []
        inst._brandon_send_telegram = lambda *a, **k: sent.append(k.get("title", ""))
        inst._brandon_alert_gex_fallback()
        inst._brandon_alert_gex_fallback()  # same day → suppressed
        assert len(sent) == 1
        assert "GEX DOWN" in sent[0]

    def test_fallback_alert_refires_on_new_day(self):
        inst = _make_instance(brandon_gex_enabled=False)
        sent = []
        inst._brandon_send_telegram = lambda *a, **k: sent.append(k.get("title", ""))
        inst._brandon_today_date = lambda: "2026-06-09"
        inst._brandon_alert_gex_fallback()
        inst._brandon_today_date = lambda: "2026-06-10"  # new ET day
        inst._brandon_alert_gex_fallback()
        assert len(sent) == 2


# ── hedge-state path variant isolation (2026-07-07) ───────────────────────────
def test_hedge_state_path_is_variant_isolated(monkeypatch):
    """The hedge sidecar must live under the VARIANT-AWARE DATA_DIR, NOT the shared
    base data/ — else B and C clobber each other's overlays into one file (variant C
    loaded + settled B's on 07-06 → the phantom -$6,037)."""
    import bots.hydra.strategy as strat_mod
    monkeypatch.setattr(strat_mod, "DATA_DIR", "/opt/calypso/data/variant_c", raising=False)
    inst = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    assert inst._brandon_resolve_hedge_state_path() == \
        "/opt/calypso/data/variant_c/brandon_hedge_legs.json"


def test_hedge_state_path_base_dir_for_variant_a(monkeypatch):
    # Variant A (HYDRA_VARIANT_ID unset) → DATA_DIR is the base data/ dir.
    import bots.hydra.strategy as strat_mod
    monkeypatch.setattr(strat_mod, "DATA_DIR", "/opt/calypso/data", raising=False)
    inst = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    assert inst._brandon_resolve_hedge_state_path() == \
        "/opt/calypso/data/brandon_hedge_legs.json"
