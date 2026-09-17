"""The type scale is named, not improvised (defect D16).

A dense financial UI legitimately needs text below Tailwind's smallest step
(`xs` = 12px), but Tailwind has no such step, so **104 one-off arbitrary values**
had accumulated:

    67 x text-[10px]
    21 x text-[11px]
    16 x text-[9px]

Three sizes within 2px of each other, and — the actual problem — used
INTERCHANGEABLY. Uppercase labels appeared at both 10px and 11px; dimmed text at
both 9px and 10px. That is drift, not a scale.

Two named steps now cover all of it, defined once in `index.css @theme`:

    2xs  11px   smallest READABLE PROSE — captions, help text
    3xs  10px   labels, badges, dense tabular data (uppercase / mono)

9px was retired into 3xs: it carried no role 10px did not already serve, and
sub-10px is below a sensible legibility floor.

VERIFIED BY MEASUREMENT, not by reading. A rendered font-size census over all 42
surfaces (7 strategies x 6 routes), counting every text-bearing leaf element:

    size    before   after
     9px      230       0
    10px     2535    2765     (+230, exactly the retired 9px elements)
    11px      694     694     unchanged
    12px     3764    3764     unchanged

The rename step alone (`text-[10px]` -> `text-3xs` etc.) produced a census
IDENTICAL bucket-for-bucket, proving it changed no rendered size. A pixel diff
was tried first and discarded: chart canvases render non-deterministically, so
6 of 42 screenshots differ between two runs of the same build.
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
COLORS = SRC / "lib" / "tradingColors.ts"


def _tsx_files():
    return list(SRC.rglob("*.tsx"))


def _strip_comments(src: str) -> str:
    """Remove block and line comments before searching for a banned token.

    A naive substring search bans the token from PROSE too, so the very comment
    explaining "9px was retired" fails the test that enforces it. That trap bit
    three separate tests in one day (the capabilities gate, the D6 calendar
    note, and this one), so it is handled explicitly rather than by rewording
    the documentation around the assertion.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def test_no_arbitrary_font_sizes_remain():
    """The whole point. Any `text-[Npx]` is a size outside the scale."""
    offenders = []
    for p in _tsx_files():
        for m in re.finditer(r"text-\[[0-9.]+(px|rem)\]", _strip_comments(p.read_text())):
            offenders.append(f"{p.relative_to(SRC)}: {m.group(0)}")
    assert not offenders, (
        f"{len(offenders)} arbitrary font size(s) reintroduced — use text-2xs "
        f"(11px) or text-3xs (10px):\n  " + "\n  ".join(offenders[:15])
    )


def test_the_scale_steps_are_defined_once():
    css = CSS.read_text()
    block = css[css.index("@theme {"): css.index("\n}", css.index("@theme {"))]
    assert "--text-2xs: 0.6875rem" in block, "2xs (11px) missing from @theme"
    assert "--text-3xs: 0.625rem" in block, "3xs (10px) missing from @theme"


def test_both_steps_are_actually_used():
    """A token nobody uses is not a scale — it is dead config."""
    joined = "\n".join(p.read_text() for p in _tsx_files())
    assert joined.count("text-2xs") >= 15, "text-2xs is barely used"
    assert joined.count("text-3xs") >= 50, "text-3xs is barely used"


def test_nine_px_is_retired_everywhere():
    """Including the places a class cannot reach: charting libraries take a px
    NUMBER. EquityCurve's marker label sat at 9 after 9px was retired from every
    class, and only a rendered census caught it."""
    for p in list(_tsx_files()) + [CSS]:
        src = _strip_comments(p.read_text())
        assert "text-[9px]" not in src, f"{p.name} still has a 9px class"
        assert not re.search(r"fontSize:\s*9\b", src), (
            f"{p.name} sets fontSize: 9 numerically — below the legibility "
            f"floor and outside the scale."
        )


def test_charting_sizes_come_from_a_shared_constant():
    """Charting props take numbers, so they cannot use a class. They must still
    track the scale rather than being magic numbers."""
    src = COLORS.read_text()
    assert "export const fontSizePx" in src
    block = src[src.index("export const fontSizePx"):]
    block = block[: block.index("} as const;")]
    assert "xs3: 10" in block and "xs2: 11" in block and "xs: 12" in block


def test_chart_label_uses_the_constant():
    src = (SRC / "components" / "pnl" / "EquityCurve.tsx").read_text()
    assert "fontSize: fontSizePx.xs3" in src


@pytest.mark.parametrize("name,rem,px", [("2xs", "0.6875rem", 11), ("3xs", "0.625rem", 10)])
def test_rem_values_are_the_intended_pixels(name, rem, px):
    """A rem typo silently resizes every use of the step."""
    assert abs(float(rem.replace("rem", "")) * 16 - px) < 0.01, (
        f"--text-{name} is {rem}, which is not {px}px at a 16px root"
    )


def test_scale_has_no_one_pixel_steps_below_xs():
    """11 -> 12 is a 1px step and that is already tight; a THIRD step in the
    same range is what produced the drift. Guard against re-adding one."""
    css = CSS.read_text()
    steps = sorted(
        float(v) * 16
        for v in re.findall(r"--text-\d?x?s?\w*:\s*([0-9.]+)rem", css)
    )
    below_xs = [s for s in steps if s < 12]
    assert len(below_xs) <= 2, (
        f"{len(below_xs)} sub-12px steps ({below_xs}) — three near-identical "
        f"sizes is what this fix removed."
    )
