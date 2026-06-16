"""Read-only access to a calendar variant's isolated ``dc_calendar.db``.

SHAPE-DISTINCT, debit-native — this is NOT a reuse of the IC ``BacktestingDBReader``
(audit AUD-3-F2 / AUD-4-F4). The calendar DB (``bots/hydra/dc_recorder.py`` schema)
has no ``daily_summaries``, no per-day ``net_pnl``, and no spread-width columns; it
is multi-day-keyed (``dc_outcomes`` carries both ``entry_date`` and ``close_date``).
Forcing it through the credit/spread-width IC math would render a net-debit calendar
as a fabricated credit + width, so this reader computes calendar-native figures:

  * daily P&L rolled up by **``close_date``** (NOT ``entry_date`` — entry-date
    attribution makes the cumulative curve retroactively mutate as old positions
    close on later days; audit AUD-4-F4);
  * lifetime capital basis = ``SUM(net_debit)`` (the defined risk of a debit
    calendar — NOT the spread-width notional the IC reader uses);
  * the output carries **NO** credit/buffer/spread-width keys (``total_credit``,
    buffer-margin, cushion, ``capital_deployed`` as width-notional) — they are
    OMITTED, not null-filled, so the calendar payload cannot leak IC fields.

Every connection is opened read-only (``?mode=ro`` + ``PRAGMA query_only=TRUE``),
mirroring the other dashboard readers; a missing DB yields empty/zero, never a crash.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from asyncio import to_thread
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("dashboard.dc_db_reader")


class DCDBReader:
    """Read-only SQLite reader for a calendar variant's ``dc_calendar.db``.

    Queries run in a worker thread (``asyncio.to_thread``) and use a thread-local
    persistent connection, matching :class:`BacktestingDBReader`'s posture. The
    connection is opened via the ``file:...?mode=ro`` URI AND sets
    ``PRAGMA query_only=TRUE`` — belt-and-braces against any accidental write.
    """

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self._local = threading.local()

    # ------------------------------------------------------------------

    def _get_connection(self) -> Optional[sqlite3.Connection]:
        """Get/create a thread-local read-only connection, or None if missing."""
        if not self.db_path.exists():
            return None
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.Error:
                try:
                    conn.close()
                except Exception:
                    pass
                self._local.conn = None
        try:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                timeout=5,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = TRUE")
            self._local.conn = conn
            return conn
        except sqlite3.Error as e:
            logger.warning(f"DCDBReader connect error ({self.db_path}): {e}")
            return None

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = self._get_connection()
        if conn is None:
            return []
        try:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.warning(f"DCDBReader query error: {e}")
            self._local.conn = None  # discard broken connection
            return []

    # ------------------------------------------------------------------

    async def is_available(self) -> bool:
        """True when the calendar DB file exists and is non-empty."""
        try:
            return await to_thread(
                lambda: self.db_path.exists() and self.db_path.stat().st_size > 0
            )
        except OSError:
            return False

    async def get_daily_calendar_summaries(self, limit: int = 365) -> list[dict]:
        """Per-day realized P&L, attributed to **``close_date``**.

        Rolls up ``dc_outcomes.realized_pnl`` GROUP BY ``close_date`` into
        ``{date, net_pnl, outcomes}`` rows (oldest-first, suitable for a running
        cumulative). P&L lands on the day a calendar CLOSES — not its entry day —
        so the cumulative curve is monotonic-in-time and never mutates a past
        point when an old position settles later (audit AUD-4-F4).
        ``outcomes`` is the count of calendars that closed that day.
        """
        rows = await to_thread(
            self._query,
            """SELECT close_date AS date,
                      ROUND(COALESCE(SUM(realized_pnl), 0), 2) AS net_pnl,
                      COUNT(*) AS outcomes
               FROM dc_outcomes
               WHERE close_date IS NOT NULL
               GROUP BY close_date
               ORDER BY close_date DESC
               LIMIT ?""",
            (limit,),
        )
        # Return oldest-first so a caller building a cumulative curve gets the
        # natural chronological order (matches the IC reader's get_all_summaries).
        return list(reversed(rows))

    async def get_calendar_outcomes(self, limit: int = 20) -> list[dict]:
        """Most-recent terminal calendar outcomes (newest first).

        Debit-native columns only — ``net_debit`` (the capital at risk) and
        ``transform_credit`` (nullable; present-but-empty for non-transformer
        calendars like E). No credit/spread-width fields.
        """
        return await to_thread(
            self._query,
            """SELECT entry_date, close_date, entry_number, strategy_id,
                      terminal_state, realized_pnl, spx_at_close,
                      close_commission, transform_credit, net_debit
               FROM dc_outcomes
               ORDER BY close_date DESC, entry_number DESC
               LIMIT ?""",
            (limit,),
        )

    async def get_cumulative_overrides_calendar(self) -> dict:
        """Lifetime calendar totals — debit-native, NO IC fields.

        Capital basis = ``SUM(net_debit)`` (the defined risk of a debit calendar),
        NOT spread-width notional. The output OMITS every credit-only key
        (``total_credit``, buffer-margin, cushion, width-notional ``capital_deployed``)
        rather than null-filling them as a misleading ``$0``/``100%`` — so the
        calendar payload structurally cannot leak an IC field. A win/loss day is a
        ``close_date`` whose summed realized P&L is > 0 / < 0.

        Returns zeros on a missing/empty DB, never raises.
        """
        rows = await to_thread(
            self._query,
            """SELECT
                ROUND(COALESCE(SUM(realized_pnl), 0), 2) AS cumulative_pnl,
                ROUND(COALESCE(SUM(net_debit), 0), 2)    AS capital_at_risk,
                COUNT(*)                                  AS total_calendars,
                COUNT(DISTINCT entry_date)                AS entry_days,
                MAX(close_date)                           AS last_updated
               FROM dc_outcomes""",
        )
        base = rows[0] if rows else {}

        # Win/loss DAY counts keyed on close_date (a day is a win iff its summed
        # realized P&L is positive) — computed in SQL so it stays consistent with
        # the daily summaries' close_date attribution.
        day_rows = await to_thread(
            self._query,
            """SELECT
                SUM(CASE WHEN day_pnl > 0 THEN 1 ELSE 0 END) AS winning_days,
                SUM(CASE WHEN day_pnl < 0 THEN 1 ELSE 0 END) AS losing_days,
                COUNT(*)                                      AS close_days
               FROM (
                  SELECT ROUND(SUM(realized_pnl), 2) AS day_pnl
                  FROM dc_outcomes
                  WHERE close_date IS NOT NULL
                  GROUP BY close_date
               )""",
        )
        days = day_rows[0] if day_rows else {}

        cumulative_pnl = base.get("cumulative_pnl") or 0.0
        capital = base.get("capital_at_risk") or 0.0
        entry_days = base.get("entry_days") or 0
        # ROI on capital at risk (debit) — the calendar analogue of the IC's
        # ROI-on-capital-deployed, but computed off net_debit, not width notional.
        roi_pct = round((cumulative_pnl / capital) * 100, 2) if capital else 0.0
        return {
            "cumulative_pnl": cumulative_pnl,
            "capital_at_risk": capital,          # SUM(net_debit) — debit basis
            "total_calendars": base.get("total_calendars") or 0,
            "entry_days": entry_days,
            "winning_days": days.get("winning_days") or 0,
            "losing_days": days.get("losing_days") or 0,
            "close_days": days.get("close_days") or 0,
            "roi_pct": roi_pct,
            "avg_capital_per_day": round(capital / entry_days, 2) if entry_days else 0.0,
            "last_updated": base.get("last_updated"),
        }
