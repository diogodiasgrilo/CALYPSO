"""Pure market-data computational kernels — the broker-agnostic, strategy-agnostic
core of HYDRA's read layer (modularity-audit item 3, first slice).

These are the genuinely pure pieces of the strike/chart read path: no broker, no
strategy instance state, no sibling calls — just data-shape normalization and
strike-grid snapping math. Extracting them here makes them independently testable
and reusable by any future strategy (the broker-touching `_read_*` orchestration
that calls these stays on the strategy for now — its extraction is the larger,
deliberately-scoped remainder of item 3).

HydraStrategy keeps thin delegating wrappers so every existing `self._normalize_chart_bar`
/ `self._snap_to_chain_strike` / `self._snap_long_for_spread` call site is unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def normalize_chart_bar(raw_bar: Dict[str, Any]) -> Dict[str, Any]:
    """Translate an IBKR-raw chart bar (compact o/h/l/c/v/t keys) into HYDRA's
    normalized shape: ``{open, high, low, close: float, volume, timestamp_ms: int}``.

    Defensive: missing/null/empty/malformed values degrade to 0 rather than
    raising. Callers filter out zero-priced bars downstream (ATR + EMA loops).
    """
    def _f(v) -> float:
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    def _i(v) -> int:
        if v is None or v == "":
            return 0
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    return {
        "open": _f(raw_bar.get("o")),
        "high": _f(raw_bar.get("h")),
        "low": _f(raw_bar.get("l")),
        "close": _f(raw_bar.get("c")),
        "volume": _i(raw_bar.get("v")),
        "timestamp_ms": _i(raw_bar.get("t")),
    }


def snap_to_chain_strike(
    target: float, uic_map: dict, max_snap: int = 15
) -> Tuple[Optional[float], Optional[int]]:
    """Find the nearest available strike in the option chain to ``target``.

    Chains use ~5pt intervals near ATM but switch to ~25pt far OTM, so a
    5pt-step candidate may not exist. Snaps to the nearest listed strike within
    ``max_snap`` points (default 15 = half of 25pt spacing).

    Returns ``(actual_strike, uic)`` if found within tolerance, else ``(None, None)``.
    """
    if target in uic_map:
        return target, uic_map[target]

    best_strike = None
    best_dist = max_snap + 1
    for strike in uic_map:
        dist = abs(strike - target)
        if dist < best_dist:
            best_dist = dist
            best_strike = strike

    if best_strike is not None and best_dist <= max_snap:
        return best_strike, uic_map[best_strike]

    return None, None


def snap_long_for_spread(
    short_strike: float, target_width: int, uic_map: dict, is_call: bool
) -> Tuple[Optional[float], Optional[int]]:
    """Find the long-leg strike giving a spread width closest to ``target_width``.

    The long leg must exist in the chain AND produce a width within ±15pt of
    target. Calls: long = short + width. Puts: long = short - width.

    Returns ``(actual_strike, uic)`` if found, else ``(None, None)``.
    """
    ideal_long = short_strike + target_width if is_call else short_strike - target_width

    best_strike = None
    best_dist = 16  # Max tolerance: 15pt
    for strike in uic_map:
        dist = abs(strike - ideal_long)
        if dist < best_dist:
            # Ensure spread is at least min_width (don't snap to tiny spreads)
            actual_width = abs(strike - short_strike)
            if actual_width >= target_width - 15:  # Allow slightly narrower
                best_dist = dist
                best_strike = strike

    if best_strike is not None:
        return best_strike, uic_map[best_strike]

    return None, None
