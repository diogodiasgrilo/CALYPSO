"""Cumulative metrics and historical data endpoints."""

from fastapi import APIRouter

from dashboard.backend.config import settings
from dashboard.backend.services.metrics_reader import MetricsFileReader
from dashboard.backend.services.db_reader import BacktestingDBReader

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

metrics_reader = MetricsFileReader(settings.hydra_metrics_file)
db_reader = BacktestingDBReader(settings.backtesting_db)


@router.get("/cumulative")
async def get_cumulative():
    """Lifetime cumulative metrics."""
    data = metrics_reader.read_latest()
    if data is None:
        return {"error": "Metrics file not available"}
    return data


@router.get("/daily")
async def get_daily(days: int = 0, year: int = 0):
    """Daily summaries for calendar heat map."""
    if year > 0:
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


@router.get("/range")
async def get_date_range():
    """Available date range in database."""
    info = await db_reader.get_date_range()
    return info or {"first_date": None, "last_date": None, "total_days": 0}
