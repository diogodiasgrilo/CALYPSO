"""GEX breach exit signal — shadow only for the first 4 weeks.

Brandon: "if we break through this entire cluster, I am going to have to sell …
likely a reversal or stop out." The thesis is that a sustained breach of the
outermost positive-gamma cluster on the threatened side means dealer flow has
failed to hold the level and a continuation move is likely.

This module is a pure decision function with explicit state. The strategy
harness owns the state object per-side and passes it in each tick. A breach
must persist for `confirmation_seconds` (default 90s) to be confirmed —
matches HYDRA's existing MKT-036 confirmation pattern, filtering out single-
tick noise.

For the first 4 weeks of variant B/C, the strategy logs `would_close` events
to shadow_entries without acting on them. After comparison to actual
credit+buffer stop outcomes, the rule may be promoted to a live exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .gex_provider import GEXCluster


@dataclass(frozen=True)
class BreachState:
    first_breach_at: Optional[datetime] = None
    confirmed: bool = False


@dataclass(frozen=True)
class BreachDecision:
    would_close: bool
    is_first_breach: bool
    reason: str


def evaluate_breach(
    *,
    side: str,
    spot_now: float,
    decel_walls: tuple[GEXCluster, ...],
    state: BreachState,
    now: datetime,
    confirmation_seconds: int = 90,
) -> tuple[BreachDecision, BreachState]:
    """Return (decision, next_state). Caller persists next_state to its own store.

    side: "call" or "put". Caller passes the decel walls relevant to that side
        (call → walls above entry spot; put → walls below).
    spot_now: current SPX.
    decel_walls: positive-gamma clusters captured at entry. Empty tuple = no
        breach signal possible.
    state: per-side state from the previous tick.
    now: current timestamp.
    confirmation_seconds: how long the breach must persist before would_close
        flips to True. Mirrors MKT-036's 75s default; default 90s here keeps a
        margin since GEX-breach is a coarser signal than credit-breach.
    """
    if side not in ("call", "put"):
        return BreachDecision(False, False, f"invalid side {side!r}"), state

    if state.confirmed:
        return BreachDecision(True, False, "breach previously confirmed"), state

    if not decel_walls:
        return BreachDecision(False, False, "no decel walls to breach"), state

    if side == "call":
        outermost = max(decel_walls, key=lambda c: c.strike_high)
        breached = spot_now > outermost.strike_high
        edge = outermost.strike_high
    else:
        outermost = min(decel_walls, key=lambda c: c.strike_low)
        breached = spot_now < outermost.strike_low
        edge = outermost.strike_low

    if not breached:
        if state.first_breach_at is not None:
            return (
                BreachDecision(
                    False,
                    False,
                    f"recovered: spot {spot_now:.0f} back inside wall edge {edge:.0f}",
                ),
                BreachState(),
            )
        return (
            BreachDecision(False, False, f"spot {spot_now:.0f} within wall (edge {edge:.0f})"),
            state,
        )

    if state.first_breach_at is None:
        return (
            BreachDecision(False, True, f"first breach at edge {edge:.0f}"),
            BreachState(first_breach_at=now),
        )

    elapsed = (now - state.first_breach_at).total_seconds()
    if elapsed >= confirmation_seconds:
        return (
            BreachDecision(
                True,
                False,
                f"sustained breach {elapsed:.0f}s >= {confirmation_seconds}s at edge {edge:.0f}",
            ),
            BreachState(first_breach_at=state.first_breach_at, confirmed=True),
        )
    return (
        BreachDecision(
            False,
            False,
            f"breach pending {elapsed:.0f}/{confirmation_seconds}s at edge {edge:.0f}",
        ),
        state,
    )
