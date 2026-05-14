"""Broker factory — Phase B.4.

Single entry point for constructing a `BrokerInterface` from the
`BROKER=` environment variable. New bots / strategy entry points wire
through this instead of importing SaxoClient / IBClient directly; the
adapter choice becomes a one-line env flip.

Usage at a bot's main entry:

    from shared.broker import build_broker
    broker = build_broker()         # reads BROKER= env (default 'saxo')
    broker.connect()
    snap = broker.get_quote(conid_or_uic)
    ...

The factory does NOT change HYDRA's existing main.py — that path still
constructs SaxoClient directly to avoid breaking the live Saxo dry-run
on the VM. Strategies that want to opt into the abstraction:

  • Accept `broker: BrokerInterface` in their __init__ alongside (not
    replacing) `saxo_client`. Use the broker handle for read paths
    first; gradually migrate write paths in subsequent phases.

  • Or instantiate `broker = build_broker()` themselves at the top of
    their entry function before constructing the strategy.

Full HYDRA migration (replacing every `self.client.X` call with
`self.broker.X`) is tracked as Phase B.5 — explicitly out of scope for
B.4. B.4's job is to make the SWITCH cheap, not to flip it.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from shared.broker.interface import BrokerError, BrokerInterface


logger = logging.getLogger(__name__)


# Recognized values for the BROKER env var. Compared case-insensitively.
_VALID_BROKERS = {"saxo", "ibkr", "ib"}


def build_broker(
    *,
    env_var: str = "BROKER",
    default: str = "saxo",
    saxo_client=None,
    ib_client=None,
    ib_environment: str = "paper",
) -> BrokerInterface:
    """Construct the configured BrokerInterface adapter.

    Resolution order for the broker selection:
      1. `os.environ[env_var]` if set (lowercased + stripped)
      2. `default` parameter

    Resolution order for the underlying client:
      • If `saxo_client` / `ib_client` is supplied, use it directly.
        Useful for tests and bots that have already constructed a client.
      • Otherwise, construct a fresh client per env's secrets.

    Args:
        env_var: name of the env var to read. Default "BROKER".
        default: fallback when env var is unset. Default "saxo" so the
            live Saxo dry-run continues to work unchanged.
        saxo_client: optional pre-built SaxoClient. If provided AND
            BROKER==saxo, the factory wraps it instead of constructing
            a new one.
        ib_client: optional pre-built IBClient (Phase A.10+ verified).
            Same pattern as saxo_client.
        ib_environment: 'paper' or 'live' — only used when BROKER==ibkr
            AND we have to construct the IBClient ourselves.

    Returns:
        A connected-or-ready BrokerInterface. Caller is responsible for
        calling .connect() before placing orders.

    Raises:
        BrokerError: invalid BROKER value, or required client+config
            unavailable for the selected adapter.
    """
    raw = os.environ.get(env_var, default)
    choice = (raw or default).strip().lower()
    if choice not in _VALID_BROKERS:
        raise BrokerError(
            f"build_broker: {env_var}={raw!r} is not recognized. "
            f"Valid values: {sorted(_VALID_BROKERS)}."
        )

    if choice == "saxo":
        return _build_saxo_adapter(saxo_client)
    # 'ibkr' or 'ib' — normalize to ibkr internally
    return _build_ib_adapter(ib_client, environment=ib_environment)


# ─── Concrete builders ──────────────────────────────────────────────────────


def _build_saxo_adapter(saxo_client) -> BrokerInterface:
    from shared.broker.saxo_adapter import SaxoBrokerAdapter

    if saxo_client is None:
        raise BrokerError(
            "build_broker(BROKER=saxo): saxo_client must be supplied. "
            "The factory does NOT construct SaxoClient itself because "
            "Saxo's auth flow (OAuth 2.0 + token cache) is too coupled "
            "to per-bot config. Pass an already-authenticated SaxoClient."
        )
    logger.info("build_broker: SaxoBrokerAdapter selected (BROKER=saxo)")
    return SaxoBrokerAdapter(saxo_client)


def _build_ib_adapter(ib_client, environment: str = "paper") -> BrokerInterface:
    from shared.broker.ibkr_adapter import IBBrokerAdapter

    if ib_client is None:
        # Construct from environment using shared.ib_oauth.load_credentials.
        # Caller can override entirely by passing ib_client.
        try:
            from shared.ib_client import IBClient, IBConfig
            from shared.ib_oauth import load_credentials
        except ImportError as exc:
            raise BrokerError(
                f"build_broker(BROKER=ibkr): IB modules unavailable: {exc}"
            ) from exc

        creds = load_credentials(environment)
        cfg = IBConfig(credentials=creds)
        ib_client = IBClient(cfg)
        logger.info(
            "build_broker: built IBClient (environment=%s) from env vars",
            environment,
        )

    logger.info("build_broker: IBBrokerAdapter selected (BROKER=ibkr)")
    return IBBrokerAdapter(ib_client)
