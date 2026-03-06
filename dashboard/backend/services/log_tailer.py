"""Tail HYDRA's bot.log for live log feed."""

import logging
import re
from pathlib import Path

logger = logging.getLogger("dashboard.log_tailer")

# HYDRA log format: YYYY-MM-DD HH:MM:SS | LEVEL | component | message
LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s*([^|]+?)\s*\|\s*(.*)$"
)


class LogTailer:
    """Tail new lines from HYDRA's bot.log file."""

    def __init__(self, file_path: Path, max_lines: int = 100):
        self.file_path = file_path
        self.max_lines = max_lines
        self._offset: int = 0
        self._initialized = False

    def seek_to_end(self) -> None:
        """Position at end of file (skip history on startup)."""
        try:
            if self.file_path.exists():
                self._offset = self.file_path.stat().st_size
                self._initialized = True
        except OSError as e:
            logger.warning(f"Error seeking log file: {e}")

    def read_today_history(self, today_str: str) -> list[dict]:
        """Read all lines from today in the log file (for bootstrapping OHLC).

        Args:
            today_str: Date string "YYYY-MM-DD" to filter for.

        Returns lines from today only, without advancing the tail offset.
        """
        try:
            if not self.file_path.exists():
                return []

            entries = []
            with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    stripped = line.rstrip("\n")
                    if not stripped.startswith(today_str):
                        continue
                    parsed = self._parse_line(stripped)
                    if parsed:
                        entries.append(parsed)

            logger.info(f"Read {len(entries)} historical log lines for {today_str}")
            return entries

        except OSError as e:
            logger.warning(f"Error reading log history: {e}")
            return []

    def read_new_lines(self) -> list[dict]:
        """Read new lines since last read. Returns parsed log entries."""
        if not self._initialized:
            self.seek_to_end()
            return []

        try:
            if not self.file_path.exists():
                return []

            file_size = self.file_path.stat().st_size

            # File was truncated or rotated
            if file_size < self._offset:
                self._offset = 0

            if file_size == self._offset:
                return []

            with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._offset)
                raw_lines = f.readlines()
                self._offset = f.tell()

            entries = []
            for line in raw_lines[-self.max_lines :]:
                parsed = self._parse_line(line.rstrip("\n"))
                if parsed:
                    entries.append(parsed)

            return entries

        except OSError as e:
            logger.warning(f"Error reading log file: {e}")
            return []

    @staticmethod
    def _parse_line(line: str) -> dict | None:
        """Parse a log line into structured data."""
        if not line.strip():
            return None

        match = LOG_PATTERN.match(line)
        if match:
            return {
                "timestamp": match.group(1),
                "level": match.group(2).upper(),
                "component": match.group(3).strip(),
                "message": match.group(4).strip(),
            }

        # Unparseable line (continuation, stack trace, etc.)
        return {
            "timestamp": "",
            "level": "INFO",
            "component": "",
            "message": line.strip(),
        }
