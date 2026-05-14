"""BrokerInterface — broker-agnostic ABC for trade operations.

Phase B.1 of the Saxo → IB migration. Defines the SINGLE surface that
HYDRA + variant strategies talk to, decoupling strategy logic from
broker-specific quirks (Saxo's UIC vs IB's conid, Saxo's
`StockIndexOption` asset_type vs IB's `SPXW` trading_class, etc.).

Design principles:

  1. **Broker-agnostic IDs.** Methods accept `instrument_id: str` (an
     opaque token the adapter interprets — UIC string for Saxo, conid
     string for IB). Strategies never construct broker-specific IDs;
     they go through `resolve_option(symbol, expiry, strike, right)`.

  2. **Dataclass returns.** No raw dicts cross the interface boundary.
     Each adapter normalizes its broker's response shape into the same
     `QuoteSnapshot` / `OrderResult` / `Position` dataclasses, so
     strategies see one schema regardless of broker.

  3. **Errors normalize too.** Adapters wrap broker-specific exceptions
     into `BrokerError` / `BrokerAuthError` / `BrokerConnectionError`.
     The strategy never catches `IBClientError` or Saxo's `requests`
     errors directly.

  4. **Synchronous.** The interface is sync — matches HYDRA's threading
     model. Adapters that wrap async libraries (none today) would block
     internally.

Phase B.2 ships `SaxoBrokerAdapter` (wraps existing `SaxoClient`).
Phase B.3 ships `IBBrokerAdapter` (wraps `IBClient`) AFTER A.10 passes.
Phase B.4 wires HYDRA to pick its adapter via `BROKER=` env var.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# ─── Errors ─────────────────────────────────────────────────────────────────


class BrokerError(Exception):
    """Base exception. Adapters wrap broker-specific errors into this."""


class BrokerAuthError(BrokerError):
    """Authentication failed (bad creds, expired token, missing OAuth)."""


class BrokerConnectionError(BrokerError):
    """Network / transport failure. Retry policy decides whether to retry."""


# ─── Dataclasses crossing the interface boundary ────────────────────────────


@dataclass(frozen=True)
class QuoteSnapshot:
    """Normalized quote across brokers.

    bid/ask/last/mid/mark are optional because brokers don't always
    return all fields (delayed feeds, off-hours, etc.). Callers should
    null-check before computing spreads or mid-prices.
    """
    instrument_id: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    mid: Optional[float] = None
    mark: Optional[float] = None
    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    iv: Optional[float] = None
    open_interest: Optional[int] = None
    availability: Optional[str] = None  # 'R' real / 'D' delayed / 'Z' stale
    currency: str = "USD"
    timestamp: Optional[str] = None
    # Adapter's raw response for callers that need a field we didn't normalize
    raw: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass
class OrderResult:
    """Normalized order placement / status response.

    `status` uses a normalized vocabulary even though each broker's
    actual string set differs:

      Submitted   — accepted, working at exchange
      PreSubmitted — broker has it but not yet at exchange (IBKR-specific
                     transient state mapped to Submitted by adapters that
                     don't care about the distinction)
      Filled      — fully filled
      PartiallyFilled — some legs/qty filled, others working
      Cancelled   — terminal: cancelled by user or system
      Rejected    — terminal: broker / exchange rejected
      Expired     — terminal: GTC expired
    """
    order_id: str
    status: str
    filled_qty: int = 0
    avg_fill_price: Optional[float] = None
    reject_reason: Optional[str] = None
    # When True, this OrderResult represents a combo (multi-leg) order;
    # `legs` carries the per-leg details when the adapter has them.
    is_combo: bool = False
    legs: list = field(default_factory=list)
    raw: dict = field(default_factory=dict, compare=False)


@dataclass
class IronCondorRequest:
    """A 4-leg short iron condor to be placed as a single net-credit combo.

    Strikes are in the underlying's quote currency (USD for SPX). For a
    SHORT IC (the canonical CALYPSO trade), short strikes sit between
    spot and the long-wing strikes.

    `net_credit_limit` is the MINIMUM net credit you'll accept (broker
    sees this as a limit price). Adapter handles the broker-specific
    sign convention internally (IBKR: SELL + positive; Saxo: amount +
    direction flag).
    """
    expiry: date
    short_call_strike: float
    long_call_strike: float
    short_put_strike: float
    long_put_strike: float
    contracts: int
    net_credit_limit: float
    underlying_symbol: str = "SPX"
    tif: str = "DAY"
    coid: Optional[str] = None  # client_order_id for retry-safety


@dataclass
class VerticalSpreadRequest:
    """A 2-leg vertical (call or put) spread.

    `action='SELL'` opens a short spread (collect net credit).
    `action='BUY'` closes a previously-sold short spread (pay net debit).
    """
    expiry: date
    short_strike: float
    long_strike: float
    right: str                # 'C' or 'P'
    contracts: int
    net_credit_limit: float
    action: str = "SELL"      # 'SELL' to open short; 'BUY' to close
    underlying_symbol: str = "SPX"
    tif: str = "DAY"
    coid: Optional[str] = None


@dataclass(frozen=True)
class Position:
    """An open position across brokers.

    instrument_id is the broker's preferred string identifier (UIC for
    Saxo, conid for IB). The same string can be re-passed to other
    BrokerInterface read methods.
    """
    instrument_id: str
    symbol: str
    quantity: int
    side: str                 # 'LONG' or 'SHORT'
    avg_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    raw: dict = field(default_factory=dict, compare=False, hash=False)


# ─── BrokerInterface ABC ────────────────────────────────────────────────────


# ─── StreamingInterface (sub-namespace) ─────────────────────────────────────


class StreamingInterface(ABC):
    """Real-time market-data streaming surface.

    Exposed on BrokerInterface via the `.streaming` property. Both brokers
    map onto this shape, but their underlying models differ:

      • IBKR (CP API): per-conid `smd+/umd+` WebSocket subscriptions
        managed by `shared.ib_streaming.StreamingManager`. Per-instrument
        subscribe/unsubscribe is the native shape; subscribe_option adds
        greeks fields to the same channel.

      • Saxo: bulk WebSocket subscription via
        `SaxoClient.start_price_streaming(list_of_uics, callback)`. The
        adapter tracks an internal subscription set and restarts streaming
        when it changes. Greeks come via REST (`get_option_greeks`), not
        streaming — `subscribe_option` falls through to `subscribe_quote`.

    Either way, callers see a single contract: subscribe, read snapshots,
    check health.
    """

    @abstractmethod
    def subscribe_quote(
        self,
        instrument_id: str,
        fields: Optional[list[str]] = None,
    ) -> None:
        """Start streaming quotes for `instrument_id`. Idempotent —
        re-subscribing replaces the field set in place."""

    @abstractmethod
    def subscribe_option(
        self,
        instrument_id: str,
        fields: Optional[list[str]] = None,
    ) -> None:
        """Subscribe with greeks fields. On brokers without push-greeks
        (Saxo), falls through to subscribe_quote; callers fetch greeks
        via REST `get_option_greeks` separately."""

    @abstractmethod
    def unsubscribe_quote(self, instrument_id: str) -> None:
        """Stop streaming for `instrument_id`. Idempotent."""

    @abstractmethod
    def unsubscribe_all(self) -> None:
        """Tear down every subscription. Manager itself stays running."""

    @abstractmethod
    def get_snapshot(self, instrument_id: str) -> Optional[QuoteSnapshot]:
        """Last-known tick for `instrument_id`. None until first tick
        arrives. NO I/O — reads from local cache populated by the
        streaming thread."""

    @abstractmethod
    def last_tick_age(self, instrument_id: str) -> Optional[float]:
        """Seconds since the last tick for `instrument_id`. None if no
        tick has ever been received."""

    @abstractmethod
    def is_healthy(self, max_tick_age_seconds: float = 60.0) -> bool:
        """STRICT: WS connected AND every subscription has a recent
        tick. Returns False during off-hours / closed markets — use
        is_ws_connected for the lightweight "pipe alive" check."""

    @abstractmethod
    def is_ws_connected(self) -> bool:
        """LIGHTWEIGHT: just the underlying WebSocket connection state.
        True even when no ticks are flowing (off-hours)."""

    @abstractmethod
    def active_subscriptions(self) -> list[str]:
        """All instrument_ids currently subscribed."""


# ─── BrokerInterface ────────────────────────────────────────────────────────


class BrokerInterface(ABC):
    """Abstract broker-agnostic surface.

    Every concrete adapter MUST implement every method. The abstract
    decorator enforces this at instantiation time — instantiating an
    incomplete adapter raises TypeError before any I/O happens.
    """

    # ─── Lifecycle ────────────────────────────────────────────────────────

    @abstractmethod
    def connect(self) -> bool:
        """Establish a working broker session. Returns True on success.

        Raises:
            BrokerAuthError: credentials invalid / expired
            BrokerConnectionError: network failure
        """

    @abstractmethod
    def is_connected(self) -> bool:
        """Cheap last-known check — does NOT round-trip to broker."""

    @abstractmethod
    def disconnect(self) -> None:
        """Tear down the broker session cleanly. Idempotent."""

    # ─── Reads ────────────────────────────────────────────────────────────

    @abstractmethod
    def get_quote(self, instrument_id: str) -> Optional[QuoteSnapshot]:
        """Latest snapshot for one instrument. None if broker has no data."""

    @abstractmethod
    def get_quotes_batch(self, instrument_ids: list[str]) -> list[QuoteSnapshot]:
        """Snapshot multiple instruments in one call (when broker supports
        batching). Order of returned list matches `instrument_ids` only
        when the underlying broker guarantees it — callers should key by
        QuoteSnapshot.instrument_id."""

    @abstractmethod
    def get_vix_price(self) -> Optional[float]:
        """VIX index spot. Convenience wrapper — VIX is queried often."""

    @abstractmethod
    def get_option_greeks(self, instrument_id: str) -> Optional[QuoteSnapshot]:
        """Snapshot enriched with delta/gamma/theta/vega/iv/OI."""

    @abstractmethod
    def get_option_chain(
        self,
        underlying_symbol: str,
        expiry: date,
    ) -> list[float]:
        """Available strikes for `underlying_symbol` on `expiry`."""

    @abstractmethod
    def get_chart_data(
        self,
        symbol: str,
        bar: str = "1min",
        period: str = "1d",
        outside_rth: bool = False,
    ) -> list[dict]:
        """Historical bars. Each entry has keys t/o/h/l/c/v."""

    @abstractmethod
    def get_fx_rate(self, source: str, target: str) -> Optional[float]:
        """FX rate from `source` to `target` (e.g. 'USD' → 'EUR')."""

    # ─── Account state ────────────────────────────────────────────────────

    @abstractmethod
    def get_account_info(self) -> dict:
        """Raw account snapshot — adapters return broker-shape verbatim
        for callers that need fields outside the normalized set."""

    @abstractmethod
    def get_balance(self, currency: str = "USD") -> dict:
        """Account balance composed for trading in `currency`.

        Returns at minimum:
            tradable       — buying power in target currency
            currency       — echoed target currency
            base_currency  — account's base currency
        """

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """All open positions on the account."""

    # ─── Order management ─────────────────────────────────────────────────

    @abstractmethod
    def get_open_orders(self) -> list[OrderResult]:
        """All working orders on the account."""

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResult:
        """Current state of a specific order."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Request cancel. Returns True if the broker accepted the cancel
        request (the actual transition to Cancelled is async)."""

    # ─── Pre-trade ────────────────────────────────────────────────────────

    @abstractmethod
    def what_if_iron_condor(self, request: IronCondorRequest) -> dict:
        """Pre-trade margin/cost check WITHOUT placing.

        Returns the broker's margin response (5 blocks for IBKR;
        equivalent dict for Saxo). Strategies use this as the
        authoritative pre-trade BP gate.
        """

    # ─── Writes ───────────────────────────────────────────────────────────

    @abstractmethod
    def place_iron_condor(self, request: IronCondorRequest) -> OrderResult:
        """Place a 4-leg short iron condor as a net-credit combo limit.

        Atomic-fill enforcement is broker-specific:
          • Saxo: NonGuaranteed=false on the combo
          • IBKR CP API: no native flag — caller must monitor sor topic
            and place per-leg market fallbacks (Phase A.5/A.7 handle this
            inside the IB adapter)
        """

    @abstractmethod
    def place_vertical_spread(self, request: VerticalSpreadRequest) -> OrderResult:
        """Place a 2-leg vertical spread (open OR close)."""

    # ─── Streaming sub-namespace ──────────────────────────────────────────

    @property
    @abstractmethod
    def streaming(self) -> StreamingInterface:
        """Real-time market-data streaming surface.

        Returns a StreamingInterface that's safe to use after `connect()`
        succeeds. Adapters may construct the proxy lazily on first
        access. Property + @abstractmethod compose so subclasses are
        forced to implement the access pattern (not just an attribute)."""
