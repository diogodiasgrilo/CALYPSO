"""Market data endpoints (OHLC, ticks, status)."""

import re

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from dashboard.backend.config import settings
from dashboard.backend.services.db_reader import BacktestingDBReader
from dashboard.backend.services.variant_readers import canonical_db_reader, reader_for
from dashboard.backend.services.live_ohlc import LiveOHLCBuilder
from dashboard.backend.services.live_state import LiveStateProvider
from dashboard.backend.services.market_status import get_current_status, get_today_et

router = APIRouter(prefix="/api/market", tags=["market"])

# NOTE: there is deliberately NO module-level trade-data reader here.
#
# Until 2026-09-18 this was `db_reader = BacktestingDBReader(settings.backtesting_db)`,
# the fallback tick source for /ohlc and /ticks. Bound once at import, it could never
# follow the 2026-07-24 B<->C swap, and its target came from a VM-only systemd drop-in
# (`primary-c.conf`, written 2026-06-02) that had named a DRY-RUN variant for two months.
#
# That is precisely the drift class variant_readers.py was created to kill on
# 2026-07-13 — and `replay_pnl`, IN THIS FILE, was fixed that day while /ohlc and /ticks
# were missed. Both now resolve per request through canonical_db_reader(), which also
# makes them BASIS-AWARE: constructing a BacktestingDBReader directly silently gets the
# defined-risk capital formula, which is how defect D2 survived in a second and third
# location after Phase 3 fixed it once.

# Market-data reader (densest recorder, currently A): SPX/VIX OHLC + ticks. SPX is
# the SAME index for every variant, so the price chart is deliberately sourced from
# whichever variant samples densest — that's what gives full candle bodies instead of
# the flat single-sample dojis a ~1-tick/min recorder produces. See config.py.
#
# This one is CORRECTLY variant-pinned and must NOT be routed through the live-seat
# resolver: it is chosen for SAMPLING DENSITY, not for whose account it belongs to.
market_reader = BacktestingDBReader(settings.market_data_db)

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

    # 1. Authoritative dense bars from the market-data source (HOMER writes
    #    market_ohlc_1min post-close for the densest variant).
    ohlc = await market_reader.get_today_ohlc(target)

    # 2. Live (intraday, before HOMER): compute dense bars from the market-data
    #    source's market_ticks (~4-8 samples/min → real candle bodies).
    if not ohlc:
        ohlc = await market_reader.compute_ohlc_from_ticks(target)

    # 3. Last resort (market-data source has nothing — e.g. it's not running):
    #    the log-parsed live builder, then the LIVE SEAT's own ticks. These are
    #    sparse (~1/min), so the frontend renders them as a line, not crosses.
    if not ohlc and _live_ohlc and _is_today(target):
        ohlc = _live_ohlc.get_ohlc_bars()
    if not ohlc:
        ohlc = await canonical_db_reader().compute_ohlc_from_ticks(target)

    return {"date": target, "count": len(ohlc), "bars": ohlc}


@router.get("/ticks")
async def get_ticks(date_str: str | None = None):
    """Market ticks (heartbeat snapshots) for P&L curve."""
    if err := _validate_date(date_str):
        return JSONResponse(status_code=400, content={"error": err})
    target = date_str or get_today_et()

    # Dense SPX/VIX track from the market-data source (account-agnostic index).
    ticks = await market_reader.get_today_ticks(target)

    # Fall back to live ticks for today, then the LIVE SEAT's own ticks (covers
    # historical dates the market-data source didn't record).
    if not ticks and _live_ohlc and _is_today(target):
        ticks = _live_ohlc.get_ticks()
    if not ticks:
        ticks = await canonical_db_reader().get_today_ticks(target)

    return {"date": target, "count": len(ticks), "ticks": ticks}


@router.get("/replay_pnl")
async def get_replay_pnl(date_str: str | None = None, strategy_id: str = Query(default="")):
    """Unrealized P&L curve from spread_snapshots for session replay.

    ``strategy_id`` scopes the spread_snapshots to the picked variant's DB (same
    resolver as the entries table) so the replay curve matches the strategy shown.
    Empty / primary id → the canonical (live) DB.
    """
    if err := _validate_date(date_str):
        return JSONResponse(status_code=400, content={"error": err})
    reader, is_canonical = reader_for(strategy_id)
    target = date_str or get_today_et()
    curve = await reader.get_replay_pnl(target)

    # Fall back to pnl_history from state file for today (primary variant only).
    if not curve and is_canonical and _live_state and _is_today(target):
        curve = _live_state.get_today_replay_pnl()

    return {"date": target, "count": len(curve), "pnl_curve": curve}


@router.get("/status")
async def get_status():
    """Current market session status."""
    return get_current_status()
