"""The rate gate measured how hard it squeezed, never WHAT it was squeezing.

Measured on the live broker 2026-10-19, ten days of uptime:

    rate_gate:   acquisitions 558,018   waited 91.8%   mean_wait 615ms   max 2,333ms
    quote_cache: hits 156,116  misses 91,959  hit_pct 62.9

So the quote cache works — and snapshots account for only ~92k of 558k
acquisitions (**16%**). The other 84% was completely unattributed. That gap is
why demand reduction was guesswork, and the one previous attempt (the 2026-10-06
pacing change) did not reduce demand at all.

`family` and the ibind method name are both already in scope at the single
`acquire()` call site in `_ib_call._invoke`, so attribution costs one dict
increment inside a lock that is already held for a few microseconds.

⚠️ These tests drive the REAL `_RateGate` — no mocks, no broker, no IBKR. The
gate is constructed at a fast interval so they run instantly.
"""

from __future__ import annotations

import os
import sys
import threading

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from shared.ib_client import _RateGate  # noqa: E402

FAST = 10000.0  # 0.1ms interval — never actually sleeps meaningfully


class TestAttribution:
    def test_calls_are_counted_per_label(self):
        g = _RateGate(FAST)
        for _ in range(5):
            g.acquire("market:live_marketdata_snapshot")
        for _ in range(3):
            g.acquire("portfolio:get_balance")
        s = g.stats()
        assert s["acquisitions"] == 8
        assert s["labels_tracked"] == 2
        top = s["by_label"][0]
        assert top["label"] == "market:live_marketdata_snapshot"
        assert top["calls"] == 5
        assert top["pct"] == pytest.approx(62.5, abs=0.1)

    def test_ordered_by_call_volume_descending(self):
        """Demand reduction acts on volume, so volume is the sort key."""
        g = _RateGate(FAST)
        for label, n in (("a:x", 2), ("b:y", 9), ("c:z", 5)):
            for _ in range(n):
                g.acquire(label)
        assert [r["label"] for r in g.stats()["by_label"]] == ["b:y", "c:z", "a:x"]

    def test_the_breakdown_is_capped_so_health_stays_small(self):
        g = _RateGate(FAST)
        for i in range(40):
            g.acquire(f"family{i}:method{i}")
        s = g.stats()
        assert len(s["by_label"]) == 15, "the list must be capped"
        assert s["labels_tracked"] == 40, "...but the true cardinality is still reported"

    def test_percentages_are_of_TOTAL_acquisitions_not_of_labelled_ones(self):
        """Otherwise an unlabelled caller would silently inflate everyone else's
        share, and the 84%-unattributed problem would reappear in a new form."""
        g = _RateGate(FAST)
        for _ in range(3):
            g.acquire("market:q")
        for _ in range(7):
            g.acquire()                     # unlabelled
        s = g.stats()
        assert s["acquisitions"] == 10
        assert s["by_label"][0]["pct"] == pytest.approx(30.0, abs=0.1)

    def test_wait_is_tracked_per_label_alongside_count(self):
        """Count and wait can disagree — a low-volume caller may consistently
        land in deep queues. Reporting only one would hide that."""
        g = _RateGate(2.0)                  # 500ms interval, so waits are real
        g.acquire("cheap:first")            # no wait, slot is now
        g.acquire("expensive:second")       # waits ~500ms
        s = g.stats()
        by = {r["label"]: r for r in s["by_label"]}
        assert by["cheap:first"]["wait_s"] == pytest.approx(0.0, abs=0.01)
        assert by["expensive:second"]["wait_s"] > 0.3


class TestNothingElseChanged:
    """The existing four counters are what B7 and the quote cache were argued
    from. Attribution must not perturb them."""

    def test_totals_match_the_labelled_sum(self):
        g = _RateGate(FAST)
        for _ in range(11):
            g.acquire("x:y")
        s = g.stats()
        assert s["acquisitions"] == 11
        assert sum(r["calls"] for r in s["by_label"]) == 11

    def test_unlabelled_acquire_still_works(self):
        """The penalty gate and any future caller must not be forced to label."""
        g = _RateGate(FAST)
        g.acquire()
        s = g.stats()
        assert s["acquisitions"] == 1
        assert s["by_label"] == []
        assert s["labels_tracked"] == 0

    def test_the_gate_still_actually_paces(self):
        """The whole point of the class. If attribution broke pacing we would
        have traded a measurement for a 429."""
        import time
        g = _RateGate(20.0)                 # 50ms interval
        t0 = time.monotonic()
        for _ in range(5):
            g.acquire("m:x")
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.15, f"5 calls at 50ms should take >=200ms-ish, took {elapsed:.3f}s"

    def test_concurrent_acquires_do_not_lose_counts(self):
        """The label dict is mutated under the existing lock; prove no races."""
        g = _RateGate(FAST)
        def worker():
            for _ in range(200):
                g.acquire("market:concurrent")
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        s = g.stats()
        assert s["acquisitions"] == 1600
        assert s["by_label"][0]["calls"] == 1600, "a race would lose increments"


class TestTheCallSiteLabelsIt:
    """Source-asserted: `_ib_call` is a ~100-line closure needing a live
    IBClient to drive, so these check the wiring exists. Weak on their own —
    the behavioural proof is above."""

    @pytest.fixture(scope="class")
    def src(self):
        return open(os.path.join(ROOT, "shared/ib_client.py")).read()

    def test_the_label_is_built_from_family_and_method(self, src):
        assert 'f"{family}:{getattr(fn, \'__name__\', None) or type(fn).__name__}"' in src

    def test_both_gates_receive_the_label(self, src):
        assert "self._penalty_gate.acquire(_label)" in src
        assert "self._rate_gate.acquire(_label)" in src

    def test_Dict_is_actually_imported(self, src):
        """It worked without the import because CPython does not evaluate
        ATTRIBUTE annotations — so `Dict[str, int]` on `self._by_label` was an
        undefined name that happened to be harmless. A type-checker flags it,
        and moving the line to class scope would raise NameError."""
        assert "from typing import Dict, Iterable, Optional" in src
