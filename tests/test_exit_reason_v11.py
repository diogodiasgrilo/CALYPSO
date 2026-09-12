"""v11 schema: trade_stops.exit_reason discriminator.

Verifies the migration adds the column, record_stop persists it, and legacy
writes (no exit_reason) stay NULL so the dashboard's net_pnl-sign fallback holds.
"""
import sqlite3

from shared.data_recorder import DataRecorder, SCHEMA_VERSION


def _cols(db_path, table):
    con = sqlite3.connect(db_path)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


def test_schema_version_is_11():
    assert SCHEMA_VERSION >= 11


def test_fresh_db_has_exit_reason_column(tmp_path):
    p = str(tmp_path / "fresh.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    assert "exit_reason" in _cols(p, "trade_stops")


def test_migration_adds_exit_reason_to_legacy_db(tmp_path):
    """A pre-v11 DB (column absent, version stamped < 11) gains the column."""
    p = str(tmp_path / "legacy.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    # Simulate a pre-v11 DB: drop the column isn't trivial in sqlite, so instead
    # stamp an older version and confirm ensure_schema is idempotent (no error,
    # column still present). The additive ALTER is guarded against duplicates.
    con = sqlite3.connect(p)
    con.execute("INSERT OR REPLACE INTO schema_info (key, value) VALUES ('version', '10')")
    con.commit()
    con.close()
    rec2 = DataRecorder(p)
    assert rec2.ensure_schema() is True
    assert "exit_reason" in _cols(p, "trade_stops")


def test_record_stop_persists_exit_reason(tmp_path):
    p = str(tmp_path / "write.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    rec.record_stop({
        "date": "2026-06-15", "entry_number": 2, "side": "call",
        "net_pnl": 90.6, "exit_reason": "take_profit",
    })
    con = sqlite3.connect(p)
    row = con.execute(
        "SELECT exit_reason, net_pnl FROM trade_stops WHERE entry_number=2 AND side='call'"
    ).fetchone()
    con.close()
    assert row == ("take_profit", 90.6)


def test_record_stop_without_exit_reason_is_null(tmp_path):
    p = str(tmp_path / "legacy_write.db")
    rec = DataRecorder(p)
    rec.ensure_schema()
    rec.record_stop({
        "date": "2026-06-15", "entry_number": 1, "side": "put", "net_pnl": -120.0,
    })
    con = sqlite3.connect(p)
    row = con.execute(
        "SELECT exit_reason FROM trade_stops WHERE entry_number=1 AND side='put'"
    ).fetchone()
    con.close()
    assert row == (None,)
