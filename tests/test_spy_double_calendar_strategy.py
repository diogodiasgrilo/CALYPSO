"""SpyDoubleCalendarStrategy (Strategy E — "SPY Double Calendar") tests.

Pins the SAFETY-critical scaffold behavior + E's genuinely-new management logic:
the dry-run lock refuses non-dry-run construction (E is a coexistence-unsafe
multi-day debit scaffold and a real-order run would need the MUST-FIXes + SPY
American-assignment handling), the BOT_NAME / undefined-risk flag, the
absolute-DTE expiry picker, the expected-move strike selection (±1×EM, $1 grid,
both expiries), the net-debit simulated entry, LADDERED profit-taking (10-lot
scale-out at rising ROR; 1-lot full-exit at the first met rung), the trading-day
TIME-EXIT (never holds through expiry), the no-stop max-loss (= full debit), and
the low-IV entry gate.

Uses the ``S.__new__(S)`` + attribute-setting pattern (mirrors
tests/test_dc_stop_hardening.py) so we exercise E's methods WITHOUT the heavy
real HydraStrategy construction.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import bots.hydra.spy_double_calendar_strategy as spydc
from bots.hydra.spy_double_calendar_strategy import SpyDoubleCalendarStrategy as E
from bots.hydra.base_strategy import ConfigError
from bots.hydra.calendar_entry import CalendarEntry, DCPhase
from bots.hydra.strategy import HydraStrategy


# ---------------------------------------------------------------------------
# Dry-run lock (the safety gate) — refuses non-dry-run BEFORE super().__init__
# ---------------------------------------------------------------------------

class TestDryRunLock:
    def _patch_super_init(self, monkeypatch):
        # Lightweight super().__init__ that only sets dry_run + the attributes E's
        # __init__ reads after super (avoids the heavy real construction).
        def fake_init(self, *a, **k):
            self.dry_run = k.get("dry_run", False)
            self.strategy_config = {}
            self.state_file = "/tmp/spydc_test/hydra_state.json"
        monkeypatch.setattr(HydraStrategy, "__init__", fake_init)

    def test_refuses_non_dry_run(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        with pytest.raises(ConfigError):
            E(None, {}, None, dry_run=False)

    def test_refuses_when_dry_run_kwarg_absent(self, monkeypatch):
        # build_strategy always passes dry_run; absence must also be refused
        # (the lock reads kwargs.get("dry_run", False) BEFORE super()).
        self._patch_super_init(monkeypatch)
        with pytest.raises(ConfigError):
            E(None, {}, None)

    def test_allows_dry_run(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        s = E(None, {}, None, dry_run=True)
        assert isinstance(s, E)
        assert s.dry_run is True
        # Knobs the inherited base methods read must be set on E.
        assert s.dc_wing_width == 0.0
        assert s.dc_delta_otm_fraction == s.spy_dc_em_fraction
        assert s._dc_stop_breach == {}


# ---------------------------------------------------------------------------
# Class contract
# ---------------------------------------------------------------------------

class TestContract:
    def test_bot_name(self):
        assert E.BOT_NAME == "SPYDC"

    def test_is_undefined_risk_flag_off(self):
        # Debit double calendar — the long legs are the protection; the base
        # naked-short emergency path must stay off.
        assert E.requires_protective_wings is False


# ---------------------------------------------------------------------------
# Helpers for the attribute-set instance pattern
# ---------------------------------------------------------------------------

def _strat(ladder=None, time_exit=2, em_fraction=1.0, max_vix=22.0):
    s = E.__new__(E)
    s.spy_dc_short_dte_target = 35
    s.spy_dc_short_dte_tolerance = 5
    s.spy_dc_long_gap_days = 7
    s.spy_dc_long_gap_tolerance = 3
    s.spy_dc_em_fraction = em_fraction
    s.spy_dc_max_vix_entry = max_vix
    s.spy_dc_profit_ladder = sorted(
        ladder or [
            {"ror": 0.20, "frac": 0.25},
            {"ror": 0.50, "frac": 0.25},
            {"ror": 1.00, "frac": 0.25},
            {"ror": 2.00, "frac": 0.25},
        ],
        key=lambda x: x["ror"],
    )
    s.spy_dc_time_exit_days_before = time_exit
    s.spy_dc_max_concurrent = 1
    s.spy_dc_max_deployed_debit = 5000.0
    s.dc_wing_width = 0.0
    s.dc_require_realtime_quotes = False
    s.dc_delta_otm_fraction = em_fraction
    s._dc_stop_breach = {}
    s._spy_dc_closed_contracts = {}
    s.strike_increment = 1
    s.contracts_per_entry = 1
    s.commission_per_leg = 0.65
    s.current_price = 600.0
    s.current_vix = 14.0
    s._dc_recorder = None
    s.daily_state = MagicMock()
    s.daily_state.active_entries = []
    s.daily_state.entries = []
    s.daily_state.entries_skipped = 0
    s.daily_state.total_realized_pnl = 0.0
    s.daily_state.total_commission = 0.0
    s._next_entry_index = 0
    # Persistence + leg-clear are no-ops in these unit tests.
    s._save_state_to_disk = lambda: None
    s._dc_clear_leg_conids = lambda e: None
    return s


class _ForcedUpnlCalendarEntry(CalendarEntry):
    """A CalendarEntry whose ``unrealized_pnl`` returns ``_force_upnl`` — lets tests
    set the mark-to-market P&L directly without wiring the 4-leg price math."""

    _force_upnl: float = 0.0

    @property
    def unrealized_pnl(self) -> float:  # type: ignore[override]
        return self._force_upnl


def _open_entry(net_debit=1000.0, upnl=0.0, contracts=1, num=1, short_exp="2026-07-20"):
    e = _ForcedUpnlCalendarEntry(entry_number=num)
    e.net_debit = net_debit
    e._force_upnl = upnl
    e.contracts = contracts
    e.original_contracts = contracts
    e.dc_phase = DCPhase.CALENDAR
    e.entry_time = datetime(2026, 6, 16, 14, 0, tzinfo=timezone.utc)
    # short_call leg carries the short expiry (CalendarEntry.short_expiry reads it).
    e.legs["short_call"].expiry = short_exp
    e.legs["long_call"].expiry = "2026-07-27"
    e.legs["short_put"].expiry = short_exp
    e.legs["long_put"].expiry = "2026-07-27"
    return e


# ---------------------------------------------------------------------------
# Expiry picker — short ~35 DTE, long ~+1 week
# ---------------------------------------------------------------------------

def test_pick_expiries_short_35_long_plus_week(monkeypatch):
    s = _strat()
    today = datetime(2026, 6, 16, 10, 0, tzinfo=timezone.utc)  # a Tuesday
    monkeypatch.setattr(spydc, "get_us_market_time", lambda: today)
    picked = s._pick_expiries()
    assert picked is not None
    short_exp, longs = picked
    t = today.date()
    short_dte = (date.fromisoformat(short_exp) - t).days
    assert 30 <= short_dte <= 40  # 35 ± 5
    assert longs, "expected at least one long candidate"
    long_gap = (date.fromisoformat(longs[0]) - date.fromisoformat(short_exp)).days
    assert 4 <= long_gap <= 10  # 7 ± 3


# ---------------------------------------------------------------------------
# Strike selection — ±1×EM, $1 grid, both expiries
# ---------------------------------------------------------------------------

def test_calculate_strikes_em_based(monkeypatch):
    s = _strat(em_fraction=1.0)
    s.current_price = 600.0
    s.current_vix = 16.0
    short_exp, long_exp = "2026-07-20", "2026-07-27"
    monkeypatch.setattr(s, "_pick_expiries", lambda: (short_exp, [long_exp]))
    # Resolve always succeeds (legs listed on both expiries).
    monkeypatch.setattr(
        s, "_dc_resolve_calendar_legs",
        lambda kc, kp, se, le: {"short_call": 1, "long_call": 2, "short_put": 3, "long_put": 4},
    )
    entry = CalendarEntry(entry_number=1)
    assert s._calculate_strikes(entry) is True
    # EM at ~16 VIX, ~34 DTE: spot*(vix/100)*sqrt(dte/365) ~ 600*0.16*0.305 ~ 29pt.
    em = s._dc_em_otm_distance(600.0, short_exp)
    assert entry.short_call_strike == round((600.0 + em) / 1) * 1
    assert entry.short_put_strike == round((600.0 - em) / 1) * 1
    assert entry.short_call_strike > 600.0  # call above spot
    assert entry.short_put_strike < 600.0   # put below spot
    # $1 grid (SPY): strikes are whole numbers.
    assert entry.short_call_strike == int(entry.short_call_strike)
    assert entry.short_put_strike == int(entry.short_put_strike)
    # Same strike per leg; both expiries stamped.
    assert entry.short_call_strike == entry.long_call_strike
    assert entry.short_put_strike == entry.long_put_strike
    assert entry.legs["short_call"].expiry == short_exp
    assert entry.legs["long_call"].expiry == long_exp


def test_calculate_strikes_skips_when_not_listed_on_both(monkeypatch):
    s = _strat()
    monkeypatch.setattr(s, "_pick_expiries", lambda: ("2026-07-20", ["2026-07-27", "2026-07-28"]))
    monkeypatch.setattr(s, "_dc_resolve_calendar_legs", lambda *a, **k: None)  # never listed
    entry = CalendarEntry(entry_number=1)
    assert s._calculate_strikes(entry) is False


# ---------------------------------------------------------------------------
# Net-debit simulated entry (via the inherited base _dc_simulate_entry)
# ---------------------------------------------------------------------------

def test_simulate_entry_is_net_debit(monkeypatch):
    s = _strat()
    entry = CalendarEntry(entry_number=1)
    entry.contracts = 1
    entry.short_call_strike = entry.long_call_strike = 630.0
    entry.short_put_strike = entry.long_put_strike = 570.0
    entry.legs["short_call"].expiry = entry.legs["short_put"].expiry = "2026-07-20"
    entry.legs["long_call"].expiry = entry.legs["long_put"].expiry = "2026-07-27"
    monkeypatch.setattr(
        s, "_dc_resolve_calendar_legs",
        lambda kc, kp, se, le: {"short_call": 1, "long_call": 2, "short_put": 3, "long_put": 4},
    )
    # Longs dearer than shorts -> net DEBIT. mids in option points.
    quotes = {
        "short_call": {"mid": 2.0, "raw": {}, "realtime": True},
        "long_call": {"mid": 3.5, "raw": {}, "realtime": True},
        "short_put": {"mid": 2.0, "raw": {}, "realtime": True},
        "long_put": {"mid": 3.5, "raw": {}, "realtime": True},
    }
    monkeypatch.setattr(s, "_dc_read_leg_quotes", lambda conids: quotes)
    monkeypatch.setattr(spydc, "get_us_market_time", lambda: datetime(2026, 6, 16, 14, 0, tzinfo=timezone.utc))
    assert s._dc_simulate_entry(entry) is True
    # (3.5+3.5-2.0-2.0)*100*1 = 300
    assert entry.net_debit == pytest.approx(300.0)
    assert entry.dc_phase == DCPhase.CALENDAR


# ---------------------------------------------------------------------------
# Laddered profit-taking
# ---------------------------------------------------------------------------

def _wire_partial_close(s, calls):
    """Replace _spy_dc_partial_close with a recorder that mutates entry.contracts."""
    def fake(entry, n_close, ror):
        calls.append((entry.entry_number, n_close, round(ror, 4)))
        entry.contracts -= n_close
        if entry.contracts <= 0:
            entry.dc_phase = DCPhase.CLOSED
    s._spy_dc_partial_close = fake


def test_ladder_10c_scales_out_at_rising_ror():
    s = _strat()  # default 4×0.25 ladder
    calls = []
    _wire_partial_close(s, calls)
    e = _open_entry(net_debit=1000.0, contracts=10, num=1)
    s._spy_dc_closed_contracts[1] = 0

    # ror 0.20 -> cumulative 0.25 -> round(2.5)=2 closed.
    e._force_upnl = 200.0
    s._spy_dc_take_profit_ladder(e)
    assert calls[-1] == (1, 2, 0.2)
    assert s._spy_dc_closed_contracts[1] == 2

    # ror 0.50 -> cumulative 0.50 -> round(5.0)=5 -> close 3 more.
    e._force_upnl = 500.0
    s._spy_dc_take_profit_ladder(e)
    assert calls[-1] == (1, 3, 0.5)
    assert s._spy_dc_closed_contracts[1] == 5

    # ror 1.00 -> cumulative 0.75 -> round(7.5)=8 -> close 3 more.
    e._force_upnl = 1000.0
    s._spy_dc_take_profit_ladder(e)
    assert calls[-1] == (1, 3, 1.0)
    assert s._spy_dc_closed_contracts[1] == 8

    # ror 2.00 -> cumulative 1.00 -> 10 -> close remaining 2.
    e._force_upnl = 2000.0
    s._spy_dc_take_profit_ladder(e)
    assert calls[-1] == (1, 2, 2.0)
    assert s._spy_dc_closed_contracts[1] == 10


def test_ladder_1c_closes_whole_at_first_threshold():
    s = _strat()
    calls = []
    _wire_partial_close(s, calls)
    e = _open_entry(net_debit=1000.0, contracts=1, num=1)
    s._spy_dc_closed_contracts[1] = 0
    # Below the first rung -> nothing.
    e._force_upnl = 100.0  # +10% < 20%
    assert s._spy_dc_take_profit_ladder(e) is None
    assert calls == []
    # At the first rung -> close the whole single contract.
    e._force_upnl = 200.0  # +20%
    msg = s._spy_dc_take_profit_ladder(e)
    assert calls == [(1, 1, 0.2)]
    assert e.contracts == 0
    assert "PROFIT-CLOSE" in msg


def test_ladder_target_close_count_1lot_full_exit():
    s = _strat()
    # 1-lot: any met rung -> 1 (full exit); no rung met -> 0.
    assert s._ladder_target_close_count(0.10, 1) == 0
    assert s._ladder_target_close_count(0.20, 1) == 1
    assert s._ladder_target_close_count(2.00, 1) == 1


# ---------------------------------------------------------------------------
# Trading-day TIME-EXIT — fires within N trading days; never holds to expiry
# ---------------------------------------------------------------------------

def test_time_exit_fires_within_window(monkeypatch):
    s = _strat(time_exit=2)
    closed = []
    s._dc_close_calendar = lambda e, reason, loss: closed.append((e.entry_number, loss))
    # Short expiry 2 trading days out (Wed -> close Mon, expiry Wed): pick a date
    # that is exactly 2 trading days after "today".
    today = datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc)  # Thursday
    monkeypatch.setattr(spydc, "get_us_market_time", lambda: today)
    # Friday + Monday = 2 trading days -> short expiry Monday 2026-07-20.
    e = _open_entry(num=1, short_exp="2026-07-20")
    s._spy_dc_closed_contracts[1] = 0
    act = s._manage_calendar(e)
    assert closed == [(1, False)]  # managed close, not a loss
    assert act == f"TIME-EXIT E#1"


def test_time_exit_does_not_fire_far_from_expiry(monkeypatch):
    s = _strat(time_exit=2)
    closed = []
    s._dc_close_calendar = lambda e, reason, loss: closed.append((e.entry_number, loss))
    s._dc_refresh_marks = lambda e: False  # not fresh -> ladder no-ops
    today = datetime(2026, 6, 16, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(spydc, "get_us_market_time", lambda: today)
    e = _open_entry(num=1, short_exp="2026-07-20")  # ~24 trading days out
    s._spy_dc_closed_contracts[1] = 0
    s._manage_calendar(e)
    assert closed == []  # nowhere near the short expiry


def test_time_exit_runs_even_on_stale_mark(monkeypatch):
    # The time-exit is TIME-based: it must fire regardless of mark freshness so we
    # NEVER hold through expiry (and as the SPY-assignment safety net).
    s = _strat(time_exit=2)
    closed = []
    s._dc_close_calendar = lambda e, reason, loss: closed.append((e.entry_number, loss))
    s._dc_refresh_marks = lambda e: False  # stale -> ladder would skip
    today = datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(spydc, "get_us_market_time", lambda: today)
    e = _open_entry(num=1, short_exp="2026-07-20")
    s._spy_dc_closed_contracts[1] = 0
    s._manage_calendar(e)
    assert closed == [(1, False)]


# ---------------------------------------------------------------------------
# No-stop max loss = full open debit
# ---------------------------------------------------------------------------

def test_max_loss_is_full_debit():
    s = _strat()
    e = _open_entry(net_debit=1000.0, num=1)
    s.daily_state.entries = [e]
    s._dc_entry_is_open = lambda x: True
    assert s._dc_open_debit_at_risk() == 1000.0
    assert s._calculate_max_loss_with_stops() == 1000.0  # NO stop fraction -> full debit
    assert s._calculate_max_loss_catastrophic() == 1000.0


# ---------------------------------------------------------------------------
# Low-IV entry gate
# ---------------------------------------------------------------------------

def test_low_iv_gate_skips_when_vix_high():
    s = _strat(max_vix=22.0)
    s.current_vix = 28.0  # above the low-IV gate
    s.daily_state.entries = []
    s._dc_entry_is_open = lambda e: False
    msg = s._pre_entry_gates(1)
    assert msg is not None and "low-IV gate" in msg


def test_low_iv_gate_allows_when_vix_low(monkeypatch):
    s = _strat(max_vix=22.0)
    s.current_vix = 14.0  # below the gate
    s.daily_state.entries = []
    s._dc_entry_is_open = lambda e: False
    s._has_orphaned_orders = lambda: False
    s._check_market_halt = lambda: (False, "")
    s._check_buying_power = lambda: (True, "")
    assert s._pre_entry_gates(1) is None


def test_concurrent_cap_skips_when_one_open():
    s = _strat()
    s.daily_state.entries = [_open_entry(num=1)]
    s._dc_entry_is_open = lambda e: True
    msg = s._pre_entry_gates(2)
    assert msg is not None and "already open" in msg


# ---------------------------------------------------------------------------
# Monitoring cadence — vigilant near a ladder rung / time-exit
# ---------------------------------------------------------------------------

def test_monitoring_vigilant_near_first_ladder_rung(monkeypatch):
    s = _strat()
    today = datetime(2026, 6, 16, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(spydc, "get_us_market_time", lambda: today)
    e = _open_entry(net_debit=1000.0, num=1, short_exp="2026-07-20")
    e._force_upnl = 160.0  # +16% >= 0.75 * 20%
    s.daily_state.active_entries = [e]
    assert s.get_monitoring_mode() == "vigilant"


def test_monitoring_vigilant_near_time_exit(monkeypatch):
    s = _strat(time_exit=2)
    today = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)  # within time_exit+1 window
    monkeypatch.setattr(spydc, "get_us_market_time", lambda: today)
    e = _open_entry(net_debit=1000.0, num=1, short_exp="2026-07-20")
    e._force_upnl = -10.0  # calm
    s.daily_state.active_entries = [e]
    assert s.get_monitoring_mode() == "vigilant"


def test_monitoring_normal_when_calm_and_far(monkeypatch):
    s = _strat(time_exit=2)
    today = datetime(2026, 6, 16, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(spydc, "get_us_market_time", lambda: today)
    e = _open_entry(net_debit=1000.0, num=1, short_exp="2026-07-20")
    e._force_upnl = 10.0  # +1% calm
    s.daily_state.active_entries = [e]
    assert s.get_monitoring_mode() == "normal"


def test_monitoring_normal_when_no_entries():
    s = _strat()
    s.daily_state.active_entries = []
    assert s.get_monitoring_mode() == "normal"
