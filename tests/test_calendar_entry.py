"""CalendarEntry (Strategy D foundation) tests — Phase 1.

Pins the two-expiration / net-DEBIT position model: per-leg expiry, phase-aware
debit-rooted economics (so the IC credit-vertical math never runs for a
calendar), the risk-free invariant, and — critically — that a CalendarEntry is
recognized by the UNCHANGED base ``active_entries`` (the payoff of subclassing
IronCondorEntry instead of editing the fix-scarred monitoring loop).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import IronCondorEntry, MEICDailyState
from bots.hydra.calendar_entry import CalendarEntry, DCPhase
from bots.hydra.leg import Leg, _new_legset


class TestLegExpiry:
    def test_expiry_defaults_none_and_is_additive(self):
        # The new field must default None so the iron-condor family is unchanged.
        leg = Leg("short", "call")
        assert leg.expiry is None
        for l in _new_legset().values():
            assert l.expiry is None

    def test_expiry_settable(self):
        leg = Leg("long", "call")
        leg.expiry = "2026-06-26"
        assert leg.expiry == "2026-06-26"


class TestConstructionAndExpiryHelpers:
    def test_defaults(self):
        e = CalendarEntry(entry_number=1)
        assert e.dc_phase == DCPhase.CALENDAR
        assert e.structure == "double_calendar"
        assert e.net_debit == 0.0
        assert e.is_risk_free is False

    def test_short_long_expiry_from_legs(self):
        e = CalendarEntry(entry_number=1)
        e.legs["short_call"].expiry = "2026-06-19"   # near
        e.legs["long_call"].expiry = "2026-06-22"    # far
        assert e.short_expiry == "2026-06-19"
        assert e.long_expiry == "2026-06-22"


class TestCalendarPhaseEconomics:
    def _calendar(self):
        e = CalendarEntry(entry_number=1)
        e.contracts = 1
        # call calendar: short near @5000, long far @5000 (SAME strike)
        e.short_call_strike = 5000.0
        e.long_call_strike = 5000.0
        e.short_put_strike = 4900.0
        e.long_put_strike = 4900.0
        e.short_call_price = 2.0
        e.long_call_price = 3.0   # long (far) dearer -> +1.0
        e.short_put_price = 2.5
        e.long_put_price = 3.5    # +1.0
        e.net_debit = 150.0
        e.wing_width = 5.0
        return e

    def test_calendar_value(self):
        e = self._calendar()
        # (3-2) + (3.5-2.5) = 2.0 pts -> *100*1 = 200
        assert e.calendar_value == 200.0

    def test_total_credit_is_zero_in_calendar_phase(self):
        e = self._calendar()
        e.call_spread_credit = 999.0  # would be summed by the IC property
        e.put_spread_credit = 999.0
        assert e.total_credit == 0.0  # debit entry — no credit collected

    def test_spread_width_uses_wing_not_zero(self):
        e = self._calendar()
        # IC width would be long_strike - short_strike = 0 (same strike). The
        # calendar must report the wing width instead.
        assert e.spread_width == 5.0

    def test_spread_value_is_debit_value_not_clamped(self):
        e = self._calendar()
        # IC clamp would be [0, width=0] -> degenerate. Calendar value = long-short.
        assert e.call_spread_value == 100.0   # (3-2)*100
        assert e.put_spread_value == 100.0    # (3.5-2.5)*100

    def test_unrealized_pnl_is_value_minus_debit(self):
        e = self._calendar()
        assert e.unrealized_pnl == 200.0 - 150.0  # 50.0


class TestTransformedPhaseEconomics:
    def _transformed(self):
        e = CalendarEntry(entry_number=1)
        e.contracts = 1
        e.dc_phase = DCPhase.TRANSFORMED
        # post-transform the longs moved to wing strikes on the near expiry -> a
        # real same-expiry iron condor.
        e.short_call_strike = 5000.0
        e.long_call_strike = 5005.0   # wing
        e.short_put_strike = 4900.0
        e.long_put_strike = 4895.0    # wing
        e.short_call_price = 1.0
        e.long_call_price = 0.5
        e.short_put_price = 1.0
        e.long_put_price = 0.5
        e.call_spread_credit = 120.0
        e.put_spread_credit = 130.0
        e.net_debit = 150.0
        e.wing_width = 5.0
        e.transform_credit = 600.0
        return e

    def test_total_credit_uses_ic_credits_when_transformed(self):
        e = self._transformed()
        assert e.total_credit == 250.0  # 120 + 130 (super)

    def test_spread_width_real_vertical_when_transformed(self):
        e = self._transformed()
        assert e.spread_width == 5.0  # max(5005-5000, 4900-4895) via super

    def test_spread_value_uses_ic_clamp_when_transformed(self):
        e = self._transformed()
        # super: (short-long)*100 clamped [0, width*100]; (1.0-0.5)*100 = 50
        assert e.call_spread_value == 50.0
        assert e.put_spread_value == 50.0

    def test_unrealized_pnl_transformed_formula(self):
        e = self._transformed()
        # (transform_credit - net_debit) - (call_val + put_val) = (600-150)-(50+50)
        assert e.unrealized_pnl == 350.0

    def test_unrealized_pnl_closed_is_zero(self):
        e = self._transformed()
        e.dc_phase = DCPhase.CLOSED
        assert e.unrealized_pnl == 0.0


class TestRiskFreeInvariant:
    def test_threshold_and_evaluation(self):
        e = CalendarEntry(entry_number=1)
        e.contracts = 1
        e.net_debit = 150.0
        e.wing_width = 5.0
        # threshold = 150 + 5*100*1 = 650
        assert e.risk_free_threshold() == 650.0
        e.transform_credit = 600.0
        assert e.evaluate_risk_free() is False
        assert e.is_risk_free is False
        e.transform_credit = 700.0
        assert e.evaluate_risk_free() is True
        assert e.is_risk_free is True

    def test_threshold_scales_with_contracts(self):
        e = CalendarEntry(entry_number=1)
        e.contracts = 10
        e.net_debit = 1500.0
        e.wing_width = 5.0
        assert e.risk_free_threshold() == 1500.0 + 5 * 100 * 10  # 6500


class TestActiveEntriesRecognition:
    """The payoff of subclassing IronCondorEntry: the UNCHANGED base
    active_entries treats a live CalendarEntry as active with zero edits to the
    fix-scarred monitoring loop."""

    def test_complete_calendar_with_conid_is_active(self):
        state = MEICDailyState()
        e = CalendarEntry(entry_number=1)
        e.is_complete = True
        e.short_call_uic = 111  # a real conid -> has an open position
        state.entries.append(e)
        active = state.active_entries
        assert len(active) == 1
        assert active[0] is e

    def test_iron_condor_still_works_alongside(self):
        # Sanity: a plain IronCondorEntry is unaffected by the new field/subclass.
        ic = IronCondorEntry(entry_number=1)
        ic.short_call_strike = 5000.0
        ic.long_call_strike = 5075.0
        assert ic.spread_width == 75.0  # standard IC width math intact
