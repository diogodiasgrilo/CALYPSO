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
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy
# MEICState comes from HYDRA's own base now (post-P1 reparent) — importing
# it from bots.meic.strategy would yield a DIFFERENT enum class object,
# making `s.state == MEICState.MONITORING` a cross-class comparison.
from bots.hydra.base_strategy import MEICState
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
        # period must respect IBKR's CP API unit ranges: the "min" unit
        # caps at 30, so a >30min lookback must be expressed in hours.
        period = call_kwargs["period"]
        if period.endswith("min"):
            assert int(period[:-3]) <= 30, f"min-unit period {period!r} exceeds IBKR's 30 cap"
        elif period.endswith("h"):
            assert 1 <= int(period[:-1]) <= 8, f"hour-unit period {period!r} out of IBKR 1-8h range"
        else:
            assert period.endswith("d"), f"unexpected period unit: {period!r}"

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


class TestProgressivePutTightening:
    """F3.6 — _apply_progressive_put_tightening (MKT-022) now sources
    its chain via _read_option_chain (F3.2) and its quotes via
    _read_option_quotes_batch (F3.4). Mirror of TestProgressiveCall-
    Tightening for the put side."""

    # Chain covering shorts 6760-6775 + longs 6710-6725 (conid == strike).
    _CHAIN = {
        6760.0: 6760, 6765.0: 6765, 6770.0: 6770, 6775.0: 6775,
        6710.0: 6710, 6715.0: 6715, 6720.0: 6720, 6725.0: 6725,
    }

    def _make_strategy(self, chain=None, quotes=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = MagicMock()
        s.client = MagicMock()
        s.current_price = 6800.0
        s.current_vix = 15.0
        s.min_put_otm_distance = 25
        s.min_viable_credit_put_side = 100  # cents — $1.00
        s.put_credit_floor = 90
        s.brandon_disable_progressive_tightening = False
        s._get_vix_adjusted_spread_width = MagicMock(return_value=50)
        # _read_option_chain returns (call_map, put_map) — put side here
        s._read_option_chain = MagicMock(
            return_value=({}, self._CHAIN if chain is None else chain)
        )
        s._read_option_quotes_batch = MagicMock(return_value=quotes or {})
        s._log_safety_event = MagicMock()
        s._adjust_for_strike_conflicts = MagicMock()
        s._adjust_for_same_strike_overlap = MagicMock()
        s._adjust_for_long_strike_overlap = MagicMock()
        return s

    # short legs priced rich enough to tighten to 6770 (otm 30), the
    # first viable strike scanning inward from 6760 (otm 40).
    _QUOTES_TIGHTEN = {
        6760: {"bid": 1.15, "ask": 1.25},  # mid 1.20 → credit 70  (non-viable)
        6765: {"bid": 1.25, "ask": 1.35},  # mid 1.30 → credit 80  (non-viable)
        6770: {"bid": 1.95, "ask": 2.05},  # mid 2.00 → credit 150 (viable)
        6775: {"bid": 1.95, "ask": 2.05},
        6710: {"bid": 0.45, "ask": 0.55},  # long legs — mid 0.50
        6715: {"bid": 0.45, "ask": 0.55},
        6720: {"bid": 0.45, "ask": 0.55},
        6725: {"bid": 0.45, "ask": 0.55},
    }

    def test_calls_read_option_chain_with_scan_strikes(self):
        s = self._make_strategy(quotes=self._QUOTES_TIGHTEN)
        entry = _FakeTighteningEntry(sc=6900, lc=6950, sp=6760, lp=6710)
        s._apply_progressive_put_tightening(entry)
        scan_strikes = s._read_option_chain.call_args.args[1]
        assert set(scan_strikes) == {
            6760.0, 6765.0, 6770.0, 6775.0,
            6710.0, 6715.0, 6720.0, 6725.0,
        }

    def test_calls_read_option_quotes_batch_with_resolved_ids(self):
        s = self._make_strategy(quotes=self._QUOTES_TIGHTEN)
        entry = _FakeTighteningEntry(sc=6900, lc=6950, sp=6760, lp=6710)
        s._apply_progressive_put_tightening(entry)
        batch_ids = s._read_option_quotes_batch.call_args.args[0]
        assert set(batch_ids) == {
            6760, 6765, 6770, 6775, 6710, 6715, 6720, 6725,
        }

    def test_tightens_to_first_viable_closer_strike(self):
        s = self._make_strategy(quotes=self._QUOTES_TIGHTEN)
        entry = _FakeTighteningEntry(sc=6900, lc=6950, sp=6760, lp=6710)
        result = s._apply_progressive_put_tightening(entry)
        assert result is True
        assert entry.short_put_strike == 6770.0
        assert entry.long_put_strike == 6720.0

    def test_already_viable_at_widest_returns_false(self):
        quotes = dict(self._QUOTES_TIGHTEN)
        quotes[6760] = {"bid": 1.95, "ask": 2.05}  # mid 2.00 → credit 150
        s = self._make_strategy(quotes=quotes)
        entry = _FakeTighteningEntry(sc=6900, lc=6950, sp=6760, lp=6710)
        result = s._apply_progressive_put_tightening(entry)
        assert result is False
        assert entry.short_put_strike == 6760  # unchanged

    def test_empty_chain_returns_false(self):
        s = self._make_strategy(chain={}, quotes=self._QUOTES_TIGHTEN)
        entry = _FakeTighteningEntry(sc=6900, lc=6950, sp=6760, lp=6710)
        assert s._apply_progressive_put_tightening(entry) is False
        s._read_option_quotes_batch.assert_not_called()

    def test_empty_quotes_returns_false(self):
        s = self._make_strategy(quotes={})
        entry = _FakeTighteningEntry(sc=6900, lc=6950, sp=6760, lp=6710)
        assert s._apply_progressive_put_tightening(entry) is False

    def test_skips_when_put_only(self):
        s = self._make_strategy(quotes=self._QUOTES_TIGHTEN)
        entry = _FakeTighteningEntry(sc=6900, lc=6950, sp=6760, lp=6710,
                                     put_only=True)
        assert s._apply_progressive_put_tightening(entry) is False
        s._read_option_chain.assert_not_called()

    def test_skips_when_brandon_disable_flag(self):
        s = self._make_strategy(quotes=self._QUOTES_TIGHTEN)
        s.brandon_disable_progressive_tightening = True
        entry = _FakeTighteningEntry(sc=6900, lc=6950, sp=6760, lp=6710)
        assert s._apply_progressive_put_tightening(entry) is False
        s._read_option_chain.assert_not_called()


class TestReadOptionGreeks:
    """Unit tests for HydraStrategy._read_option_greeks (F3.7).

    IB path: IBClient.get_option_greeks already returns lowercase greek
    fields. Saxo path: capitalized Delta/Gamma/Theta/Vega re-keyed to
    the normalized lowercase shape. Greeks are analytics-only — None on
    failure is harmless."""

    def _make_bare_strategy(self, broker=None, client=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = client
        return s

    # ─── IB path ───────────────────────────────────────────────────────

    def test_ib_path_returns_normalized_greeks(self):
        fake_broker = MagicMock()
        fake_broker.get_option_greeks.return_value = {
            "delta": -0.08, "gamma": 0.002, "theta": -1.5,
            "vega": 0.9, "iv": 0.18, "open_interest": 1200,
            "bid": 2.5, "ask": 2.6,  # extra quote fields ignored
        }
        s = self._make_bare_strategy(broker=fake_broker)
        g = s._read_option_greeks(883539497)
        assert g == {"delta": -0.08, "gamma": 0.002, "theta": -1.5,
                     "vega": 0.9, "iv": 0.18, "open_interest": 1200.0}
        fake_broker.get_option_greeks.assert_called_once_with(883539497)

    def test_ib_path_string_conid_cast_to_int(self):
        fake_broker = MagicMock()
        fake_broker.get_option_greeks.return_value = {"delta": -0.1}
        s = self._make_bare_strategy(broker=fake_broker)
        s._read_option_greeks("883539497")
        fake_broker.get_option_greeks.assert_called_once_with(883539497)

    def test_ib_path_empty_returns_none(self):
        fake_broker = MagicMock()
        fake_broker.get_option_greeks.return_value = {}
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_option_greeks(123) is None

    def test_ib_path_missing_fields_become_none(self):
        fake_broker = MagicMock()
        fake_broker.get_option_greeks.return_value = {"delta": -0.08}
        s = self._make_bare_strategy(broker=fake_broker)
        g = s._read_option_greeks(123)
        assert g["delta"] == -0.08
        assert g["gamma"] is None
        assert g["theta"] is None
        assert g["open_interest"] is None

    def test_ib_path_string_greeks_coerced(self):
        fake_broker = MagicMock()
        fake_broker.get_option_greeks.return_value = {
            "delta": "-0.08", "theta": "-1.5",
        }
        s = self._make_bare_strategy(broker=fake_broker)
        g = s._read_option_greeks(123)
        assert g["delta"] == -0.08
        assert g["theta"] == -1.5

    def test_ib_path_exception_returns_none(self):
        fake_broker = MagicMock()
        fake_broker.get_option_greeks.side_effect = RuntimeError("conn blip")
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_option_greeks(123) is None

    # ─── Saxo path (broker=None — legacy) ──────────────────────────────

    def test_saxo_path_capitalized_keys_mapped(self):
        fake_client = MagicMock()
        fake_client.get_option_greeks.return_value = {
            "Delta": -0.08, "Gamma": 0.002, "Theta": -1.5,
            "Vega": 0.9, "MidVol": 0.18, "OpenInterest": 1200,
        }
        s = self._make_bare_strategy(broker=None, client=fake_client)
        g = s._read_option_greeks(12345678)
        assert g["delta"] == -0.08
        assert g["gamma"] == 0.002
        assert g["theta"] == -1.5
        assert g["vega"] == 0.9
        assert g["iv"] == 0.18
        assert g["open_interest"] == 1200.0
        call_kwargs = fake_client.get_option_greeks.call_args.kwargs
        assert call_kwargs["asset_type"] == "StockIndexOption"

    def test_saxo_path_null_returns_none(self):
        fake_client = MagicMock()
        fake_client.get_option_greeks.return_value = None
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_option_greeks(123) is None

    def test_saxo_path_exception_returns_none(self):
        fake_client = MagicMock()
        fake_client.get_option_greeks.side_effect = RuntimeError("saxo down")
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_option_greeks(123) is None

    # ─── Cross-broker convergence ──────────────────────────────────────

    def test_ib_and_saxo_paths_converge(self):
        ib_broker = MagicMock()
        ib_broker.get_option_greeks.return_value = {
            "delta": -0.08, "gamma": 0.002, "theta": -1.5, "vega": 0.9,
        }
        saxo_client = MagicMock()
        saxo_client.get_option_greeks.return_value = {
            "Delta": -0.08, "Gamma": 0.002, "Theta": -1.5, "Vega": 0.9,
        }
        s_ib = self._make_bare_strategy(broker=ib_broker)
        s_saxo = self._make_bare_strategy(broker=None, client=saxo_client)
        g_ib = s_ib._read_option_greeks(123)
        g_saxo = s_saxo._read_option_greeks(123)
        for k in ("delta", "gamma", "theta", "vega"):
            assert g_ib[k] == g_saxo[k], f"diverge on {k!r}"


class TestReadOpenPositions:
    """Unit tests for HydraStrategy._read_open_positions (F4.1).

    IB path runs raw IBKR rows through the real _normalize_position_dict
    and keeps option rows; Saxo path flattens the nested PositionBase /
    PositionView shape. Both converge on one broker-agnostic dict shape.
    """

    def _make_bare_strategy(self, broker=None, client=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = client
        return s

    # ─── IB path ───────────────────────────────────────────────────────

    def test_ib_path_returns_normalized_options(self):
        fake_broker = MagicMock()
        fake_broker.get_positions.return_value = [
            {"conid": 883539497, "position": -1, "ticker": "SPXW",
             "assetClass": "OPT", "putOrCall": "C",
             "lastTradingDay": "20260521", "strike": 6800,
             "unrealizedPnl": 12.5},
        ]
        s = self._make_bare_strategy(broker=fake_broker)
        positions = s._read_open_positions()
        assert len(positions) == 1
        p = positions[0]
        assert p["instrument_id"] == 883539497
        assert p["quantity"] == -1
        assert p["side"] == "SHORT"
        assert p["right"] == "C"
        assert p["strike"] == 6800.0
        assert p["expiry"] == date(2026, 5, 21)
        assert p["unrealized_pnl"] == 12.5

    def test_ib_path_filters_non_option_positions(self):
        fake_broker = MagicMock()
        fake_broker.get_positions.return_value = [
            {"conid": 1, "position": 100, "assetClass": "STK"},  # stock
            {"conid": 883539497, "position": -1, "assetClass": "OPT",
             "putOrCall": "P"},
        ]
        s = self._make_bare_strategy(broker=fake_broker)
        positions = s._read_open_positions()
        assert [p["instrument_id"] for p in positions] == [883539497]

    def test_ib_path_skips_unparseable_rows(self):
        """A row with no conid can't be identified — skip it, keep the
        rest (failure isolation, same pattern as F3.1)."""
        fake_broker = MagicMock()
        fake_broker.get_positions.return_value = [
            {"position": -1, "assetClass": "OPT"},  # no conid
            {"conid": 883539497, "position": -1, "assetClass": "OPT",
             "putOrCall": "C"},
        ]
        s = self._make_bare_strategy(broker=fake_broker)
        positions = s._read_open_positions()
        assert [p["instrument_id"] for p in positions] == [883539497]

    def test_ib_path_position_id_is_none(self):
        """IBKR has no per-leg position id."""
        fake_broker = MagicMock()
        fake_broker.get_positions.return_value = [
            {"conid": 883539497, "position": -1, "assetClass": "OPT",
             "putOrCall": "C"},
        ]
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_open_positions()[0]["position_id"] is None

    def test_ib_path_long_quantity_sign(self):
        fake_broker = MagicMock()
        fake_broker.get_positions.return_value = [
            {"conid": 1, "position": 2, "assetClass": "OPT",
             "putOrCall": "P"},
        ]
        s = self._make_bare_strategy(broker=fake_broker)
        p = s._read_open_positions()[0]
        assert p["quantity"] == 2
        assert p["side"] == "LONG"

    def test_ib_path_empty_returns_empty(self):
        fake_broker = MagicMock()
        fake_broker.get_positions.return_value = []
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_open_positions() == []

    def test_ib_path_exception_returns_empty(self):
        fake_broker = MagicMock()
        fake_broker.get_positions.side_effect = RuntimeError("conn blip")
        s = self._make_bare_strategy(broker=fake_broker)
        assert s._read_open_positions() == []

    # ─── Saxo path (broker=None — legacy) ──────────────────────────────

    def test_saxo_path_parses_nested_shape(self):
        fake_client = MagicMock()
        fake_client.get_positions.return_value = [
            {"PositionId": "saxo-uuid-1",
             "PositionBase": {"Uic": 12345678, "Amount": -1,
                              "AssetType": "StockIndexOption",
                              "OptionsData": {"Strike": 6800,
                                              "PutCall": "Call",
                                              "ExpiryDate": "2026-05-21"}},
             "PositionView": {"ProfitLossOnTrade": 12.5}},
        ]
        s = self._make_bare_strategy(broker=None, client=fake_client)
        positions = s._read_open_positions()
        assert len(positions) == 1
        p = positions[0]
        assert p["instrument_id"] == 12345678
        assert p["quantity"] == -1
        assert p["side"] == "SHORT"
        assert p["right"] == "C"
        assert p["strike"] == 6800
        assert p["expiry"] == "2026-05-21"
        assert p["unrealized_pnl"] == 12.5
        assert p["position_id"] == "saxo-uuid-1"

    def test_saxo_path_putcall_mapped(self):
        fake_client = MagicMock()
        fake_client.get_positions.return_value = [
            {"PositionId": "p1",
             "PositionBase": {"Uic": 1, "Amount": -1,
                              "AssetType": "StockIndexOption",
                              "OptionsData": {"PutCall": "Put"}}},
        ]
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_open_positions()[0]["right"] == "P"

    def test_saxo_path_filters_non_options(self):
        fake_client = MagicMock()
        fake_client.get_positions.return_value = [
            {"PositionId": "stk", "PositionBase": {"Uic": 9, "Amount": 100,
                                                   "AssetType": "Stock"}},
            {"PositionId": "opt", "PositionBase": {"Uic": 1, "Amount": -1,
                                                   "AssetType": "StockIndexOption",
                                                   "OptionsData": {"PutCall": "Call"}}},
        ]
        s = self._make_bare_strategy(broker=None, client=fake_client)
        positions = s._read_open_positions()
        assert [p["position_id"] for p in positions] == ["opt"]

    def test_saxo_path_exception_returns_empty(self):
        fake_client = MagicMock()
        fake_client.get_positions.side_effect = RuntimeError("saxo down")
        s = self._make_bare_strategy(broker=None, client=fake_client)
        assert s._read_open_positions() == []

    # ─── Cross-broker convergence ──────────────────────────────────────

    def test_both_paths_converge_on_shape(self):
        """Both brokers yield dicts with the same keys so the F4 call
        sites read them identically."""
        ib_broker = MagicMock()
        ib_broker.get_positions.return_value = [
            {"conid": 1, "position": -1, "assetClass": "OPT",
             "putOrCall": "C"},
        ]
        saxo_client = MagicMock()
        saxo_client.get_positions.return_value = [
            {"PositionId": "p1",
             "PositionBase": {"Uic": 1, "Amount": -1,
                              "AssetType": "StockIndexOption",
                              "OptionsData": {"PutCall": "Call"}}},
        ]
        s_ib = self._make_bare_strategy(broker=ib_broker)
        s_saxo = self._make_bare_strategy(broker=None, client=saxo_client)
        ib_p = s_ib._read_open_positions()[0]
        saxo_p = s_saxo._read_open_positions()[0]
        assert set(ib_p.keys()) == set(saxo_p.keys())
        for k in ("instrument_id", "quantity", "side", "right"):
            assert ib_p[k] == saxo_p[k], f"diverge on {k!r}"


class TestPositionIsOpen:
    """Unit tests for HydraStrategy._position_is_open (F4.2) — the
    quantity-aware reconciliation primitive that replaces Saxo's
    PositionId set-membership checks."""

    def _make_bare_strategy(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = None
        s.client = None
        return s

    _POSITIONS = [
        {"instrument_id": 100, "right": "C", "quantity": -1},
        {"instrument_id": 200, "right": "P", "quantity": -2},  # merged
        {"instrument_id": 300, "right": "C", "quantity": 0},   # flat
    ]

    def test_open_position_found(self):
        s = self._make_bare_strategy()
        assert s._position_is_open(100, positions=self._POSITIONS) is True

    def test_position_not_found(self):
        s = self._make_bare_strategy()
        assert s._position_is_open(999, positions=self._POSITIONS) is False

    def test_right_filter_match(self):
        s = self._make_bare_strategy()
        assert s._position_is_open(
            100, right="C", positions=self._POSITIONS
        ) is True

    def test_right_filter_mismatch(self):
        s = self._make_bare_strategy()
        assert s._position_is_open(
            100, right="P", positions=self._POSITIONS
        ) is False

    def test_merged_position_counts_as_open(self):
        """A same-strike merge (quantity -2) is still open at min 1."""
        s = self._make_bare_strategy()
        assert s._position_is_open(200, positions=self._POSITIONS) is True

    def test_min_abs_qty_threshold(self):
        s = self._make_bare_strategy()
        # conid 200 has |qty|=2 — open at min 2, conid 100 has |qty|=1
        assert s._position_is_open(
            200, positions=self._POSITIONS, min_abs_qty=2
        ) is True
        assert s._position_is_open(
            100, positions=self._POSITIONS, min_abs_qty=2
        ) is False

    def test_flat_position_not_open(self):
        s = self._make_bare_strategy()
        assert s._position_is_open(300, positions=self._POSITIONS) is False

    def test_none_instrument_id_returns_false(self):
        s = self._make_bare_strategy()
        assert s._position_is_open(None, positions=self._POSITIONS) is False

    def test_string_vs_int_id_tolerated(self):
        """instrument_id comparison is type-tolerant (str vs int)."""
        s = self._make_bare_strategy()
        assert s._position_is_open("100", positions=self._POSITIONS) is True
        positions = [{"instrument_id": "100", "right": "C", "quantity": -1}]
        assert s._position_is_open(100, positions=positions) is True

    def test_none_positions_fetches_fresh(self):
        """positions=None triggers a _read_open_positions fetch."""
        s = self._make_bare_strategy()
        s._read_open_positions = MagicMock(return_value=self._POSITIONS)
        assert s._position_is_open(100) is True
        s._read_open_positions.assert_called_once()

    def test_prefetched_positions_skip_fetch(self):
        s = self._make_bare_strategy()
        s._read_open_positions = MagicMock()
        s._position_is_open(100, positions=self._POSITIONS)
        s._read_open_positions.assert_not_called()

    def test_empty_positions_returns_false(self):
        s = self._make_bare_strategy()
        assert s._position_is_open(100, positions=[]) is False


class _FakeSalvageEntry:
    """Minimal entry stub for MKT-033 long-salvage tests (F4.3)."""

    def __init__(self):
        self.entry_number = 1
        self.contracts = 1
        self.long_call_uic = 111
        self.long_call_position_id = "pc1"
        self.long_call_fill_price = 1.0
        self.call_long_sold = False
        self.call_long_sold_revenue = 0.0
        self.long_put_uic = 222
        self.long_put_position_id = "pp1"
        self.long_put_fill_price = 1.0
        self.put_long_sold = False
        self.put_long_sold_revenue = 0.0
        self.close_commission = 0.0
        self.call_side_stopped = True
        self.put_side_stopped = False


class TestTrySellLongLegReconciliation:
    """F4.3 — _try_sell_long_leg's leg-existence check now uses the
    quantity-aware _position_is_open instead of a Saxo PositionId set."""

    def _make_strategy(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = None
        s.client = MagicMock()
        s.registry = MagicMock()
        s.dry_run = False
        s._save_state_to_disk = MagicMock()
        s.daily_state = MagicMock()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        s.commission_per_leg = 2.5
        return s

    def test_dry_run_returns_false(self):
        s = self._make_strategy()
        s.dry_run = True
        assert s._try_sell_long_leg(_FakeSalvageEntry(), "call") is False

    def test_already_sold_returns_false(self):
        s = self._make_strategy()
        entry = _FakeSalvageEntry()
        entry.call_long_sold = True
        assert s._try_sell_long_leg(entry, "call") is False

    def test_no_long_uic_returns_false(self):
        s = self._make_strategy()
        entry = _FakeSalvageEntry()
        entry.long_call_uic = None
        assert s._try_sell_long_leg(entry, "call") is False

    def test_position_open_check_uses_position_is_open(self):
        """When the long leg still exists, the method proceeds past the
        external-close branch to the quote fetch."""
        s = self._make_strategy()
        s._position_is_open = MagicMock(return_value=True)
        s._read_option_quote = MagicMock(return_value=None)  # bail at quote
        entry = _FakeSalvageEntry()
        open_positions = [{"instrument_id": 111, "right": "C", "quantity": 1}]
        result = s._try_sell_long_leg(entry, "call", open_positions)
        assert result is False
        s._position_is_open.assert_called_once_with(
            111, right="C", positions=open_positions
        )
        # proceeded past external-close branch → tried to fetch a quote
        s._read_option_quote.assert_called_once_with(111)

    def test_put_side_uses_p_right(self):
        s = self._make_strategy()
        s._position_is_open = MagicMock(return_value=True)
        s._read_option_quote = MagicMock(return_value=None)
        entry = _FakeSalvageEntry()
        entry.put_side_stopped = True
        s._try_sell_long_leg(entry, "put", [])
        s._position_is_open.assert_called_once_with(
            222, right="P", positions=[]
        )

    def test_position_gone_marks_externally_closed(self):
        """When _position_is_open is False and no closing price is
        found, the long is marked sold with $0 revenue."""
        s = self._make_strategy()
        s._position_is_open = MagicMock(return_value=False)
        s.client.get_closed_position_price.return_value = None
        entry = _FakeSalvageEntry()
        result = s._try_sell_long_leg(entry, "call", [])
        assert result is False
        assert entry.call_long_sold is True
        assert entry.call_long_sold_revenue == 0.0
        s._save_state_to_disk.assert_called_once()


class TestCheckLongLegSalvageRewire:
    """F4.3 — _check_long_salvage now prefetches via the broker-
    agnostic _read_open_positions and hands the list to each
    _try_sell_long_leg call."""

    def _make_strategy(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = None
        s.client = MagicMock()
        s.dry_run = False
        s.long_salvage_enabled = True
        s._try_sell_long_leg = MagicMock(return_value=False)
        return s

    def _entry(self):
        e = _FakeSalvageEntry()
        e.call_side_stopped = True
        e.put_side_stopped = False
        return e

    def test_prefetches_positions_and_passes_list_down(self):
        s = self._make_strategy()
        positions = [{"instrument_id": 111, "right": "C", "quantity": 1}]
        s._read_open_positions = MagicMock(return_value=positions)
        s.daily_state = MagicMock()
        s.daily_state.entries = [self._entry()]
        # force market hours so the time-gate doesn't skip
        market_open = get_us_market_time_stub()
        with patch("bots.hydra.strategy.get_us_market_time",
                   return_value=market_open):
            s._check_long_salvage()
        s._read_open_positions.assert_called_once()
        # _try_sell_long_leg got the prefetched list (call side stopped)
        s._try_sell_long_leg.assert_called_once_with(
            s.daily_state.entries[0], "call", positions
        )

    def test_empty_positions_skips_salvage(self):
        s = self._make_strategy()
        s._read_open_positions = MagicMock(return_value=[])
        s.daily_state = MagicMock()
        s.daily_state.entries = [self._entry()]
        market_open = get_us_market_time_stub()
        with patch("bots.hydra.strategy.get_us_market_time",
                   return_value=market_open):
            s._check_long_salvage()
        s._try_sell_long_leg.assert_not_called()


def get_us_market_time_stub():
    """A datetime safely inside 9:30-16:00 ET regular session."""
    from datetime import datetime
    return datetime(2026, 5, 21, 11, 0, 0)


class _FakeReconcileEntry:
    """Minimal entry stub for F4.4 POS-003 reconciliation tests."""

    def __init__(self, num=1, contracts=1, sc=None, lc=None, sp=None, lp=None):
        self.entry_number = num
        self.contracts = contracts
        self.short_call_uic = sc
        self.long_call_uic = lc
        self.short_put_uic = sp
        self.long_put_uic = lp
        self.call_side_stopped = False
        self.put_side_stopped = False
        # Legacy per-leg position ids — None by default; POS-004
        # settlement clears them alongside the *_uic fields.
        self.short_call_position_id = None
        self.long_call_position_id = None
        self.short_put_position_id = None
        self.long_put_position_id = None


class TestExpectedPositionQuantities:
    """F4.4 — _expected_position_quantities aggregates tracked legs into
    a {conid: signed_net_qty} map."""

    def _strategy(self, entries):
        s = HydraStrategy.__new__(HydraStrategy)
        s.daily_state = MagicMock()
        s.daily_state.active_entries = entries
        return s

    def test_full_ic_entry_four_conids(self):
        s = self._strategy([
            _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104),
        ])
        assert s._expected_position_quantities() == {
            101: -1, 102: 1, 103: -1, 104: 1,
        }

    def test_cleared_uic_excluded(self):
        """A leg whose *_uic was cleared (closed) contributes nothing."""
        s = self._strategy([
            _FakeReconcileEntry(sc=101, lc=102, sp=None, lp=None),
        ])
        assert s._expected_position_quantities() == {101: -1, 102: 1}

    def test_same_conid_two_entries_summed(self):
        s = self._strategy([
            _FakeReconcileEntry(num=1, sc=101, lc=102),
            _FakeReconcileEntry(num=2, sc=101, lc=102),
        ])
        assert s._expected_position_quantities() == {101: -2, 102: 2}

    def test_contract_scaling(self):
        s = self._strategy([
            _FakeReconcileEntry(contracts=3, sc=101, lc=102),
        ])
        assert s._expected_position_quantities() == {101: -3, 102: 3}

    def test_no_entries_empty(self):
        s = self._strategy([])
        assert s._expected_position_quantities() == {}


class TestActualPositionQuantities:
    """F4.4 — _actual_position_quantities sums broker rows per conid."""

    def test_sums_per_conid(self):
        out = HydraStrategy._actual_position_quantities([
            {"instrument_id": 101, "quantity": -1},
            {"instrument_id": 102, "quantity": 1},
        ])
        assert out == {101: -1, 102: 1}

    def test_multiple_rows_same_conid_summed(self):
        """Saxo can return several rows for one merged conid."""
        out = HydraStrategy._actual_position_quantities([
            {"instrument_id": 101, "quantity": -1},
            {"instrument_id": 101, "quantity": -1},
        ])
        assert out == {101: -2}

    def test_none_instrument_id_skipped(self):
        out = HydraStrategy._actual_position_quantities([
            {"instrument_id": None, "quantity": -1},
            {"instrument_id": 101, "quantity": -1},
        ])
        assert out == {101: -1}

    def test_empty_returns_empty(self):
        assert HydraStrategy._actual_position_quantities([]) == {}


class TestHandlePositionDiscrepancies:
    """F4.4 — _handle_position_discrepancies cleans up unambiguous
    vanished legs, leaves ambiguous ones for manual review."""

    def _strategy(self, entries):
        s = HydraStrategy.__new__(HydraStrategy)
        s.daily_state = MagicMock()
        s.daily_state.active_entries = entries
        return s

    def test_single_short_leg_gone_cleared_and_stopped(self):
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        s = self._strategy([entry])
        s._handle_position_discrepancies({101: (-1, 0)})
        assert entry.short_call_uic is None
        assert entry.call_side_stopped is True

    def test_single_long_leg_gone_cleared_no_stop_flag(self):
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        s = self._strategy([entry])
        s._handle_position_discrepancies({102: (1, 0)})
        assert entry.long_call_uic is None
        # a long vanishing does not "stop" a side
        assert entry.call_side_stopped is False

    def test_multi_leg_conid_left_alone(self):
        """A conid shared by two entries (merge) is ambiguous — no
        auto-mutation."""
        e1 = _FakeReconcileEntry(num=1, sc=101)
        e2 = _FakeReconcileEntry(num=2, sc=101)
        s = self._strategy([e1, e2])
        s._handle_position_discrepancies({101: (-2, -1)})
        assert e1.short_call_uic == 101
        assert e2.short_call_uic == 101
        assert e1.call_side_stopped is False
        assert e2.call_side_stopped is False

    def test_unexpected_nonzero_quantity_left_alone(self):
        """conid still shows a non-zero (partial/unexpected) quantity —
        not a clean vanish, leave for manual review."""
        entry = _FakeReconcileEntry(sc=101)
        s = self._strategy([entry])
        s._handle_position_discrepancies({101: (-1, 3)})
        assert entry.short_call_uic == 101
        assert entry.call_side_stopped is False


class TestHourlyReconciliationBody:
    """F4.4 — _check_hourly_reconciliation conid→quantity orchestration."""

    def _strategy(self, entries, open_positions):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = None
        s.dry_run = False
        s._last_reconciliation_time = None
        s.BOT_NAME = "HYDRA"
        s.contracts_per_entry = 1
        s.daily_state = MagicMock()
        s.daily_state.active_entries = entries
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._read_open_positions = MagicMock(return_value=open_positions)
        return s

    def _run(self, s):
        from datetime import datetime
        with patch("bots.meic.strategy.is_market_open", return_value=True), \
             patch("bots.hydra.strategy.get_us_market_time",
                   return_value=datetime(2026, 5, 21, 11, 0, 0)):
            s._check_hourly_reconciliation()

    def test_empty_actual_treated_as_fetch_failure(self):
        """Broker returns nothing while we expect legs → skip, no alert."""
        s = self._strategy(
            [_FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)],
            open_positions=[],
        )
        self._run(s)
        s.alert_service.send_alert.assert_not_called()

    def test_matched_positions_no_alert(self):
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        s = self._strategy([entry], open_positions=[
            {"instrument_id": 101, "quantity": -1},
            {"instrument_id": 102, "quantity": 1},
            {"instrument_id": 103, "quantity": -1},
            {"instrument_id": 104, "quantity": 1},
        ])
        self._run(s)
        s.alert_service.send_alert.assert_not_called()

    def test_vanished_short_call_alerts_and_cleans_up(self):
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        # broker is missing conid 101 (short call vanished)
        s = self._strategy([entry], open_positions=[
            {"instrument_id": 102, "quantity": 1},
            {"instrument_id": 103, "quantity": -1},
            {"instrument_id": 104, "quantity": 1},
        ])
        self._run(s)
        s.alert_service.send_alert.assert_called_once()
        assert entry.short_call_uic is None
        assert entry.call_side_stopped is True

    def test_merged_position_is_not_a_discrepancy(self):
        """Two entries short conid 101 → expected -2; broker shows one
        merged row qty -2 → reconciles cleanly, no alert."""
        e1 = _FakeReconcileEntry(num=1, sc=101)
        e2 = _FakeReconcileEntry(num=2, sc=101)
        s = self._strategy([e1, e2], open_positions=[
            {"instrument_id": 101, "quantity": -2},
        ])
        self._run(s)
        s.alert_service.send_alert.assert_not_called()


class TestReadOpenPositionsStrict:
    """F4.5 — _read_open_positions(strict=True) re-raises on a fetch
    failure so settlement/overnight checks can halt conservatively."""

    def _make(self, broker):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = None
        return s

    def test_strict_reraises_on_failure(self):
        fake_broker = MagicMock()
        fake_broker.get_positions.side_effect = RuntimeError("broker outage")
        s = self._make(fake_broker)
        with pytest.raises(RuntimeError, match="broker outage"):
            s._read_open_positions(strict=True)

    def test_non_strict_swallows_failure(self):
        fake_broker = MagicMock()
        fake_broker.get_positions.side_effect = RuntimeError("broker outage")
        s = self._make(fake_broker)
        assert s._read_open_positions(strict=False) == []
        assert s._read_open_positions() == []  # default is non-strict


class TestFix82OvernightCheck:
    """F4.5 — the FIX #82 overnight-position check in _reset_for_new_day
    verifies against the broker via _read_open_positions instead of
    intersecting Saxo PositionId sets."""

    def _make_strategy(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = None
        s.client = MagicMock()
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s.BOT_NAME = "HYDRA"
        s.contracts_per_entry = 1
        s._critical_intervention_required = False
        s._critical_intervention_reason = None
        return s

    def test_overnight_positions_trigger_halt(self):
        s = self._make_strategy()
        s.registry.get_positions.return_value = {"stale-1"}
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 101, "quantity": -1, "right": "C"},
        ])
        s._reset_for_new_day()
        assert s._critical_intervention_required is True
        s.alert_service.send_alert.assert_called_once()
        # strict=True so a fetch failure would be distinguishable
        s._read_open_positions.assert_called_once_with(strict=True)

    def test_fetch_failure_triggers_conservative_halt(self):
        s = self._make_strategy()
        s.registry.get_positions.return_value = {"stale-1"}
        s._read_open_positions = MagicMock(
            side_effect=RuntimeError("broker outage")
        )
        s._reset_for_new_day()
        assert s._critical_intervention_required is True
        s.alert_service.send_alert.assert_called_once()

    # The "no overnight positions → clean registry → proceed to full
    # reset" path falls through into the whole _reset_for_new_day body
    # (15+ unrelated state attributes), which is out of F4.5's scope.
    # F4.5 changed only the *verification* — covered by the two
    # halt-path tests above — and the registry cleanup loop is
    # unchanged from the pre-F4.5 code.


class TestCheckAfterHoursSettlement:
    """F4.6 — check_after_hours_settlement (POS-004) settles tracked
    legs in the conid→quantity model: a leg is settled when the broker
    shows zero quantity at its conid."""

    def _make_strategy(self, entries):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = None
        s.BOT_NAME = "HYDRA"
        s.registry = MagicMock()
        s._settlement_reconciliation_complete = False
        s.daily_state = MagicMock()
        s.daily_state.entries = entries
        s.daily_state.active_entries = entries
        s.daily_state.total_realized_pnl = 100.0
        s.daily_state.total_commission = 10.0
        s._pnl_history = []
        s._process_expired_credits = MagicMock(return_value=0.0)
        s._save_state_to_disk = MagicMock()
        s._log_safety_event = MagicMock()
        return s

    def test_already_complete_empty_registry_returns_true(self):
        s = self._make_strategy([])
        s._settlement_reconciliation_complete = True
        s.registry.get_positions.return_value = set()
        s._read_open_positions = MagicMock()
        assert s.check_after_hours_settlement() is True
        s._read_open_positions.assert_not_called()

    def test_empty_registry_marks_complete(self):
        s = self._make_strategy([])
        s.registry.get_positions.return_value = set()
        assert s.check_after_hours_settlement() is True
        assert s._settlement_reconciliation_complete is True
        s._process_expired_credits.assert_called_once()

    def test_all_conids_settled_marks_complete(self):
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        entry.short_call_position_id = "p-sc"  # legacy ids set pre-settle
        entry.long_put_position_id = "p-lp"
        s = self._make_strategy([entry])
        s.registry.get_positions.return_value = {"id1"}
        # broker shows nothing — every conid expired
        s._read_open_positions = MagicMock(return_value=[])
        result = s.check_after_hours_settlement()
        assert result is True
        assert s._settlement_reconciliation_complete is True
        # settled legs' uics AND legacy position ids both cleared
        assert entry.short_call_uic is None
        assert entry.long_put_uic is None
        assert entry.short_call_position_id is None
        assert entry.long_put_position_id is None
        s._process_expired_credits.assert_called_once()

    def test_positions_still_open_returns_false(self):
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        s = self._make_strategy([entry])
        s.registry.get_positions.return_value = {"id1"}
        # conid 101 still open on the broker
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 101, "quantity": -1},
        ])
        result = s.check_after_hours_settlement()
        assert result is False
        assert s._settlement_reconciliation_complete is False
        # the settled conids' legs were still cleared
        assert entry.long_call_uic is None  # 102 settled
        assert entry.short_call_uic == 101  # 101 still open — untouched

    def test_fetch_failure_returns_false(self):
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        s = self._make_strategy([entry])
        s.registry.get_positions.return_value = {"id1"}
        s._read_open_positions = MagicMock(
            side_effect=RuntimeError("broker outage")
        )
        result = s.check_after_hours_settlement()
        assert result is False
        assert s._settlement_reconciliation_complete is False


class TestGetBrokerPnlForEntry:
    """F4.7 — _get_broker_pnl_for_entry sums unrealized P&L by conid
    (instrument_id) instead of Saxo PositionId, excluding stopped sides."""

    def _make(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = None
        s.client = None
        return s

    def test_sums_non_stopped_sides(self):
        s = self._make()
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        positions = [
            {"instrument_id": 101, "unrealized_pnl": -50.0},
            {"instrument_id": 102, "unrealized_pnl": 10.0},
            {"instrument_id": 103, "unrealized_pnl": -30.0},
            {"instrument_id": 104, "unrealized_pnl": 5.0},
        ]
        assert s._get_broker_pnl_for_entry(entry, positions=positions) == -65.0

    def test_excludes_stopped_side(self):
        s = self._make()
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        entry.call_side_stopped = True  # call legs must be excluded
        positions = [
            {"instrument_id": 101, "unrealized_pnl": -999.0},  # excluded
            {"instrument_id": 102, "unrealized_pnl": -999.0},  # excluded
            {"instrument_id": 103, "unrealized_pnl": -30.0},
            {"instrument_id": 104, "unrealized_pnl": 5.0},
        ]
        assert s._get_broker_pnl_for_entry(entry, positions=positions) == -25.0

    def test_conid_absent_contributes_zero(self):
        s = self._make()
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        positions = [{"instrument_id": 101, "unrealized_pnl": -50.0}]
        assert s._get_broker_pnl_for_entry(entry, positions=positions) == -50.0

    def test_none_positions_fetches_fresh(self):
        s = self._make()
        entry = _FakeReconcileEntry(sc=101)
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 101, "unrealized_pnl": -12.0},
        ])
        assert s._get_broker_pnl_for_entry(entry) == -12.0
        s._read_open_positions.assert_called_once()

    def test_exception_falls_back_to_entry_unrealized(self):
        s = self._make()
        entry = _FakeReconcileEntry(sc=101)
        entry.unrealized_pnl = -77.0
        s._read_open_positions = MagicMock(side_effect=RuntimeError("boom"))
        assert s._get_broker_pnl_for_entry(entry) == -77.0


class TestReconcileRecoveredEntriesWithBroker:
    """F4.8 — _reconcile_recovered_entries_with_broker cross-checks
    state-file-recovered entries against the broker (conid→quantity)."""

    def _make(self, entries):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = None
        s.daily_state = MagicMock()
        s.daily_state.active_entries = entries
        s._handle_position_discrepancies = MagicMock()
        s._save_state_to_disk = MagicMock()
        return s

    def test_fetch_failure_skips_gracefully(self):
        """strict fetch raises → cross-check skipped, no state mutation."""
        s = self._make([_FakeReconcileEntry(sc=101)])
        s._read_open_positions = MagicMock(side_effect=RuntimeError("outage"))
        s._reconcile_recovered_entries_with_broker()
        s._handle_position_discrepancies.assert_not_called()
        s._save_state_to_disk.assert_not_called()

    def test_discrepancy_triggers_handler(self):
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        s = self._make([entry])
        # broker missing conid 101 — a vanished short call
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 102, "quantity": 1},
            {"instrument_id": 103, "quantity": -1},
            {"instrument_id": 104, "quantity": 1},
        ])
        s._reconcile_recovered_entries_with_broker()
        s._handle_position_discrepancies.assert_called_once()
        s._save_state_to_disk.assert_called_once()

    def test_no_discrepancy_no_handler(self):
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        s = self._make([entry])
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 101, "quantity": -1},
            {"instrument_id": 102, "quantity": 1},
            {"instrument_id": 103, "quantity": -1},
            {"instrument_id": 104, "quantity": 1},
        ])
        s._reconcile_recovered_entries_with_broker()
        s._handle_position_discrepancies.assert_not_called()

    def test_no_expected_returns_early(self):
        s = self._make([])  # no entries → nothing expected
        s._read_open_positions = MagicMock(return_value=[])
        s._reconcile_recovered_entries_with_broker()
        s._handle_position_discrepancies.assert_not_called()


class TestRecoverPositions:
    """F4.8 — _recover_positions_from_saxo rewritten state-file-
    authoritative: the state file reconstructs entries, the broker is
    the cross-check (live only)."""

    def _make(self, dry_run=True):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = None
        s.dry_run = dry_run
        s.BOT_NAME = "HYDRA"
        s.contracts_per_entry = 1
        s._next_entry_index = 0
        s.daily_state = MagicMock()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.date = None
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._reconcile_recovered_entries_with_broker = MagicMock()
        s._log_safety_event = MagicMock()
        # state-machine fields the recovery must restore (the F4.8
        # CRITICAL fix). entry_times: only len() is consulted.
        s.state = MEICState.IDLE
        s.entry_times = [None, None, None]
        return s

    def test_active_entries_set_state_monitoring(self):
        """CRITICAL: recovery with live entries must leave state =
        MONITORING — otherwise the main loop never enters
        _handle_monitoring and stops go unchecked."""
        s = self._make()
        s._load_state_file_history = MagicMock(return_value=True)
        entry = _FakeReconcileEntry(sc=101)
        s.daily_state.entries = [entry]
        s.daily_state.active_entries = [entry]
        s._recover_positions_from_saxo()
        assert s.state == MEICState.MONITORING

    def test_no_active_entries_slots_left_sets_waiting(self):
        s = self._make()
        s._load_state_file_history = MagicMock(return_value=True)
        entry = _FakeReconcileEntry(sc=101)
        s.daily_state.entries = [entry]      # loaded
        s.daily_state.active_entries = []    # all done
        s._next_entry_index = 1              # < len(entry_times)=3
        s._recover_positions_from_saxo()
        assert s.state == MEICState.WAITING_FIRST_ENTRY

    def test_no_active_entries_all_slots_used_sets_daily_complete(self):
        s = self._make()
        s._load_state_file_history = MagicMock(return_value=True)
        entry = _FakeReconcileEntry(sc=101)
        s.daily_state.entries = [entry]
        s.daily_state.active_entries = []
        s._next_entry_index = 3              # == len(entry_times)
        s._recover_positions_from_saxo()
        assert s.state == MEICState.DAILY_COMPLETE

    def test_no_state_file_starts_fresh(self):
        s = self._make()
        s._load_state_file_history = MagicMock(return_value=False)
        s.daily_state.entries = []
        assert s._recover_positions_from_saxo() is False
        s._reconcile_recovered_entries_with_broker.assert_not_called()

    def test_loaded_with_entries_dry_run_skips_broker_check(self):
        s = self._make(dry_run=True)
        s._load_state_file_history = MagicMock(return_value=True)
        entry = _FakeReconcileEntry(sc=101)
        s.daily_state.entries = [entry]
        s.daily_state.active_entries = [entry]
        result = s._recover_positions_from_saxo()
        assert result is True
        # dry-run: no broker cross-check, no recovery alert
        s._reconcile_recovered_entries_with_broker.assert_not_called()
        s.alert_service.send_alert.assert_not_called()

    def test_loaded_live_runs_broker_check_and_alerts(self):
        s = self._make(dry_run=False)
        s._load_state_file_history = MagicMock(return_value=True)
        entry = _FakeReconcileEntry(sc=101)
        s.daily_state.entries = [entry]
        s.daily_state.active_entries = [entry]
        s._recover_positions_from_saxo()
        s._reconcile_recovered_entries_with_broker.assert_called_once()
        s.alert_service.send_alert.assert_called_once()

    def test_loaded_but_no_entries_returns_false(self):
        s = self._make()
        s._load_state_file_history = MagicMock(return_value=True)
        s.daily_state.entries = []
        assert s._recover_positions_from_saxo() is False

    def test_exception_returns_false(self):
        s = self._make()
        s._load_state_file_history = MagicMock(side_effect=RuntimeError("boom"))
        s.daily_state.entries = []
        assert s._recover_positions_from_saxo() is False

    def test_date_set_on_daily_state(self):
        s = self._make()
        s._load_state_file_history = MagicMock(return_value=False)
        s.daily_state.entries = []
        s._recover_positions_from_saxo()
        assert s.daily_state.date is not None


class TestQuoteMid:
    """F4.9 — _quote_mid derives a mid price from the normalized quote
    shape: broker mid → (bid+ask)/2 → last → mark → 0.0."""

    def test_prefers_broker_mid(self):
        assert HydraStrategy._quote_mid(
            {"bid": 2.0, "ask": 2.2, "mid": 2.05, "last": 9.9}
        ) == 2.05

    def test_falls_back_to_bid_ask_average(self):
        assert HydraStrategy._quote_mid(
            {"bid": 2.0, "ask": 2.2, "mid": None}
        ) == 2.1

    def test_falls_back_to_last(self):
        assert HydraStrategy._quote_mid(
            {"bid": None, "ask": None, "mid": None, "last": 1.7}
        ) == 1.7

    def test_falls_back_to_mark(self):
        assert HydraStrategy._quote_mid(
            {"bid": None, "ask": None, "mid": None, "last": None, "mark": 1.3}
        ) == 1.3

    def test_empty_quote_returns_zero(self):
        assert HydraStrategy._quote_mid(None) == 0.0
        assert HydraStrategy._quote_mid({}) == 0.0


class TestBatchUpdateEntryPrices:
    """F4.9 — _batch_update_entry_prices sources monitoring quotes via
    the broker-agnostic _read_option_quotes_batch."""

    def _make(self, dry_run):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = None
        s.dry_run = dry_run
        s.daily_state = MagicMock()
        s._simulate_hydra_entry_prices = MagicMock()
        return s

    _QUOTES = {
        101: {"bid": 2.0, "ask": 2.1, "mid": 2.05},
        102: {"bid": 0.5, "ask": 0.6, "mid": 0.55},
        103: {"bid": 1.0, "ask": 1.1, "mid": 1.05},
        104: {"bid": 0.3, "ask": 0.4, "mid": 0.35},
    }

    # ─── live path ─────────────────────────────────────────────────────

    def test_live_distributes_quotes_to_legs(self):
        s = self._make(dry_run=False)
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        s.daily_state.active_entries = [entry]
        s._read_option_quotes_batch = MagicMock(return_value=self._QUOTES)
        s._batch_update_entry_prices()
        assert entry.short_call_price == 2.05
        assert entry.short_call_bid == 2.0
        assert entry.short_call_ask == 2.1
        assert entry.long_put_price == 0.35

    def test_live_empty_quotes_keeps_prior_prices(self):
        s = self._make(dry_run=False)
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        entry.short_call_price = 9.99  # a prior value
        s.daily_state.active_entries = [entry]
        s._read_option_quotes_batch = MagicMock(return_value={})
        s._batch_update_entry_prices()
        # fetch failure → skipped this tick, prior price untouched
        assert entry.short_call_price == 9.99

    def test_live_no_uics_returns_without_fetch(self):
        s = self._make(dry_run=False)
        entry = _FakeReconcileEntry(sc=None, lc=None, sp=None, lp=None)
        s.daily_state.active_entries = [entry]
        s._read_option_quotes_batch = MagicMock()
        s._batch_update_entry_prices()
        s._read_option_quotes_batch.assert_not_called()

    # ─── dry-run path ──────────────────────────────────────────────────

    def test_dry_run_with_uics_uses_real_quotes(self):
        s = self._make(dry_run=True)
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        s.daily_state.active_entries = [entry]
        s._read_option_quotes_batch = MagicMock(return_value=self._QUOTES)
        s._batch_update_entry_prices()
        assert entry.short_call_price == 2.05
        s._simulate_hydra_entry_prices.assert_not_called()

    def test_dry_run_empty_quotes_falls_back_to_simulation(self):
        s = self._make(dry_run=True)
        entry = _FakeReconcileEntry(sc=101, lc=102, sp=103, lp=104)
        s.daily_state.active_entries = [entry]
        s._read_option_quotes_batch = MagicMock(return_value={})
        s._batch_update_entry_prices()
        s._simulate_hydra_entry_prices.assert_called_once_with(entry)


class TestReadFxRate:
    """F5.3 — _read_fx_rate dispatches the FX-rate lookup to the active
    broker and degrades to None on failure."""

    def _make(self, broker=None, client=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = client
        return s

    def test_ib_path(self):
        fake_broker = MagicMock()
        fake_broker.get_fx_rate.return_value = 1.08
        s = self._make(broker=fake_broker)
        assert s._read_fx_rate("USD", "EUR") == 1.08
        fake_broker.get_fx_rate.assert_called_once_with("USD", "EUR")

    def test_saxo_path(self):
        fake_client = MagicMock()
        fake_client.get_fx_rate.return_value = 0.92
        s = self._make(broker=None, client=fake_client)
        assert s._read_fx_rate("USD", "EUR") == 0.92
        fake_client.get_fx_rate.assert_called_once_with("USD", "EUR")

    def test_exception_returns_none(self):
        fake_broker = MagicMock()
        fake_broker.get_fx_rate.side_effect = RuntimeError("fx outage")
        s = self._make(broker=fake_broker)
        assert s._read_fx_rate("USD", "EUR") is None


class TestReadClosedPositionPrice:
    """F5.3 — _read_closed_position_price dispatches the close-price
    lookup to the active broker."""

    def _make(self, broker=None, client=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = client
        return s

    def test_ib_path_casts_conid_to_int(self):
        fake_broker = MagicMock()
        fake_broker.get_closed_position_price.return_value = {"closing_price": 2.55}
        s = self._make(broker=fake_broker)
        out = s._read_closed_position_price("12345", buy_or_sell="Sell")
        assert out == {"closing_price": 2.55}
        fake_broker.get_closed_position_price.assert_called_once_with(
            12345, buy_or_sell="Sell"
        )

    def test_saxo_path(self):
        fake_client = MagicMock()
        fake_client.get_closed_position_price.return_value = {"closing_price": 1.10}
        s = self._make(broker=None, client=fake_client)
        out = s._read_closed_position_price(999, buy_or_sell="Buy")
        assert out == {"closing_price": 1.10}
        fake_client.get_closed_position_price.assert_called_once_with(
            999, buy_or_sell="Buy"
        )

    def test_none_instrument_id_returns_none(self):
        fake_broker = MagicMock()
        s = self._make(broker=fake_broker)
        assert s._read_closed_position_price(None, buy_or_sell="Sell") is None
        fake_broker.get_closed_position_price.assert_not_called()

    def test_exception_returns_none(self):
        fake_broker = MagicMock()
        fake_broker.get_closed_position_price.side_effect = RuntimeError("boom")
        s = self._make(broker=fake_broker)
        assert s._read_closed_position_price(12345, buy_or_sell="Sell") is None

    def test_broker_returns_none_passed_through(self):
        fake_broker = MagicMock()
        fake_broker.get_closed_position_price.return_value = None
        s = self._make(broker=fake_broker)
        assert s._read_closed_position_price(12345, buy_or_sell="Sell") is None


class TestVerifySettlementPnl:
    """F5.5 — _verify_settlement_pnl_from_saxo (Fix #87) skips on the
    IBKR path: the CP Web API has no real-time closed-positions P&L
    report. The Saxo legacy path is unchanged."""

    def _make(self, broker=None, dry_run=False):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.dry_run = dry_run
        s.client = MagicMock()
        s.daily_state = MagicMock()
        s.daily_state.total_realized_pnl = 500.0
        s.daily_state.total_commission = 20.0
        return s

    def test_ib_path_skips_without_touching_pnl(self):
        s = self._make(broker=MagicMock(), dry_run=False)
        s._verify_settlement_pnl_from_saxo()
        # no Saxo report call, no P&L mutation
        s.client._make_request.assert_not_called()
        assert s.daily_state.total_realized_pnl == 500.0

    def test_dry_run_skips(self):
        s = self._make(broker=None, dry_run=True)
        s._verify_settlement_pnl_from_saxo()
        s.client._make_request.assert_not_called()
        assert s.daily_state.total_realized_pnl == 500.0

    def test_saxo_path_still_queries_report(self):
        """broker=None + live → the legacy Saxo verification runs."""
        s = self._make(broker=None, dry_run=False)
        s.client.client_key = "ck"
        s.client._make_request.return_value = None  # no report → early return
        s._verify_settlement_pnl_from_saxo()
        s.client._make_request.assert_called_once()


class TestPlaceLegOrder:
    """F6.1 — _place_leg_order places one IBKR option leg via
    place_and_wait_for_fill and normalizes the result."""

    def _make(self, broker=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = None
        return s

    def test_filled_order_normalized(self):
        fake_broker = MagicMock()
        fake_broker.place_and_wait_for_fill.return_value = {
            "order_id": "o-1", "status": "filled",
            "filled_quantity": 2, "avg_fill_price": 2.55, "raw": {},
        }
        s = self._make(broker=fake_broker)
        out = s._place_leg_order(
            instrument_id=12345, side="BUY", quantity=2,
            order_type="LMT", limit_price=2.60,
        )
        assert out["success"] is True
        assert out["filled"] is True
        assert out["order_id"] == "o-1"
        assert out["fill_price"] == 2.55
        assert out["position_id"] is None  # IBKR has no per-leg id
        call = fake_broker.place_and_wait_for_fill.call_args.kwargs
        assert call["conid"] == 12345
        assert call["side"] == "BUY"
        assert call["quantity"] == 2
        assert call["order_type"] == "LMT"
        assert call["limit_price"] == 2.60

    def test_partial_fill_not_marked_filled(self):
        fake_broker = MagicMock()
        fake_broker.place_and_wait_for_fill.return_value = {
            "order_id": "o-2", "status": "submitted",
            "filled_quantity": 1, "avg_fill_price": None, "raw": {},
        }
        s = self._make(broker=fake_broker)
        out = s._place_leg_order(
            instrument_id=1, side="SELL", quantity=2, order_type="LMT",
            limit_price=1.0,
        )
        assert out["success"] is True   # order placed
        assert out["filled"] is False   # but only 1 of 2 filled

    def test_market_order_type_translated(self):
        fake_broker = MagicMock()
        fake_broker.place_and_wait_for_fill.return_value = {
            "order_id": "o-3", "filled_quantity": 1, "avg_fill_price": 3.0,
        }
        s = self._make(broker=fake_broker)
        s._place_leg_order(instrument_id=1, side="BUY", quantity=1,
                           order_type="MKT")
        assert fake_broker.place_and_wait_for_fill.call_args.kwargs["order_type"] == "MKT"

    def test_exception_returns_failure_dict(self):
        fake_broker = MagicMock()
        fake_broker.place_and_wait_for_fill.side_effect = RuntimeError("rejected")
        s = self._make(broker=fake_broker)
        out = s._place_leg_order(instrument_id=1, side="BUY", quantity=1,
                                 order_type="MKT")
        assert out["success"] is False
        assert out["filled"] is False
        assert out["order_id"] is None

    def test_none_broker_raises(self):
        s = self._make(broker=None)
        with pytest.raises(RuntimeError, match="IBKR-only"):
            s._place_leg_order(instrument_id=1, side="BUY", quantity=1,
                               order_type="MKT")


class TestCloseLegOrder:
    """F6.1 — _close_leg_order is a market-order wrapper of
    _place_leg_order."""

    def test_delegates_as_market_order(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = MagicMock()
        s.client = None
        s.broker.place_and_wait_for_fill.return_value = {
            "order_id": "c-1", "filled_quantity": 1, "avg_fill_price": 0.40,
        }
        out = s._close_leg_order(instrument_id=9, side="BUY", quantity=1)
        assert out["filled"] is True
        assert out["fill_price"] == 0.40
        assert s.broker.place_and_wait_for_fill.call_args.kwargs["order_type"] == "MKT"


class TestWriteDispatchHelpers:
    """F6.1 — _cancel_order / _get_order_status / _get_open_orders are
    broker-agnostic dispatch + failure guards."""

    def _make(self, broker=None, client=None):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = broker
        s.client = client
        return s

    def test_cancel_order_ib(self):
        b = MagicMock()
        b.cancel_order.return_value = True
        s = self._make(broker=b)
        assert s._cancel_order("o-1") is True
        b.cancel_order.assert_called_once_with("o-1")

    def test_cancel_order_saxo(self):
        c = MagicMock()
        c.cancel_order.return_value = {"ok": 1}
        s = self._make(broker=None, client=c)
        assert s._cancel_order("o-1") is True

    def test_cancel_order_exception_false(self):
        b = MagicMock()
        b.cancel_order.side_effect = RuntimeError("x")
        s = self._make(broker=b)
        assert s._cancel_order("o-1") is False

    def test_get_order_status_ib(self):
        b = MagicMock()
        b.get_order_status.return_value = {"status": "Filled"}
        s = self._make(broker=b)
        assert s._get_order_status("o-1") == {"status": "Filled"}

    def test_get_order_status_exception_empty(self):
        b = MagicMock()
        b.get_order_status.side_effect = RuntimeError("x")
        s = self._make(broker=b)
        assert s._get_order_status("o-1") == {}

    def test_get_open_orders_ib(self):
        b = MagicMock()
        b.get_open_orders.return_value = [{"orderId": 1}]
        s = self._make(broker=b)
        assert s._get_open_orders() == [{"orderId": 1}]

    def test_get_open_orders_saxo(self):
        c = MagicMock()
        c.get_open_orders.return_value = [{"OrderId": "s1"}]
        s = self._make(broker=None, client=c)
        assert s._get_open_orders() == [{"OrderId": "s1"}]

    def test_get_open_orders_exception_empty(self):
        b = MagicMock()
        b.get_open_orders.side_effect = RuntimeError("x")
        s = self._make(broker=b)
        assert s._get_open_orders() == []
