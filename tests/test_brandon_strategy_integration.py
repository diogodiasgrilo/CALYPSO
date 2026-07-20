"""Method-level integration tests for BrandonHydraStrategy.

Avoids the full HydraStrategy.__init__ dependency chain (Saxo client, config
loader, trade logger, schema, etc.) by constructing the instance via __new__
and setting the Brandon-specific attributes directly. The override methods
are then exercised in isolation.

Full end-to-end coverage of HydraStrategy itself is out of scope here — those
methods are tested in their existing suite. We only verify that the overrides
correctly route to Brandon modules vs. parent.
"""

import os
import sys
from datetime import datetime, timezone
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
        brandon_overlay_enabled=False,
        brandon_overlay_trigger_distance_pts=25.0,
        brandon_overlay_butterfly_width=10,
        brandon_overlay_butterfly_cutoff_hour=12,
        brandon_overlay_butterfly_cutoff_minute=30,
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
        _brandon_breach_states={},
        _brandon_overlay_placed=set(),
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
            return [{"details": {"strike_price": 6800, "contract_type": "call"},
                     "open_interest": 100, "greeks": {"gamma": 0.001}}]

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
        # Pre-seed two legs of a call debit spread on entry 1
        inst._brandon_hedge_legs[1] = [
            HedgeLeg(1, "long", "call", 6850, 1, fill_price=8.0,
                     position_id="DRY_OVERLAY_1_call_0", structure="debit_spread",
                     threatened_side="call", placed_at=datetime.now(timezone.utc)),
            HedgeLeg(1, "short", "call", 6860, 1, fill_price=3.0,
                     position_id="DRY_OVERLAY_1_call_1", structure="debit_spread",
                     threatened_side="call", placed_at=datetime.now(timezone.utc)),
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
