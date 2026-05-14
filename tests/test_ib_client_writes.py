"""Tests for IBClient write methods (Phase A.4).

Covers: build_ic_conidex, build_vertical_conidex, _round_to_increment,
place_iron_condor, place_vertical_spread, place_order, place_market_order,
cancel_order, modify_order, what_if_order.

All tests use mocked IbkrClient — no live IBKR calls.
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
    DEFAULT_ORDER_ANSWERS,
    SPREAD_TEMPLATE_CONID,
    IBClient,
    IBClientError,
    IBConfig,
)
from shared.ib_oauth import IBKRCredentials


# ─── Fixtures (shared with reads test module pattern) ───────────────────────


@pytest.fixture(scope="module")
def real_dh_path(tmp_path_factory):
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
    mock_ibkr = MagicMock()
    auth_status = MagicMock()
    auth_status.data = {"authenticated": True, "connected": True, "competing": False}
    mock_ibkr.authentication_status.return_value = auth_status
    portfolio_result = MagicMock()
    portfolio_result.data = [{"accountId": "DU1234567"}]
    mock_ibkr.portfolio_accounts.return_value = portfolio_result

    with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr):
        client = IBClient(IBConfig(credentials=paper_creds))
        client.connect()
    return client, mock_ibkr


def _mk_result(data):
    r = MagicMock()
    r.data = data
    r.error = None
    return r


def _mock_conid_resolution(mock_ibkr, conids_in_order: list[int]):
    """Set up mock_ibkr to return the given conids for sequential
    qualify_contract calls (each call has 2 sub-calls: search_contract +
    search_secdef_info for options). Used to pre-populate the cache so
    place_iron_condor's 4 conid lookups produce known values."""
    # First step: search_contract_by_symbol — always returns underlying SPX
    mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
    # Second step: search_secdef_info_by_conid — return the conids in order
    secdef_results = [
        _mk_result([{"conid": c, "tradingClass": "SPXW"}])
        for c in conids_in_order
    ]
    mock_ibkr.search_secdef_info_by_conid.side_effect = secdef_results


# ─── Conidex builders ──────────────────────────────────────────────────────


class TestBuildICConidex:
    def test_format_matches_ibkr_spec(self):
        # Per research_scratch/09: format is
        # "28812380;;;{sc}/-1,{lc}/1,{sp}/-1,{lp}/1"
        result = IBClient.build_ic_conidex(100, 101, 200, 201)
        assert result == f"{SPREAD_TEMPLATE_CONID};;;100/-1,101/1,200/-1,201/1"

    def test_three_semicolons_exactly(self):
        result = IBClient.build_ic_conidex(1, 2, 3, 4)
        assert result.count(";") == 3
        # The triple-semicolon comes right after the template conid
        assert result.startswith(f"{SPREAD_TEMPLATE_CONID};;;")

    def test_short_legs_have_negative_ratio(self):
        """SHORT call + SHORT put = negative ratios (-1)."""
        result = IBClient.build_ic_conidex(
            short_call_conid=100, long_call_conid=101,
            short_put_conid=200, long_put_conid=201,
        )
        # Short legs (100, 200) have /-1; long legs (101, 201) have /1
        assert "100/-1" in result
        assert "200/-1" in result
        assert "101/1" in result
        assert "201/1" in result


class TestBuildVerticalConidex:
    def test_format(self):
        result = IBClient.build_vertical_conidex(100, 101)
        assert result == f"{SPREAD_TEMPLATE_CONID};;;100/-1,101/1"

    def test_one_short_one_long(self):
        # Short leg always has -1
        result = IBClient.build_vertical_conidex(999, 1000)
        assert "999/-1" in result
        assert "1000/1" in result


# ─── Price rounding ────────────────────────────────────────────────────────


class TestRoundToIncrement:
    def test_rounds_to_nickel(self):
        # $0.327 → $0.35 (rounded to nearest $0.05)
        assert IBClient._round_to_increment(0.327) == pytest.approx(0.35)

    def test_already_on_grid_unchanged(self):
        assert IBClient._round_to_increment(0.30) == pytest.approx(0.30)
        assert IBClient._round_to_increment(0.05) == pytest.approx(0.05)

    def test_rounds_down(self):
        assert IBClient._round_to_increment(0.07) == pytest.approx(0.05)

    def test_rounds_up(self):
        assert IBClient._round_to_increment(0.08) == pytest.approx(0.10)

    def test_custom_increment(self):
        assert IBClient._round_to_increment(0.123, 0.01) == pytest.approx(0.12)


# ─── place_iron_condor ─────────────────────────────────────────────────────


class TestPlaceIronCondor:
    def test_resolves_4_conids_and_builds_correct_conidex(self, connected_client):
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [1001, 1002, 2001, 2002])
        mock_ibkr.place_order.return_value = _mk_result([{
            "order_id": "abc123", "order_status": "PreSubmitted",
        }])

        result = client.place_iron_condor(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=0.30,
        )
        # Verify ibind got an OrderRequest with the right conidex
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        assert order_req.sec_type == "BAG"
        assert order_req.side == "SELL"
        assert order_req.order_type == "LMT"
        assert order_req.quantity == 10
        expected_conidex = f"{SPREAD_TEMPLATE_CONID};;;1001/-1,1002/1,2001/-1,2002/1"
        assert order_req.conidex == expected_conidex

    def test_rounds_credit_to_005_increment(self, connected_client):
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [1, 2, 3, 4])
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_iron_condor(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=0.327,  # off-grid
        )
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        assert order_req.price == pytest.approx(0.35)  # rounded UP

    def test_positive_price_for_short_combo(self, connected_client):
        """SHORT IC = SELL side + POSITIVE price = credit received.

        This is IBKR's counter-intuitive convention. If we get the sign wrong
        the order would be interpreted as "willing to pay $0.30 to sell" =
        immediate fill at any price = bad.
        """
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [1, 2, 3, 4])
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_iron_condor(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=0.30,
        )
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        assert order_req.side == "SELL"
        assert order_req.price > 0  # POSITIVE credit

    def test_uses_default_order_answers(self, connected_client):
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [1, 2, 3, 4])
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_iron_condor(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=0.30,
        )
        answers = mock_ibkr.place_order.call_args.kwargs["answers"]
        assert answers is DEFAULT_ORDER_ANSWERS

    def test_custom_answers_override(self, connected_client):
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [1, 2, 3, 4])
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        custom = {"WhateverPrompt": True}
        client.place_iron_condor(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=0.30,
            answers=custom,
        )
        answers = mock_ibkr.place_order.call_args.kwargs["answers"]
        assert answers is custom

    def test_passes_account_id(self, connected_client):
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [1, 2, 3, 4])
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_iron_condor(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=0.30,
        )
        assert mock_ibkr.place_order.call_args.kwargs["account_id"] == "DU1234567"

    def test_returns_order_dict(self, connected_client):
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [1, 2, 3, 4])
        mock_ibkr.place_order.return_value = _mk_result([{
            "order_id": "ABC", "order_status": "PreSubmitted", "local_order_id": "loc1",
        }])

        result = client.place_iron_condor(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=0.30,
        )
        assert result["order_id"] == "ABC"


# ─── place_vertical_spread ─────────────────────────────────────────────────


class TestPlaceVerticalSpread:
    def test_call_spread(self, connected_client):
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [5500, 5505])
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_vertical_spread(
            expiry=date(2026, 5, 16),
            short_strike=5500, long_strike=5505,
            right="C", contracts=10, net_credit_limit=0.20,
        )
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        assert order_req.conidex == f"{SPREAD_TEMPLATE_CONID};;;5500/-1,5505/1"
        assert order_req.side == "SELL"

    def test_invalid_right_raises(self, connected_client):
        client, _ = connected_client
        with pytest.raises(IBClientError, match="right must"):
            client.place_vertical_spread(
                expiry=date(2026, 5, 16),
                short_strike=5500, long_strike=5505,
                right="X", contracts=10, net_credit_limit=0.20,
            )

    def test_invalid_action_raises(self, connected_client):
        client, _ = connected_client
        with pytest.raises(IBClientError, match="action must"):
            client.place_vertical_spread(
                expiry=date(2026, 5, 16),
                short_strike=5500, long_strike=5505,
                right="C", contracts=10, net_credit_limit=0.20,
                action="HOLD",
            )

    def test_close_action_uses_buy_side(self, connected_client):
        """Closing a short spread = BUYing back the spread we sold."""
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [5500, 5505])
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_vertical_spread(
            expiry=date(2026, 5, 16),
            short_strike=5500, long_strike=5505,
            right="C", contracts=10, net_credit_limit=0.10,  # debit to close
            action="BUY",
        )
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        assert order_req.side == "BUY"


# ─── place_order (single leg) ──────────────────────────────────────────────


class TestPlaceOrder:
    def test_limit_order(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_order(
            conid=12345, side="BUY", quantity=10,
            order_type="LMT", price=5.30,
        )
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        assert order_req.conid == 12345
        assert order_req.side == "BUY"
        assert order_req.quantity == 10
        assert order_req.order_type == "LMT"
        assert order_req.price == pytest.approx(5.30)

    def test_limit_requires_price(self, connected_client):
        client, _ = connected_client
        with pytest.raises(IBClientError, match="LMT order requires price"):
            client.place_order(conid=1, side="BUY", quantity=1, order_type="LMT")

    def test_invalid_side(self, connected_client):
        client, _ = connected_client
        with pytest.raises(IBClientError, match="side must"):
            client.place_order(conid=1, side="HODL", quantity=1, order_type="LMT", price=1.0)

    def test_market_order_no_price_required(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_order(
            conid=12345, side="SELL", quantity=10, order_type="MKT",
        )
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        assert order_req.order_type == "MKT"
        assert order_req.price is None


class TestPlaceMarketOrder:
    def test_wraps_place_order_with_mkt(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_market_order(conid=12345, side="SELL", quantity=5)
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        assert order_req.order_type == "MKT"
        assert order_req.price is None


# ─── cancel_order ──────────────────────────────────────────────────────────


class TestCancelOrder:
    def test_returns_true_on_success(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.cancel_order.return_value = _mk_result({"cancelled": True})
        assert client.cancel_order("abc123") is True
        mock_ibkr.cancel_order.assert_called_with(order_id="abc123", account_id="DU1234567")

    def test_returns_false_on_ibind_error(self, connected_client):
        client, mock_ibkr = connected_client
        result = MagicMock()
        result.data = None
        result.error = "order already filled"
        mock_ibkr.cancel_order.return_value = result
        assert client.cancel_order("abc123") is False


# ─── modify_order ──────────────────────────────────────────────────────────


class TestModifyOrder:
    def test_requires_price_or_quantity(self, connected_client):
        client, _ = connected_client
        with pytest.raises(IBClientError, match="at least price or quantity"):
            client.modify_order("abc123")

    def test_price_modification(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.modify_order.return_value = _mk_result({"order_id": "abc123"})

        client.modify_order("abc123", price=0.40)
        # ibind got an OrderRequest with the new (rounded) price
        order_req = mock_ibkr.modify_order.call_args.kwargs["order_request"]
        assert order_req.price == pytest.approx(0.40)

    def test_price_rounded_to_005(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.modify_order.return_value = _mk_result({"order_id": "abc123"})

        client.modify_order("abc123", price=0.327)
        order_req = mock_ibkr.modify_order.call_args.kwargs["order_request"]
        assert order_req.price == pytest.approx(0.35)


# ─── what_if_order ─────────────────────────────────────────────────────────


class TestWhatIfOrder:
    def test_returns_five_blocks(self, connected_client):
        """whatif response has amount/equity/initial/maintenance/position keys."""
        client, mock_ibkr = connected_client
        mock_ibkr.whatif_order.return_value = _mk_result({
            "amount": {"amount": "-300.00 EUR", "total": "0", "commission": "5"},
            "equity": {"current": "50000.0", "change": "-5", "after": "49995.0"},
            "initial": {"current": "0.0", "change": "+4500", "after": "4500.0"},
            "maintenance": {"current": "0.0", "change": "+4500", "after": "4500.0"},
            "position": {"current": "0", "change": "+10", "after": "10"},
        })
        from ibind import OrderRequest
        order = OrderRequest(
            conid=None, conidex="x;;;1/-1,2/1,3/-1,4/1",
            sec_type="BAG", side="SELL", quantity=10,
            order_type="LMT", price=0.30, acct_id="DU1234567",
        )
        wif = client.what_if_order(order)
        assert "initial" in wif
        assert wif["initial"]["change"] == "+4500"

    def test_passes_account_id(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.whatif_order.return_value = _mk_result({})
        from ibind import OrderRequest
        order = OrderRequest(
            conid=12345, side="BUY", quantity=1, order_type="LMT",
            price=1.0, acct_id="DU1234567",
        )
        client.what_if_order(order)
        assert mock_ibkr.whatif_order.call_args.kwargs["account_id"] == "DU1234567"


# ─── DEFAULT_ORDER_ANSWERS sanity ──────────────────────────────────────────


class TestDefaultOrderAnswers:
    def test_has_all_known_question_types(self):
        """Sanity: every QuestionType ibind exposes should have a default.

        If a new QuestionType is added in a future ibind release, this test
        flags it so we explicitly choose Confirm/Deny.
        """
        from ibind import QuestionType
        known = set(QuestionType)
        defaults = set(DEFAULT_ORDER_ANSWERS.keys())
        missing = known - defaults
        # Allow some new ones to slip in without failing, but log them
        if missing:
            pytest.fail(
                f"New QuestionType members not in DEFAULT_ORDER_ANSWERS: {missing}. "
                f"Choose True (auto-confirm) or False (abort) for each."
            )

    def test_market_data_warning_is_DENY(self):
        """We always have OPRA. Refusing this prompt = abort the trade,
        which is safer than placing blind."""
        from ibind import QuestionType
        assert DEFAULT_ORDER_ANSWERS[QuestionType.MISSING_MARKET_DATA] is False

    def test_stop_order_warning_is_DENY(self):
        """We don't use native stop orders for 0DTE."""
        from ibind import QuestionType
        assert DEFAULT_ORDER_ANSWERS[QuestionType.STOP_ORDER_RISKS] is False
