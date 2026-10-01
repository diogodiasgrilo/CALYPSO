"""A truncated option chain cost B two entire trading days, silently.

`limit=250` x the caller's `max_pages=4` capped the Polygon chain at exactly
1000 contracts. On the two days of fifteen recorded that SPX listed more than
that — 2026-09-18 and 2026-09-30 — the remainder was dropped with **no log
line of any kind**.

The failure is not a slightly smaller profile. A dropped tail leaves a HOLE in
the delta ladder, and `find_strike_at_delta` picks whatever survived closest to
8d: on 09-30 the nearest put with delta data was 1.3d, ~120pt OTM, while the
call side was unaffected at ~48pt. The 4.0d floor then aborted all seven
entries as "degraded data". B traded on all thirteen untruncated days and on
neither truncated one.

So two properties matter, and the second as much as the first: the cap must be
high enough not to bite, AND hitting it must never be silent again. The guard
that caught the symptom blamed "chain under-hydrated", which sent a day's
investigation after a hydration timeout that misses only 4.8% of the time.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.brandon import gex_provider as gp  # noqa: E402


def _pager(total_pages, page_size=250):
    """A fake Polygon that pages `total_pages` times."""
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        i = calls["n"]
        body = {"status": "OK",
                "results": [{"ticker": f"O:SPXW_{i}_{k}"} for k in range(page_size)]}
        if i < total_pages:
            body["next_url"] = f"https://api.polygon.io/next?page={i+1}"
        return body

    return fetch, calls


# The real entry point. Discovered by reading the module rather than guessing:
# an earlier version of this file hunted for a name ending in "_fetch_chain",
# found nothing, and SKIPPED the two tests that actually exercise the warning —
# which is indistinguishable from having no tests at all.
FETCH = gp.fetch_polygon_chain
EXPIRY = __import__("datetime").date(2026, 10, 1)


class TestTheCapIsHighEnough:
    def test_the_caller_default_is_no_longer_four(self):
        """4 x 250 = 1000, the exact number observed on both broken days."""
        import inspect
        src = inspect.getsource(gp)
        assert "max_pages: int = 4," not in src, (
            "the caller's pagination cap is back to 4 pages = 1000 contracts, "
            "which truncated the chain on 2 of 15 days and cost B both of them")

    def test_twenty_pages_covers_the_observed_range(self):
        """Observed untruncated chains ran 484-652. 20 x 250 = 5000 is headroom,
        not a new guess."""
        import inspect
        src = inspect.getsource(gp)
        assert "max_pages: int = 20," in src


class TestTruncationIsNeverSilent:
    def test_hitting_the_cap_logs_a_warning(self, caplog):
        import logging
        fetch, calls = _pager(total_pages=50)
        with caplog.at_level(logging.WARNING):
            out = FETCH(underlying="SPX", expiry=EXPIRY, api_key="k", http_fetch=fetch, max_pages=2)
        assert calls["n"] == 2, f"paged {calls['n']} times, expected the cap of 2"
        assert len(out) == 500, f"got {len(out)} contracts"
        assert any("TRUNCATED" in str(r.msg) for r in caplog.records), (
            "the chain hit its page cap and nothing warned — the exact silence "
            "that hid this for two trading days")
        assert gp.chain_was_truncated() is True

    def test_a_complete_chain_does_not_warn(self, caplog):
        import logging
        fetch, calls = _pager(total_pages=3)
        with caplog.at_level(logging.WARNING):
            out = FETCH(underlying="SPX", expiry=EXPIRY, api_key="k", http_fetch=fetch, max_pages=20)
        assert len(out) == 750, f"got {len(out)}; the full chain is 3 pages"
        assert not any("TRUNCATED" in str(r.msg) for r in caplog.records), (
            "a complete chain reported truncation — a false alarm here trains "
            "people to ignore the real one")
        assert gp.chain_was_truncated() is False

    def test_the_real_world_case_now_completes(self, caplog):
        """09-30 needed more than 4 pages. At the old cap it truncated; at 20
        the same chain comes back whole."""
        import logging
        fetch, _ = _pager(total_pages=5)          # 1250 contracts, > the old 1000
        with caplog.at_level(logging.WARNING):
            out = FETCH(underlying="SPX", expiry=EXPIRY, api_key="k", http_fetch=fetch, max_pages=20)
        assert len(out) == 1250
        assert gp.chain_was_truncated() is False, (
            "a 5-page chain still reports truncation at a 20-page cap")

        fetch, _ = _pager(total_pages=5)
        with caplog.at_level(logging.WARNING):
            old = FETCH(underlying="SPX", expiry=EXPIRY, api_key="k", http_fetch=fetch, max_pages=4)
        assert len(old) == 1000, (
            f"the old cap returned {len(old)}, not the 1000 seen in production")
        assert gp.chain_was_truncated() is True

    def test_the_flag_is_queryable(self):
        assert hasattr(gp, "chain_was_truncated"), (
            "no way for telemetry or a caller to ask whether the last chain "
            "was truncated")

    def test_the_warning_names_the_downstream_symptom(self):
        """Whoever reads this log line next should not have to rediscover that
        it surfaces as 'under-hydrated' on the delta floor."""
        import inspect
        src = inspect.getsource(gp)
        i = src.find("chain TRUNCATED")
        assert i > 0
        window = src[i:i + 700]
        assert "delta ladder" in window and "under-hydrated" in window
