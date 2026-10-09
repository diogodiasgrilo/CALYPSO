"""The journal's Cumulative Metrics block must say WHICH variant it describes.

The block is whole-file state holding one variant's lifetime — whichever one
holds the live paper seat (`agents_config.json` → `homer.read_db`). On a seat
swap it therefore **changes subject silently**. On 2026-10-09 the seat moved
from `b` (78 days) to `bl` (one day), so the next write would have replaced a
long record with a one-day number under an identical heading: indistinguishable
from the strategy having lost everything.

This was created BY fixing another bug. The block had been frozen since
2026-08-04 (a fixed 180-line lookup window outgrown by a section that grows
daily), so the swap looked harmless — "it never updates anyway". Repairing the
lookup re-armed the hazard the same day. A fix can resurrect a risk that an
earlier bug was masking.

Two defences, tested here:
  1. the heading names the variant, derived from `read_db` so it follows the
     seat with no code change;
  2. a boundary note ABOVE the heading, outside the replaced range, explaining
     that a drop across the boundary is a change of subject and where `b`'s
     record still lives.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.homer.journal_parser import JournalParser  # noqa: E402
from services.homer.journal_updater import update_cumulative_metrics  # noqa: E402

JOURNAL = ROOT / "docs/HYDRA_TRADING_JOURNAL.md"

METRICS = {
    "cumulative_pnl": 123.45,
    "total_entries": 2,
    "winning_days": 1,
    "losing_days": 0,
    "total_stops": 0,
    "last_updated": "2026-10-09",
}


def _doc(note: bool = True) -> str:
    rows = [f"- Feb {d}: 100 - 0 - 10 = 90 ✓" for d in range(1, 40)]
    boundary = ["> ⚠️ **The block below changes SUBJECT on a live-seat swap.**", ""] if note else []
    return "\n".join([
        "# Journal", "",
        "## 2. Daily Summary", "",
        "### P&L Verification Formula", "`f`", *rows, "",
        *boundary,
        "### Cumulative Metrics (hydra_metrics.json as of Aug 4 EOD)",
        "```json", '{"cumulative_pnl": 15628.27}', "```", "",
        "## 3. Next", "x", "",
    ])


class TestTheHeadingNamesTheVariant:

    def test_the_label_appears_in_the_heading(self):
        jp = JournalParser(_doc())
        update_cumulative_metrics(jp, METRICS, "Oct 9", source_label="variant bl")
        start, _ = jp.get_cumulative_metrics_range()
        assert "variant bl" in jp.lines[start], jp.lines[start]

    def test_the_stale_date_is_replaced(self):
        jp = JournalParser(_doc())
        update_cumulative_metrics(jp, METRICS, "Oct 9", source_label="variant bl")
        text = "\n".join(jp.lines)
        assert "Aug 4 EOD" not in text
        assert "Oct 9 EOD" in text

    def test_the_figures_are_actually_written(self):
        """Guards against a heading-only change that leaves the stale number."""
        jp = JournalParser(_doc())
        update_cumulative_metrics(jp, METRICS, "Oct 9", source_label="variant bl")
        text = "\n".join(jp.lines)
        assert "123.45" in text
        assert "15628.27" not in text

    def test_omitting_the_label_keeps_the_old_heading(self):
        """Back-compat: the parameter is optional."""
        jp = JournalParser(_doc())
        update_cumulative_metrics(jp, METRICS, "Oct 9")
        start, _ = jp.get_cumulative_metrics_range()
        assert jp.lines[start] == "### Cumulative Metrics (hydra_metrics.json as of Oct 9 EOD)"

    def test_a_relabel_to_a_different_variant_takes_effect(self):
        """A later swap must not leave the previous variant's name behind."""
        jp = JournalParser(_doc())
        update_cumulative_metrics(jp, METRICS, "Oct 9", source_label="variant bl")
        update_cumulative_metrics(jp, METRICS, "Oct 10", source_label="variant c")
        start, _ = jp.get_cumulative_metrics_range()
        assert "variant c" in jp.lines[start]
        assert "variant bl" not in jp.lines[start]


class TestTheBoundaryNoteSurvivesTheRewrite:

    def test_the_note_is_outside_the_replaced_range(self):
        jp = JournalParser(_doc())
        start, end = jp.get_cumulative_metrics_range()
        idx = next(i for i, l in enumerate(jp.lines) if "changes SUBJECT" in l)
        assert not (start <= idx <= end), "note sits inside the range HOMER overwrites"

    def test_the_note_is_still_present_after_an_update(self):
        jp = JournalParser(_doc())
        update_cumulative_metrics(jp, METRICS, "Oct 9", source_label="variant bl")
        assert any("changes SUBJECT" in l for l in jp.lines)

    def test_it_works_even_with_no_note(self):
        """The note is documentation, not a precondition."""
        jp = JournalParser(_doc(note=False))
        update_cumulative_metrics(jp, METRICS, "Oct 9", source_label="variant bl")
        start, _ = jp.get_cumulative_metrics_range()
        assert "variant bl" in jp.lines[start]


class TestTheRealJournal:

    def test_the_committed_journal_carries_the_boundary_note(self):
        text = JOURNAL.read_text()
        assert "changes SUBJECT on a live-seat swap" in text
        assert "data/variant_b/hydra_metrics.json" in text, \
            "the note must say where b's record still lives"

    def test_the_note_precedes_the_block_in_the_real_file(self):
        jp = JournalParser(JOURNAL.read_text())
        rng = jp.get_cumulative_metrics_range()
        assert rng is not None
        idx = next(i for i, l in enumerate(jp.lines) if "changes SUBJECT" in l)
        assert idx < rng[0], (idx, rng)

    def test_an_update_to_the_real_journal_keeps_the_note_and_labels_it(self):
        """End-to-end on the committed file — what HOMER will do tonight."""
        jp = JournalParser(JOURNAL.read_text())
        update_cumulative_metrics(jp, METRICS, "Oct 9", source_label="variant bl")
        start, _ = jp.get_cumulative_metrics_range()
        assert "variant bl" in jp.lines[start]
        assert any("changes SUBJECT" in l for l in jp.lines)
        # and the P&L list is untouched by a cumulative-block write
        assert jp.get_pnl_verification_range() is not None
