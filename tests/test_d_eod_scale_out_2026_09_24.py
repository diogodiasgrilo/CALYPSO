"""Burnich's end-of-day half-close — the last rule of his D did not have.

> *"if I did 20 contracts and I come to the end of the day and I have not been
> able to transform that into a risk-free spread, I might close 10 of those and
> book a small profit on the double calendar and hold the other 10 till the next
> day and see if then I can transform it the next day."*

D had the two ends — close everything (`eod_close_if_no_transform`) or hold
everything — and not the middle one he actually reaches for. It banks something
real while keeping the chance of the risk-free transform the whole strategy
exists to produce.

THE ARITHMETIC THAT MAKES IT SUBTLE
------------------------------------
``calendar_value`` recomputes from ``contracts`` while ``net_debit`` is a stored
dollar figure already multiplied by the original count. Halving one without the
other leaves the surviving half marking against the FULL original debit — a
permanently wrong P&L on the contracts still at risk, which would look like a
losing strategy rather than a bug.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, time as dt_time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.calendar_entry import CalendarEntry, DCPhase  # noqa: E402
from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy  # noqa: E402

D_CFG = json.loads(
    (ROOT / "bots" / "hydra" / "config" / "config_variant_d.json").read_text())["strategy"]


class _MarkedEntry(CalendarEntry):
    """A CalendarEntry whose mark this test controls.

    A SUBCLASS, not a patched property on ``CalendarEntry`` itself. The first
    version did ``type(e).calendar_value = property(...)``, which mutates the
    REAL class for the rest of the session — the tests here passed in isolation
    and broke four unrelated calendar tests in the full suite, purely by import
    order. Global mutation in a fixture is a test that damages its neighbours.

    ``calendar_value`` scales with ``contracts`` exactly as the real property
    does, which is the behaviour the half-close arithmetic depends on.
    """

    _mark_at_4 = 900.0

    @property
    def calendar_value(self) -> float:
        return self._mark_at_4 * self.contracts / 4


def _entry(contracts=4, debit=800.0, cal_value=900.0, phase=DCPhase.CALENDAR):
    e = _MarkedEntry(entry_number=1)
    e.contracts = contracts
    e.net_debit = debit
    e.dc_phase = phase
    e._mark_at_4 = cal_value
    return e


def _strat(**over):
    s = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
    s.dc_eod_scale_out_enabled = over.get("enabled", True)
    s.dc_eod_scale_out_fraction = over.get("fraction", 0.5)
    s.dc_eod_cutoff = dt_time(15, 55)
    s.contracts_per_entry = 2
    s.commission_per_leg = 1.15
    s.current_price = 6000.0
    s._dc_recorder = None
    s._save_state_to_disk = lambda: None
    s.daily_state = MagicMock(total_commission=0.0)
    s._booked = []
    s._book_realized_pnl = lambda pnl, entry=None: s._booked.append(pnl)
    s._dc_past_eod_cutoff = lambda: over.get("past_cutoff", True)
    return s


class TestTheHalfCloseFires:

    def test_half_the_contracts_close_and_the_rest_carry(self):
        s, e = _strat(), _entry(contracts=4)
        assert s._dc_eod_partial_scale_out(e) == "EOD-SCALE E#1 2/4"
        assert e.contracts == 2
        assert e.dc_phase == DCPhase.CALENDAR, "the remainder must stay OPEN"

    def test_the_debit_is_scaled_WITH_the_contracts(self):
        """The subtle half. Leaving net_debit at 800 while contracts halve would
        mark the surviving position against twice the capital it actually has
        at risk — a permanently wrong P&L that reads as a losing strategy."""
        s, e = _strat(), _entry(contracts=4, debit=800.0)
        s._dc_eod_partial_scale_out(e)
        assert e.net_debit == pytest.approx(400.0)

    def test_the_remaining_position_marks_correctly_afterwards(self):
        """End to end on the arithmetic: 4c at debit 800 marking 900 is +100.
        After the half-close the survivor is 2c at debit 400 marking 450 = +50 —
        exactly half the original edge, which is what holding half means."""
        s, e = _strat(), _entry(contracts=4, debit=800.0, cal_value=900.0)
        s._dc_eod_partial_scale_out(e)
        assert e.unrealized_pnl == pytest.approx(50.0)

    def test_only_the_closed_share_of_profit_is_booked(self):
        s, e = _strat(), _entry(contracts=4, debit=800.0, cal_value=900.0)
        s._dc_eod_partial_scale_out(e)
        assert s._booked == [pytest.approx(50.0)]        # half of +100

    def test_commission_is_charged_on_the_CLOSED_contracts_only(self):
        s, e = _strat(), _entry(contracts=4)
        s._dc_eod_partial_scale_out(e)
        assert s.daily_state.total_commission == pytest.approx(4 * 1.15 * 2)

    def test_an_odd_count_floors_rather_than_over_closing(self):
        s, e = _strat(), _entry(contracts=5)
        s._dc_eod_partial_scale_out(e)
        assert e.contracts == 3                          # closed 2, kept 3


class TestWhenItMustNotFire:

    def test_not_before_the_cutoff(self):
        s, e = _strat(past_cutoff=False), _entry()
        assert s._dc_eod_partial_scale_out(e) is None
        assert e.contracts == 4

    def test_not_on_a_LOSS(self):
        """He describes the half-close only in the profitable case, and treats a
        loss as a whole-position decision. His 20% uncle point already covers the
        bad tail; halving a loser would be a rule he never states."""
        s, e = _strat(), _entry(contracts=4, debit=900.0, cal_value=800.0)
        assert s._dc_eod_partial_scale_out(e) is None
        assert e.contracts == 4

    def test_not_once_TRANSFORMED(self):
        """A risk-free condor needs no rescue — he says there is 'zero
        management' after the transform."""
        s, e = _strat(), _entry(phase=DCPhase.TRANSFORMED)
        assert s._dc_eod_partial_scale_out(e) is None

    def test_not_at_ONE_contract(self):
        """Half of one is zero. The rule simply does not exist at that size."""
        s, e = _strat(), _entry(contracts=1)
        assert s._dc_eod_partial_scale_out(e) is None
        assert e.contracts == 1

    def test_not_TWICE_in_the_same_session(self):
        """The manager runs every tick after the cutoff. Without the guard a
        five-minute window would halve the position repeatedly to nothing."""
        s, e = _strat(), _entry(contracts=8)
        assert s._dc_eod_partial_scale_out(e) is not None
        assert e.contracts == 4
        for _ in range(5):
            assert s._dc_eod_partial_scale_out(e) is None
        assert e.contracts == 4

    def test_it_can_fire_again_the_NEXT_day(self, monkeypatch):
        """He holds 'till the next day and see if then I can transform it' — so
        a position that survives another session is eligible again."""
        import bots.hydra.double_calendar_strategy as mod
        s, e = _strat(), _entry(contracts=8)
        monkeypatch.setattr(mod, "get_us_market_time",
                            lambda: datetime(2026, 9, 24, 15, 56))
        s._dc_eod_partial_scale_out(e)
        assert e.contracts == 4
        monkeypatch.setattr(mod, "get_us_market_time",
                            lambda: datetime(2026, 9, 25, 15, 56))
        s._dc_eod_partial_scale_out(e)
        assert e.contracts == 2

    def test_it_can_be_switched_off(self):
        s, e = _strat(enabled=False), _entry()
        assert s._dc_eod_partial_scale_out(e) is None


class TestTheGuardSurvivesARestart:

    def test_the_marker_round_trips_through_the_sidecar(self):
        """A restart inside the post-cutoff window must not re-arm the rule and
        halve an already-halved position — the crash-window bug in miniature."""
        from bots.hydra.calendar_strategy_base import CalendarStrategyBase
        base = CalendarStrategyBase.__new__(CalendarStrategyBase)
        e = CalendarEntry(entry_number=1)
        e.contracts, e.net_debit, e.dc_scaled_out_on = 2, 400.0, "2026-09-24"
        restored = base._dc_deserialize_entry(base._dc_serialize_entry(e))
        assert restored.dc_scaled_out_on == "2026-09-24"
        assert restored.contracts == 2 and restored.net_debit == pytest.approx(400.0)


class TestTheShippedConfigCanActuallyRunIt:

    def test_it_is_enabled_at_a_half(self):
        dc = D_CFG["double_calendar"]
        assert dc["eod_scale_out_enabled"] is True
        assert dc["eod_scale_out_fraction"] == 0.5

    def test_D_is_sized_so_the_rule_is_REACHABLE(self):
        """A rule that can never fire at the configured size produces an empty
        dataset for the one behaviour it was added to measure — the trap
        sizing_for_zero_max_loss=$500 set for H."""
        assert D_CFG["contracts_per_entry"] >= 2, (
            "half of one contract is zero; the half-close cannot exist at 1c")

    def test_the_sizing_change_records_why(self):
        raw = (ROOT / "bots" / "hydra" / "config" / "config_variant_d.json").read_text()
        assert "_comment_contracts_per_entry" in raw
        assert "half of one contract is zero" in raw.lower()
