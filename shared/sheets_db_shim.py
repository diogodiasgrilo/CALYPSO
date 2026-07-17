"""DB-backed drop-in for the agents' Google-Sheets reader.

Part of the Google-Sheets-retirement migration (2026-07-16). The SQLite
``backtesting.db`` is already written live, in parallel with the Sheet, by the
trading loop's ``DataRecorder`` — so agents don't need the Sheet to get their
data, they just need to read the DB that already has it.

This module returns Sheet-SHAPED dicts ("Daily Summary" tab labels) built from
the ``daily_summaries`` table, so a consumer that currently calls
``SheetsReader.read_tab_as_dicts(ss, "Daily Summary")`` can switch to
``DbSheetsReader(db_path).read_tab_as_dicts(ss, "Daily Summary")`` with NO change
to its downstream parsing. The label map is the exact inverse of HOMER's forward
map ``services/homer/data_collector.py:_build_summary_record`` (kept in sync by
``tests/test_sheets_db_shim.py``, a DB-row -> shim -> _build_summary_record
round-trip).

Scope: the "Daily Summary" tab (CLIO's only source, HERMES's summary context, and
HOMER's summary path). The "Trades"/"Positions" tabs are intentionally NOT served
as synthetic ``Action`` strings — HOMER reads structured ``trade_entries`` /
``trade_stops`` rows directly (see ``entries_for_day`` / ``stops_for_day``).
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional


def _to_float(v: Any) -> float:
    """Coerce a DB value to float (0.0 for None/blank/non-numeric). Real
    daily_summaries columns are REAL, but stay defensive against stray text."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

# daily_summaries DB column -> Google-Sheets "Daily Summary" label.
# Only the labels the agents actually read (HOMER/HERMES/CLIO). Inverse of
# _build_summary_record's summary.get("<label>") reads.
DAILY_DB_TO_SHEET: Dict[str, str] = {
    "date": "Date",
    "spx_open": "SPX Open",
    "spx_close": "SPX Close",
    "spx_high": "SPX High",
    "spx_low": "SPX Low",
    "vix_open": "VIX Open",
    "vix_close": "VIX Close",
    "net_pnl": "Daily P&L ($)",          # Sheet "Daily P&L ($)" is NET (post-commission)
    "commission": "Commission ($)",
    "entries_placed": "Entries Completed",
    "long_salvage_revenue": "Long Salvage ($)",
    "economic_events": "Notes",          # lossy: Sheet Notes is free-form; DB keeps economic_events
}


def _row_to_sheet_dict(
    row: sqlite3.Row, cumulative_pnl: float, call_stops: int, put_stops: int
) -> Dict[str, Any]:
    """One daily_summaries row -> a "Daily Summary"-labeled dict."""
    keys = row.keys()
    out: Dict[str, Any] = {}
    for db_col, sheet_label in DAILY_DB_TO_SHEET.items():
        if db_col in keys:
            val = row[db_col]
            out[sheet_label] = "" if val is None else val
    # Reconstructed fields with no 1:1 daily_summaries column.
    out["Cumulative P&L ($)"] = round(cumulative_pnl, 2)
    out["Call Stops"] = call_stops
    out["Put Stops"] = put_stops
    return out


def make_agent_reader(config: Dict[str, Any]):
    """Factory used by the agents during the Sheets→DB migration.

    Returns a :class:`DbSheetsReader` when ``config["data_source"] == "db"``,
    else the Google ``SheetsReader``. Both expose ``read_tab_as_dicts`` and
    ``get_last_row_as_dict`` with the same signature, so a caller swaps readers
    with no other change. Default is ``"sheets"`` (zero behavior change until an
    operator flips the flag). The DB path comes from ``config["backtesting_db"]``
    (falls back to ``data/backtesting.db``) — set it to the CANONICAL variant's DB
    (e.g. ``data/variant_c/backtesting.db``), not variant A's.
    """
    data_source = str(config.get("data_source") or "sheets").lower()
    if data_source == "db":
        db_path = config.get("backtesting_db") or "data/backtesting.db"
        return DbSheetsReader(db_path)
    from shared.sheets_reader import SheetsReader  # lazy: avoids gspread import in DB mode

    return SheetsReader(config)


class DbSheetsReader:
    """Drop-in for ``shared.sheets_reader.SheetsReader`` backed by backtesting.db.

    Implements the read surface the agents use: ``read_tab_as_dicts`` and
    ``get_last_row_as_dict`` for the "Daily Summary" tab, plus structured
    ``entries_for_day`` / ``stops_for_day`` for HOMER. ``spreadsheet_name`` is
    accepted and ignored (kept for signature-compatibility with SheetsReader).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def read_tab_as_dicts(
        self, spreadsheet_name: str, tab_name: str, limit_rows: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        if tab_name != "Daily Summary":
            # Trades/Positions are served structured via entries_for_day/stops_for_day.
            return []
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM daily_summaries ORDER BY date"
            ).fetchall()
            stop_counts: Dict[str, Dict[str, int]] = {}
            for r in conn.execute(
                "SELECT date, side, COUNT(*) n FROM trade_stops GROUP BY date, side"
            ):
                stop_counts.setdefault(r["date"], {})[r["side"]] = r["n"]
            out: List[Dict[str, Any]] = []
            cumulative = 0.0
            for row in rows:
                cumulative += _to_float(row["net_pnl"])
                sc = stop_counts.get(row["date"], {})
                out.append(
                    _row_to_sheet_dict(row, cumulative, sc.get("call", 0), sc.get("put", 0))
                )
            if limit_rows is not None and limit_rows > 0:
                out = out[-limit_rows:]
            return out
        finally:
            conn.close()

    def get_last_row_as_dict(
        self, spreadsheet_name: str, tab_name: str
    ) -> Optional[Dict[str, Any]]:
        rows = self.read_tab_as_dicts(spreadsheet_name, tab_name)
        return rows[-1] if rows else None

    # --- structured reads for HOMER (no Sheet-string round-trip) ---
    def entries_for_day(self, date_str: str) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM trade_entries WHERE date=? ORDER BY entry_number",
                    (date_str,),
                )
            ]
        finally:
            conn.close()

    def stops_for_day(self, date_str: str) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM trade_stops WHERE date=? ORDER BY entry_number, side",
                    (date_str,),
                )
            ]
        finally:
            conn.close()
