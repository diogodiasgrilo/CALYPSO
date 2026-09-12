"""Tests for shared.ib_client.

Phase A.2 scope:
  • IBConfig construction
  • IBClient lifecycle: connect / disconnect / is_connected / context manager
  • 3-stage auth: LST → ssodh/init → auth/status
  • Account discovery via portfolio_accounts
  • Saxo-compat property aliases (client_key, is_paper, is_live)
  • Error classification: IBAuthError vs IBConnectionError

All tests use mocked ibind — no live IBKR calls. The integration smoke test
(Phase A.10) is separate at tests/integration/test_ib_paper_smoke.py and
requires an activated paper OAuth credential.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_client import (
    IBAuthError,
    IBClient,
    IBClientError,
    IBConfig,
    IBConnectionError,
)
from shared.ib_oauth import IBKRCredentials


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def paper_creds(tmp_path):
    """Fake-but-structurally-valid paper credentials.

    File paths point to fake PEM files in tmp_path so .validate_paths()
    succeeds during build_oauth1a_config() — but the file contents are
    fake (which is fine because we mock ibind's IbkrClient entirely).
    """
    sig = tmp_path / "private_signature.pem"
    enc = tmp_path / "private_encryption.pem"
    dh = tmp_path / "dhparam.pem"
    sig.write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n")
    enc.write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n")
    # extract_dh_prime needs a REAL openssl-parseable file. Use a real
    # 1024-bit DH params (~1 second to generate, cached per test session).
    import subprocess
    subprocess.run(
        ["openssl", "dhparam", "-out", str(dh), "1024"],
        check=True, capture_output=True,
    )
    return IBKRCredentials(
        environment="paper",
        consumer_key="CALYPSOPP",
        access_token="fake_access_token",
        access_token_secret="fake_access_secret",
        private_signature_path=sig,
        private_encryption_path=enc,
        dh_param_path=dh,
    )


@pytest.fixture
def paper_config(paper_creds):
    return IBConfig(credentials=paper_creds)


@pytest.fixture
def mock_ibkr_client():
    """Mock for ibind.IbkrClient.

    Default state: construction succeeds (LST handshake ok), auth/status
    returns fully-authenticated, portfolio_accounts returns one DU account.
    Individual tests override as needed.
    """
    client = MagicMock()
    # ibind's Result-like object: .data + .error (None on success). Phase
    # A.8 routes portfolio_accounts through _ib_call → _unwrap, which
    # checks .error — must be explicit None on the mock or _unwrap raises
    # on the MagicMock auto-attribute (which is truthy).
    auth_status_result = MagicMock()
    auth_status_result.data = {
        "authenticated": True,
        "connected": True,
        "competing": False,
    }
    auth_status_result.error = None
    client.authentication_status.return_value = auth_status_result

    portfolio_result = MagicMock()
    portfolio_result.data = [{"accountId": "DU1234567"}]
    portfolio_result.error = None
    client.portfolio_accounts.return_value = portfolio_result

    return client


# ─── IBConfig ───────────────────────────────────────────────────────────────


class TestIBConfig:
    def test_defaults(self, paper_creds):
        cfg = IBConfig(credentials=paper_creds)
        assert cfg.account_id is None  # discovered on connect
        assert cfg.tickle_interval_seconds == 60
        assert cfg.connection_timeout_seconds == 30.0
        assert cfg.debug_log_payloads is False

    def test_account_id_can_be_pinned(self, paper_creds):
        cfg = IBConfig(credentials=paper_creds, account_id="DU9999999")
        assert cfg.account_id == "DU9999999"


# ─── IBClient lifecycle ────────────────────────────────────────────────────


class TestIBClientConnect:
    def test_successful_connect_discovers_account(self, paper_config, mock_ibkr_client):
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            assert client.connect() is True
            assert client.is_connected()
            assert client.account_id == "DU1234567"

    def test_lst_handshake_invalid_consumer_raises_auth_error(self, paper_config):
        """Pre-activation: ibind raises an exception whose str contains 'invalid consumer'."""
        with patch("shared.ib_client.IbkrClient",
                   side_effect=Exception("401 Unauthorized: invalid consumer")):
            client = IBClient(paper_config)
            # Loose match: the IBAuthError message must reference 'invalid
            # consumer'. Phrasing of the surrounding diagnostic is allowed
            # to change without breaking this test.
            with pytest.raises(IBAuthError, match="invalid consumer"):
                client.connect()
            assert not client.is_connected()

    def test_lst_handshake_network_error_raises_connection_error(self, paper_config):
        """Non-401 errors are connection problems, not auth problems."""
        with patch("shared.ib_client.IbkrClient",
                   side_effect=ConnectionRefusedError("could not connect to api.ibkr.com")):
            client = IBClient(paper_config)
            with pytest.raises(IBConnectionError, match="LST stage"):
                client.connect()

    def test_lst_handshake_410_gone_is_retryable_not_auth(self, paper_config):
        """IBKR-audit #18: a 410 Gone at the LST stage is a revoked session
        (self-healing on reconnect), so it must surface as the RETRYABLE
        IBConnectionError — never the fatal IBAuthError that implies a
        wrong/pre-activation consumer key."""
        class _Gone(Exception):
            status_code = 410
        with patch("shared.ib_client.IbkrClient", side_effect=_Gone("410 Gone")):
            client = IBClient(paper_config)
            with pytest.raises(IBConnectionError, match="Session gone"):
                client.connect()
            assert not client.is_connected()

    def test_auth_status_410_gone_is_retryable_not_auth(
        self, paper_config, mock_ibkr_client,
    ):
        """IBKR-audit #18: a 410 during the stage-3 auth/status read is also a
        revoked session — the catch-all must NOT wrap it as IBAuthError."""
        class _Gone(Exception):
            status_code = 410
        mock_ibkr_client.authentication_status.side_effect = _Gone("410 Gone")
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            with pytest.raises(IBConnectionError, match="Session gone"):
                client.connect()

    def test_auth_status_not_authenticated_raises_auth_error(self, paper_config, mock_ibkr_client):
        """Stage 2/3: even if LST succeeded, ssodh/init may have failed silently."""
        mock_ibkr_client.authentication_status.return_value.data = {
            "authenticated": False,
            "connected": True,
            "competing": False,
        }
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            with pytest.raises(IBAuthError, match="Auth status check failed"):
                client.connect()

    def test_auth_status_competing_session_raises_auth_error(self, paper_config, mock_ibkr_client):
        """Another session is logged into this account elsewhere."""
        mock_ibkr_client.authentication_status.return_value.data = {
            "authenticated": True,
            "connected": True,
            "competing": True,  # someone else is logged in
        }
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            with pytest.raises(IBAuthError, match="competing session"):
                client.connect()

    def test_account_discovery_empty_raises_auth_error(self, paper_config, mock_ibkr_client):
        """No accounts visible — likely permission/activation issue."""
        mock_ibkr_client.portfolio_accounts.return_value.data = []
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            with pytest.raises(IBAuthError, match="No managed accounts"):
                client.connect()

    def test_pinned_account_id_skips_discovery(self, paper_creds, mock_ibkr_client):
        """If config.account_id is pinned, we don't call portfolio_accounts."""
        cfg = IBConfig(credentials=paper_creds, account_id="DU0000001")
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(cfg)
            client.connect()
            assert client.account_id == "DU0000001"
            mock_ibkr_client.portfolio_accounts.assert_not_called()

    def test_multi_account_warns_and_picks_first(
        self, paper_config, mock_ibkr_client, caplog,
    ):
        """If portfolio_accounts returns multiple accounts, pick [0] but
        log a warning so the operator pins via IBConfig.account_id."""
        mock_ibkr_client.portfolio_accounts.return_value.data = [
            {"accountId": "DU1111111"},
            {"accountId": "DU2222222"},
        ]
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            import logging
            with caplog.at_level(logging.WARNING):
                client.connect()
            assert client.account_id == "DU1111111"
            assert any("multiple accounts" in r.message for r in caplog.records)

    def test_authenticated_true_connected_false_raises(
        self, paper_config, mock_ibkr_client,
    ):
        """Mirror case to test_auth_status_not_authenticated: authenticated
        flips true but connected stayed false. Still must raise."""
        mock_ibkr_client.authentication_status.return_value.data = {
            "authenticated": True,
            "connected": False,
            "competing": False,
        }
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            with pytest.raises(IBAuthError, match="Auth status check failed"):
                client.connect()

    def test_competing_retries_once_after_5s_then_succeeds(
        self, paper_config, mock_ibkr_client,
    ):
        """First read shows competing=true (ssodh/init still mid-handoff);
        we sleep 5s and re-read; second read is clean and connect succeeds."""
        from unittest.mock import MagicMock as MM
        first_status = MM(); first_status.data = {
            "authenticated": True, "connected": True, "competing": True,
        }
        second_status = MM(); second_status.data = {
            "authenticated": True, "connected": True, "competing": False,
        }
        mock_ibkr_client.authentication_status.side_effect = [first_status, second_status]
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client), \
             patch("shared.ib_client.time.sleep") as mock_sleep:
            client = IBClient(paper_config)
            client.connect()
            assert client.is_connected()
            # 5s sleep was triggered
            mock_sleep.assert_called_once_with(5.0)
            # auth_status called twice
            assert mock_ibkr_client.authentication_status.call_count == 2


class TestIBClientDisconnect:
    def test_disconnect_before_connect_is_safe(self, paper_config):
        """Idempotent — should not raise even if connect() was never called."""
        client = IBClient(paper_config)
        client.disconnect()  # no raise
        assert not client.is_connected()

    def test_disconnect_after_connect_clears_state(self, paper_config, mock_ibkr_client):
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            assert client.is_connected()
            client.disconnect()
            assert not client.is_connected()
            # ibind 0.1.23: IbkrClient.close() runs oauth_shutdown() which
            # internally calls stop_tickler + logout. We invoke close()
            # directly, not stop_tickler().
            mock_ibkr_client.close.assert_called_once()

    def test_disconnect_swallows_cleanup_errors(self, paper_config, mock_ibkr_client):
        """Errors during shutdown shouldn't propagate."""
        mock_ibkr_client.close.side_effect = Exception("network gone")
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            client.disconnect()  # no raise
            assert not client.is_connected()

    def test_disconnect_stops_streaming_if_started(self, paper_config, mock_ibkr_client):
        """If StreamingManager was started (via .streaming lazy access),
        disconnect() must call .stop() on it."""
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            # Simulate a started streaming manager
            fake_streaming = MagicMock()
            client._streaming = fake_streaming
            client.disconnect()
            fake_streaming.stop.assert_called_once()
            # _streaming is cleared after teardown
            assert client._streaming is None

    def test_disconnect_shuts_down_ws_client_if_present(
        self, paper_config, mock_ibkr_client,
    ):
        """ws_client.shutdown() must be called if the WS was spun up."""
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            fake_ws = MagicMock()
            client._ws_client = fake_ws
            client.disconnect()
            fake_ws.shutdown.assert_called_once()
            assert client._ws_client is None

    def test_disconnect_streaming_stop_raise_does_not_block_ws_teardown(
        self, paper_config, mock_ibkr_client,
    ):
        """A failure mid-teardown (streaming.stop raises) must still let
        ws_client.shutdown + client.close run, and the final state still
        reaches _connected = False."""
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            fake_streaming = MagicMock()
            fake_streaming.stop.side_effect = RuntimeError("smd unsub failed")
            fake_ws = MagicMock()
            client._streaming = fake_streaming
            client._ws_client = fake_ws
            client.disconnect()  # no raise
            fake_streaming.stop.assert_called_once()
            fake_ws.shutdown.assert_called_once()
            assert not client.is_connected()


class TestContextManager:
    def test_with_block_connects_and_disconnects(self, paper_config, mock_ibkr_client):
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            with IBClient(paper_config) as client:
                assert client.is_connected()
            assert not client.is_connected()


# ─── Properties ─────────────────────────────────────────────────────────────


class TestProperties:
    def test_client_key_is_saxo_compat_alias_for_account_id(self, paper_config, mock_ibkr_client):
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            assert client.client_key == client.account_id == "DU1234567"

    def test_account_id_before_connect_raises(self, paper_config):
        client = IBClient(paper_config)
        with pytest.raises(IBClientError, match="not yet resolved"):
            _ = client.account_id

    def test_is_paper_true_for_paper_env(self, paper_config):
        client = IBClient(paper_config)
        assert client.is_paper is True
        assert client.is_live is False

    def test_is_live_true_for_live_env(self, paper_creds):
        live_creds = IBKRCredentials(
            environment="live",
            consumer_key=paper_creds.consumer_key,
            access_token=paper_creds.access_token,
            access_token_secret=paper_creds.access_token_secret,
            private_signature_path=paper_creds.private_signature_path,
            private_encryption_path=paper_creds.private_encryption_path,
            dh_param_path=paper_creds.dh_param_path,
        )
        client = IBClient(IBConfig(credentials=live_creds))
        assert client.is_live is True
        assert client.is_paper is False


class TestRepr:
    def test_repr_includes_env_and_state(self, paper_config):
        client = IBClient(paper_config)
        rep = repr(client)
        assert "paper" in rep
        assert "disconnected" in rep
        assert "account=?" in rep

    def test_repr_after_connect_shows_account(self, paper_config, mock_ibkr_client):
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            rep = repr(client)
            assert "connected" in rep
            assert "DU1234567" in rep


# ─── Phase A.8: retry + circuit breaker integration ────────────────────────


class TestRetryAndCircuitBreakers:
    """Verify _ib_call wires retry_with_backoff + per-family CircuitBreakers
    into the live IBClient surface (Phase A.8 wiring, 2026-05-16). Tests
    here use the connected_client pattern + tripped breakers to prove the
    operator-reset path works end-to-end.
    """

    def test_breaker_registry_has_all_six_families(
        self, paper_config, mock_ibkr_client,
    ):
        """Six canonical families: oauth/session/portfolio/market/history/
        orders. Production code routes via _ib_call(family, ...) — adding a new
        family without registering it here would IBClientError. 'history' is
        separate from 'market' so chart-data flakiness can't block live quotes
        (2026-06-29)."""
        from shared.ib_retry import CircuitBreaker
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            breakers = client.circuit_breakers
            assert set(breakers.keys()) == {
                "oauth", "session", "portfolio", "market", "history", "orders",
            }
            for name, br in breakers.items():
                assert isinstance(br, CircuitBreaker)
                assert br.name == f"ib.{name}"

    def test_circuit_breakers_property_returns_live_objects(
        self, paper_config, mock_ibkr_client,
    ):
        """The dict is a defensive copy but breakers themselves are live —
        operator can `client.circuit_breakers['market'].force_reset()` to
        clear an OPEN breaker without subclassing or reaching into _breakers.
        """
        from shared.ib_retry import CircuitState
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            # Trip a breaker manually
            br = client.circuit_breakers["market"]
            for _ in range(br.consecutive_failures_threshold):
                br.record_failure()
            assert br.state == CircuitState.OPEN
            # Re-fetching the property gives back the SAME live object
            assert client.circuit_breakers["market"] is br
            assert client.circuit_breakers["market"].state == CircuitState.OPEN
            # force_reset clears it
            br.force_reset()
            assert client.circuit_breakers["market"].state == CircuitState.CLOSED

    def test_retry_policy_property_is_mutable(
        self, paper_config, mock_ibkr_client,
    ):
        """Tests / production tuning happens via in-place mutation of the
        live RetryPolicy. max_attempts=1 effectively disables retry."""
        from shared.ib_retry import RetryPolicy
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            assert isinstance(client.retry_policy, RetryPolicy)
            client.retry_policy.max_attempts = 1
            assert client.retry_policy.max_attempts == 1

    def test_open_breaker_short_circuits_subsequent_calls(
        self, paper_config, mock_ibkr_client,
    ):
        """Once a breaker is OPEN, _ib_call raises CircuitBreakerOpen
        WITHOUT invoking the underlying ibind method. This is the
        production safety net that saved us from a 12+ call cascade on
        the 2026-05-16 paper smoke run."""
        from shared.ib_retry import CircuitBreakerOpen
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            # Trip orders breaker
            for _ in range(client.circuit_breakers["orders"].consecutive_failures_threshold):
                client.circuit_breakers["orders"].record_failure()
            # Now any orders-family call short-circuits BEFORE touching ibind
            call_count_before = mock_ibkr_client.order_status.call_count
            with pytest.raises(CircuitBreakerOpen, match="ib.orders"):
                client.get_order_status("foo")
            assert mock_ibkr_client.order_status.call_count == call_count_before, (
                "Tripped breaker should NOT have called ibind"
            )

    def test_breaker_reset_after_trip_allows_calls_through(
        self, paper_config, mock_ibkr_client,
    ):
        """Operator workflow: trip → reset → next call succeeds. Proves
        the breaker is not 'stuck' OPEN forever."""
        from shared.ib_retry import CircuitState
        # Make order_status return a sane mock
        result = MagicMock(); result.data = {"status": "Filled"}; result.error = None
        mock_ibkr_client.order_status.return_value = result
        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()
            br = client.circuit_breakers["orders"]
            for _ in range(br.consecutive_failures_threshold):
                br.record_failure()
            assert br.state == CircuitState.OPEN
            br.force_reset()
            # Call should succeed (mock returns Filled)
            status = client.get_order_status("foo")
            assert status == {"status": "Filled"}
            # Breaker stayed CLOSED throughout the successful call
            assert client.circuit_breakers["orders"].state == CircuitState.CLOSED

    def test_orders_family_is_not_abortable_on_shutdown(
        self, paper_config, mock_ibkr_client,
    ):
        """2026-08-03: _ib_call must pass abortable_on_shutdown=False for the
        'orders' family only — abandoning an in-flight order-placement retry
        mid-shutdown risks leaving a naked/partial leg untracked, a strictly
        worse outcome than a slow shutdown. Every other family is
        fast-abortable (this is what actually fixes the confirmed
        calypso-broker shutdown hang — ensure_connected is family='session').
        Spies on the real retry_with_backoff (not a stub) so the underlying
        retry behavior stays exercised, not just the call signature."""
        import shared.ib_client as ib_client_module
        from shared.ib_retry import retry_with_backoff as real_retry_with_backoff

        captured: list[bool] = []

        def _spy(*args, **kwargs):
            captured.append(kwargs.get("abortable_on_shutdown"))
            return real_retry_with_backoff(*args, **kwargs)

        with patch("shared.ib_client.IbkrClient", return_value=mock_ibkr_client):
            client = IBClient(paper_config)
            client.connect()

        with patch.object(ib_client_module, "retry_with_backoff", side_effect=_spy):
            client._ib_call("orders", lambda: "ok")
            client._ib_call("session", lambda: "ok")
            client._ib_call("market", lambda: "ok")
            client._ib_call("portfolio", lambda: "ok")
            client._ib_call("history", lambda: "ok")
            client._ib_call("oauth", lambda: "ok")

        assert captured == [False, True, True, True, True, True], (
            "only 'orders' should be non-abortable; every other family must "
            "be fast-abortable — got: " + repr(captured)
        )
