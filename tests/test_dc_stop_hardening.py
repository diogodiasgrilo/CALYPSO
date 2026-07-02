"""Strategy D (DC Time Machine) hardening: persistence-confirmed 20%-debit stop,
real-time quote gate, debit-based capital, concurrent-calendar cap.

Grounded in 2026-06-16: D's only entry closed at a realized -6.3% on a -20%
trigger — the noisy 4-mid calendar mark spiked for one tick and the close
re-fetched a milder value. The fixes: confirm the breach over a window before
closing, don't re-fetch in the close, gate on real-time quotes, cap concurrency.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import bots.hydra.double_calendar_strategy as dcm
from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy as D
from bots.hydra.calendar_entry import CalendarEntry, DCPhase


def _strat():
    s = D.__new__(D)
    s.dc_pre_transform_stop_pct = 0.20
    s.dc_profit_trigger_pct = 0.075
    s.dc_stop_confirm_seconds = 20.0
    s.dc_require_realtime_quotes = True
    s.dc_max_concurrent = 1
    s.dc_max_deployed_debit = 5000.0
    s.dc_eod_close_if_no_transform = False
    s._dc_stop_breach = {}
    s.daily_state = MagicMock()
    s.daily_state.active_entries = []
    s.daily_state.entries = []
    return s


def _entry(net_debit=1000.0, upnl=0.0, num=1, phase=DCPhase.CALENDAR):
    # spec=CalendarEntry so isinstance() checks in get_monitoring_mode pass.
    e = MagicMock(spec=CalendarEntry)
    e.entry_number = num
    e.net_debit = net_debit
    e.contracts = 1
    e.unrealized_pnl = upnl
    # opening_pnl=0 so the anchored trigger basis (pnl_move_pct = (upnl - 0)/debit)
    # equals the intended move these tests express via `upnl` (2026-07-02 audit).
    e.opening_pnl = 0.0
    e.pnl_move_pct = (upnl / net_debit) if net_debit else 0.0
    e.dc_phase = phase
    return e


# ---- real-time quote gate ----------------------------------------------------
def test_quote_realtime_true_when_checker_says_so():
    s = _strat()
    s._option_quote_is_realtime = lambda q: True
    assert s._dc_quote_is_realtime({}) is True


def test_quote_realtime_false_when_checker_says_so():
    s = _strat()
    s._option_quote_is_realtime = lambda q: False
    assert s._dc_quote_is_realtime({}) is False


def test_quote_realtime_true_on_checker_error():
    s = _strat()
    def boom(q):
        raise RuntimeError("x")
    s._option_quote_is_realtime = boom
    assert s._dc_quote_is_realtime({}) is True


# ---- capital = sum of OPEN debits (not wing notional) -------------------------
def test_capital_deployed_sums_open_debits():
    s = _strat()
    e1, e2 = _entry(net_debit=1035.0, num=1), _entry(net_debit=500.0, num=2)
    s.daily_state.entries = [e1, e2]
    s._dc_entry_is_open = lambda e: True
    assert s._calculate_capital_deployed() == 1535.0
    s._dc_entry_is_open = lambda e: e is e1   # e2 now closed -> excluded
    assert s._calculate_capital_deployed() == 1035.0


# ---- persistence-confirmed 20%-debit stop ------------------------------------
def _wire(s):
    s._dc_refresh_marks = lambda e: True      # always "fresh"
    s._dc_attempt_transform = lambda e: False
    s._dc_past_eod_cutoff = lambda: False


def test_stop_holds_first_breach_then_closes_after_confirm(monkeypatch):
    s = _strat(); _wire(s)
    closed = []
    s._dc_close_calendar = lambda e, reason, loss: closed.append((reason, loss))
    e = _entry(net_debit=1000.0, upnl=-250.0)  # -25% (past the -20% trigger)
    s.daily_state.active_entries = [e]
    t0 = datetime(2026, 6, 16, 14, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(dcm, "get_us_market_time", lambda: t0)
    s._dc_manage_calendar(e)
    assert closed == []                        # first breach -> confirming, not closed
    assert e.entry_number in s._dc_stop_breach

    monkeypatch.setattr(dcm, "get_us_market_time", lambda: t0 + timedelta(seconds=25))
    s._dc_manage_calendar(e)
    assert closed == [("20%-debit stop", True)]  # persisted >= 20s -> closed


def test_stop_breach_recovers_no_close(monkeypatch):
    s = _strat(); _wire(s)
    closed = []
    s._dc_close_calendar = lambda e, reason, loss: closed.append((reason, loss))
    e = _entry(net_debit=1000.0, upnl=-250.0)
    s.daily_state.active_entries = [e]
    t0 = datetime(2026, 6, 16, 14, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(dcm, "get_us_market_time", lambda: t0)
    s._dc_manage_calendar(e)
    assert e.entry_number in s._dc_stop_breach
    e.unrealized_pnl = -50.0                    # recovers to -5% before the window elapses
    e.pnl_move_pct = -50.0 / 1000.0             # anchored move tracks the recovery
    monkeypatch.setattr(dcm, "get_us_market_time", lambda: t0 + timedelta(seconds=10))
    s._dc_manage_calendar(e)
    assert closed == []                        # false stop avoided
    assert e.entry_number not in s._dc_stop_breach


# ---- concurrent-calendar cap -------------------------------------------------
def test_concurrent_cap_skips_when_one_open():
    s = _strat()
    s.daily_state.entries = [_entry(num=1)]
    s._dc_entry_is_open = lambda e: True
    s._next_entry_index = 0
    s.daily_state.entries_skipped = 0
    msg = s._dc_pre_entry_gates(2)
    assert msg is not None and "already open" in msg


# ---- per-variant BP budget ---------------------------------------------------
def test_bp_budget_skips_when_deployed_at_budget():
    s = _strat()
    s.dc_max_concurrent = 99             # don't let the concurrent cap fire first
    s.dc_max_deployed_debit = 1000.0
    s.daily_state.entries = [_entry(net_debit=1035.0, num=1)]  # deployed 1035 >= 1000
    s._dc_entry_is_open = lambda e: True
    s._next_entry_index = 0
    s.daily_state.entries_skipped = 0
    msg = s._dc_pre_entry_gates(2)
    assert msg is not None and "BP budget" in msg


# ---- debit-based max loss (not IC math) --------------------------------------
def test_max_loss_uses_debit_not_ic_math():
    s = _strat()
    s.daily_state.entries = [_entry(net_debit=1000.0, num=1)]
    s._dc_entry_is_open = lambda e: True
    assert s._dc_open_debit_at_risk() == 1000.0
    assert s._calculate_max_loss_with_stops() == 200.0       # 20% of debit
    assert s._calculate_max_loss_catastrophic() == 1000.0    # full debit


def test_max_loss_excludes_transformed_riskfree():
    s = _strat()
    s.daily_state.entries = [_entry(net_debit=1000.0, num=1, phase=DCPhase.TRANSFORMED)]
    s._dc_entry_is_open = lambda e: True
    assert s._dc_open_debit_at_risk() == 0.0  # transformed = (gated) risk-free


# ---- calendar-aware vigilant cadence -----------------------------------------
def test_monitoring_vigilant_during_breach_confirm():
    s = _strat()
    s.daily_state.active_entries = [_entry()]
    s._dc_stop_breach = {1: "t"}
    assert s.get_monitoring_mode() == "vigilant"


def test_monitoring_vigilant_near_stop():
    s = _strat()
    s.daily_state.active_entries = [_entry(net_debit=1000.0, upnl=-160.0)]  # -16% (>=75%×-20%)
    assert s.get_monitoring_mode() == "vigilant"


def test_monitoring_vigilant_near_transform():
    s = _strat()
    s.daily_state.active_entries = [_entry(net_debit=1000.0, upnl=60.0)]    # +6% (>=75%×7.5%)
    assert s.get_monitoring_mode() == "vigilant"


def test_monitoring_normal_when_calm():
    s = _strat()
    s.daily_state.active_entries = [_entry(net_debit=1000.0, upnl=-30.0)]   # -3% calm
    assert s.get_monitoring_mode() == "normal"


def test_monitoring_normal_when_no_entries():
    s = _strat()
    s.daily_state.active_entries = []
    assert s.get_monitoring_mode() == "normal"


# ---- 2026-07-02 calendar audit: trigger anchoring to the opening mark ---------
def test_pnl_move_pct_anchors_triggers_to_opening_mark():
    """A fresh calendar is born marked at the full round-trip liquidation spread
    (~-20% on a real double calendar). The decision triggers must measure MOVEMENT
    from that opening mark — so an unmoved position reads 0% and does NOT stop at
    birth (the 9/9 same-day-loss bug). Realized P&L still uses the honest mark."""
    e = CalendarEntry(entry_number=1)
    e.contracts = 1
    e.dc_phase = DCPhase.CALENDAR
    e.net_debit = 1000.0
    # legs → calendar_value 800 → unrealized_pnl -200 (the -20% birth liquidation)
    e.long_call_price, e.short_call_price = 10.0, 5.0   # call_val 5
    e.long_put_price, e.short_put_price = 8.0, 5.0       # put_val 3
    assert e.unrealized_pnl == -200.0
    e.opening_pnl = e.unrealized_pnl                     # captured at fill
    assert e.pnl_move_pct == 0.0                         # fresh reads 0% MOVE, not -20%

    # A real 10% ADVERSE move (value 800→700): move = (-300 - -200)/1000 = -10%
    e.long_call_price = 9.0
    assert e.unrealized_pnl == -300.0
    assert e.pnl_move_pct == -0.10

    # A real favorable move (value 800→1000, back to flat): move = (0 - -200)/1000 = +20%
    e.long_call_price = 12.0
    assert e.unrealized_pnl == 0.0
    assert e.pnl_move_pct == 0.20

    # A restored pre-audit trade (opening_pnl 0) falls back to absolute pnl/debit.
    e.opening_pnl = 0.0
    assert e.pnl_move_pct == 0.0  # unrealized_pnl is 0 here
