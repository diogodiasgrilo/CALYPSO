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

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.broker_service import ALLOWED_METHODS, BrokerDispatcher, to_jsonable
from shared.broker_client import BrokerClient, BrokerError


def _wire_transport(dispatcher):
    """In-process transport that faithfully simulates the HTTP/JSON wire: the
    request args/kwargs AND the response are round-tripped through JSON, so the
    test catches any non-serializable return or shape drift."""
    def transport(method, args, kwargs):
        req = json.loads(json.dumps({"method": method, "args": args, "kwargs": kwargs}))
        resp = dispatcher.dispatch(req["method"], req["args"], req["kwargs"])
        return json.loads(json.dumps(resp))
    return transport


# (method, call_args, call_kwargs, canned IBClient return) — representative shapes.
CASES = [
    ("get_quote", (416904,), {}, {"bid": 1.0, "ask": 1.1, "last": 1.05, "mark": 1.05}),
    ("get_quotes_batch", ([1, 2],), {}, {"1": {"bid": 1.0}, "2": {"bid": 2.0}}),
    ("get_vix_price", (), {}, 15.3),
    ("get_option_chain", ("20260529",), {"strikes": [5000.0]},
     {"calls": {"5000.0": 111}, "puts": {"5000.0": 222}}),
    ("get_option_greeks", (55813670,), {}, {"delta": 0.5, "gamma": 0.01, "theta": -0.3}),
    ("get_chart_data", (416904,), {"bar": "1min"}, [{"t": 1, "c": 5000.0}, {"t": 2, "c": 5001.0}]),
    ("qualify_contract", ("SPX", "IND"), {}, {"conid": 416904, "symbol": "SPX"}),
    ("qualify_option_strikes", ("20260529", [5000.0]), {}, {"5000.0": 55813670}),
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
        # Identical to what IBClient returned, after the JSON wire round-trip.
        assert got == to_jsonable(ret)
        # Args/kwargs forwarded verbatim to the real IBClient method.
        getattr(ib, method).assert_called_once_with(*args, **kwargs)

    def test_disallowed_method_raises_attribute_error(self):
        _, client = _make()
        for bad in ("connect", "disconnect", "_ib_call", "place_order", "get_secdef"):
            with pytest.raises(AttributeError):
                getattr(client, bad)

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

    def test_dispatcher_health_never_raises(self):
        ib = MagicMock()
        ib.is_connected.side_effect = RuntimeError("boom")
        assert BrokerDispatcher(ib).health()["status"] == "degraded"
        ib2 = MagicMock(); ib2.is_connected.return_value = True
        assert BrokerDispatcher(ib2).health() == {"status": "ok", "connected": True}
