"""IBKR WebSocket streaming manager — Phase A.5.

Wraps ibind's IbkrWsClient to maintain a per-conid market-data subscription
set with automatic 13-minute rotation (per research_scratch/10_cpapi_
streaming.md). Without rotation, IBKR silently kills `smd+{conid}` topics
after ~15 minutes of session age — the WebSocket stays "connected" but no
more ticks arrive for that conid. ibind 0.1.23 does not handle this.

Design (production-grade from day 1; was a known production bug in
similar Saxo pattern):

  • One subscription set, keyed by conid.
  • Background `_refresh_loop` thread cycles each subscription every
    REFRESH_INTERVAL_S (default 13 min — safely under IBKR's 15-min
    auto-kill ceiling).
  • Background `_consume_loop` thread reads ticks from ibind's
    MARKET_DATA queue accessor and updates an in-memory snapshot dict.
  • `get_snapshot(conid)` reads the latest cached tick — no I/O.
  • `last_tick_age(conid)` for staleness alerts.
  • Graceful start/stop.
  • Thread-safe via internal RLock.

Caller pattern:
    streaming = StreamingManager(ws_client)
    streaming.start()
    streaming.subscribe_quote(spx_conid)
    streaming.subscribe_option(opt_conid)  # adds greeks fields
    ...
    snap = streaming.get_snapshot(spx_conid)
    if streaming.last_tick_age(spx_conid) > 30:
        # alert: stale data
        pass
    streaming.stop()

Or use as a context manager:
    with StreamingManager(ws_client) as streaming:
        ...

The integration with IBClient lives there (Phase A.5 wiring): IBClient
gets a `.streaming` lazy-property that creates an IbkrWsClient + StreamingManager
on first access.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


logger = logging.getLogger(__name__)


# IBKR's documented `smd` topic auto-kill is ~15 min. Rotate at 13 min to
# leave a safety margin. Sourced from ibind issue #145 / research_scratch/10.
REFRESH_INTERVAL_S = 13 * 60

# Per-conid throttle on resubscribes — avoid flooding IBKR if our refresh
# loop trips a sweep through many conids at once.
RESUBSCRIBE_GAP_S = 0.5

# How often the consume thread polls ibind's queue. Lower = lower tick
# latency at cost of CPU. 25ms is well under any monitoring cadence we
# need (HYDRA samples every 2-15 sec).
CONSUME_POLL_S = 0.025

# Default fields subscribed when the caller doesn't specify. Sourced from
# shared.ib_constants — the single source of truth for field-code defaults
# shared with shared.ib_client. Aliased to keep the local name stable.
from shared.ib_constants import (
    DEFAULT_QUOTE_FIELDS,
    DEFAULT_OPTION_QUOTE_FIELDS as DEFAULT_OPTION_FIELDS,
)


@dataclass
class TickSnapshot:
    """Last-known tick for a single conid.

    Updated by _consume_loop on every incoming WS message. `received_at`
    is set on update; `age_seconds()` / `is_stale()` derive freshness.
    """
    conid: int
    fields: dict[str, str] = field(default_factory=dict)
    received_at: Optional[datetime] = None

    def age_seconds(self) -> Optional[float]:
        """Seconds since the last tick, or None if no tick has ever arrived."""
        if self.received_at is None:
            return None
        return (datetime.now(timezone.utc) - self.received_at).total_seconds()

    def is_stale(self, max_age_seconds: float) -> bool:
        age = self.age_seconds()
        return age is None or age > max_age_seconds


class StreamingManager:
    """Owner of WS subscriptions + tick cache + smd refresh loop.

    Public API is thread-safe. Background threads stop cleanly on stop()
    or context-manager exit.
    """

    def __init__(
        self,
        ws_client,
        *,
        refresh_interval_s: float = REFRESH_INTERVAL_S,
        consume_poll_s: float = CONSUME_POLL_S,
        resubscribe_gap_s: float = RESUBSCRIBE_GAP_S,
    ):
        """
        Args:
            ws_client: an instance of ibind.IbkrWsClient (or a duck-typed
                mock for testing). Caller is responsible for connecting it.
            refresh_interval_s: cycle interval for smd rotation; default 13 min
            consume_poll_s: queue-poll cadence; default 25ms
            resubscribe_gap_s: pause between umd and following smd within
                one refresh cycle. Defaults to 500ms which is safe in prod;
                tests override to a small value to keep them fast.
        """
        self._ws = ws_client
        self._refresh_interval_s = refresh_interval_s
        self._consume_poll_s = consume_poll_s
        self._resubscribe_gap_s = resubscribe_gap_s

        # In-memory state
        self._lock = threading.RLock()
        self._subscriptions: dict[int, list[str]] = {}  # conid → fields
        self._snapshots: dict[int, TickSnapshot] = {}

        # Threading
        self._stop_event = threading.Event()
        self._refresh_thread: Optional[threading.Thread] = None
        self._consume_thread: Optional[threading.Thread] = None
        self._queue_accessor = None  # set by start()
        self._started = False

    # ─── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Spin up the refresh + consume threads. Idempotent."""
        with self._lock:
            if self._started:
                return
            self._stop_event.clear()
            # Get a typed queue accessor for MARKET_DATA messages
            try:
                from ibind import IbkrWsKey
                self._queue_accessor = self._ws.new_queue_accessor(
                    IbkrWsKey.MARKET_DATA
                )
            except Exception as exc:
                logger.warning(
                    "Could not create MARKET_DATA queue accessor: %s — "
                    "ticks will not be cached", exc,
                )
                self._queue_accessor = None

            self._consume_thread = threading.Thread(
                target=self._consume_loop, name="ib-streaming-consume", daemon=True,
            )
            self._refresh_thread = threading.Thread(
                target=self._refresh_loop, name="ib-streaming-refresh", daemon=True,
            )
            self._consume_thread.start()
            self._refresh_thread.start()
            self._started = True
            logger.info("StreamingManager started — refresh interval %ds", self._refresh_interval_s)

    def stop(self) -> None:
        """Signal stop + join threads. Unsubscribes all active conids.

        Best-effort: errors during shutdown are logged and swallowed.
        """
        with self._lock:
            if not self._started:
                return
            self._stop_event.set()

        # Unsubscribe all conids
        for conid in list(self._subscriptions.keys()):
            try:
                self._send_unsubscribe(conid)
            except Exception as exc:
                logger.warning("Unsubscribe failed for conid %s: %s", conid, exc)

        # Wait for threads
        for t in (self._refresh_thread, self._consume_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        with self._lock:
            self._started = False
            self._subscriptions.clear()
            self._snapshots.clear()
        logger.info("StreamingManager stopped")

    def __enter__(self) -> "StreamingManager":
        self.start()
        return self

    def __exit__(self, *args) -> None:
        self.stop()

    # ─── Subscription API ─────────────────────────────────────────────────

    def subscribe_quote(
        self,
        conid: int,
        fields: Optional[list[str]] = None,
    ) -> None:
        """Subscribe to streaming quotes for `conid`.

        Idempotent — re-subscribing to an already-subscribed conid replaces
        the field set in-place (sends umd then smd with new fields).

        Holds the manager lock through the unsubscribe → sleep → subscribe
        sequence so the refresh loop can't interleave a competing cycle
        for the same conid mid-flight. Lock is RLock so it's safe even
        when callers nest into this from already-locked paths.
        """
        conid = int(conid)
        fields = list(fields) if fields else DEFAULT_QUOTE_FIELDS
        with self._lock:
            already = conid in self._subscriptions
            self._subscriptions[conid] = fields
            if already:
                self._send_unsubscribe(conid)
                time.sleep(self._resubscribe_gap_s)
            self._send_subscribe(conid, fields)

    def subscribe_option(
        self,
        conid: int,
        fields: Optional[list[str]] = None,
    ) -> None:
        """Convenience wrapper that adds greeks fields by default."""
        self.subscribe_quote(conid, fields=fields or DEFAULT_OPTION_FIELDS)

    def unsubscribe_quote(self, conid: int) -> None:
        """Stop streaming for `conid`. Idempotent."""
        conid = int(conid)
        with self._lock:
            had = self._subscriptions.pop(conid, None)
            self._snapshots.pop(conid, None)
        if had is not None:
            self._send_unsubscribe(conid)

    def unsubscribe_all(self) -> None:
        """Tear down every subscription. Doesn't stop the manager itself."""
        with self._lock:
            conids = list(self._subscriptions.keys())
            self._subscriptions.clear()
            self._snapshots.clear()
        for c in conids:
            try:
                self._send_unsubscribe(c)
            except Exception as exc:
                logger.warning("Unsubscribe failed for conid %s: %s", c, exc)

    def get_snapshot(self, conid: int) -> Optional[TickSnapshot]:
        """Last-known tick for `conid`, or None if never received one yet."""
        with self._lock:
            return self._snapshots.get(int(conid))

    def last_tick_age(self, conid: int) -> Optional[float]:
        """Seconds since last tick for `conid`, or None if never received."""
        snap = self.get_snapshot(conid)
        return snap.age_seconds() if snap is not None else None

    def active_conids(self) -> list[int]:
        with self._lock:
            return list(self._subscriptions.keys())

    def is_ws_connected(self) -> bool:
        """Lightweight liveness — True iff the underlying WS is connected.

        Does NOT require any subscription to have produced ticks recently,
        so this stays True during pre-market / weekend / market-closure
        when there's nothing for IBKR to push. Use this for "is the pipe
        alive" checks; use `is_healthy` for "are quotes actually flowing".
        """
        try:
            return bool(self._ws.connected) and self._started
        except Exception:
            return False

    def is_healthy(self, max_tick_age_seconds: float = 60.0) -> bool:
        """True iff WS is connected AND every subscription has a recent tick.

        Strict: assumes the market is producing ticks. During off-hours
        every subscription will look stale because IBKR isn't pushing
        anything — use `is_ws_connected()` instead for off-hours monitors.
        Use this for HYDRA's in-session heartbeat-alive gate.
        """
        if not self.is_ws_connected():
            return False
        with self._lock:
            for conid in self._subscriptions.keys():
                snap = self._snapshots.get(conid)
                if snap is None or snap.is_stale(max_tick_age_seconds):
                    return False
        return True

    # ─── Internals ────────────────────────────────────────────────────────

    def _send_subscribe(self, conid: int, fields: list[str]) -> None:
        # ibind's IbkrSubscriptionProcessor.make_subscribe_payload prepends
        # 's' to whatever channel we pass — so we send the bare "md+{conid}"
        # and the wire payload becomes "smd+{conid}+...". Passing "smd+..."
        # here would yield "ssmd+..." and IBKR rejects it.
        try:
            self._ws.subscribe(channel=f"md+{conid}", data={"fields": fields})
            logger.debug("smd+%d subscribed (fields=%s)", conid, fields)
        except Exception as exc:
            logger.error("smd+%d subscribe failed: %s", conid, exc)
            raise

    def _send_unsubscribe(self, conid: int) -> None:
        # Same prefix rule: ibind prepends 'u'. Pass "md+{conid}" so the wire
        # payload is "umd+{conid}+{}".
        try:
            self._ws.unsubscribe(channel=f"md+{conid}")
            logger.debug("umd+%d unsubscribed", conid)
        except Exception as exc:
            # Don't raise on unsubscribe errors — IBKR sometimes returns
            # 200-ish errors for already-unsubscribed topics, harmless.
            logger.warning("umd+%d unsubscribe error (non-fatal): %s", conid, exc)

    def _refresh_loop(self) -> None:
        """Every refresh_interval_s, cycle each subscription's smd topic.

        Without this, IBKR auto-kills smd topics after ~15 min. ibind 0.1.23
        does NOT handle this; we own it.

        Each per-conid unsub→sleep→sub sequence is held under the manager
        lock so a concurrent subscribe_quote() call for the same conid
        can't interleave its own cycle. Sleeping under a lock is OK here
        — the RLock has a single owner (this manager), contention is
        only between the refresh thread and direct API callers.
        """
        while not self._stop_event.wait(self._refresh_interval_s):
            with self._lock:
                items = list(self._subscriptions.items())
            for conid, fields in items:
                if self._stop_event.is_set():
                    return
                try:
                    with self._lock:
                        # Re-check membership under the lock — caller may
                        # have unsubscribed since we snapshotted `items`.
                        if conid not in self._subscriptions:
                            continue
                        self._send_unsubscribe(conid)
                        time.sleep(self._resubscribe_gap_s)
                        self._send_subscribe(conid, fields)
                except Exception as exc:
                    logger.warning(
                        "smd refresh failed for conid %s: %s "
                        "(will retry on next cycle)", conid, exc,
                    )

    def _consume_loop(self) -> None:
        """Read ticks from ibind's MARKET_DATA queue and update snapshots."""
        if self._queue_accessor is None:
            logger.warning("StreamingManager consume loop: no queue accessor — exiting")
            return
        # ibind's QueueAccessor.get internally handles queue.Empty and
        # returns None on timeout, so the loop normally never throws here.
        # Catch narrowly and log unexpected errors so we don't silently
        # mask a bug in ibind or a corrupted queue.
        while not self._stop_event.is_set():
            try:
                msg = self._queue_accessor.get(block=True, timeout=self._consume_poll_s)
            except Exception as exc:
                logger.warning(
                    "QueueAccessor.get raised unexpectedly (continuing): %s", exc,
                )
                continue
            if msg is None:
                continue
            try:
                self._handle_tick(msg)
            except Exception as exc:
                logger.warning("Tick handler error (continuing): %s", exc)

    def _handle_tick(self, msg) -> None:
        """Update the snapshot for the conid in `msg`.

        Robust to both shapes ibind can deliver in the MARKET_DATA queue:

          • **Wrapped** — `{conid_value: {"conid": ..., "_updated": ...,
            "topic": "smd+...", "31": "5500.0", "84": "5499.5", ...}}`.
            This is what ibind's `_preprocess_market_data_message` emits
            (both with and without `unwrap_market_data`). Production path.

          • **Unwrapped** — `{"conid": ..., "31": "5500.0", ...}`. Used by
            unit tests and as a defensive fallback.

        We require `unwrap_market_data=False` at the IbkrWsClient
        construction site (see IBClient.streaming) so the inner payload
        keeps numeric CP-API field codes (`"31"`, `"84"`, …) rather than
        ibind's remapped human names (`"last_price"`, `"bid_price"`).
        """
        if not isinstance(msg, dict):
            return
        # Unwrap if outer dict is {conid_int: inner_payload}
        if "conid" not in msg:
            if len(msg) != 1:
                return
            inner = next(iter(msg.values()))
            if not isinstance(inner, dict):
                return
            msg = inner
        conid = msg.get("conid") or msg.get("conidEx")
        if conid is None:
            return
        try:
            conid = int(conid)
        except (TypeError, ValueError):
            return
        # Filter to numeric field codes (drop topic, _updated, server_id, etc.)
        fields = {
            k: v for k, v in msg.items()
            if isinstance(k, str) and (k.isdigit() or k == "6509")
        }
        with self._lock:
            snap = self._snapshots.get(conid)
            if snap is None:
                snap = TickSnapshot(conid=conid)
                self._snapshots[conid] = snap
            snap.fields.update(fields)
            snap.received_at = datetime.now(timezone.utc)
