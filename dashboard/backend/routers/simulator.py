"""Simulator API endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from dashboard.backend.config import settings
from dashboard.backend.services.db_reader import BacktestingDBReader
from dashboard.backend.services.simulator import SimParams, SimulatorEngine

router = APIRouter(prefix="/api/simulator", tags=["simulator"])

db_reader = BacktestingDBReader(settings.backtesting_db)
engine = SimulatorEngine(db_reader)


class SimRequest(BaseModel):
    """Request body for running a simulation."""
    call_stop_buffer: float = Field(default=0.35, ge=0, le=5.0)
    put_stop_buffer: float = Field(default=1.55, ge=0, le=20.0)
    min_credit_call: float = Field(default=200.0, ge=0, le=500.0)
    min_credit_put: float = Field(default=275.0, ge=0, le=1000.0)
    put_only_max_vix: float = Field(default=15.0, ge=10.0, le=50.0)
    max_entries: int = Field(default=3, ge=1, le=7)
    commission_per_leg: float = Field(default=2.50, ge=0, le=10.0)
    conditional_entries: bool = True
    downday_threshold_pct: float = Field(default=0.003, ge=0.001, le=0.02)


@router.get("/status")
async def get_status():
    """Data availability and countdown info."""
    return await engine.get_status()


@router.get("/defaults")
async def get_defaults():
    """Current production config values (baseline)."""
    return {
        "call_stop_buffer": 0.35,
        "put_stop_buffer": 1.55,
        "min_credit_call": 200.0,
        "min_credit_put": 275.0,
        "put_only_max_vix": 15.0,
        "max_entries": 3,
        "commission_per_leg": 2.50,
        "conditional_entries": True,
        "downday_threshold_pct": 0.003,
    }


@router.post("/run")
async def run_simulation(req: SimRequest):
    """Run simulation with given parameters."""
    params = SimParams(
        call_stop_buffer=req.call_stop_buffer,
        put_stop_buffer=req.put_stop_buffer,
        min_credit_call=req.min_credit_call,
        min_credit_put=req.min_credit_put,
        put_only_max_vix=req.put_only_max_vix,
        max_entries=req.max_entries,
        commission_per_leg=req.commission_per_leg,
        conditional_entries=req.conditional_entries,
        downday_threshold_pct=req.downday_threshold_pct,
    )
    return await engine.simulate(params)
