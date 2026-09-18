"""HOMER must not eat human-written journal content.

On 2026-09-17 a definition-change note was added to section 5 of the trading
journal at the operator's request, recording why reported ROI stepped up. HOMER
ran that evening and **deleted it** — it rebuilds sections 1, 2, 3, 4, 5, 8 and
9 from scratch (`new_lines = [...]`), so anything a human adds there survives
only until the next nightly run.

Nothing warned anyone. It was noticed the following morning purely because a
`git push` was rejected and the merge was inspected.

So: human-written content lives in section 10, which HOMER does not manage, and
this test fails if it disappears. A note whose whole purpose is explaining a
change in published numbers is exactly the thing that must not vanish silently.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JOURNAL = ROOT / "docs" / "HYDRA_TRADING_JOURNAL.md"
HOMER = ROOT / "services" / "homer"

#: Sections HOMER rebuilds wholesale — never put human content in these.
HOMER_MANAGED = {1, 2, 3, 4, 5, 8, 9}


def test_journal_exists():
    assert JOURNAL.exists()


def test_definition_changes_section_is_present():
    src = JOURNAL.read_text()
    assert "## 10. Reporting Definition Changes" in src, (
        "the human-owned definition-change section is gone — if HOMER ate it "
        "again, it was moved back into a managed section."
    )


@pytest.mark.parametrize("marker", [
    "reported capital is now PEAK CONCURRENT",      # D20, 2026-09-17
    "a skipped entry no longer counts as deployed capital",   # 2026-09-18
])
def test_each_recorded_definition_change_survives(marker):
    assert marker in JOURNAL.read_text(), (
        f"the '{marker}' note is missing — a published number changed and the "
        f"written reason for it has been lost."
    )


def test_the_note_is_outside_every_homer_managed_section():
    """Position matters, not just presence: inside a managed section it is
    deleted on the next nightly run."""
    src = JOURNAL.read_text()
    headings = [(m.start(), m.group(1)) for m in re.finditer(r"^## (\d+)\.", src, re.M)]
    note_at = src.index("## 10. Reporting Definition Changes")
    owning = None
    for pos, num in headings:
        if pos <= note_at:
            owning = int(num)
    assert owning == 10, f"the note now sits under section {owning}"
    assert owning not in HOMER_MANAGED


def test_homer_still_only_manages_the_expected_sections():
    """If HOMER starts rebuilding section 10 too, this test is the warning."""
    src = "\n".join(p.read_text() for p in HOMER.glob("*.py"))
    written = {int(m.group(1)) for m in re.finditer(r'"## (\d+)\. ', src)}
    assert written <= HOMER_MANAGED, (
        f"HOMER now rebuilds section(s) {sorted(written - HOMER_MANAGED)} — if "
        f"that includes 10, human notes are being destroyed again."
    )
