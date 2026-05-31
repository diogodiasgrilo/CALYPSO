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
    auth_status.error = None  # Phase A.8: _ib_call → _unwrap checks .error
    mock_ibkr.authentication_status.return_value = auth_status
    portfolio_result = MagicMock()
    portfolio_result.data = [{"accountId": "DU1234567"}]
    portfolio_result.error = None
    mock_ibkr.portfolio_accounts.return_value = portfolio_result
    # P7-audit M11: what_if_order now primes /iserver/accounts via
    # _ensure_iserver_primed (same preflight as snapshot). Configure the
    # mock so the priming call returns a clean Result.
    iserver_result = MagicMock()
    iserver_result.data = {}
    iserver_result.error = None
    mock_ibkr.receive_brokerage_accounts.return_value = iserver_result

    with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr):
        client = IBClient(IBConfig(credentials=paper_creds))
        client.connect()
    # Audit #20: qualify_* now FAIL CLOSED on a secdef row with no parseable
    # expiry. These order-construction tests use bare {conid,tradingClass} mocks
    # (they exercise conidex/pricing/coid, not expiry resolution), so enable the
    # test-only escape hatch. Expiry-resolution fail-closed is covered by the
    # dedicated tests in test_ib_client_reads.py against a flag-less client.
    client._allow_missing_expiry = True
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
        assert IBClient._round_to_increment(0.327, 0.05) == pytest.approx(0.35)

    def test_already_on_grid_unchanged(self):
        assert IBClient._round_to_increment(0.30, 0.05) == pytest.approx(0.30)
        assert IBClient._round_to_increment(0.05, 0.05) == pytest.approx(0.05)

    def test_rounds_down(self):
        assert IBClient._round_to_increment(0.07, 0.05) == pytest.approx(0.05)

    def test_rounds_up(self):
        assert IBClient._round_to_increment(0.08, 0.05) == pytest.approx(0.10)

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

    def test_negative_credit_input_propagates_without_flip(self, connected_client):
        """Defensive: a caller that mistakenly passes a NEGATIVE net_credit
        sends a NEGATIVE price to IBKR. For a SHORT IC, this would mean
        "I'll pay to sell" = immediate fill at any market price = blowup.

        IBClient does NOT silently flip the sign — it trusts the caller's
        intent and reflects whatever they gave (post-$0.05 rounding). This
        test pins that contract so future refactors don't accidentally
        add an abs() that would mask caller bugs upstream.
        """
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [1, 2, 3, 4])
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_iron_condor(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=-0.30,
        )
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        # _round_to_increment preserves sign
        assert order_req.price == pytest.approx(-0.30)
        # Side is still SELL — caller's bug surfaces as a misprice, not
        # a side flip.
        assert order_req.side == "SELL"

    def test_auto_generates_coid_when_omitted(self, connected_client):
        """When caller doesn't pass coid, IBClient mints one so the order
        is dedup-safe under retry. Format: 'CAL_' + hex prefix."""
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
        assert order_req.coid is not None
        assert order_req.coid.startswith("CAL_")

    def test_caller_coid_passes_through(self, connected_client):
        client, mock_ibkr = connected_client
        _mock_conid_resolution(mock_ibkr, [1, 2, 3, 4])
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_iron_condor(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=0.30,
            coid="my_coid_42",
        )
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        assert order_req.coid == "my_coid_42"

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

    def test_coid_auto_generated(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])
        client.place_order(conid=1, side="BUY", quantity=1, order_type="LMT", price=1.0)
        assert mock_ibkr.place_order.call_args.kwargs["order_request"].coid.startswith("CAL_")

    def test_coid_caller_supplied_passes_through(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])
        client.place_order(
            conid=1, side="BUY", quantity=1, order_type="LMT", price=1.0,
            coid="my_coid",
        )
        assert mock_ibkr.place_order.call_args.kwargs["order_request"].coid == "my_coid"

    def test_price_increment_zero_skips_rounding(self, connected_client):
        """Caller can pass price_increment=0 to skip the default $0.05 grid
        for instruments that use a finer tick (e.g. sub-$1 stocks at $0.01)."""
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])
        client.place_order(
            conid=1, side="BUY", quantity=1, order_type="LMT",
            price=5.37, price_increment=0,
        )
        assert mock_ibkr.place_order.call_args.kwargs["order_request"].price == pytest.approx(5.37)

    def test_quantity_serialized_as_float(self, connected_client):
        """OrderRequest.quantity is typed float in ibind; we cast at the
        call site so the wire payload is deterministic."""
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])
        client.place_order(conid=1, side="BUY", quantity=10, order_type="LMT", price=1.0)
        assert isinstance(
            mock_ibkr.place_order.call_args.kwargs["order_request"].quantity, float
        )


class TestSubmitOrderResponseShapes:
    def test_multi_leg_list_promotes_first_and_stashes_rest(self, connected_client):
        """When ibind returns N entries (combo fills with per-leg detail),
        the head dict becomes the top-level response and the rest land
        under '_legs' so nothing is silently dropped."""
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([
            {"order_id": "head_id", "filled": 0},
            {"order_id": "leg2_id", "filled": 0},
            {"order_id": "leg3_id", "filled": 0},
        ])
        out = client.place_order(
            conid=1, side="BUY", quantity=1, order_type="LMT", price=1.0,
        )
        assert out["order_id"] == "head_id"
        assert "_legs" in out
        assert len(out["_legs"]) == 2

    def test_single_entry_list_unwraps(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "only"}])
        out = client.place_order(
            conid=1, side="BUY", quantity=1, order_type="LMT", price=1.0,
        )
        assert out["order_id"] == "only"
        assert "_legs" not in out

    def test_empty_list_response_yields_empty_dict(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([])
        out = client.place_order(
            conid=1, side="BUY", quantity=1, order_type="LMT", price=1.0,
        )
        assert out == {}

    def test_prefers_order_id_entry_over_reply_prompt(self, connected_client):
        """P7-audit M15: if a list response interleaves a reply-prompt
        entry (no `order_id`/`id`) with a real order entry, promote the
        order entry. Otherwise the caller reads `order_id=None` and
        treats the fill as a failure even though IBKR accepted it."""
        client, mock_ibkr = connected_client
        # Reply-prompt-shaped entry FIRST, real order SECOND.
        mock_ibkr.place_order.return_value = _mk_result([
            {"message": ["Confirm price improvement"], "isSuppressed": False},
            {"order_id": "real_id", "order_status": "Submitted"},
        ])
        out = client.place_order(
            conid=1, side="BUY", quantity=1, order_type="LMT", price=1.0,
        )
        assert out["order_id"] == "real_id"
        # The reply-prompt entry is preserved under `_legs` (nothing
        # silently dropped — caller can inspect for unhandled prompts).
        assert "_legs" in out
        assert any("message" in d for d in out["_legs"])


class TestPlaceMarketOrder:
    def test_wraps_place_order_with_mkt(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])

        client.place_market_order(conid=12345, side="SELL", quantity=5)
        order_req = mock_ibkr.place_order.call_args.kwargs["order_request"]
        assert order_req.order_type == "MKT"
        assert order_req.price is None


# ─── Position normalization ────────────────────────────────────────────────


class TestNormalizePositionDict:
    """Unit tests for the module-level _normalize_position_dict helper.

    IBKR's portfolio_positions response is flat but uses broker-specific
    field naming (`conid`, `position`, `putOrCall`, `lastTradingDay`,
    etc.). Strategy code shouldn't lock in IBKR's vocabulary — the
    normalizer translates to a stable schema. These tests pin the
    contract.
    """

    # ─── Standard option position shapes ───────────────────────────────

    def test_short_call_with_full_option_fields(self):
        """Canonical SPXW short call position with all IBKR option fields."""
        from datetime import date as date_cls
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({
            "conid": 883539497,
            "ticker": "SPXW",
            "contractDesc": "SPXW 20260518 5800 C",
            "assetClass": "OPT",
            "position": -1.0,            # short = negative
            "avgCost": 2.50,
            "mktPrice": 0.05,
            "mktValue": -5.0,
            "unrealizedPnl": 245.0,
            "currency": "USD",
            "lastTradingDay": "20260518",
            "strike": 5800.0,
            "putOrCall": "C",
        })
        assert out["instrument_id"] == 883539497
        assert out["symbol"] == "SPXW"
        assert out["asset_type"] == "OPT"
        assert out["quantity"] == -1
        assert out["side"] == "SHORT"
        assert out["avg_cost"] == pytest.approx(2.50)
        assert out["market_price"] == pytest.approx(0.05)
        assert out["market_value"] == pytest.approx(-5.0)
        assert out["unrealized_pnl"] == pytest.approx(245.0)
        assert out["currency"] == "USD"
        assert out["expiry"] == date_cls(2026, 5, 18)
        assert out["strike"] == pytest.approx(5800.0)
        assert out["right"] == "C"

    def test_long_put_position(self):
        from datetime import date as date_cls
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({
            "conid": 873614067,
            "ticker": "SPXW",
            "assetClass": "OPT",
            "position": 1.0,             # long = positive
            "avgCost": 1.10,
            "lastTradingDay": "20260518",
            "strike": 5195.0,
            "putOrCall": "P",
        })
        assert out["quantity"] == 1
        assert out["side"] == "LONG"
        assert out["right"] == "P"
        assert out["strike"] == pytest.approx(5195.0)
        assert out["expiry"] == date_cls(2026, 5, 18)

    def test_index_position_has_no_option_fields(self):
        """SPX index position — option-specific fields all None."""
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({
            "conid": 416904,
            "ticker": "SPX",
            "assetClass": "IND",
            "position": 0.0,  # indices are non-tradable; "position" is informational
            "currency": "USD",
        })
        assert out["instrument_id"] == 416904
        assert out["asset_type"] == "IND"
        assert out["side"] == "FLAT"
        assert out["expiry"] is None
        assert out["strike"] is None
        assert out["right"] is None

    def test_stock_position_no_option_fields(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({
            "conid": 76792991,
            "ticker": "AAPL",
            "assetClass": "STK",
            "position": 100,
            "avgCost": 175.0,
        })
        assert out["asset_type"] == "STK"
        assert out["quantity"] == 100
        assert out["side"] == "LONG"
        assert out["expiry"] is None
        assert out["strike"] is None
        assert out["right"] is None

    # ─── Side derivation from signed quantity ──────────────────────────

    def test_zero_quantity_is_flat(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1, "position": 0})
        assert out["quantity"] == 0
        assert out["side"] == "FLAT"

    def test_negative_quantity_is_short(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1, "position": -5})
        assert out["side"] == "SHORT"

    def test_positive_quantity_is_long(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1, "position": 5})
        assert out["side"] == "LONG"

    def test_float_quantity_cast_to_int(self):
        """IBKR wire format uses float for `position`. We always work in
        whole contracts → cast to int. Fractional positions don't exist
        in our universe (no crypto, no fractional shares)."""
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1, "position": 3.0})
        assert out["quantity"] == 3
        assert isinstance(out["quantity"], int)

    # ─── Required field validation ─────────────────────────────────────

    def test_missing_conid_raises(self):
        from shared.ib_client import _normalize_position_dict
        with pytest.raises(ValueError, match="missing conid"):
            _normalize_position_dict({"position": 1, "ticker": "AAPL"})

    def test_non_dict_input_raises(self):
        from shared.ib_client import _normalize_position_dict
        with pytest.raises(ValueError, match="must be a dict"):
            _normalize_position_dict([{"conid": 1}])
        with pytest.raises(ValueError, match="must be a dict"):
            _normalize_position_dict(None)

    # ─── Right normalization (P/C variants) ────────────────────────────

    def test_right_put_full_word_normalizes_to_P(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1, "putOrCall": "PUT"})
        assert out["right"] == "P"

    def test_right_call_full_word_normalizes_to_C(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1, "putOrCall": "CALL"})
        assert out["right"] == "C"

    def test_right_lowercase_is_normalized(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1, "putOrCall": "p"})
        assert out["right"] == "P"

    def test_right_invalid_value_becomes_none(self):
        """Defensive: don't silently mis-label a position with garbage."""
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1, "putOrCall": "X"})
        assert out["right"] is None

    # ─── Expiry parsing ────────────────────────────────────────────────

    def test_expiry_parsed_from_lastTradingDay(self):
        from datetime import date as date_cls
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({
            "conid": 1, "lastTradingDay": "20260518",
        })
        assert out["expiry"] == date_cls(2026, 5, 18)

    def test_expiry_missing_field_returns_none(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1})
        assert out["expiry"] is None

    def test_expiry_malformed_field_returns_none(self):
        """Defensive parsing: bad input → None, not an exception."""
        from shared.ib_client import _normalize_position_dict
        for bad_value in ("not-a-date", "2026-05-18", "20260518X", "x"):
            out = _normalize_position_dict({
                "conid": 1, "lastTradingDay": bad_value,
            })
            assert out["expiry"] is None, f"Bad value should yield None: {bad_value!r}"

    # ─── Symbol extraction ─────────────────────────────────────────────

    def test_symbol_prefers_ticker(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({
            "conid": 1, "ticker": "SPX", "contractDesc": "Different Description",
        })
        assert out["symbol"] == "SPX"

    def test_symbol_falls_back_to_contractDesc_first_word(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({
            "conid": 1, "contractDesc": "SPXW 20260518 5800 C",
        })
        assert out["symbol"] == "SPXW"

    def test_symbol_empty_when_both_missing(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1})
        assert out["symbol"] == ""

    # ─── Numeric field defensive parsing ───────────────────────────────

    def test_missing_numeric_fields_become_none(self):
        """Status-only responses (no fills yet) won't have price fields."""
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1, "position": 1})
        assert out["avg_cost"] is None
        assert out["market_price"] is None
        assert out["market_value"] is None
        assert out["unrealized_pnl"] is None

    def test_malformed_numeric_fields_become_none(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({
            "conid": 1, "avgCost": "not-a-number", "mktPrice": "",
        })
        assert out["avg_cost"] is None
        assert out["market_price"] is None

    # ─── Defaults + preservation ───────────────────────────────────────

    def test_currency_defaults_to_USD(self):
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1})
        assert out["currency"] == "USD"

    def test_asset_class_uppercased(self):
        """IBKR sometimes returns lowercase; normalize for caller convenience."""
        from shared.ib_client import _normalize_position_dict
        out = _normalize_position_dict({"conid": 1, "assetClass": "opt"})
        assert out["asset_type"] == "OPT"

    def test_raw_preserved(self):
        """Callers needing fields we didn't normalize can drop down to `raw`."""
        from shared.ib_client import _normalize_position_dict
        raw = {
            "conid": 1, "position": 1,
            "exchange": "CBOE", "ticker": "SPX",
            "customField": "preserve-this",
        }
        out = _normalize_position_dict(raw)
        assert out["raw"] is raw  # same object


# ─── place_and_wait_for_fill ───────────────────────────────────────────────


class TestBuildFillResultDict:
    """Unit tests for the module-level _build_fill_result_dict helper.

    IBKR's order status response uses inconsistent field names across
    endpoints (snake_case vs camelCase, `filled` vs `filledQuantity`,
    etc.). The helper accepts several variants — these tests pin which.
    """

    def test_extracts_filled_quantity_and_avg_price_camelcase(self):
        from shared.ib_client import _build_fill_result_dict
        out = _build_fill_result_dict(
            order_id="abc",
            raw={"filledQuantity": "3", "avgPrice": "5.25"},
            status="filled",
        )
        assert out["order_id"] == "abc"
        assert out["status"] == "filled"
        assert out["filled_quantity"] == 3
        assert out["avg_fill_price"] == 5.25

    def test_extracts_snake_case_variants(self):
        from shared.ib_client import _build_fill_result_dict
        out = _build_fill_result_dict(
            order_id="abc",
            raw={"filled_quantity": 5, "avg_fill_price": 1.10},
            status="filled",
        )
        assert out["filled_quantity"] == 5
        assert out["avg_fill_price"] == pytest.approx(1.10)

    def test_accepts_filled_and_average_price_variants(self):
        from shared.ib_client import _build_fill_result_dict
        out = _build_fill_result_dict(
            order_id="abc",
            raw={"filled": 2, "average_price": "0.40"},
            status="filled",
        )
        assert out["filled_quantity"] == 2
        assert out["avg_fill_price"] == pytest.approx(0.40)

    def test_accepts_avgfillprice_variant(self):
        from shared.ib_client import _build_fill_result_dict
        out = _build_fill_result_dict(
            order_id="abc",
            raw={"avgFillPrice": 7.0},
            status="filled",
        )
        assert out["avg_fill_price"] == pytest.approx(7.0)

    def test_missing_fields_become_zero_and_none(self):
        """Status responses for non-filled states often omit fill info."""
        from shared.ib_client import _build_fill_result_dict
        out = _build_fill_result_dict(
            order_id="abc",
            raw={"status": "Submitted"},  # no fill fields
            status="timed_out",
        )
        assert out["filled_quantity"] == 0
        assert out["avg_fill_price"] is None

    def test_bad_values_dont_crash(self):
        """Defensive: malformed responses shouldn't raise. Bad int → 0, bad float → None."""
        from shared.ib_client import _build_fill_result_dict
        out = _build_fill_result_dict(
            order_id="abc",
            raw={"filledQuantity": "not-a-number", "avgPrice": "also-bad"},
            status="filled",
        )
        assert out["filled_quantity"] == 0
        assert out["avg_fill_price"] is None

    def test_raw_response_preserved_for_diagnostics(self):
        """Callers may need fields we didn't normalize — raw stays intact."""
        from shared.ib_client import _build_fill_result_dict
        raw = {"filledQuantity": 1, "avgPrice": 1.0, "customField": "preserve-me"}
        out = _build_fill_result_dict(order_id="abc", raw=raw, status="filled")
        assert out["raw"] is raw  # same object, not a copy


class TestPlaceAndWaitForFill:
    """Building block for HYDRA's strategy-level retry loops.

    Encapsulates place → poll-status → return-when-done so callers don't
    re-implement polling at every retry level. Polling timing is mocked
    via patching `time.sleep` / `time.monotonic`.
    """

    # ─── Input validation ──────────────────────────────────────────────

    def test_quantity_must_be_positive(self, connected_client):
        client, _ = connected_client
        with pytest.raises(ValueError, match="quantity must be positive"):
            client.place_and_wait_for_fill(
                conid=1, side="BUY", quantity=0, order_type="MKT",
            )

    def test_side_must_be_buy_or_sell(self, connected_client):
        client, _ = connected_client
        with pytest.raises(ValueError, match="side must be"):
            client.place_and_wait_for_fill(
                conid=1, side="HODL", quantity=1, order_type="MKT",
            )

    def test_lmt_requires_limit_price(self, connected_client):
        client, _ = connected_client
        with pytest.raises(ValueError, match="LMT order_type requires limit_price"):
            client.place_and_wait_for_fill(
                conid=1, side="BUY", quantity=1, order_type="LMT",
            )

    def test_place_response_without_order_id_raises(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{}])  # no order_id field
        with pytest.raises(IBClientError, match="place_order returned no order_id"):
            client.place_and_wait_for_fill(
                conid=1, side="BUY", quantity=1, order_type="MKT",
            )

    # ─── Happy paths ───────────────────────────────────────────────────

    def test_lmt_fills_on_first_poll(self, connected_client):
        """Order placed, first status poll shows Filled. No retry needed."""
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result(
            [{"order_id": "order-1", "order_status": "Submitted"}]
        )
        mock_ibkr.order_status.return_value = _mk_result({
            "status": "Filled", "filledQuantity": 1, "avgPrice": 2.50,
        })
        with patch("shared.ib_client.time.sleep"):
            result = client.place_and_wait_for_fill(
                conid=12345, side="SELL", quantity=1,
                order_type="LMT", limit_price=2.50,
            )
        assert result["order_id"] == "order-1"
        assert result["status"] == "filled"
        assert result["filled_quantity"] == 1
        assert result["avg_fill_price"] == pytest.approx(2.50)

    def test_mkt_fills_immediately_in_place_response(self, connected_client):
        """P7-audit C3+C4. IBKR's place response reports the state under
        `order_status` (not `status`) and carries NO fill detail. When it
        is terminal=Filled we short-circuit the poll loop, but must do ONE
        order_status fetch to get the real filledQuantity/avgPrice —
        otherwise a filled order is reported filled_quantity=0 and the
        caller retries into a double position."""
        client, mock_ibkr = connected_client
        # IBKR place response: state under `order_status`, NO fill detail.
        mock_ibkr.place_order.return_value = _mk_result([{
            "order_id": "order-mkt-1",
            "order_status": "Filled",
        }])
        # Authoritative fill detail comes from the order-status fetch.
        mock_ibkr.order_status.return_value = _mk_result({
            "status": "Filled", "filledQuantity": 5, "avgPrice": 1.10,
        })
        with patch("shared.ib_client.time.sleep") as mock_sleep:
            result = client.place_and_wait_for_fill(
                conid=12345, side="BUY", quantity=5, order_type="MKT",
            )
        assert result["status"] == "filled"
        assert result["filled_quantity"] == 5            # C4: real qty, not 0
        assert result["avg_fill_price"] == pytest.approx(1.10)
        # C4: exactly ONE order_status fetch for the fill detail —
        # the short-circuit means NO poll loop (and no sleep).
        assert mock_ibkr.order_status.call_count == 1
        mock_sleep.assert_not_called()

    def test_instant_fill_falls_back_to_place_resp_if_status_fetch_fails(
        self, connected_client,
    ):
        """C4 graceful path: if the post-fill order_status fetch raises
        (order purged), fall back to the place response rather than
        crashing — the order is still correctly reported `filled`."""
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{
            "order_id": "order-mkt-2", "order_status": "Filled",
        }])
        mock_ibkr.order_status.side_effect = IBClientError("is not found")
        with patch("shared.ib_client.time.sleep"):
            result = client.place_and_wait_for_fill(
                conid=12345, side="BUY", quantity=3, order_type="MKT",
            )
        assert result["status"] == "filled"  # still terminal, no crash

    def test_pendingcancel_is_NOT_terminal_keeps_polling(
        self, connected_client,
    ):
        """`pendingcancel` means cancel-in-progress but order could still
        fill. We must keep polling until truly terminal."""
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result(
            [{"order_id": "order-x"}]
        )
        # First poll: PendingCancel (not terminal). Second poll: Filled.
        mock_ibkr.order_status.side_effect = [
            _mk_result({"status": "PendingCancel"}),
            _mk_result({"status": "Filled", "filledQuantity": 1, "avgPrice": 3.0}),
        ]
        with patch("shared.ib_client.time.sleep"):
            result = client.place_and_wait_for_fill(
                conid=12345, side="SELL", quantity=1,
                order_type="LMT", limit_price=3.0,
            )
        assert result["status"] == "filled"
        # Two polls happened (PendingCancel → keep going → Filled)
        assert mock_ibkr.order_status.call_count == 2

    # ─── Terminal-not-filled paths ────────────────────────────────────

    def test_rejected_order_returns_status_rejected(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result(
            [{"order_id": "x", "order_status": "Submitted"}]
        )
        mock_ibkr.order_status.return_value = _mk_result({"status": "Rejected"})
        with patch("shared.ib_client.time.sleep"):
            result = client.place_and_wait_for_fill(
                conid=1, side="BUY", quantity=1,
                order_type="LMT", limit_price=1.0,
            )
        assert result["status"] == "rejected"
        assert result["filled_quantity"] == 0
        assert result["avg_fill_price"] is None

    def test_inactive_is_terminal(self, connected_client):
        """IBKR-specific 'Inactive' status — broker decided not to work
        the order. Treated as terminal so we don't poll forever."""
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])
        mock_ibkr.order_status.return_value = _mk_result({"status": "Inactive"})
        with patch("shared.ib_client.time.sleep"):
            result = client.place_and_wait_for_fill(
                conid=1, side="BUY", quantity=1,
                order_type="LMT", limit_price=1.0,
            )
        assert result["status"] == "inactive"

    def test_purged_order_503_not_found_treated_as_cancelled(
        self, connected_client,
    ):
        """IBKR's misuse of 503 for permanent errors: when get_order_status
        raises with 'is not found', the order was purged after reaching
        terminal state. Return status=cancelled (no longer working)."""
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])
        # Status query raises with the IBKR 503 'not found' pattern
        mock_ibkr.order_status.side_effect = IBClientError(
            "ibind error: 503 Service Unavailable :: "
            '{"error":"Order x is not found","statusCode":503}'
        )
        with patch("shared.ib_client.time.sleep"):
            result = client.place_and_wait_for_fill(
                conid=1, side="BUY", quantity=1,
                order_type="LMT", limit_price=1.0,
            )
        assert result["status"] == "cancelled"

    def test_non_permanent_ibclient_error_during_poll_propagates(
        self, connected_client,
    ):
        """A real failure mid-polling (e.g. auth error) must NOT be
        swallowed — propagate so the caller can escalate."""
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])
        mock_ibkr.order_status.side_effect = IBClientError(
            "auth failure — token expired"
        )
        with patch("shared.ib_client.time.sleep"):
            with pytest.raises(IBClientError, match="auth failure"):
                client.place_and_wait_for_fill(
                    conid=1, side="BUY", quantity=1,
                    order_type="LMT", limit_price=1.0,
                )

    # ─── Timeout path ──────────────────────────────────────────────────

    def test_times_out_when_status_never_terminal(self, connected_client):
        """If the order never reaches a terminal status within the timeout,
        return status='timed_out'. The order remains WORKING on IBKR's
        side — caller decides whether to cancel or escalate.

        Implementation note: `time.monotonic` is patched with an
        unlimited counter rather than a fixed list because the breaker
        layer (record_success on each successful poll) also calls
        monotonic, so a fixed-length iter would exhaust unpredictably.
        Counter step (0.05s) × deadline (0.5s) ensures the loop exits
        after ~10 monotonic calls regardless of how many are consumed
        by the breaker."""
        import itertools
        client, mock_ibkr = connected_client
        mock_ibkr.place_order.return_value = _mk_result([{"order_id": "x"}])
        mock_ibkr.order_status.return_value = _mk_result({"status": "Submitted"})
        counter = itertools.count(start=100.0, step=0.05)
        with patch("shared.ib_client.time.sleep"), \
             patch("shared.ib_client.time.monotonic", side_effect=lambda: next(counter)):
            result = client.place_and_wait_for_fill(
                conid=1, side="BUY", quantity=1,
                order_type="LMT", limit_price=1.0,
                timeout_seconds=0.5,
            )
        assert result["status"] == "timed_out"
        # We polled at least once and saw Submitted in `raw`
        assert (result["raw"].get("status") or "").lower() == "submitted"


# ─── cancel_order ──────────────────────────────────────────────────────────


class TestCancelOrder:
    def test_returns_true_on_success(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.cancel_order.return_value = _mk_result({"cancelled": True})
        assert client.cancel_order("abc123") is True
        mock_ibkr.cancel_order.assert_called_with(order_id="abc123", account_id="DU1234567")

    def test_returns_false_on_genuine_ibind_error(self, connected_client):
        """Genuine failure (e.g. network/auth) → False so caller escalates
        the P0 risk that a working order couldn't be cancelled."""
        client, mock_ibkr = connected_client
        result = MagicMock()
        result.data = None
        result.error = "network unreachable"
        mock_ibkr.cancel_order.return_value = result
        assert client.cancel_order("abc123") is False

    # ─── Already-terminal handling (Fix 2026-05-18 paper smoke) ────────
    # IBKR returns 4xx/5xx with body indicating the order is already in
    # a terminal state. The caller's intent ("order no longer working")
    # is satisfied even though OUR cancel arrived too late — return True.

    def test_returns_true_when_order_already_filled_or_canceled(
        self, connected_client,
    ):
        """The exact IBKR phrase from Mon 2026-05-18 paper smoke:
        '400 Bad Request: {"error":"Order Message:\\nSELL 1 Combo\\nOrder
        is filled or canceled"}'. cancel_order must treat this as success
        because the order is no longer working — caller's intent met."""
        client, mock_ibkr = connected_client
        mock_ibkr.cancel_order.side_effect = Exception(
            "IbkrClient: response error :: 400 :: Bad Request :: "
            '{"error":"Order Message:\\nSELL 1 Combo\\nOrder is filled or canceled"}'
        )
        assert client.cancel_order("417709432") is True

    def test_returns_true_when_order_already_cancelled(self, connected_client):
        """Variants of the same idea — IBKR may report 'already cancelled'
        in slightly different ways."""
        client, mock_ibkr = connected_client
        for err_msg in (
            "503 Service Unavailable: order is already cancelled",
            "400 Bad Request: order already canceled",
            "ibind error: order is already filled",
        ):
            mock_ibkr.cancel_order.side_effect = Exception(err_msg)
            assert client.cancel_order("abc123") is True, (
                f"Should treat as success: {err_msg!r}"
            )

    def test_returns_true_when_order_purged_not_found(self, connected_client):
        """503 with 'is not found' body = IBKR purged the order after a
        terminal state. Sunday's smoke uncovered this via the
        check_order.py diagnostic; cancel_order should treat same as
        already-cancelled (order is no longer working)."""
        client, mock_ibkr = connected_client
        mock_ibkr.cancel_order.side_effect = Exception(
            "IbkrClient: response error :: 503 :: Service Unavailable :: "
            '{"error":"Order 893931734 is not found","statusCode":503}'
        )
        assert client.cancel_order("893931734") is True

    def test_returns_false_when_breaker_open(self, connected_client):
        """Breaker-open means the order MAY still be working — DON'T
        pretend success. Caller must escalate."""
        from shared.ib_retry import CircuitBreakerOpen
        client, mock_ibkr = connected_client
        # Trip the orders breaker manually
        br = client.circuit_breakers["orders"]
        for _ in range(br.consecutive_failures_threshold):
            br.record_failure()
        assert client.cancel_order("abc123") is False
        # And ibind wasn't called (short-circuited)
        mock_ibkr.cancel_order.assert_not_called()


# ─── modify_order ──────────────────────────────────────────────────────────


class TestModifyOrder:
    def test_requires_side(self, connected_client):
        client, _ = connected_client
        # side is keyword-only; calling without it triggers TypeError
        with pytest.raises(TypeError):
            client.modify_order("abc123", price=0.40)

    def test_requires_positive_quantity(self, connected_client):
        client, _ = connected_client
        with pytest.raises(IBClientError, match="positive quantity"):
            client.modify_order("abc123", side="SELL", quantity=0, price=0.40)

    def test_price_modification(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.modify_order.return_value = _mk_result({"order_id": "abc123"})

        client.modify_order("abc123", side="SELL", quantity=10, price=0.40)
        # ibind got an OrderRequest with the new (rounded) price + real
        # side/qty (not placeholder SELL/0)
        order_req = mock_ibkr.modify_order.call_args.kwargs["order_request"]
        assert order_req.price == pytest.approx(0.40)
        assert order_req.side == "SELL"
        assert order_req.quantity == pytest.approx(10.0)

    def test_side_uppercased(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.modify_order.return_value = _mk_result({"order_id": "abc123"})
        client.modify_order("abc123", side="buy", quantity=5, price=1.00)
        order_req = mock_ibkr.modify_order.call_args.kwargs["order_request"]
        assert order_req.side == "BUY"

    def test_price_rounded_to_005(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.modify_order.return_value = _mk_result({"order_id": "abc123"})

        client.modify_order("abc123", side="SELL", quantity=10, price=0.327)
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
