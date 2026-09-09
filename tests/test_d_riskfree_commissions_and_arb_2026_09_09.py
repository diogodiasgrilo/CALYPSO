"""
Strategy D — Phase 0.1/0.3: charge commissions in the risk-free gate, and stop
the transformer firing on arbitrage-impossible quotes (2026-09-09).

BACKGROUND
----------
D's calendar leg is 0-for-23. Every dollar D has ever made came from the
"risk-free transform", so that gate IS the strategy's thesis. Two defects:

1. NO COMMISSION ANYWHERE IN THE GATE. Worst-case IC value at expiry is exactly
   `wing*100*n` and the old threshold was exactly `net_debit + wing*100*n`, so
   worst-case realized P&L was exactly $0 BEFORE fees — the invariant had zero
   margin by construction. On top of that the transformer's own 4 legs (sell 2
   longs, buy 2 wings) booked zero commission anywhere, and `dc_edge` derives
   commission from `close_commission`, which is $0 for a transform that settles
   at expiry — so D's only winner was scored entirely fee-free.

   Measured: `dctm_20260818_001` cleared the old gate by $11.50; 8 legs at
   $1.15 cost $9.20; true margin $2.30 = 0.16% of its own credit.

2. NO ARB SANITY, AND IT HAS ALREADY FIRED ON BAD DATA. `_CAL_ARB_EPS` guards
   only the CALENDAR phase inside `_dc_refresh_marks`. The transform gate
   checked nothing and read its legs in THREE separate quote batches. The wing
   price enters `transform_credit` with a MINUS sign, so a stale/too-cheap wing
   inflates the credit in exactly the direction that fires a bad transform.

   Real case: `dctm_20260901_001` transformed into a 7580/7575 put vertical —
   5pt wide, a $500 ceiling — and recorded `put_spread_credit` $538.40. The
   next snapshot confirms it: short_put 49.15 vs long_put 43.766 = 5.384 > 5.00.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.calendar_entry import CalendarEntry, DCPhase  # noqa: E402
from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy  # noqa: E402


def _q(mid, bid=None, ask=None):
    bid = mid - 0.1 if bid is None else bid
    ask = mid + 0.1 if ask is None else ask
    return {"mid": mid, "raw": {"bid": bid, "ask": ask}, "realtime": True}


# ---------------------------------------------------------------------------
# 0.1 — commissions in the risk-free threshold
# ---------------------------------------------------------------------------

class TestRiskFreeThresholdChargesCommission:
    def _entry(self, **kw):
        e = CalendarEntry(entry_number=1)
        e.net_debit = 945.0
        e.wing_width = 5.0
        e.contracts = 1
        for k, v in kw.items():
            setattr(e, k, v)
        return e

    def test_unstamped_entry_reproduces_the_old_threshold_exactly(self):
        """Backward compatibility: every historical row has no commission
        fields, and must score identically to before."""
        e = self._entry()
        assert e.risk_free_threshold() == 945.0 + 5.0 * 100 * 1

    def test_threshold_includes_open_and_transform_commission(self):
        e = self._entry(open_commission=4 * 1.15, commission_per_leg=1.15)
        # 4 open legs + 4 transform legs = 8 x 1.15 = 9.20
        assert e.risk_free_threshold() == pytest.approx(1445.0 + 9.20)

    def test_the_real_2026_08_18_transform_margin_collapses_to_2_30(self):
        """THE number this whole exercise was run to get. D's only settled
        winner cleared by $11.50 before fees and $2.30 after."""
        e = self._entry(open_commission=4 * 1.15, commission_per_leg=1.15)
        e.transform_credit = 1456.50
        assert e.risk_free_threshold() == pytest.approx(1454.20)
        assert e.transform_credit - e.risk_free_threshold() == pytest.approx(2.30)
        assert e.evaluate_risk_free() is True          # still clears...
        assert (e.transform_credit - e.risk_free_threshold()) / e.transform_credit < 0.002
        # ...but by 0.16% of its own credit, i.e. noise under any fill model.

    def test_the_real_2026_09_01_transform_retains_real_margin(self):
        """The contrast case: this one clears with room to spare, so charging
        fees does NOT kill every transform — the test would be worthless if it
        did (it would prove only that the threshold went up)."""
        e = self._entry(net_debit=895.0, open_commission=4 * 1.15,
                        commission_per_leg=1.15)
        e.transform_credit = 1558.40
        assert e.transform_credit - e.risk_free_threshold() == pytest.approx(154.20)
        assert e.evaluate_risk_free() is True

    def test_a_transform_inside_the_fee_band_now_fails(self):
        """The behavioural change: credit between the old and new threshold
        used to pass and must now fail."""
        e = self._entry(open_commission=4 * 1.15, commission_per_leg=1.15)
        e.transform_credit = 1450.0        # > 1445.00 old, < 1454.20 new
        assert e.evaluate_risk_free() is False

    def test_threshold_scales_with_contracts(self):
        e = self._entry(contracts=10, open_commission=4 * 1.15 * 10,
                        commission_per_leg=1.15)
        assert e.risk_free_threshold() == pytest.approx(945.0 + 5000.0 + 92.0)


# ---------------------------------------------------------------------------
# 0.3 — arb sanity on the transformer's own inputs
# ---------------------------------------------------------------------------

class TestTransformArbSanity:
    def _strat(self, quotes, wing=5.0, agg=0.0):
        s = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        s.dc_wing_width = wing
        s.contracts_per_entry = 1
        s._dc_fill_agg = agg
        s._dc_fill_slippage = 0.0
        s.commission_per_leg = 0.0        # isolate the arb check from fees
        s._get_option_uic = MagicMock(side_effect=[101, 102])
        s._dc_read_leg_quotes = MagicMock(return_value=quotes)
        s._dc_recorder = None
        s.daily_state = MagicMock(total_commission=0.0)
        return s

    def _entry(self, net_debit=200.0):
        e = CalendarEntry(entry_number=1)
        e.net_debit = net_debit
        e.contracts = 1
        e.short_call_strike = e.long_call_strike = 7700.0
        e.short_put_strike = e.long_put_strike = 7580.0
        e.legs["short_call"].expiry = "2026-09-11"
        e.legs["short_call"].uic = 1
        e.legs["short_put"].uic = 3
        e.legs["long_call"].uic = 2
        e.legs["long_put"].uic = 4
        e.dc_phase = DCPhase.CALENDAR
        return e

    def _run(self, s, e):
        mod = __import__("bots.hydra.double_calendar_strategy", fromlist=["x"])
        with patch.object(mod, "get_us_market_time") as t:
            t.return_value = MagicMock(isoformat=lambda: "2026-09-01T14:44:30",
                                       strftime=lambda f: "2026-09-01")
            return s._dc_attempt_transform(e)

    def test_the_real_2026_09_01_impossible_put_vertical_is_rejected(self):
        """THE regression case, reconstructed so the ARB GUARD is the only
        thing that can reject it.

        Real numbers: short_put 49.15 vs wing_put 43.766 on a 5pt wing implies
        selling a 5-wide vertical for 5.384 — impossible, and it was booked as
        put_spread_credit $538.40 against a $500 ceiling.

        Crucially the real transform PASSED the credit gate (credit $1,558.40
        vs threshold $1,395.00), so the longs are priced here to reproduce that:
        (40 + 45 - 25.634 - 43.766) x 100 = $1,560 >= $1,395. If the arb guard
        is removed this transform FIRES, which is exactly what happened on
        2026-09-01. An earlier version of this test used cheap longs and was
        silently rejected by the credit gate instead — it passed with the guard
        deleted, i.e. proved nothing.
        """
        quotes = {
            "long_call": _q(40.0), "long_put": _q(45.0),
            "wing_call": _q(25.634), "wing_put": _q(43.766),
            "short_call": _q(28.0),      # call side 2.366 <= 5, legal
            "short_put": _q(49.15),      # put side 5.384 > 5, IMPOSSIBLE
        }
        s = self._strat(quotes)
        e = self._entry(net_debit=895.0)
        # Sanity: the credit gate alone would let this through.
        credit = (40.0 + 45.0 - 25.634 - 43.766) * 100
        assert credit > e.net_debit + 5.0 * 100, "test would not isolate the guard"

        assert self._run(s, e) is False
        assert e.dc_phase == DCPhase.CALENDAR      # still a calendar, not an IC

    def test_impossible_call_side_is_rejected_too(self):
        quotes = {
            "long_call": _q(30.0), "long_put": _q(30.0),
            "wing_call": _q(10.0), "wing_put": _q(20.0),
            "short_call": _q(16.0), "short_put": _q(22.0),   # 16-10 = 6 > 5
        }
        s = self._strat(quotes)
        assert self._run(s, self._entry()) is False

    def test_NEGATIVE_CONTROL_a_legal_spread_still_transforms(self):
        """Guards against a guard that rejects everything — without this the
        rejection tests above would pass on a broken implementation."""
        quotes = {
            "long_call": _q(30.0), "long_put": _q(30.0),
            "wing_call": _q(20.0), "wing_put": _q(20.0),
            "short_call": _q(22.0), "short_put": _q(22.0),   # 2.0 <= 5 wing
        }
        s = self._strat(quotes)
        e = self._entry(net_debit=200.0)
        # credit = (30+30-20-20)*100 = 2000 >= 200 + 500 = 700
        assert self._run(s, e) is True
        assert e.dc_phase == DCPhase.TRANSFORMED

    def test_exactly_at_the_wing_width_is_allowed(self):
        """A vertical worth exactly its width is the legal boundary, not a
        violation — rejecting it would block legitimate deep-ITM transforms."""
        quotes = {
            "long_call": _q(30.0), "long_put": _q(30.0),
            "wing_call": _q(20.0), "wing_put": _q(20.0),
            "short_call": _q(25.0), "short_put": _q(25.0),   # exactly 5.0
        }
        s = self._strat(quotes)
        assert self._run(s, self._entry()) is True

    def test_spread_credits_are_clamped_to_the_wing_width(self):
        """Defence in depth: even a legal-but-boundary quote set must not book
        a side credit above the vertical's ceiling."""
        quotes = {
            "long_call": _q(30.0), "long_put": _q(30.0),
            "wing_call": _q(20.0), "wing_put": _q(20.0),
            "short_call": _q(25.0), "short_put": _q(25.0),
        }
        s = self._strat(quotes)
        e = self._entry()
        assert self._run(s, e) is True
        assert e.call_spread_credit <= 5.0 * 100 * 1
        assert e.put_spread_credit <= 5.0 * 100 * 1


class TestTransformCommissionIsBooked:
    def test_transform_books_its_own_four_legs(self):
        quotes = {
            "long_call": _q(30.0), "long_put": _q(30.0),
            "wing_call": _q(20.0), "wing_put": _q(20.0),
            "short_call": _q(22.0), "short_put": _q(22.0),
        }
        s = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        s.dc_wing_width = 5.0
        s.contracts_per_entry = 1
        s._dc_fill_agg = 0.0
        s._dc_fill_slippage = 0.0
        s.commission_per_leg = 1.15
        s._get_option_uic = MagicMock(side_effect=[101, 102])
        s._dc_read_leg_quotes = MagicMock(return_value=quotes)
        s._dc_recorder = None

        class _DS:
            total_commission = 0.0
        s.daily_state = _DS()

        e = CalendarEntry(entry_number=1)
        e.net_debit = 200.0
        e.contracts = 1
        e.short_call_strike = e.long_call_strike = 7700.0
        e.short_put_strike = e.long_put_strike = 7580.0
        e.legs["short_call"].expiry = "2026-09-11"
        for k, u in (("short_call", 1), ("long_call", 2), ("short_put", 3), ("long_put", 4)):
            e.legs[k].uic = u

        mod = __import__("bots.hydra.double_calendar_strategy", fromlist=["x"])
        with patch.object(mod, "get_us_market_time") as t:
            t.return_value = MagicMock(isoformat=lambda: "2026-09-09T10:00:00",
                                       strftime=lambda f: "2026-09-09")
            assert s._dc_attempt_transform(e) is True

        assert e.transform_commission == pytest.approx(4 * 1.15 * 1)
        assert s.daily_state.total_commission == pytest.approx(4.60)
