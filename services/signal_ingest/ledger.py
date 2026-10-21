"""SQLite record of every signal received and what was decided.

This is the POINT of the dry run, not a side effect: it is the instrument that
answers "how many signals per ticker per year?" — the single cheapest fact that
can settle whether automating a signal service is worth anything at all. So it
records REJECTED and DUPLICATE signals too. A ledger that stored only the
admitted ones would answer the easy question and lose the useful one.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

SCHEMA_VERSION = 2

_DDL = """
CREATE TABLE IF NOT EXISTS signals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at       TEXT NOT NULL,
    signal_time       TEXT,
    idempotency_key   TEXT NOT NULL,
    ticker            TEXT,
    action            TEXT,
    source            TEXT,
    quantity_signal   REAL,
    quantity_effective REAL,
    price_hint        REAL,
    outcome           TEXT NOT NULL,   -- would_trade | rejected | duplicate
    reason            TEXT,
    -- v2: what the ADMITTED signal did to the simulated book, so the ledger
    -- can answer "would it have worked?" and not only "how often did it fire?"
    book_effect       TEXT,            -- opened | added | closed | reduced | flat_noop
    realized_pnl      REAL,            -- gross, upper bound (see positions.py)
    book_note         TEXT,
    dry_run           INTEGER NOT NULL DEFAULT 1,
    raw               TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_key  ON signals(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(received_at);
CREATE TABLE IF NOT EXISTS schema_info (key TEXT PRIMARY KEY, value TEXT);
"""


class Ledger:
    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init(self) -> None:
        con = self._connect()
        try:
            con.executescript(_DDL)
            from services.signal_ingest import positions
            positions.ensure_schema(con)
            con.execute("INSERT OR REPLACE INTO schema_info VALUES ('version', ?)",
                        (str(SCHEMA_VERSION),))
            con.commit()
        finally:
            con.close()

    def seen(self, key: str) -> bool:
        """Has this exact signal already been ADMITTED?

        Only `would_trade` rows block a repeat. A previously REJECTED signal
        must be allowed through again — the reason may have been transient
        (market closed, daily cap) and treating a rejection as permanent would
        silently swallow the retry.
        """
        con = self._connect()
        try:
            row = con.execute(
                "SELECT 1 FROM signals WHERE idempotency_key=? AND outcome='would_trade' LIMIT 1",
                (key,)).fetchone()
            return row is not None
        finally:
            con.close()

    def count_today(self, now: Optional[datetime] = None) -> int:
        """Admitted signals so far today (UTC), for the daily cap."""
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
        con = self._connect()
        try:
            return con.execute(
                "SELECT COUNT(*) FROM signals WHERE outcome='would_trade' "
                "AND substr(received_at,1,10)=?", (day,)).fetchone()[0]
        finally:
            con.close()

    def record(self, *, received_at: datetime, key: str, payload: Dict[str, Any],
               outcome: str, reason: Optional[str] = None,
               quantity_effective: Optional[float] = None,
               dry_run: bool = True,
               book_effect: Optional[str] = None,
               realized_pnl: Optional[float] = None,
               book_note: Optional[str] = None) -> int:
        con = self._connect()
        try:
            cur = con.execute(
                "INSERT INTO signals (received_at, signal_time, idempotency_key, ticker,"
                " action, source, quantity_signal, quantity_effective, price_hint,"
                " outcome, reason, book_effect, realized_pnl, book_note, dry_run, raw)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    received_at.astimezone(timezone.utc).isoformat(),
                    str(payload.get("time") or "") or None,
                    key,
                    str(payload.get("ticker") or "").upper() or None,
                    str(payload.get("action") or "").lower() or None,
                    str(payload.get("source") or "") or None,
                    _num(payload.get("quantity")),
                    quantity_effective,
                    _num(payload.get("price")),
                    outcome,
                    reason,
                    book_effect,
                    realized_pnl,
                    book_note,
                    1 if dry_run else 0,
                    json.dumps(_redact(payload), default=str)[:4000],
                ))
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()

    def apply_to_book(self, *, symbol: str, action: str, quantity: float,
                      price: Optional[float], now: Optional[datetime] = None):
        """Apply an ADMITTED signal to the simulated book.

        Returns positions.apply_signal's (effect, realized_pnl, note).
        """
        from services.signal_ingest import positions
        con = self._connect()
        try:
            r = positions.apply_signal(con, symbol=symbol, action=action,
                                       quantity=quantity, price=price, now=now)
            con.commit()
            return r
        finally:
            con.close()

    def book_summary(self) -> Dict[str, Any]:
        """What the dry run has actually learned so far."""
        from services.signal_ingest import positions
        con = self._connect()
        try:
            return positions.summary(con)
        finally:
            con.close()


def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _redact(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Never persist the shared secret, even in the raw copy."""
    return {k: ("***" if k.lower() in ("secret", "token", "password") else v)
            for k, v in payload.items()}
