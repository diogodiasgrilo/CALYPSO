"""Tests for shared/broker/saxo_adapter.py — Phase B.2.

Verifies that:
  • SaxoBrokerAdapter satisfies the BrokerInterface contract (subclass
    instantiates with a mocked SaxoClient).
  • Easy 1:1 delegations forward args + return-shape correctly.
  • Saxo dict envelopes are normalized into QuoteSnapshot / OrderResult /
    Position dataclasses.
  • Status-string normalization collapses Saxo's vocabulary to the
    BrokerInterface canonical set.
  • Deferred stubs raise NotImplementedError with actionable messages.

All tests mock the underlying SaxoClient — no real network calls.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.broker.interface import (
    BrokerAuthError,
    BrokerError,
    BrokerInterface,
    IronCondorRequest,
    OrderResult,
    Position,
    QuoteSnapshot,
    VerticalSpreadRequest,
)
from shared.broker.saxo_adapter import SaxoBrokerAdapter, _normalize_status


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def mock_saxo():
    return MagicMock()


@pytest.fixture
def adapter(mock_saxo):
    return SaxoBrokerAdapter(mock_saxo)


# ─── Contract: adapter is a proper BrokerInterface ──────────────────────────


class TestContract:
    def test_is_broker_interface_subclass(self):
        assert issubclass(SaxoBrokerAdapter, BrokerInterface)

    def test_constructable_with_mock(self, mock_saxo):
        # If any @abstractmethod is missing, this raises TypeError.
        SaxoBrokerAdapter(mock_saxo)

    def test_saxo_escape_hatch(self, adapter, mock_saxo):
        """The .saxo property exposes the wrapped client for callers
        that need Saxo-specific functionality the interface doesn't cover."""
        assert adapter.saxo is mock_saxo


# ─── Status normalization vocabulary ────────────────────────────────────────


class TestStatusNormalize:
    def test_working_vocabulary(self):
        assert _normalize_status("Working") == "Submitted"
        assert _normalize_status("Submitted") == "Submitted"
        assert _normalize_status("PreSubmitted") == "PreSubmitted"

    def test_terminal_vocabulary(self):
        assert _normalize_status("Filled") == "Filled"
        assert _normalize_status("PartiallyFilled") == "PartiallyFilled"
        assert _normalize_status("Cancelled") == "Cancelled"
        assert _normalize_status("Canceled") == "Cancelled"  # US spelling
        assert _normalize_status("Expired") == "Expired"
        assert _normalize_status("Rejected") == "Rejected"

    def test_case_insensitive(self):
        assert _normalize_status("FILLED") == "Filled"
        assert _normalize_status("filled") == "Filled"

    def test_unknown_passthrough(self):
        """Unrecognized strings pass through verbatim so adapters never
        silently lose information."""
        assert _normalize_status("SomeNewStatus") == "SomeNewStatus"

    def test_none_becomes_unknown(self):
        assert _normalize_status(None) == "Unknown"
        assert _normalize_status("") == "Unknown"


# ─── Lifecycle ──────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_connect_success(self, adapter, mock_saxo):
        mock_saxo.authenticate.return_value = True
        assert adapter.connect() is True
        mock_saxo.authenticate.assert_called_once()

    def test_connect_returns_false_raises(self, adapter, mock_saxo):
        mock_saxo.authenticate.return_value = False
        with pytest.raises(BrokerAuthError):
            adapter.connect()

    def test_connect_raises_wrapped_as_auth_error(self, adapter, mock_saxo):
        mock_saxo.authenticate.side_effect = Exception("network down")
        with pytest.raises(BrokerAuthError, match="Saxo authenticate failed"):
            adapter.connect()

    def test_is_connected_true_when_token_valid(self, adapter, mock_saxo):
        mock_saxo._is_token_valid.return_value = True
        assert adapter.is_connected() is True

    def test_is_connected_swallows_attribute_errors(self, adapter, mock_saxo):
        """If SaxoClient internals shift and _is_token_valid raises, we
        return False rather than blowing up the caller."""
        mock_saxo._is_token_valid.side_effect = AttributeError("renamed")
        assert adapter.is_connected() is False

    def test_disconnect_is_noop(self, adapter):
        # Should not raise, regardless of prior state
        adapter.disconnect()


# ─── Quote normalization ────────────────────────────────────────────────────


class TestGetQuote:
    def test_maps_bid_ask_mid(self, adapter, mock_saxo):
        mock_saxo.get_quote.return_value = {
            "Quote": {"Bid": 5.20, "Ask": 5.40, "Mid": 5.30},
            "LastUpdated": "2026-05-14T12:00:00",
        }
        snap = adapter.get_quote("12345")
        assert isinstance(snap, QuoteSnapshot)
        assert snap.instrument_id == "12345"
        assert snap.bid == 5.20
        assert snap.ask == 5.40
        assert snap.mid == 5.30
        assert snap.timestamp == "2026-05-14T12:00:00"

    def test_computes_mid_when_missing(self, adapter, mock_saxo):
        """If Saxo returns Bid+Ask but no Mid, the adapter computes it."""
        mock_saxo.get_quote.return_value = {
            "Quote": {"Bid": 5.20, "Ask": 5.40},
        }
        snap = adapter.get_quote("12345")
        assert snap.mid == pytest.approx(5.30)

    def test_none_propagates(self, adapter, mock_saxo):
        mock_saxo.get_quote.return_value = None
        assert adapter.get_quote("12345") is None

    def test_empty_quote_dict(self, adapter, mock_saxo):
        """Saxo can return `{}` for unknown UICs."""
        mock_saxo.get_quote.return_value = {"Quote": {}}
        snap = adapter.get_quote("12345")
        assert snap is not None
        assert snap.bid is None and snap.ask is None and snap.mid is None

    def test_forwards_asset_type_to_saxo(self, adapter, mock_saxo):
        mock_saxo.get_quote.return_value = {"Quote": {}}
        adapter.get_quote("12345")
        call = mock_saxo.get_quote.call_args
        assert call.kwargs.get("asset_type") == "StockIndexOption"


class TestGetQuotesBatch:
    def test_maps_each_uic_to_quote_snapshot(self, adapter, mock_saxo):
        mock_saxo.get_quotes_batch.return_value = {
            111: {"Quote": {"Bid": 1.0, "Ask": 1.2, "Mid": 1.1}},
            222: {"Quote": {"Bid": 2.0, "Ask": 2.2, "Mid": 2.1}},
        }
        snaps = adapter.get_quotes_batch(["111", "222"])
        assert len(snaps) == 2
        assert all(isinstance(s, QuoteSnapshot) for s in snaps)
        # Order preserved from the input list
        assert snaps[0].instrument_id == "111"
        assert snaps[1].instrument_id == "222"

    def test_missing_uic_dropped(self, adapter, mock_saxo):
        """If Saxo's batch result omits some UICs, the adapter drops them
        rather than emitting None entries."""
        mock_saxo.get_quotes_batch.return_value = {
            111: {"Quote": {"Bid": 1.0, "Ask": 1.2}},
            # 222 missing entirely
        }
        snaps = adapter.get_quotes_batch(["111", "222"])
        assert len(snaps) == 1


class TestGetOptionGreeks:
    def test_maps_greeks(self, adapter, mock_saxo):
        mock_saxo.get_option_greeks.return_value = {
            "Quote": {"Bid": 5.20, "Ask": 5.40, "Mid": 5.30},
            "Greeks": {
                "Delta": -0.42, "Gamma": 0.08, "Theta": -3.4,
                "Vega": 1.2, "ImpliedVolatility": 0.18,
            },
        }
        snap = adapter.get_option_greeks("12345")
        assert snap.delta == -0.42
        assert snap.gamma == 0.08
        assert snap.theta == -3.4
        assert snap.vega == 1.2
        assert snap.iv == 0.18


class TestGetVixPrice:
    def test_returns_none_when_vix_uic_unset(self, adapter, mock_saxo):
        # Default state — VIX UIC not configured
        assert adapter.get_vix_price() is None
        mock_saxo.get_vix_price.assert_not_called()

    def test_uses_configured_vix_uic(self, adapter, mock_saxo):
        mock_saxo.get_vix_price.return_value = 18.5
        adapter.set_vix_uic(13455)
        assert adapter.get_vix_price() == 18.5
        mock_saxo.get_vix_price.assert_called_once_with(13455)


# ─── Account / balance ──────────────────────────────────────────────────────


class TestAccountInfo:
    def test_returns_dict(self, adapter, mock_saxo):
        mock_saxo.get_account_info.return_value = {"AccountId": "DU123"}
        info = adapter.get_account_info()
        assert info == {"AccountId": "DU123"}

    def test_none_becomes_empty_dict(self, adapter, mock_saxo):
        mock_saxo.get_account_info.return_value = None
        assert adapter.get_account_info() == {}


class TestGetBalance:
    def test_returns_normalized_shape(self, adapter, mock_saxo):
        mock_saxo.get_balance.return_value = {
            "CashBalance": 50000.0,
            "Currency": "USD",
        }
        bal = adapter.get_balance("USD")
        assert bal["currency"] == "USD"
        assert bal["base_currency"] == "USD"
        assert bal["tradable"] == 50000.0
        assert "raw" in bal

    def test_falls_back_to_total_value(self, adapter, mock_saxo):
        mock_saxo.get_balance.return_value = {
            "TotalValue": 45000.0,
            "Currency": "EUR",
        }
        bal = adapter.get_balance("USD")
        assert bal["tradable"] == 45000.0
        assert bal["base_currency"] == "EUR"

    def test_empty_balance_yields_zero_tradable(self, adapter, mock_saxo):
        mock_saxo.get_balance.return_value = None
        bal = adapter.get_balance("USD")
        assert bal["tradable"] == 0.0


# ─── Positions ──────────────────────────────────────────────────────────────


class TestGetPositions:
    def test_long_position(self, adapter, mock_saxo):
        mock_saxo.get_positions.return_value = [{
            "PositionBase": {
                "Uic": 416904, "Symbol": "SPX",
                "Amount": 10, "OpenPrice": 5500.0,
            },
            "PositionView": {"ProfitLossOnTrade": 150.0},
        }]
        positions = adapter.get_positions()
        assert len(positions) == 1
        p = positions[0]
        assert isinstance(p, Position)
        assert p.instrument_id == "416904"
        assert p.symbol == "SPX"
        assert p.quantity == 10
        assert p.side == "LONG"
        assert p.avg_price == 5500.0
        assert p.unrealized_pnl == 150.0

    def test_short_position(self, adapter, mock_saxo):
        mock_saxo.get_positions.return_value = [{
            "PositionBase": {
                "Uic": 416904, "Symbol": "SPX",
                "Amount": -5, "OpenPrice": 5500.0,
            },
            "PositionView": {},
        }]
        p = adapter.get_positions()[0]
        assert p.side == "SHORT"
        assert p.quantity == 5  # abs(amount)

    def test_empty(self, adapter, mock_saxo):
        mock_saxo.get_positions.return_value = None
        assert adapter.get_positions() == []


# ─── Order management ──────────────────────────────────────────────────────


class TestOrderResultMapping:
    def test_to_order_result_normalizes_status(self, adapter, mock_saxo):
        mock_saxo.get_order_status.return_value = {
            "OrderId": "abc123",
            "Status": "Working",
            "FilledAmount": 0,
        }
        result = adapter.get_order_status("abc123")
        assert isinstance(result, OrderResult)
        assert result.order_id == "abc123"
        assert result.status == "Submitted"  # 'Working' → 'Submitted'

    def test_open_orders_returns_list_of_order_results(self, adapter, mock_saxo):
        mock_saxo.get_open_orders.return_value = [
            {"OrderId": "1", "Status": "Working", "FilledAmount": 0},
            {"OrderId": "2", "Status": "PartiallyFilled", "FilledAmount": 3},
        ]
        orders = adapter.get_open_orders()
        assert len(orders) == 2
        assert orders[0].status == "Submitted"
        assert orders[1].status == "PartiallyFilled"
        assert orders[1].filled_qty == 3

    def test_filled_amount_default_zero(self, adapter, mock_saxo):
        mock_saxo.get_order_status.return_value = {
            "OrderId": "abc", "Status": "Submitted",
            # No FilledAmount
        }
        result = adapter.get_order_status("abc")
        assert result.filled_qty == 0

    def test_external_reference_as_order_id_fallback(self, adapter, mock_saxo):
        """Some Saxo endpoints return ExternalReference instead of OrderId."""
        mock_saxo.get_order_status.return_value = {
            "ExternalReference": "ext_42",
            "Status": "Submitted",
        }
        assert adapter.get_order_status("ext_42").order_id == "ext_42"


class TestCancelOrder:
    def test_returns_true_on_success(self, adapter, mock_saxo):
        mock_saxo.cancel_order.return_value = {"OrderId": "abc", "Status": "Cancelled"}
        assert adapter.cancel_order("abc") is True
        mock_saxo.cancel_order.assert_called_once_with("abc")

    def test_returns_false_on_none(self, adapter, mock_saxo):
        mock_saxo.cancel_order.return_value = None
        assert adapter.cancel_order("abc") is False

    def test_returns_false_on_exception(self, adapter, mock_saxo):
        """Cancel failures must NOT propagate — log + False instead so
        callers (stop-out, reconcile) can retry with their own policy."""
        mock_saxo.cancel_order.side_effect = Exception("net down")
        assert adapter.cancel_order("abc") is False


# ─── FX rate ────────────────────────────────────────────────────────────────


class TestGetFxRate:
    def test_delegates(self, adapter, mock_saxo):
        mock_saxo.get_fx_rate.return_value = 1.085
        assert adapter.get_fx_rate("EUR", "USD") == 1.085

    def test_wraps_exception_as_broker_error(self, adapter, mock_saxo):
        mock_saxo.get_fx_rate.side_effect = Exception("rate feed down")
        with pytest.raises(BrokerError, match="get_fx_rate"):
            adapter.get_fx_rate("EUR", "USD")


# ─── Deferred stubs ─────────────────────────────────────────────────────────


class TestSymbolRegistry:
    """Phase B.2.b — register_underlying() unlocks option_chain,
    chart_data, place_iron_condor, place_vertical_spread."""

    def test_unregistered_symbol_raises_broker_error_on_chain(self, adapter):
        with pytest.raises(BrokerError, match="not registered"):
            adapter.get_option_chain("UNKNOWN", date(2026, 5, 16))

    def test_unregistered_symbol_raises_on_chart(self, adapter):
        with pytest.raises(BrokerError, match="not registered"):
            adapter.get_chart_data("UNKNOWN")

    def test_register_uppercases_symbol(self, adapter):
        adapter.register_underlying("spx", 6469910, 128)
        # Now lookups under "SPX" work without re-uppercasing at the call site
        adapter._registry_entry("SPX")
        adapter._registry_entry("spx")
        adapter._registry_entry("sPx")


class TestGetOptionChain:
    def test_returns_sorted_unique_strikes(self, adapter, mock_saxo):
        adapter.register_underlying("SPX", 6469910, 128)
        mock_saxo.get_option_chain.return_value = [
            {"Strike": 5400},
            {"Strike": 5500},
            {"Strike": 5500},   # duplicate
            {"StrikePrice": 5450},  # alternate key
        ]
        strikes = adapter.get_option_chain("SPX", date(2026, 5, 16))
        assert strikes == [5400.0, 5450.0, 5500.0]

    def test_empty_response_returns_empty(self, adapter, mock_saxo):
        adapter.register_underlying("SPX", 6469910, 128)
        mock_saxo.get_option_chain.return_value = None
        assert adapter.get_option_chain("SPX", date(2026, 5, 16)) == []


class TestGetChartData:
    def test_delegates_with_registered_uic(self, adapter, mock_saxo):
        adapter.register_underlying("SPX", 6469910, 128)
        bars = [{"t": 1, "o": 100, "h": 101, "l": 99, "c": 100.5}]
        mock_saxo.get_chart_data.return_value = bars
        out = adapter.get_chart_data("SPX", bar="1min", period="1d")
        assert out == bars
        kw = mock_saxo.get_chart_data.call_args.kwargs
        assert kw["uic"] == 6469910
        assert kw["asset_type"] == "StockIndexOption"


class TestPlaceIronCondor:
    @pytest.fixture
    def ic_req(self):
        return IronCondorRequest(
            expiry=date.today(),  # 0DTE
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=1, net_credit_limit=0.30,
        )

    def test_resolves_uics_and_submits_4_legs(self, adapter, mock_saxo, ic_req):
        adapter.register_underlying("SPX", 6469910, 128)
        mock_saxo.find_iron_fly_options.return_value = {
            "short_call": {"uic": 111},
            "long_call":  {"uic": 222},
            "short_put":  {"uic": 333},
            "long_put":   {"uic": 444},
        }
        mock_saxo.place_multi_leg_order.return_value = {
            "OrderId": "combo_42", "Status": "Working",
        }
        result = adapter.place_iron_condor(ic_req)
        assert isinstance(result, OrderResult)
        assert result.order_id == "combo_42"
        assert result.status == "Submitted"  # 'Working' normalized
        assert result.is_combo is True
        # 4 legs submitted with correct directions
        legs = mock_saxo.place_multi_leg_order.call_args.kwargs["legs"]
        assert len(legs) == 4
        # Longs are buys, shorts are sells
        buys = [l for l in legs if l["buy_sell"] == "Buy"]
        sells = [l for l in legs if l["buy_sell"] == "Sell"]
        assert len(buys) == 2 and len(sells) == 2
        assert {l["uic"] for l in buys} == {222, 444}   # long_call + long_put
        assert {l["uic"] for l in sells} == {111, 333}  # short_call + short_put

    def test_unregistered_symbol_raises_broker_error(self, adapter, ic_req):
        with pytest.raises(BrokerError, match="not registered"):
            adapter.place_iron_condor(ic_req)

    def test_find_iron_fly_options_none_raises(self, adapter, mock_saxo, ic_req):
        adapter.register_underlying("SPX", 6469910, 128)
        mock_saxo.find_iron_fly_options.return_value = None
        with pytest.raises(BrokerError, match="no UICs"):
            adapter.place_iron_condor(ic_req)

    def test_place_multi_leg_order_none_raises(self, adapter, mock_saxo, ic_req):
        adapter.register_underlying("SPX", 6469910, 128)
        mock_saxo.find_iron_fly_options.return_value = {
            "short_call": {"uic": 1}, "long_call": {"uic": 2},
            "short_put": {"uic": 3}, "long_put": {"uic": 4},
        }
        mock_saxo.place_multi_leg_order.return_value = None
        with pytest.raises(BrokerError, match="order rejected"):
            adapter.place_iron_condor(ic_req)

    def test_per_leg_response_captured_when_present(self, adapter, mock_saxo, ic_req):
        adapter.register_underlying("SPX", 6469910, 128)
        mock_saxo.find_iron_fly_options.return_value = {
            "short_call": {"uic": 1}, "long_call": {"uic": 2},
            "short_put": {"uic": 3}, "long_put": {"uic": 4},
        }
        mock_saxo.place_multi_leg_order.return_value = {
            "OrderId": "combo_42", "Status": "Working",
            "Orders": [
                {"OrderId": "leg_1", "Status": "Working"},
                {"OrderId": "leg_2", "Status": "Working"},
                {"OrderId": "leg_3", "Status": "Working"},
                {"OrderId": "leg_4", "Status": "Working"},
            ],
        }
        result = adapter.place_iron_condor(ic_req)
        assert len(result.legs) == 4
        assert result.legs[0].order_id == "leg_1"


class TestPlaceVerticalSpreadStillDeferred:
    """Phase B.4 follow-up — Saxo's symbol→UIC lookup for 2-leg verticals
    needs a strike-pair API that doesn't exist as a 1:1 helper. Pinning
    the current contract so callers know it's not silently broken."""

    def test_raises_broker_error_with_next_step(self, adapter):
        adapter.register_underlying("SPX", 6469910, 128)
        req = VerticalSpreadRequest(
            expiry=date(2026, 5, 16),
            short_strike=5500, long_strike=5505, right="C",
            contracts=1, net_credit_limit=0.30,
        )
        with pytest.raises(BrokerError, match="find_strangle_options"):
            adapter.place_vertical_spread(req)


class TestWhatIfReturnsSentinel:
    """Saxo has no native what_if. We return a self-describing sentinel
    dict so callers can detect the no-op explicitly."""

    def test_returns_sentinel_dict(self, adapter):
        req = IronCondorRequest(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=1, net_credit_limit=0.30,
        )
        result = adapter.what_if_iron_condor(req)
        assert isinstance(result, dict)
        assert result["_status"] == "not_supported_on_saxo"
        assert "_reason" in result
