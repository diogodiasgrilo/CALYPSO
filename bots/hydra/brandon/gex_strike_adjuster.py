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

from .gex_provider import GEXProfile


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
    """

    accel_min_pct: float = 0.10
    decel_min_pct: float = 0.05
    max_shift_pts: float = 25.0
    shift_buffer_pts: float = 5.0


def _snap(strike: float) -> float:
    return round(strike / SPX_STRIKE_INCREMENT) * SPX_STRIKE_INCREMENT


def adjust_call_strike(
    *,
    spot: float,
    proposed_short: float,
    profile: GEXProfile,
    config: AdjusterConfig = AdjusterConfig(),
) -> AdjustResult:
    """Decide whether to keep, shift, or skip the proposed call short.

    Conventions:
        - call short is ABOVE spot
        - "wing further OTM" = larger strike
        - acceleration zone bad if proposed strike sits inside it
        - deceleration wall good if it sits between spot and proposed strike
    """
    if proposed_short <= spot:
        return AdjustResult(AdjustAction.KEEP, None, "proposed short below spot — caller bug, skipping adjust")

    accel_zones = profile.negative_clusters(min_strength_pct=config.accel_min_pct)
    for c in accel_zones:
        if c.strike_low <= proposed_short <= c.strike_high:
            return AdjustResult(
                AdjustAction.SKIP,
                None,
                f"call short {proposed_short:.0f} inside accel zone "
                f"[{c.strike_low:.0f}, {c.strike_high:.0f}] (GEX {c.total_gex:.2e})",
            )

    decel_walls = profile.positive_clusters(min_strength_pct=config.decel_min_pct)
    walls_above_proposed = [c for c in decel_walls if c.strike_low > proposed_short]
    if walls_above_proposed:
        wall = min(walls_above_proposed, key=lambda c: c.strike_low)
        target = _snap(wall.strike_high + config.shift_buffer_pts)
        if target - proposed_short <= config.max_shift_pts and target > proposed_short:
            return AdjustResult(
                AdjustAction.SHIFT,
                target,
                f"capturing decel wall [{wall.strike_low:.0f}, {wall.strike_high:.0f}] "
                f"inside wings; short {proposed_short:.0f} → {target:.0f}",
            )

    return AdjustResult(AdjustAction.KEEP, None, "no actionable GEX signal on call side")


def adjust_put_strike(
    *,
    spot: float,
    proposed_short: float,
    profile: GEXProfile,
    config: AdjusterConfig = AdjusterConfig(),
) -> AdjustResult:
    """Decide whether to keep, shift, or skip the proposed put short.

    Symmetric to the call adjuster, mirrored:
        - put short is BELOW spot
        - "wing further OTM" = smaller strike
        - shift target = wall.strike_low - shift_buffer_pts
    """
    if proposed_short >= spot:
        return AdjustResult(AdjustAction.KEEP, None, "proposed short above spot — caller bug, skipping adjust")

    accel_zones = profile.negative_clusters(min_strength_pct=config.accel_min_pct)
    for c in accel_zones:
        if c.strike_low <= proposed_short <= c.strike_high:
            return AdjustResult(
                AdjustAction.SKIP,
                None,
                f"put short {proposed_short:.0f} inside accel zone "
                f"[{c.strike_low:.0f}, {c.strike_high:.0f}] (GEX {c.total_gex:.2e})",
            )

    decel_walls = profile.positive_clusters(min_strength_pct=config.decel_min_pct)
    walls_below_proposed = [c for c in decel_walls if c.strike_high < proposed_short]
    if walls_below_proposed:
        wall = max(walls_below_proposed, key=lambda c: c.strike_high)
        target = _snap(wall.strike_low - config.shift_buffer_pts)
        if proposed_short - target <= config.max_shift_pts and target < proposed_short:
            return AdjustResult(
                AdjustAction.SHIFT,
                target,
                f"capturing decel wall [{wall.strike_low:.0f}, {wall.strike_high:.0f}] "
                f"inside wings; short {proposed_short:.0f} → {target:.0f}",
            )

    return AdjustResult(AdjustAction.KEEP, None, "no actionable GEX signal on put side")
