"""GhauriMeanReversionStrategy (Variant F) tests.

Pins the SAFETY-critical, adversarially-audited behavior: the dry-run lock,
the touch-trigger's independence from entry_times/_next_entry_index (the
audited first design broke on 2 of the 4 day-scenarios below — this is the
regression coverage for the corrected, fully self-contained design), the
corrected stop formula + its floor, and the trail-to-breakeven invariant
that it only ever tightens the base stop, never loosens it.
"""
from __future__ import annotations

import sys
from datetime import datetime, time as dt_time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from bots.hydra.base_strategy import ConfigError, MEICDailyState, MEICState
from bots.hydra.ghauri_strategy import (
    GHAURI_MIN_STOP_LEVEL_PER_CONTRACT,
    GhauriEntry,
    GhauriMeanReversionStrategy,
)
from bots.hydra.strategy import HydraStrategy


def _inst(**overrides):
    """Build a GhauriMeanReversionStrategy without running __init__ (avoids
    broker I/O), then set exactly the attributes each test needs — mirrors
    the established pattern in tests/test_double_calendar_strategy.py."""
    inst = GhauriMeanReversionStrategy.__new__(GhauriMeanReversionStrategy)
    inst.dry_run = True  # this variant is dry-run-LOCKED; matches every real instance
    inst.config = {}  # real instances always have this by the time any override runs
    inst.contracts_per_entry = 1
    inst.ghauri_target_delta_pct = 0.15
    inst.ghauri_delta_band = (0.05, 0.35)
    inst.ghauri_delta_max_reads = 6
    inst.ghauri_width_pt = 10.0
    inst.ghauri_strike_search_pts = 150.0
    inst.ghauri_profit_target_pct = 0.50
    inst.ghauri_pct_of_credit = 1.00
    inst.ghauri_trail_arm_pct = 0.25
    inst.ghauri_trail_lock_pct = 0.0
    inst.ghauri_entry_cutoff_time = dt_time(13, 0)
    inst.ghauri_preferred_window_min = 60
    inst._ghauri_upper_boundary = None
    inst._ghauri_lower_boundary = None
    inst._ghauri_upper_fired_today = False
    inst._ghauri_lower_fired_today = False
    inst._ghauri_pending_fire_side = None
    inst._ghauri_next_entry_number = 1
    inst.current_price = 0.0
    inst.market_data = SimpleNamespace(spx_open=0.0, vix_open=0.0)
    inst.daily_state = MEICDailyState()
    inst.state = MEICState.WAITING_FIRST_ENTRY
    for k, v in overrides.items():
        setattr(inst, k, v)
    return inst


def _et(hour, minute):
    return datetime(2026, 8, 25, hour, minute, 0)


def _active_entry():
    """A GhauriEntry that MEICDailyState.active_entries actually counts as
    active — one-sided (call_only), with a real leg marker (uic) and
    is_complete=True, matching what _execute_call_spread_only leaves behind
    on a successful placement."""
    e = GhauriEntry(entry_number=1)
    e.call_only = True
    e.put_only = False
    e.is_complete = True
    e.short_call_uic = 12345
    return e


def _inst_with_entry(side, credit, **overrides):
    """A GhauriMeanReversionStrategy instance with one real, active GhauriEntry
    attached to daily_state — the shared fixture for every test that needs to
    drive assertions through the REAL _check_stop_losses() rather than
    re-deriving its formulas inline (a prior version of this test file had
    tautological tests that reimplemented the stop/trail math by hand and
    would have kept passing even if the shipped implementation regressed —
    found by adversarial post-implementation review). Callers monkeypatch
    `{side}_spread_value` on `type(entry)` for a deterministic mark."""
    inst = _inst(**overrides)
    inst._batch_update_entry_prices = MagicMock()
    entry = GhauriEntry(entry_number=1)
    entry.call_only = side == "call"
    entry.put_only = side == "put"
    # is_complete + a uic are what MEICDailyState.active_entries actually
    # checks (a bare call_only/put_only flag isn't enough) — without these
    # the entry is silently excluded from the loop and _check_stop_losses
    # would return None regardless of what's being tested.
    entry.is_complete = True
    setattr(entry, f"short_{side}_uic", 12345)
    if side == "call":
        entry.call_spread_credit = credit
    else:
        entry.put_spread_credit = credit
    inst.daily_state.entries.append(entry)
    return inst, entry


class TestDryRunLock:
    """Mirrors TestDryRunLock in test_double_calendar_strategy.py exactly."""

    def _patch_super_init(self, monkeypatch):
        def fake_init(self, *a, **k):
            self.dry_run = k.get("dry_run", False)
        monkeypatch.setattr(HydraStrategy, "__init__", fake_init)

    def test_refuses_non_dry_run(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        with pytest.raises(ConfigError):
            GhauriMeanReversionStrategy(None, {}, None, dry_run=False)

    def test_refuses_when_dry_run_kwarg_absent(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        with pytest.raises(ConfigError):
            GhauriMeanReversionStrategy(None, {}, None)

    def test_allows_dry_run(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        s = GhauriMeanReversionStrategy(None, {}, None, dry_run=True)
        assert isinstance(s, GhauriMeanReversionStrategy)
        assert s.dry_run is True

    def test_reads_ghauri_config_block_before_super_init(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        cfg = {"strategy": {"ghauri": {
            "target_delta_pct": 0.20, "width_pt": 15.0, "pct_of_credit": 0.80,
            "trail_arm_pct": 0.30, "trail_lock_pct": 0.05, "entry_cutoff_time": "12:30",
        }}}
        s = GhauriMeanReversionStrategy(None, cfg, None, dry_run=True)
        assert s.ghauri_target_delta_pct == 0.20
        assert s.ghauri_width_pt == 15.0
        assert s.ghauri_pct_of_credit == 0.80
        assert s.ghauri_trail_arm_pct == 0.30
        assert s.ghauri_trail_lock_pct == 0.05
        assert s.ghauri_entry_cutoff_time == dt_time(12, 30)

    def test_config_defaults_when_ghauri_block_absent(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        s = GhauriMeanReversionStrategy(None, {}, None, dry_run=True)
        assert s.ghauri_target_delta_pct == 0.15
        assert s.ghauri_pct_of_credit == 1.00
        assert s.ghauri_entry_cutoff_time == dt_time(13, 0)


class TestContract:
    def test_bot_name(self):
        assert GhauriMeanReversionStrategy.BOT_NAME == "HYDRA"

    def test_ghauri_entry_subclasses_hydra_entry(self):
        from bots.hydra.strategy import HydraIronCondorEntry
        e = GhauriEntry(entry_number=1)
        assert isinstance(e, HydraIronCondorEntry)
        assert e.peak_profit_pct == 0.0
        assert e.trail_armed is False


class TestRealConstructionSmoke:
    """Constructs through the REAL HydraStrategy.__init__ (no monkeypatch
    stub, no __new__ bypass) — every other test in this file uses one of
    those two shortcuts, which is exactly why a real bug shipped undetected:
    HydraStrategy.__init__ itself reads self.vix_gate_enabled right after
    calling self._parse_entry_times() (polymorphically dispatched to this
    class's own override), and Ghauri's override never set it — a 100%
    reproducible crash on every real construction, caught only by a live
    systemd start attempt on 2026-08-27, not by any of this file's 26+
    existing tests. Fixed in _parse_entry_times (now replicates the base's
    vix-gate attribute initialization). These two tests are the regression
    coverage for that whole bug class, not just the one attribute."""

    def test_real_construction_with_minimal_config_does_not_raise(self):
        # {"strategy": {}} is the minimum CONFIG-001 accepts (a bare {} fails
        # its own "Missing required config section: strategy" check, which
        # is correct validation behavior, not the bug under test here).
        broker = MagicMock()
        s = GhauriMeanReversionStrategy(broker, {"strategy": {}}, MagicMock(), dry_run=True)
        assert isinstance(s, GhauriMeanReversionStrategy)
        assert s.vix_gate_enabled is False

    def test_real_construction_with_shipped_variant_f_config_does_not_raise(self):
        """Reproduces the exact conditions of the live crash: the actual
        checked-in config_variant_f.json, not a synthetic {}."""
        import json
        config_path = (
            Path(__file__).resolve().parents[1]
            / "bots" / "hydra" / "config" / "config_variant_f.json"
        )
        with open(config_path) as f:
            config = json.load(f)
        broker = MagicMock()
        s = GhauriMeanReversionStrategy(broker, config, MagicMock(), dry_run=True)
        assert isinstance(s, GhauriMeanReversionStrategy)
        assert s.vix_gate_enabled is False  # config_variant_f.json sets vix_time_shift.enabled=false
        assert s.dry_run is True


class TestHeartbeatDoesNotClaimFullIC:
    """2026-08-28 audit (cosmetic): every heartbeat line printed 'E1-E1: full
    IC (<0.00% drop)' — HydraStrategy's shared base-entry-schedule label,
    meaningless for a strategy whose entries are EM-boundary-touch triggered,
    never clock-scheduled. Ghauri overrides _show_ic_schedule_in_heartbeat to
    False so this line is omitted entirely rather than printing something
    misleading; the flag defaults True (unchanged) for every other variant."""

    def test_ghauri_omits_the_full_ic_schedule_line(self, monkeypatch):
        from bots.hydra.base_strategy import MEICStrategy
        monkeypatch.setattr(MEICStrategy, "get_detailed_position_status", lambda self: [])
        inst = _inst()
        inst.market_data = SimpleNamespace(spx_open=100.0, vix_open=15.0)
        inst.current_price = 105.0
        inst._current_trend = None
        inst.vix_gate_enabled = False

        lines = HydraStrategy.get_detailed_position_status(inst)

        assert not any("full IC" in line or "Up-day" in line or "Down-day" in line for line in lines)

    def test_default_hydra_strategy_still_shows_the_schedule_line(self, monkeypatch):
        """Negative control: confirms the flag genuinely defaults True and the
        line still renders for every OTHER variant (unchanged behavior)."""
        from bots.hydra.base_strategy import MEICStrategy
        monkeypatch.setattr(MEICStrategy, "get_detailed_position_status", lambda self: [])
        inst = HydraStrategy.__new__(HydraStrategy)
        inst.market_data = SimpleNamespace(spx_open=100.0, vix_open=15.0)
        inst.current_price = 105.0
        inst._current_trend = None
        inst.vix_gate_enabled = False
        inst.base_entry_downday_callonly_pct = None
        inst._base_entry_count = 3
        inst.upday_putonly_enabled = False
        inst.downday_callonly_conditional_enabled = False

        lines = HydraStrategy.get_detailed_position_status(inst)

        assert any("full IC" in line for line in lines)


class TestParseEntryTimes:
    def test_single_meaningful_placeholder_not_a_real_schedule(self):
        inst = _inst()
        inst._parse_entry_times()
        assert inst.entry_times == [dt_time(9, 30)]
        assert inst._base_entry_count == 1


class TestSkipMissedEntriesAndIsEntryTime:
    def test_skip_missed_entries_is_a_true_noop(self):
        inst = _inst()
        # Must not raise and must not touch any entry_times/_next_entry_index
        # state (this class never sets _next_entry_index at all).
        inst._skip_missed_entries(_et(11, 0))
        assert not hasattr(inst, "_next_entry_index") or inst._next_entry_index in (0,)

    def test_is_entry_time_reflects_pending_fire_side(self):
        inst = _inst()
        assert inst._is_entry_time() is False
        inst._ghauri_pending_fire_side = "call"
        assert inst._is_entry_time() is True


class TestShouldAttemptEntry:
    """The 4 day-scenarios the adversarial audit specifically named as where
    the FIRST (dummy entry_times slot) design broke. This design never reads
    entry_times/_next_entry_index in this method at all, so these are
    regression coverage for the corrected design, not a generic sanity check.
    """

    def _armed_inst(self, spx_open=6500.0, vix_open=15.0):
        return _inst(
            current_price=spx_open,
            market_data=SimpleNamespace(spx_open=spx_open, vix_open=vix_open),
        )

    def test_boundaries_not_computed_without_session_open_data(self):
        inst = _inst(current_price=6500.0, market_data=SimpleNamespace(spx_open=0.0, vix_open=0.0))
        assert inst._should_attempt_entry(_et(9, 35)) is False
        assert inst._ghauri_upper_boundary is None

    def test_scenario_a_no_touch_all_day_stays_waiting_not_daily_complete(self):
        # SPX open 6500, VIX 15 -> EM = 6500*0.15/sqrt(252) ~= 61.4pt.
        # Price drifts only slightly all day -> never touches either boundary.
        inst = self._armed_inst()
        for hour, minute, px in [(9, 35, 6500), (10, 30, 6510), (11, 45, 6495), (12, 55, 6520)]:
            inst.current_price = px
            result = inst._should_attempt_entry(_et(hour, minute))
            assert result is False
        # Still before cutoff at last check (12:55) -> must NOT have gone DAILY_COMPLETE.
        assert inst.state != MEICState.DAILY_COMPLETE
        assert inst._ghauri_upper_fired_today is False
        assert inst._ghauri_lower_fired_today is False

    def test_scenario_b_one_touch_then_calm_fires_exactly_once(self):
        inst = self._armed_inst()
        inst.current_price = 6500.0
        assert inst._should_attempt_entry(_et(9, 35)) is False  # sets boundaries, no touch yet
        upper = inst._ghauri_upper_boundary
        inst.current_price = upper + 1  # touch the upper boundary
        assert inst._should_attempt_entry(_et(9, 45)) is True
        assert inst._ghauri_pending_fire_side == "call"
        assert inst._ghauri_upper_fired_today is True
        # Simulate the entry actually getting placed (an active entry now exists)
        # and the market staying calm near/above the boundary the rest of the day.
        inst._ghauri_pending_fire_side = None
        inst.daily_state.entries.append(_active_entry())
        for hour, minute in [(10, 0), (11, 0), (12, 30)]:
            inst.current_price = upper + 2
            assert inst._should_attempt_entry(_et(hour, minute)) is False
        assert inst._ghauri_lower_fired_today is False

    def test_scenario_c_both_boundaries_touched_spread_apart_in_time(self):
        # The audited first design silently missed the SECOND touch here
        # (entry_times slots got consumed by elapsed dummy-clock time before
        # the second real touch occurred). This design has no such coupling.
        inst = self._armed_inst()
        inst.current_price = 6500.0
        inst._should_attempt_entry(_et(9, 35))
        upper, lower = inst._ghauri_upper_boundary, inst._ghauri_lower_boundary

        inst.current_price = upper + 1
        assert inst._should_attempt_entry(_et(9, 45)) is True
        assert inst._ghauri_pending_fire_side == "call"
        inst._ghauri_pending_fire_side = None

        # Hours later (well past when the old design's dummy slots would have
        # been silently consumed), price reverses hard and touches the lower
        # boundary too.
        inst.current_price = lower - 1
        assert inst._should_attempt_entry(_et(12, 15)) is True
        assert inst._ghauri_pending_fire_side == "put"
        assert inst._ghauri_upper_fired_today is True
        assert inst._ghauri_lower_fired_today is True

    def test_scenario_d_same_boundary_touched_twice_whipsaw_fires_once(self):
        inst = self._armed_inst()
        inst.current_price = 6500.0
        inst._should_attempt_entry(_et(9, 35))
        upper = inst._ghauri_upper_boundary

        inst.current_price = upper + 1
        assert inst._should_attempt_entry(_et(9, 45)) is True
        inst._ghauri_pending_fire_side = None

        # Price pulls back below the boundary, then touches it again later.
        inst.current_price = upper - 5
        assert inst._should_attempt_entry(_et(10, 0)) is False
        inst.current_price = upper + 3
        assert inst._should_attempt_entry(_et(10, 15)) is False  # already fired today

    def test_cutoff_transitions_to_daily_complete_when_no_active_entries(self):
        inst = self._armed_inst()
        inst.current_price = 6500.0
        inst._should_attempt_entry(_et(9, 35))
        # daily_state.entries stays empty -> active_entries is empty too.
        result = inst._should_attempt_entry(_et(13, 5))
        assert result is False
        assert inst.state == MEICState.DAILY_COMPLETE

    def test_cutoff_does_not_transition_when_active_entries_remain(self):
        inst = self._armed_inst()
        inst.current_price = 6500.0
        inst._should_attempt_entry(_et(9, 35))
        inst.daily_state.entries.append(_active_entry())
        result = inst._should_attempt_entry(_et(13, 5))
        assert result is False
        assert inst.state != MEICState.DAILY_COMPLETE


class TestStopFormula:
    """The audited math bug: stop_level = pct_of_credit * credit computes to
    BREAKEVEN at pct_of_credit=1.0 (self-stops on the first tick), not a
    1x-credit loss. Every test here drives the REAL _check_stop_losses() (via
    the module-level _inst_with_entry fixture) rather than re-deriving the
    formula inline — a prior version of this class reimplemented the math by
    hand and would have kept passing even if the shipped implementation
    regressed back to the audited-out bug; found by adversarial
    post-implementation review, fixed here."""

    def test_corrected_formula_at_full_credit_loss(self, monkeypatch):
        inst, entry = _inst_with_entry("call", credit=200.0, ghauri_pct_of_credit=1.00)
        monkeypatch.setattr(type(entry), "call_spread_value", property(lambda self: 190.0))  # not yet at TP/stop
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        inst._check_stop_losses()

        confirm_mock.assert_called_once()
        effective_stop = confirm_mock.call_args[0][3]
        assert effective_stop == pytest.approx(400.0)  # credit * (1 + pct_of_credit)
        # Net loss at trigger = stop - credit = 200 = exactly 1x credit.
        assert (effective_stop - 200.0) == pytest.approx(200.0)

    def test_old_wrong_formula_would_have_failed_this_assertion(self, monkeypatch):
        # Negative control: proves this test suite WOULD catch the audited-out
        # bug if it were reintroduced. The old (buggy) design computed
        # `stop = pct_of_credit * credit` = 200.0 for this input, not 400.0 —
        # asserting the real formula's actual output (400.0) means a
        # regression back to the old formula fails this test, unlike the
        # tautological version this replaced (which reimplemented both
        # formulas locally and could never fail either way).
        inst, entry = _inst_with_entry("call", credit=200.0, ghauri_pct_of_credit=1.00)
        monkeypatch.setattr(type(entry), "call_spread_value", property(lambda self: 190.0))
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        inst._check_stop_losses()

        effective_stop = confirm_mock.call_args[0][3]
        old_wrong_stop = 1.00 * 200.0  # what the audited-out formula would have produced
        assert effective_stop != pytest.approx(old_wrong_stop)
        assert effective_stop == pytest.approx(400.0)

    def test_thin_credit_hits_the_floor(self, monkeypatch):
        inst, entry = _inst_with_entry("put", credit=10.0, contracts_per_entry=1, ghauri_pct_of_credit=1.00)
        monkeypatch.setattr(type(entry), "put_spread_value", property(lambda self: 8.0))
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        inst._check_stop_losses()

        effective_stop = confirm_mock.call_args[0][3]
        assert effective_stop == pytest.approx(GHAURI_MIN_STOP_LEVEL_PER_CONTRACT)  # 10*2=20 < $50 floor
        assert entry.put_side_stop == pytest.approx(GHAURI_MIN_STOP_LEVEL_PER_CONTRACT)

    def test_floor_scales_with_contracts(self):
        inst = _inst(contracts_per_entry=3)
        assert inst._min_stop_level() == GHAURI_MIN_STOP_LEVEL_PER_CONTRACT * 3


class TestTrailToBreakeven:
    """Drives the real _check_stop_losses() for every assertion — see
    TestStopFormula's docstring for why this matters (a prior version of this
    class hand-copied the arm/tighten logic inline and would never have
    caught a real regression)."""

    def test_unarmed_trail_never_affects_base_stop(self, monkeypatch):
        # 10% captured -- well below the 25% arm threshold.
        inst, entry = _inst_with_entry(
            "call", credit=100.0, ghauri_pct_of_credit=1.00,
            ghauri_trail_arm_pct=0.25, ghauri_trail_lock_pct=0.0,
        )
        monkeypatch.setattr(type(entry), "call_spread_value", property(lambda self: 90.0))
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        inst._check_stop_losses()

        assert entry.trail_armed is False
        effective_stop = confirm_mock.call_args[0][3]
        assert effective_stop == pytest.approx(200.0)  # unarmed base stop, untouched by trail config

    def test_armed_trail_only_ever_tightens_never_loosens(self, monkeypatch):
        # 30% captured -- crosses the 25% arm threshold this same tick.
        inst, entry = _inst_with_entry(
            "call", credit=100.0, ghauri_pct_of_credit=1.00,
            ghauri_trail_arm_pct=0.25, ghauri_trail_lock_pct=0.10,
        )
        monkeypatch.setattr(type(entry), "call_spread_value", property(lambda self: 70.0))
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        inst._check_stop_losses()

        assert entry.trail_armed is True
        effective_stop = confirm_mock.call_args[0][3]
        assert effective_stop == pytest.approx(90.0)  # credit * (1 - trail_lock_pct)
        assert effective_stop < 200.0  # strictly tighter than the unarmed base stop, never looser

    def test_trail_arms_once_and_stays_armed_across_ticks(self, monkeypatch):
        inst, entry = _inst_with_entry(
            "put", credit=100.0, ghauri_pct_of_credit=1.00,
            ghauri_trail_arm_pct=0.25, ghauri_trail_lock_pct=0.0,
        )
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        # Tick 1: 20% captured -> stays unarmed.
        monkeypatch.setattr(type(entry), "put_spread_value", property(lambda self: 80.0))
        inst._check_stop_losses()
        assert entry.trail_armed is False

        # Tick 2: crosses 25% -> arms.
        monkeypatch.setattr(type(entry), "put_spread_value", property(lambda self: 70.0))
        inst._check_stop_losses()
        assert entry.trail_armed is True

        # Tick 3: profit retraces back below 25% -> stays armed (one-way latch).
        monkeypatch.setattr(type(entry), "put_spread_value", property(lambda self: 90.0))
        inst._check_stop_losses()
        assert entry.trail_armed is True
        # Peak is the high-water mark (30% at tick 2), not the current 10%.
        assert entry.peak_profit_pct == pytest.approx(0.30)


class TestCalculateStrikes:
    def _mock_broker(self, chain_strikes, greeks_by_conid):
        broker = MagicMock()
        broker.get_option_chain.return_value = chain_strikes
        conid_map = {}
        for i, k in enumerate(chain_strikes):
            conid_map[(k, "C")] = 1000 + i
            conid_map[(k, "P")] = 2000 + i
        broker.qualify_option_strikes.return_value = conid_map

        def get_greeks(conid):
            return {"delta": greeks_by_conid.get(conid)}
        broker.get_option_greeks.side_effect = get_greeks
        return broker

    def test_call_side_picks_delta_target_strike_and_sets_width(self):
        chain = [6540, 6545, 6550, 6555, 6560]
        # Call conids 1000..1004 for strikes 6540..6560, decreasing delta as OTM.
        greeks = {1000: 0.50, 1001: 0.30, 1002: 0.15, 1003: 0.08, 1004: 0.03}
        broker = self._mock_broker(chain, greeks)
        inst = _inst(ghauri_target_delta_pct=0.15, ghauri_delta_band=(0.05, 0.35), ghauri_width_pt=10.0)
        inst.broker = broker
        inst.current_price = 6540.0
        inst.underlying_symbol = "SPX"
        inst.trading_class = "SPXW"
        inst.exchange = "CBOE"
        inst._get_todays_expiry = lambda: "2026-08-25"

        entry = GhauriEntry(entry_number=1)
        entry.call_only = True
        entry.put_only = False
        ok = inst._calculate_strikes(entry)
        assert ok is True
        assert entry.short_call_strike == 6550
        assert entry.long_call_strike == 6560

    def test_no_expiry_returns_false(self):
        inst = _inst()
        inst.current_price = 6500.0
        inst._get_todays_expiry = lambda: None
        entry = GhauriEntry(entry_number=1)
        entry.call_only = True
        assert inst._calculate_strikes(entry) is False

    def test_no_price_returns_false(self):
        inst = _inst()
        inst.current_price = 0.0
        entry = GhauriEntry(entry_number=1)
        assert inst._calculate_strikes(entry) is False

    def test_empty_chain_returns_false(self):
        broker = MagicMock()
        broker.get_option_chain.return_value = []
        inst = _inst()
        inst.broker = broker
        inst.current_price = 6500.0
        inst.underlying_symbol = "SPX"
        inst.trading_class = "SPXW"
        inst.exchange = "CBOE"
        inst._get_todays_expiry = lambda: "2026-08-25"
        entry = GhauriEntry(entry_number=1)
        entry.call_only = True
        assert inst._calculate_strikes(entry) is False


class TestCheckStopLossesOrchestration:
    """Local smoke coverage for _check_stop_losses without real IBKR: mocks
    the reused broker-facing methods (_batch_update_entry_prices,
    _check_stop_with_confirmation, _close_entry_early, _book_realized_pnl)
    and verifies this class's OWN orchestration — stop-level computation,
    take-profit detection, trail arming, and the floor/skip safety check —
    drives them correctly. Uses the module-level _inst_with_entry fixture
    (shared with TestStopFormula/TestTrailToBreakeven)."""

    def test_take_profit_fires_and_closes(self, monkeypatch):
        inst, entry = _inst_with_entry("put", credit=100.0, ghauri_profit_target_pct=0.50)
        monkeypatch.setattr(
            type(entry), "put_spread_value", property(lambda self: 40.0),
        )

        def fake_close(e, skip_sides=None):
            # Real _close_entry_early marks the closed side expired as a side
            # effect — set it here rather than pre-setting it before the call
            # (pre-setting would make the loop's own already-closed guard skip
            # the entry before take-profit is ever evaluated).
            e.put_side_expired = True
            return (2, 0, [])

        inst._close_entry_early = MagicMock(side_effect=fake_close)
        inst._book_realized_pnl = MagicMock()
        inst.alert_service = MagicMock()

        result = inst._check_stop_losses()
        assert result is not None
        assert "GHAURI-TP" in result
        assert entry.put_side_stopped is True
        assert entry.close_reason == "TP"
        inst._close_entry_early.assert_called_once()
        inst._book_realized_pnl.assert_called_once()
        # credit=100, close_cost=40 -> booked profit = 60
        booked_amount = inst._book_realized_pnl.call_args[0][0]
        assert booked_amount == pytest.approx(60.0)

    def test_take_profit_not_reached_falls_through_to_stop_check(self, monkeypatch):
        inst, entry = _inst_with_entry("call", credit=100.0, ghauri_profit_target_pct=0.50)
        monkeypatch.setattr(type(entry), "call_spread_value", property(lambda self: 90.0))
        inst._close_entry_early = MagicMock()
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        inst._check_stop_losses()

        inst._close_entry_early.assert_not_called()  # TP not reached (10% captured < 50%)
        confirm_mock.assert_called_once()
        # Verify the stop level passed through is the corrected formula:
        # credit * (1 + pct_of_credit) = 100 * 2.0 = 200, floored at $50 (n/a here).
        call_args = confirm_mock.call_args[0]
        assert call_args[0] is entry
        assert call_args[1] == "call"
        assert call_args[2] == pytest.approx(90.0)  # spread_value
        assert call_args[3] == pytest.approx(200.0)  # effective_stop
        assert entry.call_side_stop == pytest.approx(200.0)

    def test_thin_credit_stop_is_floored_and_evaluated(self, monkeypatch):
        # credit=$10 -> raw base stop would be $20, well under the $50 floor.
        inst, entry = _inst_with_entry("put", credit=10.0, ghauri_pct_of_credit=1.00)
        monkeypatch.setattr(type(entry), "put_spread_value", property(lambda self: 8.0))
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        inst._check_stop_losses()

        confirm_mock.assert_called_once()
        effective_stop = confirm_mock.call_args[0][3]
        assert effective_stop == pytest.approx(GHAURI_MIN_STOP_LEVEL_PER_CONTRACT)
        assert entry.put_side_stop == pytest.approx(GHAURI_MIN_STOP_LEVEL_PER_CONTRACT)

    def test_armed_trail_tightens_the_stop_passed_to_confirmation(self, monkeypatch):
        # credit=100, pct_of_credit=1.0 -> base stop=200. Already captured 30%
        # (spread_value=70) -> arms the trail (arm at 25%). trail_lock_pct=0.10
        # -> trail_stop = 100*(1-0.10) = 90, strictly tighter than 200.
        inst, entry = _inst_with_entry(
            "call", credit=100.0,
            ghauri_pct_of_credit=1.00, ghauri_trail_arm_pct=0.25, ghauri_trail_lock_pct=0.10,
        )
        monkeypatch.setattr(type(entry), "call_spread_value", property(lambda self: 70.0))
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        inst._check_stop_losses()

        assert entry.trail_armed is True
        effective_stop = confirm_mock.call_args[0][3]
        assert effective_stop == pytest.approx(90.0)
        assert effective_stop < 200.0  # tighter than the unarmed base stop

    def test_already_closed_side_is_skipped(self):
        inst, entry = _inst_with_entry("call", credit=100.0)
        entry.call_side_stopped = True
        inst._check_stop_with_confirmation = MagicMock(return_value=None)
        inst._close_entry_early = MagicMock()

        result = inst._check_stop_losses()

        assert result is None
        inst._check_stop_with_confirmation.assert_not_called()
        inst._close_entry_early.assert_not_called()

    def test_unpriced_entry_zero_credit_is_skipped_not_errored(self):
        inst, entry = _inst_with_entry("put", credit=0.0)
        inst._check_stop_with_confirmation = MagicMock(return_value=None)
        result = inst._check_stop_losses()
        assert result is None
        inst._check_stop_with_confirmation.assert_not_called()


class TestRestartRecoveryDoesNotDisableStopLossMonitoring:
    """CRITICAL regression coverage for a real bug found by adversarial post-
    implementation review: bots/hydra/strategy.py's restart-recovery path
    (_recover_positions_from_saxo -> state restore, shared/unedited code
    every HYDRA-lineage strategy goes through on every process start)
    reconstructs EVERY entry as the plain HydraIronCondorEntry base class,
    NEVER GhauriEntry. The first version of _check_stop_losses gated its loop
    on `isinstance(entry, GhauriEntry)`, which meant any open Ghauri position
    that survived a routine systemd restart (Restart=always/RestartSec=30,
    or a deploy) was SILENTLY excluded from stop-loss/take-profit monitoring
    FOREVER — no log line, no alert. These tests construct a plain
    HydraIronCondorEntry (simulating exactly what the restart path produces,
    not a GhauriEntry) and confirm _check_stop_losses still processes it."""

    def _restart_restored_entry(self, side, credit):
        """A plain HydraIronCondorEntry — NOT GhauriEntry — with no
        peak_profit_pct/trail_armed attributes at all, matching exactly what
        bots/hydra/strategy.py:_load_state_file_history's restore loop
        actually constructs (HydraIronCondorEntry(entry_number=entry_num)),
        regardless of which strategy subclass originally placed the entry."""
        from bots.hydra.strategy import HydraIronCondorEntry
        e = HydraIronCondorEntry(entry_number=1)
        e.call_only = side == "call"
        e.put_only = side == "put"
        e.is_complete = True
        setattr(e, f"short_{side}_uic", 12345)
        if side == "call":
            e.call_spread_credit = credit
        else:
            e.put_spread_credit = credit
        assert not hasattr(e, "peak_profit_pct")  # confirms this really isn't a GhauriEntry
        return e

    def test_restart_restored_entry_still_gets_stop_checked(self, monkeypatch):
        inst = _inst(ghauri_pct_of_credit=1.00)
        inst._batch_update_entry_prices = MagicMock()
        entry = self._restart_restored_entry("call", credit=100.0)
        inst.daily_state.entries.append(entry)
        monkeypatch.setattr(type(entry), "call_spread_value", property(lambda self: 90.0))
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        inst._check_stop_losses()

        # The critical assertion: a restart-restored (non-GhauriEntry) open
        # position is NOT silently skipped — it still reaches the real stop
        # check with the correct, freshly-computed base stop.
        confirm_mock.assert_called_once()
        effective_stop = confirm_mock.call_args[0][3]
        assert effective_stop == pytest.approx(200.0)  # credit * (1 + pct_of_credit)
        assert entry.call_side_stop == pytest.approx(200.0)

    def test_restart_restored_entry_take_profit_still_fires(self, monkeypatch):
        inst = _inst(ghauri_profit_target_pct=0.50)
        inst._batch_update_entry_prices = MagicMock()
        entry = self._restart_restored_entry("put", credit=100.0)
        inst.daily_state.entries.append(entry)
        monkeypatch.setattr(type(entry), "put_spread_value", property(lambda self: 40.0))

        def fake_close(e, skip_sides=None):
            e.put_side_expired = True
            return (2, 0, [])

        inst._close_entry_early = MagicMock(side_effect=fake_close)
        inst._book_realized_pnl = MagicMock()
        inst.alert_service = MagicMock()

        result = inst._check_stop_losses()

        assert result is not None
        assert "GHAURI-TP" in result
        inst._close_entry_early.assert_called_once()

    def test_restart_restored_entry_trail_degrades_to_unarmed_not_crash(self, monkeypatch):
        # A restart-restored entry has no trail_armed/peak_profit_pct at all —
        # confirm the getattr-default fallback ("trail never armed") kicks in
        # cleanly rather than raising AttributeError.
        inst = _inst(ghauri_pct_of_credit=1.00, ghauri_trail_arm_pct=0.25, ghauri_trail_lock_pct=0.10)
        inst._batch_update_entry_prices = MagicMock()
        entry = self._restart_restored_entry("call", credit=100.0)
        inst.daily_state.entries.append(entry)
        # 40% already captured -- would arm the trail on a fresh entry too,
        # but the point here is that it doesn't crash on a field that was
        # never set by the restore path.
        monkeypatch.setattr(type(entry), "call_spread_value", property(lambda self: 60.0))
        confirm_mock = MagicMock(return_value=None)
        inst._check_stop_with_confirmation = confirm_mock

        inst._check_stop_losses()  # must not raise AttributeError

        confirm_mock.assert_called_once()
        assert getattr(entry, "trail_armed", None) is True  # armed fresh, via setattr on the plain entry
        effective_stop = confirm_mock.call_args[0][3]
        assert effective_stop == pytest.approx(90.0)  # trail-tightened, not the unarmed 200.0
