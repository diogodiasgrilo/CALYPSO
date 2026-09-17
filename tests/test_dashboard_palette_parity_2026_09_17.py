"""The palette exists twice; the two copies must agree (defect D15).

`index.css` declares the brand colours as `--color-*` custom properties, which
Tailwind classes and plain CSS consume. `lib/tradingColors.ts` re-declares the
same values as literals, because the charting libraries (recharts,
lightweight-charts) need concrete strings and cannot read a custom property.

Two copies of one palette is a drift hazard, and **the drift had already
happened** when this file was written:

    --color-bg-deep: #161d23      (index.css)
    bgDeep: "#1a2229"             (tradingColors.ts — a copy of `bg`,
                                   commented "alias for bg")

It had no visual effect only because nothing consumed `colors.bgDeep`. The first
caller would have got the wrong shade with nothing to catch it.

A runtime read of the custom properties was considered and rejected:
`getComputedStyle` at module load can run before the stylesheet applies, and a
chart silently painted in fallback colours is a worse failure than a red build.
So the duplication stays and this test makes it honest.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "dashboard" / "frontend" / "src"
CSS = SRC / "index.css"
TS = SRC / "lib" / "tradingColors.ts"


def _css_tokens() -> dict[str, str]:
    """`--color-foo-bar: #hex;` → {"fooBar": "#hex"} (camelCased to match TS)."""
    out: dict[str, str] = {}
    for name, value in re.findall(r"--color-([a-z0-9-]+)\s*:\s*([^;]+);", CSS.read_text()):
        parts = name.split("-")
        key = parts[0] + "".join(p.capitalize() for p in parts[1:])
        out[key] = value.strip().lower()
    return out


def _ts_colors() -> dict[str, str]:
    """The `colors` object literal → {key: value}."""
    src = TS.read_text()
    block = src[src.index("export const colors = {"):]
    block = block[: block.index("} as const;")]
    out: dict[str, str] = {}
    for key, value in re.findall(r"(\w+)\s*:\s*\"([^\"]+)\"", block):
        out[key] = value.strip().lower()
    return out


def test_both_palettes_parse():
    css, ts = _css_tokens(), _ts_colors()
    assert len(css) >= 15, f"only parsed {len(css)} CSS tokens — parser broken?"
    assert len(ts) >= 15, f"only parsed {len(ts)} TS colours — parser broken?"


def test_shared_tokens_have_identical_values():
    """The whole point. Every name present in BOTH must carry the same value."""
    css, ts = _css_tokens(), _ts_colors()
    shared = sorted(set(css) & set(ts))
    assert len(shared) >= 12, (
        f"only {len(shared)} shared token names — the naming convention may have "
        f"changed, which would make this test pass while checking almost nothing."
    )
    mismatches = {k: (css[k], ts[k]) for k in shared if css[k] != ts[k]}
    assert not mismatches, (
        "palette drift between index.css and lib/tradingColors.ts:\n"
        + "\n".join(f"  {k}: css={c!r} ts={t!r}" for k, (c, t) in mismatches.items())
        + "\nCharts read the TS copy and everything else reads the CSS custom "
          "properties, so the two render different colours for the same name."
    )


def test_the_specific_token_that_had_drifted():
    """Named explicitly so a regression is unambiguous rather than buried in a
    diff of the whole palette."""
    css, ts = _css_tokens(), _ts_colors()
    assert css["bgDeep"] == "#161d23"
    assert ts["bgDeep"] == "#161d23", (
        "bgDeep is back to being a copy of `bg` — it is a DISTINCT level "
        "(the deepest recess), not an alias."
    )
    assert ts["bgDeep"] != ts["bg"], "bgDeep and bg are different surface levels"


@pytest.mark.parametrize("key", ["profit", "loss", "warning", "info",
                                 "textPrimary", "textSecondary", "textDim",
                                 "card", "border"])
def test_core_semantic_colours_are_shared_and_equal(key):
    """These are the ones a drift would be most visible in — P&L green/red
    disagreeing between a chart and the number beside it."""
    css, ts = _css_tokens(), _ts_colors()
    assert key in css and key in ts, f"{key} is no longer defined in both files"
    assert css[key] == ts[key]
