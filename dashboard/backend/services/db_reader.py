"""Read-only access to HOMER's backtesting.db (SQLite, WAL mode)."""

import logging
import sqlite3
from asyncio import to_thread
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dashboard.db_reader")


class BacktestingDBReader:
    """Read-only SQLite reader for HOMER's backtesting database.

    All queries run in a thread via asyncio.to_thread() to avoid blocking.
    Connection uses PRAGMA query_only=TRUE for defense-in-depth.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        """Create a new read-only connection (thread-safe)."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=5,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = TRUE")
        conn.execute("PRAGMA journal_mode")  # Don't change WAL, just read
        return conn

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a read-only query and return list of dicts."""
        try:
            conn = self._get_connection()
            try:
                cursor = conn.execute(sql, params)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()
        except sqlite3.Error as e:
            logger.warning(f"SQLite query error: {e}")
            return []

    async def get_today_ohlc(self, date_str: str) -> list[dict]:
        """Get 1-minute OHLC bars for a date."""
        return await to_thread(
            self._query,
            "SELECT * FROM market_ohlc_1min WHERE timestamp LIKE ? ORDER BY timestamp",
            (f"{date_str}%",),
        )

    async def get_today_ticks(self, date_str: str) -> list[dict]:
        """Get market ticks (heartbeat snapshots) for a date."""
        return await to_thread(
            self._query,
            "SELECT * FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp",
            (f"{date_str}%",),
        )

    async def get_entries_for_date(self, date_str: str) -> list[dict]:
        """Get trade entries for a specific date."""
        return await to_thread(
            self._query,
            "SELECT * FROM trade_entries WHERE date = ? ORDER BY entry_number",
            (date_str,),
        )

    async def get_stops_for_date(self, date_str: str) -> list[dict]:
        """Get stop events for a specific date."""
        return await to_thread(
            self._query,
            "SELECT * FROM trade_stops WHERE date = ? ORDER BY entry_number, side",
            (date_str,),
        )

    async def get_daily_summaries(self, limit: int = 30) -> list[dict]:
        """Get recent daily summaries for calendar heat map."""
        return await to_thread(
            self._query,
            "SELECT * FROM daily_summaries ORDER BY date DESC LIMIT ?",
            (limit,),
        )

    async def get_daily_summaries_by_year(self, year: int) -> list[dict]:
        """Get all daily summaries for a specific year."""
        return await to_thread(
            self._query,
            "SELECT * FROM daily_summaries WHERE date LIKE ? ORDER BY date",
            (f"{year}-%",),
        )

    async def get_all_summaries(self) -> list[dict]:
        """Get all daily summaries for analytics."""
        return await to_thread(
            self._query,
            "SELECT * FROM daily_summaries ORDER BY date",
        )

    async def get_all_entries(self) -> list[dict]:
        """Get all trade entries for analytics."""
        return await to_thread(
            self._query,
            "SELECT * FROM trade_entries ORDER BY date, entry_number",
        )

    async def get_all_stops(self) -> list[dict]:
        """Get all stop events for analytics."""
        return await to_thread(
            self._query,
            "SELECT * FROM trade_stops ORDER BY date, entry_number",
        )

    async def get_date_range(self) -> Optional[dict]:
        """Get the min/max dates available in the database."""
        rows = await to_thread(
            self._query,
            "SELECT MIN(date) as first_date, MAX(date) as last_date, COUNT(*) as total_days FROM daily_summaries",
        )
        return rows[0] if rows else None

    async def is_available(self) -> bool:
        """Check if database file exists and is readable."""
        try:
            return await to_thread(lambda: self.db_path.exists() and self.db_path.stat().st_size > 0)
        except OSError:
            return False
