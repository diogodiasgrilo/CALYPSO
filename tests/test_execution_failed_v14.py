"""v14 schema: skipped_entries.execution_failed discriminator.

Verifies the migration adds the column (fresh + legacy DB), record_skipped_entry
persists it correctly, and legacy skip writes (no execution_failed key) default
to 0 (skip) so historical rows keep their original meaning.

Background: before this, a genuine order-EXECUTION FAILURE (broker accepted the
order but it never filled after exhausting retries) was recorded identically to
a deliberate strategic SKIP — or, before the accompanying strategy.py fix, not
recorded at all (see tests/test_entry_execution_failure.py for that half of the
fix). This column lets the dashboard/DB tell the two apart.
"""
import sqlite3

from shared.data_recorder import DataRecorder, SCHEMA_VERSION


def _cols(db_path, table):
    con = sqlite3.connect(db_path)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


def test_schema_version_is_14():
    assert SCHEMA_VERSION >= 14


def test_fresh_db_has_execution_failed_column(tmp_path):
    p = str(tmp_path / "fresh.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    assert "execution_failed" in _cols(p, "skipped_entries")


def test_migration_adds_execution_failed_to_legacy_db(tmp_path):
    """A pre-v14 DB (column absent, version stamped < 14) gains the column,
    and re-running the migration is idempotent (no error on the second run —
    the duplicate-column ALTER is swallowed by the existing error handler)."""
    p = str(tmp_path / "legacy.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    con = sqlite3.connect(p)
    con.execute("INSERT OR REPLACE INTO schema_info (key, value) VALUES ('version', '13')")
    con.commit()
    con.close()
    rec2 = DataRecorder(p)
    assert rec2.ensure_schema() is True
    assert "execution_failed" in _cols(p, "skipped_entries")

    # Idempotent: running ensure_schema again on the now-current DB must not error.
    rec3 = DataRecorder(p)
    assert rec3.ensure_schema() is True
    assert "execution_failed" in _cols(p, "skipped_entries")


def test_record_skipped_entry_persists_execution_failed_true(tmp_path):
    p = str(tmp_path / "write.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    rec.record_skipped_entry({
        "date": "2026-07-31", "entry_number": 3,
        "skip_reason": "Execution failed after 3 attempts: Entry execution failed",
        "execution_failed": 1,
    })
    con = sqlite3.connect(p)
    row = con.execute(
        "SELECT execution_failed, skip_reason FROM skipped_entries "
        "WHERE date='2026-07-31' AND entry_number=3"
    ).fetchone()
    con.close()
    assert row == (1, "Execution failed after 3 attempts: Entry execution failed")


def test_record_skipped_entry_without_execution_failed_defaults_to_zero(tmp_path):
    """Legacy call sites (the 15 pre-existing _record_skipped_entry callers)
    never pass this key — must not raise, and must default to 0 (a real skip,
    not a failure), matching the column's NOT NULL DEFAULT 0."""
    p = str(tmp_path / "legacy_write.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    rec.record_skipped_entry({
        "date": "2026-07-31", "entry_number": 1,
        "skip_reason": "MKT-011: both sides below minimum credit",
    })
    con = sqlite3.connect(p)
    row = con.execute(
        "SELECT execution_failed FROM skipped_entries WHERE date='2026-07-31' AND entry_number=1"
    ).fetchone()
    con.close()
    assert row == (0,)


def test_record_skipped_entry_explicit_zero_stays_zero(tmp_path):
    """Explicit 0 (not just an absent key) must not be coerced to something
    else by the null-safe `or 0` pattern (0 or 0 == 0, not a bug — but worth
    pinning since `or` defaulting is a classic footgun for falsy-but-valid 0)."""
    p = str(tmp_path / "explicit_zero.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    rec.record_skipped_entry({
        "date": "2026-07-31", "entry_number": 2,
        "skip_reason": "whipsaw skip", "execution_failed": 0,
    })
    con = sqlite3.connect(p)
    row = con.execute(
        "SELECT execution_failed FROM skipped_entries WHERE date='2026-07-31' AND entry_number=2"
    ).fetchone()
    con.close()
    assert row == (0,)
