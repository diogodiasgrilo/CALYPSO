"""Tests for shared/broker/ibkr_adapter.py — Phase B.3.

IBBrokerAdapter wraps IBClient — every method is a thin delegation plus
shape translation. Tests mock IBClient at the boundary so this is a
pure-unit suite, no live broker calls.
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
    BrokerConnectionError,
    BrokerError,
    BrokerInterface,
    IronCondorRequest,
    OrderResult,
    Position,
    QuoteSnapshot,
    VerticalSpreadRequest,
)
from shared.broker.ibkr_adapter import IBBrokerAdapter, _normalize_status


@pytest.fixture
def mock_ib():
    m = MagicMock()
    m.account_id = "DU1234567"
    return m


@pytest.fixture
def adapter(mock_ib):
    return IBBrokerAdapter(mock_ib)


# ─── Contract: subclass of BrokerInterface ──────────────────────────────────


class TestContract:
    def test_is_broker_interface_subclass(self):
        assert issubclass(IBBrokerAdapter, BrokerInterface)

    def test_constructable_with_mock(self, mock_ib):
        # If any @abstractmethod is missing, this raises TypeError.
        IBBrokerAdapter(mock_ib)

    def test_ib_escape_hatch(self, adapter, mock_ib):
        assert adapter.ib is mock_ib


# ─── Status normalization ───────────────────────────────────────────────────


class TestStatusNormalize:
    def test_canonical_vocabulary(self):
        assert _normalize_status("PreSubmitted") == "PreSubmitted"
        assert _normalize_status("Submitted") == "Submitted"
        assert _normalize_status("Filled") == "Filled"
        assert _normalize_status("PartiallyFilled") == "PartiallyFilled"
        assert _normalize_status("Cancelled") == "Cancelled"
        assert _normalize_status("Rejected") == "Rejected"
        assert _normalize_status("Expired") == "Expired"

    def test_apicancelled_normalizes_to_cancelled(self):
        assert _normalize_status("ApiCancelled") == "Cancelled"
        assert _normalize_status("api_cancelled") == "Cancelled"

    def test_inactive_normalizes_to_cancelled(self):
        assert _normalize_status("Inactive") == "Cancelled"

    def test_case_insensitive(self):
        assert _normalize_status("FILLED") == "Filled"
        assert _normalize_status("filled") == "Filled"

    def test_none_returns_unknown(self):
        assert _normalize_status(None) == "Unknown"


# ─── Lifecycle ──────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_connect_success(self, adapter, mock_ib):
        mock_ib.connect.return_value = True
        assert adapter.connect() is True

    def test_connect_ib_auth_error_becomes_broker_auth_error(self, adapter, mock_ib):
        from shared.ib_client import IBAuthError
        mock_ib.connect.side_effect = IBAuthError("invalid consumer")
        with pytest.raises(BrokerAuthError, match="invalid consumer"):
            adapter.connect()

    def test_connect_ib_connection_error_becomes_broker_connection_error(
        self, adapter, mock_ib,
    ):
        from shared.ib_client import IBConnectionError
        mock_ib.connect.side_effect = IBConnectionError("network down")
        with pytest.raises(BrokerConnectionError, match="network down"):
            adapter.connect()

    def test_connect_other_exception_becomes_broker_error(self, adapter, mock_ib):
        mock_ib.connect.side_effect = RuntimeError("bad state")
        with pytest.raises(BrokerError, match="bad state"):
            adapter.connect()

    def test_is_connected_swallows_attribute_error(self, adapter, mock_ib):
        mock_ib.is_connected.side_effect = AttributeError("renamed")
        assert adapter.is_connected() is False

    def test_disconnect_swallows_errors(self, adapter, mock_ib):
        mock_ib.disconnect.side_effect = Exception("net gone")
        adapter.disconnect()  # no raise


# ─── Quote mapping ──────────────────────────────────────────────────────────


class TestGetQuote:
    def test_maps_normalized_fields(self, adapter, mock_ib):
        mock_ib.get_quote.return_value = {
            "conid": 12345,
            "bid": 5.20, "ask": 5.40, "last": 5.30, "mid": 5.30, "mark": 5.30,
            "bid_size": 10, "ask_size": 12,
            "availability": "R",
        }
        snap = adapter.get_quote("12345")
        assert isinstance(snap, QuoteSnapshot)
        assert snap.instrument_id == "12345"
        assert snap.bid == 5.20
        assert snap.ask == 5.40
        assert snap.mid == 5.30
        assert snap.availability == "R"
        mock_ib.get_quote.assert_called_once_with(12345)  # int conversion

    def test_none_propagates(self, adapter, mock_ib):
        mock_ib.get_quote.return_value = None
        assert adapter.get_quote("12345") is None

    def test_exception_wrapped(self, adapter, mock_ib):
        mock_ib.get_quote.side_effect = Exception("snapshot timeout")
        with pytest.raises(BrokerError, match="get_quote"):
            adapter.get_quote("12345")


class TestGetQuotesBatch:
    def test_maps_each_conid(self, adapter, mock_ib):
        mock_ib.get_quotes_batch.return_value = [
            {"conid": 111, "bid": 1.0, "ask": 1.2},
            {"conid": 222, "bid": 2.0, "ask": 2.2},
        ]
        snaps = adapter.get_quotes_batch(["111", "222"])
        assert len(snaps) == 2
        assert {s.instrument_id for s in snaps} == {"111", "222"}


class TestGetOptionGreeks:
    def test_maps_greeks(self, adapter, mock_ib):
        mock_ib.get_option_greeks.return_value = {
            "conid": 12345,
            "bid": 5.20, "ask": 5.40,
            "delta": -0.42, "gamma": 0.08, "theta": -3.4, "vega": 1.2,
            "iv": 0.18, "open_interest": 5000,
        }
        snap = adapter.get_option_greeks("12345")
        assert snap.delta == -0.42
        assert snap.iv == 0.18
        assert snap.open_interest == 5000


class TestGetVixPrice:
    def test_delegates(self, adapter, mock_ib):
        mock_ib.get_vix_price.return_value = 18.5
        assert adapter.get_vix_price() == 18.5

    def test_returns_none_on_exception(self, adapter, mock_ib):
        mock_ib.get_vix_price.side_effect = Exception("VIX feed down")
        assert adapter.get_vix_price() is None


# ─── Account state ─────────────────────────────────────────────────────────


class TestAccount:
    def test_get_account_info(self, adapter, mock_ib):
        mock_ib.get_account_info.return_value = {"accountId": "DU1234567"}
        info = adapter.get_account_info()
        assert info == {"accountId": "DU1234567"}

    def test_get_account_info_none_returns_empty(self, adapter, mock_ib):
        mock_ib.get_account_info.return_value = None
        assert adapter.get_account_info() == {}

    def test_get_balance_delegates(self, adapter, mock_ib):
        mock_ib.get_balance.return_value = {
            "currency": "USD", "base_currency": "EUR", "tradable": 50000.0,
        }
        bal = adapter.get_balance("USD")
        assert bal["tradable"] == 50000.0
        mock_ib.get_balance.assert_called_once_with("USD")


class TestGetPositions:
    def test_long_position(self, adapter, mock_ib):
        mock_ib.get_positions.return_value = [{
            "conid": 416904, "ticker": "SPX",
            "position": 10, "avgCost": 5500.0,
            "unrealizedPnl": 200.0,
        }]
        positions = adapter.get_positions()
        assert len(positions) == 1
        p = positions[0]
        assert isinstance(p, Position)
        assert p.instrument_id == "416904"
        assert p.symbol == "SPX"
        assert p.quantity == 10
        assert p.side == "LONG"
        assert p.unrealized_pnl == 200.0

    def test_short_position(self, adapter, mock_ib):
        mock_ib.get_positions.return_value = [{
            "conid": 416904, "ticker": "SPX",
            "position": -5, "avgCost": 5500.0,
        }]
        p = adapter.get_positions()[0]
        assert p.side == "SHORT"
        assert p.quantity == 5

    def test_empty(self, adapter, mock_ib):
        mock_ib.get_positions.return_value = []
        assert adapter.get_positions() == []


# ─── Order management ──────────────────────────────────────────────────────


class TestOrderResultMapping:
    def test_to_order_result_normalizes_status(self, adapter, mock_ib):
        mock_ib.get_order_status.return_value = {
            "order_id": "abc",
            "status": "PreSubmitted",
            "filled": 0,
        }
        result = adapter.get_order_status("abc")
        assert isinstance(result, OrderResult)
        assert result.order_id == "abc"
        assert result.status == "PreSubmitted"

    def test_apicancelled_collapses_to_cancelled(self, adapter, mock_ib):
        mock_ib.get_order_status.return_value = {
            "order_id": "abc", "status": "ApiCancelled",
        }
        assert adapter.get_order_status("abc").status == "Cancelled"

    def test_legs_promoted_when_present(self, adapter, mock_ib):
        mock_ib.get_order_status.return_value = {
            "order_id": "head", "status": "Filled",
            "_legs": [
                {"order_id": "leg1", "status": "Filled"},
                {"order_id": "leg2", "status": "Filled"},
            ],
        }
        result = adapter.get_order_status("head")
        assert result.is_combo is True
        assert len(result.legs) == 2
        assert result.legs[0].order_id == "leg1"

    def test_open_orders(self, adapter, mock_ib):
        mock_ib.get_open_orders.return_value = [
            {"order_id": "1", "status": "Submitted"},
            {"order_id": "2", "status": "PartiallyFilled", "filled": 3},
        ]
        orders = adapter.get_open_orders()
        assert len(orders) == 2
        assert orders[1].filled_qty == 3


class TestCancelOrder:
    def test_returns_true_on_success(self, adapter, mock_ib):
        mock_ib.cancel_order.return_value = True
        assert adapter.cancel_order("abc") is True

    def test_returns_false_on_failure(self, adapter, mock_ib):
        mock_ib.cancel_order.return_value = False
        assert adapter.cancel_order("abc") is False

    def test_swallows_exception(self, adapter, mock_ib):
        mock_ib.cancel_order.side_effect = Exception("net down")
        assert adapter.cancel_order("abc") is False


# ─── what_if ────────────────────────────────────────────────────────────────


class TestWhatIf:
    def test_what_if_iron_condor_routes_through_ibclient(self, adapter, mock_ib):
        # Stub the conid resolution
        mock_ib.qualify_contract.side_effect = [111, 222, 333, 444]
        mock_ib.what_if_order.return_value = {
            "amount": "0.00", "equity": "+10,000", "initial": "+500",
            "maintenance": "+500", "position": "+1",
        }
        req = IronCondorRequest(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=1, net_credit_limit=0.30,
        )
        result = adapter.what_if_iron_condor(req)
        assert set(result.keys()) >= {"amount", "equity", "initial", "maintenance", "position"}
        # Verify the OrderRequest passed in had the right conidex shape
        order_req = mock_ib.what_if_order.call_args.args[0]
        assert order_req.conidex.startswith("28812380;;;")
        assert "111/-1" in order_req.conidex
        assert "222/1" in order_req.conidex

    def test_what_if_qualify_failure_wraps(self, adapter, mock_ib):
        mock_ib.qualify_contract.side_effect = Exception("unknown strike")
        req = IronCondorRequest(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=1, net_credit_limit=0.30,
        )
        with pytest.raises(BrokerError, match="what_if_iron_condor"):
            adapter.what_if_iron_condor(req)


# ─── Writes ────────────────────────────────────────────────────────────────


class TestPlaceIronCondor:
    @pytest.fixture
    def ic_req(self):
        return IronCondorRequest(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=1, net_credit_limit=0.30,
            coid="my_coid",
        )

    def test_delegates_to_ibclient(self, adapter, mock_ib, ic_req):
        mock_ib.place_iron_condor.return_value = {
            "order_id": "combo_42", "status": "Submitted",
        }
        result = adapter.place_iron_condor(ic_req)
        assert isinstance(result, OrderResult)
        assert result.order_id == "combo_42"
        assert result.is_combo is True
        # Confirm IronCondorRequest fields forwarded as kwargs
        kw = mock_ib.place_iron_condor.call_args.kwargs
        assert kw["short_call_strike"] == 5500
        assert kw["coid"] == "my_coid"
        assert kw["contracts"] == 1

    def test_exception_wrapped(self, adapter, mock_ib, ic_req):
        mock_ib.place_iron_condor.side_effect = Exception("price out of band")
        with pytest.raises(BrokerError, match="place_iron_condor"):
            adapter.place_iron_condor(ic_req)


class TestPlaceVerticalSpread:
    @pytest.fixture
    def vs_req(self):
        return VerticalSpreadRequest(
            expiry=date(2026, 5, 16),
            short_strike=5500, long_strike=5505, right="C",
            contracts=1, net_credit_limit=0.30,
        )

    def test_delegates(self, adapter, mock_ib, vs_req):
        mock_ib.place_vertical_spread.return_value = {
            "order_id": "vert_99", "status": "Submitted",
        }
        result = adapter.place_vertical_spread(vs_req)
        assert result.order_id == "vert_99"
        assert result.is_combo is True
        kw = mock_ib.place_vertical_spread.call_args.kwargs
        assert kw["right"] == "C"
        assert kw["short_strike"] == 5500
        assert kw["action"] == "SELL"

    def test_exception_wrapped(self, adapter, mock_ib, vs_req):
        mock_ib.place_vertical_spread.side_effect = Exception("market closed")
        with pytest.raises(BrokerError, match="place_vertical_spread"):
            adapter.place_vertical_spread(vs_req)


# ─── Re-export ──────────────────────────────────────────────────────────────


class TestPackageReExport:
    def test_top_level_import_resolves(self):
        from shared.broker import IBBrokerAdapter as IBA
        assert IBA is IBBrokerAdapter
