"""Snapshot priming was paid on EVERY read instead of once per conid.

`_snapshot_poll` issued ibind's "prime call" unconditionally, then polled for
data up to 12 more times. So one logical quote read cost between 2 and 13 gate
acquisitions — and the floor of 2 was paid even for a conid read seconds
earlier, because the prime was never remembered.

Measured on the live broker with the market OPEN, 2026-10-19:

    acquisitions 4003 | waited 91.3% | mean 978.85ms | max 1929.06ms
    market:live_marketdata_snapshot   2843   71.0%   wait 2764.2s
    portfolio:positions               1050   26.2%   wait 1051.7s

Snapshots were 71% of demand — nearly 3x positions, which is what redirected the
design away from a positions cache. The monitoring loop re-reads the same legs
every tick, so the WARM case is the common case and the redundant prime is the
floor.

Priming is a per-conid, per-session need: the code's own comment says "the first
call arms the conid's snapshot cache, it returns no data itself". The fix
follows two precedents already in this file — `_iserver_primed` (bool, per
session) and `_secdef_search_primed` (set, per symbol) — with the same `force`
/ clear-on-disconnect discipline.

⚠️ THE SAFETY ARGUMENT, and it is what makes this different from a data cache:
skipping the prime CANNOT return a wrong value. If a conid is marked primed but
IBKR's server-side cache has lapsed, the first data poll returns metadata-only
and the existing warmup loop keeps polling until data arrives. The failure mode
is one extra poll, self-corrected by machinery that already exists. A data cache
can hand back a stale PRICE — which is exactly why option legs are excluded from
the quote cache, and that exclusion is untouched here.

See docs/migration/SNAPSHOT_PRIMING_DESIGN_2026_10_19.md.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import shared.ib_client as ibc  # noqa: E402
from shared.ib_client import IBClient  # noqa: E402


def _row(conid, with_data=True):
    r = {"conid": int(conid), "conidEx": str(conid), "_updated": 1}
    if with_data:
        r["31"] = "100.0"      # last
        r["84"] = "99.5"       # bid
        r["86"] = "100.5"      # ask
    return r


def _client(*, warm_after=0):
    """warm_after: how many DATA polls return metadata-only before real data."""
    c = IBClient.__new__(IBClient)
    c._snapshot_primed = set()
    c._snapshot_warmup_exhausted_count = 0   # set when the budget runs out
    from types import SimpleNamespace
    c.cfg = SimpleNamespace(debug_log_payloads=False)  # the exhaust log gates on it
    c._ensure_iserver_primed = lambda **kw: None
    c._client = MagicMock()
    c.calls = []          # every live_marketdata_snapshot the gate would see
    c._data_polls = 0

    def _ib_call(family, fn, *a, **kw):
        c.calls.append(kw.get("conids"))
        conids = [x.strip() for x in str(kw.get("conids") or "").split(",") if x.strip()]
        # The prime call returns nothing; data polls return rows.
        idx = len(c.calls)
        c._data_polls += 1
        with_data = c._data_polls > warm_after
        return [_row(x, with_data) for x in conids]
    c._ib_call = _ib_call
    return c


class TestTheReduction:
    def test_first_read_primes_then_polls(self):
        c = _client()
        c._snapshot_poll("111", ["31"])
        assert len(c.calls) == 2, f"cold read should be prime+poll, got {c.calls}"
        assert "111" in c._snapshot_primed

    def test_second_read_of_a_warm_conid_skips_the_prime(self):
        """The whole point: the warm case is the monitoring loop's case."""
        c = _client()
        c._snapshot_poll("111", ["31"])
        c.calls.clear()
        c._snapshot_poll("111", ["31"])
        assert len(c.calls) == 1, f"warm read should be ONE call, got {c.calls}"

    def test_a_tick_loop_over_warm_legs_costs_one_call_each(self):
        c = _client()
        legs = "1,2,3,4"
        c._snapshot_poll(legs, ["31"])       # cold: prime + poll
        c.calls.clear()
        for _ in range(10):                   # ten monitoring ticks
            c._snapshot_poll(legs, ["31"])
        assert len(c.calls) == 10, (
            f"10 warm batch reads should be 10 calls, got {len(c.calls)}")

    def test_a_batch_with_one_cold_conid_still_primes(self):
        """Partial warmth must not skip the prime for the cold member."""
        c = _client()
        c._snapshot_poll("1,2", ["31"])
        c.calls.clear()
        c._snapshot_poll("1,2,3", ["31"])     # 3 is cold
        assert len(c.calls) == 2, "a cold member must trigger the prime"

    def test_negative_control_without_the_skip_every_read_costs_two(self):
        """Proves the assertions above are not vacuous: a conid that never gets
        recorded as primed pays the prime forever."""
        c = _client()
        c._snapshot_poll("111", ["31"])
        c._snapshot_primed.clear()            # simulate never recording
        c.calls.clear()
        c._snapshot_poll("111", ["31"])
        assert len(c.calls) == 2


class TestItCannotServeAWrongValue:
    def test_a_lapsed_prime_self_corrects_via_the_warmup_loop(self):
        """Marked primed, but IBKR has forgotten: the first data poll returns
        metadata-only and the warmup loop keeps going until data arrives. Costs
        one extra poll, returns correct data."""
        c = _client(warm_after=2)             # 2 metadata-only polls first
        c._snapshot_primed.add("111")         # claim primed
        out = c._snapshot_poll("111", ["31"])
        assert any("31" in r for r in out), "data must still arrive"
        assert len(c.calls) >= 2, "the warmup loop did the recovery"

    def test_the_warmup_budget_is_unchanged(self):
        """12 x 0.5s is calibrated (P7-audit H10, 'less is brittle'). This change
        must not have altered it."""
        assert ibc._SNAPSHOT_MAX_WARMUP_POLLS == 12
        assert ibc._SNAPSHOT_POLL_INTERVAL_S == 0.5

    def test_only_conids_that_returned_DATA_are_recorded(self, monkeypatch):
        """A metadata-only row means NOT armed. Recording it would skip the
        prime for a conid that still needs it."""
        monkeypatch.setattr(ibc, "_SNAPSHOT_POLL_INTERVAL_S", 0.0)  # keep it fast
        c = _client(warm_after=99)             # never returns data
        c._snapshot_poll("111", ["31"])
        assert "111" not in c._snapshot_primed
        assert c._snapshot_warmup_exhausted_count == 1, (
            "an exhausted warmup must still be counted — it is the signal that "
            "a conid has no entitlement or the feed is down")


class TestInvalidationObligations:
    """Both from the design doc. Each is a way the set could claim primed
    against a session that has forgotten."""

    def test_disconnect_clears_it(self):
        src = open(os.path.join(ROOT, "shared/ib_client.py")).read()
        assert "self._snapshot_primed.clear()" in src
        # must sit beside the other primed-state teardown
        i = src.index("self._secdef_search_primed.clear()")
        assert "self._snapshot_primed.clear()" in src[i:i + 400]

    def test_the_daily_reset_self_heal_clears_it(self):
        """The 01:00 ET reset wipes server-side priming while OAuth stays
        authenticated — no disconnect, so the set would otherwise keep claiming
        primed all day. This is why secdef has force=True."""
        src = open(os.path.join(ROOT, "shared/ib_client.py")).read()
        i = src.index("please query /accounts' for conids")
        window = src[i:i + 1400]
        assert "self._snapshot_primed.clear()" in window

    def test_the_set_is_keyed_on_conid_strings(self):
        c = _client()
        c._snapshot_poll("111,222", ["31"])
        assert c._snapshot_primed == {"111", "222"}
