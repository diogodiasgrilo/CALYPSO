"""2026-06-10: BrandonHydraStrategy must initialize its _brandon_* STATE
containers BEFORE super().__init__(), because the base constructor runs
position recovery → _reconcile_recovered_entries_with_broker →
_expected_position_quantities (Brandon override) which reads
self._brandon_hedge_legs. A mid-day variant-C restart crashed recovery with
AttributeError because the attr was set AFTER super(). Same init-order rule as
stop_buffer / short_only_stop (CLAUDE.md). This guards against regression."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402

# Every _brandon_* state container that a recovery-reachable override touches
# (_expected_position_quantities reads _brandon_hedge_legs; _reset_for_new_day
# .clear()s breach_states/overlay_placed/hydra_shadow_fired/hedge_legs).
_RECOVERY_TOUCHED = [
    "_brandon_hedge_legs",
    "_brandon_breach_states",
    "_brandon_overlay_placed",
    "_brandon_hydra_shadow_fired",
]


def test_state_containers_initialized_before_super_init():
    src = inspect.getsource(BrandonHydraStrategy.__init__)
    super_idx = src.index("super().__init__(\n")
    for attr in _RECOVERY_TOUCHED:
        first = src.index(attr)
        assert first < super_idx, (
            f"{attr} must be initialized BEFORE super().__init__() "
            f"(recovery in the base ctor reads it)"
        )


def test_hedge_state_loaded_before_super_init():
    # The sidecar restore must also precede super() so the startup reconcile
    # includes any restored hedge-leg conids.
    src = inspect.getsource(BrandonHydraStrategy.__init__)
    assert src.index("_brandon_load_hedge_state()") < src.index("super().__init__(\n")
