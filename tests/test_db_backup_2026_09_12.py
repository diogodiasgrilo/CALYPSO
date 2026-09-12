"""shared/db_backup.py — the one correct SQLite backup (2026-09-12).

THREE SCRIPTS hand-rolled this backup and all three got the same two things
wrong. Both mistakes produce a file that LOOKS like a backup:

  * `shutil.copy2` on a WAL database. `DataRecorder` and `dc_recorder` both set
    `PRAGMA journal_mode=WAL`, which is persistent on the file, so committed rows
    can be sitting in the `-wal` sidecar. Copying the `.db` alone silently drops
    them — and you only find out when you restore.
  * A second-granularity timestamp. Two runs in the same second share a filename,
    so the second overwrites the good backup with a copy of the ALREADY-MODIFIED
    database, destroying the original exactly when it is wanted.

The WAL test below is the one that matters: it fails against `shutil.copy2`.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db_backup import safe_db_backup  # noqa: E402


def _wal_db(tmp_path, rows=3, checkpoint=False):
    """A WAL database with committed rows still resident in the -wal sidecar."""
    db = str(tmp_path / "t.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (id INTEGER)")
    con.commit()
    if checkpoint:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for i in range(rows):
        con.execute("INSERT INTO t VALUES (?)", (i,))
    con.commit()
    # deliberately NOT closed: the -wal is live, which is the real-world shape
    return db, con


def _count(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        con.close()


class TestItIsWalSafe:
    def test_committed_rows_in_the_wal_sidecar_reach_the_backup(self, tmp_path):
        """THE test. A plain file copy of the .db alone returns 0 here."""
        db, con = _wal_db(tmp_path, rows=3)
        try:
            assert os.path.exists(db + "-wal"), "precondition: rows live in the -wal"
            backup = safe_db_backup(db, "tag")
            assert _count(backup) == 3
        finally:
            con.close()

    def test_a_plain_file_copy_would_LOSE_them(self, tmp_path):
        """Proves the bug is real rather than assumed — this is what the three
        scripts used to do."""
        import shutil
        db, con = _wal_db(tmp_path, rows=3)
        try:
            naive = str(tmp_path / "naive.db")
            shutil.copy2(db, naive)
            # Either failure mode is data loss: an under-count, or an outright
            # unreadable file (observed — the copy lands mid-WAL and the table
            # itself is missing). Both are what a restore would hand you.
            try:
                lost = _count(naive) < 3
            except sqlite3.Error:
                lost = True
            assert lost, (
                "if this ever passes, sqlite checkpointed early and the test no "
                "longer exercises the hazard — it does not mean copy2 is safe"
            )
        finally:
            con.close()

    def test_it_works_while_another_connection_holds_the_db_open(self, tmp_path):
        """backfill_overlay_residual.py backs up while holding its own open
        connection to the DB it is about to write."""
        db, con = _wal_db(tmp_path, rows=2)
        try:
            assert _count(safe_db_backup(db, "tag")) == 2
        finally:
            con.close()

    def test_it_does_not_mutate_the_source(self, tmp_path):
        db, con = _wal_db(tmp_path, rows=2)
        try:
            safe_db_backup(db, "tag")
            assert _count(db) == 2
        finally:
            con.close()


class TestTheNameNeverCollides:
    def test_two_runs_in_the_same_instant_keep_two_backups(self, tmp_path):
        """Second granularity silently clobbered the first backup."""
        db, con = _wal_db(tmp_path, rows=1, checkpoint=True)
        try:
            a = safe_db_backup(db, "tag")
            b = safe_db_backup(db, "tag")
            assert a != b
            assert os.path.exists(a) and os.path.exists(b)
        finally:
            con.close()

    def test_many_rapid_runs_all_survive(self, tmp_path):
        db, con = _wal_db(tmp_path, rows=1, checkpoint=True)
        try:
            paths = [safe_db_backup(db, "tag") for _ in range(6)]
            assert len(set(paths)) == 6
            assert all(os.path.exists(p) for p in paths)
        finally:
            con.close()

    def test_the_tag_appears_in_the_name(self, tmp_path):
        """Operators find these by grep; an unlabelled file is not findable."""
        db, con = _wal_db(tmp_path, rows=1)
        try:
            assert ".pre_skip_backfill_" in os.path.basename(
                safe_db_backup(db, "pre_skip_backfill"))
        finally:
            con.close()

    def test_the_backup_sits_beside_the_source(self, tmp_path):
        db, con = _wal_db(tmp_path, rows=1)
        try:
            assert os.path.dirname(safe_db_backup(db, "tag")) == os.path.dirname(db)
        finally:
            con.close()


class TestItRefusesRatherThanPretending:
    def test_a_missing_database_raises(self, tmp_path):
        """Silently 'succeeding' would let the caller proceed to write believing
        it has a rollback — worse than no backup at all."""
        with pytest.raises(FileNotFoundError):
            safe_db_backup(str(tmp_path / "nope.db"), "tag")


class TestTheCallersActuallyUseIt:
    """A helper nothing calls is decoration; a caller still on copy2 is the bug."""

    @pytest.mark.parametrize("script", [
        "analyze_skipped_entry_outcomes.py",
        "backfill_overlay_residual.py",
        "backfill_lost_calendars.py",
    ])
    def test_the_script_backs_up_through_the_helper(self, script):
        src = (Path(__file__).resolve().parents[1] / "scripts" / script).read_text()
        assert "safe_db_backup(" in src
        assert "shutil.copy2(" not in src, "still doing an unsafe WAL file copy"
