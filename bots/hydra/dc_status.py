"""Strategy D status reader + Telegram formatter (Phase 7).

Pure (stdlib only) so it's unit-testable and importable without the broker stack.
Reads D's authoritative dry-run artifacts — the open-calendar SIDECAR
(dc_open_trades.json) + the dc_outcomes table — and renders them. Used by the
Telegram /calendars command (variant A's poller renders D's files cross-variant).

D is a multi-day net-DEBIT double calendar, so it is deliberately NOT folded into
the 0DTE iron-condor /compare head-to-head (credit/Sharpe are apples-to-oranges);
this is its own D-native view.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional


def read_open_calendars(sidecar_path: str) -> list:
    """Distill the open-calendar sidecar into display rows."""
    if not sidecar_path or not os.path.exists(sidecar_path):
        return []
    try:
        with open(sidecar_path) as f:
            records = json.load(f)
    except (OSError, ValueError):
        return []
    rows = []
    for r in records or []:
        legs = r.get("legs", {})
        sc, lc = legs.get("short_call", {}), legs.get("long_call", {})
        sp = legs.get("short_put", {})
        rows.append({
            "entry_number": r.get("entry_number"),
            "strategy_id": r.get("strategy_id"),
            "dc_phase": r.get("dc_phase"),
            "is_risk_free": bool(r.get("is_risk_free")),
            "contracts": r.get("contracts"),
            "net_debit": r.get("net_debit"),
            "transform_credit": r.get("transform_credit"),
            "call_strike": sc.get("strike"),
            "put_strike": sp.get("strike"),
            "short_expiry": sc.get("expiry"),
            "long_expiry": lc.get("expiry"),
        })
    return rows


def read_recent_outcomes(db_path: str, limit: int = 10) -> list:
    """Most-recent dc_outcomes rows (terminal P&L), newest first."""
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        con = sqlite3.connect(db_path, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute(
                "SELECT entry_date, close_date, entry_number, terminal_state, realized_pnl "
                "FROM dc_outcomes ORDER BY close_date DESC, entry_number DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            con.close()
    except sqlite3.Error:
        return []


def dc_status(sidecar_path: str, db_path: str) -> dict:
    """Combined D status: open calendars + recent outcomes + a summary."""
    open_cals = read_open_calendars(sidecar_path)
    outcomes = read_recent_outcomes(db_path)
    return {
        "open_calendars": open_cals,
        "recent_outcomes": outcomes,
        "summary": {
            "open_count": len(open_cals),
            "transformed_count": sum(1 for c in open_cals if c.get("dc_phase") == "transformed"),
            "risk_free_count": sum(1 for c in open_cals if c.get("is_risk_free")),
            "realized_pnl_recent": round(sum(float(o.get("realized_pnl") or 0) for o in outcomes), 2),
        },
    }


def _fmt_money(v: Optional[float]) -> str:
    return f"${float(v):,.0f}" if v is not None else "—"


def format_calendars_telegram(status: dict) -> str:
    """Render dc_status() as a Telegram message."""
    s = status.get("summary", {})
    cals = status.get("open_calendars", [])
    lines = ["📅 *Strategy D — DC Time Machine* (dry-run)"]
    lines.append(
        f"Open: {s.get('open_count', 0)} | Transformed: {s.get('transformed_count', 0)} "
        f"| Risk-free: {s.get('risk_free_count', 0)}"
    )
    if not cals:
        lines.append("\nNo open calendars.")
    for c in cals:
        phase = (c.get("dc_phase") or "?").upper()
        rf = " ✅RISK-FREE" if c.get("is_risk_free") else ""
        if c.get("dc_phase") == "transformed":
            basis = f"debit {_fmt_money(c.get('net_debit'))} → credit {_fmt_money(c.get('transform_credit'))}"
        else:
            basis = f"debit {_fmt_money(c.get('net_debit'))}"
        lines.append(
            f"\n*E#{c.get('entry_number')}* [{phase}{rf}] {c.get('contracts')}c\n"
            f"  C {c.get('call_strike')} / P {c.get('put_strike')}  ({basis})\n"
            f"  short {c.get('short_expiry')} / long {c.get('long_expiry')}"
        )
    outs = status.get("recent_outcomes", [])
    if outs:
        lines.append("\n*Recent outcomes:*")
        for o in outs[:5]:
            lines.append(
                f"  {o.get('close_date')} E#{o.get('entry_number')} "
                f"{o.get('terminal_state')}: {_fmt_money(o.get('realized_pnl'))}"
            )
    return "\n".join(lines)
