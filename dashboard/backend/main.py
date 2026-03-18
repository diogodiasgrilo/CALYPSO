"""HYDRA Dashboard — FastAPI application."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from dashboard.backend.config import settings
from dashboard.backend.ws.manager import ConnectionManager
from dashboard.backend.ws.broadcaster import Broadcaster
from dashboard.backend.ws import router as ws_router_module
from dashboard.backend.routers import hydra, metrics, market, agents, widget, simulator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("dashboard")

manager = ConnectionManager()
broadcaster = Broadcaster(manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start broadcaster on startup, stop on shutdown."""
    logger.info("HYDRA Dashboard starting")
    ws_router_module.set_dependencies(manager, broadcaster)
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

# REST routers
app.include_router(hydra.router)
app.include_router(metrics.router)
app.include_router(market.router)
app.include_router(agents.router)
app.include_router(widget.router)
app.include_router(simulator.router)

# WebSocket router
app.include_router(ws_router_module.router)


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    state = broadcaster.state_reader.get_cached()
    return {
        "status": "ok",
        "clients": manager.client_count,
        "state_loaded": state is not None,
        "state_date": state.get("date") if state else None,
    }


# Serve frontend static files (fallback — nginx handles this in production)
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
