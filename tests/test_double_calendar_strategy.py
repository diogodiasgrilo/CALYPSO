"""DoubleCalendarStrategy (Strategy D — "DC Time Machine") scaffold tests.

Pins the SAFETY-critical scaffold behavior: the dry-run lock refuses any
non-dry-run construction (D's entry/transformer logic is stubbed and a
real-order run would need the coexistence MUST-FIXes), the undefined-risk
contract flag, the BOT_NAME, the DCPhase enum, and the multi-day open-position
predicate that the lifecycle overrides depend on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from bots.hydra.base_strategy import ConfigError, IronCondorEntry
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


class TestPickExpiriesListedFilter:
    """Live-found bug (2026-06-15): SPXW has expiry GAPS (Jun 29 not listed, Jun 30
    is). _dc_pick_expiries must filter generated weekdays to actually-listed
    chains so it never selects an unlisted expiry."""

    def test_skips_unlisted_long_picks_listed(self, monkeypatch):
        from datetime import datetime
        monkeypatch.setattr(
            "bots.hydra.double_calendar_strategy.get_us_market_time",
            lambda: datetime(2026, 6, 15, 10, 0),  # Monday
        )
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.dc_short_dte_min, inst.dc_short_dte_max = 6, 15
        inst.dc_long_extra_dte_min, inst.dc_long_extra_dte_max = 1, 4
        inst.dc_prefer_friday = True
        inst.broker = MagicMock()
        # Jun 29 (the natural +3 long) is NOT listed; everything else is.
        inst.broker.get_option_chain.side_effect = (
            lambda sym, iso, tc, exch: [] if iso == "2026-06-29" else [7560.0, 7565.0]
        )
        # short = Fri Jun 26 (11 DTE); long must skip the unlisted Jun 29 -> Jun 30.
        assert inst._dc_pick_expiries() == ("2026-06-26", "2026-06-30")


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
        inst.dc_wing_width = 5.0
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

    def test_rejects_net_credit_calendar(self):
        # Longs cheaper than shorts -> net_debit <= 0 (inverted term structure):
        # not a valid calendar; must skip rather than open an unmanaged position.
        inst = self._inst({"short_call": 4.0, "long_call": 2.0, "short_put": 4.0, "long_put": 2.0})
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


def _calendar_entry(net_debit=200.0):
    e = CalendarEntry(entry_number=1)
    e.contracts = 1
    e.net_debit = net_debit
    e.short_call_strike = e.long_call_strike = 5075.0
    e.short_put_strike = e.long_put_strike = 4925.0
    e.legs["short_call"].uic = 11
    e.legs["long_call"].uic = 12
    e.legs["short_put"].uic = 21
    e.legs["long_put"].uic = 22
    for nm, exp in (("short_call", "2026-06-26"), ("long_call", "2026-06-29"),
                    ("short_put", "2026-06-26"), ("long_put", "2026-06-29")):
        e.legs[nm].expiry = exp
    e.dc_phase = DCPhase.CALENDAR
    return e


class TestTransformer:
    def _inst(self, mids):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.contracts_per_entry = 1
        inst.dc_wing_width = 5.0
        # wing conids resolve (Call->9001, Put->9002)
        inst._get_option_uic = lambda strike, right, exp: 9001 if right == "Call" else 9002
        inst._dc_read_leg_quotes = lambda conids: {k: {"mid": mids[k]} for k in conids}
        return inst

    def test_fires_when_risk_free(self):
        # credit = (lc+lp - wc-wp)*100 = (4+4-0.5-0.5)*100 = 700; threshold = 200 + 5*100 = 700
        mids = {"long_call": 4.0, "long_put": 4.0, "wing_call": 0.5, "wing_put": 0.5,
                "short_call": 2.0, "short_put": 2.0}
        inst = self._inst(mids)
        e = _calendar_entry(net_debit=200.0)
        assert inst._dc_attempt_transform(e) is True
        assert e.dc_phase == DCPhase.TRANSFORMED
        assert e.long_call_strike == 5080.0 and e.long_put_strike == 4920.0  # wings
        assert e.transform_credit == 700.0
        assert e.is_risk_free is True
        # IC credit = (short - wing) per side: (2 - 0.5)*100 = 150 each -> 300
        assert e.total_credit == 300.0

    def test_holds_when_not_risk_free(self):
        # wings expensive -> credit (4+4-2-2)*100 = 400 < 700 threshold
        mids = {"long_call": 4.0, "long_put": 4.0, "wing_call": 2.0, "wing_put": 2.0,
                "short_call": 2.0, "short_put": 2.0}
        inst = self._inst(mids)
        e = _calendar_entry(net_debit=200.0)
        assert inst._dc_attempt_transform(e) is False
        assert e.dc_phase == DCPhase.CALENDAR  # unchanged
        assert e.transform_credit == 0.0

    def test_holds_when_wing_unresolved(self):
        inst = self._inst({})
        inst._get_option_uic = lambda strike, right, exp: None
        e = _calendar_entry()
        assert inst._dc_attempt_transform(e) is False
        assert e.dc_phase == DCPhase.CALENDAR


class TestCloseCalendar:
    def _inst(self):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        from bots.hydra.base_strategy import MEICDailyState
        inst.daily_state = MEICDailyState()
        inst.contracts_per_entry = 1
        inst.commission_per_leg = 1.15
        inst._dc_refresh_marks = lambda e: None  # keep the prices we set
        return inst

    def _entry_with_pnl(self, pnl):
        # net_debit 200; set leg prices so calendar_value = net_debit + pnl
        e = _calendar_entry(net_debit=200.0)
        target_value = (200.0 + pnl) / 100.0  # per-contract points
        # split across call/put evenly: each side contributes target_value/2 of (long-short)
        half = target_value / 2
        e.legs["short_call"].price = 2.0
        e.legs["long_call"].price = 2.0 + half
        e.legs["short_put"].price = 2.0
        e.legs["long_put"].price = 2.0 + half
        return e

    def test_stop_books_loss_and_flags(self):
        inst = self._inst()
        e = self._entry_with_pnl(-50.0)
        inst._dc_close_calendar(e, reason="20%-debit stop", loss=True)
        assert e.dc_phase == DCPhase.CLOSED
        assert e.call_side_stopped and e.put_side_stopped
        assert inst.daily_state.total_realized_pnl == pytest.approx(-50.0)
        assert e.close_commission == pytest.approx(4 * 1.15 * 1)

    def test_eod_close_uses_pivot_flags(self):
        inst = self._inst()
        e = self._entry_with_pnl(10.0)
        inst._dc_close_calendar(e, reason="EOD no-transform", loss=False)
        assert e.dc_phase == DCPhase.CLOSED
        assert e.call_side_pivot_closed and e.put_side_pivot_closed
        assert inst.daily_state.total_realized_pnl == pytest.approx(10.0)


class TestManageCalendar:
    def _inst(self, pnl, transform_ok=True, past_eod=False):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.dc_profit_trigger_pct = 0.075
        inst.dc_pre_transform_stop_pct = 0.20
        inst.dc_eod_close_if_no_transform = True
        inst._dc_refresh_marks = lambda e: True  # marks fresh this tick
        inst._dc_attempt_transform = lambda e: transform_ok
        inst._dc_past_eod_cutoff = lambda: past_eod
        self._closed = []
        inst._dc_close_calendar = lambda e, reason, loss: self._closed.append((reason, loss))
        return inst

    def _entry(self, pnl, net_debit=200.0):
        e = _calendar_entry(net_debit=net_debit)
        half = ((net_debit + pnl) / 100.0) / 2
        e.legs["short_call"].price = 2.0
        e.legs["long_call"].price = 2.0 + half
        e.legs["short_put"].price = 2.0
        e.legs["long_put"].price = 2.0 + half
        return e

    def test_profit_triggers_transform(self):
        inst = self._inst(pnl=20.0, transform_ok=True)  # +10% of 200
        msg = inst._dc_manage_calendar(self._entry(20.0))
        assert msg == "TRANSFORM E#1"

    def test_loss_triggers_stop(self):
        inst = self._inst(pnl=-50.0)  # -25% of 200
        msg = inst._dc_manage_calendar(self._entry(-50.0))
        assert msg == "STOP E#1"
        assert self._closed == [("20%-debit stop", True)]

    def test_eod_close_when_flat_and_past_cutoff(self):
        inst = self._inst(pnl=0.0, past_eod=True)
        msg = inst._dc_manage_calendar(self._entry(0.0))
        assert msg == "EOD-CLOSE E#1"
        assert self._closed == [("EOD no-transform", False)]

    def test_nothing_when_flat_and_not_eod(self):
        inst = self._inst(pnl=0.0, past_eod=False)
        assert inst._dc_manage_calendar(self._entry(0.0)) is None
        assert self._closed == []

    def test_stale_marks_skip_transform_and_stop(self):
        # A profit that WOULD transform, but marks aren't fresh this tick -> no
        # transform/stop fires (acting on a stale leg could be wrong).
        inst = self._inst(pnl=20.0, transform_ok=True)
        inst._dc_refresh_marks = lambda e: False  # not fresh
        assert inst._dc_manage_calendar(self._entry(20.0)) is None
        assert self._closed == []

    def test_stale_marks_still_allow_eod_close(self):
        # EOD close is time-based and must run even when marks are stale.
        inst = self._inst(pnl=0.0, past_eod=True)
        inst._dc_refresh_marks = lambda e: False
        assert inst._dc_manage_calendar(self._entry(0.0)) == "EOD-CLOSE E#1"
        assert self._closed == [("EOD no-transform", False)]


class TestSidecarLoadGuard:
    def test_save_is_noop_before_load(self, tmp_path):
        from bots.hydra.base_strategy import MEICDailyState
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.state_file = str(tmp_path / "hydra_state.json")
        inst.daily_state = MEICDailyState()
        inst.daily_state.entries.append(_calendar_entry())
        writes = []
        inst._atomic_write_json = lambda path, data: writes.append((path, data))
        # _dc_loaded not set / False -> save must NOT write (would clobber sidecar)
        inst._dc_save_sidecar()
        assert writes == []
        # after load arms it, save writes
        inst._dc_loaded = True
        inst._dc_save_sidecar()
        assert len(writes) == 1


def _transformed_entry():
    """A risk-free transformed CalendarEntry: debit 200, transform credit 700,
    wing 5pt, IC at C 5075/5080 P 4925/4920, short expiry 2026-06-19."""
    e = _calendar_entry(net_debit=200.0)
    e.dc_phase = DCPhase.TRANSFORMED
    e.transform_credit = 700.0
    e.wing_width = 5.0
    e.is_risk_free = True
    e.long_call_strike = 5080.0  # wing
    e.long_put_strike = 4920.0   # wing
    return e


class TestPersistence:
    def _inst(self, tmp_path):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.state_file = str(tmp_path / "hydra_state.json")
        inst._atomic_write_json = lambda path, data: Path(path).write_text(json.dumps(data))
        inst._dc_loaded = True  # past the startup load-guard window
        from bots.hydra.base_strategy import MEICDailyState
        inst.daily_state = MEICDailyState()
        return inst

    def test_serialize_deserialize_round_trip(self):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        e = _transformed_entry()
        e.strategy_id = "dctm_20260615_001"
        e.legs["short_call"].uic = 11
        e.legs["long_call"].expiry = "2026-06-19"
        e.legs["short_call"].expiry = "2026-06-19"
        d = inst._dc_serialize_entry(e)
        e2 = inst._dc_deserialize_entry(d)
        assert e2.dc_phase == DCPhase.TRANSFORMED
        assert e2.net_debit == 200.0 and e2.transform_credit == 700.0
        assert e2.wing_width == 5.0 and e2.is_risk_free is True
        assert e2.strategy_id == "dctm_20260615_001"
        assert e2.short_call_strike == 5075.0 and e2.long_call_strike == 5080.0
        assert e2.short_call_uic == 11
        assert e2.short_expiry == "2026-06-19"

    def test_save_then_load_round_trip(self, tmp_path):
        inst = self._inst(tmp_path)
        e = _transformed_entry()
        e.strategy_id = "dctm_20260615_001"
        inst.daily_state.entries.append(e)
        inst._dc_save_sidecar()
        assert Path(inst._dc_state_path()).exists()

        # fresh instance loads the sidecar back as a CalendarEntry
        inst2 = self._inst(tmp_path)
        assert inst2._dc_load_sidecar() is True
        assert len(inst2.daily_state.entries) == 1
        loaded = inst2.daily_state.entries[0]
        assert isinstance(loaded, CalendarEntry)
        assert loaded.dc_phase == DCPhase.TRANSFORMED
        assert loaded.transform_credit == 700.0

    def test_sidecar_excludes_closed(self, tmp_path):
        inst = self._inst(tmp_path)
        open_e = _transformed_entry(); open_e.strategy_id = "open"
        closed_e = _calendar_entry(); closed_e.strategy_id = "closed"; closed_e.dc_phase = DCPhase.CLOSED
        inst.daily_state.entries += [open_e, closed_e]
        inst._dc_save_sidecar()
        records = json.loads(Path(inst._dc_state_path()).read_text())
        assert [r["strategy_id"] for r in records] == ["open"]

    def test_load_drops_base_loaded_ic_version(self, tmp_path):
        # Simulate the base load having reconstructed an IC-shaped version of the
        # same trade (same strategy_id) — the sidecar load must replace it.
        inst = self._inst(tmp_path)
        e = _transformed_entry(); e.strategy_id = "dup"
        inst.daily_state.entries.append(e)
        inst._dc_save_sidecar()

        inst2 = self._inst(tmp_path)
        ic_version = IronCondorEntry(entry_number=1)
        ic_version.strategy_id = "dup"
        inst2.daily_state.entries.append(ic_version)
        inst2._dc_load_sidecar()
        # exactly one entry, and it's the CalendarEntry (not the IC version)
        assert len(inst2.daily_state.entries) == 1
        assert isinstance(inst2.daily_state.entries[0], CalendarEntry)


class TestSettlement:
    def _inst(self):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        from bots.hydra.base_strategy import MEICDailyState
        inst.daily_state = MEICDailyState()
        inst.contracts_per_entry = 1
        inst.current_price = 5000.0
        inst._save_state_to_disk = lambda: None  # _dc_settle_due persists via this
        return inst

    def test_transformed_settles_otm_keeps_full_locked_pnl(self):
        inst = self._inst()
        e = _transformed_entry()
        inst._dc_settle_entry(e, spx=5000.0)  # between strikes -> intrinsic 0
        assert e.dc_phase == DCPhase.CLOSED
        assert e.call_side_expired and e.put_side_expired
        # realized = transform_credit - net_debit - 0 = 500
        assert inst.daily_state.total_realized_pnl == pytest.approx(500.0)

    def test_transformed_settles_itm_floors_at_zero(self):
        inst = self._inst()
        e = _transformed_entry()
        inst._dc_settle_entry(e, spx=5100.0)  # call side deep ITM -> intrinsic = wing
        # realized = 700 - 200 - (5*100) = 0  (risk-free floor)
        assert inst.daily_state.total_realized_pnl == pytest.approx(0.0)

    def test_settle_due_settles_past_skips_future(self):
        inst = self._inst()
        due = _transformed_entry(); due.strategy_id = "due"
        due.legs["short_call"].expiry = "2026-06-19"  # short_expiry in the past
        future = _transformed_entry(); future.strategy_id = "future"
        future.legs["short_call"].expiry = "2026-07-10"  # not yet
        inst.daily_state.entries += [due, future]
        assert inst._dc_settle_due("2026-06-26") is True
        assert due.dc_phase == DCPhase.CLOSED
        assert future.dc_phase == DCPhase.TRANSFORMED  # untouched

    def test_settle_due_defers_when_spx_unreadable(self):
        inst = self._inst()
        inst.current_price = 0
        due = _transformed_entry()
        due.legs["short_call"].expiry = "2026-06-19"
        inst.daily_state.entries.append(due)
        assert inst._dc_settle_due("2026-06-26") is False
        assert due.dc_phase == DCPhase.TRANSFORMED  # not settled
