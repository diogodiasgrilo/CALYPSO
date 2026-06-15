"""DCDataRecorder — Strategy D's calendar-shaped SQLite tables (Phase 6).

The shared DataRecorder's schema is a single-expiry CREDIT iron condor (trade_entries
with credit columns, no expiry/DTE, no transformation event), so it cannot record a
multi-day net-DEBIT double calendar truthfully. Rather than bump the shared
SCHEMA_VERSION (which would migrate A/B/C's DBs too), D gets its OWN additive
tables in its OWN physically-separate DB file (data/variant_d/dc_calendar.db,
derived from the variant state-file directory) — NOT the shared backtesting.db
the base DataRecorder uses for market_ticks. This recorder owns only those
``dc_*`` tables; the shared DataRecorder is left completely untouched, so A/B/C
are byte-identical. All writes are fire-and-forget (never raise into the trading loop).

Tables (CREATE IF NOT EXISTS; own dc_schema_info, independent of the shared one):
  - dc_calendar_entries   one row per opened double calendar (debit, 2 expiries)
  - dc_transformations    one row when the transformer fires (-> risk-free IC)
  - dc_outcomes           one row per terminal state (stop / eod / settled);
                          carries BOTH entry_date and close_date so realized P&L
                          can be attributed to the ENTRY date.
  - dc_calendar_snapshots per-tick mark (debit-aware), replacing the IC-shaped
                          spread_snapshots for D.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date as _date
from typing import Optional

logger = logging.getLogger(__name__)


def _dte(from_iso: Optional[str], to_iso: Optional[str]) -> Optional[int]:
    if not from_iso or not to_iso:
        return None
    try:
        return (_date.fromisoformat(to_iso) - _date.fromisoformat(from_iso)).days
    except (ValueError, TypeError):
        return None


class DCDataRecorder:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        try:
            self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema()
            logger.info("DCDataRecorder initialized: %s", db_path)
        except Exception as e:  # non-critical — never block trading on DB
            logger.warning("DCDataRecorder init failed (non-critical): %s", e)
            self._conn = None

    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS dc_calendar_entries (
                    date TEXT, entry_number INTEGER, strategy_id TEXT, structure TEXT,
                    entry_time TEXT, spx_at_entry REAL,
                    short_expiry TEXT, long_expiry TEXT, short_dte INTEGER, long_dte INTEGER,
                    call_strike REAL, put_strike REAL, net_debit REAL, contracts INTEGER,
                    short_call_uic INTEGER, long_call_uic INTEGER,
                    short_put_uic INTEGER, long_put_uic INTEGER,
                    PRIMARY KEY (date, entry_number)
                );
                CREATE TABLE IF NOT EXISTS dc_transformations (
                    date TEXT, entry_number INTEGER, strategy_id TEXT, transform_time TEXT,
                    transform_credit REAL, net_debit REAL, wing_width REAL, is_risk_free INTEGER,
                    wing_call_strike REAL, wing_put_strike REAL,
                    call_spread_credit REAL, put_spread_credit REAL,
                    PRIMARY KEY (date, entry_number)
                );
                CREATE TABLE IF NOT EXISTS dc_outcomes (
                    entry_date TEXT, close_date TEXT, entry_number INTEGER, strategy_id TEXT,
                    terminal_state TEXT, realized_pnl REAL, spx_at_close REAL,
                    close_commission REAL, transform_credit REAL, net_debit REAL,
                    PRIMARY KEY (entry_date, entry_number)
                );
                CREATE TABLE IF NOT EXISTS dc_calendar_snapshots (
                    timestamp TEXT, entry_number INTEGER, dc_phase TEXT,
                    net_debit REAL, calendar_value REAL, unrealized_pnl REAL,
                    short_call_price REAL, long_call_price REAL,
                    short_put_price REAL, long_put_price REAL
                );
                CREATE TABLE IF NOT EXISTS dc_schema_info (version INTEGER);
                """
            )
            cur = self._conn.execute("SELECT version FROM dc_schema_info LIMIT 1")
            if cur.fetchone() is None:
                self._conn.execute("INSERT INTO dc_schema_info (version) VALUES (?)", (self.SCHEMA_VERSION,))

    def _exec(self, sql: str, params: tuple) -> None:
        if not self._conn:
            return
        try:
            with self._conn:
                self._conn.execute(sql, params)
        except Exception as e:
            logger.debug("DCDataRecorder write failed: %s", e)

    # ------------------------------------------------------------------

    def record_calendar_entry(self, entry, spx_at_entry: float, date: str) -> None:
        self._exec(
            """INSERT OR REPLACE INTO dc_calendar_entries
               (date, entry_number, strategy_id, structure, entry_time, spx_at_entry,
                short_expiry, long_expiry, short_dte, long_dte, call_strike, put_strike,
                net_debit, contracts, short_call_uic, long_call_uic, short_put_uic, long_put_uic)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                date, entry.entry_number, getattr(entry, "strategy_id", ""),
                getattr(entry, "structure", "double_calendar"),
                entry.entry_time.isoformat() if entry.entry_time else None, spx_at_entry,
                entry.short_expiry, entry.long_expiry,
                _dte(date, entry.short_expiry), _dte(date, entry.long_expiry),
                entry.short_call_strike, entry.short_put_strike,
                entry.net_debit, entry.contracts,
                entry.short_call_uic, entry.long_call_uic,
                entry.short_put_uic, entry.long_put_uic,
            ),
        )

    def record_transformation(self, entry, date: str) -> None:
        self._exec(
            """INSERT OR REPLACE INTO dc_transformations
               (date, entry_number, strategy_id, transform_time, transform_credit, net_debit,
                wing_width, is_risk_free, wing_call_strike, wing_put_strike,
                call_spread_credit, put_spread_credit)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                date, entry.entry_number, getattr(entry, "strategy_id", ""),
                entry.transformed_at, entry.transform_credit, entry.net_debit,
                entry.wing_width, 1 if entry.is_risk_free else 0,
                entry.long_call_strike, entry.long_put_strike,  # wings post-transform
                entry.call_spread_credit, entry.put_spread_credit,
            ),
        )

    def record_outcome(self, entry, terminal_state: str, realized_pnl: float,
                       spx_at_close: Optional[float], entry_date: str, close_date: str) -> None:
        self._exec(
            """INSERT OR REPLACE INTO dc_outcomes
               (entry_date, close_date, entry_number, strategy_id, terminal_state,
                realized_pnl, spx_at_close, close_commission, transform_credit, net_debit)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                entry_date, close_date, entry.entry_number, getattr(entry, "strategy_id", ""),
                terminal_state, realized_pnl, spx_at_close,
                getattr(entry, "close_commission", 0.0), entry.transform_credit, entry.net_debit,
            ),
        )

    def record_snapshot(self, entry, timestamp: str) -> None:
        self._exec(
            """INSERT INTO dc_calendar_snapshots
               (timestamp, entry_number, dc_phase, net_debit, calendar_value, unrealized_pnl,
                short_call_price, long_call_price, short_put_price, long_put_price)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                timestamp, entry.entry_number, entry.dc_phase.value, entry.net_debit,
                getattr(entry, "calendar_value", 0.0), entry.unrealized_pnl,
                entry.short_call_price, entry.long_call_price,
                entry.short_put_price, entry.long_put_price,
            ),
        )
