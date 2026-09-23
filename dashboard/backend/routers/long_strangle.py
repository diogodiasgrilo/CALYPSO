"""Strategy H (0DTE Long Strangle) status endpoints.

Read-only, long-gamma-native view. Kept separate from ``/api/variants/*`` and
from ``/api/dc/*`` for the same reason those two are separate from each other:
**the P&L shapes are not comparable.** The iron-condor renderers assume premium
was COLLECTED — "expired worthless" is profit there and maximum loss here — and
the calendar renderers assume a net debit that is theta-POSITIVE. H is net debit
and theta-negative, the only such strategy in the fleet, so it gets its own view
rather than being squeezed into one that would mis-state it.

Everything served here is dry-run. H places no real orders and is not installed
on the VM; the ``available: false`` payload is the expected response today.
"""

import os
from typing import Optional

from fastapi import APIRouter, Query

from dashboard.backend.config import settings
from dashboard.backend.services.ls_reader import (
    read_ls_recent,
    read_ls_status,
    read_spx_path,
)

router = APIRouter(prefix="/api/long-strangle", tags=["long-strangle"])

#: The only member of the ``long_gamma_0dte`` group today. Kept as a named
#: constant rather than inlined so a second long-gamma variant is a one-line
#: change here, not a search through string literals.
_VARIANT = "h"


def _db_path() -> Optional[str]:
    """Path to H's isolated ``long_strangle.db``, derived from its state file.

    Derived rather than configured separately so it cannot drift from the rest
    of variant H's paths — the same trick ``/api/dc/status`` uses for the
    calendar DBs.
    """
    sf = getattr(settings, f"variant_{_VARIANT}_state_file", None)
    if not sf:
        return None
    return os.path.join(os.path.dirname(str(sf)), "long_strangle.db")


def _market_db() -> Optional[str]:
    """A database with the session's SPX ticks.

    NOT H's own: SPX is market-wide, and a variant's database only starts when
    it was installed (H's begins mid-session on 2026-09-23). Resolves through
    the taxonomy to the live seat — never hardcoded, because that seat has
    already moved once (C→B) — then falls back to variant A's root DB.
    """
    sf = getattr(settings, f"variant_{_VARIANT}_state_file", None)
    if not sf:
        return None
    root = os.path.dirname(os.path.dirname(str(sf)))
    candidates = []
    try:
        import shared.strategy_taxonomy as tax
        for vid in tax.available_ids():
            if tax.STRATEGIES[vid].status == "live":
                candidates.append(os.path.join(root, f"variant_{vid}", "backtesting.db"))
    except Exception:  # noqa: BLE001 — a display route must not 500
        pass
    candidates.append(os.path.join(root, "backtesting.db"))
    candidates.append(os.path.join(os.path.dirname(str(sf)), "backtesting.db"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


@router.get("/status")
def long_strangle_status(date: str = Query(default="")):
    """One day of variant H: open, closed, declined, and the peak-versus-exit gap.

    ``date`` empty means the most recent day with activity, so the page has
    something to show even when H did not trade today — which, for a
    dry-run-locked variant that is not installed, is the normal case.
    """
    out = read_ls_status(_db_path(), date=date)
    # The session's SPX path, so the page can draw the expected-move band and
    # answer "did it move enough?" — including on days H declined to enter.
    if isinstance(out, dict) and out.get("date"):
        out["spx_path"] = read_spx_path(_market_db(), out["date"])
    return out


@router.get("/recent")
def long_strangle_recent(limit: int = Query(default=30, ge=1, le=200)):
    """Recent closed positions across days, newest first.

    Each row carries the debit it risked alongside the dollars it made: for this
    strategy a +$250 day means nothing without knowing whether $200 or $2,000
    was at stake.
    """
    return {"rows": read_ls_recent(_db_path(), limit=limit)}
