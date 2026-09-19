"""The shadow analyzer must measure against what the gate DID, not against a replay.

`scripts/analyze_gex_shadow.py` is the tool the project intends to use to settle
the deferred GEX decisions (sign convention, windowed normalization, the abort
rate). Until 2026-09-19 it compared every corrected variant against the `live`
entry inside `shadow_json` — and that entry is NOT a faithful replay of the live
gate. It is pure single-profile geometry (`adjuster_predicate = adj_cluster is
not None`, `brandon/gex_shadow.py`), while the real adjuster ALSO requires peak
persistence against a `prior_profile` (`brandon/defensive_overlay.py`). The
replay therefore over-confirms, always in the same direction.

Measured on variant B's 82 adjuster decisions: the replay claimed a confirmation
on 11 call decisions where the gate recorded 9 and kept the strike on the other
two, so every "would-stand-down-where-live-fired" figure was inflated against a
baseline containing confirmations that never happened. A one-directional bias in
the numbers feeding a deferred decision is not cosmetic, which is why these tests
exist.

The second fix: section 5 read only the `cluster_*` COLUMNS, which are NULL on
every row ever recorded, so it printed "(none)" four times and the script's own
question — "are we vetoing on tail artifacts or real localized walls?" — could
not be answered. The same cluster is inside `shadow_json`; it is now read from
there when the columns are empty.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.analyze_gex_shadow import analyze  # noqa: E402


def _shadow(live_adj: bool, fixed_adj: bool, cluster: dict | None = None) -> str:
    """A shadow_json payload: the replay plus one corrected variant."""
    return json.dumps([
        {"variant": "live", "adjuster_predicate": live_adj,
         "overlay_predicate": False, "n_zones": 1, "cluster": cluster},
        {"variant": "all_fixes", "adjuster_predicate": fixed_adj,
         "overlay_predicate": False, "n_zones": 1, "cluster": cluster},
    ])


def _db(tmp_path, rows) -> str:
    """Build a minimal gex_decisions DB. rows: (recorded_pred, action, shadow_json)."""
    p = tmp_path / "gex.db"
    con = sqlite3.connect(p)
    con.execute("""CREATE TABLE gex_decisions (
        timestamp TEXT, date TEXT, variant TEXT, consumer TEXT, entry_number INTEGER,
        side TEXT, spot REAL, reference_strike REAL, live_action TEXT,
        live_adjuster_predicate INTEGER, live_overlay_predicate INTEGER,
        cluster_low REAL, cluster_high REAL, cluster_peak REAL,
        cluster_n_strikes INTEGER, cluster_strength_pct REAL,
        shadow_json TEXT, shadow_disagrees INTEGER)""")
    con.execute("CREATE TABLE gex_profile_snapshots (timestamp TEXT, date TEXT)")
    for i, (recorded, action, sj) in enumerate(rows):
        con.execute(
            "INSERT INTO gex_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"2026-09-15 10:{i:02d}:00", "2026-09-15", "b", "adjuster", i + 1,
             "call", 7600.0, 7650.0, action, int(recorded), 0,
             None, None, None, None, None, sj, 0),
        )
    con.commit()
    con.close()
    return str(p)


class TestTheBaselineIsWhatTheGateDid:
    def test_a_replay_only_confirmation_is_not_counted_as_a_stand_down(self, tmp_path, capsys):
        """THE BUG. The gate did NOT confirm (recorded False, strike KEPT), but the
        replay claims it did. A corrected variant that also says False agrees with
        reality perfectly and must not be scored as "would stand down where live
        fired" — live never fired."""
        db = _db(tmp_path, [(False, "KEEP", _shadow(live_adj=True, fixed_adj=False))])
        assert analyze(db) == 0
        out = capsys.readouterr().out
        assert "-0 would-stand-down-where-live-fired" in out, out
        assert "-1 would-stand-down-where-live-fired" not in out

    def test_a_real_confirmation_IS_counted(self, tmp_path, capsys):
        """Control: when the gate genuinely fired and the corrected variant would
        not have, that IS a stand-down. Without this the fix above could be
        'count nothing' and still pass."""
        db = _db(tmp_path, [(True, "SKIP", _shadow(live_adj=True, fixed_adj=False))])
        assert analyze(db) == 0
        assert "-1 would-stand-down-where-live-fired" in capsys.readouterr().out

    def test_a_variant_confirming_where_the_gate_did_not_is_counted(self, tmp_path, capsys):
        db = _db(tmp_path, [(False, "KEEP", _shadow(live_adj=False, fixed_adj=True))])
        assert analyze(db) == 0
        assert "+1 would-confirm-where-live-did-not" in capsys.readouterr().out


class TestReplayFidelityIsReported:
    def test_divergence_is_surfaced_not_silently_absorbed(self, tmp_path, capsys):
        """A replay that does not reproduce the gate means every other variant is
        measured with a wrong yardstick, so it must be visible, not swallowed."""
        db = _db(tmp_path, [(False, "KEEP", _shadow(live_adj=True, fixed_adj=True))])
        analyze(db)
        out = capsys.readouterr().out
        assert "replay fidelity" in out
        assert "DIVERGE" in out
        assert "peak-persistence" in out

    def test_a_faithful_replay_reports_OK(self, tmp_path, capsys):
        """No-op control: identical replay and record must not raise a warning."""
        db = _db(tmp_path, [(True, "SKIP", _shadow(live_adj=True, fixed_adj=True))])
        analyze(db)
        out = capsys.readouterr().out
        assert "replay fidelity" in out
        assert "the replay matched the recorded decision on every row" in out
        # "rows DIVERGE", not bare "DIVERGE" — section 4's heading is
        # "ADJUSTER vs OVERLAY PREDICATE DIVERGENCE" and always prints.
        assert "rows DIVERGE" not in out


class TestClusterShapeFallsBackToShadowJson:
    CLUSTER = {"low": 7500.0, "high": 7835.0, "peak": 7660.0,
               "n_strikes": 59, "strength_pct": 0.1718}

    def test_it_reads_the_cluster_when_the_columns_are_null(self, tmp_path, capsys):
        """Every row ever recorded has NULL cluster_* columns, so reading only
        those made section 5 print '(none)' and left the tail-artifact question
        unanswerable — while the data sat in shadow_json all along."""
        db = _db(tmp_path, [(True, "SKIP", _shadow(True, True, self.CLUSTER))])
        analyze(db)
        out = capsys.readouterr().out
        assert "read from shadow_json" in out
        assert "cluster width (pts)   : n=1" in out
        assert "median=335.0" in out          # 7835 - 7500
        assert "cluster n_strikes     : n=1" in out

    def test_a_row_with_no_cluster_anywhere_is_skipped_not_crashed(self, tmp_path, capsys):
        """Negative control: KEEP rows carry cluster: null. They must be ignored
        rather than counted as a zero-width cluster, which would drag the median."""
        db = _db(tmp_path, [(False, "KEEP", _shadow(False, False, None))])
        assert analyze(db) == 0
        out = capsys.readouterr().out
        assert "cluster width (pts)   : (none)" in out
        assert "read from shadow_json" not in out

    def test_the_columns_win_when_they_are_populated(self, tmp_path, capsys):
        """The fallback must not override real column data if it ever lands."""
        db = _db(tmp_path, [(True, "SKIP", _shadow(True, True, self.CLUSTER))])
        con = sqlite3.connect(db)
        con.execute("UPDATE gex_decisions SET cluster_low=7640, cluster_high=7660, "
                    "cluster_peak=7650, cluster_n_strikes=5, cluster_strength_pct=0.5")
        con.commit()
        con.close()
        analyze(db)
        out = capsys.readouterr().out
        assert "read from shadow_json" not in out
        assert "median=20.0" in out           # 7660 - 7640, the COLUMN value


class TestItStillRunsOnRealShapedInput:
    def test_empty_db_is_not_an_error(self, tmp_path, capsys):
        db = _db(tmp_path, [])
        assert analyze(db) == 0
        assert "No gex_decisions rows yet" in capsys.readouterr().out

    def test_unparseable_shadow_json_does_not_crash(self, tmp_path, capsys):
        db = _db(tmp_path, [(True, "SKIP", "{not json")])
        assert analyze(db) == 0
