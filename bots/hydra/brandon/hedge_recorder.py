"""BrandonHedgeRecorder — durable hedge-history tracking for the defensive
overlay (2026-08-25).

Before this, the only record of a hedge's placement/outcome was
``data/variant_<id>/brandon_hedge_legs.json`` — wiped every day at
``_reset_for_new_day`` — plus whatever survives in the log file. Reconstructing
"how have the hedges done" required grepping BRANDON-OVERLAY* log lines across
days. This gives it its own physically-separate DB file
(``data/variant_<id>/brandon_hedges.db``), mirroring ``dc_recorder.py``'s
pattern for Strategy D/E: rather than bump the shared ``backtesting.db``
``SCHEMA_VERSION`` (which would add two Brandon-only tables to A/D/E's
databases too, for no benefit), Brandon hedges get their own additive tables
in their own file. All writes are fire-and-forget (never raise into the
trading loop) — a recording failure must never affect trading logic.

Tables (CREATE IF NOT EXISTS; own schema_info, independent of the shared one):
  - hedge_placements   one row per LEG of a placed hedge (a debit spread has
                       2 legs, a butterfly has 3) — carries both leg-level
                       fields (strike, quantity, fill_price, conid) and the
                       placement-level context (structure, gex_confirmed,
                       trigger_distance_at_arm_pts, confirm_seconds,
                       severity_bypassed) duplicated across its legs' rows.
  - hedge_settlements  one row per settled hedge (matches HedgeSettlement).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


class BrandonHedgeRecorder:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        try:
            self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema()
            logger.info("BrandonHedgeRecorder initialized: %s", db_path)
        except Exception as e:  # non-critical — never block trading on DB
            logger.warning("BrandonHedgeRecorder init failed (non-critical): %s", e)
            self._conn = None

    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS hedge_placements (
                    date TEXT, entry_number INTEGER, threatened_side TEXT, structure TEXT,
                    leg_side TEXT, contract_type TEXT, strike REAL, quantity INTEGER,
                    fill_price REAL, conid INTEGER, placed_at TEXT,
                    gex_confirmed INTEGER, trigger_distance_at_arm_pts REAL,
                    confirm_seconds REAL, severity_bypassed INTEGER
                );
                CREATE TABLE IF NOT EXISTS hedge_settlements (
                    date TEXT, entry_number INTEGER, threatened_side TEXT, structure TEXT,
                    spx_close REAL, debit_paid REAL, hedge_pnl REAL, settled_at TEXT
                );
                CREATE TABLE IF NOT EXISTS hedge_schema_info (version INTEGER);
                """
            )
            cur = self._conn.execute("SELECT version FROM hedge_schema_info LIMIT 1")
            if cur.fetchone() is None:
                self._conn.execute(
                    "INSERT INTO hedge_schema_info (version) VALUES (?)", (self.SCHEMA_VERSION,)
                )

    def _exec(self, sql: str, params: tuple) -> None:
        if not self._conn:
            return
        try:
            with self._conn:
                self._conn.execute(sql, params)
        except Exception as e:
            logger.debug("BrandonHedgeRecorder write failed: %s", e)

    # ------------------------------------------------------------------

    def record_hedge_placement(
        self,
        *,
        date: str,
        entry_number: int,
        threatened_side: str,
        structure: str,
        legs: Iterable,
        gex_confirmed: Optional[bool],
        trigger_distance_at_arm_pts: Optional[float],
        confirm_seconds: Optional[float],
        severity_bypassed: bool,
    ) -> None:
        """One row per leg — ``legs`` is an iterable of HedgeLeg."""
        for leg in legs:
            self._exec(
                """INSERT INTO hedge_placements
                   (date, entry_number, threatened_side, structure, leg_side, contract_type,
                    strike, quantity, fill_price, conid, placed_at, gex_confirmed,
                    trigger_distance_at_arm_pts, confirm_seconds, severity_bypassed)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    date, entry_number, threatened_side, structure,
                    leg.side, leg.contract_type, leg.strike, leg.quantity,
                    leg.fill_price, leg.conid,
                    leg.placed_at.isoformat() if leg.placed_at else None,
                    None if gex_confirmed is None else int(gex_confirmed),
                    trigger_distance_at_arm_pts, confirm_seconds,
                    int(bool(severity_bypassed)),
                ),
            )

    def record_hedge_settlement(
        self,
        *,
        date: str,
        entry_number: int,
        threatened_side: str,
        structure: str,
        spx_close: float,
        debit_paid: float,
        hedge_pnl: float,
        settled_at: str,
    ) -> None:
        self._exec(
            """INSERT INTO hedge_settlements
               (date, entry_number, threatened_side, structure, spx_close, debit_paid,
                hedge_pnl, settled_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (date, entry_number, threatened_side, structure, spx_close,
             debit_paid, hedge_pnl, settled_at),
        )
