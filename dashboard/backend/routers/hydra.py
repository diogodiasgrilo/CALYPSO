"""HYDRA state and entry endpoints."""

import json
import logging
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger("dashboard.hydra")

from dashboard.backend.config import settings
from dashboard.backend.services.state_reader import StateFileReader
from dashboard.backend.services.db_reader import BacktestingDBReader
from dashboard.backend.services.market_status import get_today_et

router = APIRouter(prefix="/api/hydra", tags=["hydra"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

state_reader = StateFileReader(settings.hydra_state_file)
db_reader = BacktestingDBReader(settings.backtesting_db)


@router.get("/bot-config")
async def get_bot_config():
    """Read E6/E7 enabled flags + canonical entry schedule from bot config file.

    `entry_times` / `conditional_entry_times` are the canonical (pre-VIX-cap)
    slot schedule — the dashboard uses these to label entries with stable
    canonical numbers (E1=first base slot, E2=second, …) even when the VIX
    regime cap drops entries at runtime. As of 2026-04-17, E#1 (10:15) is
    dropped at ALL VIX levels per config max_entries: [2, 2, 2, 1].
    """
    config_path = settings.calypso_root / "bots/hydra/config/config.json"
    try:
        with open(config_path) as f:
            config = json.load(f)
        strategy = config.get("strategy", {})
        return {
            "conditional_e6_enabled": strategy.get("conditional_e6_enabled", False),
            "conditional_e7_enabled": strategy.get("conditional_e7_enabled", False),
            "conditional_downday_e6_enabled": strategy.get("conditional_downday_e6_enabled", False),
            "conditional_downday_e7_enabled": strategy.get("conditional_downday_e7_enabled", False),
            "conditional_downday_threshold_pct": strategy.get(
                "conditional_downday_threshold_pct",
                strategy.get("downday_threshold_pct", 0.003),
            ),
            "conditional_upday_e6_enabled": strategy.get("conditional_upday_e6_enabled", False),
            "conditional_upday_e7_enabled": strategy.get("conditional_upday_e7_enabled", False),
            "downday_threshold_pct": strategy.get("downday_threshold_pct", 0.003),
            "upday_threshold_pct": strategy.get("upday_threshold_pct", 0.0025),
            "entry_times": strategy.get("entry_times", []),
            "conditional_entry_times": strategy.get("conditional_entry_times", []),
        }
    except Exception as e:
        logger.warning(f"Could not read bot config ({config_path}): {e}")
        return {
            "conditional_e6_enabled": False,
            "conditional_e7_enabled": False,
            "conditional_downday_e6_enabled": False,
            "conditional_downday_e7_enabled": False,
            "conditional_downday_threshold_pct": 0.0025,
            "conditional_upday_e6_enabled": False,
            "conditional_upday_e7_enabled": False,
            "downday_threshold_pct": 0.003,
            "upday_threshold_pct": 0.0025,
            "entry_times": [],
            "conditional_entry_times": [],
        }


@router.get("/state")
async def get_state():
    """Current HYDRA state from last file read."""
    data = state_reader.read_latest()
    if data is None:
        return {"error": "State file not available"}
    return data


@router.get("/entries")
async def get_entries(date_str: str | None = None):
    """Today's entries (or specific date) with full details."""
    if date_str is not None and not _DATE_RE.match(date_str):
        return JSONResponse(status_code=400, content={"error": "Invalid date format. Use YYYY-MM-DD."})
    if date_str is None:
        # Try state file first for live data
        state = state_reader.get_cached() or state_reader.read_latest()
        if state and "entries" in state:
            return {"source": "state_file", "entries": state["entries"]}

    # Fall back to SQLite for historical
    target = date_str or get_today_et()
    entries = await db_reader.get_entries_for_date(target)
    stops = await db_reader.get_stops_for_date(target)
    return {"source": "database", "date": target, "entries": entries, "stops": stops}


@router.get("/summary")
async def get_summary():
    """Today's summary: P&L, entries count, stops, credits."""
    state = state_reader.get_cached() or state_reader.read_latest()
    if not state:
        return {"error": "State not available"}

    entries = state.get("entries", [])
    return {
        "date": state.get("date"),
        "state": state.get("state"),
        "entries_completed": state.get("entries_completed", 0),
        "entries_failed": state.get("entries_failed", 0),
        "entries_skipped": state.get("entries_skipped", 0),
        "total_credit_received": state.get("total_credit_received", 0),
        "total_realized_pnl": state.get("total_realized_pnl", 0),
        "total_commission": state.get("total_commission", 0),
        "net_pnl": state.get("total_realized_pnl", 0) - state.get("total_commission", 0),
        "call_stops": state.get("call_stops_triggered", 0),
        "put_stops": state.get("put_stops_triggered", 0),
        "total_stops": state.get("call_stops_triggered", 0) + state.get("put_stops_triggered", 0),
        "one_sided_entries": state.get("one_sided_entries", 0),
        "credit_gate_skips": state.get("credit_gate_skips", 0),
        "active_entries": len([e for e in entries if not e.get("is_complete", True)]),
        "total_entries": len(entries),
    }
