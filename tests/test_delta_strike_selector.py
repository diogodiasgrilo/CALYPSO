"""Tests for shared.delta_strike_selector — the shared parameterized
delta-target strike picker (built for Variant F, written reusable for a
future multi-strike strategy per docs/STRATEGY_CANDIDATES.md)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.delta_strike_selector import select_strike_by_delta


class FakeBroker:
    """Deterministic strike -> |delta| map, with call-count tracking."""

    def __init__(self, deltas_by_conid, fail_conids=None):
        self.deltas_by_conid = deltas_by_conid
        self.fail_conids = fail_conids or set()
        self.calls = []

    def get_option_greeks(self, conid):
        self.calls.append(conid)
        if conid in self.fail_conids:
            raise RuntimeError("simulated quote failure")
        delta = self.deltas_by_conid.get(conid)
        if delta is None:
            return {"delta": None}
        return {"delta": delta}


class TestSelectStrikeByDelta:
    def test_empty_strike_map_returns_none(self):
        broker = FakeBroker({})
        assert select_strike_by_delta(broker, {}, 0.15, "Call", base=100.0) is None

    def test_picks_call_strike_closest_to_target_delta(self):
        # Calls: delta decreases as strike rises further OTM above base=100.
        strike_map = {100: 1, 105: 2, 110: 3, 115: 4, 120: 5}
        broker = FakeBroker({1: 0.50, 2: 0.30, 3: 0.15, 4: 0.08, 5: 0.03})
        result = select_strike_by_delta(
            broker, strike_map, target_delta=0.15, right="Call", base=100.0,
            band=(0.05, 0.60),
        )
        assert result == 110

    def test_picks_put_strike_closest_to_target_delta_scans_downward(self):
        # Puts: delta (as |delta|) decreases as strike falls further OTM below base.
        strike_map = {80: 1, 85: 2, 90: 3, 95: 4, 100: 5}
        broker = FakeBroker({1: 0.03, 2: 0.08, 3: 0.15, 4: 0.30, 5: 0.50})
        result = select_strike_by_delta(
            broker, strike_map, target_delta=0.15, right="Put", base=100.0,
            band=(0.05, 0.60),
        )
        assert result == 90

    def test_returns_none_when_nothing_in_band(self):
        strike_map = {100: 1, 105: 2}
        broker = FakeBroker({1: 0.90, 2: 0.85})
        result = select_strike_by_delta(
            broker, strike_map, target_delta=0.15, right="Call", base=100.0,
            band=(0.05, 0.30),
        )
        assert result is None

    def test_respects_max_reads_cap(self):
        # 10 candidates, but cap reads at 3 — must not read more than 3 conids.
        strike_map = {100 + i * 5: i + 1 for i in range(10)}
        broker = FakeBroker({v: 0.50 - v * 0.01 for v in strike_map.values()})
        select_strike_by_delta(
            broker, strike_map, target_delta=0.15, right="Call", base=100.0,
            band=(0.0, 1.0), max_reads=3,
        )
        assert len(broker.calls) <= 3

    def test_skips_conid_with_no_delta_and_continues(self):
        # First candidate near base has an unreadable delta (None) — must skip
        # forward rather than abort the whole search.
        strike_map = {100: 1, 105: 2, 110: 3}
        broker = FakeBroker({1: None, 2: 0.30, 3: 0.15})
        result = select_strike_by_delta(
            broker, strike_map, target_delta=0.15, right="Call", base=100.0,
            band=(0.05, 0.60),
        )
        assert result == 110

    def test_broker_exception_treated_as_unreadable_delta(self):
        strike_map = {100: 1, 105: 2}
        broker = FakeBroker({2: 0.15}, fail_conids={1})
        result = select_strike_by_delta(
            broker, strike_map, target_delta=0.15, right="Call", base=100.0,
            band=(0.05, 0.60),
        )
        assert result == 105

    def test_default_band_accepts_any_positive_delta(self):
        strike_map = {100: 1}
        broker = FakeBroker({1: 0.42})
        result = select_strike_by_delta(broker, strike_map, target_delta=0.15, right="Call", base=100.0)
        assert result == 100

    def test_reusable_for_repeated_calls_at_different_target_deltas(self):
        # The "build once, reuse for a future 3-target-delta strategy" claim —
        # calling the same function 3x with different target_delta values (as a
        # broken-wing butterfly's wing/body/near-wing picks would) works
        # independently per call, no shared/leaked state between calls.
        strike_map = {90: 1, 95: 2, 100: 3, 105: 4, 110: 5}
        broker = FakeBroker({1: 0.05, 2: 0.15, 3: 0.50, 4: 0.15, 5: 0.05})
        wing = select_strike_by_delta(broker, strike_map, 0.05, "Call", base=100.0, band=(0.0, 1.0))
        body = select_strike_by_delta(broker, strike_map, 0.50, "Call", base=100.0, band=(0.0, 1.0))
        assert wing == 110
        assert body == 100
