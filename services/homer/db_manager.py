"""
HOMER Backtesting Database Manager

SQLite database for storing historical market data and trade execution records.
Used for backtesting 0DTE SPX iron condor strategy variations.

Tables:
    market_ticks      - Heartbeat snapshots (~11s intervals, SPX/VIX prices)
    market_ohlc_1min  - 1-minute OHLC bars computed from ticks
    trade_entries     - Iron condor entry details (strikes, credits, signals)
    trade_stops       - Stop loss events (debit, P&L)
    daily_summaries   - End-of-day totals (SPX OHLC, P&L, entry/stop counts)
    spread_snapshots  - Per-entry spread values over time (for stop formula backtesting)
    schema_info       - Schema version tracking for future migrations
"""

import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 7

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS market_ticks (
    timestamp TEXT PRIMARY KEY,
    spx_price REAL NOT NULL,
    vix_level REAL,
    trend_signal TEXT,
    bot_state TEXT,
    entry_count INTEGER,
    active_count INTEGER
);

CREATE TABLE IF NOT EXISTS market_ohlc_1min (
    timestamp TEXT PRIMARY KEY,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    vix REAL
);

CREATE TABLE IF NOT EXISTS trade_entries (
    date TEXT NOT NULL,
    entry_number INTEGER NOT NULL,
    entry_time TEXT,
    spx_at_entry REAL,
    vix_at_entry REAL,
    expected_move REAL,
    trend_signal TEXT,
    entry_type TEXT,
    override_reason TEXT,
    short_call_strike REAL,
    long_call_strike REAL,
    short_put_strike REAL,
    long_put_strike REAL,
    call_credit REAL,
    put_credit REAL,
    total_credit REAL,
    call_spread_width REAL,
    put_spread_width REAL,
    mkt031_score INTEGER,
    mkt031_early INTEGER,
    otm_distance_call REAL,
    otm_distance_put REAL,
    delta_call REAL,
    delta_put REAL,
    theta_call REAL,
    theta_put REAL,
    vega_call REAL,
    vega_put REAL,
    bid_ask_width_call REAL,
    bid_ask_width_put REAL,
    time_to_fill_ms INTEGER,
    slippage_call REAL,
    slippage_put REAL,
    margin_available REAL,
    margin_utilization_pct REAL,
    config_version TEXT,
    attempts INTEGER DEFAULT 1,
    PRIMARY KEY (date, entry_number)
);

CREATE TABLE IF NOT EXISTS trade_stops (
    date TEXT NOT NULL,
    entry_number INTEGER NOT NULL,
    side TEXT NOT NULL,
    stop_time TEXT,
    spx_at_stop REAL,
    trigger_level REAL,
    actual_debit REAL,
    net_pnl REAL,
    salvage_sold INTEGER DEFAULT 0,
    salvage_revenue REAL DEFAULT 0.0,
    confirmation_seconds INTEGER DEFAULT 0,
    breach_recoveries INTEGER DEFAULT 0,
    quoted_mid_at_stop REAL,
    slippage_on_close REAL,
    spx_move_since_entry REAL,
    minutes_held REAL,
    cascade_gap_seconds REAL,
    PRIMARY KEY (date, entry_number, side)
);

CREATE TABLE IF NOT EXISTS daily_summaries (
    date TEXT PRIMARY KEY,
    spx_open REAL,
    spx_close REAL,
    spx_high REAL,
    spx_low REAL,
    day_range REAL,
    vix_open REAL,
    vix_close REAL,
    entries_placed INTEGER,
    entries_stopped INTEGER,
    entries_expired INTEGER,
    gross_pnl REAL,
    net_pnl REAL,
    commission REAL,
    long_salvage_revenue REAL DEFAULT 0.0,
    day_type TEXT,
    day_of_week TEXT,
    overnight_gap REAL,
    realized_volatility REAL,
    economic_events TEXT,
    config_version TEXT,
    opex_week INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schema_info (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS spread_snapshots (
    timestamp TEXT NOT NULL,
    entry_number INTEGER NOT NULL,
    call_spread_value REAL,
    put_spread_value REAL,
    short_call_price REAL,
    long_call_price REAL,
    short_put_price REAL,
    long_put_price REAL,
    short_call_bid REAL,
    short_call_ask REAL,
    long_call_bid REAL,
    long_call_ask REAL,
    short_put_bid REAL,
    short_put_ask REAL,
    long_put_bid REAL,
    long_put_ask REAL,
    PRIMARY KEY (timestamp, entry_number)
);

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

CREATE TABLE IF NOT EXISTS shadow_entries (
    date TEXT NOT NULL,
    entry_number INTEGER NOT NULL,
    entry_time TEXT,
    spx_at_entry REAL,
    vix_at_entry REAL,
    vix_regime INTEGER,
    shadow_call_otm_target REAL,
    shadow_put_otm_target REAL,
    shadow_short_call_strike REAL,
    shadow_long_call_strike REAL,
    shadow_short_put_strike REAL,
    shadow_long_put_strike REAL,
    shadow_spread_width REAL,
    actual_short_call_strike REAL,
    actual_short_put_strike REAL,
    actual_otm_distance_call REAL,
    actual_otm_distance_put REAL,
    actual_call_credit REAL,
    actual_put_credit REAL,
    actual_entry_type TEXT,
    is_skipped INTEGER DEFAULT 0,
    skip_reason TEXT,
    PRIMARY KEY (date, entry_number)
);

CREATE INDEX IF NOT EXISTS idx_ticks_date ON market_ticks(substr(timestamp, 1, 10));
CREATE INDEX IF NOT EXISTS idx_ohlc_date ON market_ohlc_1min(substr(timestamp, 1, 10));
CREATE INDEX IF NOT EXISTS idx_entries_date ON trade_entries(date);
CREATE INDEX IF NOT EXISTS idx_stops_date ON trade_stops(date);
CREATE INDEX IF NOT EXISTS idx_spreads_date ON spread_snapshots(substr(timestamp, 1, 10));
CREATE INDEX IF NOT EXISTS idx_skipped_date ON skipped_entries(date);
CREATE INDEX IF NOT EXISTS idx_mae_mfe_date ON entry_mae_mfe(date);
"""


class BacktestingDB:
    """SQLite database for backtesting data storage and retrieval."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_dir()
        self._init_db()

    def _ensure_dir(self):
        """Create parent directory if it doesn't exist."""
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _init_db(self):
        """Create tables, run migrations, and set pragmas."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(CREATE_TABLES_SQL)
            self._run_migrations(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_info (key, value) VALUES (?, ?)",
                ("version", str(SCHEMA_VERSION)),
            )

    def _run_migrations(self, conn: sqlite3.Connection):
        """Apply schema migrations for existing databases."""
        # Check current version
        try:
            row = conn.execute(
                "SELECT value FROM schema_info WHERE key = 'version'"
            ).fetchone()
            current = int(row[0]) if row else 0
        except Exception:
            current = 0

        if current < 2:
            # v2: MKT-033 long leg salvage columns
            for col, default in [
                ("salvage_sold", "0"),
                ("salvage_revenue", "0.0"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE trade_stops ADD COLUMN {col} "
                        f"{'INTEGER' if col == 'salvage_sold' else 'REAL'} "
                        f"DEFAULT {default}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists
            try:
                conn.execute(
                    "ALTER TABLE daily_summaries ADD COLUMN "
                    "long_salvage_revenue REAL DEFAULT 0.0"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
            logger.info("DB migrated to schema v2 (MKT-033 salvage columns)")

        if current < 3:
            # v3: spread_snapshots table for stop formula backtesting
            # Table is created by CREATE_TABLES_SQL above, just log migration
            logger.info("DB migrated to schema v3 (spread_snapshots table)")

        if current < 4:
            # v4: MKT-036 stop confirmation timer columns
            for col, col_type, default in [
                ("confirmation_seconds", "INTEGER", "0"),
                ("breach_recoveries", "INTEGER", "0"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE trade_stops ADD COLUMN {col} "
                        f"{col_type} DEFAULT {default}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists
            logger.info("DB migrated to schema v4 (MKT-036 confirmation columns)")

        if current < 5:
            # v5: DataRecorder enrichment columns
            v5_alters = [
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
            for sql in v5_alters:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass  # Column already exists
            # New tables created by CREATE_TABLES_SQL above
            logger.info("DB migrated to schema v5 (DataRecorder enrichment columns)")

        if current < 6:
            # v6: bid/ask capture in spread_snapshots for backtest calibration
            v6_alters = [
                "ALTER TABLE spread_snapshots ADD COLUMN short_call_bid REAL",
                "ALTER TABLE spread_snapshots ADD COLUMN short_call_ask REAL",
                "ALTER TABLE spread_snapshots ADD COLUMN long_call_bid REAL",
                "ALTER TABLE spread_snapshots ADD COLUMN long_call_ask REAL",
                "ALTER TABLE spread_snapshots ADD COLUMN short_put_bid REAL",
                "ALTER TABLE spread_snapshots ADD COLUMN short_put_ask REAL",
                "ALTER TABLE spread_snapshots ADD COLUMN long_put_bid REAL",
                "ALTER TABLE spread_snapshots ADD COLUMN long_put_ask REAL",
            ]
            for sql in v6_alters:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass  # Column already exists
            logger.info("DB migrated to schema v6 (bid/ask capture for calibration)")

        if current < 7:
            # v7: shadow_entries table for OTM-based selection counterfactual
            # Created by CREATE_TABLES_SQL above, add index for date queries
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_shadow_date ON shadow_entries(date)"
                )
            except sqlite3.OperationalError:
                pass
            logger.info("DB migrated to schema v7 (shadow_entries table)")

    def _connect(self) -> sqlite3.Connection:
        """Create a new connection with WAL mode."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # =========================================================================
    # INSERT METHODS (all idempotent via INSERT OR IGNORE)
    # =========================================================================

    def insert_market_ticks(self, ticks: List[Dict[str, Any]]) -> int:
        """
        Insert heartbeat tick data. Returns count of rows inserted.

        Each tick dict should have: timestamp, spx_price, vix_level,
        trend_signal, bot_state, entry_count, active_count.
        """
        if not ticks:
            return 0
        sql = """
            INSERT OR IGNORE INTO market_ticks
            (timestamp, spx_price, vix_level, trend_signal, bot_state, entry_count, active_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                t["timestamp"],
                t["spx_price"],
                t.get("vix_level"),
                t.get("trend_signal"),
                t.get("bot_state"),
                t.get("entry_count"),
                t.get("active_count"),
            )
            for t in ticks
        ]
        with self._connect() as conn:
            conn.executemany(sql, rows)
            inserted = conn.total_changes
        return inserted

    def insert_ohlc_1min(self, bars: List[Dict[str, Any]]) -> int:
        """Insert 1-minute OHLC bars computed from ticks. Returns rows inserted."""
        if not bars:
            return 0
        sql = """
            INSERT OR IGNORE INTO market_ohlc_1min
            (timestamp, open, high, low, close, vix)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        rows = [
            (b["timestamp"], b["open"], b["high"], b["low"], b["close"], b.get("vix"))
            for b in bars
        ]
        with self._connect() as conn:
            conn.executemany(sql, rows)
            inserted = conn.total_changes
        return inserted

    def insert_trade_entries(self, entries: List[Dict[str, Any]]) -> int:
        """Insert trade entry records. Returns rows inserted."""
        if not entries:
            return 0
        sql = """
            INSERT OR IGNORE INTO trade_entries
            (date, entry_number, entry_time, spx_at_entry, vix_at_entry,
             expected_move, trend_signal, entry_type, override_reason,
             short_call_strike, long_call_strike, short_put_strike, long_put_strike,
             call_credit, put_credit, total_credit,
             call_spread_width, put_spread_width,
             mkt031_score, mkt031_early,
             otm_distance_call, otm_distance_put)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                e["date"],
                e["entry_number"],
                e.get("entry_time"),
                e.get("spx_at_entry"),
                e.get("vix_at_entry"),
                e.get("expected_move"),
                e.get("trend_signal"),
                e.get("entry_type"),
                e.get("override_reason"),
                e.get("short_call_strike"),
                e.get("long_call_strike"),
                e.get("short_put_strike"),
                e.get("long_put_strike"),
                e.get("call_credit"),
                e.get("put_credit"),
                e.get("total_credit"),
                e.get("call_spread_width"),
                e.get("put_spread_width"),
                e.get("mkt031_score"),
                e.get("mkt031_early"),
                e.get("otm_distance_call"),
                e.get("otm_distance_put"),
            )
            for e in entries
        ]
        with self._connect() as conn:
            conn.executemany(sql, rows)
            inserted = conn.total_changes
        return inserted

    def insert_trade_stops(self, stops: List[Dict[str, Any]]) -> int:
        """Insert trade stop records. Returns rows inserted."""
        if not stops:
            return 0
        sql = """
            INSERT OR IGNORE INTO trade_stops
            (date, entry_number, side, stop_time, spx_at_stop,
             trigger_level, actual_debit, net_pnl,
             salvage_sold, salvage_revenue,
             confirmation_seconds, breach_recoveries)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                s["date"],
                s["entry_number"],
                s["side"],
                s.get("stop_time"),
                s.get("spx_at_stop"),
                s.get("trigger_level"),
                s.get("actual_debit"),
                s.get("net_pnl"),
                1 if s.get("salvage_sold") else 0,
                s.get("salvage_revenue", 0.0),
                s.get("confirmation_seconds", 0),
                s.get("breach_recoveries", 0),
            )
            for s in stops
        ]
        with self._connect() as conn:
            conn.executemany(sql, rows)
            inserted = conn.total_changes
        return inserted

    def insert_daily_summary(self, summary: Dict[str, Any]) -> int:
        """Insert a daily summary record. Returns 1 if inserted, 0 if duplicate."""
        sql = """
            INSERT OR IGNORE INTO daily_summaries
            (date, spx_open, spx_close, spx_high, spx_low, day_range,
             vix_open, vix_close,
             entries_placed, entries_stopped, entries_expired,
             gross_pnl, net_pnl, commission, long_salvage_revenue,
             day_type, day_of_week)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        row = (
            summary["date"],
            summary.get("spx_open"),
            summary.get("spx_close"),
            summary.get("spx_high"),
            summary.get("spx_low"),
            summary.get("day_range"),
            summary.get("vix_open"),
            summary.get("vix_close"),
            summary.get("entries_placed"),
            summary.get("entries_stopped"),
            summary.get("entries_expired"),
            summary.get("gross_pnl"),
            summary.get("net_pnl"),
            summary.get("commission"),
            summary.get("long_salvage_revenue", 0.0),
            summary.get("day_type"),
            summary.get("day_of_week"),
        )
        with self._connect() as conn:
            cursor = conn.execute(sql, row)
            return cursor.rowcount

    def insert_spread_snapshots(self, snapshots: List[Dict[str, Any]]) -> int:
        """Insert per-entry spread value snapshots (v6 schema). Returns rows inserted.

        Accepts dicts with any subset of the v6 fields:
            timestamp, entry_number, call_spread_value, put_spread_value,
            short_call_price, long_call_price, short_put_price, long_put_price,
            short_call_bid, short_call_ask, long_call_bid, long_call_ask,
            short_put_bid, short_put_ask, long_put_bid, long_put_ask
        Missing fields default to NULL. Backwards compatible with v5-style dicts
        (only timestamp, entry_number, call_spread_value, put_spread_value).
        """
        if not snapshots:
            return 0
        sql = """
            INSERT OR IGNORE INTO spread_snapshots
            (timestamp, entry_number, call_spread_value, put_spread_value,
             short_call_price, long_call_price, short_put_price, long_put_price,
             short_call_bid, short_call_ask, long_call_bid, long_call_ask,
             short_put_bid, short_put_ask, long_put_bid, long_put_ask)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                s["timestamp"],
                s["entry_number"],
                s.get("call_spread_value"),
                s.get("put_spread_value"),
                s.get("short_call_price"),
                s.get("long_call_price"),
                s.get("short_put_price"),
                s.get("long_put_price"),
                s.get("short_call_bid"),
                s.get("short_call_ask"),
                s.get("long_call_bid"),
                s.get("long_call_ask"),
                s.get("short_put_bid"),
                s.get("short_put_ask"),
                s.get("long_put_bid"),
                s.get("long_put_ask"),
            )
            for s in snapshots
        ]
        with self._connect() as conn:
            conn.executemany(sql, rows)
            inserted = conn.total_changes
        return inserted

    # =========================================================================
    # QUERY HELPERS
    # =========================================================================

    def has_data_for_date(self, table: str, date_str: str) -> bool:
        """Check if a table has any data for the given date (YYYY-MM-DD)."""
        allowed_tables = {
            "market_ticks",
            "market_ohlc_1min",
            "trade_entries",
            "trade_stops",
            "daily_summaries",
            "spread_snapshots",
            "skipped_entries",
            "entry_mae_mfe",
        }
        if table not in allowed_tables:
            raise ValueError(f"Unknown table: {table}")

        if table in ("market_ticks", "market_ohlc_1min", "spread_snapshots"):
            sql = f"SELECT 1 FROM {table} WHERE substr(timestamp, 1, 10) = ? LIMIT 1"
        else:
            sql = f"SELECT 1 FROM {table} WHERE date = ? LIMIT 1"

        with self._connect() as conn:
            result = conn.execute(sql, (date_str,)).fetchone()
        return result is not None

    def get_date_range(self) -> Optional[tuple]:
        """Get (min_date, max_date) from market_ticks table."""
        with self._connect() as conn:
            result = conn.execute(
                "SELECT MIN(substr(timestamp, 1, 10)), MAX(substr(timestamp, 1, 10)) "
                "FROM market_ticks"
            ).fetchone()
        if result and result[0]:
            return result
        return None

    def get_table_counts(self) -> Dict[str, int]:
        """Get row counts for all data tables."""
        tables = [
            "market_ticks",
            "market_ohlc_1min",
            "trade_entries",
            "trade_stops",
            "daily_summaries",
            "spread_snapshots",
        ]
        counts = {}
        with self._connect() as conn:
            for table in tables:
                result = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[table] = result[0] if result else 0
        return counts
