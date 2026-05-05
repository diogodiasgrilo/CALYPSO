"""Defensive overlay — Brandon's mid-trade hedge structure.

When SPX threatens one of the IC's short strikes intraday, place a hedge:

    Before 12:30 ET → debit spread, "directly above" the threatened
        credit spread on the threatened side. As price continues into the
        threat zone, the debit spread gains, offsetting the credit spread's
        loss. Brandon: cheap because vol is still rich early-day.

    From 12:30 ET   → butterfly, pinning the nearest positive-gamma
        cluster on the threatened side. As theta accelerates after lunch
        the butterfly's value grows quickly if SPX hovers near the pin.
        Brandon: thousands of percent gains realized at close.

The module is a pure proposer — given current state, returns either an
OverlayProposal (with legs and structure) or None. The strategy harness
turns the proposal into Saxo orders. Triggering requires both a distance
condition (SPX within `trigger_distance_pts` of the short strike) and,
when a GEXProfile is supplied, a confirming acceleration zone on the
threatened side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional

from .gex_provider import GEXCluster, GEXProfile


class OverlayStructure(str, Enum):
    DEBIT_SPREAD = "debit_spread"
    BUTTERFLY = "butterfly"


@dataclass(frozen=True)
class OverlayLeg:
    side: str          # "long" or "short"
    contract_type: str # "call" or "put"
    strike: float
    quantity: int


@dataclass(frozen=True)
class OverlayProposal:
    structure: OverlayStructure
    threatened_side: str  # "call" or "put"
    legs: tuple[OverlayLeg, ...]
    pin_strike: Optional[float]
    reason: str


@dataclass(frozen=True)
class OverlayConfig:
    """Knobs for the overlay proposer.

    trigger_distance_pts: SPX must be within this distance of the short
        strike (on the threatened side) to consider hedging.
    butterfly_cutoff: time-of-day pivot. Before this time we propose a
        debit spread; at or after, a butterfly. Default 12:30 ET per Brandon.
    butterfly_width_pts: half-width of the butterfly. The butterfly has
        wings at pin ± width.
    require_gex_confirmation: when True, the proposer requires a confirming
        acceleration zone (negative cluster) on the threatened side. When
        False, distance alone triggers.
    contracts: how many contracts in the hedge. Default 1 — matches HYDRA's
        contracts_per_entry.
    """

    trigger_distance_pts: float = 25.0
    butterfly_cutoff: time = time(12, 30)
    butterfly_width_pts: int = 10
    require_gex_confirmation: bool = True
    contracts: int = 1


SPX_INCREMENT = 5.0


def _snap(strike: float) -> float:
    return round(strike / SPX_INCREMENT) * SPX_INCREMENT


def _has_accel_zone_on_side(
    threatened_side: str,
    spot: float,
    profile: GEXProfile,
    min_strength_pct: float,
) -> bool:
    zones = profile.negative_clusters(min_strength_pct=min_strength_pct)
    if threatened_side == "call":
        return any(c.strike_low > spot for c in zones)
    return any(c.strike_high < spot for c in zones)


def _nearest_decel_wall_for_pin(
    threatened_side: str,
    spot: float,
    profile: GEXProfile,
    min_strength_pct: float,
) -> Optional[GEXCluster]:
    walls = profile.positive_clusters(min_strength_pct=min_strength_pct)
    if threatened_side == "call":
        candidates = [c for c in walls if c.strike_low > spot]
        return min(candidates, key=lambda c: c.strike_low) if candidates else None
    candidates = [c for c in walls if c.strike_high < spot]
    return max(candidates, key=lambda c: c.strike_high) if candidates else None


def evaluate_overlay(
    *,
    threatened_side: str,
    spot_now: float,
    short_strike: float,
    long_strike: float,
    now_et: datetime,
    config: OverlayConfig = OverlayConfig(),
    profile: Optional[GEXProfile] = None,
) -> Optional[OverlayProposal]:
    """Return an OverlayProposal if a hedge should be placed, else None.

    threatened_side: which side of the IC is at risk ("call" or "put").
    spot_now: current SPX.
    short_strike, long_strike: the existing credit spread on the threatened
        side (e.g., for a call IC: short=6840, long=6850).
    now_et: ET datetime — drives the debit/butterfly pivot.
    """
    if threatened_side not in ("call", "put"):
        return None

    if threatened_side == "call":
        if spot_now <= 0 or short_strike <= spot_now:
            return None
        distance = short_strike - spot_now
    else:
        if spot_now <= 0 or short_strike >= spot_now:
            return None
        distance = spot_now - short_strike
    if distance > config.trigger_distance_pts:
        return None

    if config.require_gex_confirmation:
        if profile is None:
            return None
        if not _has_accel_zone_on_side(threatened_side, spot_now, profile, min_strength_pct=0.05):
            return None

    spread_width = abs(long_strike - short_strike)
    is_morning = now_et.time() < config.butterfly_cutoff

    if is_morning or spread_width <= 0:
        return _propose_debit_spread(
            threatened_side=threatened_side,
            short_strike=short_strike,
            long_strike=long_strike,
            spread_width=spread_width if spread_width > 0 else SPX_INCREMENT * 2,
            contracts=config.contracts,
            distance=distance,
        )

    pin = _choose_butterfly_pin(threatened_side, spot_now, profile)
    return _propose_butterfly(
        threatened_side=threatened_side,
        pin_strike=pin,
        width=config.butterfly_width_pts,
        contracts=config.contracts,
        distance=distance,
    )


def _propose_debit_spread(
    *,
    threatened_side: str,
    short_strike: float,
    long_strike: float,
    spread_width: float,
    contracts: int,
    distance: float,
) -> OverlayProposal:
    contract_type = threatened_side
    width_int = int(spread_width)
    if threatened_side == "call":
        debit_long = _snap(long_strike)
        debit_short = _snap(long_strike + spread_width)
    else:
        debit_long = _snap(long_strike)
        debit_short = _snap(long_strike - spread_width)

    legs = (
        OverlayLeg("long", contract_type, debit_long, contracts),
        OverlayLeg("short", contract_type, debit_short, contracts),
    )
    return OverlayProposal(
        structure=OverlayStructure.DEBIT_SPREAD,
        threatened_side=threatened_side,
        legs=legs,
        pin_strike=None,
        reason=(
            f"morning hedge: {threatened_side} debit spread {debit_long:.0f}/{debit_short:.0f} "
            f"({width_int}pt wide) — SPX {distance:.0f}pt from short {short_strike:.0f}"
        ),
    )


def _choose_butterfly_pin(
    threatened_side: str, spot_now: float, profile: Optional[GEXProfile]
) -> float:
    if profile is not None:
        wall = _nearest_decel_wall_for_pin(threatened_side, spot_now, profile, min_strength_pct=0.05)
        if wall is not None:
            mid = (wall.strike_low + wall.strike_high) / 2.0
            return _snap(mid)
    return _snap(spot_now)


def _propose_butterfly(
    *,
    threatened_side: str,
    pin_strike: float,
    width: int,
    contracts: int,
    distance: float,
) -> OverlayProposal:
    contract_type = threatened_side
    pin = _snap(pin_strike)
    upper = _snap(pin + width)
    lower = _snap(pin - width)
    legs = (
        OverlayLeg("long", contract_type, lower, contracts),
        OverlayLeg("short", contract_type, pin, 2 * contracts),
        OverlayLeg("long", contract_type, upper, contracts),
    )
    return OverlayProposal(
        structure=OverlayStructure.BUTTERFLY,
        threatened_side=threatened_side,
        legs=legs,
        pin_strike=pin,
        reason=(
            f"afternoon hedge: {threatened_side} butterfly pin={pin:.0f} "
            f"width=±{width}pt — SPX {distance:.0f}pt from short"
        ),
    )
