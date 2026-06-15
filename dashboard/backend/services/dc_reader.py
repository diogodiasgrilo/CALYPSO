"""Strategy D (DC Time Machine) read-only status for the dashboard.

Pure stdlib (json + sqlite3) — the dashboard must NOT import bot/trading code
(CLAUDE.md), so this is a dashboard-owned reader of D's dry-run artifacts: the
open-calendar sidecar (dc_open_trades.json) + the dc_outcomes table in D's
isolated backtesting.db. Mirrors the other dashboard readers' read-only style.

D is a multi-day net-DEBIT double calendar and is intentionally NOT in the
0DTE iron-condor variant comparison (_VARIANT_IDS) — credit/Sharpe are
apples-to-oranges. This powers a dedicated, D-native dashboard view instead.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional


def _read_open_calendars(sidecar_path: Optional[str]) -> list:
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
        sc, lc, sp = legs.get("short_call", {}), legs.get("long_call", {}), legs.get("short_put", {})
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


def _read_recent_outcomes(db_path: Optional[str], limit: int = 20) -> list:
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
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


def read_dc_status(sidecar_path: Optional[str], db_path: Optional[str]) -> dict:
    """Open calendars + recent outcomes + a small summary, for /api/dc/status."""
    open_cals = _read_open_calendars(sidecar_path)
    outcomes = _read_recent_outcomes(db_path)
    return {
        "strategy": "double_calendar",
        "label": "Strategy D — DC Time Machine (dry-run)",
        "open_calendars": open_cals,
        "recent_outcomes": outcomes,
        "summary": {
            "open_count": len(open_cals),
            "transformed_count": sum(1 for c in open_cals if c.get("dc_phase") == "transformed"),
            "risk_free_count": sum(1 for c in open_cals if c.get("is_risk_free")),
            "realized_pnl_recent": round(sum(float(o.get("realized_pnl") or 0) for o in outcomes), 2),
        },
    }
