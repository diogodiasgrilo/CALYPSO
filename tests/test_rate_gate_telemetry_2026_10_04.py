"""The request-rate gate must record the delay it imposes.

It computed `wait`, slept on it, and recorded NOTHING — no counter, no log, no
metric. A full trading day of the live seat's log yields only a startup "pacing
multiplier" banner, so gate-induced delay was invisible.

That made B7 (raising the gate 5 -> 7) undecidable: the justification is that
queuing measurably delays work — especially the gap between closing leg 1 and
leg 2 of a stop, where exit slippage averages +$195/stop — and with no
measurement it could only be argued. These pin the reading.
"""
import time

import pytest

from shared.ib_client import IBClient, _RateGate


class TestItCountsAndTimes:
    def test_a_fresh_gate_reports_zeroes_not_junk(self):
        s = _RateGate(5.0).stats()
        assert s["acquisitions"] == 0
        assert s["waited"] == 0
        assert s["waited_pct"] == 0.0
        assert s["mean_wait_ms"] == 0.0, "must not divide by zero acquisitions"

    def test_it_counts_every_acquisition(self):
        g = _RateGate(1000.0)          # 1ms spacing: fast, no real sleeping
        for _ in range(25):
            g.acquire()
        assert g.stats()["acquisitions"] == 25

    def test_a_burst_registers_waiting(self):
        g = _RateGate(50.0)            # 20ms spacing
        t0 = time.monotonic()
        for _ in range(10):
            g.acquire()
        elapsed = time.monotonic() - t0
        s = g.stats()
        assert s["acquisitions"] == 10
        assert s["waited"] >= 8, s
        assert s["total_wait_s"] > 0
        # The recorded wait should account for most of the wall clock.
        assert s["total_wait_s"] <= elapsed + 0.05

    def test_max_wait_cannot_exceed_the_interval(self):
        """Successive reservations are spaced 1/rps apart, so no single caller
        waits longer than one interval."""
        g = _RateGate(20.0)            # 50ms
        for _ in range(6):
            g.acquire()
        assert g.stats()["max_wait_ms"] <= 50.0 + 1.0

    def test_an_unhurried_caller_records_no_wait(self):
        g = _RateGate(1000.0)
        g.acquire()
        time.sleep(0.01)
        g.acquire()
        s = g.stats()
        assert s["acquisitions"] == 2
        assert s["waited"] == 0, "a slot already in the past must not count as a wait"

    def test_max_rps_is_reported_back(self):
        assert _RateGate(7.0).stats()["max_rps"] == 7.0

    def test_stats_never_reset_themselves(self):
        """Callers difference two readings; a self-resetting counter breaks that."""
        g = _RateGate(1000.0)
        for _ in range(5):
            g.acquire()
        a = g.stats()["acquisitions"]
        b = g.stats()["acquisitions"]
        assert a == b == 5


class TestItIsExposedWhereItCanBeRead:
    def test_absent_gate_gives_an_empty_dict_not_a_crash(self):
        c = IBClient.__new__(IBClient)
        c._rate_gate = None
        assert c.rate_gate_stats == {}

    def test_missing_attribute_entirely(self):
        assert IBClient.__new__(IBClient).rate_gate_stats == {}

    def test_a_live_gate_is_surfaced(self):
        c = IBClient.__new__(IBClient)
        c._rate_gate = _RateGate(5.0)
        c._rate_gate.acquire()
        assert c.rate_gate_stats["acquisitions"] == 1

    def test_a_broken_gate_does_not_propagate(self):
        """Telemetry must never break the call path it measures."""
        class Boom:
            def stats(self):
                raise RuntimeError("nope")
        c = IBClient.__new__(IBClient)
        c._rate_gate = Boom()
        assert c.rate_gate_stats == {}


class TestHealthPublishesIt:
    def test_probe_health_merges_rate_gate(self):
        from shared.broker_service import BrokerDispatcher

        class FakeIB:
            rate_gate_stats = {"max_rps": 5.0, "acquisitions": 42, "waited": 7}
            def check_auth_status(self):
                return {"authenticated": True, "connected": True, "competing": False}

        d = BrokerDispatcher.__new__(BrokerDispatcher)
        d._ib = FakeIB()
        d._identity = lambda: {"environment": "paper", "account": "DU1"}
        h = d._probe_health()
        assert h["status"] == "ok"
        assert h["rate_gate"]["acquisitions"] == 42, h

    def test_rate_gate_appears_even_when_degraded(self):
        """A saturated gate is one reason a session goes degraded — so the
        number must survive the degraded return path."""
        from shared.broker_service import BrokerDispatcher

        class FakeIB:
            rate_gate_stats = {"max_rps": 5.0, "acquisitions": 9}
            def check_auth_status(self):
                raise RuntimeError("session gone")

        d = BrokerDispatcher.__new__(BrokerDispatcher)
        d._ib = FakeIB()
        d._identity = lambda: {"environment": "paper", "account": "DU1"}
        h = d._probe_health()
        assert h["status"] == "degraded"
        assert h["rate_gate"]["acquisitions"] == 9, h
