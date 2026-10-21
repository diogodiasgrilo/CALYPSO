"""Admission gates for an inbound signal. Pure functions, no I/O.

Every gate returns None to admit, or a string reason to reject. The reason is
recorded verbatim in the ledger, so a later reader can tell "we never saw it"
from "we saw it and declined, for this reason" — a distinction the alert DLQ
incident of 2026-10-07 showed is worth paying for up front.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

MAX_PAYLOAD_BYTES = 8192          # a TradingView alert is tiny; cap the surface
VALID_ACTIONS = ("buy", "sell")


def check_secret(supplied: Optional[str], expected: Optional[str]) -> Optional[str]:
    """Constant-time shared-secret check.

    TradingView cannot be relied on to send custom auth HEADERS, so the secret
    travels in the JSON body. `compare_digest` so the check cannot be probed by
    timing, and an unset/blank expected secret REFUSES everything rather than
    admitting everything — a misconfiguration must fail closed on a path that
    will one day place orders.
    """
    if not expected:
        return "no shared secret configured (refusing: fail-closed)"
    if not supplied:
        return "missing secret"
    if not hmac.compare_digest(str(supplied), str(expected)):
        return "bad secret"
    return None


def check_shape(payload: Dict[str, Any]) -> Optional[str]:
    """Structural validation before anything is trusted."""
    if not isinstance(payload, dict):
        return "payload is not a JSON object"
    ticker = payload.get("ticker")
    if not ticker or not isinstance(ticker, str):
        return "missing or non-string ticker"
    if len(ticker) > 16:
        return f"implausible ticker length ({len(ticker)})"
    action = str(payload.get("action", "")).strip().lower()
    if action not in VALID_ACTIONS:
        return f"action must be one of {VALID_ACTIONS}, got {payload.get('action')!r}"
    qty = payload.get("quantity")
    if qty is not None:
        try:
            q = float(qty)
        except (TypeError, ValueError):
            return f"non-numeric quantity {qty!r}"
        if q <= 0:
            return f"quantity must be positive, got {q}"
    return None


def check_allowlist(ticker: str, allowlist: Dict[str, Any]) -> Optional[str]:
    """Only symbols we named may trade.

    The indicator is a black box we cannot read; the limits have to be ours.
    An empty allowlist admits NOTHING (fail-closed), which is also the correct
    behaviour before a signal source has been chosen.
    """
    if not allowlist:
        return "symbol allowlist is empty (refusing: fail-closed)"
    if ticker.upper() not in {k.upper() for k in allowlist}:
        return f"{ticker} is not on the symbol allowlist"
    return None


def check_quantity(ticker: str, qty: Optional[float],
                   allowlist: Dict[str, Any]) -> Tuple[Optional[str], Optional[float]]:
    """Clamp to the per-symbol cap. Returns (reason, effective_quantity).

    A cap CLAMPS rather than rejects, so an oversized signal still trades at a
    size we chose instead of being silently dropped — but the clamp is recorded
    so the difference is visible later.
    """
    spec = {k.upper(): v for k, v in allowlist.items()}.get(ticker.upper(), {})
    cap = spec.get("max_quantity")
    default = spec.get("default_quantity")
    effective = qty if qty is not None else default
    if effective is None:
        return ("no quantity in signal and no default_quantity configured", None)
    if cap is not None and effective > cap:
        return (None, float(cap))
    return (None, float(effective))


def check_daily_cap(orders_today: int, max_per_day: Optional[int]) -> Optional[str]:
    if max_per_day is not None and orders_today >= max_per_day:
        return f"daily order cap reached ({orders_today}/{max_per_day})"
    return None


def check_market_open(ticker: str, allowlist: Dict[str, Any],
                      now: Optional[datetime] = None,
                      is_equity_open=None) -> Optional[str]:
    """An alert at 03:00 must not place an order into a closed or thin book.

    Crypto is 24/7 and is exempt BY ASSET CLASS, not by skipping the gate —
    the distinction matters because the same service handles both.
    """
    spec = {k.upper(): v for k, v in allowlist.items()}.get(ticker.upper(), {})
    asset = str(spec.get("asset_class", "equity")).lower()
    if asset == "crypto":
        return None
    if is_equity_open is None:
        from shared.market_hours import is_market_open as _open
        is_equity_open = _open
    return None if is_equity_open() else "equity market is closed"


def idempotency_key(payload: Dict[str, Any], received_at: datetime) -> str:
    """Stable identity for a signal, so a TradingView resend is a no-op.

    Mirrors the cOID dedup the order path already relies on. Built from
    (ticker, action, signal time, source). When the alert carries no `time` we
    fall back to a MINUTE bucket of arrival: a resend seconds later still
    collapses, while a genuine repeat next minute does not. That is a weaker
    guarantee and is why `{{timenow}}` belongs in the alert body.
    """
    t = str(payload.get("time") or "").strip()
    if not t:
        t = "bucket:" + received_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    raw = "|".join([
        str(payload.get("ticker", "")).upper(),
        str(payload.get("action", "")).lower(),
        t,
        str(payload.get("source", "")),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
