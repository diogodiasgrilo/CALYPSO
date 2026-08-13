"""GEX-aware strike adjuster (Brandon's Trojan-Horse rule).

Takes a proposed short strike (from HYDRA's credit-gate scan) and a GEXProfile,
returns one of:

    KEEP   - leave the strike alone, GEX gives no signal worth acting on
    SHIFT  - move the wing further OTM to enclose a strong deceleration wall
             (Brandon: "I would manipulate the lower bands to move a little bit
              lower… at 6945 to be able to have these areas of deceleration
              captured within")
    SKIP   - the proposed strike sits inside an acceleration zone; don't place
             this side at all (HYDRA already supports one-sided entries)

The adjuster is symmetric for call / put with the directions reversed and snaps
output strikes to the SPX 5pt grid. All thresholds and limits are config-driven
so the rule can be tuned without code changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .gex_provider import GEXCluster, GEXProfile


SPX_STRIKE_INCREMENT = 5.0


class AdjustAction(str, Enum):
    KEEP = "keep"
    SHIFT = "shift"
    SKIP = "skip"


@dataclass(frozen=True)
class AdjustResult:
    action: AdjustAction
    new_strike: Optional[float]
    reason: str


@dataclass(frozen=True)
class AdjusterConfig:
    """Knobs for the adjuster.

    accel_min_pct: minimum |GEX| (as fraction of total |GEX|) for a cluster to
        be considered an acceleration zone strong enough to skip a side.
    decel_min_pct: minimum |GEX| fraction for a deceleration wall to be
        considered worth shifting toward.
    max_shift_pts: cap on how far the strike may be shifted from the proposed
        strike. Prevents giving up too much credit chasing a weak wall.
    shift_buffer_pts: how many points beyond the wall's far edge to place the
        new short, so the wall sits cleanly inside the wings.
    accel_peak_locality_pts: SKIP fires only when the proposed short is within
        this distance of the cluster's |GEX| peak strike. Without this gate,
        the SpotGamma sign convention (calls negated, puts positive) collapses
        the entire call wing into ONE giant negative cluster and the entire
        put wing into ONE giant positive cluster on SPX 0DTE — meaning every
        call short, by definition above spot, lands inside the call-wing
        "accel" cluster and gets SKIP'd. The 5/4-5/12 data showed B/C at 77%
        and 89% put-only as a direct result. Peak-locality recovers Brandon's
        actual "wall is a localized feature, not a hemisphere" intent: a
        cluster is only a real accel zone within ±N pts of its peak strike.
    accel_peak_persistence_enabled / accel_peak_persistence_tolerance_pts
        (2026-08-12): `peak_strike` is recomputed from scratch on every GEX
        fetch with no smoothing — a single noisy OI/GEX snapshot can veto a
        whole entry. When enabled, a SKIP only fires if the caller's
        `prior_profile` ALSO shows a covering accel cluster whose peak is
        within `accel_peak_persistence_tolerance_pts` of the current peak —
        i.e. two independent reads must agree before the wall is trusted.
        Forensic audit (Aug 10-12 3-day dead streak on B/C): stable peaks
        correctly predicted a real pin (Aug 10); drifting peaks (15-40pt
        between entry-slot reads) did not (Aug 11, early Aug 12) — SPX moved
        away and never returned. Defaults to False/off — this is a live
        entry-decision behavior change and ships inert; see
        bots/hydra/__init__.py version history for the rollout plan.
    """

    accel_min_pct: float = 0.10
    decel_min_pct: float = 0.05
    max_shift_pts: float = 25.0
    shift_buffer_pts: float = 5.0
    accel_peak_locality_pts: float = 25.0
    accel_peak_persistence_enabled: bool = False
    accel_peak_persistence_tolerance_pts: float = 10.0
    # Strike-grid increment for snapping a shifted strike (modularity-audit
    # item 2). Default 5.0 = SPX; a different-increment underlying passes its own.
    strike_increment: float = SPX_STRIKE_INCREMENT


def _snap(strike: float, increment: float = SPX_STRIKE_INCREMENT) -> float:
    return round(strike / increment) * increment


def _cluster_covering(clusters: tuple[GEXCluster, ...], strike: float) -> Optional[GEXCluster]:
    """First cluster (of the given sign-filtered set) whose range covers `strike`.

    Clusters carry no identity across profile reads (rebuilt fresh every
    call) — matching by strike-range coverage is the only meaningful way to
    ask "was there a comparable accel zone here last time".
    """
    for c in clusters:
        if c.strike_low <= strike <= c.strike_high:
            return c
    return None


def adjust_call_strike(
    *,
    spot: float,
    proposed_short: float,
    profile: GEXProfile,
    config: AdjusterConfig = AdjusterConfig(),
    prior_profile: Optional[GEXProfile] = None,
    force_unconfirmed: bool = False,
) -> AdjustResult:
    """Decide whether to keep, shift, or skip the proposed call short.

    Conventions:
        - call short is ABOVE spot
        - "wing further OTM" = larger strike
        - acceleration zone bad if proposed strike sits inside it
        - deceleration wall good if it sits between spot and proposed strike

    `prior_profile`: the previous independent GEX read (see AdjusterConfig's
    accel_peak_persistence_enabled docstring). When persistence is enabled
    and a prior read is supplied, an in-locality accel zone only triggers
    SKIP if `prior_profile` shows a matching accel zone at roughly the same
    peak — otherwise the decision falls through to the SHIFT/KEEP checks
    below, exactly as if this accel zone weren't in locality range at all.

    `force_unconfirmed`: set by the caller (2026-08-12 round-2 review fix)
    when the only "prior" available IS this exact read (a same-profile
    entry-retry, or a stale-fallback branch reusing the last cached
    profile) — i.e. no NEW independent confirmation has happened since the
    last evaluation. This is deliberately distinct from `prior_profile=None`
    (which means "no prior has EVER been read" — the intentional, tested
    legacy-SKIP path for the genuinely first evaluation of the day).
    Collapsing the retry case into `prior_profile=None` would silently
    revert some retried entries to unconditional-SKIP while looking
    identical in the logs to a genuine persistence-gate decision.
    """
    if proposed_short <= spot:
        return AdjustResult(AdjustAction.KEEP, None, "proposed short below spot — caller bug, skipping adjust")

    accel_zones = profile.negative_clusters(min_strength_pct=config.accel_min_pct)
    unconfirmed_note: Optional[str] = None
    for c in accel_zones:
        if c.strike_low <= proposed_short <= c.strike_high:
            if abs(proposed_short - c.peak_strike) <= config.accel_peak_locality_pts:
                if config.accel_peak_persistence_enabled and (force_unconfirmed or prior_profile is not None):
                    if force_unconfirmed:
                        prior_c = None
                        confirmed = False
                    else:
                        prior_c = _cluster_covering(
                            prior_profile.negative_clusters(min_strength_pct=config.accel_min_pct),
                            proposed_short,
                        )
                        confirmed = (
                            prior_c is not None
                            and prior_profile.expiry == profile.expiry
                            and abs(prior_c.peak_strike - c.peak_strike) <= config.accel_peak_persistence_tolerance_pts
                        )
                    if not confirmed:
                        # Unconfirmed by the prior independent read — don't trust
                        # a single noisy snapshot enough to veto the whole entry.
                        # Fall through to the decel/SHIFT check, then KEEP — but
                        # remember why, so an operator can tell "no signal at all"
                        # apart from "signal present but not yet trusted".
                        if force_unconfirmed:
                            detail = "(re-evaluating the same GEX read as before — no new independent confirmation available yet)"
                        elif prior_c is None:
                            detail = "(no matching accel zone in prior read)"
                        elif prior_profile.expiry != profile.expiry:
                            detail = "(prior read has a different expiry — not comparable)"
                        else:
                            detail = (
                                f"(prior peak {prior_c.peak_strike:.0f}, drift "
                                f"{abs(prior_c.peak_strike - c.peak_strike):.0f}pt > "
                                f"{config.accel_peak_persistence_tolerance_pts:.0f}pt tolerance)"
                            )
                        unconfirmed_note = (
                            f"call short {proposed_short:.0f} inside accel zone (peak {c.peak_strike:.0f}) "
                            f"but UNCONFIRMED vs prior read {detail}"
                        )
                        continue
                return AdjustResult(
                    AdjustAction.SKIP,
                    None,
                    f"call short {proposed_short:.0f} within {config.accel_peak_locality_pts:.0f}pt "
                    f"of accel-zone peak {c.peak_strike:.0f} "
                    f"[zone {c.strike_low:.0f}-{c.strike_high:.0f}, GEX {c.total_gex:.2e}]",
                )

    decel_walls = profile.positive_clusters(min_strength_pct=config.decel_min_pct)
    walls_above_proposed = [c for c in decel_walls if c.strike_low > proposed_short]
    if walls_above_proposed:
        wall = min(walls_above_proposed, key=lambda c: c.strike_low)
        target = _snap(wall.strike_high + config.shift_buffer_pts, config.strike_increment)
        if target - proposed_short <= config.max_shift_pts and target > proposed_short:
            reason = (
                f"capturing decel wall [{wall.strike_low:.0f}, {wall.strike_high:.0f}] "
                f"inside wings; short {proposed_short:.0f} → {target:.0f}"
            )
            if unconfirmed_note:
                reason = f"{unconfirmed_note}; {reason}"
            return AdjustResult(AdjustAction.SHIFT, target, reason)

    return AdjustResult(AdjustAction.KEEP, None, unconfirmed_note or "no actionable GEX signal on call side")


def adjust_put_strike(
    *,
    spot: float,
    proposed_short: float,
    profile: GEXProfile,
    config: AdjusterConfig = AdjusterConfig(),
    prior_profile: Optional[GEXProfile] = None,
    force_unconfirmed: bool = False,
) -> AdjustResult:
    """Decide whether to keep, shift, or skip the proposed put short.

    Symmetric to the call adjuster, mirrored:
        - put short is BELOW spot
        - "wing further OTM" = smaller strike
        - shift target = wall.strike_low - shift_buffer_pts

    `prior_profile` / `force_unconfirmed`: see adjust_call_strike — same
    persistence-gate semantics, mirrored.
    """
    if proposed_short >= spot:
        return AdjustResult(AdjustAction.KEEP, None, "proposed short above spot — caller bug, skipping adjust")

    accel_zones = profile.negative_clusters(min_strength_pct=config.accel_min_pct)
    unconfirmed_note: Optional[str] = None
    for c in accel_zones:
        if c.strike_low <= proposed_short <= c.strike_high:
            if abs(proposed_short - c.peak_strike) <= config.accel_peak_locality_pts:
                if config.accel_peak_persistence_enabled and (force_unconfirmed or prior_profile is not None):
                    if force_unconfirmed:
                        prior_c = None
                        confirmed = False
                    else:
                        prior_c = _cluster_covering(
                            prior_profile.negative_clusters(min_strength_pct=config.accel_min_pct),
                            proposed_short,
                        )
                        confirmed = (
                            prior_c is not None
                            and prior_profile.expiry == profile.expiry
                            and abs(prior_c.peak_strike - c.peak_strike) <= config.accel_peak_persistence_tolerance_pts
                        )
                    if not confirmed:
                        if force_unconfirmed:
                            detail = "(re-evaluating the same GEX read as before — no new independent confirmation available yet)"
                        elif prior_c is None:
                            detail = "(no matching accel zone in prior read)"
                        elif prior_profile.expiry != profile.expiry:
                            detail = "(prior read has a different expiry — not comparable)"
                        else:
                            detail = (
                                f"(prior peak {prior_c.peak_strike:.0f}, drift "
                                f"{abs(prior_c.peak_strike - c.peak_strike):.0f}pt > "
                                f"{config.accel_peak_persistence_tolerance_pts:.0f}pt tolerance)"
                            )
                        unconfirmed_note = (
                            f"put short {proposed_short:.0f} inside accel zone (peak {c.peak_strike:.0f}) "
                            f"but UNCONFIRMED vs prior read {detail}"
                        )
                        continue
                return AdjustResult(
                    AdjustAction.SKIP,
                    None,
                    f"put short {proposed_short:.0f} within {config.accel_peak_locality_pts:.0f}pt "
                    f"of accel-zone peak {c.peak_strike:.0f} "
                    f"[zone {c.strike_low:.0f}-{c.strike_high:.0f}, GEX {c.total_gex:.2e}]",
                )

    decel_walls = profile.positive_clusters(min_strength_pct=config.decel_min_pct)
    walls_below_proposed = [c for c in decel_walls if c.strike_high < proposed_short]
    if walls_below_proposed:
        wall = max(walls_below_proposed, key=lambda c: c.strike_high)
        target = _snap(wall.strike_low - config.shift_buffer_pts, config.strike_increment)
        if proposed_short - target <= config.max_shift_pts and target < proposed_short:
            reason = (
                f"capturing decel wall [{wall.strike_low:.0f}, {wall.strike_high:.0f}] "
                f"inside wings; short {proposed_short:.0f} → {target:.0f}"
            )
            if unconfirmed_note:
                reason = f"{unconfirmed_note}; {reason}"
            return AdjustResult(AdjustAction.SHIFT, target, reason)

    return AdjustResult(AdjustAction.KEEP, None, unconfirmed_note or "no actionable GEX signal on put side")
