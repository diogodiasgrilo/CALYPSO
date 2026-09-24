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


def _underlying_of(vid: str) -> Optional[str]:
    """A variant's traded symbol, from its own config. None if unreadable."""
    cfg_path = getattr(settings, f"variant_{vid}_config_file", None)
    if not cfg_path:
        return None
    try:
        import json
        with open(cfg_path) as f:
            cfg = json.load(f)
        s = cfg.get("strategy", {}) or {}
        return (s.get("underlying_symbol") or cfg.get("underlying_symbol") or None)
    except Exception:  # noqa: BLE001 — a display route must not 500
        return None


def _market_db() -> Optional[str]:
    """A database whose price ticks are of the instrument **H actually trades**.

    ⚠️ THIS USED TO RESOLVE TO THE LIVE SEAT, AND THAT BECAME WRONG THE MOMENT
    H MOVED TO SPY (2026-09-24). The live seat is variant B, which trades SPX:
    the page would have drawn **SPX at ~7,700 against SPY strikes at ~765**, so
    the band chart and the "did it break the band?" verdict it feeds would both
    have been computed from a completely different instrument. Nothing would
    have errored — the chart would simply have been about another market.

    So the match is on the SYMBOL now, not on a seat:

    1. **H's own database first.** Its ticks are by definition the instrument H
       traded *on that date*, which also makes history self-consistent: the
       2026-09-23 rows are SPX and pair correctly with that day's SPX strikes.
       Its only weakness is coverage — a variant's DB starts when it was
       installed — and a partial path of the RIGHT instrument beats a complete
       one of the wrong instrument.
    2. Any other variant whose configured ``underlying_symbol`` matches H's
       (variant E trades SPY and has recorded it since long before H did).
    3. Nothing. An empty path renders no band, which is honest.

    There is deliberately **no generic fallback to the root/live DB**: that is
    precisely the behaviour that produced the mismatch.
    """
    sf = getattr(settings, f"variant_{_VARIANT}_state_file", None)
    if not sf:
        return None
    own = os.path.join(os.path.dirname(str(sf)), "backtesting.db")
    candidates = [own]

    want = (_underlying_of(_VARIANT) or "").strip().upper()
    if want:
        root = os.path.dirname(os.path.dirname(str(sf)))
        try:
            import shared.strategy_taxonomy as tax
            for vid in tax.available_ids():
                if vid == _VARIANT:
                    continue
                if (_underlying_of(vid) or "").strip().upper() == want:
                    candidates.append(
                        os.path.join(root, f"variant_{vid}", "backtesting.db"))
        except Exception:  # noqa: BLE001
            pass

    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _sibling_market_dbs() -> list:
    """Same-underlying databases OTHER than H's own, in taxonomy order.

    Used only when H's own DB does not cover the requested day — the instrument
    still has to match, so this never widens to "any DB with ticks".
    """
    sf = getattr(settings, f"variant_{_VARIANT}_state_file", None)
    want = (_underlying_of(_VARIANT) or "").strip().upper()
    if not sf or not want:
        return []
    root = os.path.dirname(os.path.dirname(str(sf)))
    out = []
    try:
        import shared.strategy_taxonomy as tax
        for vid in tax.available_ids():
            if vid == _VARIANT:
                continue
            if (_underlying_of(vid) or "").strip().upper() == want:
                p = os.path.join(root, f"variant_{vid}", "backtesting.db")
                if os.path.exists(p):
                    out.append(p)
    except Exception:  # noqa: BLE001
        pass
    return out


@router.get("/status")
def long_strangle_status(date: str = Query(default="")):
    """One day of variant H: open, closed, declined, and the peak-versus-exit gap.

    ``date`` empty means the most recent day with activity, so the page has
    something to show even when H did not trade today — which, for a
    dry-run-locked variant that is not installed, is the normal case.
    """
    out = read_ls_status(_db_path(), date=date)
    # The session's underlying path, so the page can draw the expected-move band
    # and answer "did it move enough?" — including on days H declined to enter.
    if isinstance(out, dict) and out.get("date"):
        day = out["date"]
        # H's own DB is the right instrument by construction but may not cover
        # the day (it only starts when the variant was installed). Take the
        # symbol-matched fallback when its coverage is too thin to draw.
        path = read_spx_path(_market_db(), day)
        if len(path) < 2:
            for alt in _sibling_market_dbs():
                path = read_spx_path(alt, day)
                if len(path) >= 2:
                    break
        out["spx_path"] = path
        # The SYMBOL, so the page stops calling everything SPX. Without it the
        # axis, the tooltip and the prose all assert an instrument the strategy
        # no longer trades.
        out["underlying_symbol"] = _underlying_of(_VARIANT)
    return out


@router.get("/recent")
def long_strangle_recent(limit: int = Query(default=30, ge=1, le=200)):
    """Recent closed positions across days, newest first.

    Each row carries the debit it risked alongside the dollars it made: for this
    strategy a +$250 day means nothing without knowing whether $200 or $2,000
    was at stake.
    """
    return {"rows": read_ls_recent(_db_path(), limit=limit)}
