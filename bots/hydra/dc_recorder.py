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
    SCHEMA_VERSION = 3

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
                    mid_net_debit REAL, touch_net_debit REAL,
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
                    short_put_price REAL, long_put_price REAL,
                    mid_calendar_value REAL, touch_calendar_value REAL,
                    fill_agg REAL, fill_slip REAL,
                    strategy_id TEXT, entry_date TEXT
                );
                CREATE TABLE IF NOT EXISTS dc_transform_attempts (
                    timestamp TEXT, date TEXT, strategy_id TEXT, entry_number INTEGER,
                    outcome TEXT,
                    transform_credit REAL, threshold REAL, margin REAL,
                    net_debit REAL, wing_width REAL, contracts INTEGER,
                    long_call_px REAL, long_put_px REAL,
                    wing_call_px REAL, wing_put_px REAL,
                    short_call_px REAL, short_put_px REAL,
                    mid_credit REAL, touch_credit REAL, fill_agg REAL
                );
                CREATE TABLE IF NOT EXISTS dc_schema_info (version INTEGER);
                """
            )
            cur = self._conn.execute("SELECT version FROM dc_schema_info LIMIT 1")
            row = cur.fetchone()
            if row is None:
                self._conn.execute("INSERT INTO dc_schema_info (version) VALUES (?)", (self.SCHEMA_VERSION,))
            else:
                self._migrate(int(row[0]))

    def _migrate(self, from_version: int) -> None:
        """Additive-only migrations for an EXISTING dc_calendar.db.

        The CREATE TABLE statements above are ``IF NOT EXISTS``, so a DB created
        under an older schema keeps its original columns and needs explicit
        ALTERs. Every migration here must be additive (ADD COLUMN with a NULL
        default) — this DB holds D's and E's entire trade history and must never
        be rewritten in place.

        v2 -> v3 (2026-09-09): attribution + the transform-gate telemetry.
          * ``dc_calendar_snapshots`` gains ``strategy_id`` / ``entry_date``.
            Until now a snapshot carried only ``entry_number``, which is
            ALWAYS 1 for D and E (verified: the distinct set is literally
            [1]) — so 59,305 D rows and 69,660 E rows were unattributable to
            a specific trade except by timestamp-range guesswork.
          * a UNIQUE index on ``dc_outcomes.strategy_id``. The table's PK is
            ``(entry_date, entry_number)`` and ``INSERT OR REPLACE`` keys on
            it; with entry_number pinned at 1 that silently overwrites if two
            entries ever share an entry_date. No collision has happened yet,
            but the concurrency slot HAS been freed early three times by the
            vanished-position bug, which is exactly how it would.
          * ``dc_transform_attempts``, the reason for this migration. D's
            transform gate is evaluated on every monitoring tick — roughly 600
            times per trade — but only the 2 evaluations that FIRED were ever
            persisted. Everything else went to a rotating log. That reduced
            the strategy's central question to a 2-of-8 binary when a
            continuous distance-to-gate series was being computed and thrown
            away. Each row stores the six leg prices plus the credit at mid and
            at full touch, so the gate is recomputable offline at ANY fill
            aggressiveness.

        v1 -> v2 (2026-09-07): record the same marks priced at MID and FULL TOUCH
        so any fill aggressiveness is recoverable offline by interpolation. The
        v1 schema stored only the post-haircut price, discarding the bid/ask —
        which is why the 2026-09-06 forensic could not re-price D's history after
        discovering it had run 19 trades at full touch under a silently-ignored
        ``dry_run_fill_model: 0.5``. Old rows keep NULL in the new columns; that
        is the honest value, since the information was never captured.
        """
        if from_version >= self.SCHEMA_VERSION:
            return
        additions = {
            "dc_calendar_entries": ["mid_net_debit REAL", "touch_net_debit REAL"],
            "dc_calendar_snapshots": [
                "mid_calendar_value REAL", "touch_calendar_value REAL",
                "fill_agg REAL", "fill_slip REAL",
                "strategy_id TEXT", "entry_date TEXT",
            ],
        }
        for table, cols in additions.items():
            try:
                existing = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            except Exception as e:
                logger.warning("DC schema migrate: cannot read %s (%s)", table, e)
                continue
            for coldef in cols:
                name = coldef.split()[0]
                if name in existing:
                    continue
                try:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
                    logger.info("DC schema migrate: added %s.%s", table, name)
                except Exception as e:
                    logger.warning("DC schema migrate: %s.%s failed (%s)", table, name, e)
        # Guard dc_outcomes against the latent INSERT OR REPLACE overwrite.
        # Additive (an index, not a table rebuild) so the trade history is
        # never rewritten. Fails harmlessly if duplicates somehow already
        # exist — better to leave the index off than to lose a row.
        try:
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_dc_outcomes_strategy_id "
                "ON dc_outcomes(strategy_id)"
            )
        except Exception as e:
            logger.warning(
                "DC schema migrate: could not add the dc_outcomes strategy_id "
                "unique index (%s) — duplicate strategy_ids may already exist", e
            )
        self._conn.execute("UPDATE dc_schema_info SET version = ?", (self.SCHEMA_VERSION,))
        logger.info("DC schema migrated v%s -> v%s", from_version, self.SCHEMA_VERSION)

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
                net_debit, contracts, short_call_uic, long_call_uic, short_put_uic, long_put_uic,
                mid_net_debit, touch_net_debit)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                getattr(entry, "mid_net_debit", None) or None,
                getattr(entry, "touch_net_debit", None) or None,
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

    def record_transform_attempt(
        self, entry, timestamp: str, date: str, outcome: str,
        transform_credit=None, threshold=None,
        leg_px: Optional[dict] = None,
        mid_credit=None, touch_credit=None, fill_agg=None,
    ) -> None:
        """One row per EVALUATION of the transform gate, fired or not.

        D's gate runs on every monitoring tick — order 600 times per trade —
        but before 2026-09-09 only the evaluations that FIRED were persisted
        (2 rows, ever). Everything else went to a rotating log, of which ~955
        of ~4,700 evaluations survived. That collapsed the strategy's central
        question into a 2-of-8 binary while a continuous distance-to-gate
        series was being computed and discarded.

        ``leg_px`` carries the six modelled leg prices and ``mid_credit`` /
        ``touch_credit`` the transform credit at aggressiveness 0 and 1, so the
        gate can be recomputed offline at ANY fill assumption — the one input
        that decides this strategy and has never been validated.

        ``outcome`` is one of: ``fired``, ``below_threshold``, ``arb_rejected``,
        ``incomplete_quotes``.
        """
        px = leg_px or {}
        margin = (
            transform_credit - threshold
            if (transform_credit is not None and threshold is not None) else None
        )
        self._exec(
            """INSERT INTO dc_transform_attempts
               (timestamp, date, strategy_id, entry_number, outcome,
                transform_credit, threshold, margin,
                net_debit, wing_width, contracts,
                long_call_px, long_put_px, wing_call_px, wing_put_px,
                short_call_px, short_put_px,
                mid_credit, touch_credit, fill_agg)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                timestamp, date, getattr(entry, "strategy_id", ""),
                entry.entry_number, outcome,
                transform_credit, threshold, margin,
                getattr(entry, "net_debit", None), getattr(entry, "wing_width", None),
                getattr(entry, "contracts", None),
                px.get("long_call"), px.get("long_put"),
                px.get("wing_call"), px.get("wing_put"),
                px.get("short_call"), px.get("short_put"),
                mid_credit, touch_credit, fill_agg,
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
                short_call_price, long_call_price, short_put_price, long_put_price,
                mid_calendar_value, touch_calendar_value, fill_agg, fill_slip,
                strategy_id, entry_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                timestamp, entry.entry_number, entry.dc_phase.value, entry.net_debit,
                getattr(entry, "calendar_value", 0.0), entry.unrealized_pnl,
                entry.short_call_price, entry.long_call_price,
                entry.short_put_price, entry.long_put_price,
                # Record-only (schema v2). NULL when the tick wasn't priced at
                # mid/touch — e.g. a partial quote, or a mark carried over from a
                # prior tick because this one failed the sanity guard. NULL is the
                # honest value there; do NOT coalesce it to 0.0, which would read
                # as "the calendar was worth nothing at mid".
                getattr(entry, "_dc_mark_mid_value", None),
                getattr(entry, "_dc_mark_touch_value", None),
                getattr(entry, "_dc_mark_fill_agg", None),
                getattr(entry, "_dc_mark_fill_slip", None),
                # Attribution (schema v3). entry_number is always 1 on D and E,
                # so without these a snapshot cannot be tied to a trade.
                getattr(entry, "strategy_id", "") or None,
                (getattr(entry, "strategy_id", "") or "")[5:13] or None,
            ),
        )
