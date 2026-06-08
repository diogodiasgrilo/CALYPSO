"""StrangleStrategy — a 0DTE SPX short strangle (modularity-audit driver).

This is the concrete second strategy that proves the modularity work: it reuses
the base machinery (scheduling, monitoring loop, state, instrument params, the
market-data kernels) and only supplies what is genuinely strategy-specific via
the item-4b extension hooks.

What makes it different from the iron-condor family:
  - **2 naked legs, no protective wings** — a short call + a short put, both ~Nδ
    OTM. ``requires_protective_wings = False`` (item-6a) so the base never treats
    its intended shorts as a naked-short emergency.
  - **Undefined risk** — sized by broker margin, not the defined-risk floor
    (wired in a later step, S2).

Build status: INCREMENTAL. This commit lands the class + 2-leg strike selection
(``_calculate_strikes``). The entry placement (2 shorts, no longs/hedge-pairing)
and the naked-short exit rule land in subsequent commits. The strategy is
**deliberately NOT registered** in ``bots/hydra/registry.py`` yet — it cannot be
selected/run until those pieces exist, so it is inert and safe. It is also
**dry-run only**; going live is a separate, explicit operator decision.
"""

from __future__ import annotations

import logging

from bots.hydra.strategy import HydraIronCondorEntry, HydraStrategy

logger = logging.getLogger(__name__)


class StrangleStrategy(HydraStrategy):
    """0DTE SPX short strangle: two naked short legs at a symmetric OTM distance.

    Inherits HYDRA's scheduling / monitoring / state machinery and overrides only
    the strategy-defining hooks. ``requires_protective_wings = False`` is the
    crux — it tells the shared safety path (item-6a) that unhedged shorts are the
    position here, not an emergency to be auto-closed.
    """

    BOT_NAME = "STRANGLE"
    requires_protective_wings = False  # undefined-risk: naked shorts by design

    def _calculate_strikes(self, entry: HydraIronCondorEntry) -> bool:
        """Select the two short strikes — a short call and a short put at a
        symmetric ~target_delta OTM distance. No long wings (strikes left 0).

        Reuses the base 8δ-anchored OTM math and the instrument strike grid
        (``_snap_to_grid`` → ``self.strike_increment``), so it is instrument-
        and grid-agnostic for free. Returns True if both strikes were set.
        """
        spx = self.current_price
        if spx <= 0:
            logger.error("Strangle: cannot calculate strikes — no SPX price")
            return False

        vix = self.current_vix if self.current_vix > 0 else 15.0
        rounded_spx = self._snap_to_grid(spx)

        # Same 8-delta-anchored OTM distance as the iron-condor base, snapped to
        # the configured strike grid. Symmetric: a classic strangle places the
        # call and put the same distance OTM.
        base_distance_at_vix15 = 40  # ~8δ at VIX 15
        delta_adjustment = 8.0 / self.target_delta
        vix_factor = max(0.7, min(2.5, vix / 15.0))
        otm_distance = self._snap_to_grid(base_distance_at_vix15 * vix_factor * delta_adjustment)
        otm_distance = max(25, min(180, otm_distance))

        # Two naked shorts; no protective long wings.
        entry.short_call_strike = rounded_spx + otm_distance
        entry.short_put_strike = rounded_spx - otm_distance
        entry.long_call_strike = 0.0
        entry.long_put_strike = 0.0

        logger.info(
            f"STRANGLE strikes: SC {entry.short_call_strike:.0f} / "
            f"SP {entry.short_put_strike:.0f} (±{otm_distance:.0f}pt OTM, VIX {vix:.1f})"
        )
        return True
