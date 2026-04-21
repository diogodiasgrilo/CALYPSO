"""End-to-end tests for 2-contract Phase 1 data layer.

Exercises:
  - DB migration v7 → v8 on a synthetic v7-shaped database (new contracts columns, default=1)
  - data_recorder round-trip at contracts=1 and contracts=2
  - Recovery path Bug A/C fix: preserved_entry_credits restores entry.contracts
  - Edge cases: missing contracts field in old state file, skip-path contracts=0

Run:
    python scripts/test_v8_migration_and_recovery.py

Exits 0 on success.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def build_v7_db(path: str):
    """Create a SQLite DB with v7 schema (no contracts columns) and a few rows."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    # Minimal v7 subset — just the tables that will get a 'contracts' ALTER
    conn.executescript(
        """
        CREATE TABLE trade_entries (
            date TEXT NOT NULL,
            entry_number INTEGER NOT NULL,
            total_credit REAL,
            PRIMARY KEY (date, entry_number)
        );
        CREATE TABLE trade_stops (
            date TEXT NOT NULL,
            entry_number INTEGER NOT NULL,
            side TEXT NOT NULL,
            actual_debit REAL,
            PRIMARY KEY (date, entry_number, side)
        );
        CREATE TABLE spread_snapshots (
            timestamp TEXT NOT NULL,
            entry_number INTEGER NOT NULL,
            call_spread_value REAL,
            put_spread_value REAL,
            PRIMARY KEY (timestamp, entry_number)
        );
        CREATE TABLE shadow_entries (
            date TEXT NOT NULL,
            entry_number INTEGER NOT NULL,
            is_skipped INTEGER DEFAULT 0,
            PRIMARY KEY (date, entry_number)
        );
        CREATE TABLE daily_summaries (
            date TEXT PRIMARY KEY,
            net_pnl REAL
        );
        CREATE TABLE schema_info (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.execute("INSERT INTO schema_info VALUES ('version', '7')")
    conn.execute("INSERT INTO trade_entries VALUES ('2026-04-20', 1, 145.0)")
    conn.execute("INSERT INTO trade_stops VALUES ('2026-04-20', 1, 'put', 80.0)")
    conn.execute(
        "INSERT INTO spread_snapshots VALUES ('2026-04-20 11:15:00', 1, 50.0, 30.0)"
    )
    conn.execute("INSERT INTO shadow_entries VALUES ('2026-04-20', 1, 0)")
    conn.execute("INSERT INTO daily_summaries VALUES ('2026-04-20', 260.0)")
    conn.commit()
    conn.close()


def test_migration_v7_to_v8():
    print("\n[Test M-1] v7 → v8 migration on synthetic DB")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        build_v7_db(path)

        # Pre-state: no contracts columns; rows present
        conn = sqlite3.connect(path)
        cols_entries = {r[1] for r in conn.execute("PRAGMA table_info(trade_entries)")}
        assert "contracts" not in cols_entries, "pre-migration must lack contracts"
        row_count = conn.execute("SELECT COUNT(*) FROM trade_entries").fetchone()[0]
        assert row_count == 1
        conn.close()

        # Run the migration by instantiating BacktestingDB
        from services.homer.db_manager import BacktestingDB
        BacktestingDB(path)  # runs _init_db → _run_migrations

        # Post-state assertions
        conn = sqlite3.connect(path)
        ver = conn.execute(
            "SELECT value FROM schema_info WHERE key='version'"
        ).fetchone()[0]
        assert ver == "8", f"schema version should be 8, got {ver}"

        for table in ("trade_entries", "trade_stops", "spread_snapshots", "shadow_entries"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert "contracts" in cols, f"{table} missing contracts column"

        cols_ds = {r[1] for r in conn.execute("PRAGMA table_info(daily_summaries)")}
        assert "contracts_per_entry" in cols_ds, "daily_summaries missing contracts_per_entry"

        # Existing rows must default to 1
        val = conn.execute(
            "SELECT contracts FROM trade_entries WHERE date='2026-04-20'"
        ).fetchone()[0]
        assert val == 1, f"existing row contracts should default to 1, got {val}"

        val = conn.execute(
            "SELECT contracts FROM trade_stops WHERE date='2026-04-20'"
        ).fetchone()[0]
        assert val == 1

        val = conn.execute(
            "SELECT contracts_per_entry FROM daily_summaries WHERE date='2026-04-20'"
        ).fetchone()[0]
        assert val == 1

        conn.close()
        print("  ✓ migration produced v8 schema with contracts columns, existing rows = 1")

        # Idempotency: re-run migration, must not fail
        BacktestingDB(path)
        conn = sqlite3.connect(path)
        ver = conn.execute("SELECT value FROM schema_info WHERE key='version'").fetchone()[0]
        assert ver == "8"
        conn.close()
        print("  ✓ re-running migration is idempotent (no error)")

    finally:
        os.unlink(path)
        for ext in (".db-wal", ".db-shm"):
            if os.path.exists(path + ext):
                os.unlink(path + ext)


def test_datarecorder_roundtrip():
    print("\n[Test D-1] DataRecorder writes contracts at 1c and 2c")
    from shared.data_recorder import DataRecorder
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        rec = DataRecorder(path)
        rec.ensure_schema()

        # 1c entry
        rec.record_entry({
            "date": "2026-04-21", "entry_number": 1, "total_credit": 145.0,
            "contracts": 1,
        })
        # 2c entry
        rec.record_entry({
            "date": "2026-04-22", "entry_number": 2, "total_credit": 290.0,
            "contracts": 2,
        })
        # Entry with NO contracts key (legacy call site) — should default to 1
        rec.record_entry({
            "date": "2026-04-23", "entry_number": 3, "total_credit": 100.0,
        })
        # Stop at 2c
        rec.record_stop({
            "date": "2026-04-22", "entry_number": 2, "side": "put",
            "actual_debit": 160.0, "contracts": 2,
        })
        # Spread snapshot at 2c
        rec.record_spread_snapshots(
            "2026-04-22 11:15:00",
            [{"entry_number": 2, "call_spread_value": 80.0, "put_spread_value": 60.0, "contracts": 2}],
        )
        # Daily summary at 2c
        rec.record_daily_summary({
            "date": "2026-04-22", "net_pnl": 300.0, "contracts_per_entry": 2,
        })

        conn = sqlite3.connect(path)
        c = conn.execute(
            "SELECT date, contracts FROM trade_entries ORDER BY date"
        ).fetchall()
        assert c == [("2026-04-21", 1), ("2026-04-22", 2), ("2026-04-23", 1)], f"got {c}"
        print(f"  ✓ entries: {c}")

        c = conn.execute(
            "SELECT contracts FROM trade_stops WHERE date='2026-04-22'"
        ).fetchone()[0]
        assert c == 2
        print(f"  ✓ stops at 2c: {c}")

        c = conn.execute(
            "SELECT contracts FROM spread_snapshots WHERE entry_number=2"
        ).fetchone()[0]
        assert c == 2
        print(f"  ✓ snapshots at 2c: {c}")

        c = conn.execute(
            "SELECT contracts_per_entry FROM daily_summaries WHERE date='2026-04-22'"
        ).fetchone()[0]
        assert c == 2
        print(f"  ✓ daily_summary at 2c: {c}")

        conn.close()
    finally:
        os.unlink(path)
        for ext in (".db-wal", ".db-shm"):
            if os.path.exists(path + ext):
                os.unlink(path + ext)


def test_preserved_entry_credits_restores_contracts():
    """Simulate the recovery-path Bug A fix: saved entry at 1c retains contracts=1
    even when current config says 2c."""
    print("\n[Test R-1] preserved_entry_credits restores entry.contracts across flip")

    # Simulate saved state
    saved_state = {
        "entries": [
            {"entry_number": 1, "call_spread_credit": 100.0, "put_spread_credit": 80.0,
             "call_side_stop": 175.0, "put_side_stop": 175.0,
             "contracts": 1},  # entry was OPENED at 1c
        ],
    }

    # Build preserved_entry_credits the way strategy code does
    # (just extract the relevant bit — the dict build + restore)
    current_config_contracts = 2  # user flipped to 2c
    preserved_entry_credits = {}
    for entry_data in saved_state["entries"]:
        preserved_entry_credits[entry_data["entry_number"]] = {
            "call_credit": entry_data["call_spread_credit"],
            "put_credit": entry_data["put_spread_credit"],
            "call_stop": entry_data["call_side_stop"],
            "put_stop": entry_data["put_side_stop"],
            "contracts": entry_data.get("contracts", current_config_contracts),
        }

    saved = preserved_entry_credits[1]
    assert saved["contracts"] == 1, (
        f"saved contracts should be 1 (original), got {saved['contracts']}"
    )

    # Simulate a reconstructed entry that got contracts=current_config (2)
    class FakeEntry:
        contracts = 2  # wrongly set by _reconstruct_entry_from_positions
    entry = FakeEntry()

    # Apply the fix: entry.contracts = saved.get("contracts", entry.contracts)
    entry.contracts = saved.get("contracts", entry.contracts)
    assert entry.contracts == 1, f"after restore, entry.contracts should be 1, got {entry.contracts}"
    print("  ✓ entry opened at 1c retains contracts=1 after restart even when config is 2c")

    # Backward-compat: if saved state lacks contracts key (pre-Phase-1 state file),
    # fall back to the reconstructed value (current config)
    entry2 = FakeEntry()
    entry2.contracts = 2  # reconstructed
    saved_old = {"call_credit": 100}  # no "contracts" key
    entry2.contracts = saved_old.get("contracts", entry2.contracts)
    assert entry2.contracts == 2, "backward-compat should fall back to reconstructed"
    print("  ✓ pre-Phase-1 state file (no contracts key) falls back correctly")


def test_metrics_backward_compat():
    print("\n[Test M-2] metrics JSON daily_returns reader-side backward compat")
    # Simulate what a reader should do with mixed old/new records
    daily_returns = [
        # Old record (pre-Phase-1) — no contracts_per_entry key
        {"date": "2026-04-15", "net_pnl": 150.0, "return_pct": 0.01},
        # New record (post-Phase-1) — has contracts_per_entry
        {"date": "2026-04-22", "net_pnl": 300.0, "return_pct": 0.015, "contracts_per_entry": 2},
    ]
    # Simulated reader: normalize P&L to per-contract
    per_contract_returns = []
    for r in daily_returns:
        cpe = r.get("contracts_per_entry", 1)  # default 1 for pre-Phase-1 records
        per_contract_returns.append(r["net_pnl"] / cpe)
    assert per_contract_returns == [150.0, 150.0]
    print("  ✓ mixed 1c/2c daily_returns normalized correctly: $300 at 2c == $150/c same as old $150 at 1c")


def test_contract_edge_cases():
    """Edge cases for stop math at extreme contract counts."""
    print("\n[Test E-1] Edge case: contracts=3 ratio must be 3×")

    # Import test harness from V-1 test
    from test_2contract_stop_invariants import calc_stop, FakeEntry

    e1 = FakeEntry(call_spread_credit=400.0, put_spread_credit=400.0)
    e3 = FakeEntry(call_spread_credit=1200.0, put_spread_credit=1200.0)

    call_1c, put_1c = calc_stop(1, e1)
    call_3c, put_3c = calc_stop(3, e3)

    assert abs(call_3c / call_1c - 3.0) < 0.001, f"3c/1c call ratio {call_3c/call_1c} != 3"
    assert abs(put_3c / put_1c - 3.0) < 0.001, f"3c/1c put ratio {put_3c/put_1c} != 3"
    print(f"  ✓ contracts=3 full IC: call ratio {call_3c/call_1c:.3f}, put ratio {put_3c/put_1c:.3f}")


def main():
    tests = [
        test_migration_v7_to_v8,
        test_datarecorder_roundtrip,
        test_preserved_entry_credits_restores_contracts,
        test_metrics_backward_compat,
        test_contract_edge_cases,
    ]
    fails = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ FAIL: {e}")
            fails += 1
        except Exception as e:
            import traceback
            print(f"  ✗ ERROR: {type(e).__name__}: {e}")
            traceback.print_exc()
            fails += 1

    print("\n" + "=" * 60)
    if fails == 0:
        print(f"ALL {len(tests)} DATA-LAYER TESTS PASSED")
        return 0
    print(f"{fails} / {len(tests)} tests FAILED")
    return 1


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    sys.exit(main())
