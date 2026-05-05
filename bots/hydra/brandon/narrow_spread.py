"""Brandon-style narrow spread width.

Brandon Jones in the interview: "5 to 10 width usually… When VIX is trading
between 24 to 28, maybe 30-ish, we usually look to capitalize off of the 10
delta widths… typically we look to enter the longs in a 10 width unless we're
trending more towards the 20 spot price on the VIX, then we'll go with the
fives."

Encoded as a single VIX breakpoint (default 22.0): 5pt below, 10pt at-or-above.
A floor and ceiling cap the result so configuration mistakes don't produce
zero-width or absurdly large spreads.

Variant C swaps HYDRA's MKT-027 dynamic formula (VIX × 6, capped at 110pt)
for this rule so we can A/B "full stack" vs "full stack + narrow widths."
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NarrowSpreadConfig:
    breakpoint_vix: float = 22.0
    width_low: int = 5
    width_high: int = 10
    floor_pts: int = 5
    ceiling_pts: int = 25  # safety cap; Brandon never goes wider than 10 in his rule


def narrow_spread_width(vix: float, config: NarrowSpreadConfig = NarrowSpreadConfig()) -> int:
    """Return Brandon's narrow spread width in points for a given VIX.

    Out-of-band inputs (VIX <= 0, NaN) fall back to width_low so the bot keeps
    placing orders rather than crashing on a momentary VIX feed glitch.
    """
    if vix is None or vix != vix or vix <= 0:
        chosen = config.width_low
    elif vix < config.breakpoint_vix:
        chosen = config.width_low
    else:
        chosen = config.width_high
    return max(config.floor_pts, min(chosen, config.ceiling_pts))
