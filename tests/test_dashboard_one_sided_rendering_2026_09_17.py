"""A one-sided entry must never render the side it did not trade.

Phase 8 / defect D7. F (Ghauri) places a true one-sided vertical and stores the
absent side at ZERO — verified against its real record:

    date        entry_type   SC     LC     SP      LP      call_credit  put_credit
    2026-09-15  put_only     0.0    0.0    7550.0  7540.0     0.0         127.5

Any renderer that draws both sides unguarded therefore prints a call spread at
strike 0 for $0 — a leg that does not exist.

FINDING, recorded because the plan was wrong about it. `DASHBOARD_REBUILD_PLAN`
§8 stated "F still renders a phantom call leg wherever entries are drawn". It
does not. Rendering F's real entry shows a PUT ONLY badge, "C SKIPPED", and
"C:skipped" beside "P:7550/7540". The guards landed in fb99151 and de4b895 on
2026-06-15/16, three months before the plan claimed the bug was live.

So this file is a REGRESSION GUARD over behaviour that already works, not a fix
— plus one genuine hardening in SessionReplay, which guarded on the recorded
`entry_type` string alone. That string can be blank on a legacy row, and the
absent side is stored at 0.0, so `entry_type` by itself would have rendered a
phantom "C:0". It now also requires the strike to be positive.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402

SRC = ROOT / "dashboard" / "frontend" / "src"

#: Every component that renders a short-call strike. If a new one appears it
#: must be added here WITH a guard — that is the point of the inventory test.
RENDERERS = {
    "components/entries/EntryCard.tsx",
    "components/dashboard/IronCondorDashboard.tsx",
    "components/dashboard/icEntryView.tsx",
    "components/history/DayDetailEntries.tsx",
    "components/history/SessionReplay.tsx",
    "components/market/SPXChart.tsx",
    "components/market/PositionHeatmap.tsx",
    "pages/Comparison.tsx",
}

#: Any of these constitutes a guard against drawing an absent side.
GUARD_PATTERNS = (
    "short_call_strike > 0",
    "short_put_strike > 0",
    "call_side_skipped",
    "put_side_skipped",
    "put_only",
    "call_only",
)


def _renderers_on_disk() -> set[str]:
    out = set()
    for p in SRC.rglob("*.tsx"):
        if "short_call_strike" in p.read_text():
            out.add(str(p.relative_to(SRC)))
    return out


def test_f_is_the_one_sided_strategy():
    assert tax.meta("f").sides == "one_sided"
    for vid in ("a", "b", "c", "g"):
        assert tax.meta(vid).sides == "two_sided"


def test_renderer_inventory_is_complete():
    """A NEW component that draws a call strike must be added to RENDERERS and
    guarded. Without this the suite silently stops covering the new one."""
    on_disk = _renderers_on_disk()
    missing = on_disk - RENDERERS
    assert not missing, (
        f"new call-strike renderer(s) not covered by this test: {sorted(missing)} "
        f"— add them to RENDERERS and confirm they guard the absent side."
    )
    gone = RENDERERS - on_disk
    assert not gone, f"RENDERERS lists files that no longer draw a call strike: {sorted(gone)}"


@pytest.mark.parametrize("rel", sorted(RENDERERS))
def test_every_renderer_guards_the_absent_side(rel):
    src = (SRC / rel).read_text()
    assert any(g in src for g in GUARD_PATTERNS), (
        f"{rel} draws a short-call strike with no one-sided guard — F's "
        f"put_only entry stores short_call_strike 0.0, so it would render a "
        f"phantom call spread at strike 0 for $0."
    )


def test_entry_card_shows_a_one_sided_badge():
    src = (SRC / "components" / "entries" / "EntryCard.tsx").read_text()
    assert "PUT ONLY" in src and "CALL ONLY" in src, (
        "the one-sided badge is gone — a reader cannot tell a deliberate "
        "one-sided entry from a half-failed iron condor."
    )


def test_entry_card_gates_the_strike_line_on_a_positive_strike():
    """Guarding on skip-flags alone is not enough: the absent side is stored at
    0.0, and a 0 strike must never be printed."""
    src = (SRC / "components" / "entries" / "EntryCard.tsx").read_text()
    assert "!(entry.short_call_strike > 0)" in src
    assert "!(entry.short_put_strike > 0)" in src


def test_session_replay_guards_on_the_strike_not_only_entry_type():
    """The one genuine hardening here. entry_type is a recorded string and can
    be blank on a legacy row; the strike cannot lie."""
    src = (SRC / "components" / "history" / "SessionReplay.tsx").read_text()
    assert "!(e.short_call_strike > 0)" in src, (
        "SessionReplay guards only on entry_type — a row with a blank "
        "entry_type and a 0 strike renders a phantom 'C:0'."
    )
    assert "!(e.short_put_strike > 0)" in src


def test_position_heatmap_requires_a_positive_strike_to_draw_a_bar():
    src = (SRC / "components" / "market" / "PositionHeatmap.tsx").read_text()
    for side in ("call", "put"):
        assert re.search(rf"has{side.capitalize()}\s*=\s*e\.short_{side}_strike > 0", src), (
            f"the {side} bar is drawn without checking the strike is positive — "
            f"a one-sided entry would paint a bar at 0."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Rules of hooks — the latent crash in the same component
# ─────────────────────────────────────────────────────────────────────────────

def test_entry_card_hooks_run_before_the_early_returns():
    """EntryCard called useAnimatedNumber TWICE after two early returns.

    It never actually crashed, and a three-arm repro showed why: React's
    didRenderTooFewHooks check is `currentHook !== null && ...`, so a branch
    calling ZERO hooks slips through. It was benign only because nothing else
    hooked above those returns — adding any hook there would have turned a
    routine transition (an entry becoming execution_failed mid-session, as one
    did on 2026-08-03) into "Rendered fewer hooks than expected".

    computeEntryPnl is pure, so hoisting costs nothing.
    """
    src = (SRC / "components" / "entries" / "EntryCard.tsx").read_text()
    body = src[src.index("export function EntryCard("):]
    first_hook = body.index("useAnimatedNumber(")
    first_return = body.index("return (")
    assert first_hook < first_return, (
        "useAnimatedNumber is called after an early return again — React Hooks "
        "must run in the same order on every render."
    )
