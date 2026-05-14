"""Concrete StreamingInterface implementations for Saxo + IBKR.

Two proxies wrap two structurally different streaming models behind a
single contract. Imported by the adapter modules; not part of the
public `shared.broker` surface — callers should reach streaming via
`broker.streaming`, not by constructing proxies directly.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from shared.broker.interface import QuoteSnapshot, StreamingInterface


logger = logging.getLogger(__name__)


# ─── IB proxy ───────────────────────────────────────────────────────────────


class IBStreamingProxy(StreamingInterface):
    """Wraps `IBClient.streaming` (a StreamingManager) into the
    BrokerInterface streaming contract.

    Mostly 1:1 — StreamingManager was designed with this abstraction in
    mind (Phase A.5). Conversions:
      • instrument_id (str) ↔ conid (int) at the boundary
      • TickSnapshot(field_codes_dict) → QuoteSnapshot(named_fields)
    """

    def __init__(self, ib_client):
        self._ib = ib_client

    @property
    def _mgr(self):
        """StreamingManager — created lazily by IBClient.streaming on first
        access. Raises if the IBClient isn't connected."""
        mgr = self._ib.streaming
        if mgr is None:
            raise RuntimeError(
                "IBStreamingProxy: IBClient.streaming returned None — "
                "client not connected. Call broker.connect() first."
            )
        return mgr

    def subscribe_quote(
        self,
        instrument_id: str,
        fields: Optional[list[str]] = None,
    ) -> None:
        self._mgr.subscribe_quote(int(instrument_id), fields=fields)

    def subscribe_option(
        self,
        instrument_id: str,
        fields: Optional[list[str]] = None,
    ) -> None:
        # IBClient's streaming has a dedicated subscribe_option that adds
        # the greeks fields. Use it.
        self._mgr.subscribe_option(int(instrument_id), fields=fields)

    def unsubscribe_quote(self, instrument_id: str) -> None:
        self._mgr.unsubscribe_quote(int(instrument_id))

    def unsubscribe_all(self) -> None:
        self._mgr.unsubscribe_all()

    def get_snapshot(self, instrument_id: str) -> Optional[QuoteSnapshot]:
        """Translate ibind's field-code-keyed TickSnapshot to the
        broker-agnostic QuoteSnapshot.

        IBKR field codes mapped (per shared.ib_constants):
          31 → last, 84 → bid, 86 → ask, 88 → bid_size, 85 → ask_size,
          7635 → mark, 7308 → delta, 7309 → gamma, 7310 → theta,
          7311 → vega, 7633 → iv, 7638 → open_interest,
          6509 → availability
        """
        snap = self._mgr.get_snapshot(int(instrument_id))
        if snap is None:
            return None
        f = snap.fields
        def _f(code: str) -> Optional[float]:
            v = f.get(code)
            if v is None or v == "":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        return QuoteSnapshot(
            instrument_id=str(instrument_id),
            bid=_f("84"),
            ask=_f("86"),
            last=_f("31"),
            mark=_f("7635"),
            bid_size=int(_f("88")) if _f("88") is not None else None,
            ask_size=int(_f("85")) if _f("85") is not None else None,
            delta=_f("7308"),
            gamma=_f("7309"),
            theta=_f("7310"),
            vega=_f("7311"),
            iv=_f("7633"),
            open_interest=int(_f("7638")) if _f("7638") is not None else None,
            availability=f.get("6509"),
            timestamp=snap.received_at.isoformat() if snap.received_at else None,
            raw=dict(f),
        )

    def last_tick_age(self, instrument_id: str) -> Optional[float]:
        return self._mgr.last_tick_age(int(instrument_id))

    def is_healthy(self, max_tick_age_seconds: float = 60.0) -> bool:
        return self._mgr.is_healthy(max_tick_age_seconds=max_tick_age_seconds)

    def is_ws_connected(self) -> bool:
        return self._mgr.is_ws_connected()

    def active_subscriptions(self) -> list[str]:
        return [str(c) for c in self._mgr.active_conids()]


# ─── Saxo proxy ─────────────────────────────────────────────────────────────


class SaxoStreamingProxy(StreamingInterface):
    """Wraps SaxoClient's bulk-subscription streaming behind the same
    per-instrument-or-bulk contract.

    Saxo's WebSocket model: one `start_price_streaming(uics, callback)`
    call seeds the cache; subsequent quote reads via
    `client.get_quote(uic, skip_cache=False)` come from cache.

    This proxy maintains a private subscription set and restarts the
    Saxo WS when it changes. That's inefficient (restart on every
    add/remove) but matches Saxo's natural shape — and per-instrument
    add/remove is rare in practice (HYDRA subscribes a known set at
    boot and never changes it intraday).

    Greeks: Saxo doesn't push greeks via WS. `subscribe_option` falls
    through to `subscribe_quote`; callers fetch greeks separately via
    REST `get_option_greeks`.
    """

    def __init__(self, saxo_client, asset_type: str = "StockIndexOption"):
        self._saxo = saxo_client
        self._asset_type = asset_type
        # Local subscription set — UIC strings
        self._subscriptions: set[str] = set()
        # Callback chain — caller can register a real callback;
        # default is a no-op so the WS cache fills automatically.
        self._user_callback: Optional[Callable[[int, dict], None]] = None
        self._lock = threading.RLock()

    def set_callback(self, cb: Callable[[int, dict], None]) -> None:
        """Register a tick callback. Optional — Saxo's cache fills
        regardless. Useful for callers that want to be notified per tick."""
        self._user_callback = cb

    def _restart_streaming(self) -> None:
        """Stop + restart Saxo's WS with the current subscription set.

        Cheap when the set is empty (just `stop_price_streaming`).
        """
        with self._lock:
            uics = sorted(int(s) for s in self._subscriptions)

        # Stop the existing WS (idempotent on SaxoClient)
        try:
            self._saxo.stop_price_streaming()
        except Exception as exc:
            logger.debug("SaxoStreamingProxy: stop_price_streaming raised: %s", exc)

        if not uics:
            return

        # Build the subscriptions payload Saxo expects
        payload = [
            {"uic": uic, "asset_type": self._asset_type} for uic in uics
        ]

        def _internal_cb(uic: int, data: dict) -> None:
            if self._user_callback is not None:
                try:
                    self._user_callback(uic, data)
                except Exception as exc:
                    logger.warning(
                        "SaxoStreamingProxy user callback raised "
                        "(swallowed): %s", exc,
                    )

        ok = self._saxo.start_price_streaming(payload, _internal_cb)
        if not ok:
            logger.warning(
                "SaxoStreamingProxy: start_price_streaming returned False — "
                "WS may not be alive for uics=%s", uics,
            )

    def subscribe_quote(
        self,
        instrument_id: str,
        fields: Optional[list[str]] = None,
    ) -> None:
        # Saxo doesn't accept a per-instrument fields list — fields arg
        # ignored. Idempotent: re-adding an existing id is a no-op restart.
        with self._lock:
            self._subscriptions.add(str(int(instrument_id)))
        self._restart_streaming()

    def subscribe_option(
        self,
        instrument_id: str,
        fields: Optional[list[str]] = None,
    ) -> None:
        # Saxo doesn't push greeks via WS — subscribe normally; callers
        # poll get_option_greeks for greeks fields.
        self.subscribe_quote(instrument_id, fields=fields)

    def unsubscribe_quote(self, instrument_id: str) -> None:
        with self._lock:
            self._subscriptions.discard(str(int(instrument_id)))
        self._restart_streaming()

    def unsubscribe_all(self) -> None:
        with self._lock:
            self._subscriptions.clear()
        # Stop without restart
        try:
            self._saxo.stop_price_streaming()
        except Exception as exc:
            logger.debug(
                "SaxoStreamingProxy.unsubscribe_all: stop raised %s", exc,
            )

    def get_snapshot(self, instrument_id: str) -> Optional[QuoteSnapshot]:
        """Pull the latest tick from Saxo's WS cache via get_quote(skip_cache=False).

        Returns None if nothing's been received yet (cache miss) — caller
        can fall back to REST get_quote with skip_cache=True if needed.
        """
        try:
            raw = self._saxo.get_quote(
                int(instrument_id),
                asset_type=self._asset_type,
                skip_cache=False,
            )
        except Exception as exc:
            logger.warning(
                "SaxoStreamingProxy.get_snapshot(%s) failed: %s",
                instrument_id, exc,
            )
            return None
        if not raw:
            return None
        quote = raw.get("Quote") or {}
        bid = quote.get("Bid")
        ask = quote.get("Ask")
        mid = quote.get("Mid")
        if mid is None and bid is not None and ask is not None:
            try:
                mid = (float(bid) + float(ask)) / 2
            except (TypeError, ValueError):
                mid = None
        return QuoteSnapshot(
            instrument_id=str(instrument_id),
            bid=bid,
            ask=ask,
            last=quote.get("LastTraded"),
            mid=mid,
            mark=mid,
            bid_size=quote.get("BidSize"),
            ask_size=quote.get("AskSize"),
            timestamp=raw.get("LastUpdated"),
            raw=raw,
        )

    def last_tick_age(self, instrument_id: str) -> Optional[float]:
        """Saxo doesn't expose per-uic last-tick timestamps via a clean
        API. Returning None signals "unavailable" — callers monitoring
        staleness should use is_healthy() and / or check get_snapshot()
        for a timestamp field."""
        return None

    def is_healthy(self, max_tick_age_seconds: float = 60.0) -> bool:
        """For Saxo, healthy = WS healthy. Per-instrument tick-age
        tracking isn't available from SaxoClient's public surface, so
        this collapses onto the WS-connectivity check."""
        return self.is_ws_connected()

    def is_ws_connected(self) -> bool:
        try:
            return bool(self._saxo.is_websocket_healthy())
        except Exception:
            return False

    def active_subscriptions(self) -> list[str]:
        with self._lock:
            return sorted(self._subscriptions)
