"""Strategy H (0DTE Long Strangle) read-only status for the dashboard.

Pure stdlib (sqlite3) — the dashboard must NOT import bot/trading code
(CLAUDE.md), so this is a dashboard-owned reader of H's isolated
``long_strangle.db``. It deliberately duplicates the small amount of SQL in
``bots/hydra/ls_status.py`` rather than importing it, which is the same boundary
``dc_reader.py`` keeps for D and E.

H IS THE ONLY LONG-GAMMA STRATEGY IN THE FLEET, and the view has to say so
differently from every other page:

* **P&L is a percentage of the DEBIT, not of a credit.** Nothing is sold. The
  existing iron-condor renderers assume premium was collected — "expired
  worthless" means profit there and maximum loss here — so H gets its own view
  rather than being folded into one of theirs.
* **The maximum loss is a fact, not an estimate.** It is the debit paid, known
  before the position opens. No other strategy on this dashboard can state its
  worst case up front.
* **The peak matters as much as the exit.** A long strangle can touch +50% on a
  gamma spike and give it all back within a minute, so the page shows what each
  position *reached* alongside what it *captured*. That gap is the measurement
  this variant exists to produce.

Read-only throughout: ``mode=ro`` connections, no INSERT/UPDATE anywhere, and
every failure degrades to an empty view rather than an error page.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional


def _connect(db_path: Optional[str]) -> Optional[sqlite3.Connection]:
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def _latest_date(con: sqlite3.Connection) -> str:
    """The most recent date with any activity, so the page has something to show
    even when H did not trade today — which, for a dry-run-locked variant that
    is not installed on the VM, is the normal case."""
    rows = _rows(con, "SELECT MAX(date) AS d FROM ls_entries")
    return (rows[0].get("d") if rows else None) or ""


def read_ls_status(db_path: Optional[str], date: str = "") -> Dict[str, Any]:
    """Open + closed + declined for one day, plus the peak-versus-exit gap.

    ``date`` empty means "the most recent day with activity".
    """
    empty = {
        "strategy": "long_strangle",
        "label": "Strategy H — 0DTE Long Strangle (dry-run · places no real orders)",
        "available": False,
        "date": date,
        "open": [],
        "closed": [],
        "skipped": [],
        "summary": {
            "open_count": 0, "closed_count": 0, "skipped_count": 0,
            "debit_deployed": 0.0, "realized_pnl": 0.0, "max_possible_loss": 0.0,
        },
    }
    con = _connect(db_path)
    if not con:
        empty["reason"] = "No long_strangle.db yet — variant H has never recorded a row."
        return empty
    try:
        day = date or _latest_date(con)
        if not day:
            empty["reason"] = "long_strangle.db exists but holds no entries yet."
            return empty

        entries = _rows(
            con, "SELECT * FROM ls_entries WHERE date = ? ORDER BY entry_number", (day,))
        exits = {r["entry_number"]: r for r in _rows(
            con, "SELECT * FROM ls_exits WHERE date = ?", (day,))}
        skipped = _rows(
            con,
            "SELECT * FROM ls_skipped WHERE date = ? ORDER BY entry_number, skip_time",
            (day,),
        )
        marks: Dict[int, Dict[str, Any]] = {}
        for a in _rows(
            con,
            "SELECT entry_number, MAX(pnl_pct_of_debit) AS peak_pct, "
            "MIN(pnl_pct_of_debit) AS trough_pct, COUNT(*) AS ticks "
            "FROM ls_snapshots WHERE date = ? GROUP BY entry_number",
            (day,),
        ):
            n = a["entry_number"]
            last = _rows(
                con,
                "SELECT timestamp, spx, total_value, unrealized_pnl, pnl_pct_of_debit "
                "FROM ls_snapshots WHERE date = ? AND entry_number = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (day, n),
            )
            marks[n] = {
                "last": last[0] if last else None,
                "peak_pct": a["peak_pct"],
                "trough_pct": a["trough_pct"],
                "ticks": a["ticks"],
            }

        open_rows: List[Dict[str, Any]] = []
        closed_rows: List[Dict[str, Any]] = []
        for e in entries:
            n = e["entry_number"]
            m = marks.get(n, {})
            row = {
                "entry_number": n,
                "entry_time": e.get("entry_time"),
                "call_strike": e.get("call_strike"),
                "put_strike": e.get("put_strike"),
                "contracts": e.get("contracts"),
                "total_debit": e.get("total_debit"),
                "call_debit": e.get("call_debit"),
                "put_debit": e.get("put_debit"),
                "spx_at_entry": e.get("spx_at_entry"),
                "vix_at_entry": e.get("vix_at_entry"),
                "em_source": e.get("em_source"),
                "expected_move": e.get("expected_move"),
                "skew_gap_pct": e.get("skew_gap_pct"),
                "peak_pct": m.get("peak_pct"),
                "trough_pct": m.get("trough_pct"),
            }
            x = exits.get(n)
            if x:
                row.update({
                    "exit_time": x.get("exit_time"),
                    "exit_reason": x.get("exit_reason"),
                    "realized_pnl": x.get("realized_pnl"),
                    "pnl_pct_of_debit": x.get("pnl_pct_of_debit"),
                    "minutes_held": x.get("minutes_held"),
                    "spx_at_exit": x.get("spx_at_exit"),
                    # Reached but not captured. Invisible in realized P&L alone,
                    # and the number the source's win-rate claim turns on.
                    "peak_minus_exit_pct": (
                        round(m["peak_pct"] - x["pnl_pct_of_debit"], 2)
                        if m.get("peak_pct") is not None
                        and x.get("pnl_pct_of_debit") is not None else None
                    ),
                })
                closed_rows.append(row)
            else:
                row["last_mark"] = m.get("last")
                open_rows.append(row)

        debit = round(sum(float(e.get("total_debit") or 0) for e in entries), 2)
        return {
            "strategy": "long_strangle",
            "label": "Strategy H — 0DTE Long Strangle (dry-run · places no real orders)",
            "available": True,
            "date": day,
            "open": open_rows,
            "closed": closed_rows,
            "skipped": skipped,
            "summary": {
                "open_count": len(open_rows),
                "closed_count": len(closed_rows),
                "skipped_count": len(skipped),
                "debit_deployed": debit,
                "realized_pnl": round(
                    sum(float(r.get("realized_pnl") or 0) for r in closed_rows), 2),
                # Not an estimate. The debit IS the worst case, by construction.
                "max_possible_loss": debit,
            },
        }
    finally:
        con.close()


def read_ls_recent(db_path: Optional[str], limit: int = 30) -> List[Dict[str, Any]]:
    """Recent closed positions across days, newest first — the history strip.

    Joins each exit to its entry so a row carries the debit it risked, not just
    the dollars it made: for this strategy a +$250 day means nothing without
    knowing whether $200 or $2,000 was at stake.
    """
    con = _connect(db_path)
    if not con:
        return []
    try:
        return _rows(
            con,
            "SELECT x.date, x.entry_number, x.exit_time, x.exit_reason, "
            "       x.realized_pnl, x.pnl_pct_of_debit, x.minutes_held, "
            "       e.total_debit, e.contracts, e.call_strike, e.put_strike, "
            "       e.em_source "
            "FROM ls_exits x LEFT JOIN ls_entries e "
            "  ON e.date = x.date AND e.entry_number = x.entry_number "
            "ORDER BY x.date DESC, x.entry_number DESC LIMIT ?",
            (limit,),
        )
    finally:
        con.close()
