"""HYDRA state and entry endpoints."""

import json
import logging
import re

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger("dashboard.hydra")

from dashboard.backend.config import settings
from dashboard.backend.services.state_reader import StateFileReader
from dashboard.backend.services.variant_readers import reader_for
from dashboard.backend.services.market_status import get_today_et

router = APIRouter(prefix="/api/hydra", tags=["hydra"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

state_reader = StateFileReader(settings.hydra_state_file)


@router.get("/bot-config")
async def get_bot_config():
    """Read E6/E7 enabled flags + canonical entry schedule from bot config file.

    `entry_times` / `conditional_entry_times` are the canonical (pre-VIX-cap)
    slot schedule — the dashboard uses these to label entries with stable
    canonical numbers (E1=first base slot, E2=second, …) even when the VIX
    regime cap drops entries at runtime. As of 2026-04-17, E#1 (10:15) is
    dropped at ALL VIX levels per config max_entries: [2, 2, 2, 1].
    """
    # Read the PRIMARY variant's config (settings.bot_config_file → currently
    # variant C, the live canonical strategy). The dry_run flag + schedule here
    # drive the main dashboard's banner/labels, so they must match whichever
    # variant the main page is showing.
    config_path = settings.bot_config_file
    try:
        with open(config_path) as f:
            config = json.load(f)
        strategy = config.get("strategy", {})
        # Phase 2 X-1: expose contracts_per_entry so the frontend can render a
        # [Nc] badge on P&L panels, entry cards, and history rows. Null-safe
        # fallback mirrors the Phase 1 pattern — None/0/missing → 1.
        contracts = strategy.get("contracts_per_entry") or 1
        # 2026-04-27: expose dry_run flag so the frontend can render a prominent
        # DRY-RUN banner — eliminates ambiguity when the primary bot is in dry
        # mode (realistic dry-run uses real IBKR-paper prices, so positions look
        # identical to live except for the DRY_* prefix on position IDs).
        dry_run = bool(config.get("dry_run", False))
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
            "contracts_per_entry": contracts,
            "dry_run": dry_run,
            "primary_label": settings.primary_label,
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
            "contracts_per_entry": 1,
            "dry_run": False,
            "primary_label": settings.primary_label,
        }


@router.get("/state")
async def get_state():
    """Current HYDRA state from last file read."""
    data = state_reader.read_latest()
    if data is None:
        return {"error": "State file not available"}
    return data


@router.get("/entries")
async def get_entries(date_str: str | None = None, strategy_id: str = Query(default="")):
    """Entries + stops for a date, scoped to the picked strategy variant.

    ``strategy_id`` selects the variant's DB via the shared ``reader_for`` (same
    resolver as ``/api/metrics/daily``) so the History day-detail ENTRIES /
    STOP-LOSSES tables read the SAME variant as the header count cards. Empty /
    primary id → the canonical (live) DB. The live state-file fast path (no
    ``date_str``) is primary-only by construction — the day-detail modal always
    passes an explicit ``date_str``, so it goes through the variant-aware DB path.
    """
    if date_str is not None and not _DATE_RE.match(date_str):
        return JSONResponse(status_code=400, content={"error": "Invalid date format. Use YYYY-MM-DD."})
    if date_str is None:
        # Try state file first for live data (primary variant only)
        state = state_reader.get_cached() or state_reader.read_latest()
        if state and "entries" in state:
            return {"source": "state_file", "entries": state["entries"]}

    # Fall back to SQLite for historical — read the PICKED variant's DB.
    reader, _ = reader_for(strategy_id)
    target = date_str or get_today_et()
    entries = await reader.get_entries_for_date(target)
    stops = await reader.get_stops_for_date(target)
    return {"source": "database", "date": target, "entries": entries, "stops": stops}


@router.get("/summary")
async def get_summary():
    """Today's summary: P&L, entries count, stops, credits.

    Delegates to the canonical `_summary_from_state` (variants router) so the
    main summary card matches the per-strategy snapshot / leaderboard exactly:
    net_pnl is LIVE (realized + unrealized − commission) and active_entries is
    gated on the per-side live flags — NOT `is_complete`, which goes True at
    placement and undercounted monitoring entries (dashboard audit 2026-07-22).
    """
    from dashboard.backend.routers.variants import _summary_from_state

    state = state_reader.get_cached() or state_reader.read_latest()
    if not state:
        return {"error": "State not available"}

    summary = _summary_from_state(state)
    # Preserve the two counters `_summary_from_state` doesn't carry.
    summary["one_sided_entries"] = state.get("one_sided_entries", 0)
    summary["credit_gate_skips"] = state.get("credit_gate_skips", 0)
    return summary
