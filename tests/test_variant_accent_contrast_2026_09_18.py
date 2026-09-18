"""Adding a variant could hand it an illegible colour, and nothing would say so.

Caught by CI on 2026-09-18, minutes after variant ``bm`` landed: the contrast probe
reported six failing combinations on the comparison page, all of them
``rgb(152,100,216)`` — a violet at **3.41:1** against the card, under the 4.5:1 this app
holds itself to.

THE MECHANISM. ``lib/pnlShape.ts`` keys accents by variant letter and falls back for
anything unlisted:

    function hashedHue(id) { ...; return `hsl(${h % 360}, 60%, 62%)`; }

whose comment promised "a 6th+ variant still gets a distinct, stable color". Distinct and
stable, yes. **Legible, no** — and nothing said so. At 62% lightness the worst hue (240,
blue) scores **2.88:1** on ``card``; an unlisted variant whose name happens to hash into
that band gets a colour a reader cannot see. ``bm`` hashed into exactly such a band.

TWO FIXES, because fixing only the first leaves the trap armed for variant H:

  1. ``bm`` gets an explicit palette entry (pink, 7.38:1).
  2. The FALLBACK is made safe: lightness 62% -> 72%, the measured minimum at which every
     hue 0-359 clears 4.5:1 against both backgrounds (worst case 4.65:1 at hue 240).

The second is what this file mainly guards, and it is guarded by walking all 360 hues
rather than by spot-checking the ids that exist today — a check that only tests today's
variants is the same shape of mistake as the hand-written settings list that let this
commit's sibling bug through an hour earlier.
"""

from __future__ import annotations

import colorsys
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import strategy_taxonomy as tax  # noqa: E402

PNL_SHAPE = ROOT / "dashboard" / "frontend" / "src" / "lib" / "pnlShape.ts"
COLORS_TS = ROOT / "dashboard" / "frontend" / "src" / "lib" / "tradingColors.ts"

AA_TEXT = 4.5


def _srgb(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum_rgb(r: float, g: float, b: float) -> float:
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def _lum_hex(h: str) -> float:
    h = h.lstrip("#")
    return _lum_rgb(*(int(h[i:i + 2], 16) for i in (0, 2, 4)))


def contrast(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def tokens() -> dict:
    return dict(re.findall(r'^\s*([a-zA-Z]+):\s*"(#[0-9a-fA-F]{6})"',
                           COLORS_TS.read_text(), flags=re.M))


def backgrounds() -> dict:
    """The two surfaces an accent is drawn on. Both must clear AA — the CI findings
    included pairs against each."""
    t = tokens()
    return {"card": _lum_hex(t["card"]), "bg": _lum_hex(t["bg"])}


def palette() -> dict:
    """The explicit per-letter accents, resolved to hex.

    Entries written as `colors.x` are resolved through tradingColors so this follows a
    palette change instead of pinning a stale copy.
    """
    src = PNL_SHAPE.read_text()
    block = src[src.index("const ACCENT_PALETTE"):]
    block = block[: block.index("};")]
    t = tokens()
    out = {}
    for key, val in re.findall(r"^\s*(\w+):\s*([^,]+),", block, flags=re.M):
        val = val.strip()
        if val.startswith('"'):
            out[key] = val.strip('"')
        elif val.startswith("colors."):
            name = val.split(".", 1)[1]
            assert name in t, f"ACCENT_PALETTE references colors.{name}, which is gone"
            out[key] = t[name]
    return out


def hashed_hue_lightness() -> int:
    m = re.search(r"hsl\(\$\{h % 360\},\s*(\d+)%,\s*(\d+)%\)", PNL_SHAPE.read_text())
    assert m, "hashedHue's hsl() template is gone or reshaped"
    return int(m.group(1)), int(m.group(2))


# ══════════════════════════════════════════════════════════════════════════════
# THE DEFECT CI CAUGHT
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("vid", sorted(tax.STRATEGIES))
def test_every_variant_has_a_legible_accent(vid):
    """Every strategy in the taxonomy — whether it has an explicit entry or falls through
    to the hash — must render at AA on both backgrounds."""
    pal = palette()
    bgs = backgrounds()
    if vid in pal:
        lum = _lum_hex(pal[vid])
        where = f"explicit {pal[vid]}"
    else:
        sat, light = hashed_hue_lightness()
        h = 0
        for ch in vid:
            h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        r, g, b = colorsys.hls_to_rgb((h % 360) / 360, light / 100, sat / 100)
        lum = _lum_rgb(r * 255, g * 255, b * 255)
        where = f"hashed hue {h % 360}"
    for name, bl in bgs.items():
        got = contrast(lum, bl)
        assert got >= AA_TEXT, (
            f"variant {vid!r} accent ({where}) is {got:.2f}:1 on {name} — below "
            f"{AA_TEXT}:1. This is what CI caught when `bm` fell through to the hash."
        )


def test_bm_has_an_explicit_accent():
    """It is a real, known variant — it should not depend on a hash landing well."""
    assert "bm" in palette(), "bm has no explicit accent and relies on the fallback"


# ══════════════════════════════════════════════════════════════════════════════
# THE TRAP, which is the part that outlives this commit
# ══════════════════════════════════════════════════════════════════════════════

def test_the_hashed_fallback_is_legible_at_EVERY_hue():
    """THE ONE THAT MATTERS. Walks all 360 hues, not just the ids that exist today.

    A check over current variants would have passed the day before `bm` and failed the
    day after — it tests the roster, not the rule. The rule is that ANY id the hash can
    produce must be legible, so every hue is checked.
    """
    sat, light = hashed_hue_lightness()
    bgs = backgrounds()
    worst = (99.0, None, None)
    for hue in range(360):
        r, g, b = colorsys.hls_to_rgb(hue / 360, light / 100, sat / 100)
        lum = _lum_rgb(r * 255, g * 255, b * 255)
        for name, bl in bgs.items():
            c = contrast(lum, bl)
            if c < worst[0]:
                worst = (c, hue, name)
    assert worst[0] >= AA_TEXT, (
        f"the hashed accent fallback (hsl(h, {sat}%, {light}%)) drops to "
        f"{worst[0]:.2f}:1 at hue {worst[1]} on {worst[2]} — an unlisted variant whose "
        f"name hashes there gets a colour a reader cannot see. Raise the lightness."
    )


def test_the_fallback_lightness_was_actually_raised():
    """Pins the specific fix. 62% was the shipped value and its worst case is 2.88:1."""
    _, light = hashed_hue_lightness()
    assert light >= 72, (
        f"hashedHue lightness is {light}% — below the measured 72% minimum at which "
        f"every hue clears AA"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Controls
# ══════════════════════════════════════════════════════════════════════════════

def test_accents_stay_distinguishable_from_each_other():
    """An accent exists to tell variants apart. Two that render alike defeat the point
    even if both are legible.

    MEASURED BY HUE SEPARATION, not luminance ratio — the first version used the latter
    and flagged a/b (blue vs amber) and d/e (coral vs purple) as "too similar". They are
    plainly distinguishable; luminance ratio simply cannot see colour, so it was the
    wrong instrument answering a question it had no access to. A test with a wrong metric
    is worse than none: it fails on correct code and would have been "fixed" by loosening
    the threshold until it stopped complaining.

    Threshold 30 degrees, below the palette's current 38-degree minimum (amber 41 vs
    coral 3) so there is headroom, but tight enough to catch a genuinely confusable pair.
    """
    import colorsys as _cs

    def _hue(hexc: str) -> float:
        h = hexc.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        return _cs.rgb_to_hls(r, g, b)[0] * 360.0

    pal = palette()
    hues = {k: _hue(v) for k, v in pal.items()}
    ids = sorted(hues)
    too_close = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            d = abs(hues[a] - hues[b])
            d = min(d, 360.0 - d)          # hue is circular
            if d < 30.0:
                too_close.append(f"{a}/{b} ({d:.0f}deg apart)")
    assert not too_close, f"accent pairs too similar to tell apart: {too_close}"


def test_the_palette_still_resolves_through_tradingColors():
    """CONTROL. Most entries are `colors.x` references; if they became literals this
    file would be checking a stale copy while the app used something else."""
    src = PNL_SHAPE.read_text()
    block = src[src.index("const ACCENT_PALETTE"):]
    block = block[: block.index("};")]
    assert "colors." in block, (
        "ACCENT_PALETTE no longer references tradingColors — a palette change would no "
        "longer flow through to the accents"
    )
