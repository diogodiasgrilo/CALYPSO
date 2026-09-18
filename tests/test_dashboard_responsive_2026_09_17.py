"""The dashboard must not break on a phone.

Every prior audit in this project measured ONE viewport (1440x1000) and reported
"responsive layout is sound — zero horizontal overflow". Measured across real
device widths, that claim was false:

    viewport      body / content     verdict
    iPhone 14      390 /  566        45% OVERFLOW — page scrolls sideways
    iPad           820 /  820        ok
    laptop        1440 / 1440        ok
    wide          1920 / 1920        ok

The dashboard is PWA-installable and ships an iOS Scriptable widget, so phones
are an intended surface, not an edge case.

THREE ROOT CAUSES, all in the chrome rather than page content:

  1. NavTabs — five tabs (three fixed + one per comparable group) need ~520px
     in a non-wrapping flex row. Hiding tabs would hide whole pages, so the
     strip scrolls horizontally instead: the standard mobile pattern.
  2. StrategyPicker — a `<select>` claims the intrinsic width of its LONGEST
     option ("Brandon Narrow (7-slot) — LIVE"), 238px inside a 171px slot.
  3. Header groups could not shrink at all.

AND A LESSON WORTH KEEPING, because the first fix attempt made things worse:
`min-w-0` belongs ONLY on a flex child whose content can actually truncate.
Applied to the icon groups it let flex shrink them below their icons' width, so
the mute and logout buttons spilled PAST the viewport — a shrink that content
cannot absorb just relocates the overflow.

The browser measurements live in `dashboard/frontend/uiaudit/viewports.mjs`
(overflow per viewport) and `diag-a11y.mjs` (tap targets, truncation titles).
This file pins the structural rules those measurements established, so a
regression fails the normal suite without needing a browser.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "dashboard" / "frontend" / "src"
APP = SRC / "App.tsx"
HEADER = SRC / "components" / "layout" / "Header.tsx"
SWITCHER = SRC / "components" / "shared" / "StrategySwitcher.tsx"
CSS = SRC / "index.css"
COMPARISON = SRC / "pages" / "Comparison.tsx"
CAL_COMPARISON = SRC / "components" / "comparison" / "CalendarComparison.tsx"


# ─────────────────────────────────────────────────────────────────────────────
# NavTabs — scroll, never hide
# ─────────────────────────────────────────────────────────────────────────────

def test_nav_strip_scrolls_horizontally():
    src = APP.read_text()
    nav = src[src.index("<nav"): src.index(">", src.index("<nav"))]
    assert "overflow-x-auto" in nav, (
        "the tab strip no longer scrolls — five tabs need ~520px and a phone "
        "has 390px, so the page goes wider than the viewport again."
    )


def test_tabs_keep_their_width_inside_the_strip():
    """Without `shrink-0` flex compresses the labels instead of scrolling."""
    src = APP.read_text()
    block = src[src.index("const linkClass"): src.index("const comparableGroups")]
    assert "shrink-0" in block and "whitespace-nowrap" in block


def test_no_tab_is_hidden_on_small_screens():
    """Hiding a tab hides a whole page. Scrolling is the correct answer."""
    src = APP.read_text()
    block = src[src.index("const linkClass"): src.index("</nav>")]
    assert "max-sm:hidden" not in block, (
        "a nav tab is hidden on small screens — that removes a page rather "
        "than making it reachable."
    )


def test_scrollbar_chrome_is_suppressed_for_the_strip():
    css = CSS.read_text()
    assert ".nav-scroll" in css
    block = css[css.index(".nav-scroll"):]
    assert "scrollbar-width: none" in block


# ─────────────────────────────────────────────────────────────────────────────
# The min-w-0 rule — the mistake that made the first attempt worse
# ─────────────────────────────────────────────────────────────────────────────

def _header_group_lines() -> list[str]:
    src = HEADER.read_text()
    body = src[src.index("<header"):]
    return [l for l in body.splitlines() if "flex items-center" in l and "gap-" in l]


def test_min_w_0_only_where_content_can_truncate():
    """`min-w-0` lets a flex child shrink below its content. That is right for
    the picker (it truncates) and WRONG for the icon groups (icons cannot),
    where it pushed the buttons past the viewport edge."""
    src = HEADER.read_text()
    # The centre (prices) and right (icons) groups must NOT be shrinkable.
    centre = src[src.index("{/* Center: underlying + VIX"):]
    centre = centre[: centre.index(">")]
    assert "shrink-0" in centre and "min-w-0" not in centre, (
        "the price group is shrinkable again — its content cannot truncate, so "
        "shrinking it just relocates the overflow."
    )
    right = src[src.index("{/* Right: Market status + mute"):]
    right = right[: right.index(">")]
    assert "shrink-0" in right and "min-w-0" not in right, (
        "the icon group is shrinkable again — the mute and logout buttons will "
        "spill past the viewport."
    )


def test_picker_group_can_shrink():
    src = HEADER.read_text()
    left = src[src.index("{/* Left: Logo + title"):]
    left = left[: left.index("\n", left.index("<div"))]
    assert "min-w-0" in left, (
        "the picker's group can no longer shrink, so the select's intrinsic "
        "width (its longest option) forces the header wider than the phone."
    )


def test_picker_width_is_capped_more_tightly_on_phones():
    # REWRITTEN 2026-09-18. The old assertion was about a `<select>`, which
    # claims the intrinsic width of its LONGEST option and therefore needed an
    # explicit max-width cap. The switcher that replaced it is a button sized by
    # its own content, so that cap is not just unnecessary — the mechanism it
    # guarded no longer exists.
    #
    # The PROPERTY still matters and is unchanged: the trigger must not blow out
    # a 390px header. Measured — showing the full name there pushed the SPX price
    # off screen entirely — so the name is hidden below `sm` and the letter badge
    # carries the identity.
    src = SWITCHER.read_text()
    assert "max-sm:hidden" in src, (
        "the switcher shows its full name at phone width; that pushed the SPX "
        "price off the header when it was measured."
    )
    assert "min-w-0" in src, "the switcher cannot shrink inside its header group"


def test_wordmark_yields_before_information():
    """What gets dropped on a phone must be the least information-bearing
    chrome. The logo already brands the page; the strategy name does not."""
    src = HEADER.read_text()
    wordmark = src[src.index(">\n          HYDRA") - 400: src.index(">\n          HYDRA")]
    assert "max-sm:hidden" in wordmark, "the HYDRA wordmark no longer yields on phones"


# ─────────────────────────────────────────────────────────────────────────────
# Touch and accessibility
# ─────────────────────────────────────────────────────────────────────────────

def test_icon_only_buttons_have_accessible_names():
    """Two 16x16 icon buttons had NO accessible name — unusable with a screen
    reader and hard to hit with a thumb."""
    src = HEADER.read_text()
    assert 'aria-label={muted ? "Unmute alert sounds" : "Mute alert sounds"}' in src
    assert 'aria-label="Sign out"' in src


def test_icon_buttons_have_a_real_hit_area():
    """Padding enlarges the tap target without changing the icon's look."""
    src = HEADER.read_text()
    mute = src[src.index("onClick={handleMuteToggle}"):]
    mute = mute[: mute.index(">")]
    assert "p-2" in mute, "the mute button is back to a 16x16 tap target"


def test_touch_targets_are_taller_on_phones():
    assert "max-sm:py-2.5" in APP.read_text(), "nav tabs lost their touch height"
    # The switcher sets an explicit 44px floor (min-h-11) rather than relying on
    # padding, which is stronger: it survives a change to the type scale.
    assert "min-h-11" in SWITCHER.read_text(), (
        "the strategy switcher lost its 44px tap floor"
    )


@pytest.mark.parametrize("path,expr", [
    (COMPARISON, "title={v.label}"),
    (CAL_COMPARISON, "title={m.display_name}"),
])
def test_truncated_labels_carry_a_title(path, expr):
    """Truncation is deliberate in a narrow column, but with no title the rest
    of the name is unrecoverable on a phone — where the column is narrowest."""
    assert expr in path.read_text(), (
        f"{path.name} truncates a strategy label with no title attribute"
    )
