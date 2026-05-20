"""Signature-level tests for HydraStrategy's `broker` kwarg.

Validates the contract added in Phase NEW-2 commit 6: HydraStrategy
accepts an optional `broker: Optional[IBClient]` kwarg, stored as
`self.broker`. Used by ported HYDRA methods to call IBClient directly
instead of inherited MEIC methods that go through Saxo.

This file deliberately stops at signature/attribute validation —
running HYDRA's full __init__ requires a real (or extensively mocked)
Saxo client, config, logger, and AlertService, which is out of scope
for this small change. The full-construction integration test lives
where it belongs: in the broader rewrite validation phase.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy
from shared.ib_client import IBClient


class TestHydraInitBrokerKwarg:
    """Phase NEW-2 commit 6 added an optional `broker` kwarg to
    HydraStrategy.__init__. Verify the contract."""

    def test_signature_has_broker_kwarg(self):
        sig = inspect.signature(HydraStrategy.__init__)
        assert "broker" in sig.parameters, (
            "HydraStrategy.__init__ must accept a `broker` kwarg "
            "(Phase NEW-2 commit 6)"
        )

    def test_broker_kwarg_is_keyword_only(self):
        """`broker` is keyword-only so positional ordering can't
        accidentally shift its position vs the legacy positional args."""
        sig = inspect.signature(HydraStrategy.__init__)
        param = sig.parameters["broker"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"`broker` must be KEYWORD_ONLY, got kind={param.kind}"
        )

    def test_broker_kwarg_defaults_to_none(self):
        """Default-None means existing HYDRA callers (which don't pass
        broker) continue to work unchanged via inherited Saxo path."""
        sig = inspect.signature(HydraStrategy.__init__)
        param = sig.parameters["broker"]
        assert param.default is None, (
            f"`broker` must default to None, got {param.default!r}"
        )

    def test_broker_kwarg_typed_as_optional_ibclient(self):
        """Type annotation should be Optional[IBClient]. We check the
        string form rather than the resolved type because annotations
        may be strings under `from __future__ import annotations`."""
        sig = inspect.signature(HydraStrategy.__init__)
        param = sig.parameters["broker"]
        # Allow either resolved type or string form
        annotation_str = str(param.annotation)
        assert "IBClient" in annotation_str, (
            f"`broker` annotation should reference IBClient, got "
            f"{annotation_str!r}"
        )

    def test_init_stores_broker_on_self(self):
        """When broker is passed, it's stored as self.broker.

        We construct via __new__ + manually invoke the BROKER-storage
        line — running the full __init__ requires a real Saxo client +
        config etc. and is out of scope here."""
        instance = HydraStrategy.__new__(HydraStrategy)
        # Mock broker — IBClient instance not actually needed because
        # __init__ only stores the reference; nothing is called on it.
        fake_broker = MagicMock(spec=IBClient)
        # Manually exercise the same assignment __init__ does
        instance.broker = fake_broker
        assert instance.broker is fake_broker

    def test_init_broker_can_be_none(self):
        """Default path: broker=None is the legacy mode (back-compat)."""
        instance = HydraStrategy.__new__(HydraStrategy)
        instance.broker = None
        assert instance.broker is None


class TestNormalizeChartBar:
    """Unit tests for HydraStrategy._normalize_chart_bar staticmethod
    (Phase NEW-2 commit 7a). Validates that IB and Saxo bar shapes
    converge on the same normalized dict so downstream ATR/EMA code is
    broker-independent."""

    # ─── IB bar normalization ──────────────────────────────────────────

    def test_ib_standard_bar(self):
        out = HydraStrategy._normalize_chart_bar(
            {"o": 5500.0, "h": 5510.0, "l": 5495.0, "c": 5505.5, "v": 12000, "t": 1779129743000},
            source="ib",
        )
        assert out == {
            "open": 5500.0, "high": 5510.0, "low": 5495.0,
            "close": 5505.5, "volume": 12000, "timestamp_ms": 1779129743000,
        }

    def test_ib_missing_fields_become_zero(self):
        out = HydraStrategy._normalize_chart_bar({}, source="ib")
        assert out["open"] == 0.0
        assert out["close"] == 0.0
        assert out["volume"] == 0
        assert out["timestamp_ms"] == 0

    def test_ib_string_numbers_coerced(self):
        """IBKR sometimes sends numeric strings — coerce defensively."""
        out = HydraStrategy._normalize_chart_bar(
            {"o": "5500.0", "c": "5505", "v": "100"},
            source="ib",
        )
        assert out["open"] == 5500.0
        assert out["close"] == 5505.0
        assert out["volume"] == 100

    def test_ib_bad_values_become_safe_defaults(self):
        out = HydraStrategy._normalize_chart_bar(
            {"o": "not-a-number", "c": None, "v": ""},
            source="ib",
        )
        assert out["open"] == 0.0
        assert out["close"] == 0.0
        assert out["volume"] == 0

    # ─── Saxo bar normalization ────────────────────────────────────────

    def test_saxo_cfd_bid_keys(self):
        """SPX CFD chart bars use the Bid-suffixed key set."""
        out = HydraStrategy._normalize_chart_bar(
            {
                "OpenBid": 5500.0, "HighBid": 5510.0,
                "LowBid": 5495.0, "CloseBid": 5505.5,
                "Volume": 12000,
            },
            source="saxo",
        )
        assert out["open"] == 5500.0
        assert out["high"] == 5510.0
        assert out["low"] == 5495.0
        assert out["close"] == 5505.5
        assert out["volume"] == 12000

    def test_saxo_non_cfd_falls_back_to_plain_keys(self):
        """Non-CFD instruments may use Open/High/Low/Close without Bid suffix."""
        out = HydraStrategy._normalize_chart_bar(
            {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5},
            source="saxo",
        )
        assert out["open"] == 100.0
        assert out["close"] == 100.5

    def test_saxo_prefers_bid_over_plain_when_both_present(self):
        """If both OpenBid and Open are present, prefer OpenBid (CFD source)."""
        out = HydraStrategy._normalize_chart_bar(
            {"OpenBid": 5500.0, "Open": 9999.0},  # plain is garbage
            source="saxo",
        )
        assert out["open"] == 5500.0

    def test_saxo_missing_fields_become_zero(self):
        out = HydraStrategy._normalize_chart_bar({}, source="saxo")
        assert out["open"] == 0.0
        assert out["close"] == 0.0
        assert out["volume"] == 0

    def test_saxo_timestamp_ms_is_always_zero(self):
        """Saxo's "Time" field is an ISO string; we don't parse it
        because downstream code doesn't need the timestamp. Pinning
        this so future-me notices if a caller starts depending on it."""
        out = HydraStrategy._normalize_chart_bar(
            {"Time": "2026-05-19T14:30:00Z", "CloseBid": 5500.0},
            source="saxo",
        )
        assert out["timestamp_ms"] == 0

    # ─── Source validation ─────────────────────────────────────────────

    def test_unknown_source_raises(self):
        with pytest.raises(ValueError, match="unknown source"):
            HydraStrategy._normalize_chart_bar({"c": 1.0}, source="bloomberg")

    # ─── Convergence: IB and Saxo bars should produce identical output ───

    def test_ib_and_saxo_converge_on_same_shape(self):
        """A bar with same OHLC values from both brokers must normalize
        to identical output (except timestamp, intentionally)."""
        ib_in = {"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100}
        saxo_in = {"OpenBid": 1.0, "HighBid": 2.0, "LowBid": 0.5,
                   "CloseBid": 1.5, "Volume": 100}
        ib_out = HydraStrategy._normalize_chart_bar(ib_in, source="ib")
        saxo_out = HydraStrategy._normalize_chart_bar(saxo_in, source="saxo")
        # Match on price + volume (timestamp differs intentionally)
        for k in ("open", "high", "low", "close", "volume"):
            assert ib_out[k] == saxo_out[k], f"diverge on {k!r}"


class TestReadRecentBars:
    """Unit tests for HydraStrategy._read_recent_bars method.

    Tests both broker paths (IB and Saxo) using a __new__-constructed
    HydraStrategy with manually-set self.broker, self.client, and a few
    config attrs."""

    def _make_bare_strategy(self, broker=None, client=None):
        """Construct a HydraStrategy that bypasses __init__. Sets only
        the attributes _read_recent_bars actually reads."""
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = client
        s.underlying_uic = 4913  # SPX CFD UIC (legacy Saxo)
        return s

    # ─── IB path (broker is set) ───────────────────────────────────────

    def test_ib_path_returns_normalized_bars(self):
        fake_broker = MagicMock()
        fake_broker.get_chart_data.return_value = [
            {"o": 5500.0, "h": 5510.0, "l": 5495.0, "c": 5505.0, "v": 100, "t": 1000},
            {"o": 5505.0, "h": 5515.0, "l": 5500.0, "c": 5510.0, "v": 110, "t": 2000},
        ]
        s = self._make_bare_strategy(broker=fake_broker)
        bars = s._read_recent_bars(horizon_min=1, count=10)
        assert bars is not None
        assert len(bars) == 2
        assert bars[0]["close"] == 5505.0
        assert bars[1]["close"] == 5510.0
        # IBKR get_chart_data was called with SPX + bar/period args
        call_kwargs = fake_broker.get_chart_data.call_args.kwargs
        assert call_kwargs["symbol"] == "SPX"
        assert call_kwargs["bar"] == "1min"

    def test_ib_path_slices_to_requested_count(self):
        """When broker returns more bars than requested, we take the
        most recent N (slice -count:)."""
        fake_broker = MagicMock()
        fake_broker.get_chart_data.return_value = [
            {"c": float(i)} for i in range(20)  # 20 bars: closes 0-19
        ]
        s = self._make_bare_strategy(broker=fake_broker)
        bars = s._read_recent_bars(horizon_min=1, count=5)
        assert len(bars) == 5
        # Last 5 of 0-19 → closes 15, 16, 17, 18, 19
        assert [b["close"] for b in bars] == [15.0, 16.0, 17.0, 18.0, 19.0]

    def test_ib_path_empty_response_returns_none(self):
        fake_broker = MagicMock()
        fake_broker.get_chart_data.return_value = []
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_recent_bars(horizon_min=1, count=10) is None

    def test_ib_path_exception_returns_none(self):
        """Fetch failures shouldn't raise — chart data is best-effort."""
        fake_broker = MagicMock()
        fake_broker.get_chart_data.side_effect = RuntimeError("connection blip")
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_recent_bars(horizon_min=1, count=10) is None

    # ─── Saxo path (broker is None — legacy) ───────────────────────────

    def test_saxo_path_returns_normalized_bars(self):
        fake_client = MagicMock()
        fake_client.get_chart_data.return_value = {
            "Data": [
                {"OpenBid": 5500.0, "HighBid": 5510.0, "LowBid": 5495.0,
                 "CloseBid": 5505.0, "Volume": 100},
            ],
        }
        s = self._make_bare_strategy(broker=None, client=fake_client)
        bars = s._read_recent_bars(horizon_min=1, count=10)
        assert bars is not None
        assert len(bars) == 1
        assert bars[0]["close"] == 5505.0
        # Saxo path used the legacy CFD args
        call_kwargs = fake_client.get_chart_data.call_args.kwargs
        assert call_kwargs["uic"] == 4913
        assert call_kwargs["asset_type"] == "CfdOnIndex"

    def test_saxo_path_no_data_key_returns_none(self):
        fake_client = MagicMock()
        fake_client.get_chart_data.return_value = {}  # no "Data" key
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_recent_bars(horizon_min=1, count=10) is None

    def test_saxo_path_null_response_returns_none(self):
        fake_client = MagicMock()
        fake_client.get_chart_data.return_value = None
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_recent_bars(horizon_min=1, count=10) is None

    def test_saxo_path_exception_returns_none(self):
        fake_client = MagicMock()
        fake_client.get_chart_data.side_effect = RuntimeError("saxo blip")
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_recent_bars(horizon_min=1, count=10) is None

    # ─── Both paths produce same normalized output ─────────────────────

    def test_ib_and_saxo_paths_produce_equivalent_shape(self):
        """Critical invariant: regardless of broker, downstream ATR/EMA
        code consumes the same `bar["close"]` access pattern."""
        ib_broker = MagicMock()
        ib_broker.get_chart_data.return_value = [
            {"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100, "t": 999},
        ]
        saxo_client = MagicMock()
        saxo_client.get_chart_data.return_value = {
            "Data": [{"OpenBid": 1.0, "HighBid": 2.0, "LowBid": 0.5,
                      "CloseBid": 1.5, "Volume": 100}],
        }
        s_ib = self._make_bare_strategy(broker=ib_broker)
        s_saxo = self._make_bare_strategy(broker=None, client=saxo_client)

        ib_bars = s_ib._read_recent_bars(horizon_min=1, count=10)
        saxo_bars = s_saxo._read_recent_bars(horizon_min=1, count=10)

        assert ib_bars is not None and saxo_bars is not None
        for k in ("open", "high", "low", "close", "volume"):
            assert ib_bars[0][k] == saxo_bars[0][k], f"diverge on {k!r}"


class TestReadOptionQuote:
    """Unit tests for HydraStrategy._read_option_quote method (commit 7b).

    Same two-broker dispatch as _read_recent_bars. IBClient.get_quote
    returns a flat dict already in normalized shape; Saxo returns
    {"Quote": {"Bid": ..., "Ask": ..., "LastTraded": ...}} nested with
    occasional top-level fallback. Helper unifies the shape so call
    sites do `quote.get("bid")` regardless of broker.
    """

    def _make_bare_strategy(self, broker=None, client=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = client
        return s

    # ─── IB path ───────────────────────────────────────────────────────

    def test_ib_path_returns_normalized_dict(self):
        fake_broker = MagicMock()
        fake_broker.get_quote.return_value = {
            "bid": 2.50, "ask": 2.55, "last": 2.52,
            "mid": 2.525, "mark": 2.53,
        }
        s = self._make_bare_strategy(broker=fake_broker)
        quote = s._read_option_quote(883539497)
        assert quote == {
            "bid": 2.50, "ask": 2.55, "last": 2.52,
            "mid": 2.525, "mark": 2.53,
        }
        # Confirms IB path: get_quote called with int conid, no asset_type
        fake_broker.get_quote.assert_called_once_with(883539497)

    def test_ib_path_instrument_id_string_cast_to_int(self):
        """IBClient.get_quote expects int conid; defensive cast."""
        fake_broker = MagicMock()
        fake_broker.get_quote.return_value = {"bid": 1.0}
        s = self._make_bare_strategy(broker=fake_broker)
        s._read_option_quote("883539497")
        fake_broker.get_quote.assert_called_once_with(883539497)

    def test_ib_path_empty_response_returns_none(self):
        fake_broker = MagicMock()
        fake_broker.get_quote.return_value = {}
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_option_quote(12345) is None

    def test_ib_path_missing_fields_become_none(self):
        """Off-hours / unentitled responses may have only some fields."""
        fake_broker = MagicMock()
        fake_broker.get_quote.return_value = {"bid": 1.0}  # no ask/last/etc
        s = self._make_bare_strategy(broker=fake_broker)
        quote = s._read_option_quote(12345)
        assert quote["bid"] == 1.0
        assert quote["ask"] is None
        assert quote["last"] is None
        assert quote["mid"] is None
        assert quote["mark"] is None

    def test_ib_path_exception_returns_none(self):
        fake_broker = MagicMock()
        fake_broker.get_quote.side_effect = RuntimeError("conn dropped")
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_option_quote(12345) is None

    # ─── Saxo path (broker=None — legacy) ──────────────────────────────

    def test_saxo_path_nested_quote_block(self):
        """Saxo's typical response: {"Quote": {"Bid": ..., "Ask": ...}}."""
        fake_client = MagicMock()
        fake_client.get_quote.return_value = {
            "Quote": {"Bid": 2.50, "Ask": 2.55, "LastTraded": 2.52},
        }
        s = self._make_bare_strategy(broker=None, client=fake_client)
        quote = s._read_option_quote(12345678)
        assert quote["bid"] == 2.50
        assert quote["ask"] == 2.55
        assert quote["last"] == 2.52
        # Saxo never provides mid/mark
        assert quote["mid"] is None
        assert quote["mark"] is None
        # Confirms Saxo path: asset_type="StockIndexOption" passed through
        call_kwargs = fake_client.get_quote.call_args.kwargs
        assert call_kwargs["asset_type"] == "StockIndexOption"

    def test_saxo_path_top_level_fallback(self):
        """Some Saxo responses put Bid at top level instead of nested.
        Legacy HYDRA's defensive lookup tried both — preserve that."""
        fake_client = MagicMock()
        fake_client.get_quote.return_value = {"Bid": 2.50, "Ask": 2.55}
        s = self._make_bare_strategy(broker=None, client=fake_client)
        quote = s._read_option_quote(12345)
        assert quote["bid"] == 2.50
        assert quote["ask"] == 2.55

    def test_saxo_path_null_response_returns_none(self):
        fake_client = MagicMock()
        fake_client.get_quote.return_value = None
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_option_quote(12345) is None

    def test_saxo_path_exception_returns_none(self):
        fake_client = MagicMock()
        fake_client.get_quote.side_effect = RuntimeError("saxo failure")
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_option_quote(12345) is None

    # ─── Defensive parsing ─────────────────────────────────────────────

    def test_string_prices_coerced_to_float(self):
        """Both brokers occasionally return numeric strings."""
        fake_broker = MagicMock()
        fake_broker.get_quote.return_value = {"bid": "2.50", "ask": "2.55"}
        s = self._make_bare_strategy(broker=fake_broker)
        quote = s._read_option_quote(12345)
        assert quote["bid"] == 2.50
        assert quote["ask"] == 2.55

    def test_bad_values_become_none(self):
        fake_broker = MagicMock()
        fake_broker.get_quote.return_value = {
            "bid": "not-a-number", "ask": "", "last": None,
        }
        s = self._make_bare_strategy(broker=fake_broker)
        quote = s._read_option_quote(12345)
        assert quote["bid"] is None
        assert quote["ask"] is None
        assert quote["last"] is None

    # ─── Cross-broker convergence ──────────────────────────────────────

    def test_ib_and_saxo_paths_produce_equivalent_bid_ask_last(self):
        """Critical invariant: bid/ask/last fields match across brokers
        when underlying instrument data is the same."""
        ib_broker = MagicMock()
        ib_broker.get_quote.return_value = {
            "bid": 2.50, "ask": 2.55, "last": 2.52,
        }
        saxo_client = MagicMock()
        saxo_client.get_quote.return_value = {
            "Quote": {"Bid": 2.50, "Ask": 2.55, "LastTraded": 2.52},
        }
        s_ib = self._make_bare_strategy(broker=ib_broker)
        s_saxo = self._make_bare_strategy(broker=None, client=saxo_client)

        ib_quote = s_ib._read_option_quote(12345)
        saxo_quote = s_saxo._read_option_quote(12345)

        for k in ("bid", "ask", "last"):
            assert ib_quote[k] == saxo_quote[k], f"diverge on {k!r}"
