"""LongStrangleDataRecorder — variant H's isolated database. Playbook Step 7.

WHY A SEPARATE FILE AND NOT ``backtesting.db``
-----------------------------------------------
Step 7 applies "only if your rows don't fit ``backtesting.db``'s IC schema".
Step 2 established that they do not, concretely rather than by argument:

``trade_entries`` records what was **collected** — ``call_credit``, ``put_credit``,
``total_credit`` — and has **no column that can hold a debit**. A long strangle
collects nothing and pays ``total_debit``. Writing H to that table would record a
strangle that **cost nothing**, and every shared consumer downstream (the
thin-credit analysis, "expired credits", the ``MAX_PNL_PER_IC`` bounds) would
read that zero as fact.

The alternative — migrating the shared schema to add debit columns — would touch
the table **variant B trades on live**, to serve a dry-run-locked strategy that
has never placed an order. That trade is obviously wrong, which is why D set the
precedent with ``dc_calendar.db`` and H follows it.

DESIGN RULES INHERITED FROM ``dc_recorder.py``
-----------------------------------------------
* **Its own file**: ``data/variant_h/long_strangle.db``. The shared
  ``backtesting.db`` schema version is never bumped and A/B/C/F/G cannot be
  affected by anything here.
* **Fire-and-forget**: every write is wrapped, and a failure is logged at debug
  and swallowed. **Recording must never block or break trading** — a full disk
  should cost a row, not a position.
* **``CREATE TABLE IF NOT EXISTS`` throughout**, so a fresh DB and an existing one
  take the same path and there is no first-run special case.
* **Degrades to a no-op**: if the connection cannot be opened, ``_conn`` stays
  None and every method returns silently. The strategy runs without persistence
  rather than refusing to start.

WHAT IT STORES THAT THE IC SCHEMA CANNOT
-----------------------------------------
``total_debit`` (the premium paid, which is also the maximum loss), the two long
legs' values, and ``pnl_pct_of_debit`` — the unit this strategy's exits are
actually expressed in (+50% / +100%). Exits are recorded with the percentage that
triggered them, so a later analysis can ask whether the +50% target was reached
and given back, which is the question the source's 80%-win-rate claim really
turns on.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


class LongStrangleDataRecorder:
    """Isolated SQLite recorder for variant H. Never raises into the caller."""

    SCHEMA_VERSION = 2

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        try:
            self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema()
            logger.info("LongStrangleDataRecorder initialized: %s", db_path)
        except Exception as e:  # non-critical — never block trading on DB
            logger.warning("LongStrangleDataRecorder init failed (non-critical): %s", e)
            self._conn = None

    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                -- One row per long strangle opened. Note there is no *_credit
                -- column anywhere: nothing is sold in this strategy, and a
                -- credit column would invite exactly the mis-record that made
                -- the isolated DB necessary.
                CREATE TABLE IF NOT EXISTS ls_entries (
                    date TEXT, entry_number INTEGER, entry_time TEXT,
                    spx_at_entry REAL, vix_at_entry REAL,
                    expiry TEXT,
                    call_strike REAL, put_strike REAL,
                    call_debit REAL, put_debit REAL, total_debit REAL,
                    contracts INTEGER,
                    long_call_uic INTEGER, long_put_uic INTEGER,
                    -- which expected-move definition picked these strikes, and
                    -- what it said. Recorded per entry because the choice is
                    -- config-driven and unsettled (Step 3 probe) — without this
                    -- a later analysis cannot separate the two regimes.
                    em_source TEXT, expected_move REAL,
                    call_premium_mid REAL, put_premium_mid REAL, skew_gap_pct REAL,
                    -- The IV reading this entry PASSED at, and the sample it was
                    -- computed over. Only ls_skipped carried these until v2,
                    -- which made the source's "< ~35%" threshold untestable: the
                    -- vetoed side was recorded and the admitted side was not, so
                    -- no later analysis could ask whether 35 was the right line.
                    iv_percentile REAL, iv_percentile_n INTEGER,
                    PRIMARY KEY (date, entry_number)
                );

                -- One row per exit. pnl_pct_of_debit is the unit the strategy's
                -- targets are expressed in (+50% / +100%), so it is stored
                -- directly rather than recomputed from dollars later.
                CREATE TABLE IF NOT EXISTS ls_exits (
                    date TEXT, entry_number INTEGER, exit_time TEXT,
                    exit_reason TEXT,
                    call_exit_value REAL, put_exit_value REAL, total_exit_value REAL,
                    realized_pnl REAL, pnl_pct_of_debit REAL,
                    commissions REAL,
                    spx_at_exit REAL, minutes_held REAL,
                    PRIMARY KEY (date, entry_number)
                );

                -- Periodic marks while a position is open. The reason this
                -- matters more here than for a credit strategy: a long strangle
                -- can touch +50% on a gamma spike and give it all back within a
                -- minute, so the peak is a different number from the exit and
                -- both are needed to judge the exit rule.
                CREATE TABLE IF NOT EXISTS ls_snapshots (
                    date TEXT, entry_number INTEGER, timestamp TEXT,
                    spx REAL, call_value REAL, put_value REAL,
                    total_value REAL, unrealized_pnl REAL, pnl_pct_of_debit REAL
                );

                -- Entries the filters declined, with the reason. The source's
                -- IV-percentile filter and the skew check both reject entries;
                -- without this the dry run cannot tell "no signal" from
                -- "signal, vetoed".
                CREATE TABLE IF NOT EXISTS ls_skipped (
                    date TEXT, entry_number INTEGER, skip_time TEXT,
                    skip_reason TEXT,
                    spx REAL, vix REAL,
                    proposed_call_strike REAL, proposed_put_strike REAL,
                    proposed_debit REAL,
                    em_source TEXT, expected_move REAL,
                    iv_percentile REAL, skew_gap_pct REAL,
                    -- How many prior days the percentile was computed over. A
                    -- bare percentile is uninterpretable: one year and three
                    -- days produce the same shape.
                    iv_percentile_n INTEGER
                );

                CREATE TABLE IF NOT EXISTS ls_schema_info (version INTEGER);
                """
            )
            self._add_missing_columns()
            cur = self._conn.execute("SELECT version FROM ls_schema_info")
            row = cur.fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO ls_schema_info (version) VALUES (?)", (self.SCHEMA_VERSION,)
                )
            elif row[0] != self.SCHEMA_VERSION:
                self._conn.execute(
                    "UPDATE ls_schema_info SET version = ?", (self.SCHEMA_VERSION,)
                )

    #: Columns added after v1. ``CREATE TABLE IF NOT EXISTS`` is a no-op on a
    #: database that already exists, so a new column in the schema above reaches
    #: a FRESH database and never an existing one — H's was created 2026-09-23
    #: and already holds rows. Every addition must therefore be listed here too.
    _ADDED_COLUMNS = (
        ("ls_entries", "iv_percentile", "REAL"),
        ("ls_entries", "iv_percentile_n", "INTEGER"),
        ("ls_skipped", "iv_percentile_n", "INTEGER"),
    )

    def _add_missing_columns(self) -> None:
        """Additive-only migration: ADD COLUMN for anything absent.

        Deliberately the weakest migration that works. It never drops, renames or
        rewrites, so it cannot lose a recorded observation — and since existing
        rows get NULL, a reader can tell "not measured then" from "measured as
        zero", which matters for exactly the interpretation question the
        ``_n`` columns exist to answer.
        """
        for table, column, decl in self._ADDED_COLUMNS:
            try:
                have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if column not in have:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            except sqlite3.Error as e:  # noqa: PERF203 — per-column, keep going
                logger.warning("LS-REC: could not add %s.%s: %s", table, column, e)

    # ------------------------------------------------------------------

    def _exec(self, sql: str, params: tuple) -> None:
        """Fire-and-forget. A recording failure must never reach the trading loop."""
        if not self._conn:
            return
        try:
            with self._conn:
                self._conn.execute(sql, params)
        except Exception as e:
            logger.debug("LongStrangleDataRecorder write failed: %s", e)

    # ------------------------------------------------------------------

    def record_entry(self, entry, date: str, spx_at_entry: float,
                     vix_at_entry: float = 0.0, em_source: str = "",
                     expected_move: float = 0.0, skew_gap_pct: float = 0.0,
                     iv_percentile: float = None,
                     iv_percentile_n: int = None) -> None:
        """Persist an opened long strangle."""
        self._exec(
            """INSERT OR REPLACE INTO ls_entries
               (date, entry_number, entry_time, spx_at_entry, vix_at_entry, expiry,
                call_strike, put_strike, call_debit, put_debit, total_debit, contracts,
                long_call_uic, long_put_uic, em_source, expected_move,
                call_premium_mid, put_premium_mid, skew_gap_pct,
                iv_percentile, iv_percentile_n)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                date, entry.entry_number,
                entry.entry_time.isoformat() if getattr(entry, "entry_time", None) else None,
                spx_at_entry, vix_at_entry, getattr(entry, "expiry", None),
                entry.long_call_strike, entry.long_put_strike,
                getattr(entry, "call_debit", 0.0), getattr(entry, "put_debit", 0.0),
                getattr(entry, "total_debit", 0.0), entry.contracts,
                getattr(entry, "long_call_uic", None), getattr(entry, "long_put_uic", None),
                em_source, expected_move,
                getattr(entry, "long_call_price", None), getattr(entry, "long_put_price", None),
                skew_gap_pct, iv_percentile, iv_percentile_n,
            ),
        )

    def record_exit(self, entry, date: str, exit_reason: str, realized_pnl: float,
                    commissions: float = 0.0, spx_at_exit: float = 0.0,
                    minutes_held: float = 0.0, exit_time: str = "") -> None:
        """Persist a closed long strangle, in dollars AND as a % of the debit."""
        self._exec(
            """INSERT OR REPLACE INTO ls_exits
               (date, entry_number, exit_time, exit_reason,
                call_exit_value, put_exit_value, total_exit_value,
                realized_pnl, pnl_pct_of_debit, commissions, spx_at_exit, minutes_held)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                date, entry.entry_number, exit_time, exit_reason,
                getattr(entry, "call_leg_value", 0.0), getattr(entry, "put_leg_value", 0.0),
                getattr(entry, "current_value", 0.0),
                realized_pnl, getattr(entry, "pnl_pct_of_debit", 0.0),
                commissions, spx_at_exit, minutes_held,
            ),
        )

    def record_snapshot(self, entry, date: str, timestamp: str, spx: float = 0.0) -> None:
        """A periodic mark. Captures the PEAK a +50% target may have touched and
        given back — a distinct number from the exit, and the one the source's
        win-rate claim actually rests on."""
        self._exec(
            """INSERT INTO ls_snapshots
               (date, entry_number, timestamp, spx, call_value, put_value,
                total_value, unrealized_pnl, pnl_pct_of_debit)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                date, entry.entry_number, timestamp, spx,
                getattr(entry, "call_leg_value", 0.0), getattr(entry, "put_leg_value", 0.0),
                getattr(entry, "current_value", 0.0),
                getattr(entry, "unrealized_pnl", 0.0),
                getattr(entry, "pnl_pct_of_debit", 0.0),
            ),
        )

    def record_skip(self, date: str, entry_number: int, skip_time: str, skip_reason: str,
                    spx: float = 0.0, vix: float = 0.0,
                    proposed_call_strike: float = 0.0, proposed_put_strike: float = 0.0,
                    proposed_debit: float = 0.0, em_source: str = "",
                    expected_move: float = 0.0, iv_percentile: float = None,
                    skew_gap_pct: float = 0.0,
                    iv_percentile_n: int = None) -> None:
        """A declined entry, with enough context to score the decision later —
        the counterfactual the GEX work had to be retro-fitted for on variant B
        and could never recover for its first 95 vetoes."""
        self._exec(
            """INSERT INTO ls_skipped
               (date, entry_number, skip_time, skip_reason, spx, vix,
                proposed_call_strike, proposed_put_strike, proposed_debit,
                em_source, expected_move, iv_percentile, skew_gap_pct,
                iv_percentile_n)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                date, entry_number, skip_time, skip_reason, spx, vix,
                proposed_call_strike, proposed_put_strike, proposed_debit,
                em_source, expected_move, iv_percentile, skew_gap_pct,
                iv_percentile_n,
            ),
        )

    def fetch_entries(self, date: str) -> list:
        """Every entry recorded for ``date``, as plain dicts. Read-only.

        Exists for RESTART RECOVERY, and that is worth stating because a
        recorder is otherwise write-only by design. The shared state file
        persists only credit-shaped fields and its restore path hardcodes
        ``HydraIronCondorEntry`` — so a mid-day restart would hand variant H back
        an entry with **no debit at all**, which is both unmanageable (no
        percentage of a zero debit) and unsettleable.

        Rather than touch the fix-scarred shared save/load to carry a field only
        one dry-run-locked strategy needs, H reads its cost basis back out of its
        own database. The row is written before the position is ever monitored,
        so it is always there first.

        Returns ``[]`` on any failure — recovery degrades to "no entries
        recovered", which the caller reports loudly, rather than raising into
        startup.
        """
        if not self._conn:
            return []
        try:
            cur = self._conn.execute(
                "SELECT * FROM ls_entries WHERE date = ? ORDER BY entry_number",
                (date,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.warning("LongStrangleDataRecorder read failed: %s", e)
            return []

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover - shutdown best-effort
                pass
            self._conn = None
