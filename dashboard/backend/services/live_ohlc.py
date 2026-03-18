"""Build live 1-minute OHLC bars from heartbeat log lines (zero bot changes).

HYDRA logs heartbeat lines every ~11 seconds in this format:
  HEARTBEAT | Monitoring | SPX: 5892.41 | VIX: 16.20 | ...

This module parses SPX/VIX from those log lines and aggregates them into
1-minute OHLC bars for the live chart. After market close, HOMER writes
the authoritative OHLC to SQLite — these live bars fill the gap during
trading hours.
"""

import logging
import re
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger("dashboard.live_ohlc")

# Extract SPX and VIX from heartbeat log messages
HEARTBEAT_RE = re.compile(
    r"HEARTBEAT\s*\|.*?SPX:\s*([\d.]+).*?VIX:\s*([\d.]+)"
)


class LiveOHLCBuilder:
    """Aggregates heartbeat ticks into 1-minute OHLC bars."""

    def __init__(self):
        # Keyed by minute string "HH:MM", value is {open, high, low, close, vix}
        self._bars: dict[str, dict] = {}
        self._current_date: str = ""
        self._last_spx: float = 0.0
        self._last_vix: float = 0.0
        self._tick_count: int = 0
        # Raw ticks for /api/market/ticks fallback
        self._ticks: list[dict] = []

    @property
    def last_spx(self) -> float:
        return self._last_spx

    @property
    def last_vix(self) -> float:
        return self._last_vix

    def reset(self) -> None:
        """Reset for a new trading day."""
        self._bars.clear()
        self._ticks.clear()
        self._current_date = ""
        self._last_spx = 0.0
        self._last_vix = 0.0
        self._tick_count = 0

    def process_log_lines(self, lines: list[dict]) -> bool:
        """Process parsed log lines, extract heartbeat ticks.

        Returns True if any new ticks were added (bars changed).
        """
        changed = False

        for line in lines:
            msg = line.get("message", "")
            ts = line.get("timestamp", "")

            match = HEARTBEAT_RE.search(msg)
            if not match:
                continue

            spx = float(match.group(1))
            vix = float(match.group(2))

            if spx <= 0:
                continue

            # Extract date and minute from timestamp "YYYY-MM-DD HH:MM:SS"
            if len(ts) < 16:
                continue

            date_str = ts[:10]
            minute_key = ts[11:16]  # "HH:MM"

            # New day? Reset bars.
            if date_str != self._current_date:
                self._bars.clear()
                self._ticks.clear()
                self._current_date = date_str
                self._tick_count = 0

            self._last_spx = spx
            self._last_vix = vix
            self._tick_count += 1

            # Store raw tick for /api/market/ticks fallback
            self._ticks.append({
                "timestamp": ts,
                "spx_price": spx,
                "vix_level": vix,
            })

            # Update or create 1-minute bar
            if minute_key in self._bars:
                bar = self._bars[minute_key]
                bar["high"] = max(bar["high"], spx)
                bar["low"] = min(bar["low"], spx)
                bar["close"] = spx
                bar["vix"] = vix
            else:
                self._bars[minute_key] = {
                    "open": spx,
                    "high": spx,
                    "low": spx,
                    "close": spx,
                    "vix": vix,
                }

            changed = True

        return changed

    def get_ticks(self) -> list[dict]:
        """Return raw heartbeat ticks matching market_ticks schema."""
        return list(self._ticks)

    def get_ohlc_bars(self) -> list[dict]:
        """Return all bars as sorted list matching SQLite schema.

        Format matches market_ohlc_1min: {timestamp, open, high, low, close, vix}
        """
        if not self._current_date:
            return []

        bars = []
        for minute_key in sorted(self._bars.keys()):
            bar = self._bars[minute_key]
            bars.append({
                "timestamp": f"{self._current_date} {minute_key}:00",
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "vix": bar["vix"],
            })

        return bars
