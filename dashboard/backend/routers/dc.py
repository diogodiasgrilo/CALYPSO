"""Calendar-strategy status endpoint (D = DC Time Machine, E = SPY Double Calendar).

Read-only, calendar-native view (open calendars + outcomes from each strategy's
sidecar + isolated DB). Kept separate from /api/variants/* because these are
multi-day net-DEBIT double calendars and do not belong in the 0DTE iron-condor
head-to-head.

2026-09-17 (dashboard rebuild Phase 6, defect D6): this router read strategy
D's state-file and baseline-date settings fields directly, so **E could never
show its own data** — the /dc page served D's calendars under whichever strategy
was picked. E has had its own ``dc_calendar.db`` and sidecar since it was built.
Now scoped by ``strategy_id``, defaulting to D for backwards compatibility with
every existing caller.
"""

import os
from typing import Optional

from fastapi import APIRouter, Query

from dashboard.backend.config import settings
from dashboard.backend.services.dc_reader import read_dc_status
import shared.strategy_taxonomy as tax

router = APIRouter(prefix="/api/dc", tags=["dc"])

#: Default when no strategy is specified — D, the original single occupant of
#: this page. Keeps pre-Phase-6 callers (and bookmarked /dc links) unchanged.
_DEFAULT_CALENDAR = "d"


def _calendar_ids() -> list[str]:
    """Calendar strategies, from the taxonomy rather than a hardcoded list.

    Driven by ``pnl_shape == "debit"`` so a third calendar registers itself here
    automatically — the failure this phase fixes was precisely a hardcoded
    single-strategy assumption.
    """
    out = []
    for vid in tax.available_ids():
        try:
            if tax.group(vid).pnl_shape == "debit":
                out.append(vid)
        except Exception:  # noqa: BLE001 — a display route must not 500
            continue
    return out or [_DEFAULT_CALENDAR]


def _resolve(strategy_id: Optional[str]) -> str:
    """Pick the calendar strategy to serve; unknown/empty → the default.

    Degrades rather than 404s, matching ``reader_for``'s contract elsewhere: a
    bad query param renders something sensible instead of an error page.
    """
    sid = (strategy_id or "").strip().lower()
    return sid if sid in _calendar_ids() else _DEFAULT_CALENDAR


def _vd_dir(vid: str) -> Optional[str]:
    """Data dir for a calendar strategy (sidecar + isolated calendar DB)."""
    sf = getattr(settings, f"variant_{vid}_state_file", None)
    return os.path.dirname(str(sf)) if sf else None


@router.get("/status")
def dc_status(strategy_id: str = Query(default="")):
    """Open calendars + recent outcomes + summary for ONE calendar strategy.

    Reads that strategy's isolated calendar DB (``dc_calendar.db``) — separate
    from the shared backtesting.db — and its open-calendar sidecar.
    """
    vid = _resolve(strategy_id)
    vd = _vd_dir(vid)
    if vd is None:
        return {"available": False, "reason": f"No data directory for strategy {vid!r}"}
    status = read_dc_status(
        os.path.join(vd, "dc_open_trades.json"),
        os.path.join(vd, "dc_calendar.db"),
        baseline_date=getattr(settings, f"variant_{vid}_baseline_date", "") or "",
    )
    # Echo which strategy answered, so the page can never mislabel D's calendars
    # as E's — the exact failure this replaces.
    if isinstance(status, dict):
        status["strategy_id"] = vid
    return status
