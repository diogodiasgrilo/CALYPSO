"""A short-TTL cache for position reads — with the two safety properties.

WHY. `portfolio/<acct>/positions/0` was **32% of the broker's shared IBKR
request budget** (904 calls in a 600s window, measured 2026-09-24), and that
gate runs **85% saturated**. Stop DETECTION queues behind it. The callers are
all display — the status log, the P&L banner, the dashboard metrics — each
re-fetching identical rows on a per-tick cadence.

A ~3s TTL is bounded by IBKR's own behaviour, not our appetite:
``ib_client.get_balance`` records that the risk engine updates about every 3s,
"so polling faster than that is pointless".

THE TWO PROPERTIES THAT MAKE IT SAFE, and the tests that would catch losing
either one:

1. ``strict=True`` never reads the cache — those callers (settlement,
   overnight checks, the EMERGENCY-001 "already closed?" probe) act
   irreversibly, and a stale "flat" would abandon an open position.
   -> ``test_strict_NEVER_reads_the_cache``
2. Any order action invalidates — a read right after a fill must never be
   served from before it.
   -> ``test_placing_an_order_invalidates`` / ``test_cancelling_invalidates``

Both are written as controls: delete the guard and they go red while the
"it caches at all" tests stay green.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _row(conid=111, qty=-7):
    return {
        "conid": conid, "position": qty, "assetClass": "OPT",
        "strike": "7705", "putOrCall": "C", "expiry": "20260924",
        "unrealizedPnl": 0.0,
    }


class _S(HydraStrategy):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise AttributeError(name)


def _strategy(ttl=3.0, rows=None):
    s = HydraStrategy.__new__(HydraStrategy)
    s.strategy_config = {"positions_cache_ttl_s": ttl}
    s._positions_cache = None
    s.broker = MagicMock()
    s.broker.get_positions = MagicMock(return_value=list(rows if rows is not None else [_row()]))
    return s


def _reads(s) -> int:
    return s.broker.get_positions.call_count


# --------------------------------------------------------------------------
class TestItActuallyCaches:
    def test_second_read_does_not_hit_the_broker(self):
        s = _strategy()
        a = HydraStrategy._read_open_positions(s)
        b = HydraStrategy._read_open_positions(s)
        assert _reads(s) == 1
        assert a == b and len(a) == 1

    def test_ttl_zero_disables_caching_entirely(self):
        """The escape hatch has to actually work."""
        s = _strategy(ttl=0)
        HydraStrategy._read_open_positions(s)
        HydraStrategy._read_open_positions(s)
        assert _reads(s) == 2

    def test_expiry_forces_a_refetch(self, monkeypatch):
        s = _strategy(ttl=3.0)
        clock = {"t": 1000.0}
        monkeypatch.setattr("bots.hydra.strategy.time.monotonic", lambda: clock["t"])
        HydraStrategy._read_open_positions(s)
        clock["t"] += 2.9
        HydraStrategy._read_open_positions(s)
        assert _reads(s) == 1, "still inside the TTL"
        clock["t"] += 0.2
        HydraStrategy._read_open_positions(s)
        assert _reads(s) == 2, "past the TTL"

    def test_mutating_a_MISS_result_cannot_poison_the_cache(self):
        """Guards the copy in _positions_cache_put (the miss path returns the
        freshly-built list, so the cache must store its own copies)."""
        s = _strategy()
        first = HydraStrategy._read_open_positions(s)      # miss -> populates
        first[0]["quantity"] = 999_999
        assert HydraStrategy._read_open_positions(s)[0]["quantity"] == -7

    def test_mutating_a_HIT_result_cannot_poison_the_cache(self):
        """Guards the copy in _positions_cache_get. Without it, the second
        reader hands out the cache's own dicts and a third read sees the
        mutation. The miss-path test above does NOT cover this — verified by
        control: removing only the _get copy left it green."""
        s = _strategy()
        HydraStrategy._read_open_positions(s)              # miss -> populates
        hit = HydraStrategy._read_open_positions(s)        # hit
        hit[0]["quantity"] = 999_999
        assert HydraStrategy._read_open_positions(s)[0]["quantity"] == -7


# --------------------------------------------------------------------------
class TestSafetyProperties:
    def test_strict_NEVER_reads_the_cache(self):
        """CONTROL. A stale 'flat' served to a strict caller abandons an open
        position — settlement and the EMERGENCY-001 probe both act on this."""
        s = _strategy()
        HydraStrategy._read_open_positions(s)            # populate
        HydraStrategy._read_open_positions(s, strict=True)
        assert _reads(s) == 2, "strict read was served from cache"

    def test_strict_still_refreshes_the_cache(self):
        s = _strategy()
        HydraStrategy._read_open_positions(s, strict=True)   # 1 fetch, populates
        HydraStrategy._read_open_positions(s)                # should be a hit
        assert _reads(s) == 1

    def test_strict_still_raises_on_fetch_failure(self):
        """Pre-existing contract: strict must distinguish failure from empty."""
        s = _strategy()
        s.broker.get_positions = MagicMock(side_effect=RuntimeError("broker down"))
        try:
            HydraStrategy._read_open_positions(s, strict=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("strict swallowed a fetch failure")
        assert HydraStrategy._read_open_positions(s) == []   # non-strict degrades

    def test_placing_an_order_invalidates(self):
        """CONTROL. A read right after a fill must not predate it."""
        s = _strategy()
        HydraStrategy._read_open_positions(s)
        s.broker.place_and_wait_for_fill = MagicMock(
            return_value={"status": "filled", "filled_quantity": 7, "avg_price": 1.0})
        HydraStrategy._place_leg_order(
            s, instrument_id=111, side="BUY", quantity=7,
            order_type="MKT", limit_price=None)
        HydraStrategy._read_open_positions(s)
        assert _reads(s) == 2, "stale positions served after an order"

    def test_cancelling_invalidates(self):
        """A cancel can race a fill — the ORDER-010 case that stranded 2 puts."""
        s = _strategy()
        HydraStrategy._read_open_positions(s)
        s.broker.cancel_order = MagicMock(return_value=True)
        HydraStrategy._cancel_order(s, "abc")
        HydraStrategy._read_open_positions(s)
        assert _reads(s) == 2, "stale positions served after a cancel"

    def test_a_failed_placement_still_invalidates(self):
        """Invalidation happens BEFORE the order, so an exception mid-flight
        cannot leave a pre-order snapshot readable."""
        s = _strategy()
        HydraStrategy._read_open_positions(s)
        s.broker.place_and_wait_for_fill = MagicMock(side_effect=RuntimeError("boom"))
        try:
            HydraStrategy._place_leg_order(
                s, instrument_id=111, side="BUY", quantity=7,
                order_type="MKT", limit_price=None)
        except Exception:
            pass
        HydraStrategy._read_open_positions(s)
        assert _reads(s) == 2
