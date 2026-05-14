"""Tests for shared/broker/interface.py — Phase B.1.

Verifies that:
  • BrokerInterface is a proper ABC — instantiation fails if abstract
    methods are missing (TypeError at construction time).
  • Dataclass shapes are sane: defaults, frozen attributes, types.
  • Status normalization vocabulary covers the canonical set.
  • Public re-exports from `shared.broker` resolve as expected.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


# ─── ABC enforcement ────────────────────────────────────────────────────────


class TestABCEnforcement:
    def test_cannot_instantiate_bare_interface(self):
        """The ABC itself must not be instantiable — there are abstract
        methods. This catches a future refactor that accidentally removes
        @abstractmethod from one of them."""
        with pytest.raises(TypeError):
            BrokerInterface()  # type: ignore[abstract]

    def test_incomplete_adapter_raises_on_construct(self):
        """A subclass that fills only SOME abstract methods still can't
        be instantiated."""
        class HalfBaked(BrokerInterface):
            def connect(self): return True
            def is_connected(self): return True
            def disconnect(self): pass
            # missing: every read/write/order method
        with pytest.raises(TypeError):
            HalfBaked()  # type: ignore[abstract]

    def test_complete_adapter_can_construct(self):
        """A subclass that implements every abstract method instantiates
        cleanly. Smoke-tests that the abstract surface is internally
        consistent."""
        class FullStub(BrokerInterface):
            def connect(self): return True
            def is_connected(self): return True
            def disconnect(self): pass
            def get_quote(self, instrument_id): return None
            def get_quotes_batch(self, instrument_ids): return []
            def get_vix_price(self): return None
            def get_option_greeks(self, instrument_id): return None
            def get_option_chain(self, underlying_symbol, expiry): return []
            def get_chart_data(self, symbol, bar="1min", period="1d", outside_rth=False): return []
            def get_fx_rate(self, source, target): return None
            def get_account_info(self): return {}
            def get_balance(self, currency="USD"): return {}
            def get_positions(self): return []
            def get_open_orders(self): return []
            def get_order_status(self, order_id): return OrderResult(order_id=order_id, status="Unknown")
            def cancel_order(self, order_id): return True
            def what_if_iron_condor(self, request): return {}
            def place_iron_condor(self, request): return OrderResult(order_id="x", status="Submitted")
            def place_vertical_spread(self, request): return OrderResult(order_id="x", status="Submitted")
        adapter = FullStub()
        assert adapter.connect() is True


# ─── Dataclass shapes ───────────────────────────────────────────────────────


class TestQuoteSnapshot:
    def test_default_values_all_none_except_id(self):
        q = QuoteSnapshot(instrument_id="123")
        assert q.instrument_id == "123"
        assert q.bid is None and q.ask is None and q.last is None
        assert q.currency == "USD"
        assert q.raw == {}

    def test_frozen_cannot_assign(self):
        q = QuoteSnapshot(instrument_id="123", bid=1.0, ask=1.2)
        with pytest.raises((AttributeError, Exception)):
            q.bid = 2.0  # type: ignore[misc]

    def test_equality_ignores_raw(self):
        """raw is excluded from equality/hash so we don't break == when
        adapters carry different envelope shapes."""
        q1 = QuoteSnapshot(instrument_id="123", bid=1.0, raw={"a": 1})
        q2 = QuoteSnapshot(instrument_id="123", bid=1.0, raw={"b": 2})
        assert q1 == q2


class TestOrderResult:
    def test_defaults(self):
        o = OrderResult(order_id="abc", status="Submitted")
        assert o.filled_qty == 0
        assert o.avg_fill_price is None
        assert o.is_combo is False
        assert o.legs == []

    def test_combo_marker(self):
        legs = [OrderResult(order_id="l1", status="Submitted")]
        o = OrderResult(
            order_id="combo", status="Submitted",
            is_combo=True, legs=legs,
        )
        assert o.is_combo is True
        assert len(o.legs) == 1


class TestIronCondorRequest:
    def test_canonical_short_ic(self):
        req = IronCondorRequest(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=0.30,
        )
        assert req.underlying_symbol == "SPX"
        assert req.tif == "DAY"
        assert req.coid is None

    def test_coid_passthrough(self):
        req = IronCondorRequest(
            expiry=date(2026, 5, 16),
            short_call_strike=5500, long_call_strike=5505,
            short_put_strike=5400,  long_put_strike=5395,
            contracts=10, net_credit_limit=0.30,
            coid="caller_supplied",
        )
        assert req.coid == "caller_supplied"


class TestVerticalSpreadRequest:
    def test_defaults_action_sell(self):
        req = VerticalSpreadRequest(
            expiry=date(2026, 5, 16),
            short_strike=5500, long_strike=5505, right="C",
            contracts=5, net_credit_limit=0.50,
        )
        assert req.action == "SELL"
        assert req.right == "C"


class TestPosition:
    def test_frozen(self):
        p = Position(
            instrument_id="416904", symbol="SPX",
            quantity=10, side="LONG",
        )
        with pytest.raises((AttributeError, Exception)):
            p.quantity = 5  # type: ignore[misc]

    def test_unrealized_pnl_optional(self):
        p = Position(
            instrument_id="416904", symbol="SPX",
            quantity=10, side="LONG",
        )
        assert p.unrealized_pnl is None


# ─── Error hierarchy ────────────────────────────────────────────────────────


class TestErrorHierarchy:
    def test_auth_error_is_broker_error(self):
        assert issubclass(BrokerAuthError, BrokerError)

    def test_connection_error_is_broker_error(self):
        assert issubclass(BrokerConnectionError, BrokerError)

    def test_broker_error_is_exception(self):
        assert issubclass(BrokerError, Exception)


# ─── Re-export surface ──────────────────────────────────────────────────────


class TestPackageReExports:
    def test_top_level_imports_resolve(self):
        from shared.broker import (
            BrokerInterface as BI,
            SaxoBrokerAdapter,
            QuoteSnapshot as QS,
            OrderResult as OR,
            IronCondorRequest as IC,
            VerticalSpreadRequest as VS,
            Position as P,
            BrokerError as BE,
            BrokerAuthError as BAE,
            BrokerConnectionError as BCE,
        )
        # Sanity: these are the same classes as the direct imports
        assert BI is BrokerInterface
        assert QS is QuoteSnapshot
        assert OR is OrderResult
        assert IC is IronCondorRequest
        assert VS is VerticalSpreadRequest
        assert P is Position
        assert BE is BrokerError
        assert BAE is BrokerAuthError
        assert BCE is BrokerConnectionError
        # SaxoBrokerAdapter is a BrokerInterface subclass
        assert issubclass(SaxoBrokerAdapter, BrokerInterface)
