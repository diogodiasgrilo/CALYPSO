"""The strategy picker was the plainest control on the page, and it decided
what every number MEANT.

Reviewed 2026-09-18 by rendering the header at four widths and reading the real
taxonomy. Three problems, all structural rather than cosmetic:

1. THE NAV ROW MIXED TWO AXES. Dashboard / History / Analytics are views OF the
   selected strategy; the comparison links LEAVE that strategy and show a whole
   group. Rendered identically in one undifferentiated row, so nothing told a
   reader that clicking one abandoned their context.

2. A NATIVE <select> HID THE FLEET. Seven strategies run; the UI showed one name
   and made you open a menu to remember the others existed. At 390px it
   collapsed to "Brandon Na…" — the single most important piece of state,
   truncated to ambiguity.

3. FOUR NAMING CONVENTIONS ACROSS SEVEN ITEMS, and B and C differed only by a
   parenthetical:

       A  HYDRA Baseline            system name
       B  Brandon Narrow (7-slot)   person + parameter
       C  Brandon Narrow (3-slot)   person + parameter
       D  DC Time Machine           nickname
       E  SPY Double Calendar       instrument + structure
       F  Ghauri Mean Reversion     person + technique
       G  Strangle (0DTE Naked)     structure + qualifier

   Nothing said what any of them traded, or which carried undefined risk.

THE FIX RESTS ON ONE OBSERVATION: this whole project thinks in LETTERS. Every
doc, commit and conversation says "B is the live seat", "variant F", "D and E
are calendars". The letters lived in `short_name` and the UI never showed them.
A letter badge is the shortest unambiguous identity available and it survives
truncation, which is why the phone trigger can drop the name entirely.

`display_name` was deliberately NOT renamed. It is the ALERT IDENTITY on the
live seat — the taxonomy says so on B's entry — so changing it would change what
arrives in Telegram for the only variant that places real orders. The spec line
is a NEW `subtitle` field instead.

A REAL TRADE-OFF, MEASURED AND THEN CORRECTED: the first version showed the full
name on phone, which consumed the header and pushed the SPX price off screen
entirely. That is a worse exchange than the truncation it fixed, so the name is
hidden below `sm` and the badge carries the identity.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from tests.conftest import strip_comments

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "dashboard" / "frontend" / "src"
SWITCHER = SRC / "components" / "shared" / "StrategySwitcher.tsx"
HEADER = SRC / "components" / "layout" / "Header.tsx"
APP = SRC / "App.tsx"


# ── The taxonomy half ──────────────────────────────────────────────────────

def test_every_strategy_has_a_spec_line():
    """The subtitle is what tells a reader what a strategy actually trades —
    the "14-inch, M3" under the product name. Without it, B and C are
    distinguishable only by a parenthetical."""
    from shared.strategy_taxonomy import STRATEGIES

    missing = [sid for sid, m in STRATEGIES.items() if not getattr(m, "subtitle", "")]
    assert not missing, f"strategies with no subtitle: {missing}"


def test_subtitles_distinguish_the_two_brandons():
    """B and C share a name by design — the letter badge disambiguates them, the
    way the same MacBook Pro name covers two sizes. But the SPEC must differ, or
    the switcher shows two identical rows."""
    from shared.strategy_taxonomy import STRATEGIES

    b, c = STRATEGIES["b"].subtitle, STRATEGIES["c"].subtitle
    assert b and c and b != c, f"B and C have indistinguishable specs: {b!r} / {c!r}"


def test_subtitle_says_what_is_traded():
    """A spec that repeats the name is not a spec. Each should name an
    underlying or a structure."""
    from shared.strategy_taxonomy import STRATEGIES

    vague = []
    for sid, m in STRATEGIES.items():
        sub = (m.subtitle or "").lower()
        if not re.search(r"spx|spy|condor|calendar|strangle|vertical", sub):
            vague.append((sid, m.subtitle))
    assert not vague, f"subtitles that name neither an underlying nor a structure: {vague}"


def test_display_name_is_untouched_on_the_live_seat():
    """display_name is the alert identity for the ONLY variant placing real
    orders. The navigation revamp must not have changed what Telegram says."""
    from shared.strategy_taxonomy import STRATEGIES

    assert STRATEGIES["b"].display_name == "Brandon Narrow (7-slot)", (
        "B's display_name changed — that is the live seat's alert identity, not "
        "just a dashboard label"
    )


def test_the_meta_api_exposes_the_subtitle():
    """The switcher cannot render what the API does not send. This is also the
    gap that made the first prototype render blank spec lines — the fixtures
    predated the field."""
    import shared.strategy_taxonomy as tax
    from dashboard.backend.routers.strategies import _strategy_meta_dict

    d = _strategy_meta_dict(tax.STRATEGIES["b"])
    assert d.get("subtitle"), "meta row has no subtitle"


# ── The component half ─────────────────────────────────────────────────────

def test_the_letter_leads_IN_THE_TRIGGER():
    """Not merely "a badge exists somewhere in the file".

    The trigger's badge is the whole point: on a phone the name is hidden, so
    the badge is the ONLY identity on screen. Mutation testing caught this —
    deleting the badge from the trigger while leaving it in the popover rows
    passed the first version of this assertion, which would have shipped a
    phone header identifying nothing at all.
    """
    src = SWITCHER.read_text()
    assert "id.toUpperCase()" in src, "the badge does not render the variant letter"

    i = src.index('aria-label="Switch strategy"')
    trigger = src[i: src.index("</button>", i)]
    assert "LetterBadge" in trigger, (
        "the trigger has no letter badge — with the name hidden below `sm`, a "
        "phone header would identify nothing"
    )


def test_live_is_a_badge_not_a_string():
    """It used to be ` — LIVE` appended to the label, doing a badge's job with
    text, which left six of seven options looking identical in kind."""
    # strip_comments FIRST: this file's own docstring, and the component's,
    # both quote the ` — LIVE` string being banned. Tenth occurrence of that
    # trap today, which is why the helper now lives in conftest.
    src = strip_comments(SWITCHER.read_text())
    assert "LiveBadge" in src
    assert '" — LIVE"' not in src and "' — LIVE'" not in src


def test_the_phone_trigger_drops_the_name_not_the_market_data():
    """Measured: showing the full name at 390px pushed the SPX price off the
    header entirely. The badge is the identity, so the NAME is what gives way."""
    src = SWITCHER.read_text()
    m = re.search(r'<span className="([^"]*)"[^>]*>\s*\{current\?\.display_name', src)
    assert m, "could not find the trigger's name span"
    assert "max-sm:hidden" in m.group(1), (
        f"the name is still shown at phone width: {m.group(1)!r}"
    )


def test_the_switcher_replaced_the_select():
    """A native <select> cannot show a spec line, a badge, or per-row state."""
    assert "StrategySwitcher" in HEADER.read_text()
    assert "StrategyPicker" not in HEADER.read_text(), (
        "the old dropdown is still mounted"
    )


def test_the_popover_is_dismissible():
    """A popover with no way out is worse than a select."""
    src = SWITCHER.read_text()
    assert 'e.key === "Escape"' in src, "Escape does not close the switcher"
    assert "mousedown" in src, "clicking outside does not close the switcher"


def test_the_popover_is_announced():
    src = SWITCHER.read_text()
    assert 'role="listbox"' in src and 'role="option"' in src
    assert "aria-expanded" in src and "aria-selected" in src


# ── The navigation split ───────────────────────────────────────────────────

def test_comparisons_are_visually_separated_from_the_views():
    """The two axes must not read as one tab row. A rule plus a label is the
    minimum that says "these are a different kind of destination"."""
    src = APP.read_text()
    nav = src[src.index("<nav"):src.index("</nav>")]
    assert "Compare" in nav, "no label distinguishes the cross-strategy links"
    assert re.search(r"w-px|border-l", nav), (
        "no visual divider between the per-strategy views and the group "
        "comparisons — they still read as one undifferentiated row"
    )


@pytest.mark.parametrize("view", ["Dashboard", "History", "Analytics"])
def test_the_per_strategy_views_are_still_present(view):
    """CONTROL. The split must not have dropped a destination."""
    assert view in APP.read_text()
