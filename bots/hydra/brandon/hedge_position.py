"""Hedge position tracking for the defensive overlay.

The defensive overlay places a debit-spread (mornings) or butterfly (afternoons).
For dry-run those go through this module: each leg gets a synthetic DRY_*
position id, a Black-Scholes-estimated fill price at placement time, and is
settled against SPX_close at expiry. Net hedge P&L is then surfaced via
Telegram and journal so the daily numbers actually reflect Brandon's full
strategy outcome — not just "the bot would have hedged here."

Live mode flow uses HYDRA's `_place_option_order` per leg in the strategy
override; this module is the bookkeeping layer only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional


@dataclass(frozen=True)
class HedgeLeg:
    entry_number: int
    side: str            # "long" or "short"
    contract_type: str   # "call" or "put"
    strike: float
    quantity: int
    fill_price: float    # per-contract price (dollars per share — multiply by 100 for $/contract)
    position_id: str     # DRY_OVERLAY_<entry>_<i> in dry-run, real Saxo id in live
    structure: str       # "debit_spread" or "butterfly"
    threatened_side: str # "call" or "put" — which IC side the hedge protects
    placed_at: datetime


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes_call_price(spot: float, strike: float, iv: float, t_years: float, r: float = 0.0) -> float:
    """Standard Black-Scholes call price. Returns 0.0 on degenerate inputs."""
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, spot - strike)  # intrinsic at expiry / degenerate
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    return spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)


def black_scholes_put_price(spot: float, strike: float, iv: float, t_years: float, r: float = 0.0) -> float:
    """Standard Black-Scholes put price. Returns 0.0 on degenerate inputs."""
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, strike - spot)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def estimate_fill_price(
    *,
    contract_type: str,
    strike: float,
    spot: float,
    t_years: float,
    iv: float = 0.18,
) -> float:
    """Per-contract premium estimate at placement time. Used as the synthetic
    fill in dry-run so settlement P&L = intrinsic_at_expiry − fill_price has
    the same sign and approximate magnitude as a real fill would.
    """
    if contract_type == "call":
        return black_scholes_call_price(spot=spot, strike=strike, iv=iv, t_years=t_years)
    return black_scholes_put_price(spot=spot, strike=strike, iv=iv, t_years=t_years)


def leg_intrinsic_at_expiry(leg: HedgeLeg, spx_settle: float) -> float:
    """Per-share intrinsic value of the leg at expiry."""
    if leg.contract_type == "call":
        return max(0.0, spx_settle - leg.strike)
    return max(0.0, leg.strike - spx_settle)


def leg_pnl_at_expiry(leg: HedgeLeg, spx_settle: float) -> float:
    """Total dollar P&L for this leg at expiry, accounting for direction
    and quantity. Long: +(intrinsic - fill). Short: +(fill - intrinsic).
    Multiplies by 100 (option contract multiplier) and by quantity.
    """
    intrinsic = leg_intrinsic_at_expiry(leg, spx_settle)
    if leg.side == "long":
        per_contract = intrinsic - leg.fill_price
    else:
        per_contract = leg.fill_price - intrinsic
    return per_contract * 100.0 * leg.quantity


def total_hedge_pnl(legs: Iterable[HedgeLeg], spx_settle: float) -> float:
    return sum(leg_pnl_at_expiry(l, spx_settle) for l in legs)


@dataclass(frozen=True)
class HedgeSettlement:
    """Snapshot of a single hedge's settled outcome — for journaling/Telegram."""
    entry_number: int
    threatened_side: str
    structure: str
    legs: tuple[HedgeLeg, ...]
    spx_settle: float
    total_pnl: float
    total_debit_paid: float  # sum of long fills, less sum of short fills (positive = paid out)


def settle_hedge(legs: Iterable[HedgeLeg], spx_settle: float) -> Optional[HedgeSettlement]:
    legs_t = tuple(legs)
    if not legs_t:
        return None
    total_pnl = total_hedge_pnl(legs_t, spx_settle)
    debit_paid = 0.0
    for l in legs_t:
        sign = +1.0 if l.side == "long" else -1.0
        debit_paid += sign * l.fill_price * 100.0 * l.quantity
    return HedgeSettlement(
        entry_number=legs_t[0].entry_number,
        threatened_side=legs_t[0].threatened_side,
        structure=legs_t[0].structure,
        legs=legs_t,
        spx_settle=spx_settle,
        total_pnl=total_pnl,
        total_debit_paid=debit_paid,
    )
