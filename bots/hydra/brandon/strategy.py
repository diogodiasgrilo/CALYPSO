"""BrandonHydraStrategy — HYDRA + Trojan Horse additions, fully live.

Subclassing keeps variant A's HydraStrategy completely untouched. Variants
B and C load this class instead. The ONLY shadow-only behavior left is the
%-of-width stop STUDY (`_brandon_check_pctwidth_shadow_stop`) — it logs the
tighter would-fire trigger for the eventual flip decision but never closes a
position. Every other feature ACTS — including HYDRA's credit+buffer stop:
since the L-C2 backstop fix (2026-06-10, commit c0281d9) it ACTS as a live
floor beneath the GEX breach-exit in BOTH GEX states (the per-tick
`BRANDON-HYDRA-SHADOW … would fire` line is now just a head-to-head heads-up;
`super()._check_stop_losses()` is what actually closes the side, MKT-046-
confirmed). NOTE (pre-2026-06-10 this docstring said the credit+buffer "never
acts" — that was the bug the 06-10 C max-loss incident exposed and L-C2 fixed).

Feature matrix (both B and C):

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
    gex_cache           3-min    background refresh every 3 min (cut from
                                 15 min on 2026-05-13; Polygon Starter is
                                 unlimited). force_refresh at entry time
                                 pulls a fresh chain regardless of TTL.
                                 Failure cooldown: 60s before retry.
    credit+buffer_stop  LIVE     HYDRA's credit+buffer stop ACTS as the floor
                                 beneath the GEX breach-exit, in BOTH GEX states
                                 (L-C2, 2026-06-10): GEX breach is PRIMARY (fires
                                 at the wall), credit+buffer is the backstop when
                                 the breach exit can't fire (no decel wall near
                                 the short, or GEX down). MKT-046-confirmed,
                                 mutually exclusive per tick → no double-stop.
                                 A per-tick "would-fire" line still logs for the
                                 head-to-head record (that part is shadow/alert).
    pctwidth_stop       SHADOW   tighter %-of-width stop — logs the would-fire
                                 trigger only (A2-SHADOW), never acts. Data for
                                 the "is the credit+buffer too wide for narrow
                                 spreads?" flip decision.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone  # AUD2-L9: removed unused `timedelta`
from typing import Optional

from bots.hydra.strategy import HydraStrategy

from . import (
    defensive_overlay,
    gex_breach_exit,
    gex_provider,
    gex_shared_cache,
    gex_strike_adjuster,
    hedge_position,
    narrow_spread,
    take_profit,
)
from .gex_provider import GEXProfile
from .hedge_position import HedgeLeg, HedgeSettlement

logger = logging.getLogger(__name__)


_GEX_REFRESH_SECONDS = 3 * 60    # 2026-05-13: cut 15min → 3min after divergence audit. With force_refresh at entry time, the background TTL only governs breach-exit / overlay ticks, where freshness matters less and Polygon's underlying snapshot only updates every ~15 min anyway. 3 min strikes the balance between staleness on the strike-adjuster re-check path and avoiding wasted fetches between entries.
_GEX_FAILURE_COOLDOWN = 60       # don't hammer a flaky API
# 2026-06-08 forensic (multi-variant contention): even a force_refresh, once it
# holds the cross-variant fetch_lock, will REUSE a sibling's just-written
# profile if it's at most this fresh — instead of issuing its own serial
# Polygon fetch. Without it, 3 variants entering the same slot each force a
# fetch UNDER the lock (~10s each → ~30s of GEX latency for the last variant,
# burning entry-window time). A sibling fetch a few seconds old is plenty fresh
# for an entry decision (Polygon's underlying snapshot only updates ~15 min).
_GEX_FORCE_REFRESH_SIBLING_WINDOW_S = 30
# Max age of a GEX profile usable for live STRIKE SELECTION. A force_refresh
# that SUCCEEDED yields a profile seconds old; one that FAILED returns the stale
# in-process profile (its fetched_at is NOT bumped on the failure paths), so a
# profile older than this means the live fetch failed — do NOT compute an 8δ
# short off it (the 2026-06-10 ~35δ put). Fall back to the conservative
# OTM-multiplier instead, which can only place the short WIDER, never closer.
_GEX_MAX_STRIKE_AGE_S = 45


class BrandonHydraStrategy(HydraStrategy):
    """HYDRA with Brandon Jones's Trojan Horse iron condor enhancements."""

    def __init__(
        self,
        broker,
        config,
        logger_service=None,
        dry_run: bool = False,
        alert_service=None,
    ):
        # Brandon STATE containers + hedge-state restore MUST be initialized
        # BEFORE super().__init__(): the base constructor runs position recovery
        # → _reconcile_recovered_entries_with_broker → _expected_position_quantities
        # (a Brandon override) which reads self._brandon_hedge_legs. Same
        # init-order rule as stop_buffer / short_only_stop (CLAUDE.md). 2026-06-10:
        # a mid-day variant-C restart raised "AttributeError: 'BrandonHydraStrategy'
        # object has no attribute '_brandon_hedge_legs'" here, so the startup
        # broker-reconcile was skipped (recovered ~10s later by the hourly POS-003).
        # Loading the sidecar pre-super() also lets the startup reconcile correctly
        # include any restored hedge-leg conids. Safe pre-super:
        # _brandon_resolve_hedge_state_path uses a module constant and
        # _brandon_today_date is a staticmethod — neither depends on super() state.
        self._brandon_gex_profile: Optional[GEXProfile] = None
        self._brandon_gex_profile_fetched_at: Optional[datetime] = None
        self._brandon_gex_failure_at: Optional[datetime] = None
        self._brandon_breach_states: dict[tuple[int, str], gex_breach_exit.BreachState] = {}
        self._brandon_overlay_placed: set[tuple[int, str]] = set()
        self._brandon_hydra_shadow_fired: set[tuple[int, str]] = set()
        # %-of-width stop SHADOW: (entry_number, side) already logged a would-fire
        # today (one head-to-head datapoint per side per day; see
        # _brandon_check_pctwidth_shadow_stop). Cleared in _reset_for_new_day.
        self._brandon_pctwidth_shadow_fired: set[tuple[int, str]] = set()
        # CONFIRMED %-of-width shadow (2026-06-25): when a side's SV FIRST crossed
        # the %-width trigger ({(entry, side): datetime}), and which (entry, side)
        # have logged the persistence-confirmed would-fire today. Both cleared in
        # _reset_for_new_day. The confirmed variant only "fires" if the breach
        # PERSISTS narrow_spread_stop_confirm_seconds — filtering whipsaw spikes.
        self._brandon_pctwidth_breach_at: dict = {}
        self._brandon_pctwidth_confirmed_fired: set[tuple[int, str]] = set()
        # Cooldown after a TP/BREACH close that transacted 0 legs (a doomed
        # close — e.g. a worthless far-OTM leg the broker won't fill). Keyed by
        # (entry_number, side); value is the ET timestamp of the last failed
        # attempt. Without this the close re-fires (and re-alerts) every ~11s
        # tick, which flooded the inbox on 2026-06-11. The side stays alive for
        # monitoring; we just space the retries to _BRANDON_FAILED_CLOSE_COOLDOWN_S
        # apart and the L-C2 credit+buffer backstop + end-of-day expiry still
        # protect the position between retries.
        self._brandon_failed_close_at: dict[tuple[int, str], datetime] = {}
        # Hedge legs placed during the day, keyed by entry_number. List grows
        # when an overlay fires; cleared in _reset_for_new_day. Persisted to
        # a sidecar JSON next to the bot's state file so a mid-day restart
        # doesn't lose hedge tracking. Settled against SPX_close in
        # log_daily_summary.
        self._brandon_hedge_legs: dict[int, list[HedgeLeg]] = {}
        self._brandon_hedge_settlements: list[HedgeSettlement] = []
        # Per-day set of entry_numbers whose overlay P&L has been booked into
        # total_realized_pnl (by EITHER the entry-attributed OR the aggregate-only
        # path). The settle sweep re-runs after a restart (settlements aren't
        # persisted); this UNIFIED, persisted guard makes the booking idempotent
        # regardless of path or of an entry's presence flipping between runs, so an
        # overlay reaches the day total exactly once. Persisted in hydra_state.json
        # ATOMICALLY with total_realized_pnl (NOT the hedge sidecar — else a crash
        # could restore the guard without the booked total, losing the overlay);
        # cleared on the new-day reset. Initialized BEFORE super().__init__() so it
        # exists if base-class recovery restores it.
        self._brandon_overlay_booked: set[int] = set()
        self._brandon_hedge_state_path = self._brandon_resolve_hedge_state_path()
        self._brandon_load_hedge_state()

        super().__init__(
            broker,
            config,
            logger_service,
            dry_run=dry_run,
            alert_service=alert_service,
        )

        bcfg = (config.get("strategy", {}) or {}).get("brandon", {}) or {}

        tp = bcfg.get("take_profit") or {}
        self.brandon_take_profit_enabled = bool(tp.get("enabled", False))
        self.brandon_take_profit_threshold = float(tp.get("threshold", 0.80))
        # Near-expiry "hold-if-safe": in the final N minutes, prefer riding a
        # comfortably-OTM IC to expiry (keeps 100% of credit, zero close cost)
        # over an 80% TP that pays slippage + commission. The credit+buffer stop
        # still backstops a reversal (this only suppresses the early TP, never the
        # stop). hold_to_expiry_minutes=0 disables it.
        #
        # cushion default 25 → 50pt (2026-06-23): an EMPIRICAL break-even from 84
        # trading days of SPX 1-min paths (Feb–Jun 2026). Riding to expiry instead
        # of taking the ~80% TP gains only ~20% of a thin credit (~$12/contract)
        # but risks a near-max-loss stop if a short is breached, so it is +EV ONLY
        # when the final-hour reversal (touch) rate is below ~3–7%. That rate is
        # 26.5% at a 25pt cushion (decisively −EV) but ~4–5% at 50pt — so 50pt is
        # the data-derived threshold at which holding is genuinely safe. Full
        # analysis + EV math: docs/HYDRA_HOLD_IF_SAFE_ANALYSIS.md.
        self.brandon_tp_hold_to_expiry_minutes = float(tp.get("hold_to_expiry_minutes", 60.0))
        self.brandon_tp_hold_safe_cushion_pts = float(tp.get("hold_safe_cushion_pts", 50.0))
        # A side whose spread VALUE is $0 is trusted as genuinely worthless (so
        # the IC's TP can still fire) only when its short is at least this many
        # points OTM; a $0 nearer the money is treated as a stale/missing quote
        # and TP is deferred (the 2026-06-15 worthless-leg-blocks-TP fix).
        self.brandon_tp_worthless_otm_pts = float(tp.get("worthless_otm_pts", 20.0))
        # MKT-049 (2026-06-22): net-of-cost TP gate. evaluate_iron_condor decides
        # on the MID mark (entry.*_spread_value), but a thin 0DTE credit spread
        # CLOSES at short_ask − long_bid, which can be many times the mid. On
        # 2026-06-22 variant-C E#2 the mid said "SV $17.50 → 87.5% captured" but
        # the real close cost was $105 (25% captured); after commission the $140
        # credit netted +$2.80 — the TP gave back ~75% to slippage it never saw.
        # When enabled, before firing a TP we recompute the REAL net capture from
        # live bid/ask (buy the short at ask, sell the long at bid) minus the
        # close commission, and HOLD if it's below `min_net_capture` — the
        # comfortably-OTM short then rides to expiry (100%, no close cost), still
        # backstopped by the GEX breach-exit + credit-buffer stop. FAIL-OPEN: a
        # missing / crossed quote falls back to the mid decision (current
        # behavior), so a flaky quote never blocks a legitimate close.
        self.brandon_tp_net_of_cost_gate_enabled = bool(
            tp.get("net_of_cost_gate_enabled", True)
        )
        # The real net-capture bar a TP must clear to actually close. Defaults to
        # the same threshold as the mid trigger, so "close at 80% captured" means
        # a REAL 80% net of slippage + commission. Lower it if the gate defers too
        # much (thin 0DTE spreads will then mostly ride to expiry — usually the
        # better outcome, see the 2026-06-22 post-mortem).
        self.brandon_tp_min_net_capture = float(
            tp.get("min_net_capture", self.brandon_take_profit_threshold)
        )
        if self.brandon_take_profit_enabled:
            logger.info(
                "  MKT-049 TP net-of-cost gate: %s (min real net capture %.0f%%)",
                "ENABLED" if self.brandon_tp_net_of_cost_gate_enabled else "DISABLED",
                self.brandon_tp_min_net_capture * 100,
            )

        gex = bcfg.get("gex") or {}
        self.brandon_gex_enabled = bool(gex.get("enabled", False))
        self.brandon_polygon_api_key_env = str(gex.get("polygon_api_key_env", "POLYGON_API_KEY"))
        self.brandon_polygon_underlying = str(gex.get("polygon_underlying", "SPX"))
        self.brandon_strike_adjuster_enabled = bool(gex.get("strike_adjuster_enabled", False))
        self.brandon_breach_exit_enabled = bool(gex.get("breach_exit_enabled", False))
        # A1 (2026-06-10): demote the GEX breach exit to ADVISORY — it still
        # evaluates + logs "would-close", but does NOT act; the credit+buffer
        # (with the L-C2 backstop) is the acting PRIMARY. The research is decisive
        # that a 90s wall-breach is mostly noise (70-86% false; needs ~20min) and
        # GEX adds little over VIX/IV — so this is the recommended B/C config.
        self.brandon_breach_exit_advisory = bool(gex.get("breach_exit_advisory", False))
        self.brandon_breach_confirmation_seconds = int(gex.get("breach_confirmation_seconds", 90))
        self.brandon_decel_min_pct = float(gex.get("decel_min_pct", 0.05))
        self.brandon_accel_min_pct = float(gex.get("accel_min_pct", 0.10))
        self.brandon_max_shift_pts = float(gex.get("max_shift_pts", 25.0))
        self.brandon_shift_buffer_pts = float(gex.get("shift_buffer_pts", 5.0))
        self.brandon_accel_peak_locality_pts = float(gex.get("accel_peak_locality_pts", 25.0))

        ov = bcfg.get("defensive_overlay") or {}
        self.brandon_overlay_enabled = bool(ov.get("enabled", False))
        self.brandon_overlay_trigger_distance_pts = float(ov.get("trigger_distance_pts", 25.0))
        self.brandon_overlay_butterfly_width = int(ov.get("butterfly_width_pts", 10))
        self.brandon_overlay_butterfly_cutoff_hour = int(ov.get("butterfly_cutoff_hour", 12))
        self.brandon_overlay_butterfly_cutoff_minute = int(ov.get("butterfly_cutoff_minute", 30))
        # Realistic dry-run ENTRY fill (2026-07-21). estimate_fill_price returns a
        # Black-Scholes MID with NO bid/ask spread, so the modeled overlay debit is
        # too cheap and the overlay P&L is inflated (the settlement side is already
        # realistic — SPXW is PM-settled at the close, cash-settled, held to expiry).
        # Cross the spread on entry: buy long legs toward the ask (+spread), sell
        # short legs toward the bid (−spread), by this per-leg half-spread ($/share).
        # Only the DRY-RUN path uses it; live fills come from the broker already
        # spread-crossed. 0.0 restores the old mid pricing. Tune against real 0DTE
        # SPX spreads (mirrors Strategy-D's dry_run_fill_model calibration).
        self.brandon_overlay_fill_spread = float(ov.get("dry_run_fill_spread_per_leg", 0.25))

        ns = bcfg.get("narrow_spread") or {}
        self.brandon_narrow_spread_enabled = bool(ns.get("enabled", False))
        self.brandon_narrow_breakpoint_vix = float(ns.get("breakpoint_vix", 22.0))
        self.brandon_narrow_width_low = int(ns.get("width_low", 5))
        self.brandon_narrow_width_high = int(ns.get("width_high", 10))

        # Per Brandon: strikes are picked by GEX zones (decel walls, accel zones)
        # and the configured starting OTM multiplier. NOT by HYDRA's
        # MKT-020/MKT-022 progressive credit-chasing tighteners. When True,
        # tighteners are skipped — strikes stay at their initial GEX-aware
        # position even if the credit-gate floor isn't met (MKT-011 will then
        # skip the entry, which is what Brandon would do anyway).
        # Live evidence 2026-05-07: MKT-022 walked B's E#5/E#6 puts from 125pt
        # OTM (safe) to 35-40pt OTM (right on the 7330 GEX wall) chasing
        # credit. Brandon's strike adjuster fired AFTER tightening but couldn't
        # find a decel cluster at that exact moment, returned silent KEEP, and
        # the strikes stayed on the wall. Result: 4 breach exits when the wall
        # broke, ~$8.7K in close costs.
        self.brandon_disable_progressive_tightening = bool(
            bcfg.get("disable_progressive_tightening", False)
        )

        # Brandon-faithful strike selection: anchor short strikes to a delta
        # target read from the live option chain (Polygon greeks). Replaces
        # HYDRA's "starting OTM = N × expected_move" approximation, which
        # drifts onto walls when expected_move underestimates real risk
        # (low-VIX days like 2026-05-07). target_delta_pct is the absolute
        # delta target as a fraction (0.08 = 8 delta); we read the existing
        # strategy.target_delta config (expressed as 8 = 8%) by default so a
        # variant doesn't have to set it twice. When True: _calculate_strikes
        # asks the GEX profile for the strike whose option delta is closest
        # to the target. If the chain isn't available or has no greeks, the
        # method falls back to HYDRA's OTM-multiplier path.
        dts = bcfg.get("delta_target_strike_selection") or {}
        self.brandon_delta_target_enabled = bool(dts.get("enabled", False))
        # Look first at the brandon-specific target (lets variants override),
        # then strategy.target_delta (already set, e.g. 8 = 8δ), default 0.08.
        _td_pct = dts.get("target_delta_pct")
        if _td_pct is None:
            _td_strategy = self.strategy_config.get("target_delta", 8)
            _td_pct = float(_td_strategy) / 100.0  # 8 → 0.08
        self.brandon_delta_target_pct = float(_td_pct)
        # PRICE SANITY VETO (2026-06-11): the delta-target picker keys off the
        # cached/recomputed delta, which on 0DTE carries a calendar-time ~2x
        # under-delta bias (Polygon's greeks AND our BS both understate). A short
        # truly at ~30delta can read ~0.12-0.16delta and pass the 0.16 clamp, so
        # the picker places it far too close (E#2 2026-06-11: 7250 put, 38pt OTM,
        # filled $2.50/share). The clamp validates a suspect delta against itself.
        # Price is the market's own, bias-immune statement of moneyness: a true
        # 8delta short collects a TINY fraction of its width; a too-close short
        # collects a large one. After the picker chooses strikes we estimate the
        # spread credit and REJECT (fall back to the conservative OTM-multiplier,
        # which can only place WIDER) when credit/width exceeds this ceiling.
        # E#2's *estimate* was $1.15/share on a 5pt width = 0.23 — already over a
        # 0.20 ceiling — so this catches it pre-placement even though the fill came
        # in richer. Default 0.20 ≈ the existing "2x target delta" (0.16) intent,
        # enforced on price instead of the biased delta. 0 disables the veto.
        self.brandon_delta_target_max_credit_pct_of_width = float(
            dts.get("max_credit_pct_of_width", 0.20)
        )
        # DEGRADED-DATA FLOOR (2026-07-17): the max-delta clamp only catches
        # too-CLOSE picks. The mirror failure — a too-FAR pick — happens when
        # Polygon's greek feed degrades (that day: 80/1000 strikes hydrated) and
        # the "closest to 8δ" among a sparse chain is really ~0.5-1δ, far-OTM,
        # ~$0.05 premium. B booked 3 phantom $0-credit ICs; C churned leg-in/
        # unwinds. Reject a selected short whose achieved delta is below
        # target × this fraction (0.5 → 4δ floor for an 8δ target) and SKIP the
        # entry (operator-chosen over falling back to the OTM-multiplier). 0
        # disables the floor. Acceptable band becomes [target×frac, 2×target].
        self.brandon_delta_target_min_pct_of_target = float(
            dts.get("min_delta_pct_of_target", 0.5)
        )

        hs = bcfg.get("hydra_stop_shadow") or {}
        self.brandon_hydra_shadow_enabled = bool(hs.get("enabled", True))

        # NOTE: the _brandon_* STATE containers + hedge-state restore are
        # initialized BEFORE super().__init__() above (recovery needs them) —
        # do not re-initialize them here or a same-day restart would wipe the
        # hedge legs just restored from the sidecar.

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
    # Strike calculation override — delta-target via Polygon chain
    # ------------------------------------------------------------------

    def _calculate_strikes(self, entry) -> bool:
        """Anchor short strikes to a Brandon delta target before falling
        through to HYDRA's calculation.

        Brandon's framework: pick the short strike whose option delta is
        closest to the configured target (default 8δ). Long strike sits at
        the spread width away. Spreads then enter the rest of HYDRA's
        pipeline (whipsaw filter, credit gate, MKT-045 chain snap, the
        Brandon GEX adjuster on top, etc) unchanged.

        Falls back to HYDRA's parent _calculate_strikes when:
          - delta_target_strike_selection is disabled
          - the GEX profile isn't available yet (Polygon failure / first
            tick of the day)
          - the chain has no per-contract greeks for either side
        """
        if not getattr(self, "brandon_delta_target_enabled", False):
            return super()._calculate_strikes(entry)

        spot = float(self.current_price or 0.0)
        if spot <= 0:
            logger.debug("BRANDON-DELTA-TARGET: spot unknown — falling back to OTM-multiplier")
            return super()._calculate_strikes(entry)

        # Force-refresh: this is the entry-placement strike-selection moment,
        # the most consequential GEX read of the day. Pull a brand-new chain
        # snapshot (under the cross-variant fetch lock) so the picker keys
        # off chain data that's seconds old, not minutes. The just-written
        # profile then warms the shared cache so _brandon_apply_strike_adjuster
        # (called a few hundred ms later from _execute_entry / _simulate_entry)
        # and any breach-exit ticks in the next 3 min hit the cache without
        # re-fetching.
        profile = self._brandon_get_gex_profile(
            self._brandon_today_date(), force_refresh=True
        )
        # STALE-GREEKS GUARD: never compute a delta-target short off a stale
        # profile. A failed live fetch returns the last (unrefreshed) in-process
        # profile, whose old fetched_at we age-reject here so the picker falls
        # back to HYDRA's conservative OTM-multiplier (structurally WIDER) rather
        # than placing a too-close short (2026-06-10 ~35δ put off a stale chain).
        prof_age = None
        if profile is not None and getattr(profile, "fetched_at", None) is not None:
            prof_age = (datetime.now(timezone.utc) - profile.fetched_at).total_seconds()
        stale = prof_age is not None and prof_age > _GEX_MAX_STRIKE_AGE_S
        if profile is None or not profile.deltas or stale:
            reason = (
                f"stale GEX profile (age={prof_age:.0f}s > {_GEX_MAX_STRIKE_AGE_S}s — live fetch likely failed)"
                if stale else "no chain delta data"
            )
            logger.warning(
                "BRANDON-DELTA-TARGET E#%s: %s — falling back to OTM-multiplier",
                getattr(entry, "entry_number", "?"), reason,
            )
            if stale:
                # Observability: a real entry's strike selection silently
                # degraded to the conservative path — the operator should know
                # GEX was unavailable for this entry.
                self._brandon_send_telegram(
                    message=(
                        f"Entry #{getattr(entry, 'entry_number', '?')}: GEX profile stale "
                        f"({prof_age:.0f}s) — used conservative OTM-multiplier instead of the "
                        f"{self.brandon_delta_target_pct:.2f}δ delta-target. Polygon GEX fetch likely timed out."
                    ),
                    title="GEX stale — strike selection fell back",
                    priority_name="MEDIUM",
                    alert_type_name="SLIPPAGE_ALERT",
                )
            return super()._calculate_strikes(entry)

        target = self.brandon_delta_target_pct
        # Recompute delta from cached IV at LIVE spot. The cached snapshot
        # is up to 3 minutes old (cache TTL) plus Polygon's own ~15-min
        # delayed feed — on 0DTE that's enough drift to turn an 8δ pick
        # into a 14δ one (verified on B's 2026-05-12 E#4: chain showed
        # 8δ at 7320 from spot=7358 fetch, but live spot at placement was
        # 7366.61 → real delta = 14δ). Falls back to cached delta when IV
        # is missing for a strike.
        t_years = self._brandon_estimate_t_years_to_close()
        # S-HIGH-1: reject a "closest" match more than 2x the target delta (8δ →
        # 16δ ceiling). A sparse/ATM-biased chain can otherwise place the short
        # at 20-35δ — far too close — even off a fresh profile; falling back to
        # the OTM-multiplier is the safe outcome there.
        max_delta = target * 2.0
        call_short, call_delta = gex_provider.find_strike_at_delta(
            profile, side="call", target_delta_abs=target, spot_fallback=spot,
            recompute_t_years=t_years, increment=self.strike_increment,
            max_delta_abs=max_delta, return_delta=True,
        )
        put_short, put_delta = gex_provider.find_strike_at_delta(
            profile, side="put", target_delta_abs=target, spot_fallback=spot,
            recompute_t_years=t_years, increment=self.strike_increment,
            max_delta_abs=max_delta, return_delta=True,
        )
        if call_short is None or put_short is None:
            logger.warning(
                "BRANDON-DELTA-TARGET E#%s: chain missing %s — falling back to OTM-multiplier",
                getattr(entry, "entry_number", "?"),
                "call greeks" if call_short is None else "put greeks",
            )
            return super()._calculate_strikes(entry)

        # Apply VIX-adjusted spread width (Brandon narrow on B/C).
        call_width = self._get_vix_adjusted_spread_width(self.current_vix, "call")
        put_width = self._get_vix_adjusted_spread_width(self.current_vix, "put")
        entry.short_call_strike = float(call_short)
        entry.long_call_strike = float(call_short + call_width)
        entry.short_put_strike = float(put_short)
        entry.long_put_strike = float(put_short - put_width)
        # entry.spread_width is a derived @property reading max(call_width,
        # put_width) from the strikes we just set — no setter needed.

        logger.info(
            "BRANDON-DELTA-TARGET E#%s: target=%.3fδ → call %.0f/%.0f (w=%dpt), put %.0f/%.0f (w=%dpt) at spot %.2f",
            getattr(entry, "entry_number", "?"),
            target,
            entry.short_call_strike, entry.long_call_strike, call_width,
            entry.short_put_strike, entry.long_put_strike, put_width,
            spot,
        )

        # DEGRADED-DATA FLOOR (2026-07-17) — reject a too-FAR pick (mirror of the
        # max-delta clamp). When Polygon's greek feed degrades (that day: 80/1000
        # strikes hydrated), the "closest to 8δ" among a sparse chain is really
        # ~0.5-1δ: near-worthless, far-OTM garbage that passes every existing
        # guard (fresh profile, under the max clamp, ~$0 credit so the price-veto
        # can't fire). Operator choice 2026-07-17: SKIP the entry — an
        # under-hydrated chain is not a tradeable signal — rather than fall back
        # to the OTM-multiplier. call_delta/put_delta are the achieved
        # (drift-adjusted) deltas of the picked shorts from find_strike_at_delta.
        floor_frac = getattr(self, "brandon_delta_target_min_pct_of_target", 0.0)
        if floor_frac and floor_frac > 0:
            min_delta = target * floor_frac
            worst = None
            if call_delta is not None and abs(call_delta) < min_delta:
                worst = ("call", entry.short_call_strike, call_delta)
            elif put_delta is not None and abs(put_delta) < min_delta:
                worst = ("put", entry.short_put_strike, put_delta)
            if worst is not None:
                side, strike, dlt = worst
                reason = (
                    f"delta-target {side} short {strike:.0f} is {abs(dlt) * 100:.1f}δ "
                    f"< {min_delta * 100:.1f}δ floor (target {target * 100:.1f}δ) — chain "
                    f"under-hydrated, picked near-worthless far-OTM strikes"
                )
                logger.warning(
                    "BRANDON-DELTA-TARGET E#%s: %s → SKIPPING entry (degraded data).",
                    getattr(entry, "entry_number", "?"), reason,
                )
                self._brandon_send_telegram(
                    message=(
                        f"Entry #{getattr(entry, 'entry_number', '?')}: delta-target picked a "
                        f"{abs(dlt) * 100:.1f}δ {side} short (target {target * 100:.1f}δ) — Polygon "
                        f"greeks look degraded/sparse. Skipped the entry (no trade on unreliable "
                        f"chain data)."
                    ),
                    title="Delta-target degraded — entry skipped",
                    priority_name="MEDIUM",
                    alert_type_name="SLIPPAGE_ALERT",
                    details={
                        "entry_number": getattr(entry, "entry_number", None),
                        "side": side, "achieved_delta": round(abs(dlt), 4), "target": target,
                    },
                )
                entry.abort_entry_reason = reason
                return True

        # PRICE SANITY VETO (2026-06-11) — see __init__ comment. The picker keys
        # off a 0DTE delta that systematically UNDER-states moneyness, so a short
        # truly ~25-35delta can be selected as the "8delta" short and placed far
        # too close (E#2 2026-06-11). Price is the bias-immune cross-check: a true
        # 8delta short collects a tiny fraction of its width; a too-close one
        # collects a large fraction. Estimate the spread credit for the chosen
        # strikes and, if either side's credit exceeds max_credit_pct_of_width of
        # its width, fall back to the conservative OTM-multiplier (which can only
        # place WIDER). FAIL-SAFE: an estimation failure (0 credit / exception)
        # never vetoes — we only widen on a CONFIRMED too-rich short, never block
        # an entry on a flaky quote. est_* are per-contract dollars; a spread's
        # max value per contract is width_pt * 100, so credit/width = est/(w*100).
        ceiling = getattr(self, "brandon_delta_target_max_credit_pct_of_width", 0.0)
        if ceiling and ceiling > 0:
            try:
                est_call, est_put = self._estimate_entry_credit(entry)
            except Exception as exc:
                est_call, est_put = 0.0, 0.0
                logger.debug(
                    "BRANDON-DELTA-TARGET E#%s: price-veto estimate failed (%s) — not vetoing",
                    getattr(entry, "entry_number", "?"), exc,
                )
            too_rich = None
            if est_call and call_width > 0 and est_call / (call_width * 100) > ceiling:
                too_rich = ("call", est_call, call_width, est_call / (call_width * 100))
            elif est_put and put_width > 0 and est_put / (put_width * 100) > ceiling:
                too_rich = ("put", est_put, put_width, est_put / (put_width * 100))
            if too_rich is not None:
                side, est, w, ratio = too_rich
                logger.warning(
                    "BRANDON-DELTA-TARGET E#%s: %s short credit $%.2f/contract = %.0f%% of %dpt width "
                    "(> %.0f%% ceiling) — the '%.3fδ' pick is really far closer than target "
                    "(0DTE delta under-stated it); falling back to OTM-multiplier (wider).",
                    getattr(entry, "entry_number", "?"), side, est / 100.0, ratio * 100, w,
                    ceiling * 100, target,
                )
                self._brandon_send_telegram(
                    message=(
                        f"Entry #{getattr(entry, 'entry_number', '?')}: delta-target {side} short would "
                        f"collect {ratio * 100:.0f}% of its {w}pt width (${est / 100:.2f}/contract) — too "
                        f"close for a {target:.2f}δ target (likely under-stated 0DTE delta). Used the "
                        f"conservative OTM-multiplier instead."
                    ),
                    title="Delta-target too close — used OTM-multiplier",
                    priority_name="MEDIUM",
                    alert_type_name="SLIPPAGE_ALERT",
                    details={"entry_number": getattr(entry, "entry_number", None), "side": side},
                )
                return super()._calculate_strikes(entry)

        return True

    # ------------------------------------------------------------------
    # Strike adjuster — applied just before order placement (live + dry)
    # ------------------------------------------------------------------

    def _execute_entry(self, entry) -> bool:
        self._brandon_apply_strike_adjuster(entry)
        if getattr(entry, "require_both_abort", False):
            # one_sided disabled + GEX would route one-sided → don't place;
            # _initiate_entry converts the False return into a clean skip.
            return False
        return super()._execute_entry(entry)

    def _simulate_entry(self, entry) -> bool:
        self._brandon_apply_strike_adjuster(entry)
        if getattr(entry, "require_both_abort", False):
            return False
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
            accel_peak_locality_pts=self.brandon_accel_peak_locality_pts,
            strike_increment=self.strike_increment,
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
                if not getattr(self, "one_sided_entries_enabled", True):
                    # require-both-sides: refuse to route one-sided; abort the entry.
                    logger.warning(
                        "BRANDON-GEX-ADJ E#%s call: SKIP — %s. one_sided_entries_enabled=false "
                        "→ ABORTING entry (require both sides).",
                        entry.entry_number, r.reason,
                    )
                    entry.require_both_abort = True
                    return
                logger.warning(
                    "BRANDON-GEX-ADJ E#%s call: SKIP — %s. Routing as put-only entry.",
                    entry.entry_number, r.reason,
                )
                entry.call_side_skipped = True
                entry.short_call_strike = 0.0
                entry.long_call_strike = 0.0
                if hasattr(entry, "put_only"):
                    entry.put_only = True
            else:
                # KEEP — log so we have visibility on no-op decisions. Without
                # this the journal looked like the adjuster wasn't running.
                logger.info(
                    "BRANDON-GEX-ADJ E#%s call: KEEP — short %.0f, %s",
                    entry.entry_number, entry.short_call_strike, r.reason,
                )

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
                if not getattr(self, "one_sided_entries_enabled", True):
                    # require-both-sides: refuse to route one-sided; abort the entry.
                    logger.warning(
                        "BRANDON-GEX-ADJ E#%s put: SKIP — %s. one_sided_entries_enabled=false "
                        "→ ABORTING entry (require both sides).",
                        entry.entry_number, r.reason,
                    )
                    entry.require_both_abort = True
                    return
                logger.warning(
                    "BRANDON-GEX-ADJ E#%s put: SKIP — %s. Routing as call-only entry.",
                    entry.entry_number, r.reason,
                )
                entry.put_side_skipped = True
                entry.short_put_strike = 0.0
                entry.long_put_strike = 0.0
                if hasattr(entry, "call_only"):
                    entry.call_only = True
            else:
                # KEEP — log so we can audit no-op decisions. The 2026-05-07
                # incident was hidden because the put adjuster returned KEEP
                # silently (no decel cluster detected at that exact tick), and
                # the journal had no record of the adjuster having run.
                logger.info(
                    "BRANDON-GEX-ADJ E#%s put: KEEP — short %.0f, %s",
                    entry.entry_number, entry.short_put_strike, r.reason,
                )

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

        # 2. GEX breach exit — Brandon's PRIMARY stop, but ONLY when GEX is
        #    actually armed this tick. L-C1: GEX can be unavailable (no Polygon
        #    key, fetch failure, failure-cooldown, empty profile, or breach-exit
        #    disabled) — and when it is, the breach exit can NEVER fire, leaving
        #    a defined-risk IC riding to ~max loss with NO acting stop. We probe
        #    availability up front (cheap — the profile is cached / cooldown'd
        #    and returns None instantly when no Polygon key) and, when GEX is
        #    down, promote HYDRA's proven credit+buffer stop from SHADOW to the
        #    LIVE-acting fallback (super()._check_stop_losses — the same
        #    MKT-046-confirmed stop + _execute_stop_loss that variant A runs).
        #    Per tick the GEX stop and the fallback are mutually exclusive, so
        #    there is never a double-stop.
        gex_stop_armed = False
        if self.brandon_gex_enabled and self.brandon_breach_exit_enabled:
            gex_profile = self._brandon_get_gex_profile(self._brandon_today_date())
            gex_stop_armed = gex_profile is not None

        if gex_stop_armed:
            for entry in list(self.daily_state.active_entries):
                action = self._brandon_check_breach_exit(entry)
                if action:
                    # HYDRA shadow stop also evaluates this tick before we return,
                    # so the comparison log captures both events on the same day.
                    self._brandon_check_hydra_shadow_stop(entry)
                    return action

        # 3. HYDRA credit+buffer stop — a LIVE backstop in BOTH GEX states.
        #
        #    FALLBACK (L-C1): GEX fully unavailable → the credit+buffer is the
        #    ONLY protection; a one-time-per-day alert announces the degraded
        #    mode.
        #
        #    BACKSTOP (L-C2, 2026-06-10): GEX is armed but the breach exit did
        #    NOT fire this tick. The breach exit only fires when spot breaches a
        #    decel-wall EDGE, which can sit far from the short — a wide/low wall,
        #    or a strike placed off a stale-greeks profile. That left a
        #    threatened short with NO acting stop while the credit+buffer ran
        #    shadow-only: the 2026-06-10 variant-C Entry#1 gap — put deep ITM at
        #    ~16% cushion, the only decel wall 340pt below the 7290 short so the
        #    breach exit could never fire, credit+buffer shadowed → the short
        #    rode unstopped toward max loss. Fix: the credit+buffer ACTS as the
        #    backstop here too. The GEX breach already had first crack above (it
        #    returns early when it fires), so super() only catches a side the
        #    breach exit left open AND that has breached its MKT-046-confirmed
        #    credit+buffer level. GEX breach stays the PRIMARY (fires earlier, at
        #    the wall); the credit+buffer is the floor beneath it. The two are
        #    mutually exclusive per tick → never a double-stop.
        if list(self.daily_state.active_entries):
            if not gex_stop_armed:
                self._brandon_alert_gex_fallback()
            elif self.brandon_hydra_shadow_enabled:
                # Early-warning + head-to-head record: the FIRST tick a side
                # breaches its credit+buffer level, log/alert it. super() below
                # then ACTS once MKT-046 confirms (~10s later) — so this is a
                # heads-up that the backstop is arming, not a never-acting shadow.
                for entry in list(self.daily_state.active_entries):
                    self._brandon_check_hydra_shadow_stop(entry)
            action = super()._check_stop_losses()
            if action:
                return action

        # 3b. %-of-width stop SHADOW (logs only, never acts) — head-to-head data
        #     for the shadow-first rollout decision on C. Runs only when no stop
        #     fired this tick (i.e. credit+buffer did NOT act), which is exactly
        #     when we want to know "would the tighter %-of-width have fired here?".
        #     Defensive: a shadow bug must never break the trading loop.
        if getattr(self, "narrow_spread_stop_shadow", False):
            for entry in list(self.daily_state.active_entries):
                try:
                    self._brandon_check_pctwidth_shadow_stop(entry)
                except Exception as exc:
                    logger.debug("A2-SHADOW check failed (non-fatal): %s", exc)

        # 4. Defensive overlay (LIVE) — places hedge orders when triggered
        if self.brandon_gex_enabled and self.brandon_overlay_enabled:
            for entry in list(self.daily_state.active_entries):
                self._brandon_check_overlay(entry)

        # 5. GEX breach (step 2) is the PRIMARY stop; the credit+buffer (step 3)
        #    is the LIVE backstop beneath it. Both stop paths have already run,
        #    so there is nothing further to call.
        return None

    # ------------------------------------------------------------------
    # Take-profit (LIVE)
    # ------------------------------------------------------------------

    @staticmethod
    def _tp_value_trustworthy(value: float, otm_pts: Optional[float], worthless_otm_pts: float) -> bool:
        """Is a side's spread value reliable enough to base a TP decision on?

        True if value > 0 (a real quote), OR value is $0 but the short is
        comfortably OTM (>= worthless_otm_pts) — a far-OTM option near expiry is
        genuinely worthless, so $0 is real. A $0 nearer the money is treated as a
        stale/missing quote (untrustworthy) so we defer rather than fire TP on it.

        Replaces the old ``spread_value == 0 -> skip`` guard, which PERMANENTLY
        blocked TP on any IC with a worthless leg (2026-06-15: variant C E#1's
        7450 put decayed to $0 and the entry could never take profit).
        """
        if value > 0:
            return True
        if otm_pts is None:
            return False
        return otm_pts >= worthless_otm_pts

    @staticmethod
    def _tp_hold_to_expiry(
        minutes_to_close: Optional[float],
        hold_window_min: float,
        call_otm_pts: Optional[float],
        put_otm_pts: Optional[float],
        cushion_pts: float,
    ) -> bool:
        """Prefer holding to expiry over an 80% TP in the final ``hold_window_min``
        minutes when every LIVE short (otm not None) is at least ``cushion_pts``
        OTM. Expiry keeps 100% with zero close cost; the credit+buffer stop still
        guards a reversal (this only suppresses the early TP, never the stop). A
        short within the cushion is NOT safe -> returns False so TP can still fire.
        """
        if hold_window_min <= 0:
            return False
        if minutes_to_close is None or minutes_to_close > hold_window_min:
            return False
        known = [o for o in (call_otm_pts, put_otm_pts) if o is not None]
        if not known:
            return False  # no spot / no assessable live short -> can't confirm safe
        return all(o >= cushion_pts for o in known)

    def _minutes_to_market_close(self) -> Optional[float]:
        """Minutes from now until the regular (or early-close) cash session end,
        or None outside the session (so hold-if-safe is inert after hours and in
        unit tests run after market close)."""
        try:
            from shared.market_hours import get_us_market_time, get_market_close_time
            from bots.hydra.base_strategy import is_market_open
            if not is_market_open():
                return None
            now = get_us_market_time()
            close_t = get_market_close_time(now)
            close_dt = now.replace(
                hour=close_t.hour, minute=close_t.minute, second=0, microsecond=0
            )
            return (close_dt - now).total_seconds() / 60.0
        except Exception:
            return None

    def _brandon_check_take_profit(self, entry) -> Optional[str]:
        call_alive = self._brandon_side_alive(entry, "call")
        put_alive = self._brandon_side_alive(entry, "put")
        if not call_alive and not put_alive:
            return None

        # OTM cushion (index points, + = still OTM) for each LIVE short. Numeric
        # guards so a missing/non-numeric strike yields None (treated as unknown)
        # rather than raising.
        spot = self.current_price
        spot_ok = isinstance(spot, (int, float)) and spot > 0
        sc, sp = entry.short_call_strike, entry.short_put_strike
        call_otm = (sc - spot) if (call_alive and spot_ok and isinstance(sc, (int, float)) and sc > 0) else None
        put_otm = (spot - sp) if (put_alive and spot_ok and isinstance(sp, (int, float)) and sp > 0) else None
        worthless_otm = getattr(self, "brandon_tp_worthless_otm_pts", 20.0)

        # Staleness guard (fixed 2026-06-15): defer TP only when a live side's $0
        # value is UNTRUSTWORTHY (a stale/missing quote near the money) — NOT when
        # it decayed to a genuine $0 far OTM. The old ``value == 0`` test blocked
        # TP forever on any worthless-leg IC (variant C E#1 never took profit).
        if call_alive and entry.call_spread_credit > 0 and not self._tp_value_trustworthy(
            entry.call_spread_value, call_otm, worthless_otm
        ):
            return None
        if put_alive and entry.put_spread_credit > 0 and not self._tp_value_trustworthy(
            entry.put_spread_value, put_otm, worthless_otm
        ):
            return None

        # Near-expiry hold-if-safe: ride a comfortably-OTM IC to expiry (100%, no
        # close cost) rather than take an 80% profit that pays slippage + fees.
        hold_window = getattr(self, "brandon_tp_hold_to_expiry_minutes", 60.0)
        if self._tp_hold_to_expiry(
            self._minutes_to_market_close(),
            hold_window,
            call_otm,
            put_otm,
            getattr(self, "brandon_tp_hold_safe_cushion_pts", 50.0),
        ):
            logger.debug(
                "BRANDON-TP E#%s: holding to expiry (safe OTM, within %.0fm of close)",
                entry.entry_number, hold_window,
            )
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

        # MKT-049 (2026-06-22): net-of-cost gate. The decision above used the MID
        # mark; re-check against the REAL closeable cost (short_ask − long_bid)
        # net of close commission. Close ONLY when the real net capture
        # POSITIVELY clears the bar.
        #
        # FAIL CLOSED (2026-06-23): hold unless we can confirm the bar is cleared.
        # `real_capture is None` means a SHORT leg is unquoted/crossed (cost
        # unpriceable) — the old code fell through to the optimistic mid there,
        # and that is exactly how C E#1/E#2 fired TPs at ~50% real capture once a
        # worthless long leg went unquoted near expiry. A held winner is still
        # protected by the GEX breach-exit + credit+buffer stop + expiry, so
        # holding is the safe default for a DISCRETIONARY profit-take.
        if getattr(self, "brandon_tp_net_of_cost_gate_enabled", True):
            min_net = getattr(
                self, "brandon_tp_min_net_capture",
                getattr(self, "brandon_take_profit_threshold", 0.80),
            )
            real_capture = self._brandon_real_close_capture(entry, call_alive, put_alive)
            if real_capture is None or real_capture < min_net:
                if not getattr(entry, "_mkt049_deferred", False):
                    entry._mkt049_deferred = True
                    if real_capture is None:
                        logger.info(
                            "MKT-049 E#%s: TP HELD — real close cost unpriceable (a "
                            "short leg is unquoted/crossed); NOT closing on the "
                            "optimistic mid. Protected by stop + GEX exit + expiry.",
                            entry.entry_number,
                        )
                    else:
                        logger.info(
                            "MKT-049 E#%s: TP DEFERRED — real net capture %.0f%% < %.0f%% "
                            "bar (mid mark said %.0f%%, but short_ask−long_bid + commission "
                            "give the gain back). Holding to expiry / stop / GEX exit.",
                            entry.entry_number, real_capture * 100,
                            min_net * 100, decision.profit_captured_pct * 100,
                        )
                else:
                    logger.debug(
                        "MKT-049 E#%s: TP still held — real %s vs %.0f%% bar",
                        entry.entry_number,
                        "unpriceable" if real_capture is None else f"{real_capture * 100:.0f}%",
                        min_net * 100,
                    )
                return None
            # Cleared — reset so a later genuine hold logs again.
            if getattr(entry, "_mkt049_deferred", False):
                entry._mkt049_deferred = False

        # Don't re-fire a doomed close every tick: if every still-alive side is
        # cooling down from a recent 0-leg close, wait. The side(s) stay alive
        # and monitored; the L-C2 credit+buffer backstop + expiry still protect.
        tp_alive = [s for s in ("call", "put") if self._brandon_side_alive(entry, s)]
        if tp_alive and all(self._brandon_close_in_cooldown(entry, s) for s in tp_alive):
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
        # 2. Realized-P&L correction — DRY-RUN ONLY. In live mode
        #    _close_entry_early already subtracts the real fill-based close
        #    cost from total_realized_pnl (it adds credit then subtracts
        #    side_close_cost once a fill price is available). Subtracting the
        #    pre-close mark here as well would double-count the close cost and
        #    understate realized P&L by ~one close cost per closed side. In
        #    dry-run, deferred fills never resolve (no real broker positions),
        #    so _close_entry_early leaves a credit-only number; we subtract
        #    close_cost here to match what live mode converges to. The
        #    actual_*_stop_debit journaling field is set in BOTH modes (for the
        #    dashboard); only the total_realized_pnl subtraction is guarded.
        # FAIL-CLOSED (06-04 audit): only mark a side stopped if _close_entry_early
        # ACTUALLY closed it. It sets *_side_expired iff >=1 leg of that side
        # closed, so that flag is the per-side success signal. If a side was alive
        # but did NOT close (broker failure), the live legs are STILL OPEN — do
        # NOT mark it stopped (that would silently drop a live position from
        # monitoring, the 06-04 orphan bug). Leave it alive so the next tick
        # re-fires TP and retries, and alert.
        call_did_close = call_alive and getattr(entry, "call_side_expired", False)
        put_did_close = put_alive and getattr(entry, "put_side_expired", False)
        if call_did_close:
            self._brandon_clear_close_failed(entry, "call")
            entry.call_side_stopped = True
            close_cost_call = float(entry.call_spread_value) if entry.call_spread_value else 0.0
            # _close_entry_early already wrote the REAL fill-based close cost to
            # actual_call_stop_debit in live mode; only fall back to the pre-close
            # MARK (spread_value) when that's absent (dry-run / no real fills) so
            # the dashboard P&L isn't overstated by the mark-vs-fill gap.
            if not getattr(entry, "actual_call_stop_debit", 0):
                entry.actual_call_stop_debit = close_cost_call
            if self.dry_run:
                self._book_realized_pnl(-close_cost_call, entry)
        elif call_alive:
            logger.critical(
                "BRANDON-TP E#%s: call close returned 0 legs but the call legs are STILL OPEN "
                "— NOT marking stopped, will retry next tick (orphaned live position; investigate).",
                entry.entry_number,
            )
            self._brandon_alert_orphan_close(entry, "call", "TP")
        if put_did_close:
            self._brandon_clear_close_failed(entry, "put")
            entry.put_side_stopped = True
            close_cost_put = float(entry.put_spread_value) if entry.put_spread_value else 0.0
            # See call side: prefer the real fill cost _close_entry_early wrote;
            # only fall back to the pre-close MARK when absent (dry-run).
            if not getattr(entry, "actual_put_stop_debit", 0):
                entry.actual_put_stop_debit = close_cost_put
            if self.dry_run:
                self._book_realized_pnl(-close_cost_put, entry)
        elif put_alive:
            logger.critical(
                "BRANDON-TP E#%s: put close returned 0 legs but the put legs are STILL OPEN "
                "— NOT marking stopped, will retry next tick (orphaned live position; investigate).",
                entry.entry_number,
            )
            self._brandon_alert_orphan_close(entry, "put", "TP")
        # Tag the close so the dashboard / journal can distinguish "TP at 80%
        # captured" from a stop or end-of-day expiry. Only tag if something
        # actually closed.
        if call_did_close or put_did_close:
            entry.close_reason = "TP"
        return (
            f"BRANDON-TP E#{entry.entry_number}: closed {legs_closed} legs "
            f"({legs_failed} failed) — {decision.profit_captured_pct:.1%} captured, "
            f"close_cost call=${entry.actual_call_stop_debit:.2f} put=${entry.actual_put_stop_debit:.2f}"
        )

    def _brandon_real_close_capture(
        self, entry, call_alive: bool, put_alive: bool
    ) -> Optional[float]:
        """Fraction of credit a TP would ACTUALLY net if closed right now, from
        live fillable prices instead of the mid mark (MKT-049).

        For each still-alive side we close by BUYING the short back at its ask
        and SELLING the long at its bid, so the real per-share close cost is
        ``short_ask − long_bid``; net capture =
        ``(Σcredit − Σreal_close_cost − close_commission) / Σcredit``.

        Returns the net-capture fraction, or ``None`` ONLY when a SHORT leg (the
        cost driver — you must buy it back) is unquoted or its book is crossed,
        so the real cost genuinely can't be determined. The caller then FAILS
        CLOSED (holds), never closing on the optimistic mid.

        A worthless / unquoted / crossed LONG leg is NOT a None — the long is
        recovery only, so we treat its bid as $0 (you recover nothing) and still
        price the side from the short. This stops a single dead long leg from
        blinding the gate to a still-wide short side (2026-06-23: a worthless
        long_call near expiry was unquoted → the old code returned None → the
        caller fail-OPENed → the optimistic mid TP fired at ~50% real capture on
        C E#1/E#2). $0 recovery is CONSERVATIVE — it over-states the close cost,
        understates capture, and biases toward HOLDING, the safe direction for a
        discretionary profit-take. Mirrors the entry-side MKT-048.
        """
        try:
            sides = []  # (short_conid, long_conid, credit_dollars)
            if call_alive and float(getattr(entry, "call_spread_credit", 0) or 0) > 0:
                sides.append((entry.short_call_uic, entry.long_call_uic,
                              float(entry.call_spread_credit)))
            if put_alive and float(getattr(entry, "put_spread_credit", 0) or 0) > 0:
                sides.append((entry.short_put_uic, entry.long_put_uic,
                              float(entry.put_spread_credit)))
            if not sides:
                return None

            # The SHORT conids are MANDATORY (you must price the buy-back) — a
            # missing/invalid short → unpriceable → None (caller HOLDS). A missing
            # LONG conid (e.g. a long independently salvaged while its short stays
            # alive) is treated like a worthless long: $0 recovery, side priced
            # from the short (the per-side loop handles lq=None below). Only fetch
            # the conids that are real, so an invalid long can't poison the batch.
            short_conids = [s[0] for s in sides]
            if not all(isinstance(c, int) and c > 0 for c in short_conids):
                return None
            conids = [c for s in sides for c in (s[0], s[1])
                      if isinstance(c, int) and c > 0]
            quotes = self._read_option_quotes_batch(conids) or {}

            contracts = int(getattr(entry, "contracts", 0) or self.contracts_per_entry)
            if contracts <= 0:
                return None

            total_credit = 0.0
            total_close_cost = 0.0
            for short_cid, long_cid, credit in sides:
                sq, lq = quotes.get(short_cid), quotes.get(long_cid)
                # SHORT leg drives the cost (buy it back). Unquoted or crossed →
                # the close is unpriceable → None (caller FAILS CLOSED = holds).
                if not isinstance(sq, dict):
                    return None
                short_ask = sq.get("ask")
                if short_ask is None:
                    return None
                sb, sa = sq.get("bid"), sq.get("ask")
                if sb is not None and sa is not None and float(sb) > float(sa):
                    return None  # crossed short book — garbage, can't price
                # LONG leg is recovery only (sell it). A worthless / unquoted /
                # crossed long, or one whose bid illogically exceeds the short ask,
                # recovers ~nothing → treat its bid as $0 and STILL price the side
                # from the short, instead of aborting the whole entry on one dead
                # leg. $0 recovery is conservative (over-states cost → holds).
                long_bid = lq.get("bid") if isinstance(lq, dict) else None
                if long_bid is not None and isinstance(lq, dict):
                    lb, la = lq.get("bid"), lq.get("ask")
                    if lb is not None and la is not None and float(lb) > float(la):
                        long_bid = None  # crossed long book — distrust the quote
                if long_bid is None or float(long_bid) > float(short_ask):
                    long_bid = 0.0
                close_cost_ps = float(short_ask) - float(long_bid)  # >= 0 by construction
                total_close_cost += close_cost_ps * 100.0 * contracts
                total_credit += credit

            if total_credit <= 0:
                return None
            # Close commission: 2 legs per live side at the configured per-leg rate.
            close_commission = 2 * len(sides) * self.commission_per_leg * contracts
            return (total_credit - total_close_cost - close_commission) / total_credit
        except Exception as exc:
            # A quote-fetch / data error during a TP decision must never crash the
            # monitoring loop — return None so the caller FAILS CLOSED (holds the
            # winner) rather than closing on the unverified mid.
            logger.debug("MKT-049 E#%s: real-capture check errored (%s) — holding (fail closed)",
                         getattr(entry, "entry_number", "?"), exc)
            return None

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
            # Filter to walls that actually protect the THREATENED wing. The
            # band is bounded on BOTH sides and anchored to the IC's own
            # strikes (fixed at entry), NOT to live spot.
            #
            # Why not gate on live spot: once spot has pushed past a wall the
            # wall's edge falls on the far side of spot, so a live-spot gate
            # would drop the wall exactly mid-breach and reset
            # evaluate_breach's confirmation timer — losing the real signal
            # when we need it most. The reference must be a fixed entry-time
            # level. We use the IC midpoint between the two shorts as a
            # spot-at-entry proxy (the condor is built symmetrically around
            # the entry spot).
            #
            # The previous filter used only ONE bound (call: strike_low <=
            # short_call; put: strike_high >= short_put). That admits walls in
            # the OPPOSITE wing: under build_profile's SpotGamma convention the
            # positive/decel clusters are put-dominated and sit BELOW spot, so
            # on the call side a put-wing wall far below entry spot trivially
            # satisfies strike_low <= short_call. evaluate_breach then takes it
            # as the outermost wall (max strike_high) and, with its
            # strike_high < spot, reports `spot > strike_high` every tick → a
            # perpetual false "confirmed call breach" after the 90s window,
            # closing the call leg though price never approached the short.
            # The mirror defect exists on the put side for above-spot walls.
            #
            # Fix — keep only walls genuinely on the threatened wing:
            #   call: above the entry midpoint AND not beyond the call short
            #         (mid <= strike_high, strike_low <= short_call)
            #   put:  below the entry midpoint AND not beyond the put short
            #         (strike_low <= mid, strike_high >= short_put)
            # A wall straddling between spot and the short (the genuine
            # breach case) is retained; opposite-wing walls are excluded.
            sc = float(entry.short_call_strike or 0.0)
            sp = float(entry.short_put_strike or 0.0)
            if sc > 0 and sp > 0:
                mid = (sc + sp) / 2.0
            else:
                # One-sided entry (the other wing was skipped): no opposing
                # short to form a midpoint — fall back to live spot as the
                # entry-spot proxy for the wing boundary.
                mid = spot
            if side == "call":
                ref = entry.short_call_strike
                relevant = tuple(
                    c for c in walls if c.strike_high >= mid and c.strike_low <= ref
                )
            else:
                ref = entry.short_put_strike
                relevant = tuple(
                    c for c in walls if c.strike_low <= mid and c.strike_high >= ref
                )
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
                # A1: advisory mode — log the would-close for the head-to-head
                # record but do NOT act; the credit+buffer (L-C2 backstop) is the
                # acting primary. Skip to the next side so this returns no action
                # and _check_stop_losses falls through to super()._check_stop_losses().
                if getattr(self, "brandon_breach_exit_advisory", False):
                    logger.warning(
                        "BRANDON-BREACH E#%s %s: ADVISORY would-close (breach confirmed) "
                        "— NOT acting; credit+buffer stop is primary. %s",
                        entry.entry_number, side, decision.reason,
                    )
                    continue
                # Cooldown: a recent 0-leg close on this breached side means we
                # already tried and it didn't transact — don't re-fire (and
                # re-alert) the close every ~11s tick. The wing stays alive and
                # monitored; the L-C2 credit+buffer backstop still covers it.
                if self._brandon_close_in_cooldown(entry, side):
                    continue
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
                # of breach. The total_realized_pnl subtraction is DRY-RUN
                # ONLY — in live mode _close_entry_early already subtracts the
                # real fill-based close cost, so subtracting the pre-close mark
                # here too would double-count it. actual_*_stop_debit is set in
                # both modes for journaling.
                # FAIL-CLOSED (06-04 audit): only mark a side stopped/pivot-closed
                # if _close_entry_early ACTUALLY closed it (*_side_expired set iff
                # >=1 leg closed). A 0-leg close on a breached side means the
                # losing wing is STILL OPEN — never silently mark it done (that
                # abandons a live, losing position). Keep it alive to retry + alert.
                call_did_close = call_alive_pre and getattr(entry, "call_side_expired", False)
                put_did_close = put_alive_pre and getattr(entry, "put_side_expired", False)
                if call_did_close:
                    self._brandon_clear_close_failed(entry, "call")
                    entry.call_side_stopped = True
                    entry.actual_call_stop_debit = close_cost_call_real
                    if self.dry_run:
                        self._book_realized_pnl(-close_cost_call_real, entry)
                    setattr(entry, "call_side_pivot_closed", True)
                elif call_alive_pre:
                    logger.critical(
                        "BRANDON-BREACH E#%s: call close returned 0 legs but the BREACHED call wing "
                        "is STILL OPEN — NOT marking stopped, will retry (orphaned losing position!).",
                        entry.entry_number,
                    )
                    self._brandon_alert_orphan_close(entry, "call", "BREACH")
                if put_did_close:
                    self._brandon_clear_close_failed(entry, "put")
                    entry.put_side_stopped = True
                    entry.actual_put_stop_debit = close_cost_put_real
                    if self.dry_run:
                        self._book_realized_pnl(-close_cost_put_real, entry)
                    setattr(entry, "put_side_pivot_closed", True)
                elif put_alive_pre:
                    logger.critical(
                        "BRANDON-BREACH E#%s: put close returned 0 legs but the BREACHED put wing "
                        "is STILL OPEN — NOT marking stopped, will retry (orphaned losing position!).",
                        entry.entry_number,
                    )
                    self._brandon_alert_orphan_close(entry, "put", "BREACH")
                # Tag close type for the dashboard. BREACH = Brandon GEX wall
                # breach, distinct from TP and from a HYDRA credit+buffer stop.
                if call_did_close or put_did_close:
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
        """Early-warning that the HYDRA credit+buffer backstop is arming.

        The check mirrors HydraStrategy._check_stop_with_confirmation's core
        condition (spread_value >= side_stop) but does not itself close — it is
        the head-to-head comparison datapoint vs Brandon's GEX breach. First
        fire per side per day is announced via Telegram; subsequent ticks of the
        same side are silent. Since L-C2 (2026-06-10) the credit+buffer is no
        longer a never-acting shadow: ~10s after this heads-up, once MKT-046
        confirms, super()._check_stop_losses() (run in _check_stop_losses step 3)
        ACTS as the backstop and closes the side.
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
                f"BRANDON-HYDRA-BACKSTOP E#{entry.entry_number} {side}: "
                f"credit+buffer level breached — "
                f"SV ${sv:.0f} >= trigger ${stop:.0f}, expected loss ${expected_loss:.0f}. "
                f"GEX breach is primary; the credit+buffer backstop ACTS if this "
                f"persists ~10s (MKT-046)."
            )
            logger.warning(msg)
            self._brandon_send_telegram(
                msg,
                title=f"HYDRA backstop arming E#{entry.entry_number} {side}",
                priority_name="MEDIUM",
                alert_type_name="STOP_LOSS",
            )

    def _brandon_check_pctwidth_shadow_stop(self, entry) -> None:
        """SHADOW (logs only, never acts): what the %-of-width stop WOULD do.

        Enabled via narrow_spread_stop.shadow=true (with .enabled=false so it
        does NOT override the acting credit+buffer stop). LOG ONLY — pull the
        'A2-SHADOW' / 'A2-SHADOW-CONFIRMED' lines (and the spread_snapshots table)
        for the analysis (bots/hydra/stop_shadow.py).

        TWO shadow variants run head-to-head against the acting credit+buffer stop:
        - RAW (A2-SHADOW): "would fire" the first tick a side's cost-to-close
          (spread_value) crosses pct×width×100×contracts. Once per side per day.
        - CONFIRMED (A2-SHADOW-CONFIRMED, 2026-06-25): only "would fire" if that
          breach PERSISTS narrow_spread_stop_confirm_seconds (MKT-046-style) — a
          spike that drops back below the trigger first is a "whipsaw avoided" and
          does NOT fire. This is the variant that should keep the trend-day tail-
          capping WITHOUT the premature-stop cost that made the RAW %-width stop
          net-negative over C's history (see docs analysis). Once per side per day.
        """
        if not getattr(self, "narrow_spread_stop_shadow", False):
            return
        from shared.market_hours import get_us_market_time
        pct = getattr(self, "narrow_spread_stop_pct", 0.40)
        contracts = getattr(self, "contracts_per_entry", 1) or 1
        confirm_s = getattr(self, "narrow_spread_stop_confirm_seconds", 10.0)
        now = get_us_market_time()
        for side in ("call", "put"):
            if not self._brandon_side_alive(entry, side):
                continue
            if side == "call":
                sv = entry.call_spread_value
                width = (entry.long_call_strike or 0) - (entry.short_call_strike or 0)
                acting = entry.call_side_stop
            else:
                sv = entry.put_spread_value
                width = (entry.short_put_strike or 0) - (entry.long_put_strike or 0)
                acting = entry.put_side_stop
            if not width or width <= 0:
                continue
            shadow_trigger = pct * width * 100 * contracts
            if shadow_trigger <= 0:
                continue
            key = (entry.entry_number, side)

            if sv < shadow_trigger:
                # Below the trigger: a pending CONFIRMED breach recovered before
                # the confirm window → whipsaw avoided (the case the confirmed
                # variant exists to filter). Log it once, then clear the timer.
                ba = self._brandon_pctwidth_breach_at.pop(key, None)
                if ba is not None and key not in self._brandon_pctwidth_confirmed_fired:
                    logger.info(
                        "A2-SHADOW-CONFIRMED E#%s %s: breach recovered after %.0fs "
                        "(< %.0fs confirm) — whipsaw avoided, would NOT have fired.",
                        entry.entry_number, side,
                        (now - ba).total_seconds(), confirm_s,
                    )
                continue

            # At/above the %-width trigger.
            earlier = acting and shadow_trigger < acting
            # RAW: log once on the first crossing.
            if key not in self._brandon_pctwidth_shadow_fired:
                self._brandon_pctwidth_shadow_fired.add(key)
                logger.info(
                    "A2-SHADOW E#%s %s: %%-of-width stop WOULD fire — SV $%.0f >= "
                    "%.0f%%×%.0fpt×%dc trigger $%.0f (acting credit+buffer trigger $%.0f, "
                    "%s). SHADOW ONLY — not acting.",
                    entry.entry_number, side, sv, pct * 100, width, contracts, shadow_trigger,
                    acting or 0.0,
                    "%-width is TIGHTER" if earlier else "credit+buffer is tighter/equal",
                )
            # CONFIRMED: fire only after the breach persists confirm_s.
            if key not in self._brandon_pctwidth_confirmed_fired:
                ba = self._brandon_pctwidth_breach_at.get(key)
                if ba is None:
                    self._brandon_pctwidth_breach_at[key] = now
                elif (now - ba).total_seconds() >= confirm_s:
                    self._brandon_pctwidth_confirmed_fired.add(key)
                    logger.info(
                        "A2-SHADOW-CONFIRMED E#%s %s: %%-of-width stop WOULD fire "
                        "(breach held %.0fs >= %.0fs confirm) — SV $%.0f >= trigger "
                        "$%.0f (acting credit+buffer trigger $%.0f). SHADOW ONLY — "
                        "not acting.",
                        entry.entry_number, side, (now - ba).total_seconds(), confirm_s,
                        sv, shadow_trigger, acting or 0.0,
                    )

    def _brandon_alert_gex_fallback(self) -> None:
        """L-C1: announce (once per ET day) that GEX/Polygon is unavailable so
        HYDRA's credit+buffer stop is now the LIVE-acting fallback.

        Positions ARE protected by the fallback — this is operational awareness
        that the PRIMARY GEX breach stop is down (no Polygon key, fetch failure,
        failure-cooldown, empty profile, or breach-exit disabled). Restoring the
        GEX feed re-arms the primary on the next tick. HIGH (not CRITICAL): the
        IC is still stopped, so it is degraded, not unprotected.
        """
        today = self._brandon_today_date()
        if getattr(self, "_brandon_gex_fallback_alert_date", None) == today:
            return
        self._brandon_gex_fallback_alert_date = today
        msg = (
            "Brandon GEX/Polygon UNAVAILABLE — primary GEX breach stop is DOWN. "
            "HYDRA credit+buffer stop is now the LIVE-acting fallback, so open "
            "positions ARE protected. Restore POLYGON_API_KEY / the GEX feed to "
            "re-arm the primary stop."
        )
        logger.warning("BRANDON-GEX-FALLBACK active: %s", msg)
        try:
            self._brandon_send_telegram(
                msg,
                title="Brandon GEX DOWN — HYDRA fallback stop ACTIVE",
                priority_name="HIGH",
                alert_type_name="DATA_QUALITY",
            )
        except Exception as exc:
            logger.debug("BRANDON-GEX-FALLBACK alert send failed (non-fatal): %s", exc)

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
        `_place_option_order` against IBKR (through the shared broker). IBKR
        has no per-leg position id, so the conid (uic) identifies the leg;
        the DRY_OVERLAY_* placeholders are not replaced by a broker-issued
        position id. Hedges are held to expiry —
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

        # DRY-RUN: synthetic legs with a Black-Scholes-estimated fill (unchanged).
        if self.dry_run:
            hedge_legs: list[HedgeLeg] = []
            _half_spread = float(getattr(self, "brandon_overlay_fill_spread", 0.0))
            for i, leg in enumerate(proposal.legs):
                fill_price = hedge_position.estimate_fill_price(
                    contract_type=leg.contract_type,
                    strike=leg.strike,
                    spot=spot,
                    t_years=t_years,
                )
                # Cross the spread so the modeled debit reflects a real marketable
                # fill, not the BS mid: buy (long) toward the ask, sell (short)
                # toward the bid. Deflates the inflated overlay P&L (2026-07-21).
                if _half_spread:
                    fill_price = max(
                        0.0,
                        fill_price + _half_spread if leg.side == "long"
                        else fill_price - _half_spread,
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
            return

        # LIVE wiring (L-H6). Previously this pre-built synthetic-id legs +
        # saved state BEFORE placement, then placed each leg and DISCARDED the
        # return — so the real overlay conids were never tracked
        # (_expected_position_quantities blind → spurious POS-003 mismatch) and
        # settlement priced the hedge off a model estimate, not the real fill.
        # Now we place first, capture the REAL conid + fill per leg, track ONLY
        # legs that actually filled (with the native-type conid so
        # reconciliation matches), save state AFTER, and surface any partial
        # failure with a CRITICAL alert instead of swallowing it.
        from bots.hydra.order_types import BuySell
        expiry = self._get_todays_expiry() if hasattr(self, "_get_todays_expiry") else None
        if not expiry:
            logger.error(
                "BRANDON-OVERLAY E#%s: could not determine expiry — skipping placement",
                entry.entry_number,
            )
            self._brandon_alert_overlay_partial(
                entry, proposal, placed=0,
                expected=sum(int(l.quantity) for l in proposal.legs),
            )
            return

        # Each overlay leg's `quantity` is ALREADY scaled to contracts_per_entry
        # (a butterfly body is 2×contracts, a debit-spread leg is 1×contracts).
        # Place each leg ONCE at its full quantity — chunked only to respect
        # max_contracts_per_order — via the quantity-aware _place_option_order.
        #
        # BUG FIX (2026-07-21): the prior code looped `for q in range(leg.quantity)`
        # and each _place_option_order call placed contracts_per_entry contracts,
        # so it placed leg.quantity × contracts_per_entry — a contracts_per_entry-
        # fold OVER-placement (a 40-contract butterfly attempted 400). The
        # max_contracts_per_underlying cap then truncated it mid-structure into a
        # naked short with no unwind. This DID fire once live — on C, 2026-06-10
        # (placed 98 vs 14 contracts) — after which C's overlays were disabled as
        # the mitigation pending "the sizing fix" (see config _comment_disabled).
        # This is that fix; it has not recurred since because C stayed overlay-OFF
        # and B is dry-run.
        filled_legs: list[HedgeLeg] = []
        expected_contracts = sum(int(l.quantity) for l in proposal.legs)
        placed_contracts = 0
        chunk_cap = max(1, int(getattr(self, "max_contracts_per_order", 15)))
        for i, leg in enumerate(proposal.legs):
            buy_sell = BuySell.BUY if leg.side == "long" else BuySell.SELL
            put_call = "Call" if leg.contract_type == "call" else "Put"
            external_ref = f"OVERLAY_{entry.entry_number}_{proposal.threatened_side}_{i}"
            leg_filled_qty = 0
            leg_conid = None
            weighted_fill = 0.0
            priced_qty = 0   # contracts whose chunk reported a real fill price
            remaining = int(leg.quantity)
            chunk_idx = 0
            while remaining > 0:
                chunk = min(remaining, chunk_cap)
                res = None
                try:
                    res = self._place_option_order(
                        strike=leg.strike,
                        put_call=put_call,
                        buy_sell=buy_sell,
                        expiry=expiry,
                        external_ref=f"{external_ref}_{chunk_idx}",
                        quantity=chunk,
                    )
                except Exception as exc:
                    logger.error(
                        "BRANDON-OVERLAY E#%s leg %s %s %.0f x%d failed: %s",
                        entry.entry_number, leg.side, leg.contract_type,
                        leg.strike, chunk, exc,
                    )
                # _place_option_order returns non-None ONLY when the full chunk
                # filled (it flattens any sub-partial itself), so a truthy result
                # means exactly `chunk` contracts are on.
                if res and res.get("uic"):
                    leg_filled_qty += chunk
                    leg_conid = res.get("uic")  # raw native conid — matches IC uic type
                    fp = res.get("fill_price")
                    if fp:
                        weighted_fill += float(fp) * chunk
                        priced_qty += chunk
                remaining -= chunk
                chunk_idx += 1
            placed_contracts += leg_filled_qty
            if leg_filled_qty > 0:
                # Average over PRICED chunks only (divisor == numerator's set) so
                # a mixed priced/unpriced multi-chunk leg can't understate the fill.
                leg_fill_price = (
                    weighted_fill / priced_qty if priced_qty > 0 else 0.0
                )
                # Fall back to the BS estimate ONLY if the broker reported no
                # fill price, so settlement still has a non-zero basis.
                if leg_fill_price <= 0:
                    leg_fill_price = hedge_position.estimate_fill_price(
                        contract_type=leg.contract_type,
                        strike=leg.strike,
                        spot=spot,
                        t_years=t_years,
                    )
                filled_legs.append(HedgeLeg(
                    entry_number=entry.entry_number,
                    side=leg.side,
                    contract_type=leg.contract_type,
                    strike=leg.strike,
                    quantity=leg_filled_qty,
                    fill_price=leg_fill_price,
                    position_id=str(leg_conid),
                    structure=proposal.structure.value,
                    threatened_side=proposal.threatened_side,
                    placed_at=placed_at,
                    conid=leg_conid,
                ))

        # ATOMICITY (2026-07-21): a defensive overlay that did NOT fully fill
        # every leg is worse than no overlay — a filled short wing without its
        # protective long is a naked 0DTE short. Unwind every filled leg
        # (opposite MARKET) rather than track a partial structure, then surface
        # it CRITICAL. Unwound legs are deliberately not added to the hedge set.
        if placed_contracts < expected_contracts:
            logger.critical(
                "BRANDON-OVERLAY E#%s %s: PARTIAL fill %d/%d contracts — "
                "unwinding all filled legs (no partial hedge / naked short)",
                entry.entry_number, proposal.threatened_side,
                placed_contracts, expected_contracts,
            )
            self._brandon_unwind_overlay_legs(filled_legs, expiry)
            self._brandon_alert_overlay_partial(
                entry, proposal, placed=placed_contracts, expected=expected_contracts,
            )
            return

        # Fully filled — track for reconciliation + settlement, persist state.
        if filled_legs:
            self._brandon_hedge_legs.setdefault(entry.entry_number, []).extend(filled_legs)
            self._brandon_save_hedge_state()

    def _brandon_unwind_overlay_legs(self, filled_legs, expiry) -> None:
        """Flatten every filled overlay leg (opposite MARKET) so a partially-
        filled overlay never strands a naked short or a stray long.

        Reuses the IC path's `_flatten_accumulated_partial` (opposite-side
        MARKET close + orphan-on-failure). Legs unwound here are intentionally
        NOT added to `self._brandon_hedge_legs` — they're being closed, so they
        must never settle. A failed flatten orphans the conid + fires CRITICAL
        (inside `_flatten_accumulated_partial`), and because the leg was never
        tracked, POS-003 reconciliation surfaces the residual for manual review.
        """
        for hl in filled_legs:
            conid = getattr(hl, "conid", None)
            if not conid or int(hl.quantity) <= 0:
                continue
            orig_side = "BUY" if hl.side == "long" else "SELL"
            # DELIBERATE asymmetry vs the placement path: the flatten is placed
            # as ONE MARKET order at the full leg quantity, NOT chunked to
            # max_contracts_per_order and NOT run through _validate_order_size.
            # An emergency close must never be rejected/throttled by a
            # self-imposed entry cap (that would strand the naked short); IBKR's
            # real per-order limit is far above an overlay leg (<= 2x
            # contracts_per_entry). A failed/partial flatten still fails CLOSED —
            # the leg was never tracked, so it surfaces as a POS-003 orphan.
            self._flatten_accumulated_partial(
                conid, orig_side, int(hl.quantity),
                f"OVERLAY_UNWIND_{hl.entry_number}_{hl.threatened_side}"
                f"_{int(hl.strike)}_{hl.contract_type[0]}",
                "ovunwind",
                f"overlay {hl.side} {hl.contract_type} {hl.strike:.0f}",
            )

    def _brandon_alert_overlay_partial(self, entry, proposal, *, placed: int, expected: int) -> None:
        """L-H6: a LIVE overlay that did not fully fill is UNWOUND (its filled
        legs flattened) rather than left as an incomplete hedge — a partial
        defensive structure can be a naked 0DTE short. Surface it CRITICAL so
        the operator knows the intended protection never went on (and can
        confirm the unwind flattened cleanly)."""
        msg = (
            f"BRANDON-OVERLAY E#{entry.entry_number} {proposal.threatened_side}: "
            f"PARTIAL fill — only {placed}/{expected} contracts filled. All "
            f"filled legs have been UNWOUND (flattened); no overlay is on. "
            f"Verify the flatten completed cleanly (check for orphaned orders)."
        )
        logger.critical(msg)
        try:
            self._brandon_send_telegram(
                msg,
                title=f"Brandon overlay PARTIAL E#{entry.entry_number}",
                priority_name="CRITICAL",
                alert_type_name="CRITICAL_INTERVENTION",
            )
        except Exception as exc:
            logger.debug("BRANDON-OVERLAY partial alert send failed (non-fatal): %s", exc)

    def _expected_position_quantities(self):
        """Brandon override (L-H6): fold LIVE overlay hedge legs into the
        expected-position set so reconciliation accounts for the real IBKR
        overlay positions. The base method only counts the 4 IC legs, so an
        untracked overlay leg would otherwise show as a spurious POS-003
        mismatch. Only legs with a real conid (live placement) are added;
        dry-run DRY_OVERLAY_* placeholders (conid=None) are skipped. The conid
        is kept in its native type to match `_actual_position_quantities`.
        """
        expected = super()._expected_position_quantities()
        for legs in self._brandon_hedge_legs.values():
            for hl in legs:
                conid = getattr(hl, "conid", None)
                if conid is None:
                    continue
                sign = 1 if hl.side == "long" else -1
                expected[conid] = expected.get(conid, 0) + sign * int(hl.quantity)
        return expected

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
            entry = next(
                (e for e in self.daily_state.entries
                 if e.entry_number == entry_number), None)
            # UNIFIED double-book guard: if this entry's overlay was already booked
            # today — via the per-day set (either path) OR the per-entry
            # overlay_pnl_booked flag — SKIP IT ENTIRELY (2026-07-21 fix). Previously
            # the loop still re-priced the legs at whatever SPX was current on a
            # restart re-run (this sweep re-runs because _brandon_hedge_settlements is
            # not persisted) and re-logged a duplicate "BRANDON-OVERLAY-SETTLED" line
            # at that different SPX_close — confusing, and it corrupted any log-based
            # analysis. The booking was always guarded, so P&L never double-counted;
            # now the re-run is a true no-op: no re-price, no duplicate log/Telegram.
            # The guard set is persisted in hydra_state.json ATOMICALLY with
            # daily_state.total_realized_pnl (the same os.replace save), NOT the hedge
            # sidecar, so a crash between the sidecar write and the state save can't
            # restore the guard without the booked total (2026-07-18 review) — the
            # booking + guard flip both ride the next state save.
            if (entry_number in self._brandon_overlay_booked
                    or (entry is not None and getattr(entry, "overlay_pnl_booked", False))):
                continue
            s = hedge_position.settle_hedge(legs, spx_settle)
            if s is None:
                continue
            settlements.append(s)
            # Fold the overlay's GROSS realized P&L into the day aggregate AND the
            # specific entry (via _book_realized_pnl), ONCE. If the hedged entry is
            # missing from daily_state, book to the aggregate only so the day total
            # stays complete; the reconciliation guard then flags the rare miss.
            if entry is not None:
                self._book_realized_pnl(s.total_pnl, entry)
                entry.overlay_pnl_booked = True
                self._brandon_overlay_booked.add(entry_number)
            else:  # entry is None — book aggregate-only, guarded so a re-run can't double
                self._book_realized_pnl(s.total_pnl, None)
                self._brandon_overlay_booked.add(entry_number)
                logger.warning(
                    "BRANDON-OVERLAY E#%s: no matching daily_state entry — booked "
                    "$%.2f to the day aggregate only (per-entry attribution missed; "
                    "guarded against restart double-book).",
                    entry_number, s.total_pnl,
                )
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

    def _unattributed_overlay_pnl(self) -> float:
        """Overlay P&L booked to the day AGGREGATE only — hedges whose entry is
        absent from daily_state at settle, so ``_brandon_settle_hedges`` folded
        ``s.total_pnl`` into ``total_realized_pnl`` via ``_book_realized_pnl(None)``
        but no ``entry.realized_pnl`` carries it. The RECONCILE guard adds this back
        so ``sum(entry.realized_pnl) + this == total_realized_pnl`` (2026-07-07 B:
        an overlay loss booked aggregate-only made per-entry sum $2925 vs gross
        $392, which is EXPECTED, not drift). Derived from THIS process's settlement
        sweep (runs first, log_daily_summary line ~1831), so a re-run that
        double-books the aggregate still drifts rather than being masked."""
        settlements = getattr(self, "_brandon_hedge_settlements", None)
        if not settlements:
            return 0.0
        present = {e.entry_number for e in self.daily_state.entries}
        return sum(
            s.total_pnl for s in settlements
            if s.entry_number not in present
        )

    def log_daily_summary(self):
        # Settle hedges BEFORE the parent's daily summary so they're journaled
        # for the same day. Use the VALIDATED close (_resolve_spx_close), NOT raw
        # self.current_price: a post-close restart can leave current_price stale
        # (variant C 07-06 held 7420.22, a prior-day value, and the overlays
        # settled against it → phantom -$6,037 loss). _resolve_spx_close
        # cross-checks the on-disk recorded intraday close and rejects a >1%
        # divergent live price. The last in-session tick is a ~4 PM ET value,
        # within ~0.05% of the official SPXW PM settlement — fine for analytics.
        try:
            spx_settle = float(self._resolve_spx_close() or 0.0)
            if spx_settle > 0:
                self._brandon_settle_hedges(spx_settle)
                # Persist the overlay booking immediately (2026-07-21 fix). Without
                # this the last state save on the day was POS-004's IC-only
                # settlement, so total_realized_pnl / entry.realized_pnl / the
                # brandon_overlay_booked guard on disk stayed PRE-overlay while the
                # DB + metrics captured the full total — the dashboard's "today"
                # card (which reads the state file) then contradicted the cumulative
                # (e.g. +$1,795 card vs +$11,852 cumulative). One atomic os.replace;
                # the overlay double-book guard rides this same save (see
                # _brandon_settle_hedges' persistence note).
                self._save_state_to_disk()
        except Exception as exc:
            logger.error("BRANDON-OVERLAY settlement failed (non-fatal): %s", exc)
        super().log_daily_summary()

    # ------------------------------------------------------------------
    # GEX profile cache (3-min TTL, 60s failure cooldown, cross-variant
    # shared via filesystem + per-variant in-memory fast path)
    # ------------------------------------------------------------------

    def _brandon_get_gex_profile(
        self, expiry_date, *, force_refresh: bool = False
    ) -> Optional[GEXProfile]:
        """Return the current GEX profile. Used by strike adjuster, breach
        exit, and the delta-target picker.

        Cache layers (in order):
        1. Shared filesystem cache (cross-variant) — read first, so B and C
           see the same chain snapshot. Persisted by whichever variant most
           recently fetched.
        2. Per-variant in-memory cache — fast path when the shared file
           system is unavailable (defensive — should rarely apply).
        3. Fresh Polygon fetch under fetch_lock(), serializing concurrent
           refreshes across variant processes. The lock holder writes the
           result to the shared cache; the lock waiter re-checks the cache
           on entry and reuses the holder's just-written profile instead of
           fetching again.

        force_refresh=True bypasses layers (1) and (2) and forces a Polygon
        round trip. Used at strike-selection time so each entry decision
        keys off a chain ≤ a few seconds old, regardless of the background
        TTL. Other call sites (breach-exit ticks, overlay checks) use the
        default and stay on the TTL cache to limit API load.
        """
        if not self.brandon_gex_enabled:
            return None
        now = datetime.now(timezone.utc)

        if (
            self._brandon_gex_failure_at is not None
            and (now - self._brandon_gex_failure_at).total_seconds() < _GEX_FAILURE_COOLDOWN
        ):
            return self._brandon_gex_profile  # honor cooldown — return stale or None

        # Layer 1: shared filesystem cache (cross-variant)
        if not force_refresh:
            shared = gex_shared_cache.load_shared_profile(
                underlying=self.brandon_polygon_underlying,
                expiry=expiry_date,
                max_age_seconds=_GEX_REFRESH_SECONDS,
            )
            if shared is not None:
                # Sync in-memory fast path so subsequent reads avoid the
                # filesystem round-trip during the TTL window.
                self._brandon_gex_profile = shared
                self._brandon_gex_profile_fetched_at = now
                return shared

            # Layer 2: in-memory fast path (defensive fallback if shared FS
            # is unwritable / unreadable — still better than re-fetching).
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

        # Layer 3: fresh fetch under cross-variant lock. The lock makes two
        # variants entering the same scheduled slot reuse one Polygon fetch
        # instead of issuing two — and gives them identical inputs so their
        # strike decisions can't diverge from chain-snapshot noise.
        with gex_shared_cache.fetch_lock():
            # After acquiring the lock, check the shared cache once more — a
            # sibling variant may have just refreshed it.
            #  • default path: accept anything within the normal TTL.
            #  • force_refresh path: accept ONLY a VERY-recent sibling write
            #    (≤ _GEX_FORCE_REFRESH_SIBLING_WINDOW_S). This is the
            #    multi-variant contention fix (2026-06-08 forensic H): without
            #    it each variant entering the same slot ran its own serial
            #    Polygon fetch under the lock (~10s each). A sibling's fetch a
            #    few seconds old is plenty fresh for an entry decision, so reuse
            #    it instead of re-fetching — collapsing N serial fetches into 1
            #    fetch + (N-1) cache reads.
            recheck_max_age = (
                _GEX_FORCE_REFRESH_SIBLING_WINDOW_S if force_refresh
                else _GEX_REFRESH_SECONDS
            )
            shared = gex_shared_cache.load_shared_profile(
                underlying=self.brandon_polygon_underlying,
                expiry=expiry_date,
                max_age_seconds=recheck_max_age,
            )
            if shared is not None:
                self._brandon_gex_profile = shared
                self._brandon_gex_profile_fetched_at = now
                logger.info(
                    "Brandon GEX: reusing sibling variant's just-fetched "
                    "profile from shared cache (force_refresh=%s, age<=%ds)",
                    force_refresh, recheck_max_age,
                )
                return shared

            try:
                # 2-pass fetch: chain endpoint for OI (Greeks-stripped on
                # Starter), then per-contract endpoint for Greeks/IV on the
                # most liquid strikes near spot.
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

            # Publish for sibling variants. Failure is silent — the caller
            # still got its profile in-process; the sibling will fetch its
            # own next time around.
            gex_shared_cache.save_shared_profile(
                profile, underlying=self.brandon_polygon_underlying
            )

            # Surface chain coverage so a sudden gap (e.g., Polygon dropping
            # Greeks on most strikes) shows up in the journal. Normal: dropped
            # few. If this number spikes, GEX cluster strength is being
            # underestimated.
            chain_total = len(contracts)
            with_greeks_or_iv = sum(
                1 for c in contracts
                if (c.get("greeks") or {}).get("gamma") is not None
                or c.get("implied_volatility") is not None
            )
            contributed = len(profile.strikes)
            dropped = chain_total - contributed
            logger.info(
                "Brandon GEX profile refreshed (force=%s): spot=%.2f, %d strikes contributed, "
                "%d positive / %d negative clusters; chain=%d, hydrated_with_greeks_or_iv=%d, dropped=%d",
                force_refresh,
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
        """Return the path to the variant's hedge_legs JSON sidecar, under the
        VARIANT-AWARE data dir (``DATA_DIR`` = data/variant_<id>/ when
        HYDRA_VARIANT_ID is set, else the base data/).

        2026-07-07 FIX: this previously used ``_PROJECT_DATA_DIR`` (the SHARED base
        data/ dir), so Brandon variants B and C BOTH wrote data/brandon_hedge_legs.json
        and clobbered each other. On 07-06 variant C restarted and loaded variant B's
        6 overlays from that shared file (the date matched), settling them onto C's
        day total — the "no matching daily_state entry" E3/E4/E6/E7 orphans that,
        together with the stale-SPX settle, produced C's phantom -$6,037. ``DATA_DIR``
        is a module constant derived from the HYDRA_VARIANT_ID env var, so this is
        still safe to call pre-super() (no dependency on self.state_file)."""
        try:
            from bots.hydra.strategy import DATA_DIR
            return os.path.join(DATA_DIR, "brandon_hedge_legs.json")
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
                    # L-H6: keep the real conid RAW (no str coercion) so an int
                    # conid round-trips as int and still matches reconciliation.
                    conid=d.get("conid"),
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
                            "conid": l.conid,  # L-H6: real broker conid (live), raw type
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

    # Space between retries of a TP/BREACH close that transacted 0 legs.
    _BRANDON_FAILED_CLOSE_COOLDOWN_S = 90.0

    def _brandon_failed_close_store(self) -> dict:
        """The (entry, side) → last-failed-close timestamp map. Lazily created
        so partial constructions (tests build via __new__, bypassing __init__)
        and any pre-init call path are safe."""
        store = getattr(self, "_brandon_failed_close_at", None)
        if store is None:
            store = {}
            self._brandon_failed_close_at = store
        return store

    def _brandon_close_in_cooldown(self, entry, side: str) -> bool:
        """True if a TP/BREACH close for (entry, side) transacted 0 legs within
        the last _BRANDON_FAILED_CLOSE_COOLDOWN_S — caller should skip re-firing
        the close this tick. The side stays alive and monitored; we only avoid
        hammering the broker (and re-alerting) every ~11s on a doomed close."""
        last = self._brandon_failed_close_store().get((getattr(entry, "entry_number", -1), side))
        if last is None:
            return False
        try:
            elapsed = (self._brandon_now_et() - last).total_seconds()
        except Exception:
            return False
        return elapsed < self._BRANDON_FAILED_CLOSE_COOLDOWN_S

    def _brandon_mark_close_failed(self, entry, side: str) -> None:
        """Record that a TP/BREACH close for (entry, side) transacted 0 legs."""
        self._brandon_failed_close_store()[
            (getattr(entry, "entry_number", -1), side)
        ] = self._brandon_now_et()

    def _brandon_clear_close_failed(self, entry, side: str) -> None:
        """A close for (entry, side) succeeded — drop any cooldown so a future
        re-open of that key isn't spuriously throttled."""
        self._brandon_failed_close_store().pop((getattr(entry, "entry_number", -1), side), None)

    def _brandon_alert_orphan_close(self, entry, side: str, close_kind: str) -> None:
        """A Brandon close (TP/BREACH) transacted 0 legs while the side's legs
        are still OPEN at the broker — the side is kept alive to retry. Alerts
        the operator and starts a retry cooldown so the close (and this alert)
        do NOT re-fire every tick.

        Alert type/priority (changed 2026-06-11): this is an "close moved 0
        legs, retrying" OPERATIONAL warning, NOT a confirmed naked short (a true
        naked short — one leg filled, the other didn't — is detected on the B2
        path in base_strategy and stays CRITICAL/NAKED_POSITION). It was
        previously typed NAKED_POSITION/CRITICAL, which is in the send_alert
        gate's _NEVER_SUPPRESS set, so it bypassed dedup and — combined with the
        every-tick retry — flooded the inbox with hundreds of identical emails.
        Re-typed EMERGENCY_CLOSE/HIGH so it still emails the operator. Deduped to
        fire AT MOST ONCE per (entry, side, kind) per day (2026-06-12): the 90s
        retry cooldown alone still let it re-alert ~28× across an afternoon when a
        close couldn't transact (variant-C 2026-06-12), so a per-episode flag
        keeps it to a single heads-up. Wrapped so an alert failure can't break
        the loop.
        """
        # Start the retry cooldown regardless of whether the alert send works.
        self._brandon_mark_close_failed(entry, side)
        # Alert ONCE per (entry, side, kind) per day — the cooldown gates the
        # close RETRY; this gates the ALERT so a persistent 0-leg close is a
        # single heads-up, not a re-ping every cooldown window.
        key = (getattr(entry, "entry_number", "?"), side, close_kind)
        seen = getattr(self, "_brandon_orphan_alerted", None)
        if seen is None:
            seen = set()
            self._brandon_orphan_alerted = seen
        if key in seen:
            return
        seen.add(key)
        try:
            self._brandon_send_telegram(
                message=(
                    f"Entry #{getattr(entry, 'entry_number', '?')} {side} side {close_kind} close "
                    f"transacted 0 legs but the {side} legs are still OPEN at the broker. The bot "
                    f"kept the side alive and will retry in "
                    f"~{int(self._BRANDON_FAILED_CLOSE_COOLDOWN_S)}s — check for an orphaned/naked "
                    f"live position if this repeats."
                ),
                title=f"ORPHANED CLOSE — {close_kind} closed 0 legs",
                priority_name="HIGH",
                alert_type_name="EMERGENCY_CLOSE",
                details={"entry_number": getattr(entry, "entry_number", None), "side": side},
            )
        except Exception as exc:
            logger.debug("orphan-close alert failed (non-fatal): %s", exc)

    def _brandon_send_telegram(
        self,
        message: str,
        title: str = "Brandon stack",
        priority_name: str = "MEDIUM",
        alert_type_name: str = "STOP_LOSS",
        details: Optional[dict] = None,
    ) -> None:
        """Fire an AlertService alert. Maps Brandon-stack events into the
        existing CALYPSO alert pipeline (Pub/Sub → Telegram + Email).

        Defaults: alert_type=STOP_LOSS (semantically: would-fire shadow stop),
        priority=MEDIUM (Telegram only). Caller can override per call site —
        e.g., overlay placements use HIGH; a hypothetical critical breach
        would use CRITICAL.

        ``details`` is passed through to send_alert; the anti-spam gate reads
        ``entry_number`` / ``side`` from it to keep distinct real events distinct
        in its content-dedup fingerprint.
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
                details=details,
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
        self._brandon_pctwidth_shadow_fired.clear()
        self._brandon_pctwidth_breach_at.clear()
        self._brandon_pctwidth_confirmed_fired.clear()
        self._brandon_failed_close_store().clear()
        if hasattr(self, "_brandon_orphan_alerted"):
            self._brandon_orphan_alerted.clear()
        self._brandon_hedge_legs.clear()
        self._brandon_hedge_settlements = []
        self._brandon_overlay_booked.clear()  # reset the overlay double-book guard
        # Wipe yesterday's hedge sidecar so a new-day restart won't restore it.
        try:
            path = getattr(self, "_brandon_hedge_state_path", None)
            if path and os.path.exists(path):
                os.remove(path)
        except Exception as exc:
            logger.debug("BRANDON: hedge sidecar cleanup failed (non-fatal): %s", exc)
