"""Tests for shared/broker/streaming_proxies.py.

IBStreamingProxy + SaxoStreamingProxy each wrap their broker's native
streaming surface behind the StreamingInterface contract. Tests mock
the underlying clients — no live broker.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.broker.interface import QuoteSnapshot, StreamingInterface
from shared.broker.streaming_proxies import (
    IBStreamingProxy,
    SaxoStreamingProxy,
)


# ─── IBStreamingProxy ───────────────────────────────────────────────────────


@pytest.fixture
def ib_with_streaming():
    """Mock IBClient + nested MagicMock for .streaming. Default state:
    streaming exists, ws connected, healthy, no snapshots."""
    ib = MagicMock()
    ib.streaming = MagicMock()
    ib.streaming.is_ws_connected.return_value = True
    ib.streaming.is_healthy.return_value = True
    ib.streaming.active_conids.return_value = []
    return ib


@pytest.fixture
def ib_proxy(ib_with_streaming):
    return IBStreamingProxy(ib_with_streaming)


class TestIBContract:
    def test_is_streaming_interface(self, ib_proxy):
        assert isinstance(ib_proxy, StreamingInterface)


class TestIBSubscribeUnsubscribe:
    def test_subscribe_quote_converts_id_to_int(self, ib_proxy, ib_with_streaming):
        ib_proxy.subscribe_quote("12345")
        ib_with_streaming.streaming.subscribe_quote.assert_called_once_with(
            12345, fields=None,
        )

    def test_subscribe_quote_with_fields(self, ib_proxy, ib_with_streaming):
        ib_proxy.subscribe_quote("12345", fields=["31", "84"])
        ib_with_streaming.streaming.subscribe_quote.assert_called_once_with(
            12345, fields=["31", "84"],
        )

    def test_subscribe_option_routes_to_subscribe_option(
        self, ib_proxy, ib_with_streaming,
    ):
        ib_proxy.subscribe_option("12345")
        ib_with_streaming.streaming.subscribe_option.assert_called_once_with(
            12345, fields=None,
        )

    def test_unsubscribe_quote_int_conversion(self, ib_proxy, ib_with_streaming):
        ib_proxy.unsubscribe_quote("12345")
        ib_with_streaming.streaming.unsubscribe_quote.assert_called_once_with(12345)

    def test_unsubscribe_all_delegates(self, ib_proxy, ib_with_streaming):
        ib_proxy.unsubscribe_all()
        ib_with_streaming.streaming.unsubscribe_all.assert_called_once()


class TestIBGetSnapshot:
    def test_translates_field_codes_to_named(self, ib_proxy, ib_with_streaming):
        """The 6-key map: 84→bid, 86→ask, 31→last, 7635→mark, 7308→delta, 6509→availability."""
        fake_snap = MagicMock()
        fake_snap.fields = {
            "31": "5500.0", "84": "5499.5", "86": "5500.5",
            "88": "10", "85": "12",
            "7635": "5500.0",
            "7308": "-0.42", "7309": "0.08",
            "7633": "0.18",
            "7638": "5000",
            "6509": "R",
        }
        fake_snap.received_at = datetime(2026, 5, 14, 17, 0, tzinfo=timezone.utc)
        ib_with_streaming.streaming.get_snapshot.return_value = fake_snap

        snap = ib_proxy.get_snapshot("12345")
        assert isinstance(snap, QuoteSnapshot)
        assert snap.instrument_id == "12345"
        assert snap.bid == 5499.5
        assert snap.ask == 5500.5
        assert snap.last == 5500.0
        assert snap.mark == 5500.0
        assert snap.bid_size == 10
        assert snap.ask_size == 12
        assert snap.delta == -0.42
        assert snap.gamma == 0.08
        assert snap.iv == 0.18
        assert snap.open_interest == 5000
        assert snap.availability == "R"
        assert snap.timestamp is not None

    def test_missing_fields_become_none(self, ib_proxy, ib_with_streaming):
        fake_snap = MagicMock()
        fake_snap.fields = {"31": "5500.0"}  # only last
        fake_snap.received_at = None
        ib_with_streaming.streaming.get_snapshot.return_value = fake_snap

        snap = ib_proxy.get_snapshot("12345")
        assert snap.last == 5500.0
        assert snap.bid is None and snap.ask is None
        assert snap.delta is None

    def test_empty_string_becomes_none(self, ib_proxy, ib_with_streaming):
        """IBKR sometimes returns empty strings for fields it has no data for."""
        fake_snap = MagicMock()
        fake_snap.fields = {"31": "", "84": "5499.5"}
        fake_snap.received_at = None
        ib_with_streaming.streaming.get_snapshot.return_value = fake_snap

        snap = ib_proxy.get_snapshot("12345")
        assert snap.last is None
        assert snap.bid == 5499.5

    def test_no_snapshot_returns_none(self, ib_proxy, ib_with_streaming):
        ib_with_streaming.streaming.get_snapshot.return_value = None
        assert ib_proxy.get_snapshot("12345") is None


class TestIBHealthChecks:
    def test_is_ws_connected_delegates(self, ib_proxy, ib_with_streaming):
        ib_with_streaming.streaming.is_ws_connected.return_value = False
        assert ib_proxy.is_ws_connected() is False

    def test_is_healthy_forwards_max_age(self, ib_proxy, ib_with_streaming):
        ib_proxy.is_healthy(max_tick_age_seconds=120.0)
        ib_with_streaming.streaming.is_healthy.assert_called_with(
            max_tick_age_seconds=120.0,
        )

    def test_active_subscriptions_int_to_str(self, ib_proxy, ib_with_streaming):
        ib_with_streaming.streaming.active_conids.return_value = [111, 222, 333]
        assert ib_proxy.active_subscriptions() == ["111", "222", "333"]


class TestIBStreamingNotReady:
    def test_subscribe_when_not_connected_raises_runtime_error(self):
        ib = MagicMock()
        ib.streaming = None  # IBClient returns None when not connected
        proxy = IBStreamingProxy(ib)
        with pytest.raises(RuntimeError, match="not connected"):
            proxy.subscribe_quote("12345")


# ─── SaxoStreamingProxy ─────────────────────────────────────────────────────


@pytest.fixture
def mock_saxo():
    s = MagicMock()
    s.is_websocket_healthy.return_value = True
    s.start_price_streaming.return_value = True
    return s


@pytest.fixture
def saxo_proxy(mock_saxo):
    return SaxoStreamingProxy(mock_saxo)


class TestSaxoContract:
    def test_is_streaming_interface(self, saxo_proxy):
        assert isinstance(saxo_proxy, StreamingInterface)


class TestSaxoSubscribeBulkRestartModel:
    def test_subscribe_adds_uic_to_set(self, saxo_proxy, mock_saxo):
        saxo_proxy.subscribe_quote("12345")
        assert "12345" in saxo_proxy.active_subscriptions()
        # Saxo's bulk model: each change restarts streaming
        mock_saxo.stop_price_streaming.assert_called()
        mock_saxo.start_price_streaming.assert_called()

    def test_multiple_subscribes_accumulate(self, saxo_proxy, mock_saxo):
        saxo_proxy.subscribe_quote("111")
        saxo_proxy.subscribe_quote("222")
        saxo_proxy.subscribe_quote("333")
        assert saxo_proxy.active_subscriptions() == ["111", "222", "333"]
        # Last start_price_streaming carries all three uics
        last_call_uics = sorted([
            entry["uic"] for entry in
            mock_saxo.start_price_streaming.call_args_list[-1].args[0]
        ])
        assert last_call_uics == [111, 222, 333]

    def test_subscribe_idempotent(self, saxo_proxy, mock_saxo):
        saxo_proxy.subscribe_quote("111")
        saxo_proxy.subscribe_quote("111")  # same uic
        assert saxo_proxy.active_subscriptions() == ["111"]

    def test_unsubscribe_removes(self, saxo_proxy, mock_saxo):
        saxo_proxy.subscribe_quote("111")
        saxo_proxy.subscribe_quote("222")
        saxo_proxy.unsubscribe_quote("111")
        assert saxo_proxy.active_subscriptions() == ["222"]

    def test_unsubscribe_all_clears_and_stops_without_restart(
        self, saxo_proxy, mock_saxo,
    ):
        saxo_proxy.subscribe_quote("111")
        saxo_proxy.subscribe_quote("222")
        mock_saxo.reset_mock()
        saxo_proxy.unsubscribe_all()
        assert saxo_proxy.active_subscriptions() == []
        mock_saxo.stop_price_streaming.assert_called()
        # NO restart after unsubscribe_all
        mock_saxo.start_price_streaming.assert_not_called()

    def test_subscribe_option_falls_through_to_subscribe_quote(
        self, saxo_proxy, mock_saxo,
    ):
        """Saxo doesn't push greeks via WS; subscribe_option ≡ subscribe_quote."""
        saxo_proxy.subscribe_option("12345")
        assert "12345" in saxo_proxy.active_subscriptions()


class TestSaxoGetSnapshot:
    def test_reads_via_get_quote_skip_cache_false(self, saxo_proxy, mock_saxo):
        mock_saxo.get_quote.return_value = {
            "Quote": {"Bid": 5.20, "Ask": 5.40, "Mid": 5.30},
            "LastUpdated": "2026-05-14T12:00",
        }
        snap = saxo_proxy.get_snapshot("12345")
        assert isinstance(snap, QuoteSnapshot)
        assert snap.bid == 5.20
        assert snap.ask == 5.40
        assert snap.mid == 5.30
        kwargs = mock_saxo.get_quote.call_args.kwargs
        assert kwargs["skip_cache"] is False
        assert kwargs["asset_type"] == "StockIndexOption"

    def test_computes_mid_when_missing(self, saxo_proxy, mock_saxo):
        mock_saxo.get_quote.return_value = {"Quote": {"Bid": 5.20, "Ask": 5.40}}
        snap = saxo_proxy.get_snapshot("12345")
        assert snap.mid == pytest.approx(5.30)

    def test_none_response_returns_none(self, saxo_proxy, mock_saxo):
        mock_saxo.get_quote.return_value = None
        assert saxo_proxy.get_snapshot("12345") is None

    def test_swallows_get_quote_exception(self, saxo_proxy, mock_saxo):
        mock_saxo.get_quote.side_effect = Exception("REST timeout")
        assert saxo_proxy.get_snapshot("12345") is None


class TestSaxoHealthChecks:
    def test_is_ws_connected_delegates(self, saxo_proxy, mock_saxo):
        mock_saxo.is_websocket_healthy.return_value = False
        assert saxo_proxy.is_ws_connected() is False

    def test_is_ws_connected_swallows_attribute_error(self, saxo_proxy, mock_saxo):
        mock_saxo.is_websocket_healthy.side_effect = AttributeError("renamed")
        assert saxo_proxy.is_ws_connected() is False

    def test_is_healthy_collapses_onto_ws_connected(self, saxo_proxy, mock_saxo):
        """Saxo doesn't expose per-uic tick-age; is_healthy folds onto
        the WS check."""
        mock_saxo.is_websocket_healthy.return_value = True
        assert saxo_proxy.is_healthy() is True
        mock_saxo.is_websocket_healthy.return_value = False
        assert saxo_proxy.is_healthy() is False

    def test_last_tick_age_always_none(self, saxo_proxy):
        """Saxo doesn't expose per-uic last-tick timestamps cleanly —
        signal unavailability with None rather than guessing."""
        assert saxo_proxy.last_tick_age("12345") is None


class TestSaxoCallbackChain:
    def test_user_callback_invoked_on_tick(self, saxo_proxy, mock_saxo):
        ticks: list = []

        def cb(uic, data):
            ticks.append((uic, data))

        saxo_proxy.set_callback(cb)
        saxo_proxy.subscribe_quote("12345")
        # Grab the internal callback that the proxy passed to Saxo
        internal_cb = mock_saxo.start_price_streaming.call_args.args[1]
        internal_cb(12345, {"Bid": 5.20})
        assert ticks == [(12345, {"Bid": 5.20})]

    def test_user_callback_exception_swallowed(self, saxo_proxy, mock_saxo):
        def cb(uic, data):
            raise RuntimeError("user bug")

        saxo_proxy.set_callback(cb)
        saxo_proxy.subscribe_quote("12345")
        internal_cb = mock_saxo.start_price_streaming.call_args.args[1]
        # No raise propagates out
        internal_cb(12345, {})


# ─── Property hookups on the adapters ───────────────────────────────────────


class TestAdapterStreamingProperty:
    def test_ib_adapter_exposes_proxy(self):
        from shared.broker.ibkr_adapter import IBBrokerAdapter
        ib = MagicMock()
        ib.streaming = MagicMock()
        adapter = IBBrokerAdapter(ib)
        s = adapter.streaming
        assert isinstance(s, StreamingInterface)
        # Second access returns the same proxy (lazy cache)
        assert adapter.streaming is s

    def test_saxo_adapter_exposes_proxy(self):
        from shared.broker.saxo_adapter import SaxoBrokerAdapter
        adapter = SaxoBrokerAdapter(MagicMock())
        s = adapter.streaming
        assert isinstance(s, StreamingInterface)
        assert adapter.streaming is s
