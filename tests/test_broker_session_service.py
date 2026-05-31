"""Contract tests for the calypso-broker seam (Option 1, P1).

Exercises the full BrokerClient -> (JSON wire) -> BrokerDispatcher -> IBClient
round-trip IN-PROCESS (no FastAPI/HTTP), asserting:
  • every one of the 16 allowlisted methods returns the SAME shape the IBClient
    returned (shape parity is the #1 correctness risk — design §6),
  • args/kwargs are forwarded verbatim,
  • non-allowlisted access raises (AttributeError),
  • IBClient exceptions surface as BrokerError (so strategies degrade, not crash).

See docs/migration/BROKER_SESSION_SERVICE_DESIGN.md.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.broker_service import ALLOWED_METHODS, BrokerDispatcher, _json_default
from shared.broker_client import BrokerClient, BrokerError


def _wire_encode(obj):
    """Encode exactly the way the real HTTP transport does: stdlib json with the
    shared ``_json_default`` hook (broker_client._http_transport builds the body
    this way because requests' ``json=`` path uses no ``default=``). Returns the
    decoded JSON, i.e. precisely what arrives on the other side of the wire."""
    return json.loads(json.dumps(obj, default=_json_default))


def _wire_transport(dispatcher):
    """In-process transport that faithfully simulates the HTTP/JSON wire: the
    request args/kwargs AND the response are round-tripped through JSON using the
    SAME encoder the real transport uses, so the test catches any non-serializable
    return, shape drift, OR argument mangling (e.g. a datetime.date arg)."""
    def transport(method, args, kwargs):
        req = _wire_encode({"method": method, "args": args, "kwargs": kwargs})
        resp = dispatcher.dispatch(req["method"], req["args"], req["kwargs"])
        return _wire_encode(resp)
    return transport


# A real option expiry crosses the wire as a ``datetime.date`` from the strategy
# (bots/hydra/strategy.py passes ``expiry=expiry_date``), so the contract test
# must exercise that native type — not a wire-safe string stand-in.
_EXPIRY = datetime.date(2026, 5, 29)

# (method, call_args, call_kwargs, canned IBClient return) — the REAL shapes the
# live IBClient produces/consumes (datetime.date args, list[float] / tuple-keyed
# dict returns), NOT JSON-native stand-ins. Asserting against these makes the
# round-trip catch any lossy/divergent wire transform.
CASES = [
    ("get_quote", (416904,), {}, {"bid": 1.0, "ask": 1.1, "last": 1.05, "mark": 1.05}),
    ("get_quotes_batch", ([1, 2],), {}, {"1": {"bid": 1.0}, "2": {"bid": 2.0}}),
    ("get_vix_price", (), {}, 15.3),
    # get_option_chain(symbol: str, expiry: date, trading_class="SPXW") -> list[float]
    ("get_option_chain", ("SPX", _EXPIRY), {}, [4990.0, 5000.0, 5010.0]),
    ("get_option_greeks", (55813670,), {}, {"delta": 0.5, "gamma": 0.01, "theta": -0.3}),
    ("get_chart_data", (416904,), {"bar": "1min"}, [{"t": 1, "c": 5000.0}, {"t": 2, "c": 5001.0}]),
    ("qualify_contract", ("SPX", "IND"), {}, {"conid": 416904, "symbol": "SPX"}),
    # qualify_option_strikes(*, symbol, expiry: date, strikes) -> dict[(strike,right)->conid].
    # Keyword-only, real date arg, and the genuine tuple-keyed dict return the
    # strategy unpacks via `for (strike, right), conid in conid_map.items()`.
    ("qualify_option_strikes", (), {"symbol": "SPX", "expiry": _EXPIRY, "strikes": [5000.0]},
     {(5000.0, "C"): 55813670, (5000.0, "P"): 55813671}),
    ("get_positions", (), {}, [{"conid": 1, "position": -1.0}, {"conid": 2, "position": 1.0}]),
    ("get_balance", (), {"currency": "USD"}, {"USD": 100000.0, "EUR": 0.0}),
    ("get_fx_rate", ("USD", "EUR"), {}, 0.92),
    ("get_open_orders", (), {}, [{"orderId": "1", "status": "Submitted"}]),
    ("get_order_status", ("O1",), {}, {"order_id": "O1", "status": "Filled"}),
    ("get_closed_position_price", (55813670,), {}, 1.23),
    ("place_and_wait_for_fill", (), {"conid": 55813670, "side": "SELL", "quantity": 1,
                                     "order_type": "LMT", "limit_price": 1.0, "coid": "x"},
     {"order_id": "O1", "status": "filled", "filled_quantity": 1, "avg_fill_price": 1.0, "raw": {}}),
    ("cancel_order", ("O1",), {}, True),
]


def _make():
    ib = MagicMock()
    for method, _a, _k, ret in CASES:
        getattr(ib, method).return_value = ret
    client = BrokerClient(transport=_wire_transport(BrokerDispatcher(ib)))
    return ib, client


class TestBrokerContract:
    def test_cases_cover_every_allowlisted_method(self):
        assert {m for m, *_ in CASES} == set(ALLOWED_METHODS)

    @pytest.mark.parametrize("method,args,kwargs,ret", CASES, ids=[c[0] for c in CASES])
    def test_shape_parity_and_arg_forwarding(self, method, args, kwargs, ret):
        ib, client = _make()
        got = getattr(client, method)(*args, **kwargs)
        # The strategy must see EXACTLY the object a direct IBClient would have
        # returned. Compare against the REAL return shape (`ret`), not a
        # pre-JSON-mangled copy — so any lossy/divergent wire transform (date->str,
        # tuple-key explosion, Decimal->float) fails here instead of passing
        # against itself.
        assert got == ret
        # The real IBClient is invoked with whatever actually crosses the wire.
        # Request args are JSON-encoded with the same `_json_default` the HTTP
        # transport uses, so this asserts faithful forwarding AND documents that
        # a datetime.date arg arrives as its isoformat string on the broker side
        # (the dispatcher does NOT re-hydrate it — see cross-file note).
        wire = _wire_encode({"args": list(args), "kwargs": kwargs})
        getattr(ib, method).assert_called_once_with(*wire["args"], **wire["kwargs"])

    def test_disallowed_method_raises_attribute_error(self):
        _, client = _make()
        for bad in ("disconnect", "_ib_call", "place_order", "get_secdef", "foobar"):
            with pytest.raises(AttributeError):
                getattr(client, bad)

    def test_circuit_breakers_is_empty_mapping(self):
        # alert_hooks polls broker.circuit_breakers — must be present + empty
        # (real breakers live in the broker process), never AttributeError.
        _, client = _make()
        assert client.circuit_breakers == {}
        assert dict(client.circuit_breakers.items()) == {}

    def test_ibclient_exception_surfaces_as_broker_error(self):
        ib = MagicMock()
        ib.get_quote.side_effect = RuntimeError("IBKR 503 Service Unavailable")
        client = BrokerClient(transport=_wire_transport(BrokerDispatcher(ib)))
        with pytest.raises(BrokerError) as ei:
            client.get_quote(416904)
        assert "503" in str(ei.value) and "RuntimeError" in str(ei.value)

    def test_unreachable_broker_raises_broker_error_not_crash(self):
        def dead_transport(method, args, kwargs):
            raise ConnectionError("connection refused")
        client = BrokerClient(transport=dead_transport)
        with pytest.raises(BrokerError):
            client.get_vix_price()

    def test_connect_ok_when_broker_holds_session(self):
        _, client = _make()
        client.health = lambda: {"status": "ok", "connected": True}
        assert client.connect() is True

    def test_connect_raises_when_broker_has_no_session(self):
        _, client = _make()
        client.health = lambda: {"status": "degraded", "connected": False}
        with pytest.raises(BrokerError):
            client.connect()

    def test_ensure_connected_reflects_health_and_never_raises(self):
        _, client = _make()
        client.health = lambda: {"connected": True}
        assert client.ensure_connected() is True

        def boom():
            raise ConnectionError("broker down")
        client.health = boom
        assert client.ensure_connected() is False  # never raises → gate handles it

    def test_dispatcher_health_never_raises(self):
        # Audit #13: /health is now AUTHORITATIVE (a live check_auth_status
        # round-trip), not a cached is_connected() flag, and fails CLOSED.
        # auth/status raises → degraded.
        ib = MagicMock()
        ib.check_auth_status.side_effect = RuntimeError("boom")
        assert BrokerDispatcher(ib).health()["status"] == "degraded"
        # truly-live session → ok + connected.
        ib2 = MagicMock()
        ib2.check_auth_status.return_value = {
            "authenticated": True, "connected": True, "competing": False,
        }
        h2 = BrokerDispatcher(ib2).health()
        assert h2["status"] == "ok" and h2["connected"] is True
        # competing login → fail closed (degraded, not connected).
        ib3 = MagicMock()
        ib3.check_auth_status.return_value = {
            "authenticated": True, "connected": True, "competing": True,
        }
        h3 = BrokerDispatcher(ib3).health()
        assert h3["status"] == "degraded" and h3["connected"] is False
