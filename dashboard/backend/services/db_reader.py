"""Read-only access to HOMER's backtesting.db (SQLite, WAL mode)."""

import logging
import sqlite3
import threading
from asyncio import to_thread
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dashboard.db_reader")


class BacktestingDBReader:
    """Read-only SQLite reader for HOMER's backtesting database.

    All queries run in a thread via asyncio.to_thread() to avoid blocking.
    Connection uses PRAGMA query_only=TRUE for defense-in-depth.

    Uses a thread-local persistent connection to avoid creating a new
    connection per query (~40+ queries on analytics page load).
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a thread-local read-only connection."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.Error:
                # Connection is stale, recreate
                try:
                    conn.close()
                except Exception:
                    pass
                self._local.conn = None

        conn = sqlite3.connect(
            str(self.db_path),
            timeout=5,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = TRUE")
        conn.execute("PRAGMA journal_mode")  # Don't change WAL, just read
        self._local.conn = conn
        return conn

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a read-only query and return list of dicts."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.warning(f"SQLite query error: {e}")
            # Discard broken connection so next query creates a fresh one
            self._local.conn = None
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

    async def get_comparison_stats(self) -> Optional[dict]:
        """Get comparison statistics (averages, best/worst) across all trading days."""
        rows = await to_thread(
            self._query,
            """SELECT
                AVG(net_pnl) as avg_pnl,
                AVG(entries_placed) as avg_entries,
                AVG(entries_stopped) as avg_stops,
                (SELECT AVG(day_credit) FROM (
                    SELECT SUM(total_credit) as day_credit
                    FROM trade_entries GROUP BY date
                )) as avg_credit,
                MAX(net_pnl) as best_day,
                MIN(net_pnl) as worst_day,
                COUNT(*) as total_days
            FROM daily_summaries""",
        )
        return rows[0] if rows else None

    async def get_daily_pnls(self) -> list[float]:
        """Get all daily net P&L values for performance metric calculations."""
        rows = await to_thread(
            self._query,
            "SELECT net_pnl FROM daily_summaries ORDER BY date",
        )
        return [row["net_pnl"] for row in rows if row.get("net_pnl") is not None]

    async def get_replay_pnl(self, date_str: str) -> list[dict]:
        """Compute unrealized P&L curve from spread_snapshots + trade_entries.

        Returns 1-minute resolution [{time: "HH:MM", pnl: float}] showing
        how total P&L fluctuated throughout the day as SPX moved.
        """
        def _compute():
            try:
                conn = self._get_connection()

                # Get per-entry credits
                entry_rows = conn.execute(
                    "SELECT entry_number, call_credit, put_credit FROM trade_entries WHERE date = ?",
                    (date_str,),
                ).fetchall()
                credits = {}
                for r in entry_rows:
                    credits[r["entry_number"]] = (r["call_credit"] or 0) + (r["put_credit"] or 0)

                if not credits:
                    return []

                # Get all spread snapshots for the day
                snap_rows = conn.execute(
                    "SELECT timestamp, entry_number, call_spread_value, put_spread_value "
                    "FROM spread_snapshots WHERE timestamp LIKE ? ORDER BY timestamp",
                    (f"{date_str}%",),
                ).fetchall()

                if not snap_rows:
                    return []

                # Group by timestamp, compute total P&L
                from collections import OrderedDict
                by_minute: OrderedDict[str, float] = OrderedDict()

                current_ts = None
                entry_pnls: dict[int, float] = {}

                for row in snap_rows:
                    ts = row["timestamp"]
                    minute_key = ts[:16]  # "2026-03-16 10:16"
                    entry_num = row["entry_number"]
                    cost = (row["call_spread_value"] or 0) + (row["put_spread_value"] or 0)
                    credit = credits.get(entry_num, 0)

                    if minute_key != current_ts:
                        if current_ts is not None:
                            by_minute[current_ts] = sum(entry_pnls.values())
                        current_ts = minute_key

                    entry_pnls[entry_num] = credit - cost

                # Last minute
                if current_ts is not None:
                    by_minute[current_ts] = sum(entry_pnls.values())

                return [
                    {"time": ts[11:16], "pnl": round(pnl, 2)}
                    for ts, pnl in by_minute.items()
                ]

            except Exception as e:
                logger.warning(f"replay_pnl computation error: {e}")
                return []

        return await to_thread(_compute)

    async def is_available(self) -> bool:
        """Check if database file exists and is readable."""
        try:
            return await to_thread(lambda: self.db_path.exists() and self.db_path.stat().st_size > 0)
        except OSError:
            return False
