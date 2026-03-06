"""Market data endpoints (OHLC, ticks, status)."""

from fastapi import APIRouter

from dashboard.backend.config import settings
from dashboard.backend.services.db_reader import BacktestingDBReader
from dashboard.backend.services.market_status import get_current_status, get_today_et

router = APIRouter(prefix="/api/market", tags=["market"])

db_reader = BacktestingDBReader(settings.backtesting_db)


@router.get("/ohlc")
async def get_ohlc(date_str: str | None = None):
    """1-minute OHLC bars for SPX chart."""
    target = date_str or get_today_et()
    ohlc = await db_reader.get_today_ohlc(target)
    return {"date": target, "count": len(ohlc), "bars": ohlc}


@router.get("/ticks")
async def get_ticks(date_str: str | None = None):
    """Market ticks (heartbeat snapshots) for P&L curve."""
    target = date_str or get_today_et()
    ticks = await db_reader.get_today_ticks(target)
    return {"date": target, "count": len(ticks), "ticks": ticks}


@router.get("/status")
async def get_status():
    """Current market session status."""
    return get_current_status()
