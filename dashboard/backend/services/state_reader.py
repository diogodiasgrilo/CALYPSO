"""Read hydra_state.json with mtime-based change detection."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dashboard.state_reader")

# Import get_us_market_time via the same path market_status.py uses
try:
    import sys
    _calypso_root = Path(__file__).resolve().parents[3]
    if str(_calypso_root) not in sys.path:
        sys.path.insert(0, str(_calypso_root))
    from shared.market_hours import get_us_market_time
    _HAS_MARKET_TIME = True
except ImportError:
    _HAS_MARKET_TIME = False


def _now_et_iso() -> str:
    """Get current ET time as ISO string."""
    if _HAS_MARKET_TIME:
        return get_us_market_time().isoformat()
    return datetime.utcnow().isoformat()


class StateFileReader:
    """Reads HYDRA state JSON with fast change detection via mtime."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._last_mtime: float = 0.0
        self._last_data: Optional[dict] = None
        # Stop transition detection
        self._detected_stops: list[dict] = []
        self._prev_entries: dict[int, dict] = {}  # keyed by entry_number
        self._current_date: Optional[str] = None

    def read_if_changed(self) -> Optional[dict]:
        """Return parsed JSON if file changed since last read, else None."""
        try:
            if not self.file_path.exists():
                return None

            mtime = self.file_path.stat().st_mtime
            if mtime == self._last_mtime and self._last_data is not None:
                return None  # No change

            data = self._read_file()
            if data is not None:
                self._last_mtime = mtime
                self._last_data = data
                self._detect_stop_transitions(data)
            return data

        except Exception as e:
            logger.warning(f"Error checking state file: {e}")
            return None

    def read_latest(self) -> Optional[dict]:
        """Force-read the file regardless of mtime."""
        data = self._read_file()
        if data is not None:
            try:
                self._last_mtime = self.file_path.stat().st_mtime
            except OSError:
                pass
            self._last_data = data
            self._detect_stop_transitions(data)
        return data

    def get_cached(self) -> Optional[dict]:
        """Return last successfully read data without touching disk."""
        return self._last_data

    def get_stop_events(self) -> list[dict]:
        """Return accumulated stop events detected during this session."""
        return list(self._detected_stops)

    def _detect_stop_transitions(self, data: dict) -> None:
        """Compare entry stop flags vs previous read. Record transitions."""
        state_date = data.get("date")
        entries = data.get("entries", [])

        # Day boundary — reset everything
        if state_date != self._current_date:
            self._detected_stops = []
            self._prev_entries = {}
            self._current_date = state_date

        # Build current entry lookup
        current: dict[int, dict] = {}
        for e in entries:
            num = e.get("entry_number")
            if num is not None:
                current[num] = e

        # Already-tracked stop keys for dedup
        existing_keys = {
            (s["entry_number"], s["side"]) for s in self._detected_stops
        }

        if not self._prev_entries:
            # First read after start — skip seeding. We can't place markers
            # accurately for already-stopped entries (no stop timestamp in
            # state file). Entry arrows already show amber/red for stops.
            # Only detect real-time transitions going forward.
            pass
        else:
            # Normal read — detect transitions (False → True)
            now = _now_et_iso()
            for num, e in current.items():
                prev = self._prev_entries.get(num, {})
                for side, flag in [("call", "call_side_stopped"), ("put", "put_side_stopped")]:
                    was_stopped = prev.get(flag, False)
                    is_stopped = e.get(flag, False)
                    if not was_stopped and is_stopped and (num, side) not in existing_keys:
                        self._detected_stops.append({
                            "entry_number": num,
                            "side": side,
                            "stop_time": now,
                        })
                        existing_keys.add((num, side))
                        logger.info(
                            f"Stop detected: Entry #{num} {side} side"
                        )

        self._prev_entries = current

    def _read_file(self) -> Optional[dict]:
        """Read and parse the JSON file. Returns None on any error."""
        try:
            raw = self.file_path.read_text(encoding="utf-8")
            return json.loads(raw)
        except json.JSONDecodeError as e:
            # Atomic writes via os.replace should prevent this,
            # but handle gracefully in case of partial read
            logger.warning(f"JSONDecodeError reading {self.file_path.name}: {e}")
            return None
        except OSError as e:
            logger.warning(f"OSError reading {self.file_path.name}: {e}")
            return None
