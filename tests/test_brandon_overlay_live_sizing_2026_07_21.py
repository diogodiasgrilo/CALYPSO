"""2026-07-21: LIVE Brandon defensive-overlay placement sizing + atomicity.

Regression for the overlay over-placement bug found while auditing the B<->C
swap. Overlay leg `quantity` is ALREADY scaled to contracts_per_entry (a
butterfly is 10/20/10 for a 10-lot variant = 40 contracts total). The old live
path looped `for q in range(leg.quantity)` and each _place_option_order placed
contracts_per_entry contracts → it attempted leg.quantity × contracts_per_entry
= 400 on a 40-contract butterfly. The max_contracts_per_underlying cap then
truncated it mid-structure into a naked short with no unwind. It never fired in
production only because the sole live variant (C) had overlays OFF — but it
would the instant an overlay-enabled variant (B) went live.

The fix: place each leg ONCE at leg.quantity (chunked by max_contracts_per_order)
via a quantity-aware _place_option_order, and make the overlay ATOMIC — a
partial fill unwinds every filled leg rather than stranding a naked short.
"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from bots.hydra.order_types import BuySell  # noqa: E402
from bots.hydra.brandon.defensive_overlay import (  # noqa: E402
    OverlayProposal, OverlayLeg, OverlayStructure,
)


def _live_stub(max_per_order=15, contracts_per_entry=10):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s.dry_run = False
    s.contracts_per_entry = contracts_per_entry
    s.max_contracts_per_order = max_per_order
    s.current_price = 7510.0
    s._brandon_hedge_legs = {}
    s._brandon_estimate_t_years_to_close = lambda: 0.01
    s._brandon_now_et = lambda: datetime(2026, 7, 21, 13, 0)
    s._brandon_save_hedge_state = lambda: None
    s._brandon_send_telegram = lambda *a, **k: None
    s._get_todays_expiry = lambda: "2026-07-21"
    return s


def _butterfly():
    # A 10-lot variant's butterfly: wings 10, body 20 → 40 contracts total.
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


class TestLiveOverlaySizing:
    def test_places_exact_contracts_not_contracts_per_entry_fold(self):
        """The whole point: 40 contracts, not 400."""
        calls = []

        def place(**kw):
            calls.append(kw)
            return {"uic": int(kw["strike"]), "fill_price": 1.0}

        s = _live_stub()
        s._place_option_order = place
        s._flatten_accumulated_partial = lambda *a, **k: pytest.fail(
            "must not unwind on a full fill")
        s._brandon_place_overlay(SimpleNamespace(entry_number=5), _butterfly())

        total = sum(int(c["quantity"]) for c in calls)
        assert total == 40, f"expected 40 contracts, placed {total}"

    def test_every_order_respects_max_contracts_per_order(self):
        calls = []

        def place(**kw):
            calls.append(kw)
            return {"uic": int(kw["strike"]), "fill_price": 1.0}

        s = _live_stub(max_per_order=15)
        s._place_option_order = place
        s._flatten_accumulated_partial = lambda *a, **k: None
        s._brandon_place_overlay(SimpleNamespace(entry_number=5), _butterfly())

        assert all(int(c["quantity"]) <= 15 for c in calls)
        # the 20-lot body is chunked 15 + 5
        body = sorted(int(c["quantity"]) for c in calls if int(c["strike"]) == 7510)
        assert body == [5, 15]

    def test_full_fill_tracks_legs_with_absolute_quantities(self):
        s = _live_stub()
        s._place_option_order = lambda **kw: {"uic": int(kw["strike"]), "fill_price": 1.0}
        s._flatten_accumulated_partial = lambda *a, **k: None
        s._brandon_place_overlay(SimpleNamespace(entry_number=5), _butterfly())

        legs = s._brandon_hedge_legs[5]
        assert sorted(l.quantity for l in legs) == [10, 10, 20]

    def test_mixed_priced_unpriced_chunks_average_over_priced_only(self):
        """Body 20 -> chunks 15 (priced 1.00) + 5 (filled, no price). The leg
        fill price must be 1.00 (avg over priced qty), NOT 0.75 (avg over all)."""
        calls = {"n": 0}

        def place(**kw):
            if int(kw["strike"]) == 7510:          # body leg, two chunks
                calls["n"] += 1
                if calls["n"] == 1:
                    return {"uic": 7510, "fill_price": 1.0}
                return {"uic": 7510}                # filled, but no fill_price
            return {"uic": int(kw["strike"]), "fill_price": 1.0}

        s = _live_stub()
        s._place_option_order = place
        s._flatten_accumulated_partial = lambda *a, **k: pytest.fail("full fill")
        s._brandon_place_overlay(SimpleNamespace(entry_number=6), _butterfly())

        body = next(l for l in s._brandon_hedge_legs[6] if l.strike == 7510.0)
        assert body.quantity == 20
        assert body.fill_price == pytest.approx(1.0)   # not 15.0/20 = 0.75

    def test_partial_fill_unwinds_all_filled_legs_and_does_not_track(self):
        """Upper wing fails → the two filled legs are flattened, nothing tracked."""
        flat_calls = []
        alerts = []

        def place(**kw):
            if int(kw["strike"]) == 7520:      # upper wing fails entirely
                return None
            return {"uic": int(kw["strike"]), "fill_price": 1.0}

        s = _live_stub()
        s._place_option_order = place
        s._flatten_accumulated_partial = lambda *a, **k: flat_calls.append(a)
        s._brandon_alert_overlay_partial = (
            lambda entry, proposal, *, placed, expected: alerts.append((placed, expected))
        )
        s._brandon_place_overlay(SimpleNamespace(entry_number=7), _butterfly())

        # nothing tracked for settlement
        assert not s._brandon_hedge_legs.get(7)
        # both filled legs (7500 wing=10, 7510 body=20) were flattened
        assert len(flat_calls) == 2
        flattened_conids = sorted(a[0] for a in flat_calls)
        assert flattened_conids == [7500, 7510]
        # operator was told: 30/40 filled
        assert alerts == [(30, 40)]

    def test_chunk_failure_mid_body_unwinds(self):
        """Body fills its first chunk (15) then the second (5) fails → partial."""
        seen = {"n": 0}
        flat_calls = []

        def place(**kw):
            if int(kw["strike"]) == 7510:
                seen["n"] += 1
                if seen["n"] == 2:             # second body chunk fails
                    return None
            return {"uic": int(kw["strike"]), "fill_price": 1.0}

        s = _live_stub()
        s._place_option_order = place
        s._flatten_accumulated_partial = lambda *a, **k: flat_calls.append(a)
        s._brandon_alert_overlay_partial = lambda *a, **k: None
        s._brandon_place_overlay(SimpleNamespace(entry_number=9), _butterfly())

        assert not s._brandon_hedge_legs.get(9)
        # filled: 7500 wing (10) + 7510 body first chunk (15) + 7520 wing (10)
        assert len(flat_calls) == 3


class TestPlaceOptionOrderQuantityAware:
    def test_place_option_order_forwards_explicit_quantity(self):
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.dry_run = False
        captured = {}
        s._place_option_order_ib = lambda *a, **k: captured.update(k) or {"uic": 1}
        s._place_option_order(
            strike=7500.0, put_call="Call", buy_sell=BuySell.BUY,
            expiry="2026-07-21", external_ref="x", quantity=5,
        )
        assert captured.get("quantity") == 5

    def test_place_option_order_default_quantity_is_none(self):
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.dry_run = False
        captured = {}
        s._place_option_order_ib = lambda *a, **k: captured.update(k) or {"uic": 1}
        s._place_option_order(
            strike=7500.0, put_call="Call", buy_sell=BuySell.BUY,
            expiry="2026-07-21", external_ref="x",
        )
        assert captured.get("quantity") is None

    def test_ib_validates_requested_quantity_not_contracts_per_entry(self):
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.dry_run = False
        s.contracts_per_entry = 10
        seen = []
        s._read_option_chain = lambda expiry, strikes: ({7500.0: 999}, {})
        s._validate_order_size = lambda amount, desc: (seen.append(amount), (False, "stop"))[1]
        r = s._place_option_order_ib(
            7500.0, "Call", BuySell.BUY, "2026-07-21", "x", quantity=13)
        assert seen == [13]     # validated the requested 13, not 10
        assert r is None        # validation False → None

    def test_ib_default_validates_contracts_per_entry(self):
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.dry_run = False
        s.contracts_per_entry = 10
        seen = []
        s._read_option_chain = lambda expiry, strikes: ({7500.0: 999}, {})
        s._validate_order_size = lambda amount, desc: (seen.append(amount), (False, "stop"))[1]
        s._place_option_order_ib(7500.0, "Call", BuySell.BUY, "2026-07-21", "x")
        assert seen == [10]

    def test_ib_rejects_non_positive_quantity(self):
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.dry_run = False
        s.contracts_per_entry = 10
        s._read_option_chain = lambda *a, **k: pytest.fail("should short-circuit before chain read")
        assert s._place_option_order_ib(
            7500.0, "Call", BuySell.BUY, "2026-07-21", "x", quantity=0) is None
