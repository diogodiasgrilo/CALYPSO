"""Read hydra_metrics.json (cumulative performance metrics)."""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dashboard.metrics_reader")


class MetricsFileReader:
    """Reads HYDRA cumulative metrics JSON with mtime change detection."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._last_mtime: float = 0.0
        self._last_data: Optional[dict] = None

    def read_if_changed(self) -> Optional[dict]:
        """Return parsed JSON if file changed since last read, else None."""
        try:
            if not self.file_path.exists():
                return None

            mtime = self.file_path.stat().st_mtime
            if mtime == self._last_mtime and self._last_data is not None:
                return None

            data = self._read_file()
            if data is not None:
                self._last_mtime = mtime
                self._last_data = data
            return data

        except Exception as e:
            logger.warning(f"Error checking metrics file: {e}")
            return None

    def read_latest(self) -> Optional[dict]:
        """Force-read the file."""
        data = self._read_file()
        if data is not None:
            try:
                self._last_mtime = self.file_path.stat().st_mtime
            except OSError:
                pass
            self._last_data = data
        return data

    def get_cached(self) -> Optional[dict]:
        """Return last successfully read data."""
        return self._last_data

    def _read_file(self) -> Optional[dict]:
        try:
            raw = self.file_path.read_text(encoding="utf-8")
            return json.loads(raw)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Error reading {self.file_path.name}: {e}")
            return None
