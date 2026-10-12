"""Every strategy's entry cards render the same way — one path, not two.

Reported from a phone 2026-10-12: variant `bl` (the live seat) showed proper
per-slot entry cards, while every OTHER strategy collapsed that whole section
into a single squashed card.

Cause: `EntryGrid` carried TWO render paths. The primary (live-seat) path drew
one card per CONFIGURED slot, filling each with an `EntryCard` or a
`PendingSlot`. Polled non-primary strategies took an early return that drew
only the positions that actually opened and swept every skipped entry into one
compact strip — so a day that skipped everything rendered as exactly one card.

That early return existed for a stated reason:

    "We don't have the selected strategy's own schedule client-side"

**That premise stopped being true on 2026-10-09**, when `useBotConfig()` was
scoped to the selected strategy (it now sends `strategy_id`). The degenerate
path was a workaround for a limitation that no longer existed, so the two paths
are merged and the workaround is gone.

Two things had to be preserved, and they are what these tests mostly check:

1. **The no-hiding guarantee.** The deleted path was itself a fix, from
   2026-06-17: deriving the slot count from an unscoped config hid any entry
   beyond variant A's 2 slots — hiding its P&L too, which made a losing day
   look like a winning one. The merged path renders per-slot, so an entry whose
   number falls outside the configured slots must still appear. It does, in an
   explicit "Additional entries" block.
2. **The runtime schedule must not come from the canonical store** while
   rendering a polled strategy — `hydraState.entry_schedule` belongs to the
   live seat, and applying its VIX-regime cap to another strategy's slots is
   the same defect fixed in `EntryTimeline` on 2026-10-09.

⚠️ HONEST LIMIT: this project has no JavaScript test runner (package.json
scripts are dev/build/lint/preview, zero test deps), so these are SOURCE
assertions. They prove the structure is present, never that it renders — the
rendering was verified by typecheck, lint, a clean build, and the emitted
bundle. Treat a pass here as necessary, not sufficient.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GRID = ROOT / "dashboard/frontend/src/components/entries/EntryGrid.tsx"
TIMELINE = ROOT / "dashboard/frontend/src/components/entries/EntryTimeline.tsx"


@pytest.fixture(scope="module")
def src() -> str:
    return GRID.read_text()


class TestOneRenderPath:
    """The whole bug was that polled strategies took a different path."""

    def test_there_is_no_entriesProp_early_return(self, src):
        assert "if (entriesProp) {" not in src, (
            "the degenerate non-primary path is back — polled strategies will "
            "collapse their entry section into one card again")

    def test_the_degenerate_skip_strip_is_gone(self, src):
        for token in ("groupSkipsByReason", "isFullySkipped", "Skipped — no position"):
            assert token not in src, f"{token} survived; skips should occupy slot cards"

    def test_slots_are_rendered_from_the_config_schedule(self, src):
        """Per-slot rendering is what makes every strategy look like bl."""
        assert "config.entry_times" in src
        assert "activeBaseTimes.map" in src
        assert "PendingSlot" in src, "unfired slots must still draw a card"


class TestTheRuntimeScheduleIsNotTheLiveSeats:

    def test_the_store_schedule_is_gated_on_entriesProp(self, src):
        assert re.search(
            r"const schedule = entriesProp \? undefined : hydraState\?\.entry_schedule", src), (
            "a polled strategy would take the LIVE SEAT's runtime schedule and "
            "apply its VIX-regime cap to another strategy's slots")

    def test_the_timeline_has_the_same_guard(self):
        """Same defect, same family — pinned together so neither regresses alone."""
        assert "entriesProp ? undefined : hydraState?.entry_schedule" in TIMELINE.read_text()


class TestNoHidingGuarantee:
    """The deleted path WAS a fix (2026-06-17). Its invariant must survive."""

    def test_entries_outside_the_configured_slots_still_render(self, src):
        assert "Additional entries" in src
        assert "orphans" in src

    def test_the_orphan_set_covers_base_AND_conditional_slots(self, src):
        """Counting only base slots would re-hide every conditional entry."""
        block = src[src.index("NO-HIDING GUARANTEE"):]
        assert "activeBaseTimes.map(effectiveBaseNum)" in block
        assert "activeCondTimes.map(effectiveCondNum)" in block

    def test_orphans_are_matched_by_entry_number_not_index(self, src):
        block = src[src.index("NO-HIDING GUARANTEE"):]
        assert "e.entry_number != null" in block, (
            "an entry with no number must not be treated as entry #0 and "
            "silently swallowed by the slotted set")


class TestMobileLayout:
    """The reported symptom was mobile. Every grid must drop to one column."""

    def test_every_grid_container_collapses_to_one_column_on_phones(self, src):
        containers = re.findall(r'className=\{?["`][^"`]*\bgrid\b[^"`]*["`]\}?', src)
        grids = [c for c in containers if "grid-cols" in c or "${baseColsClass}" in c
                 or "${condColsClass}" in c]
        assert grids, "no grid containers found — the selector is wrong, not the code"
        for c in grids:
            assert "max-sm:grid-cols-1" in c, f"grid without a phone breakpoint: {c}"

    def test_desktop_column_counts_are_static_strings(self, src):
        """Tailwind's JIT cannot compile a dynamic `grid-cols-` + n — it emits
        NO columns at all, which is its own squashed-layout bug.

        Comments are stripped first: this file deliberately DISCUSSES the
        dynamic form in a warning comment, and an earlier version of this test
        matched that comment and failed on correct code. A test that fires on
        prose is not testing the code."""
        code = re.sub(r"/\*[\s\S]*?\*/", "", src)          # block comments
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)     # line comments
        assert "grid-cols-${" not in code

    def test_the_widest_grid_steps_down_before_phone_width(self, src):
        """7 slots at 4 columns would be unreadable on a tablet."""
        assert "grid-cols-4 max-lg:grid-cols-3" in src
