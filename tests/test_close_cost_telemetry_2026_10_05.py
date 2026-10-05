"""Decompose what a close actually costs — without touching the close itself.

WHY. A stop-loss close leaks a measured −$197 (n=20, t=2.73), nearly 2× variant
B's entire live net profit, while the EOD flatten leaks about nothing (−$5,
t=0.17). So the expense is specific to closing under STOP conditions, and
`trade_stops` records only the total. Three numbers split it:

    trigger -> place   our own latency (bot + a 96%-saturated rate gate)
    place -> fill      the broker's fill time (NOT ours to fix)
    fill vs quote      the spread we actually crossed

Unlike the strategy's edge (t=0.24, ~2500 days to measure) this needs ~29
observations, so it is a question that can be finished.

These drive the REAL `_place_marketable_close` against a REAL DataRecorder and
read the rows back.
"""
import sqlite3

import pytest

from bots.hydra.base_strategy import MEICStrategy
from shared.data_recorder import DataRecorder


class _Rig(MEICStrategy):
    """Concrete subclass — MEICStrategy is abstract."""
    def _calculate_strikes(self, *a, **k): raise NotImplementedError
    def _check_stop_losses(self, *a, **k): raise NotImplementedError
    def _initiate_entry(self, *a, **k): raise NotImplementedError


def _rig(tmp_path, quote, fill=6.20, filled_qty=7):
    s = _Rig.__new__(_Rig)
    rec = DataRecorder(str(tmp_path / "t.db"))
    assert rec.ensure_schema(), "schema init failed — the rig, not the code"
    s._data_recorder = rec
    s.eod_flatten_market_minutes = 0.0
    s.quote_calls = 0
    def _q(uic):
        s.quote_calls += 1
        return quote
    s._read_option_quote = _q
    s.placed = []
    def _place(**kw):
        s.placed.append(kw)
        return {"filled": True, "fill_price": fill, "filled_quantity": filled_qty,
                "order_id": "o1"}
    s._place_leg_order = _place
    s._close_leg_order = lambda **kw: {"filled": True, "fill_price": fill,
                                       "filled_quantity": filled_qty, "order_id": "o2"}
    return s


def _rows(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.row_factory = sqlite3.Row
    r = [dict(x) for x in c.execute("SELECT * FROM close_leg_executions")]
    c.close()
    return r


def test_a_limit_close_records_the_quote_it_priced_against(tmp_path):
    s = _rig(tmp_path, {"bid": 7.00, "ask": 7.10}, fill=6.20)
    s._place_marketable_close(uic=918981908, side="SELL", quantity=7, attempt_num=1)
    r = _rows(tmp_path)
    assert len(r) == 1, r
    row = r[0]
    assert row["order_type"] == "LMT"
    assert row["bid_at_place"] == 7.00 and row["ask_at_place"] == 7.10
    assert row["fill_price"] == 6.20
    assert row["order_side"] == "SELL" and row["quantity"] == 7
    assert row["conid"] == 918981908
    # the whole point: sold 0.80 under the bid we priced against
    assert row["bid_at_place"] - row["fill_price"] == pytest.approx(0.80)


def test_it_adds_no_broker_traffic(tmp_path):
    """The rate gate runs at 96% of capacity — telemetry must be free."""
    s = _rig(tmp_path, {"bid": 7.00, "ask": 7.10})
    s._place_marketable_close(uic=1, side="SELL", quantity=7, attempt_num=1)
    assert s.quote_calls == 1, (
        "the quote must be the one the limit pricing already fetched, not a "
        "second read; got %d calls" % s.quote_calls)


def test_timings_are_recorded(tmp_path):
    s = _rig(tmp_path, {"bid": 7.00, "ask": 7.10})
    s._close_ctx = None
    s._place_marketable_close(uic=1, side="SELL", quantity=7, attempt_num=1)
    row = _rows(tmp_path)[0]
    assert row["place_to_fill_ms"] is not None and row["place_to_fill_ms"] >= 0
    # no context -> no trigger reference, so this one is legitimately NULL
    assert row["trigger_to_place_ms"] is None


def test_context_attributes_the_row_and_times_from_the_trigger(tmp_path):
    s = _rig(tmp_path, {"bid": 7.00, "ask": 7.10})
    with s.close_cost_context(entry_number=5, entry_side="call", reason="stop_loss"):
        s._close_ctx["leg_name"] = "short_call"
        s._place_marketable_close(uic=1, side="BUY", quantity=7, attempt_num=2)
    row = _rows(tmp_path)[0]
    assert row["entry_number"] == 5
    assert row["entry_side"] == "call"
    assert row["close_reason"] == "stop_loss"
    assert row["leg_name"] == "short_call"
    assert row["attempt"] == 2
    assert row["trigger_to_place_ms"] is not None


def test_no_quote_falls_back_to_market_and_still_records(tmp_path):
    s = _rig(tmp_path, None)
    s._place_marketable_close(uic=1, side="SELL", quantity=7, attempt_num=1)
    row = _rows(tmp_path)[0]
    assert row["order_type"] == "MKT_NOQUOTE"
    assert row["bid_at_place"] is None and row["limit_price"] is None
    assert row["fill_price"] == 6.20, "a MARKET close must still record its fill"


def test_mkt047_escalation_is_recorded_distinctly(tmp_path, monkeypatch):
    """Near the close MKT-047 forces a true MARKET order — label it, because its
    cost profile is not comparable to a crossing limit.

    The clock is PINNED. Reading the real one made this pass only between 09:30
    and 16:00 ET: outside the session `mins_to_close` goes negative, the
    escalation correctly declines, and the test failed for a reason that had
    nothing to do with what it tests. (Caught at 18:03 ET — third wall-clock
    dependency found in this codebase's tests this week.)
    """
    import datetime
    import bots.hydra.base_strategy as bs
    pinned = datetime.datetime(2026, 10, 5, 15, 55)      # 5 min before the close
    monkeypatch.setattr(bs, "get_us_market_time", lambda: pinned)
    monkeypatch.setattr(bs, "get_market_close_time", lambda _n: pinned.replace(hour=16, minute=0))
    s = _rig(tmp_path, {"bid": 7.00, "ask": 7.10})
    s.eod_flatten_market_minutes = 10.0         # 5 min to close <= 10 -> escalate
    s._place_marketable_close(uic=1, side="SELL", quantity=7, attempt_num=1)
    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["order_type"] == "MKT_MKT047", rows[0]


def test_a_broken_recorder_CANNOT_break_the_close(tmp_path):
    """THE safety property. A close is a risk action; telemetry is not."""
    s = _rig(tmp_path, {"bid": 7.00, "ask": 7.10})
    class Boom:
        def record_close_leg(self, *a, **k):
            raise RuntimeError("disk full")
    s._data_recorder = Boom()
    res = s._place_marketable_close(uic=1, side="SELL", quantity=7, attempt_num=1)
    assert res["filled"] is True, "the close was lost because telemetry threw"
    assert res["fill_price"] == 6.20


def test_no_recorder_at_all_is_fine(tmp_path):
    s = _rig(tmp_path, {"bid": 7.00, "ask": 7.10})
    s._data_recorder = None
    res = s._place_marketable_close(uic=1, side="SELL", quantity=7, attempt_num=1)
    assert res["filled"] is True


def test_every_attempt_of_the_same_leg_is_kept(tmp_path):
    """Retries are where the cost compounds — they must not overwrite."""
    s = _rig(tmp_path, {"bid": 7.00, "ask": 7.10})
    for n in (1, 2, 3):
        s._place_marketable_close(uic=1, side="SELL", quantity=7, attempt_num=n)
    assert sorted(r["attempt"] for r in _rows(tmp_path)) == [1, 2, 3]
