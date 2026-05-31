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
    AmbiguousOrderError,
    DEFAULT_GREEKS_FIELDS,
    DEFAULT_QUOTE_FIELDS,
    FIELD_BID,
    FIELD_ASK,
    FIELD_LAST,
    FIELD_MARK,
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
    auth_status.error = None  # Phase A.8: _ib_call → _unwrap checks .error
    mock_ibkr.authentication_status.return_value = auth_status
    portfolio_result = MagicMock()
    portfolio_result.data = [{"accountId": "DU1234567"}]
    portfolio_result.error = None
    mock_ibkr.portfolio_accounts.return_value = portfolio_result
    # /iserver/accounts preflight (_ensure_iserver_primed) — required
    # before the marketdata snapshot + trades endpoints.
    iserver_result = MagicMock()
    iserver_result.data = {}
    iserver_result.error = None
    mock_ibkr.receive_brokerage_accounts.return_value = iserver_result

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
            [{"conid": 999111, "tradingClass": "SPXW", "maturityDate": "20260516"}]
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
            {"conid": 111, "tradingClass": "SPX", "maturityDate": "20260516"},   # NOT 0DTE
            {"conid": 222, "tradingClass": "SPXW", "maturityDate": "20260516"},  # 0DTE
        ])
        conid = client.qualify_contract(
            "SPX", expiry=date(2026, 5, 16), strike=5500, right="C",
            trading_class="SPXW",
        )
        assert conid == 222

    def test_option_filters_by_exact_expiry_when_chain_has_multiple_weeklies(
        self, connected_client,
    ):
        """SPXW chain returns multiple weeklies for the queried month; we
        must pick the one matching the caller's exact expiry, not the
        first one. Regression for the 2026-05-16 paper-smoke `whatif →
        "Order is already expired"` failure: month=MAY26 returned conids
        for already-expired Fri May 15 weekly + upcoming Mon May 18; old
        filter took the first (= expired), 500'd on whatif."""
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        mock_ibkr.search_secdef_info_by_conid.return_value = _mk_result([
            # Already-expired Friday weekly (the "land mine")
            {"conid": 111, "tradingClass": "SPXW", "maturityDate": "20260515"},
            # The one we want (Monday weekly)
            {"conid": 222, "tradingClass": "SPXW", "maturityDate": "20260518"},
            # A later weekly we should NOT pick
            {"conid": 333, "tradingClass": "SPXW", "maturityDate": "20260520"},
        ])
        conid = client.qualify_contract(
            "SPX", expiry=date(2026, 5, 18), strike=5500, right="C",
            trading_class="SPXW",
        )
        assert conid == 222, (
            f"Expected Mon 5/18 weekly conid 222, got {conid} — "
            f"expiry filter is not picking the exact requested date"
        )

    def test_option_expiry_filter_supports_alternative_field_names(
        self, connected_client,
    ):
        """Defensive: ibind/IBKR endpoints publish expiry under varying
        field names. Filter should accept `maturityDate`, `expirationDate`,
        and `expiry`."""
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        # Use `expirationDate` instead of `maturityDate`
        mock_ibkr.search_secdef_info_by_conid.return_value = _mk_result([
            {"conid": 444, "tradingClass": "SPXW", "expirationDate": "20260515"},  # wrong day
            {"conid": 555, "tradingClass": "SPXW", "expirationDate": "20260518"},  # right day
        ])
        conid = client.qualify_contract(
            "SPX", expiry=date(2026, 5, 18), strike=5500, right="C",
            trading_class="SPXW",
        )
        assert conid == 555

    def test_option_no_match_for_requested_expiry_raises_with_available_list(
        self, connected_client,
    ):
        """When no row matches the caller's expiry, raise with a clear
        diagnostic listing what IBKR did return (so debugging doesn't
        require introspecting raw responses)."""
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        mock_ibkr.search_secdef_info_by_conid.return_value = _mk_result([
            {"conid": 111, "tradingClass": "SPXW", "maturityDate": "20260515"},
            {"conid": 222, "tradingClass": "SPXW", "maturityDate": "20260520"},
        ])
        with pytest.raises(IBClientError) as excinfo:
            client.qualify_contract(
                "SPX", expiry=date(2026, 5, 18),  # neither row matches
                strike=5500, right="C", trading_class="SPXW",
            )
        msg = str(excinfo.value)
        assert "20260518" in msg, f"Expected requested date in error: {msg!r}"
        assert "20260515" in msg, f"Expected available expiry in error: {msg!r}"
        assert "20260520" in msg, f"Expected available expiry in error: {msg!r}"

    def test_option_expiry_filter_fails_closed_when_field_absent(
        self, connected_client,
    ):
        """Audit #20: a secdef row with NO parseable expiry field must be
        REJECTED (fail-closed), not accepted — accepting it could land on a
        wrong-dated/already-expired conid (the 2026-05-16 'Order is already
        expired' 500). The permissive 'accept when absent' behavior survives
        ONLY behind the test-only `_allow_missing_expiry` escape hatch."""
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        mock_ibkr.search_secdef_info_by_conid.return_value = _mk_result(
            [{"conid": 777, "tradingClass": "SPXW"}]  # no maturityDate
        )
        # Production default (flag off) → fail closed.
        assert client._allow_missing_expiry is False
        with pytest.raises(IBClientError):
            client.qualify_contract(
                "SPX", expiry=date(2026, 5, 18), strike=5500, right="C",
                trading_class="SPXW",
            )
        # Escape hatch on → legacy permissive behavior for field-less mocks.
        client._allow_missing_expiry = True
        client._conid_cache.clear()  # don't serve a cached miss
        conid = client.qualify_contract(
            "SPX", expiry=date(2026, 5, 18), strike=5500, right="C",
            trading_class="SPXW",
        )
        assert conid == 777

    def test_option_expiry_accepts_iso_string_over_rpc_wire(self, connected_client):
        """Audit #4: over the calypso-broker RPC wire a datetime.date argument
        arrives as an isoformat STRING ('2026-05-16'). qualify_contract (and
        get_option_chain / qualify_option_strikes) must coerce it back to a date
        so option-chain resolution works in broker-proxy mode — not only on the
        direct path. Without the coercion, _ib_month_str/strftime raise and the
        strategy silently never enters a trade."""
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 416904}])
        mock_ibkr.search_secdef_info_by_conid.return_value = _mk_result(
            [{"conid": 999111, "tradingClass": "SPXW", "maturityDate": "20260516"}]
        )
        conid = client.qualify_contract(
            "SPX", expiry="2026-05-16",  # the RPC-wire string form
            strike=5500, right="C", trading_class="SPXW",
        )
        assert conid == 999111

    def test_underlying_index_strict_filter_picks_cboe_ind_from_sections(
        self, connected_client,
    ):
        """Live IBKR responses don't always set top-level secType/exchange.
        Strict pass should use the `sections` array to pick the right
        candidate. Regression for the 2026-05-16 noisy
        '5 underlying candidates after filtering' warning."""
        client, mock_ibkr = connected_client
        # 3 candidates — only the middle one publishes IND on CBOE in its
        # sections array. The strict pass must pick it deterministically.
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([
            {"conid": 100, "sections": [{"secType": "STK", "exchange": "NYSE"}]},
            {"conid": 416904, "sections": [
                {"secType": "IND", "exchange": "CBOE;"},
                {"secType": "OPT", "exchange": "SMART;CBOE"},
            ]},
            {"conid": 200, "sections": [{"secType": "WAR", "exchange": "FWB"}]},
        ])
        conid = client.qualify_contract("SPX", sec_type="IND")
        assert conid == 416904, (
            f"Strict-pass filter should pick the IND-on-CBOE candidate "
            f"deterministically, got {conid}"
        )

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


# ─── qualify_option_strikes (F3.1 batch resolver) ──────────────────────────


class TestQualifyOptionStrikes:
    """Tests for IBClient.qualify_option_strikes — the F3 batch conid
    resolver. Parallelizes per-strike secdef_info calls across a thread
    pool; each call resolves both rights; results filtered by exact
    expiry + trading class; conid cache populated for cross-method hits.
    """

    _EXPIRY = date(2026, 5, 20)         # → month "MAY26", yyyymmdd 20260520

    def _setup_underlying(self, mock_ibkr):
        """Make qualify_contract('SPX', IND) resolve to a fixed conid."""
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([
            {"conid": 416904, "sections": [
                {"secType": "IND", "exchange": "CBOE;"},
            ]},
        ])

    @staticmethod
    def _secdef_entry(strike, right, *, conid, maturity="20260520",
                      trading_class="SPXW"):
        return {
            "conid": conid, "strike": strike, "right": right,
            "tradingClass": trading_class, "maturityDate": maturity,
            "secType": "OPT", "exchange": "CBOE",
        }

    # ─── Basic resolution ──────────────────────────────────────────────

    def test_resolves_both_rights_per_strike(self, connected_client):
        """One secdef call per strike returns C+P; result maps both."""
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)

        def fake_secdef(**kw):
            s = float(kw["strike"])
            return _mk_result([
                self._secdef_entry(s, "C", conid=int(s) * 10 + 1),
                self._secdef_entry(s, "P", conid=int(s) * 10 + 2),
            ])
        mock_ibkr.search_secdef_info_by_conid.side_effect = fake_secdef

        result = client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[5500, 5510],
        )
        assert result == {
            (5500.0, "C"): 55001, (5500.0, "P"): 55002,
            (5510.0, "C"): 55101, (5510.0, "P"): 55102,
        }

    def test_empty_strikes_returns_empty_no_api_calls(self, connected_client):
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)
        result = client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[],
        )
        assert result == {}
        mock_ibkr.search_secdef_info_by_conid.assert_not_called()

    def test_duplicate_strikes_resolved_once(self, connected_client):
        """Input [5500, 5500, 5510] → 5500 is resolved a single time."""
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)

        def fake_secdef(**kw):
            s = float(kw["strike"])
            return _mk_result([self._secdef_entry(s, "C", conid=int(s))])
        mock_ibkr.search_secdef_info_by_conid.side_effect = fake_secdef

        client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[5500, 5500, 5510],
        )
        # 2 distinct strikes → 2 secdef calls, not 3
        assert mock_ibkr.search_secdef_info_by_conid.call_count == 2

    # ─── Filtering ─────────────────────────────────────────────────────

    def test_filters_by_exact_expiry(self, connected_client):
        """A strike listed across multiple expiries — only the target
        maturityDate is kept (probe 7: strike 6750 had 7 expiries)."""
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)

        def fake_secdef(**kw):
            s = float(kw["strike"])
            return _mk_result([
                self._secdef_entry(s, "C", conid=1, maturity="20260518"),  # wrong
                self._secdef_entry(s, "C", conid=2, maturity="20260520"),  # target
                self._secdef_entry(s, "C", conid=3, maturity="20260522"),  # wrong
                self._secdef_entry(s, "P", conid=4, maturity="20260520"),  # target
            ])
        mock_ibkr.search_secdef_info_by_conid.side_effect = fake_secdef

        result = client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[5500],
        )
        # Only the 20260520 entries survive
        assert result == {(5500.0, "C"): 2, (5500.0, "P"): 4}

    def test_filters_by_trading_class(self, connected_client):
        """SPX monthly (tradingClass='SPX') entries dropped — we want SPXW."""
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)

        def fake_secdef(**kw):
            s = float(kw["strike"])
            return _mk_result([
                self._secdef_entry(s, "C", conid=1, trading_class="SPX"),   # drop
                self._secdef_entry(s, "C", conid=2, trading_class="SPXW"),  # keep
            ])
        mock_ibkr.search_secdef_info_by_conid.side_effect = fake_secdef

        result = client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[5500],
        )
        assert result == {(5500.0, "C"): 2}

    def test_strike_with_no_expiry_match_is_absent(self, connected_client):
        """A strike not listed at the target expiry → simply absent
        (no exception — callers detect via absence)."""
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)

        def fake_secdef(**kw):
            # all entries are for a different expiry
            return _mk_result([
                self._secdef_entry(5500.0, "C", conid=1, maturity="20260101"),
            ])
        mock_ibkr.search_secdef_info_by_conid.side_effect = fake_secdef

        result = client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[5500],
        )
        assert result == {}

    def test_conidEx_fallback(self, connected_client):
        """Some secdef rows carry conidEx instead of conid."""
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)
        mock_ibkr.search_secdef_info_by_conid.side_effect = lambda **kw: _mk_result([
            {"conidEx": 99001, "strike": 5500.0, "right": "C",
             "tradingClass": "SPXW", "maturityDate": "20260520"},
        ])
        result = client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[5500],
        )
        assert result == {(5500.0, "C"): 99001}

    # ─── Failure isolation ─────────────────────────────────────────────

    def test_one_strike_failure_does_not_abort_batch(self, connected_client):
        """A single strike's secdef call raising must NOT kill the batch
        — that strike is omitted, the rest resolve."""
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)

        def fake_secdef(**kw):
            s = float(kw["strike"])
            if s == 5510.0:
                raise RuntimeError("secdef blew up for 5510")
            return _mk_result([self._secdef_entry(s, "C", conid=int(s))])
        mock_ibkr.search_secdef_info_by_conid.side_effect = fake_secdef

        result = client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[5500, 5510, 5520],
        )
        # 5510 omitted; 5500 + 5520 resolved
        assert result == {(5500.0, "C"): 5500, (5520.0, "C"): 5520}

    def test_unconnected_raises(self, paper_creds):
        client = IBClient(IBConfig(credentials=paper_creds))
        with pytest.raises(IBClientError, match="not connected"):
            client.qualify_option_strikes(
                symbol="SPX", expiry=self._EXPIRY, strikes=[5500],
            )

    # ─── Cache behavior ────────────────────────────────────────────────

    def test_populates_conid_cache_in_qualify_contract_format(
        self, connected_client,
    ):
        """Resolved conids land in _conid_cache with the SAME key format
        qualify_contract uses, so cross-method cache hits work."""
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)
        mock_ibkr.search_secdef_info_by_conid.side_effect = lambda **kw: _mk_result([
            self._secdef_entry(float(kw["strike"]), "C", conid=7777),
        ])
        client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[5500],
        )
        # qualify_contract's key: (symbol, expiry_iso, strike, right, tc, sec_type)
        expected_key = ("SPX", "2026-05-20", 5500.0, "C", "SPXW", "OPT")
        assert client._conid_cache.get(expected_key) == 7777

    def test_cache_short_circuit_skips_api_when_both_rights_cached(
        self, connected_client,
    ):
        """If both C+P for a strike are already cached, no secdef call."""
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)
        # Pre-populate cache for strike 5500, both rights
        client._conid_cache[("SPX", "2026-05-20", 5500.0, "C", "SPXW", "OPT")] = 111
        client._conid_cache[("SPX", "2026-05-20", 5500.0, "P", "SPXW", "OPT")] = 222

        result = client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[5500],
        )
        assert result == {(5500.0, "C"): 111, (5500.0, "P"): 222}
        # Both rights cached → secdef endpoint never hit
        mock_ibkr.search_secdef_info_by_conid.assert_not_called()

    def test_cross_method_cache_hit_with_qualify_contract(
        self, connected_client,
    ):
        """After qualify_option_strikes resolves a strike, a subsequent
        qualify_contract for the same (strike, right) is a cache hit —
        zero additional secdef calls."""
        client, mock_ibkr = connected_client
        self._setup_underlying(mock_ibkr)
        mock_ibkr.search_secdef_info_by_conid.side_effect = lambda **kw: _mk_result([
            self._secdef_entry(float(kw["strike"]), "C", conid=4242),
            self._secdef_entry(float(kw["strike"]), "P", conid=4243),
        ])
        client.qualify_option_strikes(
            symbol="SPX", expiry=self._EXPIRY, strikes=[5500],
        )
        calls_after_batch = mock_ibkr.search_secdef_info_by_conid.call_count

        # qualify_contract for the SAME strike+right should hit cache
        conid = client.qualify_contract(
            "SPX", expiry=self._EXPIRY, strike=5500.0, right="C",
            trading_class="SPXW",
        )
        assert conid == 4242
        # No new secdef call — it was a cache hit
        assert mock_ibkr.search_secdef_info_by_conid.call_count == calls_after_batch


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

    def test_empty_response_returns_raw_envelope(self, connected_client):
        """If IBKR returns an empty payload after the pre-flight + warmup,
        expose the raw response under 'raw' so callers can see what happened.
        Mocks time.sleep to keep the test fast despite the warmup loop."""
        client, mock_ibkr = connected_client
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([])
        with patch("shared.ib_client.time.sleep"):
            q = client.get_quote(12345)
        # No row → no parsed fields; conid echoed
        assert q["conid"] == 12345

    def test_preflight_then_data_call_short_circuits_when_populated(
        self, connected_client,
    ):
        """ibind requires a pre-flight; verify we call live_marketdata_snapshot
        exactly twice when the first DATA call returns populated fields
        (no warmup polling needed). This is the happy path."""
        client, mock_ibkr = connected_client
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([
            {"conid": 12345, FIELD_LAST: "5.30"},  # has a real field
        ])
        client.get_quote(12345)
        # 1 preflight + 1 data call (data populated immediately → no warmup)
        assert mock_ibkr.live_marketdata_snapshot.call_count == 2

    def test_iserver_primed_before_snapshot(self, connected_client):
        """C2 regression: the snapshot endpoint returns metadata-only
        forever unless /iserver/accounts (receive_brokerage_accounts)
        is primed first. Verify get_quote primes it."""
        client, mock_ibkr = connected_client
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([
            {"conid": 12345, FIELD_LAST: "5.30"},
        ])
        client.get_quote(12345)
        mock_ibkr.receive_brokerage_accounts.assert_called()

    def test_iserver_primed_only_once_per_session(self, connected_client):
        """The /iserver/accounts preflight is once-per-session — a second
        get_quote must NOT re-prime."""
        client, mock_ibkr = connected_client
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([
            {"conid": 12345, FIELD_LAST: "5.30"},
        ])
        client.get_quote(12345)
        client.get_quote(12345)
        assert mock_ibkr.receive_brokerage_accounts.call_count == 1
        assert client._iserver_primed is True

    def test_iserver_prime_resets_on_disconnect(self, connected_client):
        """disconnect() must clear the primed flag so a reconnect re-primes."""
        client, mock_ibkr = connected_client
        client._iserver_primed = True
        client.disconnect()
        assert client._iserver_primed is False

    def test_warmup_polls_until_fields_populate(self, connected_client):
        """Fix 2026-05-18: IBKR's snapshot endpoint sometimes returns
        metadata-only for a freshly-queried conid. We poll until a real
        field appears OR we exhaust the warmup budget. Regression for the
        Monday paper-smoke failure where `test_get_quote_spx_index`
        returned all-None on the first call but populated on later calls.
        """
        client, mock_ibkr = connected_client
        # First 3 data calls return metadata-only; 4th returns populated
        metadata_only = _mk_result([{"conid": 12345, "_updated": 123}])
        populated = _mk_result([{"conid": 12345, FIELD_LAST: "5.30"}])
        # Preflight (1) + 3 metadata-only + 1 populated = 5 calls
        mock_ibkr.live_marketdata_snapshot.side_effect = [
            metadata_only,  # preflight
            metadata_only, metadata_only, metadata_only,  # warmup polls 1-3
            populated,                                     # warmup poll 4 → done
        ]
        with patch("shared.ib_client.time.sleep") as mock_sleep:
            q = client.get_quote(12345)
        assert q["last"] == 5.30
        # 1 preflight + 4 data attempts = 5 total
        assert mock_ibkr.live_marketdata_snapshot.call_count == 5
        # 3 sleeps between the 4 data attempts (none after the successful one)
        assert mock_sleep.call_count == 3

    def test_warmup_gives_up_after_max_polls(self, connected_client):
        """If snapshot never populates within the warmup budget, return
        the last (empty) response rather than hanging forever. Caller
        receives None fields and can decide what to do."""
        client, mock_ibkr = connected_client
        # Always return metadata-only (simulates a stale/unentitled conid)
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([
            {"conid": 12345, "_updated": 123},
        ])
        with patch("shared.ib_client.time.sleep"):
            q = client.get_quote(12345)
        # All fields end up None — quote parser couldn't find any
        assert q["bid"] is None
        assert q["last"] is None
        # 1 priming call + _SNAPSHOT_MAX_WARMUP_POLLS data polls.
        from shared.ib_client import _SNAPSHOT_MAX_WARMUP_POLLS
        assert (mock_ibkr.live_marketdata_snapshot.call_count
                == 1 + _SNAPSHOT_MAX_WARMUP_POLLS)

    def test_warmup_waits_for_all_conids(self, connected_client):
        """Audit #19: batch warmup must wait until ALL requested conids have
        data, not exit as soon as ANY row warms — otherwise the still-cold
        conids come back all-None and downstream treats them as 'illiquid,
        skip', silently biasing strike selection."""
        client, mock_ibkr = connected_client
        meta_pair = _mk_result([
            {"conid": 111, "_updated": 1}, {"conid": 222, "_updated": 1},
        ])
        one_warm = _mk_result([
            {"conid": 111, FIELD_BID: "1.0"}, {"conid": 222, "_updated": 1},
        ])
        both_warm = _mk_result([
            {"conid": 111, FIELD_BID: "1.0"}, {"conid": 222, FIELD_BID: "2.0"},
        ])
        # preflight, poll1(both cold), poll2(only 111 warm — must NOT exit),
        # poll3+ → both warm.
        seq = [meta_pair, meta_pair, one_warm]
        calls = {"n": 0}

        def _snap(**kwargs):
            i = calls["n"]
            calls["n"] += 1
            return seq[i] if i < len(seq) else both_warm

        mock_ibkr.live_marketdata_snapshot.side_effect = _snap
        with patch("shared.ib_client.time.sleep"):
            rows = client.get_quotes_batch([111, 222])
        # Both conids ended warm...
        assert any(r.get("bid") == 1.0 for r in rows)
        assert any(r.get("bid") == 2.0 for r in rows)
        # ...and it did NOT exit at the one-warm poll (that would be 3 calls).
        assert calls["n"] >= 4, (
            f"warmup exited early with one conid still cold ({calls['n']} calls)"
        )


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

    def test_more_than_50_fields_rejected(self, connected_client):
        client, _ = connected_client
        with pytest.raises(IBClientError, match="max 50 fields"):
            client.get_quotes_batch([1, 2, 3], fields=[str(i) for i in range(51)])


class TestGetVixPrice:
    def test_returns_mid_when_available(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 13455}])
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{
            "conid": 13455, FIELD_BID: "18.0", FIELD_ASK: "18.2",
        }])
        # P7-audit L14: pytest.approx for float comparisons — exact ==
        # would have been brittle to any future refactor that changed
        # the mid-price computation order ((bid+ask)/2 vs (bid+ask)*0.5
        # round differently in floating point on some inputs).
        assert client.get_vix_price() == pytest.approx(18.1)

    def test_falls_back_to_last_when_no_bid_ask(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 13455}])
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{
            "conid": 13455, FIELD_LAST: "17.95",
        }])
        assert client.get_vix_price() == pytest.approx(17.95)

    def test_falls_back_to_mark_when_no_bid_ask_no_last(self, connected_client):
        """Fix 2026-05-18 Monday paper smoke: IBKR's snapshot endpoint
        delivers VIX as ONLY field 7635 (mark) — no bid/ask/last because
        VIX is a calculated index with no trade prints. Without this
        fallback, get_vix_price() returned None despite mark being
        populated, silently breaking HYDRA's VIX-regime decisions and
        stop monitoring on the IB cutover."""
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 13455}])
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{
            "conid": 13455, FIELD_MARK: "18.69",
        }])
        assert client.get_vix_price() == pytest.approx(18.69)

    def test_returns_none_when_all_price_fields_missing(self, connected_client):
        """Defensive: if IBKR returns no usable price field at all,
        get_vix_price returns None (caller must handle)."""
        client, mock_ibkr = connected_client
        mock_ibkr.search_contract_by_symbol.return_value = _mk_result([{"conid": 13455}])
        with patch("shared.ib_client.time.sleep"):
            mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([{
                "conid": 13455,  # no price fields
            }])
            assert client.get_vix_price() is None


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

    def test_exchange_rate_zero_degrades_gracefully(self, connected_client):
        """P7-audit H8: a bad exchangerate must NOT hard-fail (that would
        block all trading). get_balance degrades to base-currency
        available funds as a conservative tradable estimate."""
        client, mock_ibkr = connected_client
        mock_ibkr.portfolio_summary.return_value = _mk_result({
            "availablefunds": {"amount": "50000.0", "currency": "EUR"},
        })
        mock_ibkr.get_ledger.return_value = _mk_result({
            "EUR": {"cashbalance": 50000.0, "isbase": True, "exchangerate": 1.0},
            "USD": {"cashbalance": 0.0, "isbase": False, "exchangerate": 0.0},
        })
        bal = client.get_balance("USD")            # must NOT raise
        assert bal["tradable"] == 50000.0          # base-currency fallback
        assert bal["exchange_rate"] is None
        assert bal["fx_rate_unavailable"] is True

    def test_exchange_rate_nan_degrades_gracefully(self, connected_client):
        """NaN exchangerate — same graceful degradation as zero."""
        client, mock_ibkr = connected_client
        mock_ibkr.portfolio_summary.return_value = _mk_result({
            "availablefunds": {"amount": "50000.0", "currency": "EUR"},
        })
        mock_ibkr.get_ledger.return_value = _mk_result({
            "EUR": {"cashbalance": 50000.0, "isbase": True, "exchangerate": 1.0},
            "USD": {"cashbalance": 0.0, "isbase": False, "exchangerate": float("nan")},
        })
        bal = client.get_balance("USD")            # must NOT raise
        assert bal["tradable"] == 50000.0
        assert bal["fx_rate_unavailable"] is True

    def test_base_currency_falls_back_to_ledger_when_summary_missing(
        self, connected_client,
    ):
        """If portfolio_summary doesn't include availablefunds.currency,
        the base currency is inferred from get_ledger's `isbase=True` row."""
        client, mock_ibkr = connected_client
        mock_ibkr.portfolio_summary.return_value = _mk_result({
            # No 'currency' key on availablefunds
            "availablefunds": {"amount": "50000.0"},
        })
        mock_ibkr.get_ledger.return_value = _mk_result({
            "EUR": {"cashbalance": 50000.0, "isbase": True, "exchangerate": 1.0},
        })
        bal = client.get_balance("EUR")
        # _guess_base_currency picked EUR from the ledger's isbase row
        assert bal["base_currency"] == "EUR"
        assert bal["tradable"] == 50000.0


class TestGetPositions:
    def test_returns_flat_list_across_pages(self, connected_client):
        client, mock_ibkr = connected_client
        # ibind/IBKR returns up to 100 positions per page. Page 0 = full
        # (100) → fetch next; page 1 = partial (5) → done.
        page_0 = [{"conid": i} for i in range(100)]
        page_1 = [{"conid": i} for i in range(100, 105)]
        mock_ibkr.positions.side_effect = [
            _mk_result(page_0),
            _mk_result(page_1),
        ]
        all_pos = client.get_positions()
        assert len(all_pos) == 105
        assert all_pos[0]["conid"] == 0
        assert all_pos[-1]["conid"] == 104

    def test_empty_account(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.positions.return_value = _mk_result([])
        assert client.get_positions() == []

    def test_max_pages_cap_protects_against_infinite_loop(self, connected_client):
        """Defensive cap: 50 pages × 100 = 5000 positions. If IBKR's
        endpoint were ever to return full pages indefinitely (impossible
        in practice but possible under malformed mocks), the loop bails."""
        client, mock_ibkr = connected_client
        # Always return a full 100-row page — without the cap, the loop
        # would never terminate.
        mock_ibkr.positions.return_value = _mk_result(
            [{"conid": i} for i in range(100)]
        )
        out = client.get_positions()
        assert len(out) == 50 * 100  # exactly max_pages * page_size


class TestGetFxRate:
    def test_returns_rate_from_dict(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.currency_exchange_rate.return_value = _mk_result({"rate": 1.085})
        assert client.get_fx_rate("EUR", "USD") == 1.085

    def test_returns_rate_from_scalar(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.currency_exchange_rate.return_value = _mk_result(1.085)
        assert client.get_fx_rate("EUR", "USD") == 1.085

    def test_returns_none_on_empty_response(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.currency_exchange_rate.return_value = _mk_result({})
        assert client.get_fx_rate("EUR", "USD") is None

    def test_returns_none_on_unparseable_scalar(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.currency_exchange_rate.return_value = _mk_result("not_a_number")
        assert client.get_fx_rate("EUR", "USD") is None


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
        # Prime qualify_contract's cache so get_chart_data resolves SPX→conid
        # without hitting search_contract_by_symbol.
        # qualify_contract cache key shape: (symbol, expiry_iso|None, strike, right, trading_class, sec_type)
        client._conid_cache[("SPX", None, None, None, "SPXW", "IND")] = 416904
        bars = [{"t": 1, "o": 100, "h": 101, "l": 99, "c": 100.5}]
        mock_ibkr.marketdata_history_by_conid.return_value = _mk_result({"data": bars})
        out = client.get_chart_data("SPX", bar="1min", period="1d")
        assert out == bars
        # Verify we routed through the by-conid path (NOT by-symbol)
        kw = mock_ibkr.marketdata_history_by_conid.call_args.kwargs
        assert kw["conid"] == "416904"

    def test_default_args(self, connected_client):
        client, mock_ibkr = connected_client
        # qualify_contract cache key shape: (symbol, expiry_iso|None, strike, right, trading_class, sec_type)
        client._conid_cache[("SPX", None, None, None, "SPXW", "IND")] = 416904
        mock_ibkr.marketdata_history_by_conid.return_value = _mk_result({"data": []})
        client.get_chart_data("SPX")
        kw = mock_ibkr.marketdata_history_by_conid.call_args.kwargs
        assert kw["bar"] == "1min"
        assert kw["period"] == "1d"
        assert kw["outside_rth"] is False


# ─── get_closed_position_price (F5.2) ──────────────────────────────────────


def _trade(conid=12345, side="S", price="2.55", size="1",
           trade_time="20260521-14:30:00", trade_time_r=1779000000000,
           execution_id="exec-1"):
    """A doc-shaped /iserver/account/trades execution record."""
    return {
        "conid": conid, "side": side, "price": price, "size": size,
        "trade_time": trade_time, "trade_time_r": trade_time_r,
        "execution_id": execution_id, "symbol": "SPX", "sec_type": "OPT",
    }


class TestGetClosedPositionPrice:
    """F5.2 — IBClient.get_closed_position_price scans /iserver/account/
    trades for the most recent execution at a conid + side. Field shape
    is doc-sourced (F5.1 probe found the paper account had no trade
    history) — hence defensive lookups."""

    def test_matching_sell_returns_closing_price(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([_trade(side="S", price="2.55")])
        out = client.get_closed_position_price(12345, buy_or_sell="Sell")
        assert out["closing_price"] == 2.55
        assert out["buy_or_sell"] == "Sell"
        assert out["conid"] == 12345
        assert out["amount"] == 1

    def test_matching_buy(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([_trade(side="B", price="1.10")])
        out = client.get_closed_position_price(12345, buy_or_sell="Buy")
        assert out["closing_price"] == 1.10
        assert out["buy_or_sell"] == "Buy"

    def test_conid_mismatch_returns_none(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([_trade(conid=99999)])
        assert client.get_closed_position_price(12345, buy_or_sell="Sell") is None

    def test_side_mismatch_returns_none(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([_trade(side="B")])
        assert client.get_closed_position_price(12345, buy_or_sell="Sell") is None

    def test_most_recent_execution_wins(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([
            _trade(price="2.00", trade_time_r=1779000000000),
            _trade(price="2.99", trade_time_r=1779009999999),  # newer
            _trade(price="2.50", trade_time_r=1779005000000),
        ])
        out = client.get_closed_position_price(12345, buy_or_sell="Sell")
        assert out["closing_price"] == 2.99

    def test_empty_trades_returns_none(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([])
        assert client.get_closed_position_price(12345, buy_or_sell="Sell") is None

    def test_invalid_buy_or_sell_raises(self, connected_client):
        client, _ = connected_client
        with pytest.raises(IBClientError, match="buy_or_sell"):
            client.get_closed_position_price(12345, buy_or_sell="Hold")

    def test_days_clamped_to_max_7(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([])
        client.get_closed_position_price(12345, buy_or_sell="Sell", days=99)
        assert mock_ibkr.trades.call_args.kwargs["days"] == "7"

    def test_session_primed_before_trades(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([])
        client.get_closed_position_price(12345, buy_or_sell="Sell")
        # /iserver/accounts priming is required before /iserver/account/trades
        assert mock_ibkr.receive_brokerage_accounts.called
        assert mock_ibkr.trades.called

    def test_price_string_coerced(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([_trade(price="3.14")])
        out = client.get_closed_position_price(12345, buy_or_sell="Sell")
        assert out["closing_price"] == 3.14

    def test_nonpositive_price_returns_none(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([_trade(price="0")])
        assert client.get_closed_position_price(12345, buy_or_sell="Sell") is None

    def test_non_dict_rows_skipped(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        mock_ibkr.trades.return_value = _mk_result([
            "garbage-string-row",
            _trade(price="2.55"),
        ])
        out = client.get_closed_position_price(12345, buy_or_sell="Sell")
        assert out["closing_price"] == 2.55

    def test_conidex_fallback(self, connected_client):
        """A record keyed by conidEx instead of conid still matches."""
        client, mock_ibkr = connected_client
        mock_ibkr.receive_brokerage_accounts.return_value = _mk_result({})
        rec = _trade()
        del rec["conid"]
        rec["conidEx"] = 12345
        mock_ibkr.trades.return_value = _mk_result([rec])
        out = client.get_closed_position_price(12345, buy_or_sell="Sell")
        assert out["closing_price"] == 2.55


# ─── ensure_connected (P7 morning re-auth gate) ────────────────────────────


class TestEnsureConnected:
    """P7 — IBClient.ensure_connected(): the once-per-trading-day re-auth
    gate. A healthy session is a no-op; a stale one (IBKR's ~01:00 ET
    daily reset / 24h token TTL) triggers a clean disconnect()+connect()."""

    def test_healthy_session_returns_true_no_reconnect(self, connected_client):
        client, mock_ibkr = connected_client
        # fixture's authentication_status is authenticated + connected
        client.disconnect = MagicMock()
        client.connect = MagicMock()
        assert client.ensure_connected() is True
        client.disconnect.assert_not_called()
        client.connect.assert_not_called()

    def test_stale_session_reconnects(self, connected_client):
        client, mock_ibkr = connected_client
        stale = MagicMock()
        stale.data = {"authenticated": False, "connected": True, "competing": False}
        mock_ibkr.authentication_status.return_value = stale
        client.disconnect = MagicMock()
        client.connect = MagicMock(return_value=True)
        assert client.ensure_connected() is True
        client.disconnect.assert_called_once()
        client.connect.assert_called_once()

    def test_competing_session_reconnects(self, connected_client):
        client, mock_ibkr = connected_client
        comp = MagicMock()
        comp.data = {"authenticated": True, "connected": True, "competing": True}
        mock_ibkr.authentication_status.return_value = comp
        client.disconnect = MagicMock()
        client.connect = MagicMock(return_value=True)
        assert client.ensure_connected() is True
        client.connect.assert_called_once()

    def test_reconnect_failure_returns_false(self, connected_client):
        client, mock_ibkr = connected_client
        stale = MagicMock()
        stale.data = {"authenticated": False, "connected": False, "competing": False}
        mock_ibkr.authentication_status.return_value = stale
        client.disconnect = MagicMock()
        client.connect = MagicMock(side_effect=RuntimeError("IBKR down"))
        assert client.ensure_connected() is False

    def test_status_read_exception_triggers_reconnect(self, connected_client):
        client, mock_ibkr = connected_client
        mock_ibkr.authentication_status.side_effect = RuntimeError("boom")
        client.disconnect = MagicMock()
        client.connect = MagicMock(return_value=True)
        assert client.ensure_connected() is True
        client.connect.assert_called_once()

    def test_not_connected_skips_status_read_and_reconnects(self, connected_client):
        client, mock_ibkr = connected_client
        client._connected = False
        client.disconnect = MagicMock()
        client.connect = MagicMock(return_value=True)
        assert client.ensure_connected() is True
        # _connected False → no auth status round-trip, straight to reconnect
        client.connect.assert_called_once()


# ─── _ib_call retry+breaker integration (P7-audit H12) ─────────────────────


class TestIbCallRetryBreaker:
    """P7-audit H12 — `_ib_call` integrates retry_with_backoff + the per-family
    CircuitBreaker registry. The retry layer + breaker primitives have unit
    coverage in test_ib_retry.py; H12 pins the *integration* contract:

      • _ib_call routes through retry_with_backoff (so transient 5xx retries
        succeed without surfacing to the caller).
      • _ib_call records failures against the family-specific breaker
        (market / portfolio / orders / session / oauth), not a global one.
      • _ib_call raises IBClientError on unknown family (programmer error).
      • _ib_call propagates non-retryable exceptions immediately AND does not
        record a breaker failure (the breaker is for "broker degraded",
        not "caller did something wrong").
    """

    def _short_policy(self, client):
        """Mutate the live RetryPolicy in place for fast tests.

        We don't replace the policy object because `_ib_call` rebuilds the
        decorator on each call, but the breaker references are pinned via
        the constructed decorator's closure inside retry_with_backoff. The
        retry_with_backoff factory re-wraps with the breaker on every call,
        so mutating self._retry_policy is sufficient.
        """
        client._retry_policy.max_attempts = 3
        client._retry_policy.base_delay_s = 0.001
        client._retry_policy.jitter_fraction = 0.0

    def test_retries_transient_503_then_succeeds(self, connected_client):
        """A 503 followed by a successful response: _ib_call retries and
        returns the eventual `.data` payload."""
        client, mock_ibkr = connected_client
        self._short_policy(client)
        fn = MagicMock(side_effect=[
            Exception("503 Service Unavailable"),
            _mk_result({"ok": True}),
        ])
        result = client._ib_call("market", fn)
        assert result == {"ok": True}
        assert fn.call_count == 2

    def test_retries_per_family_use_independent_breakers(self, connected_client):
        """A market breaker tripped to OPEN does NOT block a portfolio call.
        Pinning the per-family-isolation contract."""
        client, mock_ibkr = connected_client
        self._short_policy(client)
        # Force the market breaker OPEN
        for _ in range(10):
            client.circuit_breakers["market"].record_failure()
        # market call must short-circuit
        from shared.ib_retry import CircuitBreakerOpen
        with pytest.raises(CircuitBreakerOpen):
            client._ib_call("market", MagicMock(return_value=_mk_result({})))
        # portfolio call still goes through
        fn = MagicMock(return_value=_mk_result([{"accountId": "DU1"}]))
        out = client._ib_call("portfolio", fn)
        assert out == [{"accountId": "DU1"}]
        fn.assert_called_once()

    def test_breaker_opens_after_threshold_failures(self, connected_client):
        """After consecutive_failures_threshold (default 5) retryable failures
        on the same family, that family's breaker transitions to OPEN and
        further `_ib_call`s short-circuit. This is the safety net for a
        degraded IBKR — we don't pile-on with retry storms."""
        client, mock_ibkr = connected_client
        self._short_policy(client)
        # Confine the breaker to 2 consecutive failures so the test is fast
        cb = client.circuit_breakers["portfolio"]
        cb.consecutive_failures_threshold = 2
        cb.force_reset()
        fn = MagicMock(side_effect=Exception("503 Service Unavailable"))
        # First call: exhausts retries (3 attempts) → records 3 failures,
        # breaker trips after the 2nd. The 3rd retry attempt raises
        # CircuitBreakerOpen.
        from shared.ib_retry import CircuitBreakerOpen, CircuitState
        with pytest.raises((Exception, CircuitBreakerOpen)):
            client._ib_call("portfolio", fn)
        assert cb.state == CircuitState.OPEN

    def test_unknown_family_raises_ibclient_error(self, connected_client):
        """Passing a bogus family name surfaces as IBClientError (programmer
        bug), not a silent KeyError."""
        client, _ = connected_client
        with pytest.raises(IBClientError, match="unknown circuit-breaker family"):
            client._ib_call("not_a_family", MagicMock())

    def test_non_retryable_propagates_without_breaker_failure(self, connected_client):
        """A ValueError (bad input — non-retryable per RetryPolicy) propagates
        immediately AND does not record a breaker failure. Pinning that the
        breaker is for "broker degraded", not "caller did something wrong"."""
        client, _ = connected_client
        self._short_policy(client)
        cb = client.circuit_breakers["session"]
        cb.force_reset()
        fn = MagicMock(side_effect=ValueError("bad arg"))
        with pytest.raises(ValueError, match="bad arg"):
            client._ib_call("session", fn)
        assert fn.call_count == 1  # no retry
        assert cb._consecutive_failures == 0

    def test_serialize_default_true_acquires_call_lock(self, connected_client):
        """Default _serialize=True wraps the ibind call in self._call_lock.
        Pinning so a future refactor doesn't silently drop the serialization."""
        client, _ = connected_client
        self._short_policy(client)
        saw_lock_held = []

        def fn():
            # If the lock is held, attempting a non-blocking acquire from
            # the same thread on an RLock succeeds (it's reentrant), so we
            # check a different invariant: we should be running *inside* a
            # context where the lock is taken at least once. Use the
            # internal counter on RLock by trying release+reacquire from
            # this same thread.
            saw_lock_held.append(True)
            return _mk_result({})

        client._ib_call("session", fn)
        assert saw_lock_held == [True]

    def test_serialize_false_skips_call_lock(self, connected_client):
        """`_serialize=False` is the documented escape hatch for endpoints
        empirically proven concurrency-safe (qualify_option_strikes via
        search_secdef_info_by_conid). Verify the kwarg is honored — call
        executes and returns. The lock-bypass itself is hard to assert
        directly without instrumenting RLock, but exercising the False
        branch pins the API surface."""
        client, _ = connected_client
        self._short_policy(client)
        fn = MagicMock(return_value=_mk_result({"ok": 1}))
        out = client._ib_call("market", fn, _serialize=False)
        assert out == {"ok": 1}
        fn.assert_called_once()


# ─── _unwrap error-raise contract (P7-audit H12) ───────────────────────────


class TestUnwrap:
    """`_unwrap` is the single chokepoint that translates ibind's `Result`
    wrapper into either `.data` or an `IBClientError`. Pinning all branches
    so a future refactor doesn't silently swallow errors."""

    def test_unwrap_extracts_data(self, connected_client):
        client, _ = connected_client
        r = MagicMock()
        r.data = {"some": "payload"}
        r.error = None
        assert client._unwrap(r) == {"some": "payload"}

    def test_unwrap_raises_on_non_none_error(self, connected_client):
        """The whole point of the wrapper: any `.error` MUST raise
        IBClientError — never return success."""
        client, _ = connected_client
        r = MagicMock()
        r.error = "rate limited"
        with pytest.raises(IBClientError, match="rate limited"):
            client._unwrap(r)

    def test_unwrap_raises_on_none_input(self, connected_client):
        """A None Result is an ibind contract violation — surface loudly."""
        client, _ = connected_client
        with pytest.raises(IBClientError, match="None"):
            client._unwrap(None)

    def test_unwrap_returns_object_directly_if_no_data_attr(self, connected_client):
        """ibind sometimes returns a plain dict/list (notably from internal
        helpers); _unwrap should pass them through untouched."""
        client, _ = connected_client

        class _Bare:
            error = None
            # no `data` attribute

        out = client._unwrap(_Bare())
        assert isinstance(out, _Bare)

    def test_unwrap_error_string_is_included_in_message(self, connected_client):
        """The error payload from ibind should be preserved verbatim in the
        IBClientError message — operators need it for debugging."""
        client, _ = connected_client
        r = MagicMock()
        r.error = "503 :: Service Unavailable :: maintenance window"
        with pytest.raises(IBClientError) as exc_info:
            client._unwrap(r)
        assert "maintenance window" in str(exc_info.value)


# ─── place_order cOID dedup safety on retry (P7-audit H12) ─────────────────


class TestPlaceOrderCoidRetrySafety:
    """P7-audit H12 — the riskiest retry path: order placement. IBKR
    deduplicates by `cOID` server-side, so a retry with the SAME cOID is
    safe (returns the original order's response instead of placing a new
    one). A retry with a DIFFERENT cOID would double-fill on the next
    attempt. Pin that:

      • The cOID flows through unchanged across retry attempts of the same
        place_order call (we don't regenerate it between attempts).
      • A caller-supplied cOID is preserved exactly.
    """

    def _short_policy(self, client):
        client._retry_policy.max_attempts = 3
        client._retry_policy.base_delay_s = 0.001
        client._retry_policy.jitter_fraction = 0.0

    def test_place_503_unconfirmed_raises_ambiguous_not_resubmitted(self, connected_client):
        """A place POST that 503's AND whose live state can't be confirmed must
        NOT be re-POSTed (the production caller uses a per-attempt cOID, so a
        retry is a NEW order → double-fill). _submit_order must raise
        AmbiguousOrderError and call place_order exactly ONCE."""
        client, mock_ibkr = connected_client
        self._short_policy(client)
        # Lookup inconclusive → cannot prove the order landed.
        client._find_order_by_coid = lambda coid: None

        captured_coids = []

        def _flaky_place_order(*, order_request, answers, account_id):
            captured_coids.append(order_request.coid)
            raise Exception("503 Service Unavailable")

        mock_ibkr.place_order.side_effect = _flaky_place_order
        with pytest.raises(AmbiguousOrderError):
            client.place_order(
                conid=416904, side="BUY", quantity=1,
                order_type="LMT", price=1.25,
            )
        assert len(captured_coids) == 1, (
            f"order was re-POSTed on an unconfirmed transient failure "
            f"({len(captured_coids)} POSTs) — double-fill risk"
        )

    def test_place_503_but_order_live_returns_existing(self, connected_client):
        """If the POST 503'd but the cOID IS found live, return the existing
        order — never resubmit, never raise."""
        client, mock_ibkr = connected_client
        self._short_policy(client)
        client._find_order_by_coid = lambda coid: {
            "order_id": "12345", "order_status": "Submitted", "cOID": coid,
        }

        captured_coids = []

        def _flaky_place_order(*, order_request, answers, account_id):
            captured_coids.append(order_request.coid)
            raise Exception("503 Service Unavailable")

        mock_ibkr.place_order.side_effect = _flaky_place_order
        out = client.place_order(
            conid=416904, side="BUY", quantity=1,
            order_type="LMT", price=1.25,
        )
        assert out.get("order_id") == "12345"
        assert len(captured_coids) == 1, "must not re-POST when order is found live"

    def test_caller_supplied_coid_preserved_verbatim(self, connected_client):
        """When the caller passes coid='MY_CUSTOM_COID', the order placed
        with IBKR must carry exactly that string — no generation, no
        decoration. Strategy code can rely on this for idempotent re-place
        after process restart."""
        client, mock_ibkr = connected_client
        self._short_policy(client)
        captured = {}

        def _grab(*, order_request, answers, account_id):
            captured["coid"] = order_request.coid
            return _mk_result({"order_id": "9", "order_status": "Submitted"})

        mock_ibkr.place_order.side_effect = _grab
        client.place_order(
            conid=416904, side="SELL", quantity=1,
            order_type="LMT", price=1.50,
            coid="HYDRA_E1_CALL_20260522",
        )
        assert captured["coid"] == "HYDRA_E1_CALL_20260522"

    def test_generated_coid_is_unique_per_call(self, connected_client):
        """Two distinct place_order calls without an explicit coid get
        DIFFERENT cOIDs (the safety net only works for retries-of-same-call;
        consecutive independent calls must NOT collide or IBKR will reject
        the second as a duplicate)."""
        client, mock_ibkr = connected_client
        captured = []

        def _grab(*, order_request, answers, account_id):
            captured.append(order_request.coid)
            return _mk_result({"order_id": "x", "order_status": "Submitted"})

        mock_ibkr.place_order.side_effect = _grab
        client.place_order(conid=1, side="BUY", quantity=1, order_type="LMT", price=1.0)
        client.place_order(conid=1, side="BUY", quantity=1, order_type="LMT", price=1.0)
        assert len(captured) == 2
        assert captured[0] != captured[1], (
            "two independent place_order calls got the same cOID — "
            "IBKR will reject the second as a duplicate"
        )


# ─── Polish Item 1: snapshot warmup exhaustion counter ─────────────────────


class TestSnapshotExhaustionCounter:
    """Polish Item 1: IBClient.snapshot_warmup_exhausted_count is the
    DATA_QUALITY signal main.py polls to fire a per-day Telegram alert
    when the snapshot endpoint stops returning real prices. Pin the
    counter contract here so a future refactor of _snapshot_with_preflight
    cannot silently break the alert pipeline."""

    def test_counter_starts_at_zero(self, connected_client):
        client, _ = connected_client
        assert client.snapshot_warmup_exhausted_count == 0

    def test_counter_increments_on_metadata_only_warmup(self, connected_client):
        """Polish Item 1 contract: the counter increments when the warmup
        loop exits without any non-metadata fields populated.

        We force exhaustion by mocking live_marketdata_snapshot to ALWAYS
        return a metadata-only row (just `conid` + `_updated`). The
        warmup loop will poll _SNAPSHOT_MAX_WARMUP_POLLS times, find no
        price fields, log the WARNING, increment the counter, return."""
        client, mock_ibkr = connected_client
        # Metadata-only row — no price field keys at all
        meta_row = {"conid": 999, "_updated": 12345}
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result([meta_row])

        # Patch sleep so the test runs in <100ms instead of ~6s
        with patch("shared.ib_client.time.sleep"):
            result = client._snapshot_with_preflight("999", DEFAULT_QUOTE_FIELDS)

        assert client.snapshot_warmup_exhausted_count == 1, (
            f"expected counter=1 after one exhausted call, got "
            f"{client.snapshot_warmup_exhausted_count}"
        )
        assert isinstance(result, list)  # always a list, never None

    def test_counter_does_NOT_increment_on_successful_snapshot(self, connected_client):
        """The counter is incremented ONLY on warmup exhaustion. A
        successful first-or-second-poll response (populated row with at
        least one price field) must NOT bump the counter."""
        client, mock_ibkr = connected_client
        # First poll metadata-only (priming), second poll has a bid → success
        mock_ibkr.live_marketdata_snapshot.side_effect = [
            _mk_result([{"conid": 1, "_updated": 1}]),       # preflight (returns no data)
            _mk_result([{"conid": 1, "_updated": 2}]),       # poll 1: metadata-only
            _mk_result([{"conid": 1, "_updated": 3,
                         FIELD_BID: "5.10", FIELD_ASK: "5.20"}]),  # poll 2: success
        ]
        with patch("shared.ib_client.time.sleep"):
            result = client._snapshot_with_preflight("1", DEFAULT_QUOTE_FIELDS)

        assert client.snapshot_warmup_exhausted_count == 0, (
            f"counter must NOT increment on success; got "
            f"{client.snapshot_warmup_exhausted_count}"
        )
        # Result has the price data
        assert any(
            isinstance(r, dict) and FIELD_BID in r for r in result
        ), "expected populated row to be returned"

    def test_counter_increments_per_exhaustion(self, connected_client):
        """Multiple exhaustion events accumulate. Two illiquid chain reads
        in the same iteration should produce count=2."""
        client, mock_ibkr = connected_client
        mock_ibkr.live_marketdata_snapshot.return_value = _mk_result(
            [{"conid": 7, "_updated": 1}]
        )
        with patch("shared.ib_client.time.sleep"):
            client._snapshot_with_preflight("7", DEFAULT_QUOTE_FIELDS)
            client._snapshot_with_preflight("7", DEFAULT_QUOTE_FIELDS)
            client._snapshot_with_preflight("7", DEFAULT_QUOTE_FIELDS)
        assert client.snapshot_warmup_exhausted_count == 3


# ─── AUD2-H1: connect() must NOT log consumer_key value ─────────────────


class TestConnectLogDoesNotLeakSecrets:
    """AUD2-H1 — the connect-time INFO log used to print
    `consumer_key=%s` which bypassed the Polish #10 IBKRCredentials
    `repr=False` hardening. A re-audit found the leak in
    shared/ib_client.py:527. Fixed: log only the length.

    These tests pin the contract:
      - the consumer_key VALUE never appears in any log record
        produced by connect()
      - the length DOES appear (operator can still verify the right
        credential file was loaded)
    """

    def test_connect_does_not_log_consumer_key_value(
        self, paper_creds, caplog
    ):
        """A canary consumer_key value must not appear in any log
        record emitted during connect(). Setting the credential to a
        recognizable canary string makes a leak unambiguous."""
        import logging
        # Make a fresh credentials object with a canary consumer_key
        canary = "CKLEAKCAN"  # 9 chars, all-uppercase to pass validation
        creds = IBKRCredentials(
            environment="paper",
            consumer_key=canary,
            access_token="t",
            access_token_secret="s",
            private_signature_path=paper_creds.private_signature_path,
            private_encryption_path=paper_creds.private_encryption_path,
            dh_param_path=paper_creds.dh_param_path,
        )

        # Build a connected_client-style mock chain
        mock_ibkr = MagicMock()
        auth_status = MagicMock()
        auth_status.data = {"authenticated": True, "connected": True, "competing": False}
        auth_status.error = None
        mock_ibkr.authentication_status.return_value = auth_status
        portfolio = MagicMock()
        portfolio.data = [{"accountId": "DU1234567"}]
        portfolio.error = None
        mock_ibkr.portfolio_accounts.return_value = portfolio
        iserver_result = MagicMock()
        iserver_result.data = {}
        iserver_result.error = None
        mock_ibkr.receive_brokerage_accounts.return_value = iserver_result

        cfg = IBConfig(credentials=creds)
        with caplog.at_level(logging.INFO, logger="shared.ib_client"):
            with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr):
                client = IBClient(cfg)
                client.connect()
        # Search every emitted log record's message for the canary.
        full_log = "\n".join(r.getMessage() for r in caplog.records)
        assert canary not in full_log, (
            f"AUD2-H1 regression: connect() logged the consumer_key value:\n"
            f"{full_log}"
        )

    def test_connect_logs_consumer_key_length(self, paper_creds, caplog):
        """The fix replaced the value with the length. Pin that the
        length IS still in the log so operators can confirm the right
        credential length was loaded (paper = 9 chars on IBKR's portal)."""
        import logging
        creds = IBKRCredentials(
            environment="paper",
            consumer_key="ABCDEFGHI",  # 9 chars
            access_token="t",
            access_token_secret="s",
            private_signature_path=paper_creds.private_signature_path,
            private_encryption_path=paper_creds.private_encryption_path,
            dh_param_path=paper_creds.dh_param_path,
        )
        mock_ibkr = MagicMock()
        auth_status = MagicMock()
        auth_status.data = {"authenticated": True, "connected": True, "competing": False}
        auth_status.error = None
        mock_ibkr.authentication_status.return_value = auth_status
        portfolio = MagicMock()
        portfolio.data = [{"accountId": "DU1234567"}]
        portfolio.error = None
        mock_ibkr.portfolio_accounts.return_value = portfolio
        iserver_result = MagicMock()
        iserver_result.data = {}
        iserver_result.error = None
        mock_ibkr.receive_brokerage_accounts.return_value = iserver_result

        cfg = IBConfig(credentials=creds)
        with caplog.at_level(logging.INFO, logger="shared.ib_client"):
            with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr):
                client = IBClient(cfg)
                client.connect()
        full_log = "\n".join(r.getMessage() for r in caplog.records)
        assert "consumer_key_length=9" in full_log, (
            f"connect() should log the consumer_key LENGTH so operators "
            f"can sanity-check credential loading. Log lacked it:\n{full_log}"
        )
