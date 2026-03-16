"""
Real-time SQLite data recorder for HYDRA trading bot.

Writes trading data directly to backtesting.db during the trading loop,
replacing the fragile log-parsing pipeline (bot → log → HOMER → SQLite)
with direct writes (bot → SQLite).

Safety guarantees:
- All writes wrapped in try/except — DB errors NEVER affect trading
- WAL mode for concurrent read (dashboard) + write (bot)
- INSERT OR IGNORE for idempotency (HOMER can re-write same data)
- Fresh connection per batch (no stale connections)
- timeout=5 on all connections

Schema v5 adds: individual leg prices, Greeks, bid-ask width, slippage,
margin, execution quality, MAE/MFE, skipped entries, economic events.
"""

import json
import logging
import os
import sqlite3
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Schema version this module expects/creates
SCHEMA_VERSION = 5

# ============================================================================
# Schema Migration SQL
# ============================================================================

# v5 migrations: new columns on existing tables + new tables
MIGRATION_V5_SQL = [
    # spread_snapshots: individual leg prices
    "ALTER TABLE spread_snapshots ADD COLUMN short_call_price REAL",
    "ALTER TABLE spread_snapshots ADD COLUMN long_call_price REAL",
    "ALTER TABLE spread_snapshots ADD COLUMN short_put_price REAL",
    "ALTER TABLE spread_snapshots ADD COLUMN long_put_price REAL",

    # trade_entries: Greeks
    "ALTER TABLE trade_entries ADD COLUMN delta_call REAL",
    "ALTER TABLE trade_entries ADD COLUMN delta_put REAL",
    "ALTER TABLE trade_entries ADD COLUMN theta_call REAL",
    "ALTER TABLE trade_entries ADD COLUMN theta_put REAL",
    "ALTER TABLE trade_entries ADD COLUMN vega_call REAL",
    "ALTER TABLE trade_entries ADD COLUMN vega_put REAL",

    # trade_entries: execution quality
    "ALTER TABLE trade_entries ADD COLUMN bid_ask_width_call REAL",
    "ALTER TABLE trade_entries ADD COLUMN bid_ask_width_put REAL",
    "ALTER TABLE trade_entries ADD COLUMN time_to_fill_ms INTEGER",
    "ALTER TABLE trade_entries ADD COLUMN slippage_call REAL",
    "ALTER TABLE trade_entries ADD COLUMN slippage_put REAL",

    # trade_entries: margin & config
    "ALTER TABLE trade_entries ADD COLUMN margin_available REAL",
    "ALTER TABLE trade_entries ADD COLUMN margin_utilization_pct REAL",
    "ALTER TABLE trade_entries ADD COLUMN config_version TEXT",
    "ALTER TABLE trade_entries ADD COLUMN attempts INTEGER DEFAULT 1",

    # trade_stops: enrichment
    "ALTER TABLE trade_stops ADD COLUMN quoted_mid_at_stop REAL",
    "ALTER TABLE trade_stops ADD COLUMN slippage_on_close REAL",
    "ALTER TABLE trade_stops ADD COLUMN spx_move_since_entry REAL",
    "ALTER TABLE trade_stops ADD COLUMN minutes_held REAL",
    "ALTER TABLE trade_stops ADD COLUMN cascade_gap_seconds REAL",

    # daily_summaries: enrichment
    "ALTER TABLE daily_summaries ADD COLUMN overnight_gap REAL",
    "ALTER TABLE daily_summaries ADD COLUMN realized_volatility REAL",
    "ALTER TABLE daily_summaries ADD COLUMN economic_events TEXT",
    "ALTER TABLE daily_summaries ADD COLUMN config_version TEXT",
    "ALTER TABLE daily_summaries ADD COLUMN opex_week INTEGER DEFAULT 0",
]

# New tables for v5
CREATE_SKIPPED_ENTRIES_SQL = """
CREATE TABLE IF NOT EXISTS skipped_entries (
    date TEXT NOT NULL,
    entry_number INTEGER NOT NULL,
    skip_time TEXT,
    skip_reason TEXT,
    spx_at_skip REAL,
    vix_at_skip REAL,
    theoretical_short_call REAL,
    theoretical_long_call REAL,
    theoretical_short_put REAL,
    theoretical_long_put REAL,
    estimated_call_credit REAL,
    estimated_put_credit REAL,
    would_have_stopped INTEGER,
    theoretical_pnl REAL,
    PRIMARY KEY (date, entry_number)
);
"""

CREATE_MAE_MFE_SQL = """
CREATE TABLE IF NOT EXISTS entry_mae_mfe (
    date TEXT NOT NULL,
    entry_number INTEGER NOT NULL,
    side TEXT NOT NULL,
    mae_value REAL,
    mae_time TEXT,
    mfe_value REAL,
    mfe_time TEXT,
    cushion_min_pct REAL,
    cushion_min_time TEXT,
    PRIMARY KEY (date, entry_number, side)
);
"""

CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_skipped_date ON skipped_entries(date);
CREATE INDEX IF NOT EXISTS idx_mae_mfe_date ON entry_mae_mfe(date);
"""


class DataRecorder:
    """
    Real-time SQLite writer for HYDRA trading data.

    All public methods return bool (True=success). Callers should NOT
    check this value — recording failures are non-critical.
    """

    def __init__(self, db_path: str):
        """Initialize with path to backtesting.db."""
        self.db_path = db_path
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        """Create a fresh connection with WAL mode and timeout=5."""
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _safe_write(self, operation_name: str, fn: Callable) -> bool:
        """Wrap any write operation in try/except. Returns True on success."""
        try:
            fn()
            return True
        except Exception as e:
            logger.warning(f"DataRecorder.{operation_name} failed (non-critical): {e}")
            return False

    # ========================================================================
    # Schema Management
    # ========================================================================

    def ensure_schema(self) -> bool:
        """Run schema migration v4 → v5 (additive ALTER TABLE only).

        Safe to call multiple times — duplicate column errors are silently ignored.
        """
        def _migrate():
            with self._connect() as conn:
                # Check current version
                try:
                    row = conn.execute(
                        "SELECT value FROM schema_info WHERE key = 'version'"
                    ).fetchone()
                    current_version = int(row[0]) if row else 0
                except sqlite3.OperationalError:
                    # schema_info table doesn't exist yet (fresh DB)
                    current_version = 0

                if current_version >= SCHEMA_VERSION:
                    self._initialized = True
                    return

                # Create new tables (IF NOT EXISTS = safe)
                conn.executescript(CREATE_SKIPPED_ENTRIES_SQL)
                conn.executescript(CREATE_MAE_MFE_SQL)
                conn.executescript(CREATE_INDEXES_SQL)

                # Add new columns (catch duplicate column errors)
                for sql in MIGRATION_V5_SQL:
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():
                            logger.warning(f"Migration SQL failed: {sql} — {e}")

                # Update version
                conn.execute(
                    "INSERT OR REPLACE INTO schema_info (key, value) VALUES ('version', ?)",
                    (str(SCHEMA_VERSION),)
                )
                conn.commit()
                self._initialized = True
                logger.info(f"DataRecorder schema migrated to v{SCHEMA_VERSION}")

        return self._safe_write("ensure_schema", _migrate)

    # ========================================================================
    # Heartbeat Writes (~every 10s during market hours)
    # ========================================================================

    def record_tick(
        self,
        timestamp: str,
        spx_price: float,
        vix_level: Optional[float],
        trend_signal: str,
        bot_state: str,
        entry_count: int,
        active_count: int,
    ) -> bool:
        """Write a single market_ticks row."""
        def _write():
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO market_ticks
                    (timestamp, spx_price, vix_level, trend_signal, bot_state,
                     entry_count, active_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (timestamp, spx_price, vix_level, trend_signal, bot_state,
                     entry_count, active_count)
                )
                conn.commit()

        return self._safe_write("record_tick", _write)

    def record_spread_snapshots(
        self,
        timestamp: str,
        snapshots: List[Dict[str, Any]],
    ) -> bool:
        """Write spread_snapshots with individual leg price columns.

        Each dict: {entry_number, call_spread_value, put_spread_value,
                    short_call_price, long_call_price, short_put_price, long_put_price}
        """
        if not snapshots:
            return True

        def _write():
            with self._connect() as conn:
                conn.executemany(
                    """INSERT OR IGNORE INTO spread_snapshots
                    (timestamp, entry_number, call_spread_value, put_spread_value,
                     short_call_price, long_call_price, short_put_price, long_put_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            timestamp,
                            s["entry_number"],
                            s.get("call_spread_value"),
                            s.get("put_spread_value"),
                            s.get("short_call_price"),
                            s.get("long_call_price"),
                            s.get("short_put_price"),
                            s.get("long_put_price"),
                        )
                        for s in snapshots
                    ],
                )
                conn.commit()

        return self._safe_write("record_spread_snapshots", _write)

    # ========================================================================
    # Entry Writes (after successful fill, ~5 per day)
    # ========================================================================

    def record_entry(self, entry_data: Dict[str, Any]) -> bool:
        """Write a single trade_entries row with all fields (existing + new).

        entry_data must include 'date' and 'entry_number' as primary key.
        All other fields are optional (NULL if missing).
        """
        def _write():
            cols = [
                "date", "entry_number", "entry_time", "spx_at_entry", "vix_at_entry",
                "expected_move", "trend_signal", "entry_type", "override_reason",
                "short_call_strike", "long_call_strike", "short_put_strike", "long_put_strike",
                "call_credit", "put_credit", "total_credit",
                "call_spread_width", "put_spread_width",
                "mkt031_score", "mkt031_early",
                "otm_distance_call", "otm_distance_put",
                # v5 new columns
                "delta_call", "delta_put", "theta_call", "theta_put",
                "vega_call", "vega_put",
                "bid_ask_width_call", "bid_ask_width_put",
                "time_to_fill_ms", "slippage_call", "slippage_put",
                "margin_available", "margin_utilization_pct",
                "config_version", "attempts",
            ]
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            values = tuple(entry_data.get(c) for c in cols)

            with self._connect() as conn:
                conn.execute(
                    f"INSERT OR IGNORE INTO trade_entries ({col_names}) VALUES ({placeholders})",
                    values,
                )
                conn.commit()

        return self._safe_write("record_entry", _write)

    # ========================================================================
    # Stop Loss Writes (after position closed, 0-5 per day)
    # ========================================================================

    def record_stop(self, stop_data: Dict[str, Any]) -> bool:
        """Write a single trade_stops row with all fields (existing + new).

        stop_data must include 'date', 'entry_number', 'side' as primary key.
        """
        def _write():
            cols = [
                "date", "entry_number", "side",
                "stop_time", "spx_at_stop", "trigger_level", "actual_debit", "net_pnl",
                "salvage_sold", "salvage_revenue",
                "confirmation_seconds", "breach_recoveries",
                # v5 new columns
                "quoted_mid_at_stop", "slippage_on_close",
                "spx_move_since_entry", "minutes_held", "cascade_gap_seconds",
            ]
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            values = tuple(stop_data.get(c) for c in cols)

            with self._connect() as conn:
                conn.execute(
                    f"INSERT OR IGNORE INTO trade_stops ({col_names}) VALUES ({placeholders})",
                    values,
                )
                conn.commit()

        return self._safe_write("record_stop", _write)

    # ========================================================================
    # Skip Writes (on entry skip, 0-3 per day)
    # ========================================================================

    def record_skipped_entry(self, skip_data: Dict[str, Any]) -> bool:
        """Write a single skipped_entries row for counterfactual tracking.

        skip_data must include 'date' and 'entry_number' as primary key.
        """
        def _write():
            cols = [
                "date", "entry_number", "skip_time", "skip_reason",
                "spx_at_skip", "vix_at_skip",
                "theoretical_short_call", "theoretical_long_call",
                "theoretical_short_put", "theoretical_long_put",
                "estimated_call_credit", "estimated_put_credit",
            ]
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            values = tuple(skip_data.get(c) for c in cols)

            with self._connect() as conn:
                conn.execute(
                    f"INSERT OR IGNORE INTO skipped_entries ({col_names}) VALUES ({placeholders})",
                    values,
                )
                conn.commit()

        return self._safe_write("record_skipped_entry", _write)

    # ========================================================================
    # Settlement Writes (once per day after 4 PM)
    # ========================================================================

    def record_daily_summary(self, summary_data: Dict[str, Any]) -> bool:
        """Write daily_summaries row with enrichment fields.

        Uses INSERT OR REPLACE so DataRecorder can overwrite HOMER's row
        with richer data (economic events, overnight gap, etc.).
        """
        def _write():
            cols = [
                "date", "spx_open", "spx_close", "spx_high", "spx_low", "day_range",
                "vix_open", "vix_close",
                "entries_placed", "entries_stopped", "entries_expired",
                "gross_pnl", "net_pnl", "commission", "long_salvage_revenue",
                "day_type", "day_of_week",
                # v5 new columns
                "overnight_gap", "realized_volatility", "economic_events",
                "config_version", "opex_week",
            ]
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            values = tuple(summary_data.get(c) for c in cols)

            with self._connect() as conn:
                conn.execute(
                    f"INSERT OR REPLACE INTO daily_summaries ({col_names}) VALUES ({placeholders})",
                    values,
                )
                conn.commit()

        return self._safe_write("record_daily_summary", _write)

    def compute_mae_mfe(self, date_str: str) -> bool:
        """Compute MAE/MFE from spread_snapshots for all entries on a date.

        MAE = max spread value (worst P&L moment) during entry lifetime.
        MFE = min spread value (best P&L moment) during entry lifetime.
        """
        def _compute():
            with self._connect() as conn:
                # Get all entries for the date
                entries = conn.execute(
                    "SELECT entry_number FROM trade_entries WHERE date = ?",
                    (date_str,)
                ).fetchall()

                for (entry_num,) in entries:
                    for side, col in [("call", "call_spread_value"), ("put", "put_spread_value")]:
                        rows = conn.execute(
                            f"""SELECT timestamp, {col}
                            FROM spread_snapshots
                            WHERE substr(timestamp, 1, 10) = ? AND entry_number = ?
                            AND {col} IS NOT NULL AND {col} > 0
                            ORDER BY timestamp""",
                            (date_str, entry_num)
                        ).fetchall()

                        if not rows:
                            continue

                        # MAE = max value (highest cost-to-close = worst moment)
                        mae_row = max(rows, key=lambda r: r[1])
                        # MFE = min value (lowest cost-to-close = best moment)
                        mfe_row = min(rows, key=lambda r: r[1])

                        conn.execute(
                            """INSERT OR REPLACE INTO entry_mae_mfe
                            (date, entry_number, side, mae_value, mae_time,
                             mfe_value, mfe_time)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (date_str, entry_num, side,
                             mae_row[1], mae_row[0],
                             mfe_row[1], mfe_row[0])
                        )

                conn.commit()

        return self._safe_write("compute_mae_mfe", _compute)

    def update_skipped_entry_backtest(
        self,
        date_str: str,
        entry_number: int,
        would_have_stopped: bool,
        theoretical_pnl: float,
    ) -> bool:
        """Update skipped_entries with hindsight P&L data (post-settlement)."""
        def _write():
            with self._connect() as conn:
                conn.execute(
                    """UPDATE skipped_entries
                    SET would_have_stopped = ?, theoretical_pnl = ?
                    WHERE date = ? AND entry_number = ?""",
                    (1 if would_have_stopped else 0, theoretical_pnl,
                     date_str, entry_number)
                )
                conn.commit()

        return self._safe_write("update_skipped_entry_backtest", _write)

    def wal_checkpoint(self) -> bool:
        """Run a passive WAL checkpoint (non-blocking).

        Call once daily at settlement to prevent unbounded WAL growth.
        Does not block readers — checkpoints what it can, skips the rest.
        """
        def _checkpoint():
            with self._connect() as conn:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

        return self._safe_write("wal_checkpoint", _checkpoint)

    def get_yesterday_spx_close(self, today_date: str) -> Optional[float]:
        """Query yesterday's SPX close for overnight gap calculation."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """SELECT spx_close FROM daily_summaries
                    WHERE date < ? ORDER BY date DESC LIMIT 1""",
                    (today_date,)
                ).fetchone()
                return row[0] if row else None
        except Exception:
            return None
