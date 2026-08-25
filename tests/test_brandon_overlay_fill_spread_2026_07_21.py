"""2026-08-25: real-quote-mid dry-run hedge pricing (supersedes the original
2026-07-21 fix this file is named for).

The 2026-07-21 fix crossed a Black-Scholes MID by a flat ±$0.25/leg to
de-inflate the modeled overlay P&L. The 2026-08-24 audit found the BS-model
base price itself diverged from B's real fill by 4.4x on an identical
structure. The fix replaces the model with REAL broker quotes (resolved via
_get_option_uic + _read_option_quotes_batch, same as every other simulated
leg in this codebase), priced at the quote mid — dropping the flat-spread
crossing entirely since it was compensating for the wrong base price.
hedge_position.estimate_fill_price is now a fallback ONLY for a leg whose
live quote is genuinely unavailable.
"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from bots.hydra.brandon import hedge_position  # noqa: E402
from bots.hydra.brandon.defensive_overlay import (  # noqa: E402
    OverlayProposal, OverlayLeg, OverlayStructure,
)


def _stub(quotes_by_strike=None, expiry="2026-07-21"):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s.dry_run = True
    s.current_price = 7510.0
    s._brandon_hedge_legs = {}
    s._brandon_hedge_recorder = None
    s._brandon_estimate_t_years_to_close = lambda: 0.01
    s._brandon_now_et = lambda: datetime(2026, 7, 21, 13, 0)
    s._brandon_save_hedge_state = lambda: None
    s._brandon_send_telegram = lambda *a, **k: None
    s._brandon_record_hedge_placement = lambda *a, **k: None
    s._get_todays_expiry = lambda: expiry

    quotes_by_strike = quotes_by_strike or {}
    conid_by_strike = {strike: 90000 + i for i, strike in enumerate(sorted(quotes_by_strike))}

    def _get_option_uic(strike, put_call, exp):
        assert exp == expiry
        return conid_by_strike.get(strike)

    def _read_option_quotes_batch(conids):
        out = {}
        for strike, conid in conid_by_strike.items():
            if conid in conids:
                out[conid] = quotes_by_strike[strike]
        return out

    s._get_option_uic = _get_option_uic
    s._read_option_quotes_batch = _read_option_quotes_batch
    return s


def _butterfly():
    return OverlayProposal(
        structure=OverlayStructure.BUTTERFLY,
        threatened_side="call",
        legs=(
            OverlayLeg("long", "call", 7500.0, 10),
            OverlayLeg("short", "call", 7510.0, 20),
            OverlayLeg("long", "call", 7520.0, 10),
        ),
        pin_strike=7510.0,
        reason="test butterfly",
    )


class TestOverlayRealQuotePricing:
    def test_uses_real_quote_mid_for_every_leg_when_available(self):
        quotes = {
            7500.0: {"bid": 12.0, "ask": 12.4, "mid": 12.2, "last": None, "mark": None},
            7510.0: {"bid": 8.0, "ask": 8.4, "mid": 8.2, "last": None, "mark": None},
            7520.0: {"bid": 5.0, "ask": 5.4, "mid": 5.2, "last": None, "mark": None},
        }
        s = _stub(quotes)
        s._brandon_place_overlay(SimpleNamespace(entry_number=5), _butterfly())
        legs = {(l.strike, l.side): l for l in s._brandon_hedge_legs[5]}

        assert legs[(7500.0, "long")].fill_price == pytest.approx(12.2)
        assert legs[(7510.0, "short")].fill_price == pytest.approx(8.2)
        assert legs[(7520.0, "long")].fill_price == pytest.approx(5.2)

    def test_records_the_resolved_conid_on_dry_run_legs(self):
        quotes = {7500.0: {"bid": 1.0, "ask": 1.2, "mid": 1.1, "last": None, "mark": None}}
        s = _stub(quotes)
        proposal = OverlayProposal(
            structure=OverlayStructure.DEBIT_SPREAD,
            threatened_side="call",
            legs=(OverlayLeg("long", "call", 7500.0, 5),),
            pin_strike=None,
            reason="test",
        )
        s._brandon_place_overlay(SimpleNamespace(entry_number=1), proposal)
        leg = s._brandon_hedge_legs[1][0]
        assert leg.conid == 90000
        assert leg.fill_price == pytest.approx(1.1)

    def test_falls_back_to_black_scholes_when_no_quote_available(self, caplog):
        s = _stub(quotes_by_strike={})  # no conids resolve → no quotes anywhere
        with caplog.at_level("WARNING"):
            s._brandon_place_overlay(SimpleNamespace(entry_number=1), _butterfly())
        for leg in s._brandon_hedge_legs[1]:
            expected = hedge_position.estimate_fill_price(
                contract_type="call", strike=leg.strike, spot=7510.0, t_years=0.01)
            assert leg.fill_price == pytest.approx(expected)
            assert leg.conid is None
        assert any("BRANDON-OVERLAY-DRYRUN-FALLBACK" in r.message for r in caplog.records)

    def test_mixed_availability_falls_back_only_for_the_unquoted_leg(self, caplog):
        # Only the short (pin) leg has a live quote; both long wings don't.
        quotes = {7510.0: {"bid": 8.0, "ask": 8.4, "mid": 8.2, "last": None, "mark": None}}
        s = _stub(quotes)
        with caplog.at_level("WARNING"):
            s._brandon_place_overlay(SimpleNamespace(entry_number=1), _butterfly())
        legs = {(l.strike, l.side): l for l in s._brandon_hedge_legs[1]}

        assert legs[(7510.0, "short")].fill_price == pytest.approx(8.2)
        assert legs[(7510.0, "short")].conid == 90000

        for strike in (7500.0, 7520.0):
            expected = hedge_position.estimate_fill_price(
                contract_type="call", strike=strike, spot=7510.0, t_years=0.01)
            assert legs[(strike, "long")].fill_price == pytest.approx(expected)
            assert legs[(strike, "long")].conid is None

        fallback_lines = [r.message for r in caplog.records if "BRANDON-OVERLAY-DRYRUN-FALLBACK" in r.message]
        assert len(fallback_lines) == 2  # exactly the two unquoted legs

    def test_no_expiry_falls_back_for_every_leg(self):
        s = _stub({7500.0: {"bid": 1.0, "ask": 1.2, "mid": 1.1, "last": None, "mark": None}}, expiry=None)
        s._brandon_place_overlay(SimpleNamespace(entry_number=1), _butterfly())
        for leg in s._brandon_hedge_legs[1]:
            expected = hedge_position.estimate_fill_price(
                contract_type="call", strike=leg.strike, spot=7510.0, t_years=0.01)
            assert leg.fill_price == pytest.approx(expected)
            assert leg.conid is None
