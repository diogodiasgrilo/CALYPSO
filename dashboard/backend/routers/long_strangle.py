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
from dashboard.backend.services.ls_reader import read_ls_recent, read_ls_status

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


@router.get("/status")
def long_strangle_status(date: str = Query(default="")):
    """One day of variant H: open, closed, declined, and the peak-versus-exit gap.

    ``date`` empty means the most recent day with activity, so the page has
    something to show even when H did not trade today — which, for a
    dry-run-locked variant that is not installed, is the normal case.
    """
    return read_ls_status(_db_path(), date=date)


@router.get("/recent")
def long_strangle_recent(limit: int = Query(default=30, ge=1, le=200)):
    """Recent closed positions across days, newest first.

    Each row carries the debit it risked alongside the dollars it made: for this
    strategy a +$250 day means nothing without knowing whether $200 or $2,000
    was at stake.
    """
    return {"rows": read_ls_recent(_db_path(), limit=limit)}
