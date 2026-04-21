"""Targeted test: DataRecorder on a FRESH DB (no schema_info, no tables at all).

Scenario the audit flagged: if shadow_entries CREATE is gated on current_version < 7,
and v8 ALTER assumes the table exists, a v7→v8 migration on a DB missing shadow_entries
would silently fail. We unconditionally CREATE shadow_entries now; this test verifies.

Runs the DataRecorder ensure_schema end-to-end on:
  1. Fresh DB (current_version=0) → must reach v8 with all contracts columns
  2. V7 DB without shadow_entries table present (pathological) → still succeeds
  3. V7 DB WITH shadow_entries (normal v7→v8 case) → still succeeds
"""
from __future__ import annotations
import os, sqlite3, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _v8_columns_present(path: str) -> dict:
    conn = sqlite3.connect(path)
    result = {}
    for table in ("trade_entries", "trade_stops", "spread_snapshots", "shadow_entries"):
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            result[table] = "contracts" in cols
        except sqlite3.OperationalError:
            result[table] = False
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_summaries)")}
        result["daily_summaries.contracts_per_entry"] = "contracts_per_entry" in cols
    except sqlite3.OperationalError:
        result["daily_summaries.contracts_per_entry"] = False
    ver_row = conn.execute("SELECT value FROM schema_info WHERE key='version'").fetchone()
    result["version"] = ver_row[0] if ver_row else None
    conn.close()
    return result


def test_fresh_db():
    print("\n[Test F-1] Fresh DB (current_version=0) → v8")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    os.unlink(path)  # we want the file NOT to exist
    try:
        from shared.data_recorder import DataRecorder
        rec = DataRecorder(path)
        rec.ensure_schema()
        cols = _v8_columns_present(path)
        print(f"  result: {cols}")
        assert cols["version"] == "8", f"version should be 8, got {cols['version']}"
        for k in ("trade_entries", "trade_stops", "spread_snapshots", "shadow_entries",
                 "daily_summaries.contracts_per_entry"):
            assert cols[k], f"{k} missing contracts column"
        print("  ✓ fresh DB reaches v8 with all contracts columns present")
    finally:
        for ext in ("", "-wal", "-shm"):
            p = path + ext
            if os.path.exists(p):
                os.unlink(p)


def test_v7_without_shadow_entries():
    """Pathological: DB stamped at v7 but shadow_entries doesn't exist."""
    print("\n[Test F-2] V7 DB pathologically missing shadow_entries → recoverable")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE trade_entries (date TEXT, entry_number INTEGER, PRIMARY KEY (date, entry_number));
            CREATE TABLE trade_stops (date TEXT, entry_number INTEGER, side TEXT, PRIMARY KEY (date, entry_number, side));
            CREATE TABLE spread_snapshots (timestamp TEXT, entry_number INTEGER, PRIMARY KEY (timestamp, entry_number));
            CREATE TABLE daily_summaries (date TEXT PRIMARY KEY);
            CREATE TABLE skipped_entries (date TEXT, entry_number INTEGER, PRIMARY KEY (date, entry_number));
            CREATE TABLE entry_mae_mfe (date TEXT, entry_number INTEGER, side TEXT, PRIMARY KEY (date, entry_number, side));
            -- Intentionally no shadow_entries
            INSERT INTO schema_info VALUES ('version', '7');
        """)
        conn.commit()
        conn.close()

        from shared.data_recorder import DataRecorder
        rec = DataRecorder(path)
        rec.ensure_schema()

        cols = _v8_columns_present(path)
        print(f"  result: {cols}")
        assert cols["version"] == "8"
        # All four v8 targets should have contracts now
        for k in ("trade_entries", "trade_stops", "spread_snapshots", "shadow_entries",
                 "daily_summaries.contracts_per_entry"):
            assert cols[k], f"{k} missing contracts column"
        print("  ✓ even with missing shadow_entries, v8 migration recovered (creates then ALTERs)")
    finally:
        for ext in ("", "-wal", "-shm"):
            p = path + ext
            if os.path.exists(p):
                os.unlink(p)


def test_v7_with_shadow_entries():
    """Normal case: V7 DB with shadow_entries already present."""
    print("\n[Test F-3] Normal V7 → V8 with shadow_entries present")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE trade_entries (date TEXT, entry_number INTEGER, PRIMARY KEY (date, entry_number));
            CREATE TABLE trade_stops (date TEXT, entry_number INTEGER, side TEXT, PRIMARY KEY (date, entry_number, side));
            CREATE TABLE spread_snapshots (timestamp TEXT, entry_number INTEGER, PRIMARY KEY (timestamp, entry_number));
            CREATE TABLE daily_summaries (date TEXT PRIMARY KEY);
            CREATE TABLE skipped_entries (date TEXT, entry_number INTEGER, PRIMARY KEY (date, entry_number));
            CREATE TABLE entry_mae_mfe (date TEXT, entry_number INTEGER, side TEXT, PRIMARY KEY (date, entry_number, side));
            CREATE TABLE shadow_entries (date TEXT, entry_number INTEGER, is_skipped INTEGER DEFAULT 0, PRIMARY KEY (date, entry_number));
            INSERT INTO schema_info VALUES ('version', '7');
            -- Seed a shadow row to verify preservation
            INSERT INTO shadow_entries VALUES ('2026-04-20', 1, 0);
        """)
        conn.commit()
        conn.close()

        from shared.data_recorder import DataRecorder
        rec = DataRecorder(path)
        rec.ensure_schema()

        cols = _v8_columns_present(path)
        assert cols["version"] == "8"
        for k in ("trade_entries", "trade_stops", "spread_snapshots", "shadow_entries",
                 "daily_summaries.contracts_per_entry"):
            assert cols[k], f"{k} missing contracts column"

        # Verify existing shadow row survived and got contracts=1 default
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT contracts FROM shadow_entries WHERE date='2026-04-20'").fetchone()
        assert row and row[0] == 1
        conn.close()
        print("  ✓ v7→v8 preserves existing shadow rows, adds contracts=1 default")
    finally:
        for ext in ("", "-wal", "-shm"):
            p = path + ext
            if os.path.exists(p):
                os.unlink(p)


def test_idempotent_reentry():
    """ensure_schema called twice in a row should no-op the second time."""
    print("\n[Test F-4] ensure_schema idempotent across two calls")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    os.unlink(path)
    try:
        from shared.data_recorder import DataRecorder
        rec = DataRecorder(path)
        rec.ensure_schema()
        rec.ensure_schema()  # second call
        # No exception means we're good
        cols = _v8_columns_present(path)
        assert cols["version"] == "8"
        print("  ✓ two sequential ensure_schema calls = no error")
    finally:
        for ext in ("", "-wal", "-shm"):
            p = path + ext
            if os.path.exists(p):
                os.unlink(p)


def main():
    tests = [test_fresh_db, test_v7_without_shadow_entries, test_v7_with_shadow_entries,
             test_idempotent_reentry]
    fails = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ FAIL: {e}")
            fails += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            fails += 1
    print("\n" + "=" * 60)
    if fails == 0:
        print(f"ALL {len(tests)} FRESH-DB TESTS PASSED")
        return 0
    print(f"{fails} / {len(tests)} tests FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
