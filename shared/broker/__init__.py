"""Broker abstraction layer — Phase B of the Saxo → IB migration.

Provides a single broker-agnostic surface (`BrokerInterface`) that HYDRA
and its variants can be wired against. Two concrete adapters:

  • `SaxoBrokerAdapter` — wraps the existing `shared.saxo_client.SaxoClient`.
    No behavior change; preserves the live Saxo dry-run while migration
    is in progress.

  • `IBBrokerAdapter` — wraps `shared.ib_client.IBClient`. Phase B.3.
    Built ahead of A.10 paper-smoke verification; any IBClient response-
    shape surprises caught Sunday get fixed inside IBClient itself, not
    in this adapter (whose only job is boundary translation).

Public re-exports for callers:
    from shared.broker import (
        BrokerInterface,
        SaxoBrokerAdapter,        # B.2
        IBBrokerAdapter,          # B.3
        QuoteSnapshot, OrderResult, IronCondorRequest,
        VerticalSpreadRequest, Position,
        BrokerError, BrokerAuthError, BrokerConnectionError,
    )
"""

from shared.broker.interface import (
    BrokerInterface,
    QuoteSnapshot,
    OrderResult,
    IronCondorRequest,
    VerticalSpreadRequest,
    Position,
    BrokerError,
    BrokerAuthError,
    BrokerConnectionError,
)
from shared.broker.saxo_adapter import SaxoBrokerAdapter
from shared.broker.ibkr_adapter import IBBrokerAdapter
from shared.broker.factory import build_broker

__all__ = [
    "BrokerInterface",
    "SaxoBrokerAdapter",
    "IBBrokerAdapter",
    "build_broker",
    "QuoteSnapshot",
    "OrderResult",
    "IronCondorRequest",
    "VerticalSpreadRequest",
    "Position",
    "BrokerError",
    "BrokerAuthError",
    "BrokerConnectionError",
]
