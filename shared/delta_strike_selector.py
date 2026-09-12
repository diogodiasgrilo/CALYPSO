"""Delta-based strike selection — a standalone, broker-parameterized helper.

Adapts the target-delta walk from ``bots/hydra/double_calendar_strategy.py``'s
``_dc_pick_delta_strike`` (D's two-expiry delta-target strike picker) into a
single-expiry, dependency-free function. D's original intersects a short-expiry
and long-expiry strike map (its "listed on both expiries" gap-day guard) — that
intersection is specific to D's calendar shape and is dropped here, not reused;
this function walks a single ``strike -> conid`` map for one expiry.

Built on ``IBClient.get_option_greeks`` (native IBKR delta, no Polygon
dependency) so it works for any variant regardless of whether it runs Brandon's
GEX-coupled delta-target picker. First consumer: Variant F (Ghauri mean-
reversion), which needs one target delta per fire. Written parameterized (a
list-of-target-deltas caller can just call this once per target) specifically
so a future multi-strike strategy (e.g. a broken-wing butterfly needing 3
target deltas) can reuse it as-is — see docs/STRATEGY_CANDIDATES.md.

``get_option_greeks`` is a single-conid call (not batched like MKT-020/022's
"1 chain + 1 batch quote" pattern) — each read can cost several REST calls
through the shared ``market``-family circuit breaker every live variant shares
in broker-mode deployment. ``max_reads`` bounds the worst case per selection
call; callers should also set a conservative ``api_pacing_multiplier`` in their
own variant config.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple


def select_strike_by_delta(
    broker,
    strike_map: Dict[float, int],
    target_delta: float,
    right: str,
    base: float,
    band: Optional[Tuple[float, float]] = None,
    max_reads: int = 6,
) -> Optional[float]:
    """Walk ``strike_map`` outward from ``base``, returning the strike whose
    live delta is closest to ``target_delta`` within ``band``.

    Args:
        broker: An object exposing ``get_option_greeks(conid) -> dict`` (e.g.
            ``IBClient``) — a single-conid greeks snapshot whose returned dict
            has a ``"delta"`` key (may be ``None``/missing on a bad read).
        strike_map: ``{strike: conid}`` for ONE expiry and ONE right (calls or
            puts) — the candidate universe to search. Build via
            ``IBClient.get_option_chain`` + ``qualify_option_strikes``.
        target_delta: Desired |delta| (e.g. 0.15 for a 15-delta strike). Always
            compared against ``abs(delta)`` — sign convention (calls positive,
            puts negative) is normalized away, so pass a positive value here
            regardless of ``right``.
        right: ``"Call"`` or ``"Put"`` — determines walk direction (calls scan
            upward in strike from ``base``, puts scan downward, matching how
            |delta| decreases moving further OTM on either side).
        base: Starting strike (typically ATM/current spot) — the walk begins at
            the candidate nearest this value and moves toward ``target_delta``.
        band: ``(lo, hi)`` acceptable |delta| range. A candidate outside this
            band is never selected even if it's the closest score seen.
            Defaults to ``(0.0, 1.0)`` (no effective band) when omitted.
        max_reads: Hard cap on the number of ``get_option_greeks`` calls this
            selection may make — bounds worst-case REST/circuit-breaker load.

    Returns:
        The best-matching strike, or ``None`` if ``strike_map`` is empty or no
        candidate's delta ever falls inside ``band`` within ``max_reads`` reads.
    """
    if not strike_map:
        return None

    candidates = sorted(strike_map.keys())
    ordered = candidates if right == "Call" else list(reversed(candidates))
    lo, hi = band if band is not None else (0.0, 1.0)
    cap = max(1, int(max_reads))
    cache: Dict[float, Optional[float]] = {}

    def delta_at(k: float) -> Optional[float]:
        if k in cache:
            return cache[k]
        try:
            greeks = broker.get_option_greeks(strike_map[k]) or {}
            d = abs(float(greeks.get("delta")))
        except (TypeError, ValueError):
            d = None
        except Exception:
            d = None
        cache[k] = d if (d and d > 0) else None
        return cache[k]

    i = min(range(len(ordered)), key=lambda j: abs(ordered[j] - base))
    best: Optional[Tuple[float, float]] = None  # (score, strike)

    while 0 <= i < len(ordered) and len(cache) < cap:
        k = ordered[i]
        d = delta_at(k)
        if d is None:
            i += 1
            continue
        in_band = lo <= d <= hi
        if in_band:
            score = abs(d - target_delta)
            if best is None or score < best[0]:
                best = (score, k)
        nxt = i + 1 if d > target_delta else i - 1
        if best is not None and not in_band:
            break
        if 0 <= nxt < len(ordered) and ordered[nxt] in cache:
            break
        i = nxt

    return best[1] if best else None
