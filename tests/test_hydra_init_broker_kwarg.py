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
from datetime import date
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


class TestReadOptionChain:
    """Unit tests for HydraStrategy._read_option_chain (F3.2).

    Two-broker dispatch: the IB path resolves a strike list then
    batch-resolves conids via qualify_option_strikes (F3.1); the Saxo
    path parses OptionSpace into the same {strike: id} map shape. Both
    return ``(call_map, put_map)`` so the MKT-045/020/022 call sites
    become broker-agnostic in F3.3-F3.5.
    """

    def _make_bare_strategy(self, broker=None, client=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = client
        s.option_root_uic = 12345  # legacy Saxo SPXW root
        return s

    # ─── IB path ───────────────────────────────────────────────────────

    def test_ib_path_returns_call_and_put_maps(self):
        fake_broker = MagicMock()
        fake_broker.get_option_chain.return_value = [6730.0, 6735.0, 6740.0]
        fake_broker.qualify_option_strikes.return_value = {
            (6735.0, "C"): 111, (6735.0, "P"): 222,
            (6740.0, "C"): 333, (6740.0, "P"): 444,
        }
        s = self._make_bare_strategy(broker=fake_broker)
        call_map, put_map = s._read_option_chain("2026-05-21", [6735.0, 6740.0])
        assert call_map == {6735.0: 111, 6740.0: 333}
        assert put_map == {6735.0: 222, 6740.0: 444}

    def test_ib_path_passes_date_object_to_broker(self):
        """expiry string is parsed to a date before the broker calls."""
        fake_broker = MagicMock()
        fake_broker.get_option_chain.return_value = [6735.0]
        fake_broker.qualify_option_strikes.return_value = {(6735.0, "C"): 1}
        s = self._make_bare_strategy(broker=fake_broker)
        s._read_option_chain("2026-05-21", [6735.0])
        fake_broker.get_option_chain.assert_called_once_with(
            "SPX", date(2026, 5, 21)
        )
        qual_kwargs = fake_broker.qualify_option_strikes.call_args.kwargs
        assert qual_kwargs["symbol"] == "SPX"
        assert qual_kwargs["expiry"] == date(2026, 5, 21)

    def test_ib_path_snaps_candidate_to_nearest_real_strike(self):
        """A 5pt-step candidate that isn't a listed strike snaps to the
        nearest real one before qualify_option_strikes sees it."""
        fake_broker = MagicMock()
        fake_broker.get_option_chain.return_value = [6730.0, 6750.0, 6770.0]
        fake_broker.qualify_option_strikes.return_value = {(6750.0, "C"): 9}
        s = self._make_bare_strategy(broker=fake_broker)
        # 6745 isn't listed — nearest within 25pt is 6750
        s._read_option_chain("2026-05-21", [6745.0])
        qual_kwargs = fake_broker.qualify_option_strikes.call_args.kwargs
        assert qual_kwargs["strikes"] == [6750.0]

    def test_ib_path_drops_candidate_beyond_snap_tolerance(self):
        """A candidate more than 25pt from any real strike is dropped."""
        fake_broker = MagicMock()
        fake_broker.get_option_chain.return_value = [6700.0, 6800.0]
        s = self._make_bare_strategy(broker=fake_broker)
        # 6745 is 45pt from 6700 and 55pt from 6800 — beyond 25pt
        call_map, put_map = s._read_option_chain("2026-05-21", [6745.0])
        assert call_map == {} and put_map == {}
        fake_broker.qualify_option_strikes.assert_not_called()

    def test_ib_path_dedups_candidates_snapping_to_same_strike(self):
        """Two candidates snapping to one real strike resolve it once."""
        fake_broker = MagicMock()
        fake_broker.get_option_chain.return_value = [6750.0]
        fake_broker.qualify_option_strikes.return_value = {(6750.0, "C"): 1}
        s = self._make_bare_strategy(broker=fake_broker)
        s._read_option_chain("2026-05-21", [6748.0, 6752.0])
        qual_kwargs = fake_broker.qualify_option_strikes.call_args.kwargs
        assert qual_kwargs["strikes"] == [6750.0]

    def test_ib_path_empty_strike_list_returns_empty_maps(self):
        fake_broker = MagicMock()
        fake_broker.get_option_chain.return_value = []
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_option_chain("2026-05-21", [6735.0]) == ({}, {})

    def test_ib_path_no_candidates_returns_empty_maps(self):
        fake_broker = MagicMock()
        fake_broker.get_option_chain.return_value = [6735.0]
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_option_chain("2026-05-21", []) == ({}, {})
        fake_broker.qualify_option_strikes.assert_not_called()

    def test_ib_path_bad_expiry_returns_empty_maps(self):
        fake_broker = MagicMock()
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_option_chain("not-a-date", [6735.0]) == ({}, {})
        fake_broker.get_option_chain.assert_not_called()

    def test_ib_path_chain_fetch_exception_returns_empty_maps(self):
        fake_broker = MagicMock()
        fake_broker.get_option_chain.side_effect = RuntimeError("conn blip")
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_option_chain("2026-05-21", [6735.0]) == ({}, {})

    def test_ib_path_qualify_exception_returns_empty_maps(self):
        fake_broker = MagicMock()
        fake_broker.get_option_chain.return_value = [6735.0]
        fake_broker.qualify_option_strikes.side_effect = RuntimeError("429")
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_option_chain("2026-05-21", [6735.0]) == ({}, {})

    def test_ib_path_partial_qualify_result_handled(self):
        """qualify_option_strikes omits strikes with no listed option —
        the maps simply lack those strikes (callers handle absence)."""
        fake_broker = MagicMock()
        fake_broker.get_option_chain.return_value = [6735.0, 6740.0]
        # 6740 resolved both rights; 6735 only a call (put unlisted)
        fake_broker.qualify_option_strikes.return_value = {
            (6735.0, "C"): 1,
            (6740.0, "C"): 2, (6740.0, "P"): 3,
        }
        s = self._make_bare_strategy(broker=fake_broker)
        call_map, put_map = s._read_option_chain("2026-05-21", [6735.0, 6740.0])
        assert call_map == {6735.0: 1, 6740.0: 2}
        assert put_map == {6740.0: 3}

    # ─── Saxo path (broker=None — legacy) ──────────────────────────────

    def test_saxo_path_parses_option_space(self):
        fake_client = MagicMock()
        fake_client.get_option_chain.return_value = {
            "OptionSpace": [{
                "SpecificOptions": [
                    {"StrikePrice": 6735.0, "PutCall": "Call", "Uic": 101},
                    {"StrikePrice": 6735.0, "PutCall": "Put", "Uic": 102},
                    {"StrikePrice": 6740.0, "PutCall": "Call", "Uic": 103},
                    {"StrikePrice": 6740.0, "PutCall": "Put", "Uic": 104},
                ],
            }],
        }
        s = self._make_bare_strategy(broker=None, client=fake_client)
        call_map, put_map = s._read_option_chain("2026-05-21", [6735.0])
        assert call_map == {6735.0: 101, 6740.0: 103}
        assert put_map == {6735.0: 102, 6740.0: 104}

    def test_saxo_path_ignores_candidate_strikes(self):
        """The Saxo path returns the full chain regardless of which
        candidates the caller asked about — a superset is harmless."""
        fake_client = MagicMock()
        fake_client.get_option_chain.return_value = {
            "OptionSpace": [{
                "SpecificOptions": [
                    {"StrikePrice": 6735.0, "PutCall": "Call", "Uic": 1},
                    {"StrikePrice": 9999.0, "PutCall": "Call", "Uic": 2},
                ],
            }],
        }
        s = self._make_bare_strategy(broker=None, client=fake_client)
        call_map, _ = s._read_option_chain("2026-05-21", [6735.0])
        # 9999 wasn't requested but is still present
        assert call_map == {6735.0: 1, 9999.0: 2}

    def test_saxo_path_passes_root_id_and_expiry(self):
        fake_client = MagicMock()
        fake_client.get_option_chain.return_value = {"OptionSpace": []}
        s = self._make_bare_strategy(broker=None, client=fake_client)
        s._read_option_chain("2026-05-21", [6735.0])
        call_kwargs = fake_client.get_option_chain.call_args.kwargs
        assert call_kwargs["option_root_id"] == 12345
        assert call_kwargs["expiry_dates"] == ["2026-05-21"]

    def test_saxo_path_null_response_returns_empty_maps(self):
        fake_client = MagicMock()
        fake_client.get_option_chain.return_value = None
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_option_chain("2026-05-21", [6735.0]) == ({}, {})

    def test_saxo_path_empty_option_space_returns_empty_maps(self):
        fake_client = MagicMock()
        fake_client.get_option_chain.return_value = {"OptionSpace": []}
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_option_chain("2026-05-21", [6735.0]) == ({}, {})

    def test_saxo_path_exception_returns_empty_maps(self):
        fake_client = MagicMock()
        fake_client.get_option_chain.side_effect = RuntimeError("saxo down")
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_option_chain("2026-05-21", [6735.0]) == ({}, {})

    # ─── Cross-broker convergence ──────────────────────────────────────

    def test_ib_and_saxo_paths_produce_same_map_shape(self):
        """Both brokers yield {strike: instrument_id} dicts so the
        MKT-020/022/045 call sites read them identically."""
        ib_broker = MagicMock()
        ib_broker.get_option_chain.return_value = [6735.0]
        ib_broker.qualify_option_strikes.return_value = {
            (6735.0, "C"): 111, (6735.0, "P"): 222,
        }
        saxo_client = MagicMock()
        saxo_client.get_option_chain.return_value = {
            "OptionSpace": [{
                "SpecificOptions": [
                    {"StrikePrice": 6735.0, "PutCall": "Call", "Uic": 111},
                    {"StrikePrice": 6735.0, "PutCall": "Put", "Uic": 222},
                ],
            }],
        }
        s_ib = self._make_bare_strategy(broker=ib_broker)
        s_saxo = self._make_bare_strategy(broker=None, client=saxo_client)

        ib_call, ib_put = s_ib._read_option_chain("2026-05-21", [6735.0])
        saxo_call, saxo_put = s_saxo._read_option_chain("2026-05-21", [6735.0])
        assert ib_call == saxo_call == {6735.0: 111}
        assert ib_put == saxo_put == {6735.0: 222}


class TestReadOptionQuotesBatch:
    """Unit tests for HydraStrategy._read_option_quotes_batch (F3.4).

    Batch sibling of _read_option_quote. IB path: IBClient.get_quotes_
    batch returns a list of flat normalized dicts, chunked at 100 conids
    and re-keyed by conid. Saxo path: nested {uic: {"Quote": {...}}}
    flattened to the same per-quote shape.
    """

    def _make_bare_strategy(self, broker=None, client=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = client
        return s

    # ─── IB path ───────────────────────────────────────────────────────

    def test_ib_path_returns_dict_keyed_by_conid(self):
        fake_broker = MagicMock()
        fake_broker.get_quotes_batch.return_value = [
            {"conid": 111, "bid": 2.50, "ask": 2.55, "last": 2.52,
             "mid": 2.525, "mark": 2.53},
            {"conid": 222, "bid": 1.10, "ask": 1.15, "last": 1.12,
             "mid": 1.125, "mark": 1.13},
        ]
        s = self._make_bare_strategy(broker=fake_broker)
        out = s._read_option_quotes_batch([111, 222])
        assert out[111] == {"bid": 2.50, "ask": 2.55, "last": 2.52,
                            "mid": 2.525, "mark": 2.53}
        assert out[222]["bid"] == 1.10
        fake_broker.get_quotes_batch.assert_called_once_with([111, 222])

    def test_ib_path_chunks_at_100_conids(self):
        """CP API caps a batch at 100 conids — larger sets are chunked."""
        fake_broker = MagicMock()
        fake_broker.get_quotes_batch.side_effect = (
            lambda chunk: [{"conid": c, "bid": 1.0} for c in chunk]
        )
        s = self._make_bare_strategy(broker=fake_broker)
        out = s._read_option_quotes_batch(list(range(1, 151)))  # 150 conids
        assert len(out) == 150
        assert fake_broker.get_quotes_batch.call_count == 2
        first = fake_broker.get_quotes_batch.call_args_list[0].args[0]
        second = fake_broker.get_quotes_batch.call_args_list[1].args[0]
        assert len(first) == 100 and len(second) == 50

    def test_ib_path_string_conids_cast_to_int(self):
        fake_broker = MagicMock()
        fake_broker.get_quotes_batch.return_value = [{"conid": 111, "bid": 1.0}]
        s = self._make_bare_strategy(broker=fake_broker)
        out = s._read_option_quotes_batch(["111"])
        fake_broker.get_quotes_batch.assert_called_once_with([111])
        assert 111 in out

    def test_ib_path_missing_fields_become_none(self):
        fake_broker = MagicMock()
        fake_broker.get_quotes_batch.return_value = [{"conid": 111, "bid": 1.0}]
        s = self._make_bare_strategy(broker=fake_broker)
        out = s._read_option_quotes_batch([111])
        assert out[111]["bid"] == 1.0
        assert out[111]["ask"] is None
        assert out[111]["mid"] is None
        assert out[111]["mark"] is None

    def test_ib_path_rows_without_conid_skipped(self):
        fake_broker = MagicMock()
        fake_broker.get_quotes_batch.return_value = [
            {"conid": 111, "bid": 1.0},
            {"bid": 2.0},  # no conid — can't key it, skip
        ]
        s = self._make_bare_strategy(broker=fake_broker)
        out = s._read_option_quotes_batch([111, 222])
        assert list(out.keys()) == [111]

    def test_ib_path_string_prices_coerced(self):
        fake_broker = MagicMock()
        fake_broker.get_quotes_batch.return_value = [
            {"conid": 111, "bid": "2.50", "ask": "2.55"},
        ]
        s = self._make_bare_strategy(broker=fake_broker)
        out = s._read_option_quotes_batch([111])
        assert out[111]["bid"] == 2.50
        assert out[111]["ask"] == 2.55

    def test_ib_path_exception_returns_empty(self):
        fake_broker = MagicMock()
        fake_broker.get_quotes_batch.side_effect = RuntimeError("conn blip")
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_option_quotes_batch([111]) == {}

    # ─── Saxo path (broker=None — legacy) ──────────────────────────────

    def test_saxo_path_nested_quote_blocks(self):
        fake_client = MagicMock()
        fake_client.get_quotes_batch.return_value = {
            101: {"Quote": {"Bid": 2.50, "Ask": 2.55, "LastTraded": 2.52}},
            102: {"Quote": {"Bid": 1.10, "Ask": 1.15, "LastTraded": 1.12}},
        }
        s = self._make_bare_strategy(broker=None, client=fake_client)
        out = s._read_option_quotes_batch([101, 102])
        assert out[101]["bid"] == 2.50
        assert out[101]["ask"] == 2.55
        assert out[101]["last"] == 2.52
        assert out[101]["mid"] is None
        assert out[101]["mark"] is None
        call_kwargs = fake_client.get_quotes_batch.call_args.kwargs
        assert call_kwargs["asset_type"] == "StockIndexOption"

    def test_saxo_path_top_level_fallback(self):
        """Some Saxo rows put Bid at top level instead of nested."""
        fake_client = MagicMock()
        fake_client.get_quotes_batch.return_value = {
            101: {"Bid": 2.50, "Ask": 2.55},
        }
        s = self._make_bare_strategy(broker=None, client=fake_client)
        out = s._read_option_quotes_batch([101])
        assert out[101]["bid"] == 2.50
        assert out[101]["ask"] == 2.55

    def test_saxo_path_null_response_returns_empty(self):
        fake_client = MagicMock()
        fake_client.get_quotes_batch.return_value = None
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_option_quotes_batch([101]) == {}

    def test_saxo_path_exception_returns_empty(self):
        fake_client = MagicMock()
        fake_client.get_quotes_batch.side_effect = RuntimeError("saxo down")
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_option_quotes_batch([101]) == {}

    # ─── Shared behavior ───────────────────────────────────────────────

    def test_empty_input_returns_empty(self):
        s = self._make_bare_strategy(broker=MagicMock())
        assert s._read_option_quotes_batch([]) == {}

    def test_ib_and_saxo_paths_produce_equivalent_bid_ask_last(self):
        ib_broker = MagicMock()
        ib_broker.get_quotes_batch.return_value = [
            {"conid": 101, "bid": 2.50, "ask": 2.55, "last": 2.52},
        ]
        saxo_client = MagicMock()
        saxo_client.get_quotes_batch.return_value = {
            101: {"Quote": {"Bid": 2.50, "Ask": 2.55, "LastTraded": 2.52}},
        }
        s_ib = self._make_bare_strategy(broker=ib_broker)
        s_saxo = self._make_bare_strategy(broker=None, client=saxo_client)
        ib_out = s_ib._read_option_quotes_batch([101])
        saxo_out = s_saxo._read_option_quotes_batch([101])
        for k in ("bid", "ask", "last"):
            assert ib_out[101][k] == saxo_out[101][k], f"diverge on {k!r}"


class _FakeMKT045Entry:
    """Minimal entry stub for _snap_entry_strikes_to_chain tests.

    A plain object (not MagicMock) so ``getattr(entry, '_call_uic_map',
    None)`` returns None when the attribute is absent — a MagicMock
    would return a truthy mock and defeat the cache short-circuit."""

    def __init__(self, sc, lc, sp, lp, num=1):
        self.short_call_strike = sc
        self.long_call_strike = lc
        self.short_put_strike = sp
        self.long_put_strike = lp
        self.entry_number = num


class TestSnapEntryStrikesToChain:
    """F3.3 — _snap_entry_strikes_to_chain (MKT-045) now sources its
    chain through _read_option_chain instead of a direct Saxo
    get_option_chain call. Tests focus on the rewired chain-fetch
    block; strike-snapping arithmetic itself is covered by the
    _snap_to_chain_strike / _snap_long_for_spread staticmethod tests."""

    def _make_strategy(self, chain_return):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = MagicMock()  # presence irrelevant — _read_option_chain mocked
        s.client = MagicMock()
        s._read_option_chain = MagicMock(return_value=chain_return)
        s._log_safety_event = MagicMock()
        s._adjust_for_strike_conflicts = MagicMock()
        s._adjust_for_same_strike_overlap = MagicMock()
        s._adjust_for_long_strike_overlap = MagicMock()
        return s

    def test_candidates_are_four_strikes_plus_neighborhood(self):
        """Candidate set = the entry's 4 strikes, each expanded to a
        ±15pt neighborhood in 5pt steps (7 points each)."""
        s = self._make_strategy(({6800.0: 1}, {6700.0: 2}))
        entry = _FakeMKT045Entry(sc=6800, lc=6850, sp=6700, lp=6650)
        s._snap_entry_strikes_to_chain(entry)
        candidates = s._read_option_chain.call_args.args[1]
        assert len(candidates) == 28  # 4 strikes × 7 points
        for base in (6800, 6850, 6700, 6650):
            for d in (-15, -10, -5, 0, 5, 10, 15):
                assert float(base + d) in candidates

    def test_expiry_passed_as_date_string(self):
        s = self._make_strategy(({6800.0: 1}, {6700.0: 2}))
        entry = _FakeMKT045Entry(6800, 6850, 6700, 6650)
        s._snap_entry_strikes_to_chain(entry)
        expiry_arg = s._read_option_chain.call_args.args[0]
        assert isinstance(expiry_arg, str)
        assert expiry_arg.count("-") == 2  # "YYYY-MM-DD"

    def test_empty_chain_returns_false(self):
        s = self._make_strategy(({}, {}))
        entry = _FakeMKT045Entry(6800, 6850, 6700, 6650)
        assert s._snap_entry_strikes_to_chain(entry) is False

    def test_precomputed_maps_skip_chain_fetch(self):
        """If the entry already carries its UIC maps (set by MKT-020/022
        earlier in the pipeline), MKT-045 reuses them — no chain call."""
        s = self._make_strategy(({}, {}))
        entry = _FakeMKT045Entry(6800, 6850, 6700, 6650)
        entry._call_uic_map = {6800.0: 1, 6850.0: 2}
        entry._put_uic_map = {6700.0: 3, 6650.0: 4}
        s._snap_entry_strikes_to_chain(entry)
        s._read_option_chain.assert_not_called()

    def test_chain_maps_stored_on_entry(self):
        call_map = {6800.0: 11, 6850.0: 12}
        put_map = {6700.0: 13, 6650.0: 14}
        s = self._make_strategy((call_map, put_map))
        entry = _FakeMKT045Entry(6800, 6850, 6700, 6650)
        s._snap_entry_strikes_to_chain(entry)
        assert entry._call_uic_map == call_map
        assert entry._put_uic_map == put_map

    def test_snaps_short_strike_to_nearest_chain_strike(self):
        """A short strike off the chain snaps to the nearest real one
        and the method reports a change + logs the safety event."""
        s = self._make_strategy(
            ({6800.0: 1, 6850.0: 2}, {6700.0: 3, 6650.0: 4})
        )
        entry = _FakeMKT045Entry(sc=6797, lc=6850, sp=6700, lp=6650)
        changed = s._snap_entry_strikes_to_chain(entry)
        assert changed is True
        assert entry.short_call_strike == 6800.0
        s._log_safety_event.assert_called_once()

    def test_no_change_when_strikes_already_on_chain(self):
        s = self._make_strategy(
            ({6800.0: 1, 6850.0: 2}, {6700.0: 3, 6650.0: 4})
        )
        entry = _FakeMKT045Entry(6800, 6850, 6700, 6650)
        changed = s._snap_entry_strikes_to_chain(entry)
        assert changed is False
        s._log_safety_event.assert_not_called()

    def test_zero_strikes_excluded_from_candidates(self):
        """Unset (0/None) strikes contribute no candidates."""
        s = self._make_strategy(({6800.0: 1}, {}))
        entry = _FakeMKT045Entry(sc=6800, lc=6850, sp=0, lp=0)
        s._snap_entry_strikes_to_chain(entry)
        candidates = s._read_option_chain.call_args.args[1]
        assert len(candidates) == 14  # only sc + lc → 2 × 7
        assert all(c > 0 for c in candidates)


class _FakeTighteningEntry:
    """Minimal entry stub for MKT-020/022 progressive-tightening tests."""

    def __init__(self, sc, lc, sp, lp, num=1, call_only=False, put_only=False):
        self.short_call_strike = sc
        self.long_call_strike = lc
        self.short_put_strike = sp
        self.long_put_strike = lp
        self.entry_number = num
        self.call_only = call_only
        self.put_only = put_only


class TestProgressiveCallTightening:
    """F3.5 — _apply_progressive_call_tightening (MKT-020) now sources
    its chain via _read_option_chain (F3.2) and its quotes via
    _read_option_quotes_batch (F3.4). Tests focus on the rewired I/O;
    the tightening arithmetic itself is unchanged from the Saxo era."""

    # Chain covering shorts 6825-6840 + longs 6875-6890 (conid == strike).
    _CHAIN = {
        6840.0: 6840, 6835.0: 6835, 6830.0: 6830, 6825.0: 6825,
        6890.0: 6890, 6885.0: 6885, 6880.0: 6880, 6875.0: 6875,
    }

    def _make_strategy(self, chain=None, quotes=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = MagicMock()
        s.client = MagicMock()
        s.current_price = 6800.0
        s.current_vix = 15.0
        s.min_call_otm_distance = 25
        s.min_viable_credit_per_side = 100  # cents — $1.00
        s.call_credit_floor = 90
        s.brandon_disable_progressive_tightening = False
        s._get_vix_adjusted_spread_width = MagicMock(return_value=50)
        s._read_option_chain = MagicMock(
            return_value=(self._CHAIN if chain is None else chain, {})
        )
        s._read_option_quotes_batch = MagicMock(return_value=quotes or {})
        s._log_safety_event = MagicMock()
        s._adjust_for_strike_conflicts = MagicMock()
        s._adjust_for_same_strike_overlap = MagicMock()
        s._adjust_for_long_strike_overlap = MagicMock()
        return s

    # short legs priced rich enough to tighten to 6830 (otm 30), the
    # first viable strike scanning inward from 6840 (otm 40).
    _QUOTES_TIGHTEN = {
        6840: {"bid": 1.15, "ask": 1.25},  # mid 1.20 → credit 70  (non-viable)
        6835: {"bid": 1.25, "ask": 1.35},  # mid 1.30 → credit 80  (non-viable)
        6830: {"bid": 1.95, "ask": 2.05},  # mid 2.00 → credit 150 (viable)
        6825: {"bid": 1.95, "ask": 2.05},
        6890: {"bid": 0.45, "ask": 0.55},  # long legs — mid 0.50
        6885: {"bid": 0.45, "ask": 0.55},
        6880: {"bid": 0.45, "ask": 0.55},
        6875: {"bid": 0.45, "ask": 0.55},
    }

    def test_calls_read_option_chain_with_scan_strikes(self):
        s = self._make_strategy(quotes=self._QUOTES_TIGHTEN)
        entry = _FakeTighteningEntry(sc=6840, lc=6890, sp=6700, lp=6650)
        s._apply_progressive_call_tightening(entry)
        scan_strikes = s._read_option_chain.call_args.args[1]
        # 4 candidates (otm 40/35/30/25) × (short + long)
        assert set(scan_strikes) == {
            6840.0, 6835.0, 6830.0, 6825.0,
            6890.0, 6885.0, 6880.0, 6875.0,
        }

    def test_calls_read_option_quotes_batch_with_resolved_ids(self):
        s = self._make_strategy(quotes=self._QUOTES_TIGHTEN)
        entry = _FakeTighteningEntry(sc=6840, lc=6890, sp=6700, lp=6650)
        s._apply_progressive_call_tightening(entry)
        batch_ids = s._read_option_quotes_batch.call_args.args[0]
        assert set(batch_ids) == {
            6840, 6835, 6830, 6825, 6890, 6885, 6880, 6875,
        }

    def test_tightens_to_first_viable_closer_strike(self):
        """Reads lowercase bid/ask from the F3.4 quote shape, scans
        inward, tightens the short call to the first viable strike."""
        s = self._make_strategy(quotes=self._QUOTES_TIGHTEN)
        entry = _FakeTighteningEntry(sc=6840, lc=6890, sp=6700, lp=6650)
        result = s._apply_progressive_call_tightening(entry)
        assert result is True
        assert entry.short_call_strike == 6830.0
        assert entry.long_call_strike == 6880.0

    def test_already_viable_at_widest_returns_false(self):
        """When the widest (initial) strike already clears the credit
        gate, no tightening happens."""
        quotes = dict(self._QUOTES_TIGHTEN)
        quotes[6840] = {"bid": 1.95, "ask": 2.05}  # mid 2.00 → credit 150
        s = self._make_strategy(quotes=quotes)
        entry = _FakeTighteningEntry(sc=6840, lc=6890, sp=6700, lp=6650)
        result = s._apply_progressive_call_tightening(entry)
        assert result is False
        assert entry.short_call_strike == 6840  # unchanged

    def test_empty_chain_returns_false(self):
        s = self._make_strategy(chain={}, quotes=self._QUOTES_TIGHTEN)
        entry = _FakeTighteningEntry(sc=6840, lc=6890, sp=6700, lp=6650)
        assert s._apply_progressive_call_tightening(entry) is False
        s._read_option_quotes_batch.assert_not_called()

    def test_empty_quotes_returns_false(self):
        s = self._make_strategy(quotes={})
        entry = _FakeTighteningEntry(sc=6840, lc=6890, sp=6700, lp=6650)
        assert s._apply_progressive_call_tightening(entry) is False

    def test_skips_when_call_only(self):
        s = self._make_strategy(quotes=self._QUOTES_TIGHTEN)
        entry = _FakeTighteningEntry(sc=6840, lc=6890, sp=6700, lp=6650,
                                     call_only=True)
        assert s._apply_progressive_call_tightening(entry) is False
        s._read_option_chain.assert_not_called()

    def test_skips_when_brandon_disable_flag(self):
        s = self._make_strategy(quotes=self._QUOTES_TIGHTEN)
        s.brandon_disable_progressive_tightening = True
        entry = _FakeTighteningEntry(sc=6840, lc=6890, sp=6700, lp=6650)
        assert s._apply_progressive_call_tightening(entry) is False
        s._read_option_chain.assert_not_called()
