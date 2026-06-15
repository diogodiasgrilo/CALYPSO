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


def _vd_dir() -> str:
    # Variant D's data dir (where the sidecar + isolated calendar DB live).
    return os.path.dirname(str(settings.variant_d_state_file))


@router.get("/status")
def dc_status():
    """Strategy D open calendars + recent outcomes + summary.

    Reads D's isolated calendar DB (dc_calendar.db) — separate from the shared
    backtesting.db — and the open-calendar sidecar.
    """
    vd = _vd_dir()
    return read_dc_status(
        os.path.join(vd, "dc_open_trades.json"),
        os.path.join(vd, "dc_calendar.db"),
    )
