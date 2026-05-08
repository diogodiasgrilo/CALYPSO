"""BrandonHydraStrategy — HYDRA + Trojan Horse additions, fully live.

Subclassing keeps variant A's HydraStrategy completely untouched. Variants
B and C load this class instead. As of v1.27.1 there is exactly ONE
shadow-only behavior: HYDRA's existing credit+buffer stop is computed every
tick in parallel for comparison and Telegrammed when it would have fired,
but it never closes positions in B/C. Every other Brandon feature acts.

Feature matrix (v1.27.1, both B and C):

    take_profit         LIVE     close IC at threshold% of credit captured
    narrow_spread       LIVE     5/10pt widths in C (overrides MKT-027); off in B
    gex_strike_adjuster LIVE     mutate entry.short_*_strike (and long_) before
                                 _execute_entry / _simulate_entry; SKIP routes
                                 through HYDRA's existing one-sided entry path
    gex_breach_exit     LIVE     sustained-90s breach of the outermost decel
                                 wall on the threatened side closes the IC
                                 via _close_entry_early (same disposition as a
                                 directional-pivot close)
    defensive_overlay   LIVE     debit spread (before 12:30 ET) or butterfly
                                 (12:30 ET onward) when SPX threatens a short
                                 strike + GEX confirms an accel zone. Hedge
                                 legs placed via _place_option_order in live
                                 mode; synthetic DRY_* fills in dry-run
    gex_cache           15-min   refresh every 15 min (Polygon Starter is
                                 unlimited; matches feed delay). Failure
                                 cooldown: 60s before retry.
    HYDRA_stop_shadow   SHADOW   credit+buffer stop computed but never acts;
                                 Telegram alert when each side would fire
                                 with expected loss
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from bots.hydra.strategy import HydraStrategy

from . import (
    defensive_overlay,
    gex_breach_exit,
    gex_provider,
    gex_strike_adjuster,
    hedge_position,
    narrow_spread,
    take_profit,
)
from .gex_provider import GEXProfile
from .hedge_position import HedgeLeg, HedgeSettlement

logger = logging.getLogger(__name__)


_GEX_REFRESH_SECONDS = 15 * 60   # match Polygon's 15-min delay; refreshing more often re-reads the same data
_GEX_FAILURE_COOLDOWN = 60       # don't hammer a flaky API


class BrandonHydraStrategy(HydraStrategy):
    """HYDRA with Brandon Jones's Trojan Horse iron condor enhancements."""

    def __init__(
        self,
        saxo_client,
        config,
        logger_service=None,
        dry_run: bool = False,
        alert_service=None,
    ):
        super().__init__(
            saxo_client,
            config,
            logger_service,
            dry_run=dry_run,
            alert_service=alert_service,
        )

        bcfg = (config.get("strategy", {}) or {}).get("brandon", {}) or {}

        tp = bcfg.get("take_profit") or {}
        self.brandon_take_profit_enabled = bool(tp.get("enabled", False))
        self.brandon_take_profit_threshold = float(tp.get("threshold", 0.80))

        gex = bcfg.get("gex") or {}
        self.brandon_gex_enabled = bool(gex.get("enabled", False))
        self.brandon_polygon_api_key_env = str(gex.get("polygon_api_key_env", "POLYGON_API_KEY"))
        self.brandon_polygon_underlying = str(gex.get("polygon_underlying", "SPX"))
        self.brandon_strike_adjuster_enabled = bool(gex.get("strike_adjuster_enabled", False))
        self.brandon_breach_exit_enabled = bool(gex.get("breach_exit_enabled", False))
        self.brandon_breach_confirmation_seconds = int(gex.get("breach_confirmation_seconds", 90))
        self.brandon_decel_min_pct = float(gex.get("decel_min_pct", 0.05))
        self.brandon_accel_min_pct = float(gex.get("accel_min_pct", 0.10))
        self.brandon_max_shift_pts = float(gex.get("max_shift_pts", 25.0))
        self.brandon_shift_buffer_pts = float(gex.get("shift_buffer_pts", 5.0))

        ov = bcfg.get("defensive_overlay") or {}
        self.brandon_overlay_enabled = bool(ov.get("enabled", False))
        self.brandon_overlay_trigger_distance_pts = float(ov.get("trigger_distance_pts", 25.0))
        self.brandon_overlay_butterfly_width = int(ov.get("butterfly_width_pts", 10))
        self.brandon_overlay_butterfly_cutoff_hour = int(ov.get("butterfly_cutoff_hour", 12))
        self.brandon_overlay_butterfly_cutoff_minute = int(ov.get("butterfly_cutoff_minute", 30))

        ns = bcfg.get("narrow_spread") or {}
        self.brandon_narrow_spread_enabled = bool(ns.get("enabled", False))
        self.brandon_narrow_breakpoint_vix = float(ns.get("breakpoint_vix", 22.0))
        self.brandon_narrow_width_low = int(ns.get("width_low", 5))
        self.brandon_narrow_width_high = int(ns.get("width_high", 10))

        hs = bcfg.get("hydra_stop_shadow") or {}
        self.brandon_hydra_shadow_enabled = bool(hs.get("enabled", True))

        self._brandon_gex_profile: Optional[GEXProfile] = None
        self._brandon_gex_profile_fetched_at: Optional[datetime] = None
        self._brandon_gex_failure_at: Optional[datetime] = None
        self._brandon_breach_states: dict[tuple[int, str], gex_breach_exit.BreachState] = {}
        self._brandon_overlay_placed: set[tuple[int, str]] = set()
        self._brandon_hydra_shadow_fired: set[tuple[int, str]] = set()
        # Hedge legs placed during the day, keyed by entry_number. List grows
        # when an overlay fires; cleared in _reset_for_new_day. Persisted to
        # a sidecar JSON next to the bot's state file so a mid-day restart
        # doesn't lose hedge tracking. Settled against SPX_close in
        # log_daily_summary.
        self._brandon_hedge_legs: dict[int, list[HedgeLeg]] = {}
        self._brandon_hedge_settlements: list[HedgeSettlement] = []
        self._brandon_hedge_state_path = self._brandon_resolve_hedge_state_path()
        self._brandon_load_hedge_state()

        logger.info(
            "Brandon features active: tp=%s (thr=%.2f) | narrow_spread=%s | "
            "gex=%s (adj=%s, breach=%s) | overlay=%s | hydra_stop_shadow=%s",
            self.brandon_take_profit_enabled,
            self.brandon_take_profit_threshold,
            self.brandon_narrow_spread_enabled,
            self.brandon_gex_enabled,
            self.brandon_strike_adjuster_enabled,
            self.brandon_breach_exit_enabled,
            self.brandon_overlay_enabled,
            self.brandon_hydra_shadow_enabled,
        )

    # ------------------------------------------------------------------
    # Spread width override (variant C only when narrow_spread.enabled)
    # ------------------------------------------------------------------

    def _get_vix_adjusted_spread_width(self, vix: float, side: str = "call") -> int:
        if self.brandon_narrow_spread_enabled:
            cfg = narrow_spread.NarrowSpreadConfig(
                breakpoint_vix=self.brandon_narrow_breakpoint_vix,
                width_low=self.brandon_narrow_width_low,
                width_high=self.brandon_narrow_width_high,
            )
            return narrow_spread.narrow_spread_width(vix, cfg)
        return super()._get_vix_adjusted_spread_width(vix, side)

    # ------------------------------------------------------------------
    # Strike adjuster — applied just before order placement (live + dry)
    # ------------------------------------------------------------------

    def _execute_entry(self, entry) -> bool:
        self._brandon_apply_strike_adjuster(entry)
        return super()._execute_entry(entry)

    def _simulate_entry(self, entry) -> bool:
        self._brandon_apply_strike_adjuster(entry)
        return super()._simulate_entry(entry)

    def _brandon_apply_strike_adjuster(self, entry) -> None:
        """Mutate `entry` strikes per the GEX adjuster, in place.

        SHIFT moves the short strike outward and recomputes the long strike at
        the existing spread width. SKIP routes the side through HYDRA's
        existing one-sided entry path by setting the corresponding `*_only`
        and `*_side_skipped` flags. KEEP is a no-op.

        Failure (no GEX profile yet, missing strikes, etc.) is a no-op so
        order placement falls through to the standard credit-scan strikes.
        """
        if not (self.brandon_gex_enabled and self.brandon_strike_adjuster_enabled):
            return
        spot = float(self.current_price or 0.0)
        if spot <= 0:
            return
        profile = self._brandon_get_gex_profile(self._brandon_today_date())
        if profile is None:
            return

        cfg = gex_strike_adjuster.AdjusterConfig(
            accel_min_pct=self.brandon_accel_min_pct,
            decel_min_pct=self.brandon_decel_min_pct,
            max_shift_pts=self.brandon_max_shift_pts,
            shift_buffer_pts=self.brandon_shift_buffer_pts,
        )

        if entry.short_call_strike and not getattr(entry, "call_side_skipped", False):
            r = gex_strike_adjuster.adjust_call_strike(
                spot=spot, proposed_short=entry.short_call_strike, profile=profile, config=cfg,
            )
            if r.action == gex_strike_adjuster.AdjustAction.SHIFT and r.new_strike is not None:
                width = entry.long_call_strike - entry.short_call_strike
                logger.info(
                    "BRANDON-GEX-ADJ E#%s call: SHIFT %.0f → %.0f (width %.0f preserved) — %s",
                    entry.entry_number, entry.short_call_strike, r.new_strike, width, r.reason,
                )
                entry.short_call_strike = r.new_strike
                entry.long_call_strike = r.new_strike + width
            elif r.action == gex_strike_adjuster.AdjustAction.SKIP:
                logger.warning(
                    "BRANDON-GEX-ADJ E#%s call: SKIP — %s. Routing as put-only entry.",
                    entry.entry_number, r.reason,
                )
                entry.call_side_skipped = True
                entry.short_call_strike = 0.0
                entry.long_call_strike = 0.0
                if hasattr(entry, "put_only"):
                    entry.put_only = True

        if entry.short_put_strike and not getattr(entry, "put_side_skipped", False):
            r = gex_strike_adjuster.adjust_put_strike(
                spot=spot, proposed_short=entry.short_put_strike, profile=profile, config=cfg,
            )
            if r.action == gex_strike_adjuster.AdjustAction.SHIFT and r.new_strike is not None:
                width = entry.short_put_strike - entry.long_put_strike
                logger.info(
                    "BRANDON-GEX-ADJ E#%s put: SHIFT %.0f → %.0f (width %.0f preserved) — %s",
                    entry.entry_number, entry.short_put_strike, r.new_strike, width, r.reason,
                )
                entry.short_put_strike = r.new_strike
                entry.long_put_strike = r.new_strike - width
            elif r.action == gex_strike_adjuster.AdjustAction.SKIP:
                logger.warning(
                    "BRANDON-GEX-ADJ E#%s put: SKIP — %s. Routing as call-only entry.",
                    entry.entry_number, r.reason,
                )
                entry.put_side_skipped = True
                entry.short_put_strike = 0.0
                entry.long_put_strike = 0.0
                if hasattr(entry, "call_only"):
                    entry.call_only = True

    # ------------------------------------------------------------------
    # Per-tick monitoring: TP / breach / overlay / HYDRA-shadow stop
    # ------------------------------------------------------------------

    def _check_stop_losses(self) -> Optional[str]:
        # Refresh entry prices BEFORE running any Brandon decision. Parent's
        # _check_stop_losses does this at the top; we replace parent's flow
        # so we have to call it ourselves. Without this, entry.{call,put}_spread_value
        # stays at the dataclass default 0.0 right after placement, and
        # take_profit.evaluate() sees credit > 0, value = 0 → 100% captured →
        # fires immediately. _batch_update_entry_prices is idempotent; a
        # parallel call from a future tick is fine.
        try:
            self._batch_update_entry_prices()
        except Exception as exc:
            logger.debug("BRANDON: price refresh failed (non-fatal): %s", exc)

        # 1. Take-profit (LIVE)
        if self.brandon_take_profit_enabled:
            for entry in list(self.daily_state.active_entries):
                action = self._brandon_check_take_profit(entry)
                if action:
                    return action

        # 2. GEX breach exit (LIVE) — Brandon's stop. Replaces credit+buffer in B/C.
        if self.brandon_gex_enabled and self.brandon_breach_exit_enabled:
            for entry in list(self.daily_state.active_entries):
                action = self._brandon_check_breach_exit(entry)
                if action:
                    # HYDRA shadow stop also evaluates this tick before we return,
                    # so the comparison log captures both events on the same day.
                    self._brandon_check_hydra_shadow_stop(entry)
                    return action

        # 3. HYDRA credit+buffer stop (SHADOW) — never acts; Telegram on first fire per side
        if self.brandon_hydra_shadow_enabled:
            for entry in list(self.daily_state.active_entries):
                self._brandon_check_hydra_shadow_stop(entry)

        # 4. Defensive overlay (LIVE) — places hedge orders when triggered
        if self.brandon_gex_enabled and self.brandon_overlay_enabled:
            for entry in list(self.daily_state.active_entries):
                self._brandon_check_overlay(entry)

        # 5. Standard parent stops are deliberately NOT called in B/C — Brandon's
        #    GEX breach is the primary stop. Falling through to super would
        #    fire HYDRA's credit+buffer stop in addition, which defeats the
        #    head-to-head comparison. Variant A keeps super() because it
        #    loads HydraStrategy directly, not this subclass.
        return None

    # ------------------------------------------------------------------
    # Take-profit (LIVE)
    # ------------------------------------------------------------------

    def _brandon_check_take_profit(self, entry) -> Optional[str]:
        call_alive = self._brandon_side_alive(entry, "call")
        put_alive = self._brandon_side_alive(entry, "put")
        if not call_alive and not put_alive:
            return None

        # Per-side staleness check (closes the hole in evaluate_iron_condor's
        # SUM-based check): if a side is alive with credit but spread_value
        # is 0, that side hasn't been refreshed yet on this tick. Wait one
        # tick rather than firing TP on bogus data.
        if call_alive and entry.call_spread_credit > 0 and entry.call_spread_value == 0:
            return None
        if put_alive and entry.put_spread_credit > 0 and entry.put_spread_value == 0:
            return None

        decision = take_profit.evaluate_iron_condor(
            call_credit=entry.call_spread_credit if call_alive else 0.0,
            put_credit=entry.put_spread_credit if put_alive else 0.0,
            call_value=entry.call_spread_value if call_alive else 0.0,
            put_value=entry.put_spread_value if put_alive else 0.0,
            threshold=self.brandon_take_profit_threshold,
        )
        if not decision.should_close:
            return None

        logger.info("BRANDON-TP E#%s: %s — closing IC", entry.entry_number, decision.reason)
        try:
            legs_closed, legs_failed, _ = self._close_entry_early(entry)
        except Exception as exc:
            logger.error("BRANDON-TP E#%s: close failed (%s)", entry.entry_number, exc)
            return None

        # P&L attribution. Two pieces to get right:
        #
        # 1. actual_*_stop_debit storage. spread_value is ALREADY in dollars
        #    (computed as (short_price − long_price) × 100 × contracts inside
        #    IronCondorEntry). Just store it raw — multiplying by 100 ×
        #    contracts again is a 1500× double-multiply at 15c. Live evidence
        #    2026-05-07: state file recorded actual_put_stop_debit=$56,250
        #    for SV=$37.50 closes (= $37.50 × 100 × 15) until this fix.
        #
        # 2. Realized-P&L correction. _close_entry_early already added the
        #    full credit to total_realized_pnl on the deferred-fill path
        #    (line ~2030). In dry-run, deferred fills never resolve (no real
        #    Saxo positions), so the credit-only number sticks and the
        #    journal overstates profit by close_cost per side. We subtract
        #    close_cost here to match what live mode would converge to once
        #    the async deferred-fill correction landed.
        contracts = max(int(getattr(entry, "contracts", 1) or 1), 1)
        if call_alive:
            entry.call_side_stopped = True
            close_cost_call = float(entry.call_spread_value) if entry.call_spread_value else 0.0
            entry.actual_call_stop_debit = close_cost_call
            self.daily_state.total_realized_pnl -= close_cost_call
        if put_alive:
            entry.put_side_stopped = True
            close_cost_put = float(entry.put_spread_value) if entry.put_spread_value else 0.0
            entry.actual_put_stop_debit = close_cost_put
            self.daily_state.total_realized_pnl -= close_cost_put
        # Tag the close so the dashboard / journal can distinguish "TP at 80%
        # captured" from a stop or end-of-day expiry. Both sides share the
        # same reason because Brandon TP fires aggregate (both legs go out
        # together when total captured ≥ threshold).
        entry.close_reason = "TP"
        return (
            f"BRANDON-TP E#{entry.entry_number}: closed {legs_closed} legs "
            f"({legs_failed} failed) — {decision.profit_captured_pct:.1%} captured, "
            f"close_cost call=${entry.actual_call_stop_debit:.2f} put=${entry.actual_put_stop_debit:.2f}"
        )

    # ------------------------------------------------------------------
    # GEX breach exit (LIVE) — Brandon's stop
    # ------------------------------------------------------------------

    def _brandon_check_breach_exit(self, entry) -> Optional[str]:
        profile = self._brandon_get_gex_profile(self._brandon_today_date())
        if profile is None:
            return None
        spot = float(self.current_price or 0.0)
        if spot <= 0:
            return None
        now = self._brandon_now_et()

        for side in ("call", "put"):
            if not self._brandon_side_alive(entry, side):
                continue
            walls = profile.positive_clusters(min_strength_pct=self.brandon_decel_min_pct)
            # Filter relative to the SHORT STRIKE, not current spot — once
            # spot has breached past a wall the wall would otherwise be
            # excluded by the filter and the breach signal would die just
            # when we need it most. Walls qualify if they sit between entry
            # spot and the short (call: strike_low <= short_call; put:
            # strike_high >= short_put).
            if side == "call":
                ref = entry.short_call_strike
                relevant = tuple(c for c in walls if c.strike_low <= ref)
            else:
                ref = entry.short_put_strike
                relevant = tuple(c for c in walls if c.strike_high >= ref)
            key = (entry.entry_number, side)
            state = self._brandon_breach_states.get(key, gex_breach_exit.BreachState())
            decision, new_state = gex_breach_exit.evaluate_breach(
                side=side,
                spot_now=spot,
                decel_walls=relevant,
                state=state,
                now=now,
                confirmation_seconds=self.brandon_breach_confirmation_seconds,
            )
            self._brandon_breach_states[key] = new_state
            if decision.is_first_breach:
                logger.info("BRANDON-BREACH E#%s %s: first breach — %s", entry.entry_number, side, decision.reason)
            if decision.would_close:
                logger.warning(
                    "BRANDON-BREACH E#%s %s: confirmed breach — closing IC. %s",
                    entry.entry_number, side, decision.reason,
                )
                # CRITICAL: capture aliveness AND spread_value BEFORE
                # _close_entry_early runs. _close_entry_early sets
                # *_side_expired=True on every closed side, which makes
                # _brandon_side_alive return False — so checking it AFTER
                # the close skips the entire close-cost block, leaves
                # actual_*_stop_debit at its 0.0 default, and never
                # subtracts the real close cost from total_realized_pnl.
                # Live evidence 2026-05-07: 3 breach exits on B (E#4 SV
                # $750, E#5 SV $4,125, E#6 SV $3,900) and 1 on C (E#2 SV
                # $2,925) all recorded close_cost=$0 → reported a +$787
                # day on B that was actually ~-$8,000 net of real close
                # costs. Same shape of bug we fixed in TP path days ago,
                # missed in breach exit because it uses live attr lookup
                # instead of a captured local.
                call_alive_pre = self._brandon_side_alive(entry, "call")
                put_alive_pre = self._brandon_side_alive(entry, "put")
                close_cost_call_real = float(entry.call_spread_value) if (call_alive_pre and entry.call_spread_value) else 0.0
                close_cost_put_real = float(entry.put_spread_value) if (put_alive_pre and entry.put_spread_value) else 0.0

                try:
                    legs_closed, legs_failed, _ = self._close_entry_early(entry)
                except Exception as exc:
                    logger.error("BRANDON-BREACH E#%s %s: close failed (%s)", entry.entry_number, side, exc)
                    return None
                # P&L attribution: same pattern as TP. Use the captured
                # pre-close aliveness flags + spread_values to record the
                # real close cost on each side that was alive at the moment
                # of breach.
                contracts = max(int(getattr(entry, "contracts", 1) or 1), 1)
                if call_alive_pre:
                    entry.call_side_stopped = True
                    entry.actual_call_stop_debit = close_cost_call_real
                    self.daily_state.total_realized_pnl -= close_cost_call_real
                    setattr(entry, "call_side_pivot_closed", True)
                if put_alive_pre:
                    entry.put_side_stopped = True
                    entry.actual_put_stop_debit = close_cost_put_real
                    self.daily_state.total_realized_pnl -= close_cost_put_real
                    setattr(entry, "put_side_pivot_closed", True)
                # Tag close type for the dashboard. BREACH = Brandon GEX wall
                # breach, distinct from TP and from a HYDRA credit+buffer stop.
                entry.close_reason = "BREACH"
                return (
                    f"BRANDON-BREACH E#{entry.entry_number} {side}: closed "
                    f"{legs_closed} legs ({legs_failed} failed) on confirmed wall breach, "
                    f"close_cost call=${entry.actual_call_stop_debit:.2f} "
                    f"put=${entry.actual_put_stop_debit:.2f}"
                )
        return None

    # ------------------------------------------------------------------
    # HYDRA credit+buffer stop SHADOW comparison (logs only)
    # ------------------------------------------------------------------

    def _brandon_check_hydra_shadow_stop(self, entry) -> None:
        """Record when HYDRA's credit+buffer stop WOULD fire, without closing.

        The check mirrors HydraStrategy._check_stop_with_confirmation's core
        condition (spread_value >= side_stop) but does not call any close
        helper. First fire per side per day is announced via Telegram so the
        head-to-head comparison with Brandon's GEX breach is observable in
        real time. Subsequent ticks of the same side are silent.
        """
        for side in ("call", "put"):
            if not self._brandon_side_alive(entry, side):
                continue
            sv = entry.call_spread_value if side == "call" else entry.put_spread_value
            stop = entry.call_side_stop if side == "call" else entry.put_side_stop
            if stop <= 0 or sv < stop:
                continue
            key = (entry.entry_number, side)
            if key in self._brandon_hydra_shadow_fired:
                continue
            self._brandon_hydra_shadow_fired.add(key)
            credit = entry.call_spread_credit if side == "call" else entry.put_spread_credit
            expected_loss = sv - credit
            msg = (
                f"BRANDON-HYDRA-SHADOW E#{entry.entry_number} {side}: "
                f"HYDRA credit+buffer stop WOULD fire now — "
                f"SV ${sv:.0f} >= trigger ${stop:.0f}, expected loss ${expected_loss:.0f}. "
                f"Brandon GEX breach is the live stop; this is shadow only."
            )
            logger.warning(msg)
            self._brandon_send_telegram(
                msg,
                title=f"HYDRA-shadow-stop E#{entry.entry_number} {side}",
                priority_name="MEDIUM",
                alert_type_name="STOP_LOSS",
            )

    # ------------------------------------------------------------------
    # Defensive overlay (LIVE) — debit / butterfly hedge placement
    # ------------------------------------------------------------------

    def _brandon_check_overlay(self, entry) -> None:
        spot = float(self.current_price or 0.0)
        if spot <= 0:
            return
        profile = self._brandon_get_gex_profile(self._brandon_today_date())
        cfg = defensive_overlay.OverlayConfig(
            trigger_distance_pts=self.brandon_overlay_trigger_distance_pts,
            butterfly_cutoff=__import__("datetime").time(
                self.brandon_overlay_butterfly_cutoff_hour,
                self.brandon_overlay_butterfly_cutoff_minute,
            ),
            butterfly_width_pts=self.brandon_overlay_butterfly_width,
            require_gex_confirmation=(profile is not None),
            contracts=int(getattr(entry, "contracts", 1) or 1),
        )
        now_et = self._brandon_now_et()

        for side in ("call", "put"):
            if not self._brandon_side_alive(entry, side):
                continue
            short = entry.short_call_strike if side == "call" else entry.short_put_strike
            longs = entry.long_call_strike if side == "call" else entry.long_put_strike
            if not short or not longs:
                continue
            key = (entry.entry_number, side)
            if key in self._brandon_overlay_placed:
                continue
            proposal = defensive_overlay.evaluate_overlay(
                threatened_side=side,
                spot_now=spot,
                short_strike=short,
                long_strike=longs,
                now_et=now_et,
                config=cfg,
                profile=profile,
            )
            if proposal is None:
                continue

            self._brandon_overlay_placed.add(key)
            self._brandon_place_overlay(entry, proposal)

    def _brandon_place_overlay(self, entry, proposal) -> None:
        """Place the overlay hedge legs.

        In dry-run mode each leg is materialised as a HedgeLeg with a
        synthetic DRY_OVERLAY_* position id and a Black-Scholes-estimated
        fill price (using the cached GEX profile's spot + a default 18% IV
        for SPX 0DTE). The legs are stored on
        `self._brandon_hedge_legs[entry.entry_number]` so they're part of the
        end-of-day settlement and the daily P&L picture is complete.

        In live mode the same legs are also tracked, but each is placed via
        `_place_option_order` against Saxo. Position ids returned by Saxo
        replace the DRY_OVERLAY_* placeholders. Hedges are held to expiry —
        intraday management of the hedge itself is not yet wired.
        """
        legs_summary = ", ".join(
            f"{l.side[0].upper()}{l.contract_type[0].upper()} {l.strike:.0f}×{l.quantity}"
            for l in proposal.legs
        )
        logger.warning(
            "BRANDON-OVERLAY E#%s %s: placing %s — %s. Legs: %s",
            entry.entry_number, proposal.threatened_side,
            proposal.structure.value, proposal.reason, legs_summary,
        )
        self._brandon_send_telegram(
            f"BRANDON-OVERLAY E#{entry.entry_number} {proposal.threatened_side}: "
            f"{proposal.structure.value} placed — {legs_summary}",
            title=f"Brandon overlay E#{entry.entry_number} {proposal.threatened_side}",
            priority_name="HIGH",
            alert_type_name="POSITION_OPENED",
        )

        spot = float(self.current_price or 0.0)
        t_years = self._brandon_estimate_t_years_to_close()
        placed_at = self._brandon_now_et()
        hedge_legs: list[HedgeLeg] = []
        for i, leg in enumerate(proposal.legs):
            fill_price = hedge_position.estimate_fill_price(
                contract_type=leg.contract_type,
                strike=leg.strike,
                spot=spot,
                t_years=t_years,
            )
            position_id = f"DRY_OVERLAY_{entry.entry_number}_{proposal.threatened_side}_{i}"
            hedge_legs.append(HedgeLeg(
                entry_number=entry.entry_number,
                side=leg.side,
                contract_type=leg.contract_type,
                strike=leg.strike,
                quantity=leg.quantity,
                fill_price=fill_price,
                position_id=position_id,
                structure=proposal.structure.value,
                threatened_side=proposal.threatened_side,
                placed_at=placed_at,
            ))
        self._brandon_hedge_legs.setdefault(entry.entry_number, []).extend(hedge_legs)
        self._brandon_save_hedge_state()

        if self.dry_run:
            return

        # Live wiring — mirrors _execute_entry's per-leg pattern. Imported
        # lazily so dry-run tests don't pull BuySell.
        try:
            from shared.saxo_client import BuySell
        except Exception as exc:
            logger.error("BRANDON-OVERLAY E#%s: BuySell import failed (%s)", entry.entry_number, exc)
            return
        expiry = self._get_todays_expiry() if hasattr(self, "_get_todays_expiry") else None
        if not expiry:
            logger.error(
                "BRANDON-OVERLAY E#%s: could not determine expiry — skipping placement",
                entry.entry_number,
            )
            return
        for i, leg in enumerate(proposal.legs):
            buy_sell = BuySell.BUY if leg.side == "long" else BuySell.SELL
            put_call = "Call" if leg.contract_type == "call" else "Put"
            external_ref = f"OVERLAY_{entry.entry_number}_{proposal.threatened_side}_{i}"
            for q in range(leg.quantity):
                try:
                    self._place_option_order(
                        strike=leg.strike,
                        put_call=put_call,
                        buy_sell=buy_sell,
                        expiry=expiry,
                        external_ref=f"{external_ref}_{q}",
                    )
                except Exception as exc:
                    logger.error(
                        "BRANDON-OVERLAY E#%s leg %s %s %.0f failed: %s",
                        entry.entry_number, leg.side, leg.contract_type, leg.strike, exc,
                    )

    def _brandon_estimate_t_years_to_close(self) -> float:
        """Calendar time from now to today's 4 PM ET expiry, in years."""
        try:
            from shared.market_hours import US_EASTERN, get_us_market_time
            now_et = get_us_market_time()
            close_et = US_EASTERN.localize(
                datetime.combine(now_et.date(), datetime.min.time()).replace(hour=16)
            )
            return gex_provider.time_to_expiry_years(now_et, close_et)
        except Exception:
            # Conservative fallback: 1 hour
            return 1.0 / (365.0 * 24.0)

    def _brandon_settle_hedges(self, spx_settle: float) -> list[HedgeSettlement]:
        """Settle every open hedge against SPX_close. Idempotent within the day —
        runs once at log_daily_summary time and Telegrams the per-entry outcomes.
        """
        if not self._brandon_hedge_legs or self._brandon_hedge_settlements:
            return self._brandon_hedge_settlements

        settlements: list[HedgeSettlement] = []
        for entry_number, legs in self._brandon_hedge_legs.items():
            s = hedge_position.settle_hedge(legs, spx_settle)
            if s is None:
                continue
            settlements.append(s)
            logger.warning(
                "BRANDON-OVERLAY-SETTLED E#%s %s %s: SPX_close=%.2f, debit_paid=$%.2f, hedge_pnl=$%.2f",
                s.entry_number, s.threatened_side, s.structure,
                s.spx_settle, s.total_debit_paid, s.total_pnl,
            )
            self._brandon_send_telegram(
                f"BRANDON-OVERLAY-SETTLED E#{s.entry_number} {s.threatened_side}: "
                f"{s.structure} — SPX_close ${s.spx_settle:.2f}, "
                f"debit paid ${s.total_debit_paid:.2f}, hedge P&L ${s.total_pnl:+.2f}",
                title=f"Brandon overlay settlement E#{s.entry_number}",
                priority_name="MEDIUM",
                alert_type_name="POSITION_CLOSED",
            )
        self._brandon_hedge_settlements = settlements

        # Aggregate summary if there were any hedges today
        if settlements:
            total = sum(s.total_pnl for s in settlements)
            self._brandon_send_telegram(
                f"BRANDON-OVERLAY-DAY: {len(settlements)} hedge(s) settled, "
                f"net hedge P&L ${total:+.2f} (already reflected in BRANDON-OVERLAY-SETTLED line items above).",
                title="Brandon hedge totals",
                priority_name="MEDIUM",
                alert_type_name="DAILY_SUMMARY" if False else "POSITION_CLOSED",
            )
        return settlements

    def log_daily_summary(self):
        # Settle hedges BEFORE the parent's daily summary so they're journaled
        # for the same day. We use self.current_price as the proxy for the
        # actual SPXW PM-settlement value. The bot's last in-session price
        # update is normally a 4:00 PM ET tick, so this is within ~0.05% of
        # the official settlement value — acceptable for dry-run analytics.
        # If precision matters more later, wire up Polygon's settlement
        # endpoint or pull SPX from /v2/aggs/ticker/I:SPX/prev (next morning).
        try:
            spx_settle = float(self.current_price or 0.0)
            if spx_settle > 0:
                self._brandon_settle_hedges(spx_settle)
        except Exception as exc:
            logger.error("BRANDON-OVERLAY settlement failed (non-fatal): %s", exc)
        super().log_daily_summary()

    # ------------------------------------------------------------------
    # GEX profile cache (15-min TTL, 60s failure cooldown)
    # ------------------------------------------------------------------

    def _brandon_get_gex_profile(self, expiry_date) -> Optional[GEXProfile]:
        if not self.brandon_gex_enabled:
            return None
        now = datetime.now(timezone.utc)

        if (
            self._brandon_gex_failure_at is not None
            and (now - self._brandon_gex_failure_at).total_seconds() < _GEX_FAILURE_COOLDOWN
        ):
            return self._brandon_gex_profile  # honor cooldown — return stale or None

        if (
            self._brandon_gex_profile is not None
            and self._brandon_gex_profile_fetched_at is not None
            and (now - self._brandon_gex_profile_fetched_at).total_seconds() < _GEX_REFRESH_SECONDS
        ):
            return self._brandon_gex_profile

        api_key = os.environ.get(self.brandon_polygon_api_key_env)
        if not api_key:
            logger.warning(
                "Brandon GEX disabled: env var %s not set", self.brandon_polygon_api_key_env
            )
            self._brandon_gex_failure_at = now
            return None

        spot = float(self.current_price or 0.0)
        if spot <= 0:
            return self._brandon_gex_profile  # keep last good profile if spot momentarily 0

        try:
            # 2-pass fetch: chain endpoint for OI (returns Greeks-stripped on
            # Starter), then per-contract endpoint for Greeks/IV on the most
            # liquid strikes near spot. See gex_provider.fetch_polygon_chain_with_greeks.
            contracts = gex_provider.fetch_polygon_chain_with_greeks(
                underlying=self.brandon_polygon_underlying,
                expiry=expiry_date,
                api_key=api_key,
                max_pages=4,
                oi_threshold=50,
                spot=spot,
                spot_window_pct=0.05,
                max_contracts_to_hydrate=80,
            )
            try:
                from shared.market_hours import US_EASTERN, get_us_market_time
                now_et = get_us_market_time()
                expiry_close_et = US_EASTERN.localize(
                    datetime.combine(expiry_date, datetime.min.time()).replace(hour=16)
                )
            except Exception:
                from datetime import timezone as _tz
                now_et = datetime.now(_tz.utc)
                expiry_close_et = datetime.combine(
                    expiry_date, datetime.min.time(), tzinfo=_tz.utc
                ).replace(hour=20)
            t_years = max(
                gex_provider.time_to_expiry_years(now_et, expiry_close_et),
                1.0 / (365.0 * 24.0 * 60.0),
            )
            profile = gex_provider.build_profile(
                contracts, spot=spot, expiry=expiry_date, time_to_expiry=t_years
            )
        except Exception as exc:
            logger.warning("Brandon GEX fetch failed: %s", exc)
            self._brandon_gex_failure_at = now
            return self._brandon_gex_profile  # keep last good profile if any

        self._brandon_gex_profile = profile
        self._brandon_gex_profile_fetched_at = now
        self._brandon_gex_failure_at = None
        # Surface chain coverage so a sudden gap (e.g., Polygon dropping Greeks
        # on most strikes) shows up in the journal. Normal: dropped few. If
        # this number spikes, GEX cluster strength is being underestimated.
        chain_total = len(contracts)
        with_greeks_or_iv = sum(
            1 for c in contracts
            if (c.get("greeks") or {}).get("gamma") is not None
            or c.get("implied_volatility") is not None
        )
        contributed = len(profile.strikes)
        dropped = chain_total - contributed
        logger.info(
            "Brandon GEX profile refreshed: spot=%.2f, %d strikes contributed, "
            "%d positive / %d negative clusters; chain=%d, hydrated_with_greeks_or_iv=%d, dropped=%d",
            profile.spot,
            contributed,
            len(profile.positive_clusters(min_strength_pct=self.brandon_decel_min_pct)),
            len(profile.negative_clusters(min_strength_pct=self.brandon_accel_min_pct)),
            chain_total,
            with_greeks_or_iv,
            dropped,
        )
        return profile

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _brandon_side_alive(entry, side: str) -> bool:
        prefix = "call" if side == "call" else "put"
        return not (
            getattr(entry, f"{prefix}_side_stopped", False)
            or getattr(entry, f"{prefix}_side_expired", False)
            or getattr(entry, f"{prefix}_side_skipped", False)
            or getattr(entry, f"{prefix}_side_pivot_closed", False)
        )

    # ------------------------------------------------------------------
    # Hedge-leg persistence (survives mid-day restart)
    # ------------------------------------------------------------------

    def _brandon_resolve_hedge_state_path(self) -> str:
        """Return the path to the variant's hedge_legs JSON sidecar.

        Lives alongside the bot's state file (same data dir) so it follows
        the variant_<id> isolation. Format: brandon_hedge_legs.json.
        """
        try:
            from bots.hydra.strategy import _PROJECT_DATA_DIR
            return os.path.join(_PROJECT_DATA_DIR, "brandon_hedge_legs.json")
        except Exception:
            return "/opt/calypso/data/brandon_hedge_legs.json"

    def _brandon_load_hedge_state(self) -> None:
        """Restore hedge_legs from sidecar on startup. No-op if file absent
        or stale (different date)."""
        path = getattr(self, "_brandon_hedge_state_path", None)
        if not path or not os.path.exists(path):
            return
        try:
            with open(path) as f:
                blob = __import__("json").load(f)
        except Exception as exc:
            logger.warning("BRANDON: could not read hedge state %s: %s", path, exc)
            return
        # Stale day → start fresh
        today_str = self._brandon_today_date().isoformat()
        if blob.get("date") != today_str:
            return
        legs_by_entry = blob.get("legs_by_entry") or {}
        loaded = 0
        for ent_str, leg_dicts in legs_by_entry.items():
            try:
                ent = int(ent_str)
            except (TypeError, ValueError):
                continue
            restored = []
            for d in leg_dicts:
                try:
                    placed_at = datetime.fromisoformat(d["placed_at"])
                except Exception:
                    placed_at = datetime.now(timezone.utc)
                restored.append(HedgeLeg(
                    entry_number=int(d["entry_number"]),
                    side=str(d["side"]),
                    contract_type=str(d["contract_type"]),
                    strike=float(d["strike"]),
                    quantity=int(d["quantity"]),
                    fill_price=float(d["fill_price"]),
                    position_id=str(d["position_id"]),
                    structure=str(d["structure"]),
                    threatened_side=str(d["threatened_side"]),
                    placed_at=placed_at,
                ))
            if restored:
                self._brandon_hedge_legs[ent] = restored
                loaded += len(restored)
        if loaded:
            logger.info("BRANDON: restored %d hedge legs across %d entries from %s",
                        loaded, len(self._brandon_hedge_legs), path)

    def _brandon_save_hedge_state(self) -> None:
        """Write hedge_legs to sidecar. Called after every overlay placement
        so a restart between placement and EOD still has the hedge tracked."""
        path = getattr(self, "_brandon_hedge_state_path", None)
        if not path:
            return
        try:
            blob = {
                "date": self._brandon_today_date().isoformat(),
                "legs_by_entry": {
                    str(ent): [
                        {
                            "entry_number": l.entry_number,
                            "side": l.side,
                            "contract_type": l.contract_type,
                            "strike": l.strike,
                            "quantity": l.quantity,
                            "fill_price": l.fill_price,
                            "position_id": l.position_id,
                            "structure": l.structure,
                            "threatened_side": l.threatened_side,
                            "placed_at": l.placed_at.isoformat(),
                        }
                        for l in legs
                    ]
                    for ent, legs in self._brandon_hedge_legs.items()
                },
            }
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                __import__("json").dump(blob, f, indent=2)
            os.replace(tmp, path)
        except Exception as exc:
            logger.warning("BRANDON: could not write hedge state %s: %s", path, exc)

    # ------------------------------------------------------------------

    @staticmethod
    def _brandon_today_date():
        try:
            from shared.market_hours import get_us_market_time
            return get_us_market_time().date()
        except Exception:
            return datetime.now().date()

    @staticmethod
    def _brandon_now_et():
        try:
            from shared.market_hours import get_us_market_time
            return get_us_market_time()
        except Exception:
            return datetime.now(timezone.utc)

    def _brandon_send_telegram(
        self,
        message: str,
        title: str = "Brandon stack",
        priority_name: str = "MEDIUM",
        alert_type_name: str = "STOP_LOSS",
    ) -> None:
        """Fire an AlertService alert. Maps Brandon-stack events into the
        existing CALYPSO alert pipeline (Pub/Sub → Telegram + Email).

        Defaults: alert_type=STOP_LOSS (semantically: would-fire shadow stop),
        priority=MEDIUM (Telegram only). Caller can override per call site —
        e.g., overlay placements use HIGH; a hypothetical critical breach
        would use CRITICAL.
        """
        alert = getattr(self, "alert_service", None)
        if alert is None:
            return
        try:
            from shared.alert_service import AlertPriority, AlertType
            priority = getattr(AlertPriority, priority_name, AlertPriority.MEDIUM)
            alert_type = getattr(AlertType, alert_type_name, AlertType.STOP_LOSS)
            alert.send_alert(
                alert_type=alert_type,
                title=title,
                message=message,
                priority=priority,
            )
        except Exception as exc:
            logger.debug("BRANDON Telegram send failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Daily reset — clear Brandon-specific caches
    # ------------------------------------------------------------------

    def _reset_for_new_day(self):
        super()._reset_for_new_day()
        self._brandon_gex_profile = None
        self._brandon_gex_profile_fetched_at = None
        self._brandon_gex_failure_at = None
        self._brandon_breach_states.clear()
        self._brandon_overlay_placed.clear()
        self._brandon_hydra_shadow_fired.clear()
        self._brandon_hedge_legs.clear()
        self._brandon_hedge_settlements = []
        # Wipe yesterday's hedge sidecar so a new-day restart won't restore it.
        try:
            path = getattr(self, "_brandon_hedge_state_path", None)
            if path and os.path.exists(path):
                os.remove(path)
        except Exception as exc:
            logger.debug("BRANDON: hedge sidecar cleanup failed (non-fatal): %s", exc)
