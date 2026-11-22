"""HYDRA Dashboard — FastAPI application."""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from dashboard.backend.auth import require_session, require_widget_token
from dashboard.backend.config import settings
from dashboard.backend.ws.manager import ConnectionManager
from dashboard.backend.ws.broadcaster import Broadcaster
from dashboard.backend.ws import router as ws_router_module
from dashboard.backend.routers import (auth as auth_router, hydra, metrics, market, agents,
                                       widget, variants, dc, strategies, long_strangle)
from dashboard.backend.services import auth_db
from dashboard.backend.services.live_state import LiveStateProvider
from dashboard.backend.services.state_reader import StateFileReader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("dashboard")

manager = ConnectionManager()
broadcaster = Broadcaster(manager)

# /api/health reports the state file's REAL freshness, so it needs a live read.
# It gets its OWN reader over the same path rather than sharing the
# broadcaster's: `current()` consumes the mtime change that the broadcaster's
# `_poll_state` waits on, and a health poll that swallowed it would silence the
# WebSocket round that should have pushed that update. Same reason
# LiveStateProvider is given its own reader below.
_health_state_reader = StateFileReader(broadcaster.state_reader.file_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start broadcaster on startup, stop on shutdown."""
    logger.info("HYDRA Dashboard starting")
    auth_db.init_db(settings.dashboard_auth_db)
    auth_db.cleanup_expired_sessions(settings.dashboard_auth_db)
    if settings.require_accounts_configured and auth_db.count_users(settings.dashboard_auth_db) == 0:
        raise RuntimeError(
            "DASHBOARD_REQUIRE_ACCOUNTS_CONFIGURED is set but no dashboard accounts exist "
            f"in {settings.dashboard_auth_db}. Refusing to start with the login gate open. "
            "Run: python -m scripts.manage_dashboard_users add <username>"
        )
    ws_router_module.set_dependencies(manager, broadcaster)

    # Wire up live data sources for REST endpoints (bridges gap until HOMER runs)
    live_state = LiveStateProvider(
        StateFileReader(broadcaster.state_reader.file_path),
        db_reader=broadcaster.db_reader,
    )
    market.set_live_sources(broadcaster.live_ohlc, live_state)
    metrics.set_live_state(live_state)

    await broadcaster.start()
    yield
    logger.info("HYDRA Dashboard shutting down")
    await broadcaster.stop()


app = FastAPI(
    title="HYDRA Dashboard",
    description="Real-time monitoring dashboard for HYDRA trading bot",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# /api/auth is unguarded — it IS the login flow that issues the sessions
# every other router below requires.
app.include_router(auth_router.router)

# REST routers — guarded by a real per-account session cookie (replaces the
# old single-shared API key). When no accounts exist yet the dependency is a
# no-op (dev/local); /api/health and the static frontend below stay open either way.
_api_guard = [Depends(require_session)]
app.include_router(hydra.router, dependencies=_api_guard)
app.include_router(metrics.router, dependencies=_api_guard)
app.include_router(market.router, dependencies=_api_guard)
app.include_router(agents.router, dependencies=_api_guard)
# The widget is the ONE endpoint that also accepts a scoped widget token: the
# iOS Scriptable widget cannot perform a password + TOTP login, so it has been
# dead since the 2026-08-03 account migration retired the shared API key. The
# token is read-only by construction (no non-GET route here) and is rejected by
# every other router, which is why it gets its own dependency rather than being
# folded into require_session.
app.include_router(widget.router, dependencies=[Depends(require_widget_token)])
app.include_router(variants.router, dependencies=_api_guard)
app.include_router(dc.router, dependencies=_api_guard)  # Strategy D native view
app.include_router(strategies.router, dependencies=_api_guard)  # taxonomy-driven strategy + group API
# Strategy H native view. Separate from /api/variants (credit ICs) and /api/dc
# (theta-POSITIVE calendars) because H is net-debit AND theta-negative — the
# only long-gamma strategy here, and the one shape neither renderer states
# correctly ("expired worthless" is profit there, maximum loss here).
app.include_router(long_strangle.router, dependencies=_api_guard)

# WebSocket router
app.include_router(ws_router_module.router)


@app.get("/api/health")
async def health():
    """Health check endpoint.

    Reads the state file live. It used to report
    ``broadcaster.state_reader.get_cached()`` — explicitly "without touching
    disk" — which went permanently stale once the broadcast loops began
    skipping their reads with no client connected (2026-11-16). On 2026-11-21
    this endpoint claimed ``state_date: 2026-11-17`` while every state file on
    disk said 2026-11-21.

    ``state_file_age_seconds`` is here because a date alone cannot tell
    "tracking" from "frozen on a plausible date" — which is how that went
    unnoticed, including by the Gate 6 check in LIVE_READINESS_CHECKLIST that
    reads this endpoint.
    """
    state = _health_state_reader.current()
    age = None
    try:
        mtime = _health_state_reader.file_path.stat().st_mtime
        age = round(max(0.0, time.time() - mtime), 1)
    except OSError:
        pass
    return {
        "status": "ok",
        "clients": manager.client_count,
        "state_loaded": state is not None,
        "state_date": state.get("date") if state else None,
        "state_file_age_seconds": age,
    }


# Serve frontend static files (fallback — nginx handles this in production)
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
