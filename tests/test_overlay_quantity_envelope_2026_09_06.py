"""2026-09-06: ORDER-006b — a pre-flight quantity envelope on defensive-overlay
placement, closing the last hole in the 2026-06-10 98-vs-14 bug class.

WHY THE EXISTING GUARDS DO NOT COVER THIS (both verified against the real code):

  ORDER-006 check 1 (per-order cap) is STRUCTURALLY UNREACHABLE in the overlay
  placement loop. That loop computes `chunk = min(remaining, chunk_cap)` with
  `chunk_cap = max_contracts_per_order`, so every quantity handed to
  _validate_order_size is <= the cap BY CONSTRUCTION. `amount >
  max_contracts_per_order` can never be true there, no matter how wrong
  `remaining` is. A 98-contract leg simply places as 7 chunks of 14.

  ORDER-006 check 2 (per-underlying cap) compares against
  _get_current_position_size(), which excludes the overlay currently being
  placed — Brandon records hedge legs only after a full fill — so the baseline
  is CONSTANT across one structure's chunks. That method's own docstring calls
  this a "bounded soft-edge", which is true for ACCUMULATION across overlays
  but says nothing about a single structure whose leg quantity is itself wrong.

The 2026-07-21 fix removed the specific multiplication bug that produced
98-vs-14 (a `for q in range(leg.quantity)` loop where each call already placed
contracts_per_entry). It did not add a guard against the CLASS — any future
mis-sized proposal would still be placed faithfully. ORDER-006b is that guard:
it validates the PROPOSAL against contracts_per_entry before any order goes
out, and fails closed.

Currently latent on B (both overlay structures are disabled there since
2026-09-04) but live-relevant the moment a hedge is ever re-enabled, and
reachable on C today.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.brandon.strategy import (  # noqa: E402
    BrandonHydraStrategy,
    _OVERLAY_MAX_LEG_MULTIPLE,
    _OVERLAY_MAX_TOTAL_MULTIPLE,
)
from bots.hydra.brandon.defensive_overlay import (  # noqa: E402
    OverlayLeg, OverlayProposal, OverlayStructure,
)


def _leg(side, ctype, strike, qty):
    # The REAL OverlayLeg, so a future field/validation change surfaces here
    # rather than being papered over by a SimpleNamespace stand-in.
    return OverlayLeg(side=side, contract_type=ctype, strike=float(strike),
                      quantity=int(qty))


def _proposal(structure, side, legs, pin=None):
    return OverlayProposal(structure=structure, threatened_side=side,
                           legs=tuple(legs), pin_strike=pin,
                           reason="test-fixture")


def _butterfly(contracts, body_qty=None):
    """A normal butterfly: long 1x / short 2x / long 1x. `body_qty` overrides
    the short body so a mis-sized structure can be simulated."""
    body = body_qty if body_qty is not None else 2 * contracts
    return _proposal(
        OverlayStructure.BUTTERFLY, "call",
        [_leg("long", "call", 7720, contracts),
         _leg("short", "call", 7730, body),
         _leg("long", "call", 7740, contracts)],
        pin=7730.0,
    )


def _strat(contracts=7):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s.contracts_per_entry = contracts
    s.max_contracts_per_order = 15
    s._brandon_send_telegram = MagicMock()
    s._log_safety_event = MagicMock()
    # The guard is the FIRST thing _brandon_place_overlay does — deliberately
    # ahead of the dry-run branch, so a mis-sized structure is refused in
    # simulation too (C is the canary for exactly this bug class). So "the
    # guard let it through" surfaces as the method proceeding to work this
    # fixture intentionally does not provide.
    s.current_price = MagicMock(side_effect=AssertionError(
        "guard did not fail closed — method proceeded past the envelope check"))
    return s


def _place(strat, proposal, entry_number=4, entry_contracts=None):
    # Production derives the overlay's size from the hedged ENTRY
    # (OverlayConfig(contracts=entry.contracts)), so the guard's baseline is
    # entry.contracts — mirror that here rather than the strategy default.
    if entry_contracts is None:
        entry_contracts = strat.contracts_per_entry
    entry = SimpleNamespace(entry_number=entry_number, contracts=entry_contracts)
    return BrandonHydraStrategy._brandon_place_overlay(strat, entry, proposal)


class TestRejectsTheHistoricalBugShape:
    def test_the_actual_98_vs_14_shape_is_refused(self):
        """The real 2026-06-10 incident: 7 contracts/entry, a butterfly body
        that should have been 14 came out as 98."""
        s = _strat(contracts=7)
        assert _place(s, _butterfly(7, body_qty=98)) is None   # refused, no exception
        s._log_safety_event.assert_called_once()
        assert s._log_safety_event.call_args[0][0] == "OVERLAY_SIZE_REJECTED"

    def test_rejection_alerts_at_critical(self):
        s = _strat(contracts=7)
        _place(s, _butterfly(7, body_qty=98))
        kw = s._brandon_send_telegram.call_args.kwargs
        assert kw["priority_name"] == "CRITICAL"
        assert kw["alert_type_name"] == "CIRCUIT_BREAKER"

    def test_total_envelope_catches_it_even_if_no_single_leg_does(self):
        """Defence in depth: spread the over-placement across legs so each one
        stays under the per-leg bound, and the TOTAL bound must still fire."""
        s = _strat(contracts=7)
        # 3 legs x 20 = 60 total. Per-leg limit 21 (not tripped); total limit 42.
        p = _proposal(OverlayStructure.BUTTERFLY, "call",
                      [_leg("long", "call", 7720, 20),
                       _leg("short", "call", 7730, 20),
                       _leg("long", "call", 7740, 20)], pin=7730.0)
        assert _place(s, p) is None
        s._log_safety_event.assert_called_once()

    def test_negative_quantities_are_measured_by_magnitude(self):
        """A sign error must not smuggle an oversized leg past the check."""
        s = _strat(contracts=7)
        assert _place(s, _butterfly(7, body_qty=-98)) is None
        s._log_safety_event.assert_called_once()


class TestDoesNotBlockLegitimateStructures:
    """The guard is worthless if it fires on real overlays — these are the
    exact shapes the overlay actually proposes."""

    def _reaches_placement(self, strat, proposal):
        """A legitimate structure must get PAST the guard. The fixture stops
        short of real placement, so passing shows up as the method continuing
        into work the fixture doesn't stub."""
        with pytest.raises((AssertionError, AttributeError, TypeError)):
            _place(strat, proposal)

    def test_normal_butterfly_at_7_contracts_passes(self):
        s = _strat(contracts=7)          # 7 + 14 + 7 = 28, limits 21/42
        self._reaches_placement(s, _butterfly(7))

    def test_normal_butterfly_at_1_contract_passes(self):
        s = _strat(contracts=1)          # 1 + 2 + 1 = 4, limits 3/6
        self._reaches_placement(s, _butterfly(1))

    def test_normal_debit_spread_passes(self):
        s = _strat(contracts=7)          # 7 + 7 = 14, limits 21/42
        p = _proposal(OverlayStructure.DEBIT_SPREAD, "put",
                      [_leg("long", "put", 7600, 7),
                       _leg("short", "put", 7590, 7)])
        self._reaches_placement(s, p)

    def test_butterfly_at_10_contracts_passes(self):
        """B ran 10 contracts before the 2026-07-24 swap; the envelope must
        scale with contracts_per_entry rather than being an absolute number."""
        s = _strat(contracts=10)         # 10 + 20 + 10 = 40, limits 30/60
        self._reaches_placement(s, _butterfly(10))


class TestEnvelopeBounds:
    def test_bounds_admit_a_butterfly_with_headroom(self):
        """A butterfly is 2x on the body and 4x in total; the bounds must sit
        above both or every legitimate overlay would be refused."""
        assert _OVERLAY_MAX_LEG_MULTIPLE > 2
        assert _OVERLAY_MAX_TOTAL_MULTIPLE > 4

    def test_bounds_are_tight_enough_to_catch_a_7x_error(self):
        """At 7 contracts/entry the historical bug was a 98-contract leg
        (7x the correct 14). Both bounds must reject it."""
        cpe = 7
        assert 98 > _OVERLAY_MAX_LEG_MULTIPLE * cpe
        assert (7 + 98 + 7) > _OVERLAY_MAX_TOTAL_MULTIPLE * cpe


class TestGuardIsActuallyWired:
    def test_preflight_runs_before_any_order(self):
        """Source-level pin: the envelope check must sit BEFORE the placement
        loop. If it drifted below, it would validate after orders were already
        live — the exact failure it exists to prevent."""
        import inspect
        src = inspect.getsource(BrandonHydraStrategy._brandon_place_overlay)
        guard = src.index("ORDER-006b")
        loop = src.index("for i, leg in ordered_legs:")
        assert guard < loop
