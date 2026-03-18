"""Cumulative metrics and historical data endpoints."""

import re

from fastapi import APIRouter, Query

from dashboard.backend.config import settings
from dashboard.backend.services.metrics_reader import MetricsFileReader
from dashboard.backend.services.db_reader import BacktestingDBReader
from dashboard.backend.services.live_state import LiveStateProvider
from dashboard.backend.services.market_status import get_today_et

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

metrics_reader = MetricsFileReader(settings.hydra_metrics_file)
db_reader = BacktestingDBReader(settings.backtesting_db)

# Set by main.py at startup
_live_state: LiveStateProvider | None = None


def set_live_state(provider: LiveStateProvider) -> None:
    """Wire up the live state provider for today's data fallback."""
    global _live_state
    _live_state = provider


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _has_today(summaries: list[dict]) -> bool:
    """Check if today's date is already in the summaries."""
    today = get_today_et()
    return any(s.get("date") == today for s in summaries)


def _append_today_summary(summaries: list[dict]) -> list[dict]:
    """Append today's live summary if not already in the list."""
    if _live_state and not _has_today(summaries):
        today_summary = _live_state.get_today_summary()
        if today_summary:
            summaries = list(summaries) + [today_summary]
    return summaries


@router.get("/cumulative")
async def get_cumulative():
    """Lifetime cumulative metrics."""
    data = metrics_reader.read_latest()
    if data is None:
        return {"error": "Metrics file not available"}
    return data


@router.get("/daily")
async def get_daily(
    days: int = Query(default=0, ge=0, le=9999),
    year: int = Query(default=0, ge=0, le=2099),
):
    """Daily summaries for calendar heat map."""
    if year >= 2020:
        summaries = await db_reader.get_daily_summaries_by_year(year)
    elif days > 0:
        summaries = await db_reader.get_daily_summaries(limit=days)
    else:
        summaries = await db_reader.get_daily_summaries(limit=365)

    summaries = _append_today_summary(summaries)
    return {"days": len(summaries), "summaries": summaries}


@router.get("/entries")
async def get_all_entries():
    """All historical entries for analytics."""
    entries = await db_reader.get_all_entries()

    # Append today's entries from state file if not in DB yet
    if _live_state:
        today = get_today_et()
        has_today = any(e.get("date") == today for e in entries)
        if not has_today:
            live_entries = _live_state.get_today_entries()
            if live_entries:
                entries = list(entries) + live_entries

    return {"count": len(entries), "entries": entries}


@router.get("/stops")
async def get_all_stops():
    """All historical stops for analytics."""
    stops = await db_reader.get_all_stops()

    # Append today's stops from state file if not in DB yet
    if _live_state:
        today = get_today_et()
        has_today = any(s.get("date") == today for s in stops)
        if not has_today:
            live_stops = _live_state.get_today_stops()
            if live_stops:
                stops = list(stops) + live_stops

    return {"count": len(stops), "stops": stops}


@router.get("/comparisons")
async def get_comparisons():
    """Comparison statistics (averages across all trading days)."""
    data = await db_reader.get_comparison_stats()

    # If we have DB data, augment with today's values for more accurate stats
    if data and _live_state:
        today_summary = _live_state.get_today_summary()
        today_entries = _live_state.get_today_entries()
        if today_summary:
            n = data.get("total_days", 0)
            today_pnl = today_summary.get("net_pnl", 0)
            today_entries_count = today_summary.get("entries_placed", 0)
            today_stops = today_summary.get("entries_stopped", 0)
            today_credit = sum(e.get("total_credit", 0) for e in today_entries)

            # Update running averages: new_avg = (old_avg * n + today) / (n + 1)
            if n > 0:
                data = dict(data)  # Make mutable copy
                data["avg_pnl"] = ((data.get("avg_pnl") or 0) * n + today_pnl) / (n + 1)
                data["avg_entries"] = ((data.get("avg_entries") or 0) * n + today_entries_count) / (n + 1)
                data["avg_stops"] = ((data.get("avg_stops") or 0) * n + today_stops) / (n + 1)
                if data.get("avg_credit") is not None:
                    data["avg_credit"] = (data["avg_credit"] * n + today_credit) / (n + 1)
                data["best_day"] = max(data.get("best_day") or 0, today_pnl)
                data["worst_day"] = min(data.get("worst_day") or 0, today_pnl)
                data["total_days"] = n + 1

    if data is None:
        return {"error": "No data available"}
    return data


@router.get("/performance")
async def get_performance():
    """Daily P&L values for client-side performance metric calculations."""
    pnls = await db_reader.get_daily_pnls()

    # Append today's net P&L if not in DB yet
    if _live_state:
        today_pnl = _live_state.get_today_net_pnl()
        if today_pnl is not None:
            # Check if today is already in DB by comparing count
            # (DB returns ordered by date, today would be last)
            summaries = await db_reader.get_daily_summaries(limit=1)
            today = get_today_et()
            if not summaries or summaries[0].get("date") != today:
                pnls = list(pnls) + [today_pnl]

    return {"count": len(pnls), "daily_pnls": pnls}


@router.get("/range")
async def get_date_range():
    """Available date range in database."""
    info = await db_reader.get_date_range()
    return info or {"first_date": None, "last_date": None, "total_days": 0}
