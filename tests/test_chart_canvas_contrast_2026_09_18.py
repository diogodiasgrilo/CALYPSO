"""The price chart is a CANVAS, so no DOM probe can see it.

uiaudit/diag-contrast.mjs walks text nodes and composites their backgrounds. It
reported 0 failing nodes across six surfaces — and the SPX candle chart was
never in that number, because lightweight-charts paints to a `<canvas>` and a
canvas has no text nodes. Every axis label, crosshair and candle on the most
looked-at element of the dashboard sat outside the audit entirely.

That went unnoticed until the operator asked, directly, whether the palette
change had touched the charts. It had: the chart reads six tokens and five of
them moved. They all moved in the right direction, but that was luck rather than
verification — nothing would have caught it if they had not.

This closes the gap arithmetically rather than by sampling pixels. The chart's
colours are not painted from CSS; they are passed to lightweight-charts as
concrete strings from `tradingColors`, so every pairing is knowable statically
and exactly. Pixel-sampling would be slower, flakier, and no more truthful.

THRESHOLDS. WCAG 1.4.3 wants 4.5:1 for text; 1.4.11 wants 3:1 for graphics that
convey meaning. An axis label is text. A candle body, its border and its wick
are meaningful graphics — the wick IS the high/low, so it is not decoration.
Grid lines are decoration and carry no requirement.

FOUND BY THIS TEST ON ITS FIRST RUN: the DOWN WICK sat at 2.85:1, under the
graphics threshold — the least legible thing on the chart, and the one element
the palette work had left untouched. Lifted to 3.20:1 while staying visibly
darker than the candle body, which is the distinction the design intends.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "dashboard" / "frontend" / "src"
CHART = SRC / "components" / "market" / "SPXChart.tsx"
COLORS = SRC / "lib" / "tradingColors.ts"

AA_TEXT = 4.5      # WCAG 1.4.3
AA_GRAPHIC = 3.0   # WCAG 1.4.11


def _srgb(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tokens() -> dict[str, str]:
    """camelCase token -> hex, straight from the JS object the chart reads."""
    return dict(re.findall(r"^\s*([a-zA-Z]+):\s*\"(#[0-9a-fA-F]{6})\"",
                           COLORS.read_text(), flags=re.M))


#: Every colour SPXChart hands to lightweight-charts, and what it draws.
#: (chart option, token, threshold, what a reader actually sees)
CHART_COLOURS = [
    ("textColor",       "textSecondary", AA_TEXT,    "price + time axis labels"),
    ("upColor",         "profit",        AA_GRAPHIC, "up candle body"),
    ("downColor",       "loss",          AA_GRAPHIC, "down candle body"),
    ("borderUpColor",   "profit",        AA_GRAPHIC, "up candle border"),
    ("borderDownColor", "loss",          AA_GRAPHIC, "down candle border"),
    ("wickUpColor",     "profitMuted",   AA_GRAPHIC, "up wick — the session high"),
    ("wickDownColor",   "lossMuted",     AA_GRAPHIC, "down wick — the session low"),
    ("crosshair",       "textDim",       AA_GRAPHIC, "crosshair lines"),
]


@pytest.mark.parametrize("option,token,threshold,what", CHART_COLOURS)
def test_chart_colour_clears_its_threshold(option, token, threshold, what):
    """The chart background is `colors.card`, set on the chart itself."""
    t = tokens()
    assert token in t, f"token {token} is gone from tradingColors"
    got = contrast(t[token], t["card"])
    assert got >= threshold, (
        f"{option} ({token} {t[token]}) draws the {what} at {got:.2f}:1 on the "
        f"chart background {t['card']} — below {threshold}:1"
    )


def test_the_chart_background_is_the_card_token():
    """Every threshold above is computed against colors.card. If the chart's
    own background moves, all of them are measuring the wrong thing."""
    src = CHART.read_text()
    assert re.search(r"ColorType\.Solid,\s*color:\s*colors\.card", src), (
        "the chart background is no longer colors.card — every contrast figure "
        "in this file is now computed against the wrong surface"
    )


@pytest.mark.parametrize("option,token,_t,_w", [(o, tk, t, w) for o, tk, t, w in CHART_COLOURS
                                                if o != "crosshair"])
def test_the_chart_still_uses_the_token_this_file_checks(option, token, _t, _w):
    """Guards the premise. If SPXChart swapped `wickDownColor` to a literal or a
    different token, this file would go on happily checking a colour the chart
    no longer draws."""
    src = CHART.read_text()
    assert re.search(rf"{option}:\s*colors\.{token}\b", src), (
        f"SPXChart no longer sets {option} from colors.{token} — this file is "
        f"checking a pairing that does not exist"
    )


def test_the_dom_probe_cannot_see_the_chart():
    """Records WHY this file exists, and pins the premise.

    If the chart ever stops being a canvas, diag-contrast.mjs would start
    covering it and this file becomes redundant rather than load-bearing.
    """
    src = CHART.read_text()
    assert "lightweight-charts" in src, (
        "SPXChart no longer uses lightweight-charts — re-check whether the DOM "
        "contrast probe now covers it, and whether this file is still needed"
    )


def test_the_wick_stays_distinguishable_from_the_body():
    """The wick is deliberately darker than the candle body. Lifting it for
    contrast must not lift it INTO the body colour, or the candle loses the
    distinction the muted token exists to draw."""
    t = tokens()
    assert contrast(t["lossMuted"], t["loss"]) >= 1.2, (
        f"down wick {t['lossMuted']} is now indistinguishable from the down "
        f"body {t['loss']}"
    )
    assert _lum(t["lossMuted"]) < _lum(t["loss"]), "the wick must stay darker than the body"
