"""One RPC can be tens of IBKR calls, and the default timeout didn't allow for it.

`qualify_option_strikes` makes ONE `secdef_info` call PER STRIKE. Variant F asks
for 61 (±150pt at 5pt spacing), and each takes a slot in the broker's 5 rps
gate — a 12.2s floor with the gate entirely to itself, against a 35s client
timeout, with the gate measured 77% saturated and client-side throughput at
~0.48 RPC/s. On 2026-10-01 F's EM boundary was touched, qualification timed out,
and F lost its only trigger of the day.

The narrowness is the point. A quote read runs inside the monitoring loop where
the stop checks live; blocking that for a minute would be worse than failing
fast, which is exactly why variant E's VIX ReadTimeout the same morning needed
no fix. So only the multi-call SETUP reads get the longer budget, and a test
pins that the loop reads keep the snappy default.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.broker_client import (  # noqa: E402
    MULTI_CALL_READ_METHODS, MULTI_CALL_READ_TIMEOUT_S, BrokerClient,
)


class _Spy:
    """Captures the timeout requests.post would have been given."""
    def __init__(self):
        self.seen = {}

    def install(self, monkeypatch, client):
        import requests

        def fake_post(url, data=None, headers=None, timeout=None):
            import json as _j
            m = _j.loads(data)["method"]
            self.seen[m] = timeout

            class R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {"result": None}
            return R()
        monkeypatch.setattr(requests, "post", fake_post)
        return client


@pytest.fixture
def client(monkeypatch):
    c = BrokerClient(base_url="http://127.0.0.1:8788")
    spy = _Spy()
    spy.install(monkeypatch, c)
    return c, spy


class TestTheMultiCallReadsGetTheBudget:
    def test_qualify_option_strikes_gets_the_long_timeout(self, client):
        c, spy = client
        c._http_transport("qualify_option_strikes", [], {})
        assert spy.seen["qualify_option_strikes"] >= MULTI_CALL_READ_TIMEOUT_S, (
            f"got {spy.seen['qualify_option_strikes']}s — one RPC here is one "
            f"IBKR call PER STRIKE (61 for variant F), which cannot finish in "
            f"the snappy default")

    def test_get_option_chain_gets_it_too(self, client):
        c, spy = client
        c._http_transport("get_option_chain", [], {})
        assert spy.seen["get_option_chain"] >= MULTI_CALL_READ_TIMEOUT_S

    def test_the_budget_exceeds_the_brokers_own_worst_case(self):
        """ib_retry documents ~63s worst case for a single call (31s backoff +
        up to 36s of snapshot warmup). A client that gives up first turns a
        slow-but-working call into a phantom network error."""
        assert MULTI_CALL_READ_TIMEOUT_S > 63.0, (
            f"{MULTI_CALL_READ_TIMEOUT_S}s is below the broker's documented "
            f"~63s worst case, so the client can still abandon a working call")


class TestLoopReadsStayFast:
    """The constraint that makes this narrow rather than a blanket raise."""

    @pytest.mark.parametrize("method", ["get_quote", "get_positions", "get_balance"])
    def test_a_loop_read_keeps_the_snappy_default(self, client, method):
        c, spy = client
        c._http_transport(method, [], {})
        assert spy.seen[method] == c._timeout, (
            f"{method} now waits {spy.seen[method]}s. It runs inside the "
            f"monitoring loop where the stop checks live — blocking that is "
            f"worse than failing fast.")

    def test_the_exception_list_stays_tiny(self):
        assert MULTI_CALL_READ_METHODS == {"qualify_option_strikes", "get_option_chain"}, (
            f"the long-timeout set grew to {sorted(MULTI_CALL_READ_METHODS)}. "
            f"Every addition is a method that may now stall its caller; a loop "
            f"read must never be in here.")

    def test_no_write_method_is_in_the_set(self):
        for w in ("place_order", "place_and_wait_for_fill", "cancel_order", "modify_order"):
            assert w not in MULTI_CALL_READ_METHODS, (
                f"{w} is a WRITE. A longer transport timeout there delays the "
                f"cancel-then-verify path that resolves order ambiguity.")


class TestTheOrderPathIsUntouched:
    def test_place_and_wait_still_scales_on_its_server_budget(self, client):
        c, spy = client
        c._http_transport("place_and_wait_for_fill", [], {"timeout_seconds": 90})
        assert spy.seen["place_and_wait_for_fill"] == 100.0, (
            "the fill call's server-budget + 10s scaling changed — a "
            "legitimately-still-filling order would trip a transport timeout "
            "and abort ambiguously (L-H1)")

    def test_place_and_wait_without_a_budget_keeps_the_default(self, client):
        c, spy = client
        c._http_transport("place_and_wait_for_fill", [], {})
        assert spy.seen["place_and_wait_for_fill"] == c._timeout
