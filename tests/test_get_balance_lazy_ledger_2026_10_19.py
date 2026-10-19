"""`get_balance` fetched the ledger ~1,500x/day per variant and used it once.

The rate-gate attribution added 2026-10-19 measured `portfolio:get_ledger` at
**13.8% of ALL IBKR acquisitions**: `get_balance()` called both
`portfolio_summary` AND `get_ledger` unconditionally, and it runs on every
heartbeat across nine variants.

⚠️ THE LEDGER IS NOT DEAD WEIGHT. `grep get_balance(` finds exactly two live
consumers:

    strategy.py:2472  the margin / ORDER-004 buying-power gate — reads only
                      `tradable`, so it does NOT need the ledger
    strategy.py:7266  BROKER-RECONCILE's independent realized-P&L anchor
                      (`raw_ledger.USD.realizedpnl`) — NEEDS it, once per
                      settlement

That anchor is the only NON-CIRCULAR P&L check in the system, and it is what
exposed the 2026-10-14 stale-settlement phantom (IBKR realized -$1,324.87
against our -$12,485). Deleting the call would have destroyed it. The waste was
never the call — it was fetching it 1,500 times to use it once.

So `with_ledger` defaults to True (no unknown caller changes behaviour), the
heartbeat path opts out explicitly, and the opt-out is LAZY: every branch that
actually needs the ledger fetches it on demand, so correctness never depends on
the caller having asked for the right thing.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from shared.ib_client import IBClient  # noqa: E402

SUMMARY_USD = {"availablefunds": {"amount": 1_000_000.0, "currency": "USD"}}
LEDGER = {"USD": {"cashbalance": 500.0, "exchangerate": 1.0, "realizedpnl": -1324.87},
          "BASE": {"cashbalance": 500.0}}


def _client(summary=None, ledger=None):
    c = IBClient.__new__(IBClient)
    c._require_connected = lambda: None
    c._account_id = "DUR049068"          # account_id is a read-only property
    c._client = MagicMock()
    calls = []

    # Identity comparison, not a name attribute: MagicMock reserves `_name`
    # internally, and an earlier version of this harness set it and silently
    # mislabelled every call.
    def _ib_call(family, fn, *a, **kw):
        if fn is c._client.portfolio_summary:
            calls.append("portfolio_summary")
            return SUMMARY_USD if summary is None else summary
        if fn is c._client.get_ledger:
            calls.append("get_ledger")
            return LEDGER if ledger is None else ledger
        calls.append("other")
        return {}
    c._ib_call = _ib_call
    c._calls = calls
    return c


class TestTheReduction:
    def test_with_ledger_false_skips_the_ledger_call(self):
        c = _client()
        out = c.get_balance("USD", with_ledger=False)
        assert c._calls == ["portfolio_summary"], c._calls
        assert out["tradable"] == pytest.approx(1_000_000.0)

    def test_default_still_fetches_it_so_no_caller_changes_behaviour(self):
        c = _client()
        out = c.get_balance("USD")
        assert c._calls == ["portfolio_summary", "get_ledger"], c._calls
        assert out["raw_ledger"] == LEDGER

    def test_the_reconcile_anchor_survives_on_the_default_path(self):
        """The field BROKER-RECONCILE actually reads."""
        c = _client()
        out = c.get_balance("USD")
        assert (out["raw_ledger"] or {}).get("USD", {}).get("realizedpnl") == pytest.approx(-1324.87)


class TestCorrectnessNeverDependsOnTheCaller:
    """The opt-out is LAZY. Any branch that needs the ledger fetches it even
    when the caller said no — so a wrong `with_ledger` can only cost a call,
    never produce a wrong number."""

    def test_a_non_base_currency_fetches_the_ledger_anyway(self):
        c = _client()
        out = c.get_balance("EUR", with_ledger=False)
        assert "get_ledger" in c._calls, "the FX branch must fetch it on demand"
        assert out["exchange_rate"] is not None or out.get("fx_rate_unavailable")

    def test_a_summary_without_a_currency_fetches_the_ledger_anyway(self):
        """`base_currency` falls back to `_guess_base_currency(ledger)`."""
        c = _client(summary={"availablefunds": {"amount": 5.0}})
        c.get_balance("USD", with_ledger=False)
        assert "get_ledger" in c._calls

    def test_the_ledger_is_fetched_at_most_once_per_call(self):
        """Two lazy sites + the eager path must not triple-call it."""
        c = _client(summary={"availablefunds": {"amount": 5.0}})
        c.get_balance("EUR", with_ledger=True)
        assert c._calls.count("get_ledger") == 1, c._calls


class TestTheHeartbeatSiteOptsOutAndTheAnchorDoesNot:
    @pytest.fixture(scope="class")
    def src(self):
        return open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()

    def test_the_margin_path_passes_with_ledger_false(self, src):
        assert "self.broker.get_balance(with_ledger=False)" in src

    def test_the_reconcile_path_does_NOT(self, src):
        """If this ever opts out, the independent P&L anchor goes blind — the one
        check that is not circular.

        Anchored on the CONSUMING line, not on the first `raw_ledger` in the
        file: an earlier version matched a docstring mention and failed on
        correct code."""
        consume = 'ledger = (bal.get("raw_ledger") or {}).get("USD") or {}'
        assert consume in src, "the reconcile anchor's read is gone"
        i = src.index(consume)
        window = src[max(0, i - 400):i]
        assert "self.broker.get_balance() or {}" in window, (
            "the reconcile site must keep the ledger (no with_ledger=False)")
        assert "with_ledger=False" not in window

    def test_exactly_one_call_site_opts_out(self, src):
        assert src.count("get_balance(with_ledger=False)") == 1
