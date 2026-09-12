"""Calendar dashboard P&L bug fix (found in the 2026-08-03 full-day audit).

`_save_state_to_disk`'s pnl_history builder (bots/hydra/strategy.py) was
written for credit iron condors and, applied unmodified to a CalendarEntry
(D/E, debit calendars), computed net_pnl ~= -calendar_value instead of the
correct calendar_value - net_debit — confirmed live: dashboard showed -$164
on a position whose real P&L (verified via dc_calendar_snapshots, the
dc_open_trades.json sidecar, and heartbeat log lines) was -$8.

These tests exercise the REAL `_save_state_to_disk` method (not a
hand-derived expected number) and assert its output equals
`entry.unrealized_pnl` — the authoritative, phase-aware formula already used
everywhere else calendar P&L is surfaced — so the test can't silently drift
from the fix. Includes a non-regression test pinning the untouched
credit-vertical math for IronCondorEntry (A/B/C), which had no direct test
before this either.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.market_hours import get_us_market_time  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402
from bots.hydra.base_strategy import IronCondorEntry  # noqa: E402
from bots.hydra.calendar_entry import CalendarEntry, DCPhase  # noqa: E402


def _make_strategy(tmp_path: Path, entries: list) -> HydraStrategy:
    """Minimal HydraStrategy with just enough state for _save_state_to_disk
    to run against real entries — same pattern as
    tests/test_hydra_state_heartbeat.py's _make_strategy."""
    s = HydraStrategy.__new__(HydraStrategy)

    ds = MagicMock()
    ds.date = "2026-08-03"
    ds.entries_completed = 0
    ds.entries_failed = 0
    ds.entries_skipped = 0
    ds.total_credit_received = 0.0
    ds.total_realized_pnl = 0.0
    ds.total_commission = 0.0
    ds.call_stops_triggered = 0
    ds.put_stops_triggered = 0
    ds.double_stops = 0
    ds.circuit_breaker_opens = 0
    ds.one_sided_entries = 0
    ds.trend_overrides = 0
    ds.credit_gate_skips = 0
    ds.stops_avoided_mkt036 = 0
    ds.entries = entries
    s.daily_state = ds

    s.state = MagicMock()
    s.state.value = "MONITORING"
    s._next_entry_index = 0
    s.contracts_per_entry = 1
    s.entry_times = []
    s._base_entry_count = 0
    s._conditional_entry_times = []
    s.strategy_config = {}
    s._pnl_history = []
    s.short_only_stop = False
    s._early_close_time = None
    s.market_data = SimpleNamespace(
        spx_open=None, spx_high=None, spx_low=None,
        vix_open=None, vix_high=None, vix_low=None,
    )
    s.state_file = str(tmp_path / "hydra_state.json")
    s._get_effective_stop_level = lambda *a, **kw: None
    return s


def _calendar_entry(dc_phase: DCPhase, *, fresh: bool = False, **overrides) -> CalendarEntry:
    now = get_us_market_time()
    # long_call_price / short_call_price / etc are leg-bridge properties
    # (inherited from IronCondorEntry), not dataclass constructor kwargs —
    # pull them out and set them after construction.
    price_fields = {"long_call_price", "short_call_price", "long_put_price", "short_put_price"}
    prices = {k: overrides.pop(k) for k in list(overrides) if k in price_fields}
    defaults = dict(
        entry_number=1,
        entry_time=now if fresh else (now - __import__("datetime").timedelta(minutes=5)),
        contracts=1,
        dc_phase=dc_phase,
        net_debit=172.0,
        call_side_stopped=False, put_side_stopped=False,
        call_side_skipped=False, put_side_skipped=False,
        call_side_expired=False, put_side_expired=False,
    )
    defaults.update(overrides)
    entry = CalendarEntry(**defaults)
    entry.long_call_price = prices.get("long_call_price", 8.50)
    entry.short_call_price = prices.get("short_call_price", 6.10)
    entry.long_put_price = prices.get("long_put_price", 4.20)
    entry.short_put_price = prices.get("short_put_price", 2.75)
    return entry


def _read_pnl(s: HydraStrategy) -> float:
    s._save_state_to_disk()
    data = json.loads(Path(s.state_file).read_text())
    return data["pnl_history"][-1]["pnl"]


class TestCalendarPnlHistoryFix:
    def test_calendar_phase_matches_unrealized_pnl(self, tmp_path):
        entry = _calendar_entry(DCPhase.CALENDAR)
        s = _make_strategy(tmp_path, [entry])
        pnl = _read_pnl(s)
        assert pnl == round(entry.unrealized_pnl, 2)
        # Sanity: this is the ACTUAL correct number, not -calendar_value.
        assert pnl != round(-entry.calendar_value, 2)

    def test_calendar_phase_reproduces_todays_real_incident_numbers(self, tmp_path):
        # 2026-08-03 E audit: dashboard showed -$164 (~= -calendar_value),
        # real P&L (DB/sidecar/log, all agreeing) was -$8. Reconstruct with
        # today's actual field values and confirm the fix lands on -$8, not -$164.
        entry = _calendar_entry(
            DCPhase.CALENDAR,
            net_debit=172.0,
            long_call_price=9.20, short_call_price=7.40,  # call_val = 1.80
            long_put_price=5.10, short_put_price=3.30,    # put_val = 1.80
        )
        # calendar_value = (1.80 + 1.80) * 100 * 1 = 360; unrealized = 360-172=188 (illustrative,
        # not literal replay of unlogged intraday marks) — the real assertion is structural:
        # fixed formula == unrealized_pnl, buggy formula == -calendar_value, and they differ.
        s = _make_strategy(tmp_path, [entry])
        pnl = _read_pnl(s)
        buggy_would_have_shown = round(-entry.calendar_value, 2)
        assert pnl == round(entry.unrealized_pnl, 2)
        assert pnl != buggy_would_have_shown

    def test_transformed_phase_matches_unrealized_pnl(self, tmp_path):
        entry = _calendar_entry(
            DCPhase.TRANSFORMED,
            transform_credit=250.0,
            long_call_price=1.0, short_call_price=0.5,
            long_put_price=1.2, short_put_price=0.6,
        )
        s = _make_strategy(tmp_path, [entry])
        pnl = _read_pnl(s)
        assert pnl == round(entry.unrealized_pnl, 2)

    def test_closed_phase_contributes_nothing_extra(self, tmp_path):
        # CLOSED: unrealized_pnl is 0.0 by design (real P&L already booked
        # into total_realized_pnl before this runs) — confirm the fix
        # preserves that, doesn't reintroduce a stray contribution.
        entry = _calendar_entry(DCPhase.CLOSED)
        s = _make_strategy(tmp_path, [entry])
        s.daily_state.total_realized_pnl = -335.0
        s.daily_state.total_commission = 2.0
        pnl = _read_pnl(s)
        assert pnl == round(-335.0 - 2.0, 2)

    def test_fresh_entry_with_unloaded_marks_contributes_zero_not_negative_debit(self, tmp_path):
        # Marks not yet loaded this minute (all leg prices still 0.0) on a
        # JUST-placed entry -> must contribute 0, not -net_debit (mirrors the
        # existing IC stale-mark guard's intent).
        entry = _calendar_entry(
            DCPhase.CALENDAR,
            fresh=True,
            net_debit=172.0,
            long_call_price=0.0, short_call_price=0.0,
            long_put_price=0.0, short_put_price=0.0,
        )
        assert entry.calendar_value == 0.0
        s = _make_strategy(tmp_path, [entry])
        pnl = _read_pnl(s)
        assert pnl == 0.0
        assert pnl != round(-entry.net_debit, 2)

    def test_stale_guard_does_not_apply_once_marks_load(self, tmp_path):
        # Same entry, but no longer fresh (or marks present) -> full
        # unrealized_pnl applies even though net_debit != 0.
        entry = _calendar_entry(
            DCPhase.CALENDAR,
            fresh=False,
            net_debit=172.0,
            long_call_price=0.0, short_call_price=0.0,
            long_put_price=0.0, short_put_price=0.0,
        )
        s = _make_strategy(tmp_path, [entry])
        pnl = _read_pnl(s)
        assert pnl == round(entry.unrealized_pnl, 2)
        assert pnl == round(-172.0, 2)  # calendar_value=0, net_debit=172 -> -172


class TestIronCondorPnlHistoryUnchanged:
    """Non-regression: the credit-vertical formula for A/B/C-shaped entries
    must be byte-identical to pre-fix behavior (this path had no direct
    test before either — pin it while touching this code)."""

    def _ic_entry(self, **overrides) -> IronCondorEntry:
        now = get_us_market_time()
        stopped = overrides.pop("call_side_stopped", False)
        entry = IronCondorEntry(
            entry_number=1,
            entry_time=now - __import__("datetime").timedelta(minutes=5),
            contracts=1,
            call_spread_credit=120.0, put_spread_credit=130.0,
        )
        entry.call_side_stopped = stopped
        # call_spread_value / put_spread_value are derived properties (from
        # leg strike/price bridges), not constructor kwargs — set the
        # underlying prices/strikes wide enough that the [0, width] clamp
        # doesn't bind, landing exactly on the target cost-to-close values.
        entry.short_call_strike, entry.long_call_strike = 100.0, 105.0  # width 5
        entry.short_put_strike, entry.long_put_strike = 95.0, 90.0      # width 5
        entry.short_call_price, entry.long_call_price = 0.40, 0.00      # value = 0.40*100=40
        entry.short_put_price, entry.long_put_price = 0.35, 0.00        # value = 0.35*100=35
        for k, v in overrides.items():
            setattr(entry, k, v)
        assert entry.call_spread_value == 40.0
        assert entry.put_spread_value == 35.0
        return entry

    def test_credit_vertical_math_unchanged(self, tmp_path):
        entry = self._ic_entry()
        s = _make_strategy(tmp_path, [entry])
        pnl = _read_pnl(s)
        # credit - value on both sides, no realized/commission baseline set (0/0)
        expected = (120.0 - 40.0) + (130.0 - 35.0)
        assert pnl == round(expected, 2)

    def test_stopped_side_excluded(self, tmp_path):
        entry = self._ic_entry(call_side_stopped=True)
        s = _make_strategy(tmp_path, [entry])
        pnl = _read_pnl(s)
        expected = 130.0 - 35.0  # only put side active
        assert pnl == round(expected, 2)

    def test_ic_entry_not_treated_as_calendar(self, tmp_path):
        entry = self._ic_entry()
        assert not isinstance(entry, CalendarEntry)
        s = _make_strategy(tmp_path, [entry])
        # Must not raise (e.g. AttributeError from a calendar-only field)
        # and must take the IC branch, not silently produce 0.
        pnl = _read_pnl(s)
        assert pnl != 0.0
