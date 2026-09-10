"""
Pre-live margin correctness: width-aware ORDER-004 floor + wing-symmetry
detection, and the false allOrNone claim (2026-09-10).

All three are invisible on the paper account and become real at the funding
level contemplated for live money.

1. WIDTH-AWARE BUYING-POWER FLOOR. `min_buying_power_per_ic` is a FLAT
   per-contract number ($500 on B/C), but a defined-risk iron condor's margin
   IS its width: width x $100. Brandon's narrow-spread rule switches to 10pt
   wings at VIX >= 22, needing $1,000/contract — so the ORDER-004 gate
   under-provisioned by 2x in exactly the high-volatility conditions where
   margin is tightest. Unnoticed because ~$1M of paper buying power sits
   against a ~$35k peak requirement, so the gate never binds. At $150k-$250k it
   is the difference between passing and a mid-entry rejection with legs
   already filled.

2. WING SYMMETRY. Per IBKR KB-600, unequal put/call strike distances are
   margined as TWO SEPARATE SPREADS — roughly double the symmetric requirement.
   Both MKT-045 chain snapping (25pt tolerance) and the Brandon GEX adjuster
   mutate strikes AFTER the width is chosen, so the wings can silently diverge.

3. THE allOrNone CLAIM WAS FALSE. `ib_client.place_iron_condor`'s docstring
   told a future implementer to "pass allOrNone=True via OrderRequest rather
   than building a partial-fill watcher". ibind 0.1.23 has no such field
   anywhere. Combos CAN partial by quantity, and `entry.contracts` is never
   read back from the broker — so a 6-of-10 fill would be booked as 10.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import MEICStrategy  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Width-aware ORDER-004 floor
# ---------------------------------------------------------------------------

def _strat(configured=500.0, width=5, vix=15.0):
    # MEICStrategy is abstract; use the concrete subclass that inherits
    # the method under test unchanged.
    s = HydraStrategy.__new__(HydraStrategy)
    s.min_buying_power_per_ic = configured
    s.current_vix = vix
    s._get_vix_adjusted_spread_width = lambda v, side="call": width
    return s


class TestWidthAwareBuyingPowerFloor:
    def test_5pt_wings_match_the_configured_500(self):
        """The configured value is correct at 5pt — this must not change."""
        assert _strat(500.0, width=5)._min_buying_power_per_unit() == 500.0

    def test_10pt_wings_raise_the_floor_to_1000(self):
        """THE BUG: at VIX >= 22 Brandon uses 10pt wings, needing $1,000, but
        the gate used the flat $500 — under-provisioning 2x."""
        assert _strat(500.0, width=10, vix=25.0)._min_buying_power_per_unit() == 1000.0

    def test_75pt_baseline_wings_raise_it_further(self):
        """Variant A's MKT-027 dynamic width can reach 75pt."""
        assert _strat(500.0, width=75)._min_buying_power_per_unit() == 7500.0

    def test_it_never_LOWERS_the_configured_floor(self):
        """Fail-safe direction. A narrow width must not be able to reduce a
        deliberately conservative operator setting."""
        assert _strat(5000.0, width=5)._min_buying_power_per_unit() == 5000.0

    def test_a_failing_width_lookup_degrades_to_configured(self):
        """A width-resolver exception must not block entries."""
        s = _strat(500.0)
        s._get_vix_adjusted_spread_width = MagicMock(side_effect=RuntimeError("no vix"))
        assert s._min_buying_power_per_unit() == 500.0

    def test_missing_vix_attribute_does_not_raise(self):
        s = _strat(500.0, width=5)
        del s.current_vix
        assert s._min_buying_power_per_unit() == 500.0

    def test_NEGATIVE_CONTROL_the_gate_actually_consumes_this(self):
        """Guards against the floor being computed correctly but ignored."""
        import inspect
        src = inspect.getsource(MEICStrategy._check_buying_power)
        assert "_min_buying_power_per_unit" in src


# ---------------------------------------------------------------------------
# 2. Wing symmetry
# ---------------------------------------------------------------------------

class TestWingSymmetryDetection:
    """The check lives at the end of _snap_entry_strikes_to_chain — the last
    point all four strikes are mutated."""

    def _run(self, sc, lc, sp, lp):
        s = HydraStrategy.__new__(HydraStrategy)
        s._log_safety_event = MagicMock()
        entry = SimpleNamespace(
            entry_number=1, short_call_strike=sc, long_call_strike=lc,
            short_put_strike=sp, long_put_strike=lp,
        )
        asym = HydraStrategy._check_wing_symmetry(s, entry)
        events = [c.args[0] for c in s._log_safety_event.call_args_list]
        return ("WING_ASYMMETRY" if asym else "") + "|" + "|".join(events)

    def test_symmetric_wings_are_silent(self):
        """5pt each side — the normal case. Must not warn, or the signal is
        worthless."""
        assert "WING_ASYMMETRY" not in self._run(7700, 7705, 7600, 7595)

    def test_asymmetric_wings_are_flagged(self):
        """Call side 5pt, put side 10pt — IBKR margins this as two spreads."""
        assert "WING_ASYMMETRY" in self._run(7700, 7705, 7600, 7590)

    def test_asymmetry_the_other_way_is_flagged_too(self):
        assert "WING_ASYMMETRY" in self._run(7700, 7710, 7600, 7595)

    def test_a_missing_strike_does_not_raise(self):
        """Strike fields can be None early in construction."""
        assert "WING_ASYMMETRY" not in self._run(7700, None, 7600, 7595)


# ---------------------------------------------------------------------------
# 3. The allOrNone claim
# ---------------------------------------------------------------------------

class TestAllOrNoneIsNotAvailable:
    def test_ibind_OrderRequest_has_no_allOrNone_field(self):
        """The load-bearing fact. If a future ibind upgrade ADDS this field,
        this test fails and the combo design can be revisited — which is
        exactly the notification we want."""
        from ibind.client.ibkr_utils import OrderRequest
        names = {f.name.lower() for f in dataclasses.fields(OrderRequest)}
        assert not any("allornone" in n.replace("_", "") for n in names)

    def test_conidex_IS_available(self):
        """The other half: ibind will transmit a combo ticket even though it
        offers no AON. It just won't guarantee an all-or-none fill."""
        from ibind.client.ibkr_utils import OrderRequest
        assert "conidex" in {f.name for f in dataclasses.fields(OrderRequest)}

    def test_the_false_docstring_claim_is_gone(self):
        """The previous text told a future implementer to rely on a field that
        does not exist, instead of building the fill-count reconcile they
        actually need. Flagged as the single most dangerous line in the combo
        work."""
        import inspect
        from shared.ib_client import IBClient
        doc = inspect.getdoc(IBClient.place_iron_condor) or ""
        # The old sentence is deliberately QUOTED in the correction so a reader
        # sees what was wrong — so assert on the CORRECTION being present, not
        # on the old string being absent.
        assert "WAS FALSE" in doc
        assert "zero" in doc.lower() and "allOrNone" in doc
        assert "partial by QUANTITY" in doc
        assert "fill-count reconcile" in doc
