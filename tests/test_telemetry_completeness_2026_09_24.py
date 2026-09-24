"""Third sweep — a filter whose decision is not RECORDED cannot be judged.

Passes 1 and 2 checked entry rules and exit rules. This pass asks a different
question entirely: **does each strategy store enough to score its own
decisions afterwards?** A dry run exists to produce evidence, and a gate that
vetoes without leaving a trace produces none.

TWO GAPS, BOTH THE SAME SHAPE
------------------------------
1. **H's range-expansion reading was computed, logged, and lost.** I added that
   filter this morning and reintroduced the exact defect I had fixed hours
   earlier for ``iv_percentile``: the reading reached ``entry.ls_range_expansion``
   and no column. The asymmetry was subtle — the skip REASON embeds the detail,
   so only the days the filter PASSED would have been missing, leaving the
   threshold judgeable from rejections alone.

2. **D and E recorded no skips AT ALL.** There was no ``dc_skipped`` table, so a
   declined calendar left no trace and "placed nothing today" was
   indistinguishable from "was vetoed today". That is the gap the GEX work on
   variant B had to be retro-fitted for, and which cost it its first 95 vetoes
   permanently. It became urgent when E's low-IV gate changed from an absolute
   cutoff to a percentile that can actually veto.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.dc_recorder import DCDataRecorder  # noqa: E402
from bots.hydra.ls_recorder import LongStrangleDataRecorder  # noqa: E402
from bots.hydra.long_strangle_entry import LongStrangleEntry  # noqa: E402


class TestHStoresTheRangeExpansionReading:

    @pytest.fixture
    def rec(self, tmp_path):
        return LongStrangleDataRecorder(str(tmp_path / "ls.db"))

    def test_a_SKIPPED_entry_carries_it(self, rec):
        rec.record_skip("2026-09-24", 1, "09:45", "no range expansion",
                        range_expansion="narrow 36.1 vs baseline 51.2")
        got = sqlite3.connect(rec.db_path).execute(
            "SELECT range_expansion FROM ls_skipped").fetchone()[0]
        assert "36.1" in got

    def test_a_PLACED_entry_carries_it_too(self, rec):
        """The half that was missing. With only the skip rows, the filter could
        be judged from its rejections and never from what it admitted."""
        e = LongStrangleEntry(entry_number=1)
        e.long_call_strike, e.long_put_strike, e.contracts = 770.0, 760.0, 1
        rec.record_entry(e, date="2026-09-24", spx_at_entry=765.0,
                         range_expansion="narrow 36.1, latest 64.9")
        got = sqlite3.connect(rec.db_path).execute(
            "SELECT range_expansion FROM ls_entries").fetchone()[0]
        assert "64.9" in got

    def test_an_older_database_gains_the_columns(self, tmp_path):
        """CREATE TABLE IF NOT EXISTS is a no-op on an existing DB, so a new
        COLUMN needs the ALTER path — H's live DB already holds rows."""
        db = tmp_path / "ls.db"
        con = sqlite3.connect(db)
        con.executescript(
            "CREATE TABLE ls_entries (date TEXT, entry_number INTEGER,"
            " PRIMARY KEY (date, entry_number));"
            "CREATE TABLE ls_skipped (date TEXT, entry_number INTEGER);"
            "CREATE TABLE ls_schema_info (version INTEGER);"
            "INSERT INTO ls_schema_info VALUES (2);")
        con.commit(); con.close()
        LongStrangleDataRecorder(str(db))
        con = sqlite3.connect(db)
        for t in ("ls_entries", "ls_skipped"):
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
            assert "range_expansion" in cols, t
        assert con.execute("SELECT version FROM ls_schema_info").fetchone()[0] == 3

    def test_the_new_columns_are_declared_for_migration(self):
        """The derived guard from v2, still doing its job on v3."""
        declared = {(t, c) for t, c, _ in LongStrangleDataRecorder._ADDED_COLUMNS}
        assert ("ls_entries", "range_expansion") in declared
        assert ("ls_skipped", "range_expansion") in declared


class TestTheCalendarsRecordTheirVetoes:

    @pytest.fixture
    def rec(self, tmp_path):
        return DCDataRecorder(str(tmp_path / "dc.db"))

    def test_the_table_exists_at_all(self, rec):
        """It did not, for D and E's entire lives."""
        names = {r[0] for r in sqlite3.connect(rec.db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "dc_skipped" in names

    def test_a_veto_carries_the_MEASUREMENT_not_just_the_prose(self, rec):
        """A skip recorded only as a sentence cannot be scored against the
        outcome it avoided."""
        rec.record_skip(date="2026-09-24", strategy_id="SPYDC", entry_number=1,
                        skip_time="10:00:00", skip_reason="VIX-percentile 41% > 35%",
                        spx=765.0, vix=16.4, iv_percentile=41.0, iv_percentile_n=252)
        row = sqlite3.connect(rec.db_path).execute(
            "SELECT iv_percentile, iv_percentile_n, spx FROM dc_skipped").fetchone()
        assert row == (41.0, 252, 765.0)

    def test_it_distinguishes_D_from_E_in_a_shared_read(self, rec):
        rec.record_skip("2026-09-24", "DCTM", 1, "10:00", "a")
        rec.record_skip("2026-09-24", "SPYDC", 1, "10:00", "b")
        ids = {r[0] for r in sqlite3.connect(rec.db_path).execute(
            "SELECT strategy_id FROM dc_skipped")}
        assert ids == {"DCTM", "SPYDC"}

    def test_an_existing_database_gains_the_table(self, tmp_path):
        db = tmp_path / "old.db"
        con = sqlite3.connect(db)
        con.executescript("CREATE TABLE dc_schema_info (version INTEGER);"
                          "INSERT INTO dc_schema_info VALUES (3);")
        con.commit(); con.close()
        DCDataRecorder(str(db))
        con = sqlite3.connect(db)
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "dc_skipped" in names
        assert con.execute("SELECT version FROM dc_schema_info").fetchone()[0] == 4


class TestEActuallyCallsIt:
    """The recorder existing is necessary, not sufficient — H's restart-recovery
    was dead on arrival for exactly this reason."""

    def test_the_gate_path_records(self):
        src = (ROOT / "bots" / "hydra" / "spy_double_calendar_strategy.py").read_text()
        assert "self._spy_dc_record_skip(entry_num, iv_skip)" in src

    def test_the_measurement_is_carried_across(self):
        src = (ROOT / "bots" / "hydra" / "spy_double_calendar_strategy.py").read_text()
        assert "_spy_dc_last_iv" in src

    def test_recording_cannot_break_the_entry_path(self):
        src = (ROOT / "bots" / "hydra" / "spy_double_calendar_strategy.py").read_text()
        assert "recording must not block trading" in src

    def test_it_degrades_when_no_recorder_is_attached(self):
        """Dry-run constructions and tests attach none."""
        from bots.hydra.spy_double_calendar_strategy import SpyDoubleCalendarStrategy
        s = SpyDoubleCalendarStrategy.__new__(SpyDoubleCalendarStrategy)
        s._dc_recorder = None
        s._spy_dc_record_skip(1, "reason")      # must not raise


class TestTheVersionBumpDidNotDisableTheMigrations:
    """A regression I introduced while adding ``dc_skipped``, caught by the
    suite rather than in production.

    The version bump was written as ``elif row[0] != SCHEMA_VERSION: UPDATE``,
    which intercepted **precisely the case migration exists for**: an existing
    database had its version stamped forward while ``_migrate`` never ran,
    silently skipping every ADD COLUMN and the ``dc_outcomes`` unique index. The
    result would have been a schema claiming to be current while missing the
    columns of two prior versions — and nothing would have said so.
    """

    def test_an_old_database_gets_BOTH_the_migration_and_the_bump(self, tmp_path):
        db = tmp_path / "old.db"
        con = sqlite3.connect(db)
        con.executescript(
            "CREATE TABLE dc_outcomes (strategy_id TEXT, date TEXT);"
            "CREATE TABLE dc_schema_info (version INTEGER);"
            "INSERT INTO dc_schema_info VALUES (1);")
        con.commit(); con.close()

        DCDataRecorder(str(db))
        con = sqlite3.connect(db)
        idx = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_dc_outcomes_strategy_id" in idx, (
            "the version was stamped forward without running the migrations")
        assert con.execute(
            "SELECT version FROM dc_schema_info").fetchone()[0] == DCDataRecorder.SCHEMA_VERSION

    def test_migration_runs_before_the_stamp(self):
        src = (ROOT / "bots" / "hydra" / "dc_recorder.py").read_text()
        i_mig = src.index("self._migrate(int(row[0]))")
        i_stamp = src.index("UPDATE dc_schema_info SET version")
        assert i_mig < i_stamp, "stamping before migrating re-creates the bug"


class TestHReadsTheBackfilledVixHistory:
    """Caught LIVE, on H's first real entry (2026-09-24 09:45 ET).

    The morning's work backfilled 446 VIX closes into a ``vix_daily`` table and
    added ``vix_history_from_db`` to union it with ``market_ticks`` — and wired
    **E** to it. H kept its own private ``market_ticks``-only SELECT, so the
    backfill reached the variant it was NOT built for and missed the one it was.

    H said so itself. The entry log read *"IV gate PASSED at 33th pct, but over
    94 days not the 252 configured — this is a partial-window percentile"*, a
    line added hours earlier precisely so a short window would announce itself.
    It announced this instead.
    """

    def test_H_uses_the_shared_reader(self):
        import inspect
        from bots.hydra.long_strangle_strategy import LongStrangleStrategy
        src = inspect.getsource(LongStrangleStrategy._vix_history_for_percentile)
        assert "vix_history_from_db(" in src

    def test_H_no_longer_runs_its_own_query(self):
        """Two readers of one series drift apart — that is the whole defect."""
        import inspect
        from bots.hydra.long_strangle_strategy import LongStrangleStrategy
        src = inspect.getsource(LongStrangleStrategy._vix_history_for_percentile)
        assert "FROM market_ticks" not in src

    def test_H_and_E_now_resolve_the_SAME_function(self):
        import bots.hydra.long_strangle_strategy as h
        import bots.hydra.spy_double_calendar_strategy as e
        from bots.hydra import iv_percentile as shared
        assert h.vix_history_from_db is shared.vix_history_from_db
        assert e.vix_history_from_db is shared.vix_history_from_db

    def test_the_backfilled_table_actually_widens_the_window(self, tmp_path):
        """End to end: ticks alone give few days; ticks + vix_daily give many."""
        import datetime as dt
        from bots.hydra.iv_percentile import vix_history_from_db
        db = tmp_path / "bt.db"
        con = sqlite3.connect(db)
        with con:
            con.execute("CREATE TABLE market_ticks (timestamp TEXT, vix_level REAL)")
            con.executemany("INSERT INTO market_ticks VALUES (?,?)", [
                ((dt.date(2026, 5, 1) + dt.timedelta(days=i)).isoformat() + " 15:59:00", 15.0)
                for i in range(94)])
        assert len(vix_history_from_db(str(db), "2026-09-24", 252)) == 94
        with con:
            con.execute("CREATE TABLE vix_daily (date TEXT PRIMARY KEY, close REAL, source TEXT)")
            con.executemany("INSERT INTO vix_daily VALUES (?,?,'yahoo')", [
                ((dt.date(2025, 1, 2) + dt.timedelta(days=i)).isoformat(), 16.0)
                for i in range(300)])
        con.close()
        assert len(vix_history_from_db(str(db), "2026-09-24", 252)) == 252

    def test_the_other_methods_survived_the_edit(self):
        """I clobbered several of these with a careless slice while making this
        very fix; the restore is pinned so a re-slice cannot pass silently."""
        from bots.hydra.long_strangle_strategy import LongStrangleStrategy
        for meth in ("_daily_ranges", "_range_expansion_gate", "_snap_cap",
                     "_iv_percentile_gate", "_vix_history_for_percentile",
                     "_vix_history_db"):
            assert hasattr(LongStrangleStrategy, meth), meth
