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
from datetime import datetime

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

    def _estimate_short_premium(self, uic) -> float:
        """Per-contract premium (option points) of a short leg — mid, then last,
        then mark. 0.0 if the leg has no uic or no usable quote. Reuses the base
        normalized-quote reader (`_read_option_quote`)."""
        if not uic:
            return 0.0
        quote = self._read_option_quote(uic) or {}
        for key in ("mid", "last", "mark"):
            value = quote.get(key)
            if value is not None:
                return float(value)
        return 0.0

    def _simulate_entry(self, entry: HydraIronCondorEntry) -> bool:
        """Dry-run entry: resolve the two short conids, book the premium collected,
        and assign synthetic DRY ids — the strangle analogue of the base IC
        simulation (which requires long wings the strangle doesn't have).

        Credit is the sum of the two short premiums (no long debit — naked). The
        entry's spread-value / unrealized-pnl math collapses correctly because the
        unused long legs price at 0 (long_*_price stays 0.0).
        """
        expiry = self._get_todays_expiry()
        if not expiry:
            logger.error(f"[DRY RUN] Strangle: no expiry for entry #{entry.entry_number}")
            return False

        # Resolve the two short conids so heartbeat monitoring fetches real quotes.
        if entry.short_call_strike:
            entry.short_call_uic = self._get_option_uic(entry.short_call_strike, "Call", expiry) or 0
        if entry.short_put_strike:
            entry.short_put_uic = self._get_option_uic(entry.short_put_strike, "Put", expiry) or 0

        # Credit = premium collected on each naked short (per-contract × 100 × contracts).
        call_premium = self._estimate_short_premium(entry.short_call_uic)
        put_premium = self._estimate_short_premium(entry.short_put_uic)
        entry.call_spread_credit = call_premium * 100 * self.contracts_per_entry
        entry.put_spread_credit = put_premium * 100 * self.contracts_per_entry
        entry.short_call_fill_price = call_premium
        entry.short_put_fill_price = put_premium

        # Synthetic DRY ids for the two shorts (longs are unused).
        base_id = int(datetime.now().timestamp() * 1000)
        entry.short_call_position_id = f"DRY_{base_id}_SC"
        entry.short_put_position_id = f"DRY_{base_id}_SP"
        entry.is_complete = True

        logger.info(
            f"[DRY RUN] Simulated STRANGLE entry #{entry.entry_number}: "
            f"SC {entry.short_call_strike:.0f} (${entry.call_spread_credit:.2f}) / "
            f"SP {entry.short_put_strike:.0f} (${entry.put_spread_credit:.2f}), "
            f"total ${entry.total_credit:.2f}"
        )
        return True

    def _calculate_stop_levels_hydra(self, entry: HydraIronCondorEntry) -> None:
        """Strangle stop policy: each naked short stops on ITS OWN credit + buffer.

        This is the key exit difference from the iron-condor base, which stops
        both sides on the *total* credit (the IC is managed as one unit). A
        strangle's two legs are independent naked positions, so the call stops
        when buying it back costs more than the call premium + buffer, and the
        put likewise on its own premium. Reuses the base buffer convention
        (`call_stop_buffer`/`put_stop_buffer` × contracts, regime-adjusted
        upstream) and the per-side MIN_STOP_LEVEL floor — only the credit basis
        differs (per-side, not total).
        """
        n = self.contracts_per_entry
        min_stop_level = 50.0 * n
        call_buf = self.call_stop_buffer * n
        put_buf = self.put_stop_buffer * n

        call_credit = max(entry.call_spread_credit, min_stop_level)
        put_credit = max(entry.put_spread_credit, min_stop_level)
        entry.call_side_stop = call_credit + call_buf
        entry.put_side_stop = put_credit + put_buf

        logger.info(
            f"STRANGLE stops (per-side): call ${entry.call_side_stop:.2f} "
            f"(credit ${call_credit:.2f} + buf ${call_buf:.2f}), put "
            f"${entry.put_side_stop:.2f} (credit ${put_credit:.2f} + buf ${put_buf:.2f})"
        )
