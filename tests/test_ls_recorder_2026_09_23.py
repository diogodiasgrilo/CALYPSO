"""Variant H's isolated recorder — rows round-trip, and backtesting.db is untouched.

Step 7's exit criterion is "schema + record methods round-trip in tests; shared
`backtesting.db` untouched."

The reason this DB exists at all is worth restating, because it is the thing
these tests protect: `trade_entries` records what was COLLECTED and has no column
for a debit. Writing variant H there would persist a strangle that cost nothing,
and every shared consumer would read that zero as fact. So the tests below check
two different properties — that H's own rows are correct, and that H cannot
reach the shared schema at all.

The recorder is also fire-and-forget by design: a recording failure must cost a
row, never a position. Several tests below assert that it stays silent rather
than raising, which is the behaviour that keeps a full disk from becoming a
trading incident.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.long_strangle_entry import LongStrangleEntry  # noqa: E402
from bots.hydra.ls_recorder import LongStrangleDataRecorder  # noqa: E402


@pytest.fixture
def rec(tmp_path):
    r = LongStrangleDataRecorder(str(tmp_path / "long_strangle.db"))
    yield r
    r.close()


def _entry(call_px=2.00, put_px=1.50):
    e = LongStrangleEntry(entry_number=1)
    e.contracts = 7
    e.long_call_strike, e.long_put_strike = 7825.0, 7725.0
    e.long_call_price, e.long_put_price = call_px, put_px
    e.call_debit, e.put_debit = 1400.0, 1050.0
    e.long_call_uic, e.long_put_uic = 111, 222
    return e


class TestSchema:
    def test_all_four_tables_exist(self, rec):
        names = {r[0] for r in rec._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"ls_entries", "ls_exits", "ls_snapshots", "ls_skipped"} <= names

    def test_there_is_no_credit_column_anywhere(self, rec):
        """The whole point. A credit column would invite exactly the mis-record
        that made this database necessary — nothing is ever sold in variant H."""
        for table in ("ls_entries", "ls_exits", "ls_snapshots", "ls_skipped"):
            cols = [c[1] for c in rec._conn.execute(f"PRAGMA table_info({table})")]
            assert not [c for c in cols if "credit" in c.lower()], f"{table}: {cols}"

    def test_debit_is_stored(self, rec):
        cols = [c[1] for c in rec._conn.execute("PRAGMA table_info(ls_entries)")]
        assert {"call_debit", "put_debit", "total_debit"} <= set(cols)

    def test_schema_version_is_recorded_once(self, rec):
        """Derived from the class constant, not a literal: a deliberate bump
        should not read as a failure, while "recorded exactly once, and matching
        the code" stays pinned across every future migration."""
        rows = list(rec._conn.execute("SELECT version FROM ls_schema_info"))
        assert rows == [(LongStrangleDataRecorder.SCHEMA_VERSION,)]

    def test_reopening_an_existing_db_does_not_duplicate_the_version(self, tmp_path):
        """CREATE IF NOT EXISTS means a fresh DB and an existing one take the same
        path — there must be no first-run special case, and no second row."""
        p = str(tmp_path / "ls.db")
        LongStrangleDataRecorder(p).close()
        r2 = LongStrangleDataRecorder(p)
        assert list(r2._conn.execute("SELECT COUNT(*) FROM ls_schema_info")) == [(1,)]
        r2.close()


class TestRoundTrip:
    def test_entry_round_trips_with_the_debit_intact(self, rec):
        rec.record_entry(_entry(), date="2026-09-23", spx_at_entry=7765.0,
                         vix_at_entry=14.5, em_source="straddle",
                         expected_move=60.0, skew_gap_pct=12.5)
        row = rec._conn.execute(
            "SELECT call_strike, put_strike, total_debit, contracts, em_source, "
            "expected_move, skew_gap_pct FROM ls_entries").fetchone()
        assert row == (7825.0, 7725.0, 2450.0, 7, "straddle", 60.0, 12.5)

    def test_the_em_source_is_recorded_per_entry(self, rec):
        """The expected-move definition is config-driven and still unsettled, so
        which one picked the strikes must be stored per row — otherwise a later
        analysis cannot separate the two regimes."""
        rec.record_entry(_entry(), "2026-09-23", 7765.0, em_source="vix",
                         expected_move=70.9)
        assert rec._conn.execute("SELECT em_source FROM ls_entries").fetchone()[0] == "vix"

    def test_exit_stores_pnl_in_dollars_AND_as_percent_of_debit(self, rec):
        """+50%/+100% are the units the strategy's targets are written in."""
        e = _entry(call_px=2.50, put_px=2.75)          # value 3675 on 2450 debit
        rec.record_exit(e, "2026-09-23", exit_reason="profit_target_50",
                        realized_pnl=1225.0, commissions=18.4,
                        spx_at_exit=7800.0, minutes_held=42.0, exit_time="10:27")
        row = rec._conn.execute(
            "SELECT exit_reason, realized_pnl, pnl_pct_of_debit, commissions "
            "FROM ls_exits").fetchone()
        assert row[0] == "profit_target_50"
        assert row[1] == pytest.approx(1225.0)
        assert row[2] == pytest.approx(50.0, abs=0.01)
        assert row[3] == pytest.approx(18.4)

    def test_snapshots_accumulate_rather_than_replace(self, rec):
        """A long strangle can touch +50% on a gamma spike and give it back
        within a minute. The peak is a different number from the exit, and both
        are needed to judge the exit rule — so snapshots must not upsert."""
        e = _entry()
        for i, px in enumerate((2.0, 4.0, 1.0)):
            e.long_call_price = px
            rec.record_snapshot(e, "2026-09-23", timestamp=f"10:0{i}", spx=7765.0 + i)
        vals = [r[0] for r in rec._conn.execute(
            "SELECT pnl_pct_of_debit FROM ls_snapshots ORDER BY timestamp")]
        assert len(vals) == 3
        assert max(vals) > vals[-1], "the peak must survive a later give-back"

    def test_skip_records_enough_to_score_the_decision_later(self, rec):
        """The counterfactual the GEX work had to be retro-fitted for on variant
        B — and could never recover for its first 95 vetoes."""
        rec.record_skip("2026-09-23", 1, "09:45", "iv_percentile 62% > 35% max",
                        spx=7765.0, vix=14.5, proposed_call_strike=7825.0,
                        proposed_put_strike=7705.0, proposed_debit=2450.0,
                        em_source="straddle", expected_move=60.0,
                        iv_percentile=62.0, skew_gap_pct=9.0)
        row = rec._conn.execute(
            "SELECT skip_reason, proposed_call_strike, proposed_debit, iv_percentile "
            "FROM ls_skipped").fetchone()
        assert row[0].startswith("iv_percentile")
        assert row[1] == 7825.0 and row[2] == 2450.0 and row[3] == 62.0

    def test_entries_upsert_on_the_same_key(self, rec):
        """Re-recording the same (date, entry_number) must correct, not duplicate."""
        rec.record_entry(_entry(), "2026-09-23", 7765.0)
        rec.record_entry(_entry(), "2026-09-23", 7770.0)
        assert rec._conn.execute("SELECT COUNT(*) FROM ls_entries").fetchone()[0] == 1


class TestItNeverBreaksTrading:
    def test_an_unopenable_db_degrades_to_a_silent_no_op(self, tmp_path):
        """No connection must mean "no persistence", never "no strategy"."""
        r = LongStrangleDataRecorder(str(tmp_path / "nope" / "deep" / "ls.db"))
        assert r._conn is None
        r.record_entry(_entry(), "2026-09-23", 7765.0)   # must not raise
        r.record_exit(_entry(), "2026-09-23", "x", 0.0)
        r.record_snapshot(_entry(), "2026-09-23", "10:00")
        r.record_skip("2026-09-23", 1, "09:45", "x")
        r.close()

    def test_a_write_failure_is_swallowed(self, rec):
        """A full disk should cost a row, not a position."""
        rec._conn.close()          # every later write now raises internally
        rec.record_entry(_entry(), "2026-09-23", 7765.0)   # must not raise
        rec.record_snapshot(_entry(), "2026-09-23", "10:00")

    def test_close_is_idempotent(self, rec):
        rec.close()
        rec.close()


class TestTheSharedDatabaseIsUntouched:
    def test_the_recorder_names_its_own_file_only(self, tmp_path):
        r = LongStrangleDataRecorder(str(tmp_path / "long_strangle.db"))
        assert "backtesting.db" not in r.db_path
        r.close()

    def test_no_EXECUTABLE_reference_to_the_shared_schema(self):
        """It must be impossible for this module to bump the shared schema version
        or write an IC table — the risk Step 7 exists to remove.

        Checks executable string literals only, via the AST. A plain grep would
        also hit the module docstring, which names `backtesting.db` on purpose to
        explain why it is NOT used; forbidding the explanation would push the
        reasoning out of the file, which is the opposite of useful."""
        import ast
        tree = ast.parse((ROOT / "bots" / "hydra" / "ls_recorder.py").read_text())

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)

        live = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value not in docstrings]

        for banned in ("backtesting.db", "trade_entries", "trade_stops",
                       "daily_summaries"):
            hits = [s for s in live if banned in s]
            assert not hits, f"{banned!r} reachable in executable code: {hits}"

    def test_every_table_it_creates_is_namespaced(self, rec):
        """An ls_ prefix on every table means a future shared-DB reader cannot
        collide with these names by accident."""
        names = {r[0] for r in rec._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert all(n.startswith("ls_") for n in names), names


class TestTheFileItselfIsSqlite:
    def test_the_db_is_readable_by_a_plain_sqlite_client(self, tmp_path):
        """Backups, the dashboard and any analyzer open these read-only — the
        file must be an ordinary SQLite database with WAL enabled."""
        p = tmp_path / "long_strangle.db"
        r = LongStrangleDataRecorder(str(p))
        r.record_entry(_entry(), "2026-09-23", 7765.0)
        r.close()
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM ls_entries").fetchone()[0] == 1
        con.close()


class TestItCanBeReadBackForRestartRecovery:
    """A recorder is normally write-only. This one is read for one specific
    reason: the shared state file serialises only credit-shaped fields and its
    restore path hardcodes the IC entry class, so a mid-day restart hands variant
    H back an entry with NO COST BASIS. Rather than change the shared save/load
    that variant B trades on live, H reads its debit back out of here.

    These tests pin the CONTRACT that recovery depends on — the exact column
    names, not just "some row came back" — because a rename here would break
    recovery silently and only on a restart.
    """

    def test_the_row_carries_every_field_recovery_needs(self, rec):
        rec.record_entry(_entry(), "2026-09-23", 7765.0, em_source="straddle",
                         expected_move=22.0, skew_gap_pct=16.7)
        row = rec.fetch_entries("2026-09-23")[0]
        assert row["entry_number"] == 1
        assert row["call_debit"] == 1400.0 and row["put_debit"] == 1050.0
        assert row["contracts"] == 7
        assert row["call_strike"] == 7825.0 and row["put_strike"] == 7725.0
        assert row["long_call_uic"] == 111 and row["long_put_uic"] == 222
        assert row["em_source"] == "straddle" and row["expected_move"] == 22.0

    def test_another_day_is_not_returned(self):
        """Recovery asks for today. Yesterday's entries are settled and must not
        come back as open positions."""
        import tempfile
        r = LongStrangleDataRecorder(str(Path(tempfile.mkdtemp()) / "ls.db"))
        r.record_entry(_entry(), "2026-09-22", 7700.0)
        assert r.fetch_entries("2026-09-23") == []
        assert len(r.fetch_entries("2026-09-22")) == 1
        r.close()

    def test_rows_come_back_in_entry_order(self, rec):
        for n in (3, 1, 2):
            e = _entry()
            e.entry_number = n
            rec.record_entry(e, "2026-09-23", 7765.0)
        assert [r["entry_number"] for r in rec.fetch_entries("2026-09-23")] == [1, 2, 3]

    def test_a_dead_connection_returns_empty_rather_than_raising(self, rec):
        """Recovery degrades to "nothing recovered", which the caller reports
        loudly — it must never raise into startup."""
        rec._conn.close()
        assert rec.fetch_entries("2026-09-23") == []

    def test_no_connection_at_all_returns_empty(self, tmp_path):
        r = LongStrangleDataRecorder(str(tmp_path / "nope" / "deep" / "ls.db"))
        assert r._conn is None
        assert r.fetch_entries("2026-09-23") == []
