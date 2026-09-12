"""Strategy D (DC Time Machine) read-only status for the dashboard.

Pure stdlib (json + sqlite3) — the dashboard must NOT import bot/trading code
(CLAUDE.md), so this is a dashboard-owned reader of D's dry-run artifacts: the
open-calendar sidecar (dc_open_trades.json) + the dc_outcomes table in D's
isolated dc_calendar.db. Mirrors the other dashboard readers' read-only style.

D is a multi-day net-DEBIT double calendar and is intentionally NOT in the
0DTE iron-condor variant comparison (_VARIANT_IDS) — credit/Sharpe are
apples-to-oranges. This powers a dedicated, D-native dashboard view instead.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional


def _cal_entry_iso(r: dict) -> str:
    """The calendar's entry (birth) date as YYYY-MM-DD, from ``entry_time``, else
    the date embedded in ``strategy_id`` (e.g. ``spydc_20260622_001`` -> 2026-06-22).
    Empty string when neither is parseable."""
    et = str(r.get("entry_time") or "")[:10]
    if len(et) == 10 and et[4] == "-":
        return et
    parts = str(r.get("strategy_id") or "").split("_")
    if len(parts) >= 2 and len(parts[1]) == 8 and parts[1].isdigit():
        d = parts[1]
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return ""


def _read_open_calendars(sidecar_path: Optional[str], baseline_date: str = "") -> list:
    if not sidecar_path or not os.path.exists(sidecar_path):
        return []
    try:
        with open(sidecar_path) as f:
            records = json.load(f)
    except (OSError, ValueError):
        return []
    baseline = (baseline_date or "").strip()
    rows = []
    for r in records or []:
        # Hide calendars ENTERED before the strategy was 100%-correct (baseline),
        # so a pre-fix open position (e.g. E's pre-cfc6027 06-22 calendar) is not
        # shown as trustworthy live state. Unparseable entry date -> keep (fail open).
        if baseline:
            ed = _cal_entry_iso(r)
            if ed and ed < baseline:
                continue
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
            # Live mark-to-market (refreshed each tick by the bot; absent on
            # sidecars written before the 2026-06-17 live-mark fix -> None).
            "unrealized_pnl": r.get("unrealized_pnl"),
            "pnl_pct": r.get("pnl_pct"),
            "call_strike": sc.get("strike"),
            "put_strike": sp.get("strike"),
            "short_expiry": sc.get("expiry"),
            "long_expiry": lc.get("expiry"),
        })
    return rows


def _read_recent_outcomes(db_path: Optional[str], limit: int = 20,
                          baseline_date: str = "") -> list:
    if not db_path or not os.path.exists(db_path):
        return []
    baseline = (baseline_date or "").strip()
    where, params = ("", ())
    if baseline:
        where, params = ("WHERE entry_date >= ?", (baseline,))
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute(
                "SELECT entry_date, close_date, entry_number, terminal_state, realized_pnl "
                f"FROM dc_outcomes {where} ORDER BY close_date DESC, entry_number DESC LIMIT ?",
                (*params, limit),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            con.close()
    except sqlite3.Error:
        return []


def read_dc_status(sidecar_path: Optional[str], db_path: Optional[str],
                   baseline_date: str = "") -> dict:
    """Open calendars + recent outcomes + a small summary, for /api/dc/status.

    ``baseline_date`` (ISO) hides calendars entered before it (both open positions
    and closed outcomes) — the pre-100%-correct history stays off the dashboard."""
    open_cals = _read_open_calendars(sidecar_path, baseline_date)
    outcomes = _read_recent_outcomes(db_path, baseline_date=baseline_date)
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
