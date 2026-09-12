"""v15 schema: skipped_entries hydration/delta telemetry for the Brandon
delta-target degraded-data guard.

Verifies the migration adds the four columns (fresh + legacy DB),
record_skipped_entry persists them correctly, and every other skip reason
(the overwhelming majority of rows) writes NULL — this is pure observability,
zero trading-behavior change (see MIGRATION_V15_SQL for the full rationale).
"""
import sqlite3

from shared.data_recorder import DataRecorder, SCHEMA_VERSION

NEW_COLS = ["hydration_pct", "achieved_delta", "target_delta", "delta_floor"]


def _cols(db_path, table):
    con = sqlite3.connect(db_path)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


def test_schema_version_is_15():
    assert SCHEMA_VERSION >= 15


def test_fresh_db_has_telemetry_columns(tmp_path):
    p = str(tmp_path / "fresh.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    cols = _cols(p, "skipped_entries")
    for c in NEW_COLS:
        assert c in cols


def test_migration_adds_columns_to_legacy_db(tmp_path):
    """A pre-v15 DB (columns absent, version stamped 14) gains all four
    columns, and re-running the migration is idempotent."""
    p = str(tmp_path / "legacy.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    con = sqlite3.connect(p)
    con.execute("INSERT OR REPLACE INTO schema_info (key, value) VALUES ('version', '14')")
    con.commit()
    con.close()

    rec2 = DataRecorder(p)
    assert rec2.ensure_schema() is True
    cols = _cols(p, "skipped_entries")
    for c in NEW_COLS:
        assert c in cols

    # Idempotent: running again on the now-current DB must not error.
    rec3 = DataRecorder(p)
    assert rec3.ensure_schema() is True
    assert all(c in _cols(p, "skipped_entries") for c in NEW_COLS)


def test_record_skipped_entry_persists_degraded_data_telemetry(tmp_path):
    p = str(tmp_path / "write.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    rec.record_skipped_entry({
        "date": "2026-08-03", "entry_number": 4,
        "skip_reason": "delta-target put short 7500 is 3.5δ < 4.0δ floor (target 8.0δ)",
        "hydration_pct": 16.26,
        "achieved_delta": 0.035,
        "target_delta": 0.08,
        "delta_floor": 0.04,
    })
    con = sqlite3.connect(p)
    row = con.execute(
        "SELECT hydration_pct, achieved_delta, target_delta, delta_floor "
        "FROM skipped_entries WHERE date='2026-08-03' AND entry_number=4"
    ).fetchone()
    con.close()
    assert row == (16.26, 0.035, 0.08, 0.04)


def test_ordinary_skip_leaves_telemetry_null(tmp_path):
    """The overwhelming majority of skips (credit gate, GEX accel-zone,
    require-both-sides, ...) never pass these keys — must default to NULL,
    not error, not coerce to 0 (0.0 is a valid achieved_delta, NULL means
    'not applicable')."""
    p = str(tmp_path / "ordinary_skip.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    rec.record_skipped_entry({
        "date": "2026-08-03", "entry_number": 1,
        "skip_reason": "Not enough premium — credit gate (MKT-011)",
    })
    con = sqlite3.connect(p)
    row = con.execute(
        "SELECT hydration_pct, achieved_delta, target_delta, delta_floor "
        "FROM skipped_entries WHERE date='2026-08-03' AND entry_number=1"
    ).fetchone()
    con.close()
    assert row == (None, None, None, None)


def test_achieved_delta_zero_is_not_coerced_to_null(tmp_path):
    """0.0 is a legitimate (if extreme) achieved_delta — must round-trip as
    0.0, not be treated as falsy-and-defaulted the way execution_failed's
    `or 0` pattern intentionally does for its own NOT NULL column."""
    p = str(tmp_path / "zero_delta.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    rec.record_skipped_entry({
        "date": "2026-08-03", "entry_number": 5,
        "skip_reason": "delta-target put short is 0.0δ < floor",
        "hydration_pct": 12.0, "achieved_delta": 0.0,
        "target_delta": 0.08, "delta_floor": 0.04,
    })
    con = sqlite3.connect(p)
    row = con.execute(
        "SELECT achieved_delta FROM skipped_entries WHERE date='2026-08-03' AND entry_number=5"
    ).fetchone()
    con.close()
    assert row == (0.0,)
