"""Market data endpoints (OHLC, ticks, status)."""

import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from dashboard.backend.config import settings
from dashboard.backend.services.db_reader import BacktestingDBReader
from dashboard.backend.services.market_status import get_current_status, get_today_et

router = APIRouter(prefix="/api/market", tags=["market"])

db_reader = BacktestingDBReader(settings.backtesting_db)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(date_str: str | None) -> str | None:
    """Validate date_str format. Returns error message or None if valid."""
    if date_str is not None and not _DATE_RE.match(date_str):
        return "Invalid date format. Use YYYY-MM-DD."
    return None


@router.get("/ohlc")
async def get_ohlc(date_str: str | None = None):
    """1-minute OHLC bars for SPX chart."""
    if err := _validate_date(date_str):
        return JSONResponse(status_code=400, content={"error": err})
    target = date_str or get_today_et()
    ohlc = await db_reader.get_today_ohlc(target)
    return {"date": target, "count": len(ohlc), "bars": ohlc}


@router.get("/ticks")
async def get_ticks(date_str: str | None = None):
    """Market ticks (heartbeat snapshots) for P&L curve."""
    if err := _validate_date(date_str):
        return JSONResponse(status_code=400, content={"error": err})
    target = date_str or get_today_et()
    ticks = await db_reader.get_today_ticks(target)
    return {"date": target, "count": len(ticks), "ticks": ticks}


@router.get("/status")
async def get_status():
    """Current market session status."""
    return get_current_status()
