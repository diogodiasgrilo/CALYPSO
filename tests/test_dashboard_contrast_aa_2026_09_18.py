"""Every text colour must clear WCAG AA against every surface it lands on.

Measured 2026-09-18 with uiaudit/diag-contrast.mjs, which walks real text nodes
on the rendered pages and composites the effective background up the ancestor
chain: **281 failing text nodes across six surfaces.**

    text-dim   #5e6e82   2.67:1 on card     196 nodes (69% of all failures)
    loss       #f85149   4.15:1 on card      47 nodes (the P&L figures)
    calendar   white on the heat ramp        21 nodes
    others                                   17 nodes

The dominant cause has no fix at the surface end: ``#5e6e82`` cannot reach 4.5:1
against ANY background — on pure black it tops out at 4.03:1 — so the token
itself had to change. Raising only ``text-dim`` would have landed it 1.10:1 from
``text-secondary``, collapsing a three-level hierarchy into two, so the ramp was
re-spaced. ``text-primary`` is untouched: it already passed everywhere and it
carries most of the brand.

TWO THINGS THE FIXTURES HID, both found only by computing the whole matrix
rather than trusting the rendered sample:

  * The calendar's brightest green step scored 4.03:1 with white on it, but the
    synthetic fixture's P&L is effectively binary so no max-intensity cell ever
    rendered. The preview reported "0 failures" over a gap it never drew.
  * The cushion readout is coloured by ``cushionColor()``, used as BOTH text
    and fill. The fixture only ever showed 100% cushion, so the probe never
    sampled the low-cushion branches — the dangerous ones. Checking every
    branch by hand shows ``cushionColor`` is fine once the tokens move (worst
    4.51:1), but its muted sibling ``mutedCushion()`` in icEntryView would be
    2.85:1 as text. That one is fill-only today and must stay that way; the
    test below pins it rather than an assumption.

    Worth recording that this started as a wrong diagnosis: the first version
    of this file asserted ``cushionColor`` returned the muted tokens and
    demanded a new ``cushionTextColor()`` to fix it. Reading the function
    showed it already returned the semantic ones. The abstraction was dropped
    rather than added to satisfy a test built on a misreading.

WHY TOOLTIPS MOVED SURFACE. ``bg-elevated`` is a real text surface (chart
tooltips, command palette, pickers, tabs), and tooltips render P&L in loss red.
Solving ``loss`` against ``bg-elevated`` gives ``#fa7f79`` — a washed-out pink,
a far bigger change to the product's most identity-bearing colour than the
operator approved from the preview. Darkening the tooltip background to ``card``
instead keeps the crisp red and costs nothing visually: no loss-coloured text
remains on ``bg-elevated``.

SCOPE. This asserts PERSISTENT surfaces. Transient hover tints are measured
separately by uiaudit/diag-contrast-hover.mjs — see
test_hover_tints_do_not_drop_text_below_aa below for what is and is not covered.
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
CAL = SRC / "components" / "history" / "MonthCalendar.tsx"

AA_NORMAL = 4.5


# ── colour maths (the same formulae the browser probe uses) ────────────────

def _srgb_to_linear(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * _srgb_to_linear(r)
            + 0.7152 * _srgb_to_linear(g)
            + 0.0722 * _srgb_to_linear(b))


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tokens() -> dict[str, str]:
    """``--color-*`` declarations from index.css, keyed without the prefix."""
    return {k: v.strip() for k, v in
            re.findall(r"--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;", CSS.read_text())}


# ── the matrix ─────────────────────────────────────────────────────────────

#: Surfaces a token can sit on, as a persistent (non-hover) background.
#: text-* land on everything. The semantic accents land on everything EXCEPT
#: bg-elevated, which is enforced by its own test below rather than assumed.
ALL_SURFACES = ["bg", "bg-deep", "card", "card-hover"]
TEXT_TOKENS = ["text-primary", "text-secondary", "text-dim"]
ACCENT_TOKENS = ["profit", "loss", "warning", "info", "profit-muted"]


@pytest.mark.parametrize("token", TEXT_TOKENS)
@pytest.mark.parametrize("surface", ALL_SURFACES + ["bg-elevated"])
def test_text_tokens_clear_aa_on_every_surface(token, surface):
    t = tokens()
    got = contrast(t[token], t[surface])
    assert got >= AA_NORMAL, (
        f"--color-{token} ({t[token]}) on --color-{surface} ({t[surface]}) "
        f"is {got:.2f}:1, below {AA_NORMAL}:1"
    )


@pytest.mark.parametrize("token", ACCENT_TOKENS)
@pytest.mark.parametrize("surface", ALL_SURFACES)
def test_accent_tokens_clear_aa_on_persistent_surfaces(token, surface):
    t = tokens()
    got = contrast(t[token], t[surface])
    assert got >= AA_NORMAL, (
        f"--color-{token} ({t[token]}) on --color-{surface} ({t[surface]}) "
        f"is {got:.2f}:1, below {AA_NORMAL}:1"
    )


def test_text_ramp_keeps_three_distinguishable_levels():
    """The reason the whole ramp moved rather than just its bottom rung.

    Raising text-dim alone to 4.5:1 puts it 1.10:1 from text-secondary — the
    same colour, to any eye. Three names for two shades is worse than the
    accessibility problem it fixes.
    """
    t = tokens()
    dim, sec, pri = t["text-dim"], t["text-secondary"], t["text-primary"]
    assert contrast(dim, sec) >= 1.4, (
        f"text-dim {dim} and text-secondary {sec} are only "
        f"{contrast(dim, sec):.2f}:1 apart — the hierarchy has collapsed"
    )
    assert contrast(sec, pri) >= 1.3, (
        f"text-secondary {sec} and text-primary {pri} are only "
        f"{contrast(sec, pri):.2f}:1 apart"
    )
    # And the order must not invert.
    assert _luminance(dim) < _luminance(sec) < _luminance(pri)


# ── the calendar heat ramp ─────────────────────────────────────────────────

def calendar_ramp() -> dict[str, list[str]]:
    """The PROFIT_HEAT / LOSS_HEAT arrays declared in MonthCalendar.tsx."""
    src = CAL.read_text()
    out: dict[str, list[str]] = {}
    for name in ("PROFIT_HEAT", "LOSS_HEAT"):
        m = re.search(rf"{name}\s*(?::[^=]+)?=\s*\[([^\]]+)\]", src)
        assert m, f"{name} not found in MonthCalendar.tsx"
        out[name] = re.findall(r"#[0-9a-fA-F]{6}", m.group(1))
        assert out[name], f"{name} contains no hex colours"
    return out


def test_every_calendar_heat_step_is_legible():
    """The old ramp faded a bright mint/red toward the surface with rising
    alpha, so the LARGEST P&L days produced the LIGHTEST cells and their day
    numbers fell to 2.03:1. Legibility was inversely correlated with
    importance. The replacement darkens as it saturates."""
    t = tokens()
    fg = t["text-primary"]
    for name, ramp in calendar_ramp().items():
        for i, step in enumerate(ramp):
            got = contrast(fg, step)
            assert got >= AA_NORMAL, (
                f"{name}[{i}] = {step} gives {got:.2f}:1 with {fg} — the "
                f"strongest-P&L cells are the ones people most need to read"
            )


def test_calendar_ramp_darkens_monotonically():
    """Pins the shape, not just the endpoints: a ramp that brightened in the
    middle would pass the per-step check and still reintroduce the bug at
    whatever intensity happened not to be sampled."""
    for name, ramp in calendar_ramp().items():
        lums = [_luminance(c) for c in ramp]
        assert lums == sorted(lums), (
            f"{name} does not darken monotonically: "
            f"{[f'{x:.3f}' for x in lums]}"
        )


# ── structural rules that keep the matrix true ─────────────────────────────

def test_chart_tooltips_do_not_sit_on_bg_elevated():
    """Tooltips render P&L in loss red. Keeping them on bg-elevated would force
    loss to #fa7f79 (a washed-out pink) to reach AA; moving them to card keeps
    the red crisp. If a new chart reinstates bgElevated, loss silently fails
    there again — with no visible symptom until someone measures.

    Scans for the property, not for a `contentStyle={{...}}` shape. The first
    version matched only the inline form and passed while SEVEN of the eight
    tooltips were still on bg-elevated — they are written as a shared
    `const chartTooltipStyle = {...}` or span multiple lines.
    """
    offenders = []
    for path in list(SRC.rglob("*.tsx")) + list(SRC.rglob("*.ts")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"backgroundColor:\s*colors\.bgElevated", line):
                offenders.append(f"{path.relative_to(SRC)}:{i}")
    # EntryCard / icEntryView use bgElevated for a warning CALLOUT panel whose
    # text is text-primary and warning — both of which clear AA on it. Only
    # loss-coloured text is the problem, and no chart tooltip may carry it.
    allowed = {"components/entries/EntryCard.tsx", "components/dashboard/icEntryView.tsx"}
    offenders = [o for o in offenders if o.rsplit(":", 1)[0] not in allowed]
    assert not offenders, (
        "surface(s) still painting bg-elevated behind chart content: "
        + ", ".join(sorted(offenders))
    )


def test_cushion_readout_clears_aa_on_every_branch():
    """The cushion percentage is TEXT coloured by cushionColor(). The fixtures
    only ever rendered 100% cushion, so the browser probe never sampled the
    low-cushion branches — precisely the ones that matter. Check them all."""
    t = tokens()
    ts = TS.read_text()
    body = ts[ts.index("export function cushionColor"):]
    body = body[: body.index("\n}")]
    camel = {"profit": "profit", "loss": "loss", "warning": "warning",
             "profitMuted": "profit-muted", "info": "info"}
    values = (re.findall(r"#[0-9a-fA-F]{6}", body)
              + [t[camel[n]] for n in re.findall(r"colors\.([a-zA-Z]+)", body) if n in camel])
    assert len(values) >= 4, f"only found {values} — has cushionColor changed shape?"
    for v in values:
        for surface in ("card", "card-hover"):
            got = contrast(v, t[surface])
            assert got >= AA_NORMAL, (
                f"cushionColor can return {v}, which is {got:.2f}:1 on {surface} "
                f"— and the low-cushion branches are the dangerous ones"
            )


def test_the_muted_cushion_ramp_stays_fill_only():
    """mutedCushion() returns values as low as 2.85:1 as text. That is correct
    for a bar FILL and wrong for a label, so pin the distinction rather than
    trusting that nobody reuses it."""
    offenders = []
    for path in SRC.rglob("*.tsx"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if "mutedCushion(" in line and re.search(r"\bcolor:\s*mutedCushion", line):
                offenders.append(f"{path.relative_to(SRC)}:{i}: {line.strip()[:80]}")
    assert not offenders, (
        "mutedCushion() used as a TEXT colour:\n  " + "\n  ".join(offenders)
    )


def test_hover_tints_do_not_drop_text_below_aa():
    """card-hover must never REDUCE contrast.

    It used to: #283338 was lighter than card, so hovering an EntryCard took
    its loss-red P&L from 4.51:1 down to 4.20:1. There was no room to fix that
    by lightening loss — the arithmetic allows a hover surface no lighter than
    card at all — so the hover now goes marginally DARKER (#1d282e, the same
    perceptual step as before in the opposite direction), and every token gains
    contrast on hover instead of losing it.
    """
    t = tokens()
    assert _luminance(t["card-hover"]) <= _luminance(t["card"]), (
        f"card-hover {t['card-hover']} is lighter than card {t['card']} — every "
        f"text colour loses contrast on hover"
    )


# ── Status badges: 11 states, of which fixtures render ONE ─────────────────

BADGE = SRC / "components" / "shared" / "StatusBadge.tsx"
TRADING_COLORS = SRC / "lib" / "tradingColors.ts"


def _status_color_map() -> dict[str, str]:
    """status -> token name, parsed from statusColor()'s switch."""
    src = TRADING_COLORS.read_text()
    body = src[src.index("export function statusColor"):]
    body = body[: body.index("\n}")]
    out = {}
    for case, tok in re.findall(r'case "(\w+)":\s*(?:\n\s*)?return colors\.(\w+);', body):
        out[case] = tok
    return out


def _pill_mix() -> float:
    """The mix factor pillBackground() applies toward black."""
    src = BADGE.read_text()
    m = re.search(r"Math\.round\(v \* ([0-9.]+)\)", src)
    assert m, "pillBackground no longer mixes toward black"
    return float(m.group(1))


def test_every_status_badge_clears_aa():
    """Seven of eleven failed before this — worst 3.88:1 on `stopped`
    (Double Stop) and `failed` (Execution Failed).

    Only `skipped` ever appeared in the browser audit, because the synthetic
    fixtures never produce the other ten states. That is the same blind spot as
    the calendar's max-intensity heat step and the low-cushion readout: a probe
    can only score what the data makes it draw, so the exhaustive check has to
    be arithmetic.
    """
    t = tokens()
    camel = {"info": "info", "warning": "warning", "profit": "profit",
             "loss": "loss", "textDim": "text-dim", "textSecondary": "text-secondary",
             "profitMuted": "profit-muted"}
    mix = _pill_mix()
    statuses = _status_color_map()
    assert len(statuses) >= 10, f"only parsed {len(statuses)} statuses: {statuses}"

    for status, tok in sorted(statuses.items()):
        assert tok in camel, f"statusColor returns an unmapped token: {tok}"
        fg = t[camel[tok]]
        r, g, b = (int(fg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        pill = "#%02x%02x%02x" % tuple(round(v * mix) for v in (r, g, b))
        got = contrast(fg, pill)
        assert got >= AA_NORMAL, (
            f"badge '{status}' ({tok} {fg} on pill {pill}) is {got:.2f}:1"
        )


def test_the_pill_is_opaque_and_surface_independent():
    """Two reasons, both learned the hard way.

    An ALPHA pill composites with whatever is behind it, so the same badge
    passes on one surface and fails on another — and a self-tint can never
    reach AA for the loss red at any alpha. An opaque pill mixed toward black
    is strictly darker than every surface, so one check covers all of them.

    It must also not be a `backgroundImage` scrim: gradients are invisible to
    `getComputedStyle().backgroundColor`, so the browser probe cannot see them
    and reported a genuinely-fixed badge as still failing.
    """
    # Comments stripped: pillBackground's own docstring QUOTES the
    # `${color}20` self-tint it replaced, so a bare ban matched the
    # explanation. Ninth occurrence of that trap in this repo.
    src = re.sub(r"/\*.*?\*/", "", BADGE.read_text(), flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    style = src[src.index("backgroundColor: pillBackground"):][:200]
    assert "backgroundImage" not in style, (
        "the pill uses a gradient scrim; the contrast probe cannot measure it"
    )
    assert not re.search(r"\$\{color\}[0-9a-f]{2}", src), (
        "the pill is back to an alpha self-tint of its own text colour"
    )
