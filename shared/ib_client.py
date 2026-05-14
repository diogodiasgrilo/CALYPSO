"""IB adapter for CALYPSO — Phase A standalone module.

Wraps Voyz/ibind 0.1.23 against IBKR's Client Portal Web API using OAuth 1.0a
(no IB Gateway, no IBC, no weekly phone tap — per the architecture pivot
documented in docs/migration/SAXO_TO_IB_MIGRATION_PLAN.md).

Phase A scope (this file's current state):
  ✓ Connection lifecycle: connect / disconnect / is_connected
  ✓ 3-stage auth: LST → ssodh/init → auth/status (verified per migration plan)
  ✓ Account discovery
  ✓ pyCrypto fast-fail safety assertion
  ✓ Saxo-compat properties (`client_key`)
  ─ Read methods (quotes, positions, chains)  — Phase A.3, separate commit
  ─ Write methods (place_order, place_iron_condor) — Phase A.4
  ─ WebSocket streaming with smd refresh    — Phase A.5
  ─ Order-state reconcile on reconnect      — Phase A.7
  ─ Retry + circuit breaker                 — Phase A.8

Phase B will introduce shared/broker/{interface,saxo_adapter,ibkr_adapter}.py
to give HYDRA a single broker-agnostic interface. Until then this module is
standalone — NOT imported by HYDRA, NOT importable into the production bot.

Mapping from SaxoClient methods (kept in module-level docstring so Phase B
authors don't have to chase it down): see
docs/migration/SAXO_TO_IB_MIGRATION_PLAN.md §11.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from shared.ib_oauth import (
    IBKRCredentials,
    assert_safe_crypto_backend,
    build_oauth1a_config,
)

logger = logging.getLogger(__name__)


# ─── Public exceptions ──────────────────────────────────────────────────────


class IBClientError(Exception):
    """Base exception for IBClient. Catch this to handle any IB-side failure."""


class IBAuthError(IBClientError):
    """OAuth or brokerage session failure (LST handshake / ssodh / auth/status).

    Raised when:
      - LST handshake returns 401 with `invalid consumer` (pre-activation OR
        wrong consumer key)
      - ssodh/init returns authenticated=false / connected=false
      - auth/status returns competing=true (another session active on the
        account elsewhere)
    """


class IBConnectionError(IBClientError):
    """Network / protocol failure reaching api.ibkr.com.

    Raised on connection refused, DNS error, TLS handshake fail, etc. Wraps
    the underlying ibind/requests exception in `self.__cause__`.
    """


# ─── Config dataclass ───────────────────────────────────────────────────────


@dataclass
class IBConfig:
    """Loaded configuration for an IBClient instance.

    Phase A: constructed manually from env vars in dev/test.
    Phase B: built by shared/broker/__init__.py factory from JSON config +
             GCP Secret Manager values.

    Fields:
      credentials: IBKR OAuth credentials bundle (loaded by ib_oauth.py)
      account_id: optional pinned account ID; if None, discovered from
                  managedAccounts on connect (typical case for single-account
                  setups)
      tickle_interval_seconds: ibind's Tickler thread cadence to keep the
                  brokerage session warm (default 60s; IBKR idle timeout
                  is ~6 minutes)
      connection_timeout_seconds: hard cap on initial connect handshake;
                  beyond this we raise IBConnectionError
      debug_log_payloads: if True, log full IBKR responses at DEBUG level.
                  NEVER enable in production (responses may contain account
                  values, order IDs, etc.)
    """
    credentials: IBKRCredentials
    account_id: Optional[str] = None
    tickle_interval_seconds: int = 60
    connection_timeout_seconds: float = 30.0
    debug_log_payloads: bool = False

    # Phase A.7 will add: reconcile_state_path: Path  ─ where to load/save
    #   bot's view of open orders/positions for cross-check on reconnect


# ─── Main client ────────────────────────────────────────────────────────────


class IBClient:
    """Synchronous IBKR adapter wrapping ibind 0.1.23.

    Public API designed to match SaxoClient where reasonable so the Phase B
    `shared/broker/ibkr_adapter.py` shim is mechanical. HYDRA strategy code
    does NOT import this class directly; it goes through the broker
    abstraction.

    Lifecycle:
        client = IBClient(config)
        client.connect()                # 3-stage OAuth+session init
        try:
            # ... do work via client.get_quote / place_order / etc ...
            pass
        finally:
            client.disconnect()

    Thread safety: ibind's IbkrClient is single-threaded; we serialize calls
    via an internal lock so multiple callers from one process don't race.
    StreamingManager (Phase A.5) runs on its own thread but only writes to
    its own internal cache, so no cross-talk.

    Saxo→IB method mapping kept in migration plan §11.
    """

    def __init__(self, config: IBConfig):
        self.cfg = config
        self._client = None  # type: ignore[assignment]  # set by connect()
        self._connected = False
        self._account_id: Optional[str] = config.account_id
        # Lock for serializing ibind calls across threads.
        # ibind is documented as not thread-safe per github.com/Voyz/ibind/issues.
        self._call_lock = threading.RLock()

    # ─── Connection lifecycle ──────────────────────────────────────────────

    def connect(self) -> bool:
        """3-stage connect: LST handshake → brokerage session → auth/status.

        Returns True on success; raises IBAuthError or IBConnectionError on
        failure. After return, client is ready to place orders and read data.

        Phase A version: synchronous, blocks until all 3 stages pass or fails.
        No automatic retry — caller decides whether to retry. Phase A.8 will
        add an outer retry+circuit-breaker layer.
        """
        assert_safe_crypto_backend()

        logger.info(
            "IBClient connecting — environment=%s consumer_key=%s",
            self.cfg.credentials.environment,
            self.cfg.credentials.consumer_key,
        )

        # Stage 1: LST handshake (happens inside IbkrClient.__init__ when
        # use_oauth=True). On failure: 401 with invalid consumer (pending
        # activation) or other error.
        try:
            from ibind import IbkrClient

            oauth_cfg = build_oauth1a_config(
                self.cfg.credentials,
                init_brokerage_session=True,
            )
            with self._call_lock:
                self._client = IbkrClient(
                    use_oauth=True,
                    oauth_config=oauth_cfg,
                )
        except Exception as exc:
            err_str = str(exc).lower()
            if any(k in err_str for k in ("401", "unauthorized", "invalid consumer", "invalid_token")):
                raise IBAuthError(
                    f"LST handshake failed (pre-activation OR wrong consumer key): {exc}"
                ) from exc
            raise IBConnectionError(
                f"Connection failed at LST stage: {exc}"
            ) from exc

        logger.info("IBClient stage 1/3 ok: live session token issued")

        # Stage 2: brokerage session (init_brokerage_session=True triggers
        # ssodh/init inside ibind on construction; verify by checking
        # authentication status).
        # Stage 3: explicit auth/status check.
        try:
            with self._call_lock:
                status_result = self._client.authentication_status()
            status_data = getattr(status_result, "data", {}) or {}
            authenticated = bool(status_data.get("authenticated", False))
            connected = bool(status_data.get("connected", False))
            competing = bool(status_data.get("competing", False))
            logger.info(
                "IBClient stage 3/3 auth status: authenticated=%s connected=%s competing=%s",
                authenticated, connected, competing,
            )
            if not (authenticated and connected):
                raise IBAuthError(
                    f"Auth status check failed: data={status_data!r}"
                )
            if competing:
                raise IBAuthError(
                    "Auth status reports competing session — another client "
                    "is logged into this account. Sign out elsewhere, retry."
                )
        except IBAuthError:
            raise
        except Exception as exc:
            raise IBAuthError(
                f"auth/status check errored after LST success: {exc}"
            ) from exc

        # Resolve account ID if not pinned
        if not self._account_id:
            self._account_id = self._discover_account_id()

        self._connected = True
        logger.info(
            "IBClient connected successfully — account=%s",
            self._account_id,
        )
        return True

    def _discover_account_id(self) -> str:
        """Look up the IBKR account ID via portfolio_accounts.

        For most single-account retail users this returns one DU* (paper) or
        Uxxxx* (live) account. Multi-account setups need to pin via
        IBConfig.account_id.
        """
        with self._call_lock:
            result = self._client.portfolio_accounts()
        data = getattr(result, "data", []) or []
        if not data:
            raise IBAuthError(
                "No managed accounts returned by IBKR — likely an account "
                "permission issue or fresh activation propagating"
            )
        # Pick the first account; warn if multiple
        if len(data) > 1:
            logger.warning(
                "IBClient: multiple accounts visible (%d) — using first; "
                "pin via IBConfig.account_id to be explicit. Accounts: %s",
                len(data),
                [a.get("accountId") for a in data],
            )
        return data[0]["accountId"]

    def disconnect(self) -> None:
        """Tear down the brokerage session cleanly.

        Calls ibind's logout/shutdown if available. Idempotent — safe to call
        even if connect() failed partway through.
        """
        if not self._client:
            return
        try:
            with self._call_lock:
                # ibind's shutdown stops the Tickler thread + clears session
                if hasattr(self._client, "stop_tickler"):
                    self._client.stop_tickler()
                if hasattr(self._client, "close_session"):
                    self._client.close_session()
        except Exception as exc:
            logger.warning("IBClient disconnect cleanup non-fatal error: %s", exc)
        finally:
            self._connected = False
            logger.info("IBClient disconnected")

    def is_connected(self) -> bool:
        """Cheap check — returns last-known state. Does NOT round-trip to IBKR.

        For an authoritative live check, call check_auth_status() (Phase A.3).
        """
        return self._connected

    # ─── Account properties (Saxo-compat) ─────────────────────────────────

    @property
    def account_id(self) -> str:
        """Resolved account ID. Raises if connect() hasn't been called yet."""
        if not self._account_id:
            raise IBClientError(
                "account_id not yet resolved — call connect() first"
            )
        return self._account_id

    @property
    def client_key(self) -> str:
        """Saxo-compat alias for account_id.

        SaxoClient exposed .client_key as the account identifier. Some HYDRA
        code reads this for use in order-status URLs. Keeping the name aligned
        so the Phase B broker abstraction can swap in IBClient without
        renaming call sites.
        """
        return self.account_id

    @property
    def is_paper(self) -> bool:
        """True if connected to paper trading.

        Inferred from the environment field of the loaded credentials. Always
        correct when credentials are loaded via ib_oauth.load_credentials()
        which keys files per-environment.
        """
        return self.cfg.credentials.environment == "paper"

    @property
    def is_live(self) -> bool:
        """True if connected to live trading. Inverse of is_paper."""
        return self.cfg.credentials.environment == "live"

    # ─── Context manager support ──────────────────────────────────────────

    def __enter__(self) -> "IBClient":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    def __repr__(self) -> str:
        env = self.cfg.credentials.environment
        connected = "connected" if self._connected else "disconnected"
        return f"<IBClient env={env} {connected} account={self._account_id or '?'}>"
