"""Tests for IBClient read methods (Phase A.3).

Covers: qualify_contract, get_quote, get_quotes_batch, get_vix_price,
get_option_greeks, get_account_info, get_balance (EUR-base USD-tradable),
get_positions, get_fx_rate, get_option_chain, get_open_orders,
get_order_status, get_chart_data.

All tests use mocked IbkrClient — no live IBKR calls. The integration smoke
test (Phase A.10) is separate.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_client import (
    DEFAULT_GREEKS_FIELDS,
    DEFAULT_QUOTE_FIELDS,
    FIELD_BID,
    FIELD_ASK,
    FIELD_LAST,
    FIELD_DELTA,
    FIELD_IV,
    IBClient,
    IBClientError,
    IBConfig,
)
from shared.ib_oauth import IBKRCredentials


# ─── Shared fixtures ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_dh_path(tmp_path_factory):
    """Generate a 1024-bit DH params once for the whole module (slow)."""
    p = tmp_path_factory.mktemp("dh") / "dhparam.pem"
    subprocess.run(
        ["openssl", "dhparam", "-out", str(p), "1024"],
        check=True, capture_output=True,
    )
    return p


@pytest.fixture
def paper_creds(tmp_path, real_dh_path):
    sig = tmp_path / "private_signature.pem"
    enc = tmp_path / "private_encryption.pem"
    sig.write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n")
    enc.write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n")
    return IBKRCredentials(
        environment="paper", consumer_key="CALYPSOPP",
        access_token="t", access_token_secret="s",
        private_signature_path=sig, private_encryption_path=enc,
        dh_param_path=real_dh_path,
    )


@pytest.fixture
def connected_client(paper_creds):
    """An IBClient that's gone through a successful connect() with mocks."""
    mock_ibkr = MagicMock()
    auth_status = MagicMock()
    auth_status.data = {"authenticated": True, "connected": True, "competing": False}
    mock_ibkr.authentication_status.return_value = auth_status
    portfolio_result = MagicMock()
    portfolio_result.data = [{"accountId": "DU1234567"}]
    mock_ibkr.portfolio_accounts.return_value = portfolio_result

    cfg = IBConfig(credentials=paper_creds)
    with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr):
        client = IBClient(cfg)
        client.connect()
    # `client._client` now IS `mock_ibkr`. Return both for test convenience.
    return client, mock_ibkr


def _mk_result(data):
    """Build an ibind-like Result wrapper with .data + .error=None."""
    r = MagicMock()
    r.data = data
    r.error = None
    return r


# ─── qualify_contract ──────────────────────────────────────────────────────


class TestQualifyContract:
    def test_underlying_index_resolved(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result(
            [{"conid": 416904}]  # SPX index conid (approximate; actual varies)
        )
        conid = client.qualify_contract("SPX", sec_type="IND")
        assert conid == 416904

    def test_caches_repeated_calls(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 12345}])
        client.qualify_contract("VIX", sec_type="IND")
        client.qualify_contract("VIX", sec_type="IND")
        client.qualify_contract("VIX", sec_type="IND")
        # First call hits ibind; cache supplies the rest
        assert mock_ibkr.search_contract_by_symbol.call_count == 1

    def test_option_walks_secdef_chain(self, connected_client):
        client, mock_ibkr = connected_client
        # Step 1: underlying lookup
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        # Step 2: secdef chain
        mock_ibkr.search_secdef_info_by_conid.return_value = _mk_result(
            [{"conid": 999111, "tradingClass": "SPXW"}]
        )
        conid = client.qualify_contract(
            "SPX", expiry=date(2026, 5, 16), strike=5500, right="C",
            trading_class="SPXW",
        )
        assert conid == 999111
        mock_ibkr.search_secdef_info_by_conid.assert_called_once()
        args = mock_ibkr.search_secdef_info_by_conid.call_args.kwargs
        assert args["sec_type"] == "OPT"
        assert args["strike"] == "5500"
        assert args["right"] == "C"
        assert args["exchange"] == "CBOE"

    def test_option_filters_by_trading_class(self, connected_client):
        """Both SPXW and SPX show up at the strike; we filter to SPXW for 0DTE."""
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        mock_ibkr.search_secdef_info_by_conid.return_value = _mk_result([
            {"conid": 111, "tradingClass": "SPX"},   # NOT 0DTE
            {"conid": 222, "tradingClass": "SPXW"},  # 0DTE
        ])
        conid = client.qualify_contract(
            "SPX", expiry=date(2026, 5, 16), strike=5500, right="C",
            trading_class="SPXW",
        )
        assert conid == 222

    def test_option_missing_trading_class_raises(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        mock_ibkr.search_secdef_info_by_conid.return_value = _mk_result(
            [{"conid": 111, "tradingClass": "SPX"}]  # monthly only
        )
        with pytest.raises(IBClientError, match="No SPXW option"):
            client.qualify_contract(
                "SPX", expiry=date(2026, 5, 16), strike=5500, right="C",
                trading_class="SPXW",
            )

    def test_unknown_symbol_raises(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([])
        with pytest.raises(IBClientError, match="No contract found"):
            client.qualify_contract("ZZZNONE", sec_type="IND")

    def test_unconnected_raises(self, paper_creds):
        client = IBClient(IBConfig(credentials=paper_creds))
        with pytest.raises(IBClientError, match="not connected"):
            client.qualify_contract("SPX", sec_type="IND")


# ─── Quotes ────────────────────────────────────────────────────────────────


class TestGetQuote:
    def test_returns_parsed_quote(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{
            "conid": 12345,
            FIELD_BID: "5.20", FIELD_ASK: "5.40", FIELD_LAST: "5.30",
            "6509": "R",  # availability
        }])
        q = client.get_quote(12345)
        assert q["bid"] == 5.20
        assert q["ask"] == 5.40
        assert q["last"] == 5.30
        assert q["mid"] == pytest.approx(5.30)  # (5.20 + 5.40) / 2 has FP rounding
        assert q["availability"] == "R"
        assert q["conid"] == 12345

    def test_missing_fields_become_none(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{
            "conid": 12345,
            FIELD_BID: "",  # IBKR sometimes returns empty string
            # last/ask absent entirely
        }])
        q = client.get_quote(12345)
        assert q["bid"] is None
        assert q["ask"] is None
        assert q["last"] is None
        assert q["mid"] is None  # can't compute without both sides

    def test_custom_fields_passed_through(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{}])
        client.get_quote(99, fields=[FIELD_DELTA, FIELD_IV])
        call = mock_ibkr.live_marketdata_snapshot.call_args
        assert call.kwargs["fields"] == [FIELD_DELTA, FIELD_IV]


class TestGetQuotesBatch:
    def test_batches_up_to_100(self, connected_client):
        client, mock_ibkr = connected_client
        conids = list(range(100))
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result(
            [{"conid": c, FIELD_BID: "1", FIELD_ASK: "2"} for c in conids]
        )
        rows = client.get_quotes_batch(conids)
        assert len(rows) == 100
        call = mock_ibkr.live_marketdata_snapshot.call_args
        # conids passed as comma-joined string
        assert "," in call.kwargs["conids"]
        assert call.kwargs["conids"].count(",") == 99  # 100 entries

    def test_more_than_100_rejected(self, connected_client):
        client, _ = connected_client
        with pytest.raises(IBClientError, match="max 100"):
            client.get_quotes_batch(list(range(101)))

    def test_empty_returns_empty_no_api_call(self, connected_client):
        client, mock_ibkr = connected_client
        assert client.get_quotes_batch([]) == []
        mock_ibkr.live_marketdata_snapshot.assert_not_called()


class TestGetVixPrice:
    def test_returns_mid_when_available(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 13455}])
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{
            "conid": 13455, FIELD_BID: "18.0", FIELD_ASK: "18.2",
        }])
        assert client.get_vix_price() == 18.1

    def test_falls_back_to_last_when_no_bid_ask(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 13455}])
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{
            "conid": 13455, FIELD_LAST: "17.95",
        }])
        assert client.get_vix_price() == 17.95


class TestGetOptionGreeks:
    def test_returns_full_greeks(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{
            "conid": 999, FIELD_BID: "0.30", FIELD_ASK: "0.40",
            "7308": "0.08", "7309": "0.012", "7310": "-0.045", "7311": "0.085",
            "7633": "0.14", "7638": "1234",
        }])
        g = client.get_option_greeks(999)
        assert g["delta"] == 0.08
        assert g["gamma"] == 0.012
        assert g["theta"] == -0.045
        assert g["vega"] == 0.085
        assert g["iv"] == 0.14
        assert g["open_interest"] == 1234
        # Also has the quote fields
        assert g["bid"] == 0.30
        assert g["ask"] == 0.40
        assert g["mid"] == 0.35

    def test_uses_default_option_quote_fields(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{}])
        client.get_option_greeks(123)
        call = mock_ibkr.live_marketdata_snapshot.call_args
        for f in DEFAULT_GREEKS_FIELDS:
            assert f in call.kwargs["fields"]


# ─── Account / portfolio ───────────────────────────────────────────────────


class TestGetAccountInfo:
    def test_passes_account_id(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.portfolio_account_information.return_value = _mk_result({
            "accountId": "DU1234567", "type": "DEMO",
        })
        info = client.get_account_info()
        assert info["accountId"] == "DU1234567"
        mock_ibkr.portfolio_account_information.assert_called_with(account_id="DU1234567")


class TestGetBalance:
    def test_eur_base_usd_tradable_computation(self, connected_client):
        """The headline EUR-base case from research_scratch/11."""
        client, mock_ibkr = connected_client
        mock_ibkr.portfolio_summary.return_value = _mk_result({
            "availablefunds": {"amount": "50000.0", "currency": "EUR"},
        })
        mock_ibkr.get_ledger.return_value = _mk_result({
            "EUR": {"cashbalance": 50000.0, "isbase": True, "exchangerate": 1.0},
            "USD": {"cashbalance": 1000.0, "isbase": False, "exchangerate": 0.92},
        })
        bal = client.get_balance("USD")
        # tradable = 50000 / 0.92 + 1000 = ~55,348.6
        assert bal["currency"] == "USD"
        assert bal["base_currency"] == "EUR"
        assert bal["base_available"] == 50000.0
        assert bal["exchange_rate"] == 0.92
        assert bal["cash_in_target"] == 1000.0
        assert abs(bal["tradable"] - (50000.0 / 0.92 + 1000.0)) < 0.01

    def test_same_currency_no_conversion(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.portfolio_summary.return_value = _mk_result({
            "availablefunds": {"amount": "50000.0", "currency": "EUR"},
        })
        mock_ibkr.get_ledger.return_value = _mk_result({
            "EUR": {"cashbalance": 50000.0, "isbase": True, "exchangerate": 1.0},
        })
        bal = client.get_balance("EUR")
        assert bal["tradable"] == 50000.0
        assert bal["exchange_rate"] == 1.0


class TestGetPositions:
    def test_returns_flat_list_across_pages(self, connected_client):
        client, mock_ibkr = connected_client
        # Page 0: 30 entries (full page → fetch next)
        page_0 = [{"conid": i} for i in range(30)]
        # Page 1: 5 entries (partial → done)
        page_1 = [{"conid": i} for i in range(30, 35)]
        mock_ibkr.positions.side_effect = [
            _mk_result(page_0),
            _mk_result(page_1),
        ]
        all_pos = client.get_positions()
        assert len(all_pos) == 35
        assert all_pos[0]["conid"] == 0
        assert all_pos[-1]["conid"] == 34

    def test_empty_account(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.positions.return_value = _mk_result([])
        assert client.get_positions() == []


class TestGetFxRate:
    def test_returns_rate_from_dict(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.currency_exchange_rate.return_value = _mk_result({"rate": 1.085})
        assert client.get_fx_rate("EUR", "USD") == 1.085

    def test_returns_rate_from_scalar(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.currency_exchange_rate.return_value = _mk_result(1.085)
        assert client.get_fx_rate("EUR", "USD") == 1.085


# ─── Options chain ─────────────────────────────────────────────────────────


class TestGetOptionChain:
    def test_returns_union_of_call_put_strikes(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        mock_ibkr.search_strikes_by_conid.return_value = _mk_result({
            "call": [5500, 5510, 5520],
            "put":  [5510, 5520, 5530],  # overlapping + extending
        })
        strikes = client.get_option_chain("SPX", date(2026, 5, 16))
        assert strikes == [5500.0, 5510.0, 5520.0, 5530.0]

    def test_handles_list_response_shape(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        mock_ibkr.search_strikes_by_conid.return_value = _mk_result([5500, 5510])
        strikes = client.get_option_chain("SPX", date(2026, 5, 16))
        assert strikes == [5500.0, 5510.0]

    def test_passes_correct_month_format(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        mock_ibkr.search_strikes_by_conid.return_value = _mk_result({"call": [], "put": []})
        client.get_option_chain("SPX", date(2026, 5, 16))
        call = mock_ibkr.search_strikes_by_conid.call_args
        # IBKR expects 'MAY26' style
        assert call.kwargs["month"] == "MAY26"
        assert call.kwargs["exchange"] == "CBOE"


# ─── Orders (read) ─────────────────────────────────────────────────────────


class TestGetOpenOrders:
    def test_unwraps_orders_field(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.live_orders.return_value = _mk_result({"orders": [
            {"orderId": "1", "status": "Submitted"},
            {"orderId": "2", "status": "PreSubmitted"},
        ]})
        orders = client.get_open_orders()
        assert len(orders) == 2
        assert orders[0]["orderId"] == "1"

    def test_empty_account(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.live_orders.return_value = _mk_result({"orders": []})
        assert client.get_open_orders() == []


class TestGetOrderStatus:
    def test_passes_order_id(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.order_status.return_value = _mk_result({
            "orderId": "abc123", "status": "Filled", "filled": 10,
        })
        s = client.get_order_status("abc123")
        assert s["status"] == "Filled"
        mock_ibkr.order_status.assert_called_with(order_id="abc123")


# ─── Historical bars ───────────────────────────────────────────────────────


class TestGetChartData:
    def test_returns_bars(self, connected_client):
        client, mock_ibkr = connected_client
        bars = [{"t": 1, "o": 100, "h": 101, "l": 99, "c": 100.5}]
        mock_ibkr.marketdata_history_by_symbol.return_value = _mk_result({"data": bars})
        out = client.get_chart_data("SPX", bar="1min", period="1d")
        assert out == bars

    def test_default_args(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.marketdata_history_by_symbol.return_value = _mk_result({"data": []})
        client.get_chart_data("SPX")
        kw = mock_ibkr.marketdata_history_by_symbol.call_args.kwargs
        assert kw["bar"] == "1min"
        assert kw["period"] == "1d"
        assert kw["outside_rth"] is False
