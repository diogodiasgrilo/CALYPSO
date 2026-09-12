"""
/iserver/account/trades returned nothing because we never told IBKR WHICH
account (2026-09-10).

THE SYMPTOM. Probing the endpoint over a 7-day window that contained dozens of
real paper fills returned ZERO rows — no error, no exception, a clean empty
list. That silence made it look like a paper-account limitation, and it was
recorded as one.

THE ACTUAL CAUSE, traced end to end:
  1. we construct `IbkrClient(use_oauth=True, oauth_config=...)` and pass NO
     account_id;
  2. `$IBIND_ACCOUNT_ID` (ibind's env fallback) is set nowhere in the deploy;
  3. so ibind's own `self.account_id` is None;
  4. ibind's `trades()` does `if account_id is None: account_id = self.account_id`
     — still None;
  5. its `params_dict(optional={...})` DROPS empty optionals, so the request
     goes out with only `days` and no `accountId`;
  6. IBKR answers with an empty list.

Every OTHER IBClient method already passes `account_id=self.account_id` — 11
call sites. The two `trades()` calls were the only ones that did not.

WHY IT MATTERS BEYOND THIS ENDPOINT. `get_closed_position_price` is documented
as the F5 closed-position fill-price authority and reads the same endpoint, so
it has been returning None on every lookup. Its emptiness is ALSO the only thing
that prevented the L-M3 double-book from firing on 2026-09-04 (see
test_lm3_double_book_guard_2026_09_10.py) — which is why that guard had to land
BEFORE this fix, not after.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_client import IBClient  # noqa: E402

ACCOUNT = "DU1234567"


def _client(rows):
    c = IBClient.__new__(IBClient)
    c._require_connected = lambda: None
    c._account_id = ACCOUNT
    c._client = MagicMock()
    c.calls = []

    def _ib_call(family, fn, *a, **kw):
        c.calls.append((getattr(fn, "_mock_name", None) or str(fn), kw))
        return rows if fn is c._client.trades else []

    c._ib_call = _ib_call
    return c


def _trades_kwargs(c):
    """The kwargs of the trades() call, or None if it was never made."""
    for name, kw in c.calls:
        if "days" in kw:
            return kw
    return None


class TestAccountIdIsSent:
    """Mutation for every test here: delete `account_id=self.account_id` from
    the trades() call. Each one fails."""

    def test_get_day_executions_passes_account_id(self):
        c = _client([])
        IBClient.get_day_executions(c, days=1)
        kw = _trades_kwargs(c)
        assert kw is not None, "trades() was never called"
        assert kw.get("account_id") == ACCOUNT

    def test_get_closed_position_price_passes_account_id(self):
        c = _client([])
        IBClient.get_closed_position_price(c, 12345, buy_or_sell="Buy")
        kw = _trades_kwargs(c)
        assert kw is not None, "trades() was never called"
        assert kw.get("account_id") == ACCOUNT

    def test_it_is_the_discovered_account_not_a_literal(self):
        """A hardcoded account id would pass the tests above but break the
        moment the account changes — including the paper->live move."""
        c = _client([])
        c._account_id = "DU9999999"
        IBClient.get_day_executions(c, days=1)
        assert _trades_kwargs(c).get("account_id") == "DU9999999"


class TestNoCallSiteIsLeftBehind:
    def test_every_trades_call_in_the_client_passes_account_id(self):
        """Guards against a THIRD trades() call site being added later without
        account_id — the exact omission being fixed here."""
        src = inspect.getsource(IBClient)
        # crude but effective: each `self._client.trades` must have an
        # account_id within the following ~120 chars of the call expression
        idx = 0
        found = 0
        while True:
            idx = src.find("self._client.trades", idx)
            if idx == -1:
                break
            found += 1
            window = src[idx:idx + 200]
            assert "account_id" in window, (
                f"a self._client.trades call site at offset {idx} does not "
                f"pass account_id"
            )
            idx += 1
        assert found >= 2, f"expected >=2 trades() call sites, found {found}"


class TestTheDaysWindowIsStillClamped:
    """Pre-existing behaviour — IBKR caps the lookback at 7 days."""

    @pytest.mark.parametrize("asked,sent", [(0, "1"), (1, "1"), (7, "7"), (99, "7")])
    def test_days_is_clamped_to_1_7(self, asked, sent):
        c = _client([])
        IBClient.get_day_executions(c, days=asked)
        assert _trades_kwargs(c).get("days") == sent


class TestThePrimingIsStillDoneFirst:
    def test_brokerage_accounts_is_primed_before_trades(self):
        """IBKR returns 500 'Please query /accounts first' without it, and
        connect() only primes the DIFFERENT /portfolio/accounts namespace.
        Order matters, so assert the sequence, not just presence."""
        c = _client([])
        IBClient.get_day_executions(c, days=1)
        names = [n for n, _ in c.calls]
        assert any("receive_brokerage_accounts" in n for n in names)
        prime_i = next(i for i, n in enumerate(names)
                       if "receive_brokerage_accounts" in n)
        trades_i = next(i for i, (_, kw) in enumerate(c.calls) if "days" in kw)
        assert prime_i < trades_i, "priming must precede the trades call"
