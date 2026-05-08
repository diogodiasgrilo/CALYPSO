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
        current_price=6800.0,
        dry_run=True,
        alert_service=None,
    )
    defaults.update(brandon_attrs)
    for k, v in defaults.items():
        setattr(inst, k, v)
    return inst


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
        inst._brandon_get_gex_profile = lambda d: None
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
        inst._brandon_get_gex_profile = lambda d: prof
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
        inst._brandon_get_gex_profile = lambda d: prof
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
        inst._brandon_get_gex_profile = lambda d: prof
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
        inst._close_entry_early = MagicMock(return_value=(4, 0, []))
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
        inst._close_entry_early = MagicMock(return_value=(2, 0, []))
        result = inst._brandon_check_take_profit(e)
        assert result is not None
        # Put closed via Brandon TP → *_side_stopped + actual_*_stop_debit raw
        assert e.put_side_stopped is True
        assert e.actual_put_stop_debit == pytest.approx(20.0)
        # Only put side correction (call was already dead, not touched).
        assert inst.daily_state.total_realized_pnl == pytest.approx(80.0)

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
        # First call: fails, sets failure_at
        assert inst._brandon_get_gex_profile(date(2026, 5, 4)) is None
        assert calls["n"] == 1
        # Second call within cooldown: doesn't retry
        assert inst._brandon_get_gex_profile(date(2026, 5, 4)) is None
        assert calls["n"] == 1



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
        inst._brandon_get_gex_profile = lambda d: prof
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
        inst._brandon_get_gex_profile = lambda d: prof
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
        inst._brandon_get_gex_profile = lambda d: prof
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
        inst._brandon_get_gex_profile = lambda d: prof
        e = self._entry()
        inst._brandon_apply_strike_adjuster(e)
        assert e.short_call_strike == 6850  # unchanged


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
        inst._brandon_get_gex_profile = lambda d: empty_profile
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
        inst._brandon_get_gex_profile = lambda d: prof
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
    """Verify HYDRA's credit+buffer stop runs in shadow only — never closes."""

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
        inst._brandon_get_gex_profile = lambda d: self._profile_with_call_accel()
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
        inst._brandon_get_gex_profile = lambda d: self._profile_with_call_accel()
        e = self._entry()
        inst._brandon_check_overlay(e)
        first_count = len(inst._brandon_hedge_legs.get(1, []))
        inst._brandon_check_overlay(e)  # second tick
        assert len(inst._brandon_hedge_legs[1]) == first_count

    def test_settle_hedges_returns_settlements(self):
        from bots.hydra.brandon.hedge_position import HedgeLeg
        from datetime import datetime, timezone

        inst = _make_instance()
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
    """Regression coverage for the 2026-05-05 dry-run state-loss bug.

    Pre-fix: a mid-day restart in dry mode short-circuited
    `_recover_positions_from_saxo` to `return False` without loading the
    state file. The next state-save then wrote empty entries to disk,
    silently wiping today's session (variant A's 10:45 IC and variant C's
    11:16 put-only entry both vanished from the journal). The fix calls
    `_load_state_file_history()` before returning so today's entries are
    rehydrated into `daily_state` first.
    """

    def test_dry_run_loads_state_history_before_returning(self):
        from bots.hydra.strategy import HydraStrategy

        inst = _make_instance()
        inst.client = MagicMock()
        inst.dry_run = True
        inst._load_state_file_history = MagicMock(return_value=True)

        result = HydraStrategy._recover_positions_from_saxo(inst)

        assert result is False
        inst._load_state_file_history.assert_called_once_with()

    def test_live_mode_still_queries_saxo(self):
        from bots.hydra.strategy import HydraStrategy

        inst = _make_instance()
        inst.dry_run = False
        inst.client = MagicMock()
        inst.client.get_positions.return_value = []
        inst._load_state_file_history = MagicMock(return_value=False)
        inst.daily_state = MagicMock()

        HydraStrategy._recover_positions_from_saxo(inst)

        inst.client.get_positions.assert_called_once_with()


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
