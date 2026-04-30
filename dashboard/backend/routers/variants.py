"""1v1 head-to-head variant comparison endpoints.

Variant A = the live HYDRA bot (current spread width, current config).
Variant B = a parallel HYDRA process running in dry mode with a different
            spread width, writing to data/variant_b/* and logs/hydra_variant_b/.

All endpoints return 503 when ``settings.comparison_mode_enabled`` is False so
the comparison UI can hide cleanly when the experiment isn't running. Variant
B's state file is allowed to be missing (variant might not be running yet) —
the dashboard surfaces that as ``available: false`` rather than a 500.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from dashboard.backend.config import settings
from dashboard.backend.services.state_reader import StateFileReader
from dashboard.backend.services.metrics_reader import MetricsFileReader
from dashboard.backend.services.db_reader import BacktestingDBReader
from dashboard.backend.services.market_status import get_today_et

logger = logging.getLogger("dashboard.variants")

router = APIRouter(prefix="/api/variants", tags=["variants"])

# Variant readers — one set per variant, reusing the same primitives as the
# main HYDRA endpoints. Variant A points at the live data files; variant B at
# the parallel data/variant_b/* tree.
_state_readers = {
    "a": StateFileReader(settings.hydra_state_file),
    "b": StateFileReader(settings.variant_b_state_file),
}
_metrics_readers = {
    "a": MetricsFileReader(settings.hydra_metrics_file),
    "b": MetricsFileReader(settings.variant_b_metrics_file),
}
_db_readers = {
    "a": BacktestingDBReader(settings.backtesting_db),
    "b": BacktestingDBReader(settings.variant_b_backtesting_db),
}


def _check_enabled() -> None:
    """Raise 503 if comparison mode is off — UI uses this as the on/off gate."""
    if not settings.comparison_mode_enabled:
        raise HTTPException(
            status_code=503,
            detail="Comparison mode disabled — set DASHBOARD_COMPARISON_MODE_ENABLED=true",
        )


def _file_age_seconds(path: Path) -> Optional[float]:
    """Seconds since the file was last modified, or None if it doesn't exist."""
    try:
        if not path.exists():
            return None
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def _read_variant_config(path: Path) -> dict:
    """Read a variant's config.json so the UI can show what's actually different."""
    try:
        with open(path) as f:
            cfg = json.load(f)
        s = cfg.get("strategy", {})
        return {
            "max_spread_width": s.get("max_spread_width"),
            "call_starting_otm_multiplier": s.get("call_starting_otm_multiplier"),
            "put_starting_otm_multiplier": s.get("put_starting_otm_multiplier"),
            "call_stop_buffer": s.get("call_stop_buffer"),
            "put_stop_buffer": s.get("put_stop_buffer"),
            "dry_run": cfg.get("dry_run"),
        }
    except Exception as e:
        logger.warning(f"Could not read variant config {path}: {e}")
        return {}


def _summary_from_state(state: dict) -> dict:
    """Mirror dashboard.routers.hydra._summary, but takes a state dict directly
    so we can build a summary for either variant without code duplication."""
    if not state:
        return {}
    entries = state.get("entries", [])
    total_credit = state.get("total_credit_received", 0) or 0
    realized = state.get("total_realized_pnl", 0) or 0
    commission = state.get("total_commission", 0) or 0
    call_stops = state.get("call_stops_triggered", 0) or 0
    put_stops = state.get("put_stops_triggered", 0) or 0
    contracts = (
        state.get("contracts_per_entry")
        or max((e.get("contracts", 1) for e in entries), default=1)
        or 1
    )
    return {
        "date": state.get("date"),
        "state": state.get("state"),
        "entries_completed": state.get("entries_completed", 0),
        "entries_failed": state.get("entries_failed", 0),
        "entries_skipped": state.get("entries_skipped", 0),
        "total_credit_received": total_credit,
        "total_realized_pnl": realized,
        "total_commission": commission,
        "net_pnl": realized - commission,
        "call_stops": call_stops,
        "put_stops": put_stops,
        "total_stops": call_stops + put_stops,
        "active_entries": len([e for e in entries if not e.get("is_complete", True)]),
        "total_entries": len(entries),
        "contracts_per_entry": contracts,
    }


def _compute_buffer_utilization(entry: dict) -> dict:
    """For a single entry, return per-side buffer utilization based on the
    most recent cost-to-close vs the trigger level. Only meaningful for live
    monitoring snapshots — entries that have already stopped/expired return
    None so the UI doesn't show misleading bars on closed positions.

    Cost-to-close for a side is: ``call_spread_value`` / ``put_spread_value``
    fields, written by the bot during heartbeat. ``call_side_stop`` /
    ``put_side_stop`` is the trigger threshold. Utilization = cost / stop.
    """
    out = {"call_pct": None, "put_pct": None, "call_value": None, "put_value": None}
    is_complete = entry.get("is_complete", False)
    if is_complete:
        return out

    call_active = (
        not entry.get("call_side_stopped")
        and not entry.get("call_side_expired")
        and not entry.get("call_side_skipped")
    )
    put_active = (
        not entry.get("put_side_stopped")
        and not entry.get("put_side_expired")
        and not entry.get("put_side_skipped")
    )

    csv = entry.get("call_spread_value")
    if csv is not None and call_active:
        css = entry.get("call_side_stop")
        out["call_value"] = csv
        if css and css > 0:
            out["call_pct"] = round(min(100.0, max(0.0, csv / css * 100)), 1)

    psv = entry.get("put_spread_value")
    if psv is not None and put_active:
        pss = entry.get("put_side_stop")
        out["put_value"] = psv
        if pss and pss > 0:
            out["put_pct"] = round(min(100.0, max(0.0, psv / pss * 100)), 1)

    return out


def _enrich_entries(entries: list[dict]) -> list[dict]:
    """Add buffer-utilization fields to each entry for the comparison panel."""
    out = []
    for e in entries:
        copy = dict(e)
        copy["buffer"] = _compute_buffer_utilization(e)
        out.append(copy)
    return out


def _peak_buffer_pct(entries: list[dict]) -> dict:
    """Largest call/put utilization across all today's entries, for the
    leaderboard panel ('peak stress per variant'). Returns 0.0 if no entries
    have measurable buffer data."""
    peak_call = 0.0
    peak_put = 0.0
    for e in entries:
        b = _compute_buffer_utilization(e)
        if b.get("call_pct") is not None and b["call_pct"] > peak_call:
            peak_call = b["call_pct"]
        if b.get("put_pct") is not None and b["put_pct"] > peak_put:
            peak_put = b["put_pct"]
    return {"call_pct": peak_call, "put_pct": peak_put}


def _variant_payload(variant_id: str) -> dict:
    """Full per-variant payload used by both /{id}/state and /comparison.

    Always returns a dict; if the variant isn't running yet, the dict has
    ``available: false`` and otherwise-empty fields. The frontend renders
    a placeholder card in that case rather than erroring out.
    """
    label = settings.variant_a_label if variant_id == "a" else settings.variant_b_label
    config_path = (
        settings.calypso_root / "bots/hydra/config/config.json"
        if variant_id == "a"
        else settings.variant_b_config_file
    )
    state_file = (
        settings.hydra_state_file if variant_id == "a" else settings.variant_b_state_file
    )

    state_age = _file_age_seconds(state_file)
    if state_age is None:
        return {
            "id": variant_id.upper(),
            "label": label,
            "available": False,
            "reason": f"State file missing ({state_file})",
            "config": _read_variant_config(config_path),
        }

    state = _state_readers[variant_id].read_latest() or {}
    entries = state.get("entries", [])

    return {
        "id": variant_id.upper(),
        "label": label,
        "available": True,
        "state_file_age_seconds": round(state_age, 1),
        "config": _read_variant_config(config_path),
        "summary": _summary_from_state(state),
        "entries": _enrich_entries(entries),
        "pnl_history": state.get("pnl_history", []),
        "peak_buffer": _peak_buffer_pct(entries),
        "spx_open": (state.get("market_data_ohlc") or {}).get("spx_open"),
        "vix_open": (state.get("market_data_ohlc") or {}).get("vix_open"),
        "spx_high": (state.get("market_data_ohlc") or {}).get("spx_high"),
        "spx_low": (state.get("market_data_ohlc") or {}).get("spx_low"),
    }


@router.get("/health")
async def get_health():
    """Always returns 200 (even when disabled) so the frontend can branch
    cleanly on whether comparison mode is available without an error path.

    The frontend hides the /comparison nav entry + page when ``enabled``
    is False — same gating as the backend endpoints, single source of truth.
    """
    return {
        "enabled": settings.comparison_mode_enabled,
        "variants": ["A", "B"],
        "variant_a_label": settings.variant_a_label,
        "variant_b_label": settings.variant_b_label,
    }


@router.get("/list")
async def list_variants():
    """Lightweight list of active variants — used by nav/breadcrumbs."""
    _check_enabled()
    return {
        "variants": [
            {
                "id": "A",
                "label": settings.variant_a_label,
                "available": _file_age_seconds(settings.hydra_state_file) is not None,
            },
            {
                "id": "B",
                "label": settings.variant_b_label,
                "available": _file_age_seconds(settings.variant_b_state_file) is not None,
            },
        ]
    }


@router.get("/{variant_id}/state")
async def get_variant_state(variant_id: str):
    """Full state of one variant (state file + enriched entries)."""
    _check_enabled()
    vid = variant_id.lower()
    if vid not in _state_readers:
        raise HTTPException(404, f"Unknown variant '{variant_id}' (expected A or B)")
    return _variant_payload(vid)


@router.get("/{variant_id}/summary")
async def get_variant_summary(variant_id: str):
    """Just the today-summary block for one variant (low-bandwidth poll)."""
    _check_enabled()
    vid = variant_id.lower()
    if vid not in _state_readers:
        raise HTTPException(404, f"Unknown variant '{variant_id}' (expected A or B)")
    state = _state_readers[vid].read_latest() or {}
    return {
        "id": variant_id.upper(),
        "summary": _summary_from_state(state),
        "peak_buffer": _peak_buffer_pct(state.get("entries", [])),
    }


@router.get("/comparison")
async def get_comparison():
    """The big one — both variants + a leaderboard delta computed server-side.

    Frontend polls this every ~2s. Returns enough data to render the entire
    Comparison page without further round-trips: leaderboard, strikes table,
    buffer bars, P&L line chart series.

    P&L line: each variant exposes its ``pnl_history`` list (heartbeat-written,
    one point per ~10s during market hours). Frontend can plot directly.

    The leaderboard's ``winner`` field uses NET P&L (realized - commission).
    Tie returns ``"tie"``. ``delta_net_pnl`` is signed: positive means A is
    ahead, negative means B is ahead.
    """
    _check_enabled()

    a = _variant_payload("a")
    b = _variant_payload("b")

    a_net = (a.get("summary") or {}).get("net_pnl", 0) if a.get("available") else 0
    b_net = (b.get("summary") or {}).get("net_pnl", 0) if b.get("available") else 0
    delta = a_net - b_net

    if not (a.get("available") and b.get("available")):
        winner = "n/a"
    elif abs(delta) < 0.01:
        winner = "tie"
    else:
        winner = "A" if delta > 0 else "B"

    return {
        "date": get_today_et(),
        "leaderboard": {
            "winner": winner,
            "a_net_pnl": a_net,
            "b_net_pnl": b_net,
            "delta_net_pnl": delta,  # signed: + = A leads, - = B leads
        },
        "variants": {"A": a, "B": b},
    }


@router.get("/{variant_id}/daily")
async def get_variant_daily(variant_id: str, days: int = 30):
    """Historical daily summaries for one variant (calendar/long-term view).

    Variant B's DB only has data from when comparison mode started, so the
    list will be short until the experiment has run for a few days.
    """
    _check_enabled()
    vid = variant_id.lower()
    if vid not in _db_readers:
        raise HTTPException(404, f"Unknown variant '{variant_id}'")
    summaries = await _db_readers[vid].get_daily_summaries(limit=days)
    return {"variant": variant_id.upper(), "days": len(summaries), "summaries": summaries}
