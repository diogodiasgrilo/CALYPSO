"""DoubleCalendarStrategy (Strategy D — "DC Time Machine") scaffold tests.

Pins the SAFETY-critical scaffold behavior: the dry-run lock refuses any
non-dry-run construction (D's entry/transformer logic is stubbed and a
real-order run would need the coexistence MUST-FIXes), the undefined-risk
contract flag, the BOT_NAME, the DCPhase enum, and the multi-day open-position
predicate that the lifecycle overrides depend on.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from bots.hydra.base_strategy import ConfigError
from bots.hydra.double_calendar_strategy import CalendarEntry, DCPhase, DoubleCalendarStrategy
from bots.hydra.strategy import HydraStrategy


class TestDryRunLock:
    """The scaffold must refuse non-dry-run construction BEFORE any broker I/O —
    same guard pattern as StrangleStrategy."""

    def _patch_super_init(self, monkeypatch):
        # Lightweight super().__init__ that only sets dry_run (avoids the heavy
        # real HydraStrategy construction; we're testing D's gate, not the base).
        def fake_init(self, *a, **k):
            self.dry_run = k.get("dry_run", False)
        monkeypatch.setattr(HydraStrategy, "__init__", fake_init)

    def test_refuses_non_dry_run(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        with pytest.raises(ConfigError):
            DoubleCalendarStrategy(None, {}, None, dry_run=False)

    def test_refuses_when_dry_run_kwarg_absent(self, monkeypatch):
        # build_strategy always passes dry_run; absence must also be refused
        # (the lock reads kwargs.get("dry_run", False) BEFORE super()).
        self._patch_super_init(monkeypatch)
        with pytest.raises(ConfigError):
            DoubleCalendarStrategy(None, {}, None)

    def test_allows_dry_run(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        s = DoubleCalendarStrategy(None, {}, None, dry_run=True)
        assert isinstance(s, DoubleCalendarStrategy)
        assert s.dry_run is True


class TestContract:
    def test_is_undefined_risk(self):
        # Debit calendar / transformer manages its own structure — the base
        # naked-short emergency path must stay off.
        assert DoubleCalendarStrategy.requires_protective_wings is False

    def test_bot_name(self):
        assert DoubleCalendarStrategy.BOT_NAME == "DCTM"

    def test_dcphase_values(self):
        assert {p.value for p in DCPhase} == {"calendar", "transformed", "closed"}


class TestMultiDayPredicate:
    """_dc_entry_is_open drives the lifecycle carve-outs (carry-forward across
    the daily reset; settlement treating a held position as normal)."""

    def _inst(self):
        # Build without __init__ — the predicate only reads entry.dc_phase.
        return DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)

    def test_open_phases_are_open(self):
        inst = self._inst()
        assert inst._dc_entry_is_open(SimpleNamespace(dc_phase=DCPhase.CALENDAR)) is True
        assert inst._dc_entry_is_open(SimpleNamespace(dc_phase=DCPhase.TRANSFORMED)) is True

    def test_closed_or_unset_is_not_open(self):
        inst = self._inst()
        assert inst._dc_entry_is_open(SimpleNamespace(dc_phase=DCPhase.CLOSED)) is False
        # A recovered/plain entry with no dc_phase must NOT be treated as open
        # (documents the known restart-fragility gap; predicate is conservative).
        assert inst._dc_entry_is_open(SimpleNamespace(dc_phase=None)) is False
        assert inst._dc_entry_is_open(SimpleNamespace()) is False


class TestResolveCalendarLegs:
    """Phase 2: resolve 4 conids across two expiries via the existing
    _get_option_uic (called with EXPLICIT non-0DTE expiries)."""

    def _inst(self, uic_map):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        # Shadow the bound method with a fake keyed on (strike, right, expiry).
        inst._get_option_uic = lambda strike, right, expiry: uic_map.get((strike, right, expiry))
        return inst

    def test_resolves_all_four(self):
        m = {
            (5000.0, "Call", "2026-06-26"): 11,
            (5000.0, "Call", "2026-06-29"): 12,
            (4900.0, "Put", "2026-06-26"): 21,
            (4900.0, "Put", "2026-06-29"): 22,
        }
        inst = self._inst(m)
        legs = inst._dc_resolve_calendar_legs(5000.0, 4900.0, "2026-06-26", "2026-06-29")
        assert legs == {"short_call": 11, "long_call": 12, "short_put": 21, "long_put": 22}

    def test_none_when_a_leg_unresolved(self):
        m = {
            (5000.0, "Call", "2026-06-26"): 11,
            (5000.0, "Call", "2026-06-29"): 12,
            (4900.0, "Put", "2026-06-26"): 21,
            # long_put missing -> None
        }
        inst = self._inst(m)
        assert inst._dc_resolve_calendar_legs(5000.0, 4900.0, "2026-06-26", "2026-06-29") is None


class TestReadIV:
    def _inst(self, greeks_return=None, raise_exc=False):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.broker = MagicMock()
        if raise_exc:
            inst.broker.get_option_greeks.side_effect = RuntimeError("boom")
        else:
            inst.broker.get_option_greeks.return_value = greeks_return
        return inst

    def test_valid_iv(self):
        assert self._inst({"iv": 0.18})._dc_read_iv(1) == 0.18

    def test_zero_iv_is_none(self):
        assert self._inst({"iv": 0.0})._dc_read_iv(1) is None

    def test_missing_iv_is_none(self):
        assert self._inst({"iv": None})._dc_read_iv(1) is None
        assert self._inst({})._dc_read_iv(1) is None

    def test_broker_error_is_none(self):
        assert self._inst(raise_exc=True)._dc_read_iv(1) is None


class TestFrontBackIV:
    def _inst(self, conids, iv_by_conid):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst._get_option_uic = lambda strike, right, expiry: conids.get(expiry)
        inst.broker = MagicMock()
        inst.broker.get_option_greeks.side_effect = lambda c: {"iv": iv_by_conid.get(c)}
        return inst

    def test_returns_front_back_pair(self):
        inst = self._inst(
            conids={"2026-06-26": 1, "2026-06-29": 2},
            iv_by_conid={1: 0.25, 2: 0.20},
        )
        assert inst._dc_front_back_iv(5000.0, "Call", "2026-06-26", "2026-06-29") == (0.25, 0.20)

    def test_none_when_back_iv_missing(self):
        inst = self._inst(
            conids={"2026-06-26": 1, "2026-06-29": 2},
            iv_by_conid={1: 0.25, 2: None},
        )
        assert inst._dc_front_back_iv(5000.0, "Call", "2026-06-26", "2026-06-29") is None

    def test_none_when_conid_unresolved(self):
        inst = self._inst(conids={"2026-06-26": 1}, iv_by_conid={1: 0.25})
        # long expiry has no conid -> None before any greeks read
        assert inst._dc_front_back_iv(5000.0, "Call", "2026-06-26", "2026-06-29") is None


class TestMinBuyingPower:
    def test_reads_config_calendar_floor(self):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.strategy_config = {"min_buying_power_per_calendar": 1234.0}
        assert inst._min_buying_power_per_unit() == 1234.0

    def test_default_floor_when_absent(self):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.strategy_config = {}
        assert inst._min_buying_power_per_unit() == 2000.0


class TestDeltaTargetStrike:
    """Phase 3: 30-40delta OTM strike selection from per-strike greeks."""

    def _inst(self, right):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.strike_increment = 5
        inst.dc_target_delta = 0.35
        inst.dc_delta_band = (0.30, 0.40)
        inst._get_option_uic = lambda strike, r, expiry: int(strike)  # conid = strike
        inst.broker = MagicMock()

        def delta_for(conid):
            strike = float(conid)
            if right == "Call":
                d = 0.5 - (strike - 5000) * 0.002   # falls as strike rises
            else:
                d = 0.5 - (5000 - strike) * 0.002   # falls as strike drops
            return {"delta": d}

        inst.broker.get_option_greeks.side_effect = delta_for
        return inst

    def test_call_picks_closest_to_target(self):
        # delta 0.35 at strike 5075 (0.5 - 75*0.002)
        assert self._inst("Call")._dc_delta_target_strike(5000.0, "Call", "2026-06-26") == 5075.0

    def test_put_picks_closest_to_target(self):
        # delta 0.35 at strike 4925 (0.5 - 75*0.002)
        assert self._inst("Put")._dc_delta_target_strike(5000.0, "Put", "2026-06-26") == 4925.0

    def test_none_when_band_unreachable(self):
        inst = self._inst("Call")
        inst.dc_delta_band = (0.01, 0.02)  # deltas never get this low in 40 steps
        assert inst._dc_delta_target_strike(5000.0, "Call", "2026-06-26") is None


class TestCalculateStrikes:
    def _inst(self, kc=5075.0, kp=4925.0, expiries=("2026-06-26", "2026-06-29")):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.current_price = 5000.0
        inst.dc_target_delta = 0.35
        inst.dc_delta_band = (0.30, 0.40)
        inst._dc_pick_expiries = lambda: expiries
        inst._dc_delta_target_strike = lambda spx, right, exp: kc if right == "Call" else kp
        return inst

    def test_sets_strikes_and_expiries(self):
        inst = self._inst()
        e = CalendarEntry(entry_number=1)
        assert inst._calculate_strikes(e) is True
        # calendar: short+long of a side share the strike
        assert e.short_call_strike == 5075.0 and e.long_call_strike == 5075.0
        assert e.short_put_strike == 4925.0 and e.long_put_strike == 4925.0
        # but differ in expiry
        assert e.short_expiry == "2026-06-26" and e.long_expiry == "2026-06-29"

    def test_false_when_no_expiry_pair(self):
        inst = self._inst()
        inst._dc_pick_expiries = lambda: None
        assert inst._calculate_strikes(CalendarEntry(entry_number=1)) is False

    def test_false_when_no_delta_strike(self):
        inst = self._inst()
        inst._dc_delta_target_strike = lambda spx, right, exp: None
        assert inst._calculate_strikes(CalendarEntry(entry_number=1)) is False


class TestSimulateEntry:
    def _inst(self, mids):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.contracts_per_entry = 1
        inst._dc_resolve_calendar_legs = lambda kc, kp, se, le: {
            "short_call": 11, "long_call": 12, "short_put": 21, "long_put": 22,
        }
        inst._dc_read_leg_quotes = lambda conids: {k: {"mid": mids[k]} for k in conids}
        return inst

    def _entry(self):
        e = CalendarEntry(entry_number=1)
        e.short_call_strike = e.long_call_strike = 5075.0
        e.short_put_strike = e.long_put_strike = 4925.0
        e.legs["short_call"].expiry = "2026-06-26"
        e.legs["long_call"].expiry = "2026-06-29"
        e.legs["short_put"].expiry = "2026-06-26"
        e.legs["long_put"].expiry = "2026-06-29"
        return e

    def test_net_debit_and_fills(self):
        inst = self._inst({"short_call": 2.0, "long_call": 3.0, "short_put": 2.5, "long_put": 3.5})
        e = self._entry()
        assert inst._dc_simulate_entry(e) is True
        # debit = (long_call + long_put - short_call - short_put)*100*1 = (3+3.5-2-2.5)*100 = 200
        assert e.net_debit == 200.0
        assert e.short_call_uic == 11 and e.long_put_uic == 22
        assert e.short_call_fill_price == 2.0
        assert e.is_complete is True
        assert e.dc_phase == DCPhase.CALENDAR
        assert e.short_call_position_id.startswith("DRY_") and e.short_call_position_id.endswith("SC")

    def test_false_on_missing_mid(self):
        inst = self._inst({"short_call": 2.0, "long_call": 0.0, "short_put": 2.5, "long_put": 3.5})
        assert inst._dc_simulate_entry(self._entry()) is False


class TestInitiateEntry:
    def _inst(self):
        from bots.hydra.base_strategy import MEICDailyState
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst._next_entry_index = 0
        inst.daily_state = MEICDailyState()
        inst.dry_run = True
        inst.contracts_per_entry = 1
        inst.commission_per_leg = 1.15
        inst._entry_in_progress = False
        inst._dc_pre_entry_gates = lambda n: None
        inst._save_state_to_disk = lambda: None

        def fake_strikes(e):
            e.short_call_strike = e.long_call_strike = 5075.0
            e.short_put_strike = e.long_put_strike = 4925.0
            return True

        def fake_sim(e):
            e.net_debit = 200.0
            e.is_complete = True
            return True

        inst._calculate_strikes = fake_strikes
        inst._dc_simulate_entry = fake_sim
        return inst

    def test_happy_path_books_calendar_entry(self):
        inst = self._inst()
        msg = inst._initiate_entry()
        assert "DC Time Machine" in msg
        assert len(inst.daily_state.entries) == 1
        assert isinstance(inst.daily_state.entries[0], CalendarEntry)
        assert inst.daily_state.entries_completed == 1
        assert inst._next_entry_index == 1
        # 4-leg open commission
        assert inst.daily_state.total_commission == 4 * 1.15 * 1

    def test_skips_when_strikes_fail(self):
        inst = self._inst()
        inst._calculate_strikes = lambda e: False
        msg = inst._initiate_entry()
        assert "skipped" in msg
        assert len(inst.daily_state.entries) == 0
        assert inst.daily_state.entries_skipped == 1
