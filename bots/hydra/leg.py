"""First-class option-leg model for HYDRA entries.

This module is the substrate for the Leg/LegSet abstraction (modularity-audit
item 1). It is intentionally dependency-free (stdlib only) so it imports
without pulling in the broker / strategy stack, and so a future non-iron-condor
strategy (strangle / butterfly / ratio / vertical / calendar) can describe its
legs as a plain list without inheriting the 4-leg iron-condor shape.

`IronCondorEntry` keeps its flat ``short_call_strike`` etc. attribute surface as
a backward-compat property bridge over a ``legs`` dict of these objects — see
``docs/PR_SCOPE_LEG_INSTRUMENT.md``. Nothing imports this module yet; wiring the
bridge happens in a later commit.

A ``Leg`` is mutable on purpose: an iron-condor leg is re-struck (strike scan /
chain snapping), filled, and re-priced in place over its lifetime. (Contrast
Brandon's frozen ``HedgeLeg``, which is a one-shot hedge descriptor.)
"""

from dataclasses import dataclass
from typing import Dict, Optional

# Canonical leg ordering. The strings double as the dict keys in a LegSet and as
# the prefix of the flat ``{leg}_{prop}`` attribute names the bridge exposes, so
# this ordering is load-bearing — do not reorder casually.
LEG_NAMES = ("short_call", "long_call", "short_put", "long_put")

# The six leg-scoped properties that live on a Leg (everything else on
# IronCondorEntry is side- or entry-scoped). The bridge maps each
# ``{leg}_{prop}`` flat attribute to ``legs[leg].{prop}`` for these names.
LEG_PROPS = ("strike", "position_id", "uic", "price", "fill_price", "mid_at_fill")


@dataclass
class Leg:
    """One option leg.

    ``side`` ∈ {``"short"``, ``"long"``}, ``right`` ∈ {``"call"``, ``"put"``}.

    Field semantics mirror the flat IronCondorEntry fields they will back:
    - ``strike``       — the option strike (points)
    - ``position_id``  — Saxo-era per-leg id; always ``None`` on IBKR (no per-leg id)
    - ``uic``          — on IBKR this stores the conid (the F4 reconcile key)
    - ``price``        — current option price, refreshed every heartbeat (transient)
    - ``fill_price``   — execution price at entry (option points; ×100 for dollars)
    - ``mid_at_fill``  — (bid+ask)/2 at the filling attempt; slippage reference (transient)
    """

    side: str
    right: str
    strike: float = 0.0
    position_id: Optional[str] = None
    uic: Optional[int] = None
    price: float = 0.0
    fill_price: float = 0.0
    mid_at_fill: float = 0.0

    @property
    def name(self) -> str:
        """The canonical leg name (``"short_call"`` …) — the legacy dict key / attr prefix."""
        return f"{self.side}_{self.right}"


def _new_legset() -> Dict[str, Leg]:
    """Build a fresh iron-condor leg set keyed by canonical leg name.

    Used as the ``default_factory`` for ``IronCondorEntry.legs``. Each call
    returns independent ``Leg`` instances so two entries never share leg state.
    """
    return {
        "short_call": Leg("short", "call"),
        "long_call": Leg("long", "call"),
        "short_put": Leg("short", "put"),
        "long_put": Leg("long", "put"),
    }
