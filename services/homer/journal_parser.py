"""
HOMER journal parser — reads the HYDRA Trading Journal and locates sections,
tables, insertion points, and existing dates.
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class JournalParser:
    """Parses the HYDRA Trading Journal markdown structure."""

    def __init__(self, content: str):
        """Parse journal content into lines and locate all section headers."""
        self.lines = content.split("\n")
        self._section_starts: Dict[int, int] = {}
        self._parse_sections()

    def _parse_sections(self):
        """Find starting line indices for each major section."""
        for i, line in enumerate(self.lines):
            # Match ## N. or ## Appendix X:
            m = re.match(r"^## (\d+)\.", line)
            if m:
                self._section_starts[int(m.group(1))] = i
            elif line.startswith("## Appendix "):
                letter = line.split("## Appendix ")[1][0]
                self._section_starts[ord(letter) - ord("A") + 100] = i

        logger.info(
            f"Parsed journal: {len(self.lines)} lines, "
            f"sections at: {self._section_starts}"
        )

    def get_section_start(self, section_num: int) -> Optional[int]:
        """Get the starting line index for a section number (1-9)."""
        return self._section_starts.get(section_num)

    def get_appendix_start(self, letter: str) -> Optional[int]:
        """Get the starting line index for an appendix (A-H)."""
        return self._section_starts.get(ord(letter.upper()) - ord("A") + 100)

    # =========================================================================
    # SECTION 2: DAILY SUMMARY TABLE
    # =========================================================================

    def get_existing_dates_from_section2(self) -> List[str]:
        """
        Extract existing date columns from Section 2 table header row.

        Returns:
            List of month-day strings like ["Feb 10", "Feb 11", ...].
        """
        sec2_start = self.get_section_start(2)
        if sec2_start is None:
            return []

        # Find the header row (starts with "| Column |")
        for i in range(sec2_start, min(sec2_start + 20, len(self.lines))):
            if self.lines[i].strip().startswith("| Column |"):
                # Parse date columns from header
                parts = [p.strip() for p in self.lines[i].split("|")]
                # Skip empty first, "Column", then collect dates
                dates = []
                for p in parts[2:]:
                    p = p.replace("**", "").strip()
                    if p and p != "":
                        dates.append(p)
                return dates
        return []

    def get_section2_table_range(self) -> Optional[Tuple[int, int]]:
        """
        Get the line range (start, end) of the Section 2 data table.
        start = the "| Column |" header line, end = last row before next section.
        """
        sec2_start = self.get_section_start(2)
        if sec2_start is None:
            return None

        table_start = None
        table_end = None

        for i in range(sec2_start, min(sec2_start + 100, len(self.lines))):
            if self.lines[i].strip().startswith("| Column |"):
                table_start = i
            elif table_start is not None:
                if self.lines[i].strip().startswith("|"):
                    table_end = i
                elif table_end is not None:
                    break

        if table_start is not None and table_end is not None:
            return (table_start, table_end)
        return None

    def get_pnl_verification_range(self) -> Optional[Tuple[int, int]]:
        """Find the P&L Verification Formula section range."""
        sec2_start = self.get_section_start(2)
        if sec2_start is None:
            return None

        start = None
        for i in range(sec2_start, min(sec2_start + 150, len(self.lines))):
            if "### P&L Verification Formula" in self.lines[i]:
                start = i
            elif start and self.lines[i].startswith("### ") and i > start:
                return (start, i - 1)

        if start:
            # Find the last formula line
            last_formula = start
            for i in range(start, min(start + 30, len(self.lines))):
                if re.match(r"^- (Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Jan)", self.lines[i]):
                    last_formula = i
            return (start, last_formula)
        return None

    def get_cumulative_metrics_range(self) -> Optional[Tuple[int, int]]:
        """Find the cumulative metrics JSON block range."""
        sec2_start = self.get_section_start(2)
        if sec2_start is None:
            return None

        start = None
        end = None
        in_code_block = False

        for i in range(sec2_start, min(sec2_start + 180, len(self.lines))):
            if "### Cumulative Metrics" in self.lines[i]:
                start = i
            elif start and self.lines[i].strip() == "```json":
                in_code_block = True
            elif start and in_code_block and self.lines[i].strip() == "```":
                end = i
                break

        if start and end:
            return (start, end)
        return None

    # =========================================================================
    # SECTION 3: ENTRY-LEVEL DETAIL
    # =========================================================================

    def get_section3_insertion_point(self) -> Optional[int]:
        """
        Find where to insert new day blocks in Section 3.
        Returns the line index just before "## 4." section header.
        """
        sec4_start = self.get_section_start(4)
        if sec4_start is None:
            return None

        # Go back to find the "---" separator before section 4
        for i in range(sec4_start - 1, max(sec4_start - 5, 0), -1):
            if self.lines[i].strip() == "---":
                return i
        return sec4_start

    def get_last_section3_date(self) -> Optional[str]:
        """
        Find the last date header in Section 3.

        Returns:
            Date string like "Mar 2" from "### Mar 2 (Monday) - NET P&L: ..."
        """
        sec3_start = self.get_section_start(3)
        sec4_start = self.get_section_start(4)
        if sec3_start is None:
            return None

        end = sec4_start if sec4_start else len(self.lines)
        last_date = None

        for i in range(sec3_start, end):
            m = re.match(r"^### (\w+ \d+)", self.lines[i])
            if m:
                last_date = m.group(1)

        return last_date

    # =========================================================================
    # SECTION 4: MARKET CONDITIONS
    # =========================================================================

    def find_table_last_row(self, section_num: int, table_header_pattern: str) -> Optional[int]:
        """
        Find the last data row of a table in a section.

        Args:
            section_num: Section number (4, 5, etc.)
            table_header_pattern: Regex to match the table's header subsection.

        Returns:
            Line index of the last data row.
        """
        sec_start = self.get_section_start(section_num)
        if sec_start is None:
            return None

        next_sec = None
        for num in sorted(self._section_starts.keys()):
            if num > section_num and num < 100:
                next_sec = self._section_starts[num]
                break
        end = next_sec if next_sec else len(self.lines)

        # Find the subsection header
        header_line = None
        for i in range(sec_start, end):
            if re.search(table_header_pattern, self.lines[i]):
                header_line = i
                break

        if header_line is None:
            return None

        # Find last row of table after header
        last_row = None
        for i in range(header_line, end):
            if self.lines[i].strip().startswith("|"):
                last_row = i
            elif last_row and not self.lines[i].strip().startswith("|"):
                break

        return last_row

    # =========================================================================
    # SECTION 5: PERFORMANCE METRICS
    # =========================================================================

    def get_section5_range(self) -> Optional[Tuple[int, int]]:
        """Get the full line range of Section 5."""
        sec5_start = self.get_section_start(5)
        sec6_start = self.get_section_start(6)
        if sec5_start is None:
            return None

        end = sec6_start if sec6_start else len(self.lines)
        return (sec5_start, end)

    # =========================================================================
    # SECTION 8: IMPLEMENTATION LOG
    # =========================================================================

    def get_section8_table_last_row(self) -> Optional[int]:
        """Find the last data row in Section 8's implementation table."""
        return self.find_table_last_row(8, r"Date.*Rec.*Change Made")

    def get_existing_versions_in_section8(self) -> List[str]:
        """
        Get list of version strings already in Section 8 table.

        Returns:
            List of version strings like ["v1.2.8", "v1.4.0"].
        """
        sec8_start = self.get_section_start(8)
        sec9_start = self.get_section_start(9)
        if sec8_start is None:
            return []

        end = sec9_start if sec9_start else len(self.lines)
        versions = []

        for i in range(sec8_start, end):
            # Match version references like "v1.4.0" in table cells
            for m in re.finditer(r"v(\d+\.\d+\.\d+)", self.lines[i]):
                versions.append(m.group(0))

        return list(set(versions))

    # =========================================================================
    # SECTION 9: POST-IMPROVEMENT TRACKING
    # =========================================================================

    def get_section9_insertion_point(self) -> Optional[int]:
        """
        Find where to insert new day blocks in Section 9.
        Returns the line just before the next appendix or end of file.
        """
        sec9_start = self.get_section_start(9)
        if sec9_start is None:
            return None

        # Find Appendix A or next section
        for key in sorted(self._section_starts.keys()):
            if key >= 100:
                # Go back to find "---" separator
                idx = self._section_starts[key]
                for i in range(idx - 1, max(idx - 5, 0), -1):
                    if self.lines[i].strip() == "---":
                        return i
                return idx
        return len(self.lines)

    def get_last_post_improvement_day_number(self) -> int:
        """Get the highest "Post-Improvement Day N" number in Section 9."""
        sec9_start = self.get_section_start(9)
        if sec9_start is None:
            return 0

        max_day = 0
        for i in range(sec9_start, len(self.lines)):
            m = re.match(r"####\s+Post-Improvement Day (\d+)", self.lines[i])
            if m:
                max_day = max(max_day, int(m.group(1)))
        return max_day

    # =========================================================================
    # SECTION 1: EXECUTIVE SUMMARY
    # =========================================================================

    def get_section1_range(self) -> Optional[Tuple[int, int]]:
        """Get the full line range of Section 1."""
        sec1_start = self.get_section_start(1)
        sec2_start = self.get_section_start(2)
        if sec1_start is None:
            return None

        end = sec2_start if sec2_start else len(self.lines)
        return (sec1_start, end)

    # =========================================================================
    # APPENDIX F
    # =========================================================================

    def get_appendix_f_current_config_range(self) -> Optional[Tuple[int, int]]:
        """Find the "Config as of vX.Y.Z (Current, ...)" code block range."""
        appendix_f = self.get_appendix_start("F")
        if appendix_f is None:
            return None

        start = None
        end = None
        in_block = False

        for i in range(appendix_f, min(appendix_f + 100, len(self.lines))):
            if "Config as of" in self.lines[i] and "Current" in self.lines[i]:
                start = i
            elif start and self.lines[i].strip() == "```" and not in_block:
                in_block = True
            elif start and in_block and self.lines[i].strip() == "```":
                end = i
                break

        if start and end:
            return (start, end)
        return None

    # =========================================================================
    # UTILITY
    # =========================================================================

    def rebuild(self) -> str:
        """Rebuild the journal content from lines."""
        return "\n".join(self.lines)

    def insert_lines(self, index: int, new_lines: List[str]):
        """Insert lines at a specific index."""
        for i, line in enumerate(new_lines):
            self.lines.insert(index + i, line)
        # Re-parse sections after insertion
        self._parse_sections()

    def replace_range(self, start: int, end: int, new_lines: List[str]):
        """Replace a range of lines (inclusive start, inclusive end)."""
        self.lines[start:end + 1] = new_lines
        self._parse_sections()
