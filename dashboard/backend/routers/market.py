"""Market data endpoints (OHLC, ticks, status)."""

import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from dashboard.backend.config import settings
from dashboard.backend.services.db_reader import BacktestingDBReader
from dashboard.backend.services.live_ohlc import LiveOHLCBuilder
from dashboard.backend.services.live_state import LiveStateProvider
from dashboard.backend.services.market_status import get_current_status, get_today_et

router = APIRouter(prefix="/api/market", tags=["market"])

db_reader = BacktestingDBReader(settings.backtesting_db)

# Set by main.py at startup to share the broadcaster's live data sources
_live_ohlc: LiveOHLCBuilder | None = None
_live_state: LiveStateProvider | None = None


def set_live_sources(ohlc: LiveOHLCBuilder, state: LiveStateProvider) -> None:
    """Wire up the broadcaster's live data sources."""
    global _live_ohlc, _live_state
    _live_ohlc = ohlc
    _live_state = state


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(date_str: str | None) -> str | None:
    """Validate date_str format. Returns error message or None if valid."""
    if date_str is not None and not _DATE_RE.match(date_str):
        return "Invalid date format. Use YYYY-MM-DD."
    return None


def _is_today(target: str) -> bool:
    return target == get_today_et()


@router.get("/ohlc")
async def get_ohlc(date_str: str | None = None):
    """1-minute OHLC bars for SPX chart."""
    if err := _validate_date(date_str):
        return JSONResponse(status_code=400, content={"error": err})
    target = date_str or get_today_et()
    ohlc = await db_reader.get_today_ohlc(target)

    # Fall back to live OHLC bars for today if SQLite has no data yet
    if not ohlc and _live_ohlc and _is_today(target):
        ohlc = _live_ohlc.get_ohlc_bars()

    return {"date": target, "count": len(ohlc), "bars": ohlc}


@router.get("/ticks")
async def get_ticks(date_str: str | None = None):
    """Market ticks (heartbeat snapshots) for P&L curve."""
    if err := _validate_date(date_str):
        return JSONResponse(status_code=400, content={"error": err})
    target = date_str or get_today_et()
    ticks = await db_reader.get_today_ticks(target)

    # Fall back to live ticks for today if SQLite has no data yet
    if not ticks and _live_ohlc and _is_today(target):
        ticks = _live_ohlc.get_ticks()

    return {"date": target, "count": len(ticks), "ticks": ticks}


@router.get("/replay_pnl")
async def get_replay_pnl(date_str: str | None = None):
    """Unrealized P&L curve from spread_snapshots for session replay."""
    if err := _validate_date(date_str):
        return JSONResponse(status_code=400, content={"error": err})
    target = date_str or get_today_et()
    curve = await db_reader.get_replay_pnl(target)

    # Fall back to pnl_history from state file for today
    if not curve and _live_state and _is_today(target):
        curve = _live_state.get_today_replay_pnl()

    return {"date": target, "count": len(curve), "pnl_curve": curve}


@router.get("/status")
async def get_status():
    """Current market session status."""
    return get_current_status()
