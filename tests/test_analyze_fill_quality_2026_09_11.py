"""
Fill-quality analyzer: what we paid vs the mid, per leg (2026-09-11).

This tool exists to answer ONE question that decides whether the 2026-09-11
entry-pricing change stays: did resting on the spread instead of crossing it
save more than it cost? Both halves matter — a report that showed only the price
improvement would be measuring the benefit and hiding the cost, since a passive
rung that does not fill escalates (5% -> 10% -> MARKET) and a part-filled entry
pays the spread TWICE on the unwind.

THE SIGN CONVENTION IS THE WHOLE THING. Short legs are SOLD, long legs are
BOUGHT, so "adverse" means opposite things on the two sides:

    SHORT (sold):    adverse when fill < mid   ->  gap = mid - fill
    LONG  (bought):  adverse when fill > mid   ->  gap = fill - mid

Getting that backwards would report the leak as a gain of the same magnitude —
a wrong answer that looks entirely plausible. Hence a test per leg type, each
asserting the sign explicitly rather than the absolute value.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.analyze_fill_quality import (  # noqa: E402
    LEGS,
    _buckets,
    _leg_gap,
    analyze,
    render,
)


def _db(tmp_path, rows):
    p = tmp_path / "t.db"
    con = sqlite3.connect(str(p))
    cols = (["date TEXT", "entry_number INT", "entry_time TEXT", "entry_type TEXT",
             "contracts INT", "total_credit REAL", "time_to_fill_ms REAL",
             "attempts INTEGER"]
            + [f"{s}_fill_price REAL" for s, _ in LEGS]
            + [f"{s}_mid_at_fill REAL" for s, _ in LEGS])
    con.execute(f"CREATE TABLE trade_entries ({', '.join(cols)})")
    for r in rows:
        keys = list(r)
        con.execute(
            f"INSERT INTO trade_entries ({','.join(keys)}) "
            f"VALUES ({','.join('?' * len(keys))})", [r[k] for k in keys])
    con.commit(); con.close()
    return str(p)


def _entry(date="2026-09-11", n=1, contracts=7, ms=5000, **legs):
    r = {"date": date, "entry_number": n, "entry_time": f"{date} 10:45:00",
         "entry_type": "full_ic", "contracts": contracts, "total_credit": 200.0,
         "time_to_fill_ms": ms}
    r.setdefault("attempts", 1)
    r.update(legs)
    return r


class TestTheSignConvention:
    """One test per leg type, asserting the SIGN, not the magnitude."""

    def test_a_long_bought_ABOVE_mid_is_a_LOSS(self):
        """Paying the ask when mid was lower — the exact bug being fixed."""
        row = {"long_call_fill_price": 0.60, "long_call_mid_at_fill": 0.575,
               "contracts": 7}
        assert _leg_gap(row, "long_call", True) == pytest.approx(17.5)

    def test_a_long_bought_BELOW_mid_is_a_GAIN(self):
        row = {"long_call_fill_price": 0.55, "long_call_mid_at_fill": 0.575,
               "contracts": 7}
        assert _leg_gap(row, "long_call", True) == pytest.approx(-17.5)

    def test_a_short_sold_BELOW_mid_is_a_LOSS(self):
        """Hitting the bid when mid was higher."""
        row = {"short_call_fill_price": 0.55, "short_call_mid_at_fill": 0.575,
               "contracts": 7}
        assert _leg_gap(row, "short_call", False) == pytest.approx(17.5)

    def test_a_short_sold_ABOVE_mid_is_a_GAIN(self):
        row = {"short_call_fill_price": 0.60, "short_call_mid_at_fill": 0.575,
               "contracts": 7}
        assert _leg_gap(row, "short_call", False) == pytest.approx(-17.5)

    def test_the_two_sides_are_INVERSES_at_identical_prices(self):
        """The single most valuable assertion here: same fill, same mid, opposite
        sign. A convention collapsed to one formula fails this."""
        row = {"x_fill_price": 0.60, "x_mid_at_fill": 0.575, "contracts": 7}
        long_g = _leg_gap({"a_fill_price": 0.60, "a_mid_at_fill": 0.575, "contracts": 7},
                          "a", True)
        short_g = _leg_gap({"a_fill_price": 0.60, "a_mid_at_fill": 0.575, "contracts": 7},
                           "a", False)
        assert long_g == pytest.approx(-short_g)
        assert long_g != 0

    def test_it_scales_with_contracts_and_the_100_multiplier(self):
        row = {"a_fill_price": 0.60, "a_mid_at_fill": 0.55, "contracts": 10}
        assert _leg_gap(row, "a", True) == pytest.approx(0.05 * 100 * 10)


class TestUnquotableLegsAreSkippedNotZeroed:
    """A missing quote must not be counted as a perfect fill — that would dilute
    the average toward zero and hide the leak."""

    @pytest.mark.parametrize("fill,mid", [(None, 0.5), (0.5, None), (0, 0.5), (0.5, 0)])
    def test_missing_or_zero_returns_None(self, fill, mid):
        row = {"a_fill_price": fill, "a_mid_at_fill": mid, "contracts": 7}
        assert _leg_gap(row, "a", True) is None

    def test_a_skipped_leg_does_not_inflate_n(self, tmp_path):
        db = _db(tmp_path, [_entry(
            long_call_fill_price=0.60, long_call_mid_at_fill=0.575,
            long_put_fill_price=None, long_put_mid_at_fill=None)])
        res = analyze(db, since=None)
        assert res["legs"]["long_call"]["n"] == 1
        assert res["legs"]["long_put"]["n"] == 0


class TestAggregation:
    def test_the_total_is_the_sum_of_the_legs(self, tmp_path):
        db = _db(tmp_path, [_entry(
            long_call_fill_price=0.60, long_call_mid_at_fill=0.575,   # +17.50
            long_put_fill_price=0.60, long_put_mid_at_fill=0.575,     # +17.50
            short_call_fill_price=0.60, short_call_mid_at_fill=0.575,  # -17.50
            short_put_fill_price=0.55, short_put_mid_at_fill=0.575)])  # +17.50
        res = analyze(db, since=None)
        assert res["total_gap"] == pytest.approx(35.0)

    def test_adverse_improved_and_flat_are_counted_separately(self, tmp_path):
        db = _db(tmp_path, [
            _entry(n=1, long_call_fill_price=0.60, long_call_mid_at_fill=0.575),
            _entry(n=2, long_call_fill_price=0.55, long_call_mid_at_fill=0.575),
            _entry(n=3, long_call_fill_price=0.575, long_call_mid_at_fill=0.575),
        ])
        s = analyze(db, since=None)["legs"]["long_call"]
        assert (s["adverse"], s["improved"], s["flat"]) == (1, 1, 1)

    def test_the_date_floor_is_applied(self, tmp_path):
        db = _db(tmp_path, [
            _entry(date="2026-09-09", long_call_fill_price=0.60, long_call_mid_at_fill=0.55),
            _entry(date="2026-09-11", long_call_fill_price=0.60, long_call_mid_at_fill=0.55),
        ])
        assert analyze(db, since="2026-09-11")["n_entries"] == 1
        assert analyze(db, since=None)["n_entries"] == 2

    def test_the_until_bound_is_EXCLUSIVE(self, tmp_path):
        """--compare splits at a date; the split date must land in AFTER, not
        both. An inclusive bound would double-count the deploy day."""
        db = _db(tmp_path, [
            _entry(date="2026-09-10", long_call_fill_price=0.60, long_call_mid_at_fill=0.55),
            _entry(date="2026-09-11", long_call_fill_price=0.60, long_call_mid_at_fill=0.55),
        ])
        before = analyze(db, since=None, until="2026-09-11")
        after = analyze(db, since="2026-09-11")
        assert before["n_entries"] == 1 and after["n_entries"] == 1
        assert before["dates"] == ["2026-09-10"]
        assert after["dates"] == ["2026-09-11"]


class TestLegInDurationBuckets:
    """NOT rung counts. `_fill_start` is set before the call that places ALL FOUR
    legs, so this is the whole entry's leg-in wall-clock. The first draft of this
    tool bucketed it as rungs and would have reported "93% escalated past rung 1"
    on B's pre-change data — while B was crossing the spread on every entry. Four
    legs at ~13s each is ~52s with everything filling instantly, and B's median
    was 52.9s. The SIGNAL IS A SHIFT between windows, not the absolute value."""

    @pytest.mark.parametrize("seconds,bucket", [
        (2, "<30s"), (29, "<30s"),
        (30, "30-60s"), (59, "30-60s"),
        (60, "60-90s"), (89, "60-90s"),
        (90, ">90s"), (200, ">90s"),
    ])
    def test_bucket_boundaries(self, seconds, bucket):
        b = _buckets([seconds * 1000])
        assert b[bucket] == 1
        assert sum(b.values()) == 1

    def test_a_rightward_shift_is_visible(self):
        """What we are actually watching for: passive rungs that fail to fill
        make the whole entry take longer."""
        fast = _buckets([20000] * 10)
        slow = _buckets([70000] * 10)
        assert fast["<30s"] == 10 and fast["60-90s"] == 0
        assert slow["<30s"] == 0 and slow["60-90s"] == 10

    def test_the_buckets_are_NOT_labelled_as_rungs(self):
        """Regression guard on the corrected framing — calling these rungs is
        what made the first draft wrong."""
        assert not any("rung" in k.lower() for k in _buckets([1000]))


class TestEntryRetries:
    def test_retries_are_counted(self, tmp_path):
        db = _db(tmp_path, [
            _entry(n=1, attempts=1, long_call_fill_price=0.6, long_call_mid_at_fill=0.55),
            _entry(n=2, attempts=2, long_call_fill_price=0.6, long_call_mid_at_fill=0.55),
        ])
        assert analyze(db, since=None)["attempts"] == [1, 2]

    def test_a_missing_attempts_value_defaults_to_one(self, tmp_path):
        db = _db(tmp_path, [_entry(long_call_fill_price=0.6, long_call_mid_at_fill=0.55)])
        assert analyze(db, since=None)["attempts"] == [1]

    def test_the_retry_rate_is_rendered(self, tmp_path):
        db = _db(tmp_path, [
            _entry(n=1, attempts=2, long_call_fill_price=0.6, long_call_mid_at_fill=0.55)])
        assert "entry-level retries" in render(analyze(db, since=None), "x")


class TestRenderIsHonest:
    def test_an_empty_window_says_so_rather_than_printing_zeros(self, tmp_path):
        out = render(analyze(_db(tmp_path, []), since=None), "x")
        assert "no entries" in out

    def test_it_states_the_sign_convention_in_the_output(self, tmp_path):
        db = _db(tmp_path, [_entry(long_call_fill_price=0.60, long_call_mid_at_fill=0.55)])
        out = render(analyze(db, since=None), "x")
        assert "positive = money lost" in out

    def test_it_warns_the_duration_is_not_a_rung_count(self, tmp_path):
        db = _db(tmp_path, [_entry(long_call_fill_price=0.60, long_call_mid_at_fill=0.55)])
        out = render(analyze(db, since=None), "x")
        assert "NOT a rung count" in out

    def test_it_does_not_divide_by_zero_on_a_single_day(self, tmp_path):
        db = _db(tmp_path, [_entry(long_call_fill_price=0.60, long_call_mid_at_fill=0.55)])
        assert "/day" in render(analyze(db, since=None), "x")
