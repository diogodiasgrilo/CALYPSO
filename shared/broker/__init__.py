"""Broker abstraction layer — Phase B of the Saxo → IB migration.

Provides a single broker-agnostic surface (`BrokerInterface`) that HYDRA
and its variants can be wired against. Two concrete adapters:

  • `SaxoBrokerAdapter` — wraps the existing `shared.saxo_client.SaxoClient`.
    No behavior change; preserves the live Saxo dry-run while migration
    is in progress.

  • `IBBrokerAdapter` — wraps `shared.ib_client.IBClient`. Wired in Phase B.3
    AFTER Phase A.10 paper smoke verifies the IBClient surface end-to-end
    against the live paper account.

Public re-exports for callers:
    from shared.broker import (
        BrokerInterface,
        SaxoBrokerAdapter,        # B.2
        # IBBrokerAdapter,        # B.3 (post-A.10)
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

__all__ = [
    "BrokerInterface",
    "SaxoBrokerAdapter",
    "QuoteSnapshot",
    "OrderResult",
    "IronCondorRequest",
    "VerticalSpreadRequest",
    "Position",
    "BrokerError",
    "BrokerAuthError",
    "BrokerConnectionError",
]
