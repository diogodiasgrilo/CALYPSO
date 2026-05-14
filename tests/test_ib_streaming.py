"""Tests for shared.ib_streaming.

Covers: lifecycle (start/stop/context manager), subscribe/unsubscribe API,
tick caching from the consume loop, smd refresh cycle, idempotency,
health-check semantics.

The mock IbkrWsClient is hand-rolled to mimic ibind's surface area (start,
stop, subscribe/unsubscribe, connected flag, new_queue_accessor that returns
a real queue-backed accessor).
"""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_streaming import (
    DEFAULT_QUOTE_FIELDS,
    DEFAULT_OPTION_FIELDS,
    REFRESH_INTERVAL_S,
    RESUBSCRIBE_GAP_S,
    StreamingManager,
    TickSnapshot,
)


# ─── Mock IbkrWsClient (mimics ibind's surface) ─────────────────────────────


class FakeQueueAccessor:
    """Mimics ibind.QueueAccessor — wraps a queue.Queue with .get(block, timeout)."""

    def __init__(self, q: queue.Queue):
        self._q = q

    def get(self, block: bool = True, timeout: float = None):
        try:
            return self._q.get(block=block, timeout=timeout)
        except queue.Empty:
            return None


class FakeWsClient:
    """Records subscribe/unsubscribe calls; feeds ticks via push()."""

    def __init__(self, connected: bool = True):
        self._connected = connected
        self._tick_queue: queue.Queue = queue.Queue()
        self.subscribe_calls: list[tuple[str, dict]] = []
        self.unsubscribe_calls: list[tuple[str, dict]] = []
        self._subscribed_channels: set = set()

    @property
    def connected(self) -> bool:
        return self._connected

    def set_connected(self, v: bool) -> None:
        self._connected = v

    def subscribe(self, channel: str, data: dict = None,
                  needs_confirmation: bool = None,
                  subscription_processor=None) -> bool:
        self.subscribe_calls.append((channel, data or {}))
        self._subscribed_channels.add(channel)
        return True

    def unsubscribe(self, channel: str, data: dict = None,
                    needs_confirmation: bool = None,
                    subscription_processor=None) -> bool:
        self.unsubscribe_calls.append((channel, data or {}))
        # StreamingManager passes the bare "md+{conid}" (ibind prepends u/s)
        self._subscribed_channels.discard(channel)
        return True

    def new_queue_accessor(self, key):
        return FakeQueueAccessor(self._tick_queue)

    def push(self, tick: dict) -> None:
        """Inject a tick as if it arrived on the WS."""
        self._tick_queue.put(tick)


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def ws_client():
    return FakeWsClient(connected=True)


@pytest.fixture
def streaming(ws_client):
    """A started StreamingManager. Auto-stops at end of test."""
    sm = StreamingManager(
        ws_client, refresh_interval_s=0.5,
        consume_poll_s=0.005, resubscribe_gap_s=0.01,
    )
    sm.start()
    yield sm
    sm.stop()


# ─── Lifecycle ──────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_start_stop(self, ws_client):
        sm = StreamingManager(ws_client, consume_poll_s=0.005)
        sm.start()
        assert sm._started is True
        sm.stop()
        assert sm._started is False

    def test_start_idempotent(self, ws_client):
        sm = StreamingManager(ws_client, consume_poll_s=0.005)
        sm.start()
        sm.start()  # no-op
        sm.stop()

    def test_stop_idempotent(self, ws_client):
        sm = StreamingManager(ws_client, consume_poll_s=0.005)
        sm.start()
        sm.stop()
        sm.stop()  # no-op

    def test_context_manager(self, ws_client):
        with StreamingManager(ws_client, consume_poll_s=0.005) as sm:
            assert sm._started is True
        assert sm._started is False


# ─── Subscribe / unsubscribe ────────────────────────────────────────────────


class TestSubscribeQuote:
    def test_sends_smd_channel(self, streaming, ws_client):
        streaming.subscribe_quote(12345)
        time.sleep(0.05)  # let the call finish
        assert ("md+12345", {"fields": DEFAULT_QUOTE_FIELDS}) in ws_client.subscribe_calls

    def test_records_in_active_conids(self, streaming):
        streaming.subscribe_quote(12345)
        assert 12345 in streaming.active_conids()

    def test_custom_fields(self, streaming, ws_client):
        streaming.subscribe_quote(99, fields=["31", "84"])
        assert ("md+99", {"fields": ["31", "84"]}) in ws_client.subscribe_calls

    def test_subscribe_option_uses_greeks_fields(self, streaming, ws_client):
        streaming.subscribe_option(99)
        # Last subscribe should include the greeks codes
        last_channel, last_data = ws_client.subscribe_calls[-1]
        assert last_channel == "md+99"
        for f in ("7308", "7309", "7310", "7311", "7633"):
            assert f in last_data["fields"]

    def test_resubscribe_with_new_fields_replaces(self, streaming, ws_client):
        streaming.subscribe_quote(12345, fields=["31"])
        time.sleep(0.05)
        streaming.subscribe_quote(12345, fields=["31", "84", "86"])
        # Should see umd+12345 (the re-subscribe path) and 2 smd+ calls
        assert any(c == "md+12345" for c, _ in ws_client.unsubscribe_calls)
        smd_calls = [c for c, _ in ws_client.subscribe_calls if c == "md+12345"]
        assert len(smd_calls) >= 2


class TestUnsubscribeQuote:
    def test_sends_umd_channel(self, streaming, ws_client):
        streaming.subscribe_quote(12345)
        streaming.unsubscribe_quote(12345)
        assert any(c == "md+12345" for c, _ in ws_client.unsubscribe_calls)

    def test_removes_from_active_conids(self, streaming):
        streaming.subscribe_quote(12345)
        streaming.unsubscribe_quote(12345)
        assert 12345 not in streaming.active_conids()

    def test_clears_snapshot(self, streaming, ws_client):
        streaming.subscribe_quote(12345)
        ws_client.push({"conid": 12345, "31": "5500.0"})
        time.sleep(0.1)
        assert streaming.get_snapshot(12345) is not None
        streaming.unsubscribe_quote(12345)
        assert streaming.get_snapshot(12345) is None

    def test_unsubscribe_unknown_conid_is_noop(self, streaming, ws_client):
        # Should not raise
        streaming.unsubscribe_quote(99999)


class TestUnsubscribeAll:
    def test_clears_all_subscriptions(self, streaming, ws_client):
        for c in (1, 2, 3):
            streaming.subscribe_quote(c)
        streaming.unsubscribe_all()
        assert streaming.active_conids() == []
        for c in (1, 2, 3):
            assert any(ch == f"md+{c}" for ch, _ in ws_client.unsubscribe_calls)


# ─── Tick consumption ───────────────────────────────────────────────────────


class TestTickConsumption:
    def test_tick_updates_snapshot(self, streaming, ws_client):
        streaming.subscribe_quote(12345)
        ws_client.push({"conid": 12345, "31": "5500.0", "84": "5499.5", "86": "5500.5"})
        time.sleep(0.1)
        snap = streaming.get_snapshot(12345)
        assert snap is not None
        assert snap.fields.get("31") == "5500.0"
        assert snap.fields.get("84") == "5499.5"
        assert snap.fields.get("86") == "5500.5"
        assert snap.received_at is not None

    def test_partial_tick_merges_with_existing(self, streaming, ws_client):
        """Subsequent ticks for the same conid merge into the snapshot."""
        streaming.subscribe_quote(12345)
        ws_client.push({"conid": 12345, "31": "5500.0"})
        time.sleep(0.05)
        ws_client.push({"conid": 12345, "84": "5499.5"})
        time.sleep(0.05)
        snap = streaming.get_snapshot(12345)
        assert snap.fields.get("31") == "5500.0"  # preserved from first
        assert snap.fields.get("84") == "5499.5"  # added by second

    def test_last_tick_age(self, streaming, ws_client):
        streaming.subscribe_quote(12345)
        ws_client.push({"conid": 12345, "31": "100"})
        time.sleep(0.05)
        age = streaming.last_tick_age(12345)
        assert age is not None
        assert age < 1.0  # very recent

    def test_no_tick_yet_returns_none(self, streaming):
        streaming.subscribe_quote(12345)
        # Don't push anything
        assert streaming.last_tick_age(12345) is None
        # get_snapshot also returns None
        assert streaming.get_snapshot(12345) is None

    def test_tick_for_unsubscribed_conid_still_cached(self, streaming, ws_client):
        """If a stray tick arrives for a conid we never subscribed to, we
        still cache it (defensive — IBKR sometimes pushes during refresh
        cycles).
        """
        ws_client.push({"conid": 99, "31": "1.0"})
        time.sleep(0.1)
        snap = streaming.get_snapshot(99)
        assert snap is not None
        assert snap.fields.get("31") == "1.0"

    def test_drops_non_dict_messages(self, streaming, ws_client):
        ws_client.push("not a dict")
        ws_client.push(None)
        ws_client.push([1, 2, 3])
        time.sleep(0.05)
        # No crash; cache empty
        assert streaming.active_conids() == []

    def test_drops_messages_without_conid(self, streaming, ws_client):
        ws_client.push({"system_message": "heartbeat"})
        time.sleep(0.05)
        # No cache entries
        for cid in streaming.active_conids():
            assert streaming.get_snapshot(cid) is None

    def test_drops_message_with_uncoerceable_conid(self, streaming, ws_client):
        """If `conid` is present but can't be coerced to int (e.g. junk
        string from a malformed payload), drop the message silently."""
        ws_client.push({"conid": "not_an_int", "31": "5500"})
        ws_client.push({"conid": None, "31": "5500"})
        ws_client.push({"conid": [1, 2], "31": "5500"})
        time.sleep(0.05)
        # No crash; nothing cached
        assert streaming.active_conids() == []

    def test_unwraps_wrapped_payload(self, streaming, ws_client):
        """Production ibind delivers `{conid_int: inner_payload}`. Verify
        the unwrap path correctly extracts the conid and field codes."""
        ws_client.push({12345: {"conid": 12345, "topic": "smd+12345",
                                "31": "5500.0", "84": "5499.5"}})
        time.sleep(0.1)
        snap = streaming.get_snapshot(12345)
        assert snap is not None
        assert snap.fields.get("31") == "5500.0"
        assert snap.fields.get("84") == "5499.5"

    def test_handler_exception_does_not_kill_consume_loop(self, ws_client):
        """A pathological tick should not kill the consume thread.
        Push a malformed message that triggers _handle_tick to raise via
        an unusual outer-wrap shape, then push a good message after and
        verify the consume loop still processes it."""
        sm = StreamingManager(
            ws_client, consume_poll_s=0.005, resubscribe_gap_s=0.01,
        )
        sm.start()
        try:
            # A wrapped message whose inner is non-dict — _handle_tick's
            # inner = next(iter(msg.values())) gets a non-dict; we early-
            # return rather than raising, but if a future refactor were to
            # raise, the next good message must still flow.
            ws_client.push({1: "broken_inner_value"})
            ws_client.push({"conid": 99, "31": "1.0"})
            time.sleep(0.1)
            snap = sm.get_snapshot(99)
            assert snap is not None and snap.fields.get("31") == "1.0"
        finally:
            sm.stop()


class TestConsumeLoopErrorPaths:
    def test_no_queue_accessor_consume_exits_cleanly(self, ws_client):
        """If ibind's queue accessor can't be created, _consume_loop
        logs a warning and returns; stop() must still join cleanly."""
        sm = StreamingManager(
            ws_client, consume_poll_s=0.005, resubscribe_gap_s=0.01,
        )
        sm.start()
        # Simulate accessor disappearance after start
        sm._queue_accessor = None
        time.sleep(0.05)
        sm.stop()
        # If we get here without hanging, the test passes
        assert sm._started is False


class TestHealthChecks:
    def test_is_ws_connected_true_when_started_and_connected(
        self, streaming, ws_client,
    ):
        assert streaming.is_ws_connected() is True

    def test_is_ws_connected_false_when_disconnected(self, streaming, ws_client):
        ws_client.set_connected(False)
        assert streaming.is_ws_connected() is False

    def test_is_ws_connected_false_when_ws_attribute_raises(self, ws_client):
        sm = StreamingManager(ws_client, consume_poll_s=0.005, resubscribe_gap_s=0.01)
        sm.start()
        try:
            # Replace the connected property with one that raises
            class Broken:
                @property
                def connected(self):
                    raise RuntimeError("WS state corrupted")

                def new_queue_accessor(self, *_):
                    return ws_client.new_queue_accessor(None)

                def subscribe(self, *a, **k): return True

                def unsubscribe(self, *a, **k): return True
            sm._ws = Broken()
            assert sm.is_ws_connected() is False
        finally:
            sm.stop()


class TestUnsubscribeAllSwallowsErrors:
    def test_per_conid_unsub_error_does_not_block_others(self, ws_client):
        """If unsubscribe raises mid-loop, the remaining conids must still
        be removed from local state."""
        sm = StreamingManager(
            ws_client, consume_poll_s=0.005, resubscribe_gap_s=0.01,
        )
        sm.start()
        try:
            sm.subscribe_quote(1)
            sm.subscribe_quote(2)
            sm.subscribe_quote(3)
            orig = ws_client.unsubscribe

            def failing(channel, **kw):
                if channel == "md+2":
                    raise RuntimeError("simulated server hiccup")
                return orig(channel, **kw)
            ws_client.unsubscribe = failing
            sm.unsubscribe_all()  # no raise
            assert sm.active_conids() == []
        finally:
            sm.stop()


class TestStopHandlesPerConidErrors:
    def test_stop_swallows_per_conid_unsubscribe_errors(self, ws_client):
        """stop() should still tear down even if individual unsubscribes
        fail. Best-effort cleanup per the docstring."""
        sm = StreamingManager(
            ws_client, consume_poll_s=0.005, resubscribe_gap_s=0.01,
        )
        sm.start()
        sm.subscribe_quote(42)
        # Make every unsubscribe raise
        ws_client.unsubscribe = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("net down")
        )
        sm.stop()  # must not raise
        assert sm._started is False


# ─── smd refresh loop ───────────────────────────────────────────────────────


class TestRefreshLoop:
    def test_smd_topics_cycle_after_refresh_interval(self, ws_client):
        """The headline behavior — without this, ibind silently dies at 15 min."""
        sm = StreamingManager(
            ws_client, refresh_interval_s=0.2,
            consume_poll_s=0.005, resubscribe_gap_s=0.01,
        )
        sm.start()
        try:
            sm.subscribe_quote(12345)
            initial_sub_count = sum(1 for c, _ in ws_client.subscribe_calls if c == "md+12345")
            # Wait through ~3 refresh cycles
            time.sleep(0.7)
            final_sub_count = sum(1 for c, _ in ws_client.subscribe_calls if c == "md+12345")
            final_unsub_count = sum(1 for c, _ in ws_client.unsubscribe_calls if c == "md+12345")
            # Should have re-cycled at least twice
            assert final_sub_count >= initial_sub_count + 2
            assert final_unsub_count >= 2
        finally:
            sm.stop()

    def test_refresh_preserves_fields(self, ws_client):
        """When we re-subscribe, the field set we registered originally
        must be re-sent — IBKR doesn't remember our fields between umd/smd."""
        sm = StreamingManager(
            ws_client, refresh_interval_s=0.2,
            consume_poll_s=0.005, resubscribe_gap_s=0.01,
        )
        sm.start()
        try:
            sm.subscribe_quote(12345, fields=["31", "7635"])
            time.sleep(0.5)  # let one refresh cycle fire
            # Find the most recent smd+12345 subscribe call
            smd_12345_calls = [
                d for c, d in ws_client.subscribe_calls
                if c == "md+12345"
            ]
            assert smd_12345_calls
            assert smd_12345_calls[-1]["fields"] == ["31", "7635"]
        finally:
            sm.stop()

    def test_refresh_continues_on_individual_failure(self, ws_client):
        """If one subscription's refresh fails, others should still cycle."""
        # Make the WS fail on umd+999 but succeed on others
        orig_unsubscribe = ws_client.unsubscribe

        def selective_fail(channel, **kw):
            # Match the unsubscribe call site for conid 999. After the
            # channel double-prefix fix, the bare wire-level channel we
            # send to ibind is "md+{conid}" (ibind prepends 'u' itself).
            if channel == "md+999":
                raise Exception("simulated server hiccup")
            return orig_unsubscribe(channel, **kw)

        ws_client.unsubscribe = selective_fail
        sm = StreamingManager(
            ws_client, refresh_interval_s=0.2,
            consume_poll_s=0.005, resubscribe_gap_s=0.01,
        )
        sm.start()
        try:
            sm.subscribe_quote(999)
            sm.subscribe_quote(1000)
            time.sleep(0.5)
            # 1000 should have cycled successfully despite 999 failing
            smd_1000 = [c for c, _ in ws_client.subscribe_calls if c == "md+1000"]
            assert len(smd_1000) >= 2  # initial + at least one refresh
        finally:
            sm.stop()


# ─── Health check ───────────────────────────────────────────────────────────


class TestIsHealthy:
    def test_healthy_when_recent_ticks(self, streaming, ws_client):
        streaming.subscribe_quote(12345)
        ws_client.push({"conid": 12345, "31": "100"})
        time.sleep(0.05)
        assert streaming.is_healthy() is True

    def test_unhealthy_when_no_ticks_yet(self, streaming):
        streaming.subscribe_quote(12345)
        # No tick pushed
        assert streaming.is_healthy() is False

    def test_unhealthy_when_disconnected(self, streaming, ws_client):
        streaming.subscribe_quote(12345)
        ws_client.push({"conid": 12345, "31": "100"})
        time.sleep(0.05)
        ws_client.set_connected(False)
        assert streaming.is_healthy() is False

    def test_unhealthy_when_stop_event_set(self, ws_client):
        sm = StreamingManager(ws_client, consume_poll_s=0.005)
        # Not started → not healthy
        assert sm.is_healthy() is False

    def test_stale_tick_fails_health(self, ws_client):
        sm = StreamingManager(ws_client, consume_poll_s=0.005)
        sm.start()
        try:
            sm.subscribe_quote(12345)
            ws_client.push({"conid": 12345, "31": "100"})
            time.sleep(0.05)
            # Force the snapshot's received_at way back in time
            with sm._lock:
                sm._snapshots[12345].received_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
            assert sm.is_healthy(max_tick_age_seconds=10.0) is False
        finally:
            sm.stop()


# ─── TickSnapshot helper ────────────────────────────────────────────────────


class TestTickSnapshot:
    def test_is_stale_when_no_received_at(self):
        snap = TickSnapshot(conid=1)
        assert snap.is_stale(max_age_seconds=10) is True

    def test_fresh_is_not_stale(self):
        snap = TickSnapshot(conid=1, received_at=datetime.now(timezone.utc))
        assert snap.is_stale(max_age_seconds=10) is False

    def test_old_is_stale(self):
        snap = TickSnapshot(
            conid=1, received_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        assert snap.is_stale(max_age_seconds=10) is True
