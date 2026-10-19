"""Nine strategies fetched the SAME SPX bars for the SAME EMA, every heartbeat.

The per-label gate attribution added 2026-10-19 measured
`history:marketdata_history_by_conid` at **16.7% of acquisitions** — because all
nine strategy processes ask this one broker for identical SPX 1-minute bars to
compute identical EMA20/EMA40. Nine requests for data that changes once a minute.

Safe to cache DELIBERATELY, not by luck. Both consumers are
non-trading-critical:

    _get_trend_signal()                 the EMA trend signal, which is
        "Informational only (logged + stored, does NOT drive entry type)"
        — CLAUDE.md
    _refresh_chart_data_for_scouting()  feeds MKT-031 Smart Entry Windows,
        which is DISABLED

No stop, credit gate, strike selection or settlement path reads bars. Contrast
the quote cache, which had to EXCLUDE option legs precisely because the stop
monitor reads them — that exclusion is why this one can be unconditional.

TTL defaults to 30s: shorter than the 1-minute bar it serves, so the newest bar
can never be more than one bar stale, while nine per-heartbeat fetches collapse
into one.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from shared.ib_client import IBClient  # noqa: E402

BARS = [{"t": 1, "o": 1, "h": 2, "l": 0, "c": 1.5},
        {"t": 2, "o": 1.5, "h": 2.5, "l": 1, "c": 2.0}]


def _client(ttl=30.0, bars=None, fail_after=None):
    from unittest.mock import MagicMock
    c = IBClient.__new__(IBClient)
    c._require_connected = lambda: None
    c._client = MagicMock()      # get_chart_data dereferences it before calling
    c._chart_cache = {}
    c._chart_cache_lock = threading.Lock()
    c._chart_cache_ttl = ttl
    c._chart_cache_stats = {"hits": 0, "misses": 0, "stored": 0, "empty": 0}
    c.qualify_contract = lambda sym, **kw: 416904
    c._fetches = 0

    def _ib_call(family, fn, *a, **kw):
        c._fetches += 1
        if fail_after is not None and c._fetches > fail_after:
            return {"data": []}
        return {"data": list(BARS if bars is None else bars)}
    c._ib_call = _ib_call
    return c


class TestTheReduction:
    def test_nine_identical_requests_cause_one_fetch(self):
        """The actual shape of the problem: nine variants, same bars."""
        c = _client()
        for _ in range(9):
            out = c.get_chart_data("SPX", bar="1min", period="2h")
            assert out == BARS
        assert c._fetches == 1, f"expected 1 IBKR fetch, got {c._fetches}"
        st = c.chart_cache_stats
        assert st["hits"] == 8 and st["misses"] == 1
        assert st["hit_pct"] == pytest.approx(88.9, abs=0.2)

    def test_the_cache_expires(self):
        c = _client(ttl=0.05)
        c.get_chart_data("SPX")
        time.sleep(0.08)
        c.get_chart_data("SPX")
        assert c._fetches == 2

    def test_ttl_zero_disables_it_entirely(self):
        c = _client(ttl=0.0)
        for _ in range(3):
            c.get_chart_data("SPX")
        assert c._fetches == 3
        assert c.chart_cache_stats == {}, "stats must be {} when off"


class TestItCannotServeTheWrongData:
    @pytest.mark.parametrize("kw", [
        {"bar": "5min"}, {"period": "8h"}, {"outside_rth": True}, {"symbol": "VIX"},
    ])
    def test_a_different_request_shape_is_a_different_key(self, kw):
        c = _client()
        base = dict(symbol="SPX", bar="1min", period="2h", outside_rth=False)
        c.get_chart_data(**base)
        c.get_chart_data(**{**base, **kw})
        assert c._fetches == 2, f"{kw} must not reuse the SPX/1min/2h entry"

    def test_callers_cannot_mutate_the_cached_list(self):
        """A caller appending to the returned list would corrupt every
        subsequent reader — the quote cache copies for the same reason.

        ⚠️ Must mutate the list returned from a HIT. An earlier version mutated
        the one returned from the MISS, which is the freshly-fetched list and is
        already a distinct object from the stored `list(bars)` copy — so the
        test passed even with the read-side copy removed. A negative control
        caught it: it is the READ path that needs copying."""
        c = _client()
        c.get_chart_data("SPX")                 # miss -> populates the cache
        hit1 = c.get_chart_data("SPX")          # HIT
        assert c._fetches == 1
        hit1.append({"t": 999})
        hit2 = c.get_chart_data("SPX")          # HIT again
        assert len(hit2) == len(BARS), "the cache handed out its own list"
        assert c._fetches == 1, "still served from cache"

    def test_an_empty_result_is_never_cached(self):
        """An empty bar list is what a transient history outage looks like.
        Caching it would amplify one miss across every strategy for the TTL."""
        c = _client(bars=[])
        c.get_chart_data("SPX")
        c.get_chart_data("SPX")
        assert c._fetches == 2, "an empty result must not be served from cache"
        assert c.chart_cache_stats["empty"] == 2
        assert c.chart_cache_stats["stored"] == 0

    def test_a_good_result_then_an_outage_still_serves_the_good_one(self):
        """Within the TTL. The alternative — evicting on failure — would make a
        blip worse than caching ever was."""
        c = _client(fail_after=1)
        assert c.get_chart_data("SPX") == BARS
        assert c.get_chart_data("SPX") == BARS
        assert c._fetches == 1


class TestSafetyInvariants:
    def test_concurrent_readers_do_not_lose_counts(self):
        c = _client()
        def worker():
            for _ in range(50):
                c.get_chart_data("SPX")
        ts = [threading.Thread(target=worker) for _ in range(8)]
        for t in ts: t.start()
        for t in ts: t.join()
        st = c.chart_cache_stats
        assert st["hits"] + st["misses"] == 400

    def test_stats_is_a_property_not_a_method(self):
        """`broker_service` publishes it via getattr WITHOUT calling it, so a
        plain method would put a bound method into /health. quote_cache_stats
        is a @property for exactly this reason and mine initially was not."""
        assert isinstance(IBClient.__dict__["chart_cache_stats"], property)

    def test_health_publishes_it(self):
        src = open(os.path.join(ROOT, "shared/broker_service.py")).read()
        assert '"chart_cache": cc' in src

    def test_disconnect_clears_it(self):
        """Bars from a dead session must not survive it — a reconnect may land
        on a different trading day."""
        src = open(os.path.join(ROOT, "shared/ib_client.py")).read()
        assert "self._chart_cache.clear()" in src

    def test_bars_feed_no_trading_critical_path(self):
        """The premise of the whole change. If a stop/credit/strike path ever
        starts reading bars, this cache needs an exclusion like the quote
        cache's option-leg rule."""
        src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
        consumers = [ln for ln in src.split("\n")
                     if "_read_recent_bars(" in ln and "def " not in ln
                     and not ln.strip().startswith(("#", '"', "f\""))]
        assert len(consumers) <= 2, (
            f"a new chart-data consumer appeared ({len(consumers)}) — verify it "
            f"is not on a stop/credit/strike path before trusting the cache")
