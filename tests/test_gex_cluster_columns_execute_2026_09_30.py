"""The cluster columns must reach the DATABASE, not merely appear in source.

`tests/test_gex_cluster_columns_populated_2026_09_29.py` guards this fix with
`inspect.getsource` token checks — 6 of its 8 tests assert that a string like
"cluster_n_strikes" appears somewhere in a function body. That is the pattern
that has burned this project repeatedly: a token survives gutting the code
around it, so the test passes against a method that does nothing.

It also could not have caught the real risk here. The fix committed at 14:27 ET
on 2026-09-29; the newest `gex_decisions` row is 14:14 ET the same day, thirteen
minutes earlier. Not one row has been written since it deployed, so all 298
NULLs are pre-fix history and the fix itself is UNVERIFIED rather than broken.
"Deployed but never executed" is the category that quietly fails.

So this drives the real recorder against a real temp database and reads the
five columns back out with SQL.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.brandon.gex_provider import GEXCluster  # noqa: E402
from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from shared.data_recorder import DataRecorder  # noqa: E402

CLUSTER = GEXCluster(strike_low=7600.0, strike_high=7650.0, total_gex=-1.5e9,
                     peak_strike=7625.0, n_strikes=11, strength_pct=42.5)


def _recorder(tmp_path):
    rec = DataRecorder(str(tmp_path / "backtesting.db"))
    rec.ensure_schema()
    return rec


def _strategy(rec):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s._data_recorder = rec
    s.variant_id = "b"
    return s


def _record(s, cluster):
    s._brandon_record_gex_decision(
        consumer="adjuster", entry_number=3, side="put", spot=7680.0,
        reference_strike=7620.0, live_action="SKIP",
        live_adjuster_predicate=True, live_overlay_predicate=False,
        cluster=cluster, profile=None)


def _rows(rec):
    conn = sqlite3.connect(rec.db_path if hasattr(rec, "db_path") else rec._db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT cluster_low, cluster_high, cluster_peak, cluster_n_strikes, "
        "cluster_strength_pct FROM gex_decisions").fetchall()
    conn.close()
    return rows


class TestTheColumnsReachTheDatabase:
    def test_a_recorded_decision_carries_all_five(self, tmp_path):
        rec = _recorder(tmp_path)
        _record(_strategy(rec), CLUSTER)

        rows = _rows(rec)
        assert len(rows) == 1, f"expected exactly one row, got {len(rows)}"
        r = rows[0]
        assert r["cluster_low"] == 7600.0, f"cluster_low={r['cluster_low']}"
        assert r["cluster_high"] == 7650.0, f"cluster_high={r['cluster_high']}"
        assert r["cluster_peak"] == 7625.0, f"cluster_peak={r['cluster_peak']}"
        assert r["cluster_n_strikes"] == 11, f"cluster_n_strikes={r['cluster_n_strikes']}"
        assert r["cluster_strength_pct"] == 42.5, (
            f"cluster_strength_pct={r['cluster_strength_pct']}")

    def test_no_cluster_still_records_the_decision(self, tmp_path):
        """A decision with no covering cluster is a real outcome, not an error.

        It must still produce a row — otherwise the NULL rate stops being
        readable as "no zone here" and becomes "telemetry dropped it".
        """
        rec = _recorder(tmp_path)
        _record(_strategy(rec), None)

        rows = _rows(rec)
        assert len(rows) == 1, "a clusterless decision produced no row at all"
        assert rows[0]["cluster_low"] is None

    def test_telemetry_cannot_break_entry_selection(self, tmp_path):
        """It runs inside the live decision path. A raising recorder must not
        propagate — a broken column is worth less than a placed trade."""
        rec = _recorder(tmp_path)
        s = _strategy(rec)

        class _Boom:
            def record_gex_decision(self, *a, **k):
                raise RuntimeError("db gone")
        s._data_recorder = _Boom()
        _record(s, CLUSTER)  # must not raise


class TestTheLookupFindsTheRightCluster:
    def test_it_returns_the_cluster_covering_the_short(self):
        from types import SimpleNamespace
        other = GEXCluster(strike_low=7700.0, strike_high=7750.0, total_gex=-1e9,
                           peak_strike=7725.0, n_strikes=11, strength_pct=30.0)
        profile = SimpleNamespace(
            negative_clusters=lambda min_strength_pct=None: (other, CLUSTER))
        cfg = SimpleNamespace(accel_min_pct=20.0)
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)

        got = s._brandon_accel_cluster_for(profile, 7620.0, cfg)
        assert got is CLUSTER, (
            f"got {got} — 7620 lies inside 7600-7650, not 7700-7750; picking "
            f"the first cluster regardless of coverage would report the wrong "
            f"zone as the one the adjuster judged")

    def test_a_short_inside_no_cluster_returns_none(self):
        from types import SimpleNamespace
        profile = SimpleNamespace(negative_clusters=lambda min_strength_pct=None: (CLUSTER,))
        cfg = SimpleNamespace(accel_min_pct=20.0)
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        assert s._brandon_accel_cluster_for(profile, 7900.0, cfg) is None

    def test_a_raising_profile_returns_none_not_an_exception(self):
        from types import SimpleNamespace
        def boom(**k):
            raise RuntimeError("polygon down")
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        got = s._brandon_accel_cluster_for(
            SimpleNamespace(negative_clusters=boom), 7620.0,
            SimpleNamespace(accel_min_pct=20.0))
        assert got is None
