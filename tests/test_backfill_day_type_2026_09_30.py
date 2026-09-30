"""The backfill must agree with the live path, and must not guess.

Two failure modes worth pinning. First, the backfill builds a SHIM to feed the
live classifier — if the classifier ever reads an attribute the shim does not
provide, the shim raises, the classifier swallows it (enrichment must never
block settlement) and every backfilled row silently becomes "normal". That
failure looks exactly like success. Second, a day with no recorded ticks must
stay NULL: "not measured" and "measured, and unremarkable" have to stay
distinguishable, or the column lies about its own coverage.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backfill_day_type import classify_row  # noqa: E402
from shared.data_recorder import DataRecorder  # noqa: E402


def _db_with_ticks(tmp_path, date_str, prices):
    db = str(tmp_path / "backtesting.db")
    rec = DataRecorder(db)
    rec.ensure_schema()
    conn = sqlite3.connect(db)
    for i, px in enumerate(prices):
        conn.execute(
            "INSERT INTO market_ticks (timestamp, spx_price, vix_level) VALUES (?,?,?)",
            (f"{date_str} {9 + i // 60:02d}:{i % 60:02d}:00", px, 16.0))
    conn.commit()
    conn.close()
    return rec


class TestItDoesNotGuess:
    def test_a_day_with_no_ticks_is_left_null(self, tmp_path):
        rec = _db_with_ticks(tmp_path, "2026-09-28", [])
        dt, rv, why = classify_row(rec, "2026-09-21", fomc=False)
        assert dt is None and rv is None, (
            "a day with no recorded ticks was given a label — the column would "
            "then claim coverage it does not have")
        assert why


class TestTheShimSatisfiesTheLiveClassifier:
    """If the shim is missing a read, everything collapses to 'normal'."""

    def test_a_trending_session_reads_trend(self, tmp_path):
        # A drift with noise on it, not a ruler. A perfectly linear ramp has
        # near-zero RETURN variance by construction (see the test below), so
        # using one would have asserted the wrong thing about realised vol.
        import math
        prices = [7700 + i * 0.5 + 3.0 * math.sin(i * 1.7) for i in range(160)]
        rec = _db_with_ticks(tmp_path, "2026-09-21", prices)
        dt, rv, why = classify_row(rec, "2026-09-21", fomc=False)
        assert why is None, why
        assert dt == "trend", (
            f"got {dt!r} — a monotone session must read 'trend'; 'normal' here "
            f"means the shim is missing an attribute the classifier reads and "
            f"the exception is being swallowed")
        assert rv is not None and rv > 0, f"realized vol not computed: {rv}"

    def test_a_perfectly_linear_ramp_has_near_zero_realized_vol(self, tmp_path):
        """Not a defect — the definition.

        Realised vol is the dispersion of returns, not the distance travelled.
        A constant-increment ramp moves 80 points and is, correctly, almost
        perfectly un-volatile. Pinned because it looks like a bug on first
        reading and someone will otherwise "fix" it into measuring range,
        which is precisely the thing this column exists NOT to duplicate:
        `spx_high`/`spx_low` already record range.
        """
        prices = [7700 + i * 0.5 for i in range(160)]
        rec = _db_with_ticks(tmp_path, "2026-09-21", prices)
        _, rv, _ = classify_row(rec, "2026-09-21", fomc=False)
        assert rv is not None, "a full session of ticks must produce a number"
        assert rv < 1.0, (
            f"rv={rv} on a straight line — realised vol is being computed from "
            f"range rather than from return dispersion")

    def test_a_round_trip_session_reads_chop(self, tmp_path):
        up = [7700 + i * 0.5 for i in range(80)]
        prices = up + [up[-1] - i * 0.5 for i in range(80)]   # returns to open
        rec = _db_with_ticks(tmp_path, "2026-09-29", prices)
        dt, rv, why = classify_row(rec, "2026-09-29", fomc=False)
        assert why is None, why
        assert dt == "chop", f"got {dt!r} — a round trip is the definition of chop"

    def test_fomc_beats_the_price_path(self, tmp_path):
        prices = [7700 + i * 0.5 for i in range(160)]
        rec = _db_with_ticks(tmp_path, "2026-09-16", prices)
        dt, _, _ = classify_row(rec, "2026-09-16", fomc=True)
        assert dt == "fomc", f"got {dt!r} — an FOMC day must not be relabelled by its range"

    def test_the_same_path_yields_the_same_label_twice(self, tmp_path):
        """Determinism. A classifier that drifts between runs makes the column
        unusable for conditioning, which is the only reason it exists."""
        prices = [7700 + i * 0.5 for i in range(160)]
        rec = _db_with_ticks(tmp_path, "2026-09-21", prices)
        a = classify_row(rec, "2026-09-21", fomc=False)
        b = classify_row(rec, "2026-09-21", fomc=False)
        assert a == b, f"{a} != {b}"


class TestItReusesTheLiveCodeRatherThanACopy:
    def test_it_calls_the_strategy_classifier(self):
        """A second implementation is how the backfilled half and the live half
        of a series come to disagree with nobody able to say which is wrong."""
        import inspect
        import scripts.backfill_day_type as mod
        src = inspect.getsource(mod.classify_row)
        assert "HydraStrategy._classify_day_type" in src, (
            "the backfill no longer calls the live classifier — if it grew its "
            "own copy of the rules, backfilled rows and live rows will diverge")
        assert "HydraStrategy._realized_volatility" in src
