"""The ORDER-004 margin floor was derived from the NARROWER side of the condor.

Found 2026-09-18, by being asked whether a recommendation was actually the best one —
not by a failure. The honest answer was no, because this had not been checked.

THE INVARIANT, stated by the very function being called
(``strategy._get_vix_adjusted_spread_width``):

    "MKT-028: Separate floors for calls (60pt) and puts (75pt) — put longs cost 7x more
     due to skew, so wider put spreads save more. ... margin = max(call_width, put_width)"

``_min_buying_power_per_unit`` asked it for ``"call"`` only. An iron condor's requirement
is the GREATER of its two verticals — only one side can finish in the money — so deriving
the floor from the narrower side under-provisions by exactly their ratio. At the
documented 60/75 floors that is 20%.

LATENT, NOT ACTIVE — and saying so precisely matters. Every live config on the VM today
sets ``call_min_spread_width == put_min_spread_width == 25``, so both widths come out
identical and the old call was right by coincidence. Nothing has been under-provisioned.
It becomes real the moment someone sets the asymmetric floors MKT-028 was designed around,
which is exactly the kind of change that gets made later, to a config, by someone who will
not re-read this function.

CONTEXT: this is the second half of a bug whose first half was fixed on 2026-09-10. That
change made the floor width-AWARE (it had been a flat $500 that under-provisioned 2x at
10pt wings) and its comment already reasoned about "the $150k-$250k funding level
contemplated for a real account" — then asked for the wrong side's width. A fix that gets
the hard part right and the last step wrong is the normal shape of a near-miss.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _strat(*, call_min: int, put_min: int, configured: float = 500.0,
           vix: float = 5.0, multiplier: float = 1.0, cap: int = 100):
    """A HydraStrategy stubbed to exactly what the floor calculation reads.

    THE VIX TERM IS DELIBERATELY TINY (5 x 1.0 = 5pt), so the per-side FLOORS are what
    binds. The first version of this file used the realistic vix=15 x 6.0 = 90pt, which
    is above both floors AND above the 75pt cap — so both sides clamped to 75, the
    asymmetry vanished, and the headline test PASSED AGAINST THE BUGGY CODE. A test that
    cannot distinguish the fix from the defect is worse than no test; it reports safety
    it has not checked.
    """
    s = HydraStrategy.__new__(HydraStrategy)
    s.min_buying_power_per_ic = configured
    s.current_vix = vix
    s.call_min_spread_width = call_min
    s.put_min_spread_width = put_min
    s.max_spread_width = cap
    s.spread_vix_multiplier = multiplier
    s.strike_increment = 5
    return s


# ══════════════════════════════════════════════════════════════════════════════
# THE DEFECT
# ══════════════════════════════════════════════════════════════════════════════

def test_the_floor_follows_the_WIDER_side():
    """THE BUG. MKT-028's documented floors: calls 60pt, puts 75pt.

    Margin is the greater of the two verticals, so the floor must be 75 x 100 = $7,500,
    not 60 x 100 = $6,000. The old code asked for "call" and returned the smaller.
    """
    s = _strat(call_min=60, put_min=75)
    assert s._min_buying_power_per_unit() == pytest.approx(7500.0), (
        "the ORDER-004 floor is derived from the narrower side — it under-provisions by "
        "the ratio between the two wing widths"
    )


def test_it_also_follows_a_wider_CALL_side():
    """Symmetry of the fix: max(), not "always the put". If the asymmetry is ever set
    the other way, the floor must track that too."""
    s = _strat(call_min=80, put_min=30)
    assert s._min_buying_power_per_unit() == pytest.approx(8000.0)
    # NOTE this direction cannot catch the original bug — the old code asked for "call",
    # which here IS the wider side. It is a correctness check on max(), not a regression
    # test. test_the_floor_follows_the_WIDER_side is the discriminating one.


# ══════════════════════════════════════════════════════════════════════════════
# NO-OP CONTROLS — nothing changes for any config that runs today
# ══════════════════════════════════════════════════════════════════════════════

def test_symmetric_widths_are_completely_unaffected():
    """THE CONTROL THAT MATTERS. Every live config is call_min == put_min == 25, so this
    change must be a no-op in production. If it is not, something else moved."""
    s = _strat(call_min=25, put_min=25)
    assert s._min_buying_power_per_unit() == pytest.approx(2500.0)


def test_the_live_configs_really_are_symmetric():
    """Pins the premise of the control above. If a config ever goes asymmetric, this
    test stops being true and the fix above stops being a no-op — which is the point at
    which someone should read this file.
    """
    import json
    cfgs = sorted((ROOT / "bots" / "hydra" / "config").glob("config_variant_*.json"))
    assert cfgs, "no tracked variant configs found"
    asymmetric = {}
    for p in cfgs:
        c = json.loads(p.read_text()).get("strategy", {})
        call, put = c.get("call_min_spread_width"), c.get("put_min_spread_width")
        if call is not None and put is not None and call != put:
            asymmetric[p.name] = (call, put)
    assert not asymmetric, (
        f"config(s) now set asymmetric wing floors: {asymmetric}. That is supported — "
        f"the floor follows the wider side since 2026-09-18 — but re-read "
        f"_min_buying_power_per_unit before relying on it."
    )


def test_the_configured_floor_is_still_a_floor_not_a_ceiling():
    """Unchanged behaviour: max(configured, derived). A width lookup can only ever RAISE
    the floor, never lower it below what the operator set."""
    s = _strat(call_min=5, put_min=5, configured=30000.0)
    assert s._min_buying_power_per_unit() == pytest.approx(30000.0)


def test_a_broken_width_lookup_falls_back_to_the_configured_floor():
    """Defensive path, unchanged. If the width cannot be resolved the gate must not
    crash the entry — it degrades to the operator's number."""
    s = _strat(call_min=25, put_min=25, configured=500.0)
    s._get_vix_adjusted_spread_width = MagicMock(side_effect=RuntimeError("no vix"))
    assert s._min_buying_power_per_unit() == pytest.approx(500.0)


def test_the_naked_strategy_override_is_untouched():
    """CONTROL. G overrides this method entirely with a naked-margin floor; the IC fix
    must not have reached into it."""
    from bots.hydra.strangle_strategy import StrangleStrategy
    s = StrangleStrategy.__new__(StrangleStrategy)
    s.strategy_config = {}
    assert s._min_buying_power_per_unit() == pytest.approx(30000.0)


# ══════════════════════════════════════════════════════════════════════════════
# The invariant this fix restores
# ══════════════════════════════════════════════════════════════════════════════

def test_the_width_function_still_documents_the_invariant():
    """The fix exists because the callee already stated the rule and the caller ignored
    it. If that docstring is ever rewritten, the reasoning here needs re-checking."""
    import inspect
    src = inspect.getsource(HydraStrategy._get_vix_adjusted_spread_width)
    assert "max(call_width, put_width)" in src, (
        "_get_vix_adjusted_spread_width no longer documents margin = "
        "max(call_width, put_width) — re-verify what the ORDER-004 floor should use"
    )


def test_the_floor_asks_for_both_sides():
    """Structural pin: asking for one side is the defect, whatever the numbers say."""
    import inspect
    src = inspect.getsource(HydraStrategy._min_buying_power_per_unit)
    assert '"put"' in src and '"call"' in src, (
        "the ORDER-004 floor no longer consults both wing widths"
    )
