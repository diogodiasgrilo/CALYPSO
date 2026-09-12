"""
An OrderRequest cannot survive JSON — coerce it back at the RPC boundary
(2026-09-11).

THE GAP, found the same day `what_if_order` was allowlisted for RPC. Its
argument is an ibind `OrderRequest` DATACLASS, and ibind's
`parse_order_request` maps snake_case to IBKR's camelCase **only** for the
dataclass. Handed a plain dict it passes the keys through UNMAPPED (it even logs
"Order request supplied as a dict. Use 'OrderRequest' dataclass instead"), so
`order_type` reached IBKR verbatim and returned:

    400 Bad Request :: {"error":"Unknown order type"}

The direct in-process path was never affected — it passes the dataclass — so the
defect existed only over the wire, which is exactly the class of bug the
BrokerClient contract exists to prevent: the RPC path must be semantically
identical to the direct one.

THE COERCION IS DELIBERATELY CONSERVATIVE. It only rebuilds an OrderRequest when
EVERY key in the dict is a real OrderRequest field. A dict with unknown keys, or
one that is already camelCase, is passed through untouched — a wrong coercion is
worse than none, because it would look like it worked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.broker_service import (  # noqa: E402
    _DATACLASS_ARG_METHODS,
    _coerce_order_request,
)

SNAKE = {"conidex": "28812380;;;1/-1,2/1", "sec_type": "BAG", "side": "SELL",
         "order_type": "LMT", "price": 1.25, "quantity": 1.0, "tif": "DAY"}


def _is_order_request(v) -> bool:
    from ibind import OrderRequest
    return isinstance(v, OrderRequest)


class TestItCoercesWhereItMatters:
    def test_a_snake_case_dict_becomes_an_OrderRequest(self):
        args, _ = _coerce_order_request("what_if_order", [dict(SNAKE)], {})
        assert _is_order_request(args[0])

    def test_the_fields_survive_intact(self):
        args, _ = _coerce_order_request("what_if_order", [dict(SNAKE)], {})
        o = args[0]
        assert o.sec_type == "BAG"
        assert o.order_type == "LMT"
        assert o.conidex == SNAKE["conidex"]
        assert o.price == 1.25

    def test_it_then_serialises_to_IBKRs_camelCase(self):
        """The whole point: the dataclass is what makes parse_order_request map
        order_type -> orderType. Without it IBKR answers 'Unknown order type'."""
        from ibind.client.ibkr_utils import parse_order_request
        args, _ = _coerce_order_request("what_if_order", [dict(SNAKE)], {})
        wire = parse_order_request(args[0])
        assert "orderType" in wire and "order_type" not in wire
        assert "secType" in wire and "sec_type" not in wire

    def test_an_uncoerced_dict_does_NOT_map(self):
        """Proves the bug is real rather than assumed: the raw dict passes
        straight through unmapped."""
        from ibind.client.ibkr_utils import parse_order_request
        wire = parse_order_request(dict(SNAKE))
        assert "order_type" in wire        # <- reaches IBKR verbatim
        assert "orderType" not in wire

    def test_it_works_through_kwargs_too(self):
        _, kw = _coerce_order_request("what_if_order", [], {"order": dict(SNAKE)})
        assert _is_order_request(kw["order"])


class TestItIsConservative:
    """A wrong coercion is worse than none — it would look like it worked."""

    def test_a_dict_with_an_unknown_key_is_left_alone(self):
        d = dict(SNAKE); d["not_a_field"] = 1
        args, _ = _coerce_order_request("what_if_order", [d], {})
        assert args[0] is not None and isinstance(args[0], dict)

    def test_an_already_camelCase_dict_is_left_alone(self):
        """camelCase keys are not OrderRequest field names, so this must pass
        through — it is already in wire form."""
        d = {"conidex": "x", "secType": "BAG", "orderType": "LMT"}
        args, _ = _coerce_order_request("what_if_order", [d], {})
        assert isinstance(args[0], dict)

    def test_an_empty_dict_is_left_alone(self):
        args, _ = _coerce_order_request("what_if_order", [{}], {})
        assert args[0] == {}

    def test_non_dict_arguments_are_untouched(self):
        args, _ = _coerce_order_request("what_if_order", [42, "x", None], {})
        assert args == [42, "x", None]


class TestItTouchesNothingElse:
    @pytest.mark.parametrize("method", [
        "get_positions", "get_balance", "qualify_contract",
        "place_and_wait_for_fill", "what_if_naked_margin", "get_quote",
    ])
    def test_other_methods_pass_through_unchanged(self, method):
        """what_if_naked_margin in particular takes a LIST OF DICTS of primitive
        legs — coercing those would break the strangle margin gate."""
        d = dict(SNAKE)
        args, kw = _coerce_order_request(method, [d], {"k": d})
        assert args[0] is d and kw["k"] is d

    def test_only_what_if_order_is_registered(self):
        assert _DATACLASS_ARG_METHODS == {"what_if_order"}

    def test_the_dispatcher_actually_calls_it(self):
        """A coercion nothing invokes is decoration."""
        import inspect
        from shared.broker_service import BrokerDispatcher
        assert "_coerce_order_request" in inspect.getsource(BrokerDispatcher.dispatch)

    def test_it_runs_INSIDE_the_try(self):
        """A malformed payload must surface as an RPC error, not a 500 — the
        dispatcher's contract is that it never raises."""
        import inspect
        from shared.broker_service import BrokerDispatcher
        src = inspect.getsource(BrokerDispatcher.dispatch)
        assert src.index("try:") < src.index("_coerce_order_request")
