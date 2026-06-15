"""Strategy D (DC Time Machine) status endpoint.

Read-only, D-native view (open calendars + outcomes from D's sidecar + DB).
Kept separate from /api/variants/* because D is a multi-day net-DEBIT double
calendar and does not belong in the 0DTE iron-condor head-to-head.
"""

import os

from fastapi import APIRouter

from dashboard.backend.config import settings
from dashboard.backend.services.dc_reader import read_dc_status

router = APIRouter(prefix="/api/dc", tags=["dc"])


def _sidecar_path() -> str:
    # dc_open_trades.json lives next to variant D's state file.
    return os.path.join(os.path.dirname(str(settings.variant_d_state_file)), "dc_open_trades.json")


@router.get("/status")
def dc_status():
    """Strategy D open calendars + recent outcomes + summary."""
    return read_dc_status(_sidecar_path(), str(settings.variant_d_backtesting_db))
