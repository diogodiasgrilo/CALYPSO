"""The journal parser must not locate blocks with fixed line-count windows.

Found 2026-10-09 while verifying the `bl` seat swap's downstream effects. Two
lookups in `JournalParser` searched `section_2_start + <constant>` lines.
Section 2 grows by one line per trading day, so both constants were outgrown —
and the failures were SILENT, in opposite ways:

  * `get_cumulative_metrics_range` (window 180) — the heading drifted 31 lines
    past the window and returned None, so the updater skipped the block from
    **2026-08-04** onward while HOMER logged a WARNING and reported success.
    The journal advertised `cumulative_pnl: 15628.27` for two months against an
    actual, epoch-rebased **-432.10**: a ~$16k error on the number most likely
    to be quoted out of the journal.

  * `get_pnl_verification_range` (inner window 30) — did NOT return None. It
    returned a WRONG end, so `add_pnl_verification`, which inserts at
    `end + 1`, wrote every new day at the same mid-list line. By 2026-10-09
    **61 rows sat in reverse chronological order spliced between Mar 19 and
    Mar 20.** A wrong answer is worse than no answer: nothing logged at all.

Both now bound their scan with `_section_scan_end` (next `## ` heading, else
EOF). The important tests here are the GROWTH ones: they synthesise a journal
with a list longer than any constant, which is what the real file will keep
becoming.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.homer.journal_parser import JournalParser  # noqa: E402

JOURNAL = ROOT / "docs/HYDRA_TRADING_JOURNAL.md"
ROW = re.compile(r"^- ([A-Z][a-z]{2}) (\d{1,2}):")
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _synthetic(n_rows: int) -> str:
    """A journal whose P&L list is `n_rows` long — deliberately longer than any
    constant the parser used to rely on."""
    rows = [f"- Feb {d}: 100 - 0 - 10 = 90 ✓" for d in range(1, n_rows + 1)]
    return "\n".join([
        "# HYDRA Trading Journal", "",
        "## 1. Overview", "text", "",
        "## 2. Daily Summary", "",
        "| Metric | Feb 1 |", "|---|---|", "| P&L | 90 |", "",
        "### P&L Verification Formula",
        "`Daily P&L = Expired Credits - Stop Loss Debits - Commission`",
        *rows, "",
        "### Cumulative Metrics (hydra_metrics.json as of Feb 1 EOD)",
        "```json", '{"cumulative_pnl": 90}', "```", "",
        "## 3. Entry Detail", "text", "",
    ])


class TestGrowthDoesNotBreakTheLookups:
    """The regression that matters: a list longer than the old constants."""

    @pytest.mark.parametrize("n", [5, 29, 31, 150, 181, 400])
    def test_cumulative_block_is_found_at_any_list_length(self, n):
        jp = JournalParser(_synthetic(n))
        rng = jp.get_cumulative_metrics_range()
        assert rng is not None, f"lost the cumulative block at {n} rows"
        assert "### Cumulative Metrics" in jp.lines[rng[0]]
        assert jp.lines[rng[1]].strip() == "```"

    @pytest.mark.parametrize("n", [5, 29, 31, 150, 181, 400])
    def test_pnl_range_ends_on_the_TRUE_last_row(self, n):
        """`add_pnl_verification` inserts at end+1, so a short end silently
        writes into the middle of the list."""
        jp = JournalParser(_synthetic(n))
        rng = jp.get_pnl_verification_range()
        assert rng is not None, f"lost the P&L block at {n} rows"
        assert jp.lines[rng[1]] == f"- Feb {n}: 100 - 0 - 10 = 90 ✓", (
            f"{n} rows: end points at {jp.lines[rng[1]]!r}, not the last row"
        )

    def test_the_insertion_point_is_after_the_last_row_not_inside_the_list(self):
        jp = JournalParser(_synthetic(120))
        _, end = jp.get_pnl_verification_range()
        assert not ROW.match(jp.lines[end + 1] or ""), (
            "the next line is another row — a new day would be inserted mid-list"
        )

    def test_a_block_outside_the_section_is_not_matched(self):
        """`_section_scan_end` must stop at the next `## ` heading, or a lookup
        could reach into a later section and update the wrong lines."""
        doc = _synthetic(40).replace(
            "## 3. Entry Detail",
            "## 3. Entry Detail\n\n### P&L Verification Formula\n- Dec 31: 1 - 0 - 0 = 1")
        jp = JournalParser(doc)
        _, end = jp.get_pnl_verification_range()
        assert "Dec 31" not in jp.lines[end], "matched a heading in section 3"


class TestScanEndIsStructural:

    def test_it_stops_at_the_next_top_level_heading(self):
        jp = JournalParser(_synthetic(40))
        s2 = jp.get_section_start(2)
        stop = jp._section_scan_end(s2)
        assert jp.lines[stop].startswith("## ") and not jp.lines[stop].startswith("### ")

    def test_it_returns_EOF_when_there_is_no_next_section(self):
        jp = JournalParser("## 2. Daily Summary\n- Feb 1: 1 - 0 - 0 = 1\n")
        assert jp._section_scan_end(0) == len(jp.lines)

    def test_sub_headings_do_not_terminate_the_scan(self):
        jp = JournalParser("## 2. S\n### sub\n### sub2\n## 3. T\n")
        assert jp._section_scan_end(0) == 3


class TestTheRealJournalIsHealthy:
    """Invariants on the committed file. These would have caught the splice in
    July, the day it started."""

    def _rows(self):
        lines = JOURNAL.read_text().split("\n")
        h = next(i for i, l in enumerate(lines) if "### P&L Verification Formula" in l)
        e = next(i for i, l in enumerate(lines) if "### Cumulative Metrics" in l)
        return [l for l in lines[h:e] if ROW.match(l)]

    def test_the_pnl_list_is_in_chronological_order(self):
        keys = [(MONTHS[m.group(1)], int(m.group(2)))
                for l in self._rows() if (m := ROW.match(l))]
        bad = [(a, b) for a, b in zip(keys, keys[1:]) if b < a]
        assert not bad, f"{len(bad)} out-of-order transition(s), e.g. {bad[:3]}"

    def test_no_duplicate_dates(self):
        keys = [(m.group(1), m.group(2)) for l in self._rows() if (m := ROW.match(l))]
        dupes = {k for k in keys if keys.count(k) > 1}
        assert not dupes, dupes

    def test_both_lookups_resolve_on_the_real_file(self):
        jp = JournalParser(JOURNAL.read_text())
        assert jp.get_pnl_verification_range() is not None
        assert jp.get_cumulative_metrics_range() is not None

    def test_every_section_2_lookup_still_resolves(self):
        """A sweep, so the next constant to be outgrown fails HERE, loudly,
        instead of silently in production."""
        jp = JournalParser(JOURNAL.read_text())
        for name in ("get_section2_table_range", "get_pnl_verification_range",
                     "get_cumulative_metrics_range", "get_section1_range",
                     "get_section5_range"):
            assert getattr(jp, name)() is not None, f"{name} returned None"
