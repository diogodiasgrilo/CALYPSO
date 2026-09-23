"""Long-strangle (variant H) status reader + Telegram formatter. Playbook Step 8.

Pure (stdlib only) so it is unit-testable and importable without the broker
stack. Reads variant H's isolated ``long_strangle.db`` **read-only** and renders
it.

WHY IT READS THE DATABASE AND NOT A SIDECAR
--------------------------------------------
D and E keep an open-position sidecar because they are multi-day and the shared
state file cannot carry a two-expiry calendar. H is single-day and skips Step 6
entirely — but it still needs a live view, and the same constraint that forced
the isolated DB solves it: ``ls_snapshots`` is written every monitoring tick, so
the most recent row IS the live mark. No sidecar, no bot import, no second
source of truth to drift.

WHAT THIS VIEW SHOWS THAT NO OTHER STRATEGY'S DOES
---------------------------------------------------
**The peak versus the exit.** A long strangle can touch +50% on a gamma spike
and give it all back inside a minute, so "what it reached" and "what it captured"
are different numbers — and the gap between them is precisely what the source's
80%-win-rate claim turns on. Every other variant in this fleet sells premium,
where the peak is far less interesting than the close. Here it is the headline.

**The counterfactual.** ``ls_skipped`` carries the proposed strikes, the debit
and the expected move for every declined entry, so a skip can be scored later.
The GEX work on variant B had to be retro-fitted for this and could never
recover its first 95 vetoes.

Everything here is presentation. Nothing in this module decides anything, and
nothing it reads can affect trading.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional


def _connect(db_path: str) -> Optional[sqlite3.Connection]:
    """Read-only connection, or None. Never raises into a caller.

    ``mode=ro`` is belt-and-braces on top of the fact that this module contains
    no INSERT: a status view must be incapable of writing to a database the
    trading loop is using.
    """
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


def read_entries(db_path: str, date: str) -> List[Dict[str, Any]]:
    """Every long strangle opened on ``date``."""
    con = _connect(db_path)
    if not con:
        return []
    try:
        return _rows(con, "SELECT * FROM ls_entries WHERE date = ? ORDER BY entry_number", (date,))
    finally:
        con.close()


def read_exits(db_path: str, date: Optional[str] = None,
               limit: int = 20) -> List[Dict[str, Any]]:
    """Exits for ``date``, or the most recent ``limit`` across all days."""
    con = _connect(db_path)
    if not con:
        return []
    try:
        if date:
            return _rows(con, "SELECT * FROM ls_exits WHERE date = ? ORDER BY entry_number", (date,))
        return _rows(
            con,
            "SELECT * FROM ls_exits ORDER BY date DESC, entry_number DESC LIMIT ?",
            (limit,),
        )
    finally:
        con.close()


def read_skips(db_path: str, date: str) -> List[Dict[str, Any]]:
    """Declined entries for ``date``, with the context to score them later."""
    con = _connect(db_path)
    if not con:
        return []
    try:
        return _rows(
            con,
            "SELECT * FROM ls_skipped WHERE date = ? ORDER BY entry_number, skip_time",
            (date,),
        )
    finally:
        con.close()


def read_marks(db_path: str, date: str) -> Dict[int, Dict[str, Any]]:
    """Per-entry ``{last, peak_pct, trough_pct}`` from ``ls_snapshots``.

    The last snapshot is the live mark — it is written every monitoring tick, so
    a status view needs no bot import and no sidecar to show current P&L.

    ``peak_pct`` is the reason snapshots accumulate rather than upsert. For a
    long strangle the best mark of the day and the mark it exited at are
    routinely different numbers, and the difference is the measurement this
    variant exists to produce.
    """
    con = _connect(db_path)
    if not con:
        return {}
    try:
        agg = _rows(
            con,
            "SELECT entry_number, MAX(pnl_pct_of_debit) AS peak_pct, "
            "MIN(pnl_pct_of_debit) AS trough_pct, COUNT(*) AS ticks "
            "FROM ls_snapshots WHERE date = ? GROUP BY entry_number",
            (date,),
        )
        out: Dict[int, Dict[str, Any]] = {}
        for a in agg:
            n = a["entry_number"]
            last = _rows(
                con,
                "SELECT timestamp, spx, total_value, unrealized_pnl, pnl_pct_of_debit "
                "FROM ls_snapshots WHERE date = ? AND entry_number = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (date, n),
            )
            out[n] = {
                "last": last[0] if last else None,
                "peak_pct": a["peak_pct"],
                "trough_pct": a["trough_pct"],
                "ticks": a["ticks"],
            }
        return out
    finally:
        con.close()


def ls_status(db_path: str, date: str) -> Dict[str, Any]:
    """Variant H's day at a glance: open, closed, skipped, and the peak gap."""
    entries = read_entries(db_path, date)
    exits = {e["entry_number"]: e for e in read_exits(db_path, date)}
    marks = read_marks(db_path, date)
    skips = read_skips(db_path, date)

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
                # THE measurement. A large positive gap means the target was
                # reached and given back before the exit landed — the single
                # number that tests the source's win-rate claim.
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

    debit_deployed = sum(float(e.get("total_debit") or 0) for e in entries)
    realized = sum(float(r.get("realized_pnl") or 0) for r in closed_rows)
    return {
        "date": date,
        "open": open_rows,
        "closed": closed_rows,
        "skipped": skips,
        "summary": {
            "open_count": len(open_rows),
            "closed_count": len(closed_rows),
            "skipped_count": len(skips),
            "debit_deployed": round(debit_deployed, 2),
            "realized_pnl": round(realized, 2),
            # Max loss for the day is bounded by construction: it is the debit,
            # and nothing else. No other variant can state this.
            "max_possible_loss": round(debit_deployed, 2),
        },
    }


# ----------------------------------------------------------------------
# Telegram rendering
# ----------------------------------------------------------------------

def _money(v: Optional[float]) -> str:
    return f"${float(v):,.0f}" if v is not None else "—"


def _pct(v: Optional[float]) -> str:
    return f"{float(v):+.1f}%" if v is not None else "—"


def format_long_strangle_telegram(
    status: Dict[str, Any],
    title: str = "Strategy H — 0DTE Long Strangle",
) -> str:
    """Render ``ls_status()`` as a Telegram message.

    The dry-run disclaimer leads, per the playbook: this variant places no real
    orders, and a P&L figure that looks real must not be mistaken for one.
    """
    s = status.get("summary", {})
    lines = [
        f"🎯 *{title}* (dry-run — places NO real orders)",
        f"Open: {s.get('open_count', 0)} | Closed: {s.get('closed_count', 0)} "
        f"| Skipped: {s.get('skipped_count', 0)}",
        f"Debit deployed: {_money(s.get('debit_deployed'))}  "
        f"(= max possible loss, by construction)",
        f"Realized: {_money(s.get('realized_pnl'))}",
    ]

    for r in status.get("open", []):
        mark = r.get("last_mark") or {}
        pnl = mark.get("unrealized_pnl")
        lines.append(
            f"\n*E#{r.get('entry_number')} OPEN* {r.get('contracts')}c\n"
            f"  C {r.get('call_strike'):.0f} / P {r.get('put_strike'):.0f}  "
            f"(debit {_money(r.get('total_debit'))}, "
            f"{r.get('em_source') or '?'} EM {r.get('expected_move') or 0:.0f}pt)\n"
            f"  MTM {_money(pnl)} ({_pct(mark.get('pnl_pct_of_debit'))})  "
            f"peak {_pct(r.get('peak_pct'))}"
        )

    for r in status.get("closed", []):
        gap = r.get("peak_minus_exit_pct")
        gap_s = f"  gave back {gap:.0f}pp from peak" if gap and gap > 1 else ""
        lines.append(
            f"\n*E#{r.get('entry_number')} {(r.get('exit_reason') or '').upper()}* "
            f"{r.get('contracts')}c\n"
            f"  C {r.get('call_strike'):.0f} / P {r.get('put_strike'):.0f}  "
            f"debit {_money(r.get('total_debit'))}\n"
            f"  {_money(r.get('realized_pnl'))} ({_pct(r.get('pnl_pct_of_debit'))}) "
            f"in {r.get('minutes_held') or 0:.0f} min{gap_s}"
        )

    skips = status.get("skipped", [])
    if skips:
        lines.append("\n*Declined:*")
        for k in skips[:5]:
            lines.append(
                f"  E#{k.get('entry_number')} {k.get('skip_time') or ''} — "
                f"{k.get('skip_reason')}"
            )

    if not status.get("open") and not status.get("closed") and not skips:
        lines.append("\nNo activity today.")
    return "\n".join(lines)
