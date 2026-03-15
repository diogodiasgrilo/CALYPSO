"""Cumulative metrics and historical data endpoints."""

import re

from fastapi import APIRouter, Query

from dashboard.backend.config import settings
from dashboard.backend.services.metrics_reader import MetricsFileReader
from dashboard.backend.services.db_reader import BacktestingDBReader

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

metrics_reader = MetricsFileReader(settings.hydra_metrics_file)
db_reader = BacktestingDBReader(settings.backtesting_db)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    return {"days": len(summaries), "summaries": summaries}


@router.get("/entries")
async def get_all_entries():
    """All historical entries for analytics."""
    entries = await db_reader.get_all_entries()
    return {"count": len(entries), "entries": entries}


@router.get("/stops")
async def get_all_stops():
    """All historical stops for analytics."""
    stops = await db_reader.get_all_stops()
    return {"count": len(stops), "stops": stops}


@router.get("/comparisons")
async def get_comparisons():
    """Comparison statistics (averages across all trading days)."""
    data = await db_reader.get_comparison_stats()
    if data is None:
        return {"error": "No data available"}
    return data


@router.get("/performance")
async def get_performance():
    """Daily P&L values for client-side performance metric calculations."""
    pnls = await db_reader.get_daily_pnls()
    return {"count": len(pnls), "daily_pnls": pnls}


@router.get("/range")
async def get_date_range():
    """Available date range in database."""
    info = await db_reader.get_date_range()
    return info or {"first_date": None, "last_date": None, "total_days": 0}
