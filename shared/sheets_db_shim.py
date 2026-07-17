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
    "contracts_per_entry": "Contracts",
    "economic_events": "Notes",          # lossy: Sheet Notes is free-form; DB keeps economic_events
}

# Sheet "Daily Summary" labels that have NO DB source and stay blank in DB mode
# (documented Option-A gap): EUR columns, Capital Deployed, Return on Capital,
# Sortino Ratio, Max Loss Stops/Catastrophic, Early Close. Everything else is
# either mapped above or reconstructed below (validated vs the live Sheet).


def _row_to_sheet_dict(
    row: sqlite3.Row, cumulative_pnl: float, agg: Dict[str, Any]
) -> Dict[str, Any]:
    """One daily_summaries row + its per-day aggregates -> a "Daily Summary"-labeled
    dict. `agg` carries the reconstructed fields (validated field-for-field against
    C's live Sheet for settled days 2026-07-14/15/16)."""
    keys = row.keys()
    out: Dict[str, Any] = {}
    for db_col, sheet_label in DAILY_DB_TO_SHEET.items():
        if db_col in keys:
            val = row[db_col]
            out[sheet_label] = "" if val is None else val
    gross = _to_float(row["gross_pnl"]) if "gross_pnl" in keys else 0.0
    salvage = _to_float(row["long_salvage_revenue"]) if "long_salvage_revenue" in keys else 0.0
    stop_debits = agg.get("stop_debits", 0.0)
    out["Cumulative P&L ($)"] = round(cumulative_pnl, 2)
    out["Total Credit ($)"] = agg.get("total_credit", 0.0)
    out["Full ICs"] = agg.get("full_ics", 0)
    out["One-Sided Entries"] = agg.get("one_sided", 0)
    out["Bullish Signals"] = agg.get("bull", 0)
    out["Bearish Signals"] = agg.get("bear", 0)
    out["Neutral Signals"] = agg.get("neut", 0)
    out["Win Rate (%)"] = agg.get("win_rate", 0.0)
    out["Call Stops"] = agg.get("call_stops", 0)
    out["Put Stops"] = agg.get("put_stops", 0)
    out["Double Stops"] = agg.get("double_stops", 0)
    out["Stop Loss Debits ($)"] = round(stop_debits, 2)
    out["Expired Credits ($)"] = round(gross + stop_debits - salvage, 2)
    out["Entries Skipped"] = agg.get("skipped", 0)
    out["VIX High"] = agg.get("vix_high", "")
    out["VIX Low"] = agg.get("vix_low", "")
    return out


def resolve_agent_source(config: Dict[str, Any], agent: Optional[str] = None):
    """Return ``(data_source, db_path)`` using the same per-agent-then-top-level
    resolution as :func:`make_agent_reader`, WITHOUT constructing a reader — so a
    caller (e.g. HOMER choosing its entries builder) can branch on mode cheaply
    and only build a :class:`DbSheetsReader` when actually in ``"db"`` mode."""
    def _resolve(key, default=None):
        sub = config.get(agent) if agent else None
        if isinstance(sub, dict) and sub.get(key) is not None:
            return sub[key]
        return config.get(key, default)

    data_source = str(_resolve("data_source", "sheets") or "sheets").lower()
    db_path = _resolve("read_db") or _resolve("backtesting_db") or "data/backtesting.db"
    return data_source, db_path


def make_agent_reader(config: Dict[str, Any], *, agent: Optional[str] = None):
    """Factory used by the agents during the Sheets→DB migration.

    Returns a :class:`DbSheetsReader` when the resolved ``data_source == "db"``,
    else the Google ``SheetsReader``. Both expose ``read_tab_as_dicts`` and
    ``get_last_row_as_dict`` with the same signature, so a caller swaps readers
    with no other change. Default is ``"sheets"`` (zero behavior change until an
    operator flips the flag).

    Resolution order for BOTH ``data_source`` and the DB path is per-agent first,
    then top-level, so the migration can flip agents ONE AT A TIME — e.g. set
    ``config["clio"]["data_source"] = "db"`` without touching HERMES/HOMER::

        {"clio": {"data_source": "db", "read_db": "data/variant_c/backtesting.db"}}

    DB path prefers a dedicated ``read_db`` over ``backtesting_db`` so it never
    collides with HOMER's (write) ``backtesting_db``. Point it at the CANONICAL
    variant's DB (``data/variant_c/backtesting.db``), not variant A's.
    """
    data_source, db_path = resolve_agent_source(config, agent)
    if data_source == "db":
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
        if tab_name == "Positions":
            # Recent entries as raw position context (HERMES dumps this to Claude
            # as raw JSON, so structured entry rows are equivalent context). HOMER
            # does NOT use this path — it reads entries_for_day/stops_for_day.
            return self._recent_entries(limit_rows or 20)
        if tab_name != "Daily Summary":
            # "Trades" is served structured via entries_for_day/stops_for_day.
            return []
        conn = self._conn()
        try:
            rows = conn.execute("SELECT * FROM daily_summaries ORDER BY date").fetchall()
            aggs = self._daily_aggregates(conn)
            out: List[Dict[str, Any]] = []
            cumulative = 0.0
            for row in rows:
                cumulative += _to_float(row["net_pnl"])
                out.append(_row_to_sheet_dict(row, cumulative, aggs.get(row["date"], {})))
            if limit_rows is not None and limit_rows > 0:
                out = out[-limit_rows:]
            return out
        finally:
            conn.close()

    def _daily_aggregates(self, conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
        """Per-date reconstructions for the Daily Summary fields that have no 1:1
        daily_summaries column. Each source table is queried defensively — a missing
        table (edge/partial DB) leaves those fields at their defaults rather than
        raising. Formulas validated field-for-field vs C's live Sheet (07-14/15/16)."""
        agg: Dict[str, Dict[str, Any]] = {}

        def _g(d):
            return agg.setdefault(d, {})

        def _try(sql, handler):
            try:
                for r in conn.execute(sql):
                    handler(r)
            except sqlite3.OperationalError:
                pass  # table absent in this DB — leave fields blank/default

        # entries: credit, structure, signals, daily win rate
        def _ent(r):
            g = _g(r["date"])
            g["total_credit"] = round(r["total_credit"] or 0.0, 2)
            g["full_ics"] = r["full_ics"]
            g["one_sided"] = r["one_sided"]
            g["bull"], g["bear"], g["neut"] = r["bull"], r["bear"], r["neut"]
            g["win_rate"] = round(100.0 * r["wins"] / r["n"], 1) if r["n"] else 0.0
        _try(
            "SELECT date, COALESCE(SUM(total_credit),0) total_credit, "
            "SUM(CASE WHEN entry_type IN ('full_ic','ic','Iron Condor') THEN 1 ELSE 0 END) full_ics, "
            "SUM(CASE WHEN entry_type IN ('put_only','call_only','Put Spread','Call Spread') THEN 1 ELSE 0 END) one_sided, "
            "SUM(CASE WHEN trend_signal='bullish' THEN 1 ELSE 0 END) bull, "
            "SUM(CASE WHEN trend_signal='bearish' THEN 1 ELSE 0 END) bear, "
            "SUM(CASE WHEN trend_signal='neutral' THEN 1 ELSE 0 END) neut, "
            # win = realized_pnl >= 0 (a breakeven expiry counts as a win, matching
            # the Sheet — verified 07-06: entry2 closed exactly 0, Sheet win-rate 100%).
            "SUM(CASE WHEN realized_pnl>=0 THEN 1 ELSE 0 END) wins, COUNT(*) n "
            "FROM trade_entries GROUP BY date",
            _ent,
        )
        # stops: per-side counts + net-loss (Stop Loss Debits = -SUM(net_pnl))
        def _st(r):
            g = _g(r["date"])
            g["call_stops"], g["put_stops"] = r["calls"], r["puts"]
            g["stop_debits"] = round(-(r["net"] or 0.0), 2)
        _try(
            "SELECT date, SUM(CASE WHEN side='call' THEN 1 ELSE 0 END) calls, "
            "SUM(CASE WHEN side='put' THEN 1 ELSE 0 END) puts, COALESCE(SUM(net_pnl),0) net "
            "FROM trade_stops WHERE exit_reason IN ('stop_loss','gex_breach') GROUP BY date",
            _st,
        )
        # double stops: entries with BOTH sides stopped
        _try(
            "SELECT date, COUNT(*) n FROM (SELECT date, entry_number FROM trade_stops "
            "WHERE exit_reason IN ('stop_loss','gex_breach') AND side IN ('call','put') "
            "GROUP BY date, entry_number HAVING COUNT(DISTINCT side)=2) GROUP BY date",
            lambda r: _g(r["date"]).__setitem__("double_stops", r["n"]),
        )
        _try(
            "SELECT date, COUNT(*) n FROM skipped_entries GROUP BY date",
            lambda r: _g(r["date"]).__setitem__("skipped", r["n"]),
        )
        # VIX high/low from ticks (market_ticks keys on timestamp, not date)
        def _vix(r):
            g = _g(r["d"])
            g["vix_high"], g["vix_low"] = r["hi"], r["lo"]
        _try(
            "SELECT substr(timestamp,1,10) d, MAX(vix_level) hi, MIN(vix_level) lo "
            "FROM market_ticks WHERE vix_level>0 GROUP BY substr(timestamp,1,10)",
            _vix,
        )
        return agg

    def get_last_row_as_dict(
        self, spreadsheet_name: str, tab_name: str
    ) -> Optional[Dict[str, Any]]:
        rows = self.read_tab_as_dicts(spreadsheet_name, tab_name)
        return rows[-1] if rows else None

    def _recent_entries(self, limit: int) -> List[Dict[str, Any]]:
        """Most-recent entries (raw) — position context for HERMES."""
        conn = self._conn()
        try:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT date, entry_number, entry_type, short_call_strike, "
                    "long_call_strike, short_put_strike, long_put_strike, call_credit, "
                    "put_credit, total_credit, realized_pnl "
                    "FROM trade_entries ORDER BY date DESC, entry_number DESC LIMIT ?",
                    (limit,),
                )
            ]
        finally:
            conn.close()

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
