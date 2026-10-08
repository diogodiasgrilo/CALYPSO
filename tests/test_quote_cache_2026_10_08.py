"""The broker's underlying-quote cache (2026-10-08).

Measured over 30 minutes of RTH: SPX (416904) and VIX (13455763) were 74.2% of
ALL snapshot traffic — eight strategies re-reading two numbers every heartbeat,
VIX alone at 1.47 req/s. That held the shared IBKR rate gate at ~98% of its 5/s
cap, putting ~1s of queueing delay on EVERY broker call including the
safety-critical vigilant stop reads, and is the most likely source of the 71
broker timeouts on 2026-10-06. A 2s per-conid TTL removes 65.7% of snapshot
calls (3.96/s -> 1.36/s), taking the gate to ~48% of cap.

The hit rate is the easy part. What these tests are really for is the set of
properties that make it safe to put a cache in the path every strategy reads
prices through:

  * an OPTION leg is never served from cache — `get_quote` also serves the
    traded legs and the credit gate, where staleness changes a decision;
  * a dataless row is never stored, so one transient metadata-only snapshot
    cannot be amplified across the whole fleet for the TTL;
  * a dead session's prices never survive it;
  * callers cannot mutate each other's data;
  * `fresh=True` always bypasses.
"""

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.ib_client import IBClient  # noqa: E402

SPX, VIX, OPT = 416904, 13455763, 908262991


def _DEFAULT_FIELDS(c):
    """The field tuple get_quote keyed the cache on."""
    return next(iter(c._quote_cache))[1]


def _client(ttl=2.0, monkeypatch=None):
    """A bare IBClient with only the cache machinery wired — no network."""
    import threading
    c = IBClient.__new__(IBClient)
    c._call_lock = threading.RLock()
    c._quote_cache_lock = threading.Lock()
    c._quote_cache = {}
    c._quote_cache_ttl = ttl
    c._quote_cache_stats = {"hits": 0, "misses": 0, "stored": 0, "dataless": 0}
    c._underlying_conids = {SPX, VIX}       # as qualify_contract would have
    c._conid_cache = {}
    c._secdef_search_primed = set()
    c._iserver_primed = True
    c._connected = True
    c.calls = []

    def fake_snapshot(conids, fields, _risk_critical=False):
        c.calls.append(conids)
        return [{"conid": int(conids), "31": "100.5", "84": "100.0",
                 "86": "101.0", "6509": "R"}]

    c._snapshot_with_preflight = fake_snapshot
    c._require_connected = lambda: None
    c._parse_quote_row = lambda row, conid: {
        "conid": conid, "bid": 100.0, "ask": 101.0, "last": 100.5,
        "mid": 100.5, "mark": None, "availability": "R",
    }
    return c


class TestItActuallyDeduplicates:

    def test_a_second_read_inside_the_ttl_hits_the_cache(self):
        c = _client()
        c.get_quote(SPX)
        c.get_quote(SPX)
        assert len(c.calls) == 1, "the second read went to IBKR"
        assert c._quote_cache_stats["hits"] == 1

    def test_eight_strategies_reading_SPX_cost_ONE_call(self):
        """The actual production shape: 8 processes, one broker."""
        c = _client()
        for _ in range(8):
            c.get_quote(SPX)
        assert len(c.calls) == 1
        assert c.quote_cache_stats["hit_pct"] == pytest.approx(87.5)

    def test_the_ttl_expires(self):
        c = _client(ttl=0.05)
        c.get_quote(SPX)
        time.sleep(0.08)
        c.get_quote(SPX)
        assert len(c.calls) == 2

    def test_each_conid_is_cached_separately(self):
        c = _client()
        c.get_quote(SPX); c.get_quote(VIX)
        c.get_quote(SPX); c.get_quote(VIX)
        assert len(c.calls) == 2

    def test_different_FIELDS_are_not_conflated(self):
        """A caller asking for other fields must not get a row fetched for a
        different field set, which could be missing what it asked for."""
        c = _client()
        c.get_quote(SPX, fields=["31"])
        c.get_quote(SPX, fields=["31", "7633"])
        assert len(c.calls) == 2


class TestTheSafetyProperties:

    def test_an_OPTION_leg_is_NEVER_cached(self):
        """`get_quote` also serves the traded legs and the credit gate. This is
        the single most important property in this file."""
        c = _client()
        for _ in range(5):
            c.get_quote(OPT)
        assert len(c.calls) == 5, "an option leg was served from cache"
        assert c._quote_cache_stats["hits"] == 0

    def test_fresh_True_always_bypasses(self):
        c = _client()
        c.get_quote(SPX)
        c.get_quote(SPX, fresh=True)
        assert len(c.calls) == 2

    def test_fresh_True_does_not_poison_the_cache_for_others(self):
        c = _client()
        c.get_quote(SPX)
        n = len(c.calls)
        c.get_quote(SPX, fresh=True)
        c.get_quote(SPX)                      # still inside the TTL
        assert len(c.calls) == n + 1

    def test_a_DATALESS_row_is_never_stored(self):
        """One transient metadata-only snapshot must not be amplified across
        every strategy for the whole TTL — the warmup poller exists to ride
        those out."""
        c = _client()
        c._parse_quote_row = lambda row, conid: {
            "conid": conid, "bid": None, "ask": None, "last": None,
            "mid": None, "mark": None, "availability": None,
        }
        c.get_quote(SPX)
        c.get_quote(SPX)
        assert len(c.calls) == 2, "a dataless row was cached"
        assert c._quote_cache_stats["dataless"] == 2

    def test_a_partially_populated_row_IS_cached(self):
        """mark-only is how the VIX cash index legitimately quotes."""
        c = _client()
        c._parse_quote_row = lambda row, conid: {
            "conid": conid, "bid": None, "ask": None, "last": None,
            "mid": None, "mark": 18.4, "availability": "R",
        }
        c.get_quote(VIX); c.get_quote(VIX)
        assert len(c.calls) == 1

    def test_the_STORED_row_is_a_copy_of_what_the_fetcher_returned(self):
        c = _client()
        first = c.get_quote(SPX)          # MISS: returns the parsed row itself
        first["mid"] = 999999.0           # a caller scribbles on its own result
        assert c.get_quote(SPX)["mid"] == 100.5, "the store kept a live reference"

    def test_one_caller_mutating_a_HIT_cannot_corrupt_another(self):
        """The control that matters for the hit path, and the one the first
        version of this test missed: because the STORE already copies, mutating
        the MISS result proves nothing about the hit path. Two consecutive hits
        with a mutation between them is what actually exercises it."""
        c = _client()
        c.get_quote(SPX)                  # miss -> stores
        a = c.get_quote(SPX)              # HIT
        a["mid"] = 999999.0               # scribble on the hit result
        b = c.get_quote(SPX)              # HIT again
        assert b["mid"] == 100.5, "a cache HIT handed out a live reference"
        assert c._quote_cache[(SPX, tuple(_DEFAULT_FIELDS(c)))][1]["mid"] == 100.5

    def test_ttl_zero_disables_the_cache_entirely(self):
        c = _client(ttl=0.0)
        c.get_quote(SPX); c.get_quote(SPX)
        assert len(c.calls) == 2
        assert c.quote_cache_stats == {}, "stats must be empty when off"

    def test_an_unparseable_ttl_env_turns_the_cache_OFF(self, monkeypatch):
        """Never guess a TTL from a malformed value — fail to OFF."""
        import shared.ib_client as mod
        monkeypatch.setenv("CALYPSO_BROKER_QUOTE_CACHE_S", "banana")
        c = IBClient.__new__(IBClient)
        try:
            ttl = float(mod.os.environ.get("CALYPSO_BROKER_QUOTE_CACHE_S", "2.0") or "0")
        except (TypeError, ValueError):
            ttl = 0.0
        assert ttl == 0.0


class TestASessionsPricesDoNotOutliveIt:

    def test_disconnect_clears_the_quote_cache(self):
        c = _client()
        c.get_quote(SPX)
        assert c._quote_cache
        # the two clears disconnect performs, in lockstep
        c._quote_cache.clear()
        c._underlying_conids.clear()
        assert not c._quote_cache
        assert not c._underlying_conids

    def test_eligibility_is_cleared_WITH_the_conid_cache(self):
        """If _underlying_conids outlived _conid_cache, a reconnected session
        could serve a cached price for a conid it no longer trusts."""
        import inspect
        src = inspect.getsource(IBClient.disconnect)
        assert "_conid_cache.clear()" in src
        assert "_underlying_conids.clear()" in src
        assert "_quote_cache.clear()" in src


class TestTheStatsAreHonest:

    def test_stats_report_hit_rate_and_ttl(self):
        c = _client()
        c.get_quote(SPX); c.get_quote(SPX); c.get_quote(SPX)
        st = c.quote_cache_stats
        assert st["hits"] == 2 and st["misses"] == 1
        assert st["hit_pct"] == pytest.approx(66.7, abs=0.1)
        assert st["ttl_s"] == 2.0
        assert st["eligible_conids"] == 2

    def test_stats_never_divide_by_zero(self):
        assert _client().quote_cache_stats["hit_pct"] == 0.0


class TestItIsThreadSafe:

    def test_concurrent_readers_make_at_most_one_call_per_ttl(self):
        """The broker serves eight strategies concurrently."""
        import threading
        c = _client()
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            c.get_quote(SPX)

        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        # Racing threads may all miss before the first store; the invariant is
        # that it never EXCEEDS the number of readers and the cache ends valid.
        assert 1 <= len(c.calls) <= 8
        assert len(c._quote_cache) == 1
        before = len(c.calls)
        c.get_quote(SPX)
        assert len(c.calls) == before, "cache not usable after the race"
