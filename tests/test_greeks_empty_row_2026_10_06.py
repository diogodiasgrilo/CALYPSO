"""An EMPTY greeks row is truthy — which silently wrote NULL deltas for weeks.

IBKR answers a not-yet-warm conid with a row carrying no greek fields at all
(observed live: keys {'55','conidEx','_updated','conid','6119','31'}). Every
value parsed to None, the dict was STILL truthy, the caller treated it as
success and wrote delta=NULL, and nothing logged.

Consequence: variant G's `delta_call` — the field that verifies its own +/-8
delta rule — was NULL on both of 2026-10-05's entries and on roughly 1 entry in
4 for weeks, while variant B never missed once in 29. B quotes the same conids
repeatedly across its 7 slots, so they are warm by entry time; G and F do not.

The fetch also fires the instant the entry completes, straight into the
snapshot-warmup window (CLAUDE.md "Snapshot warmup", ~6s). It is analytics-only
and off the trading path, so retrying is free.
"""
import pytest

from bots.hydra.strategy import HydraStrategy, GREEKS_FETCH_ATTEMPTS


class _Broker:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get_option_greeks(self, conid):
        self.calls += 1
        return self.responses.pop(0) if self.responses else {}


def _s(responses):
    s = HydraStrategy.__new__(HydraStrategy)
    s.broker = _Broker(responses)
    return s


# The exact shape IBKR returned for a cold conid, captured from the live broker.
COLD_ROW = {"55": "-", "conidEx": "918981908", "_updated": 1791273777964,
            "conid": 918981908, "6119": "q3", "server_id": "q3", "31": "N/A",
            "delta": None, "gamma": None, "theta": None, "vega": None,
            "iv": None, "open_interest": None}
WARM_ROW = {"delta": 0.082, "gamma": 0.001, "theta": -12.4, "vega": 3.1,
            "iv": 0.145, "open_interest": 220}


class TestTheReaderContract:
    def test_an_empty_row_returns_None_not_a_dict_of_Nones(self):
        """THE BUG. A truthy dict of Nones is what the caller mistook for data."""
        assert _s([COLD_ROW])._read_option_greeks(1) is None

    def test_a_populated_row_is_returned(self):
        g = _s([WARM_ROW])._read_option_greeks(1)
        assert g["delta"] == pytest.approx(0.082)
        assert g["theta"] == pytest.approx(-12.4)

    def test_nothing_at_all_returns_None(self):
        assert _s([{}])._read_option_greeks(1) is None
        assert _s([None])._read_option_greeks(1) is None

    def test_a_partial_row_with_a_real_delta_is_kept(self):
        """Only an ENTIRELY empty row is discarded — a real delta with missing
        open_interest is still worth recording."""
        g = _s([{"delta": 0.07, "theta": None, "vega": None}])._read_option_greeks(1)
        assert g is not None and g["delta"] == pytest.approx(0.07)

    def test_a_broker_exception_is_caught(self):
        s = HydraStrategy.__new__(HydraStrategy)
        class Boom:
            def get_option_greeks(self, c): raise RuntimeError("gate open")
        s.broker = Boom()
        assert s._read_option_greeks(1) is None


class _Entry:
    entry_number = 2
    short_call_uic = 111
    short_put_uic = 222


class TestTheRetry:
    def test_a_cold_first_attempt_is_retried_and_succeeds(self, monkeypatch):
        monkeypatch.setattr("bots.hydra.strategy.GREEKS_RETRY_DELAY_S", 0.0)
        # call cold, put cold, then both warm
        s = _s([COLD_ROW, COLD_ROW, WARM_ROW, WARM_ROW])
        out = s._fetch_entry_greeks(_Entry())
        assert set(out) == {"call", "put"}, out
        assert out["call"]["delta"] == pytest.approx(0.082)

    def test_only_the_EMPTY_side_is_retried(self, monkeypatch):
        """The rate gate runs at 96% — a side that already answered must not be
        re-fetched."""
        monkeypatch.setattr("bots.hydra.strategy.GREEKS_RETRY_DELAY_S", 0.0)
        s = _s([WARM_ROW, COLD_ROW, WARM_ROW])   # call ok, put cold, put ok
        out = s._fetch_entry_greeks(_Entry())
        assert set(out) == {"call", "put"}
        assert s.broker.calls == 3, (
            "a side that already succeeded was re-fetched; got %d calls" % s.broker.calls)

    def test_it_gives_up_after_the_attempt_cap(self, monkeypatch):
        monkeypatch.setattr("bots.hydra.strategy.GREEKS_RETRY_DELAY_S", 0.0)
        s = _s([COLD_ROW] * 20)
        out = s._fetch_entry_greeks(_Entry())
        assert out == {}
        assert s.broker.calls == 2 * GREEKS_FETCH_ATTEMPTS, s.broker.calls

    def test_a_missing_conid_side_is_skipped_entirely(self, monkeypatch):
        monkeypatch.setattr("bots.hydra.strategy.GREEKS_RETRY_DELAY_S", 0.0)
        class OneSided:
            entry_number = 1
            short_call_uic = 111
            short_put_uic = None
        s = _s([WARM_ROW])
        out = s._fetch_entry_greeks(OneSided())
        assert set(out) == {"call"}
        assert s.broker.calls == 1, "a side with no conid was fetched anyway"

    def test_a_raising_reader_does_not_escalate(self, monkeypatch):
        """Analytics must never take down the entry path."""
        monkeypatch.setattr("bots.hydra.strategy.GREEKS_RETRY_DELAY_S", 0.0)
        s = HydraStrategy.__new__(HydraStrategy)
        s._read_option_greeks = lambda uic: (_ for _ in ()).throw(RuntimeError("x"))
        assert s._fetch_entry_greeks(_Entry()) == {}
