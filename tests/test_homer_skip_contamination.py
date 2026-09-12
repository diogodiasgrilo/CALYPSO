"""2026-07-14: HOMER's trade-row back-fill (from the Google-Sheets "Trades" tab,
which after the 2026-06-02 pivot reflects the canonical LIVE variant's REAL
trades) must NOT fabricate a phantom trade_entries / trade_stops row for a
(date, entry_number) that the DB's OWN variant SKIPPED. A skipped slot means the
variant never placed that entry, so any back-filled row for it is contamination
(config_version NULL). Reproduces the variant-A main-DB pollution (7 phantom rows
since 2026-06-25) and pins that genuine gap-fill + the live variant's DB are
unaffected.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.homer.db_manager import BacktestingDB  # noqa: E402


def _db(tmp_path):
    return BacktestingDB(str(tmp_path / "bt.db"))


def _mark_skipped(db, date, entry_number):
    """Mimic the live DataRecorder recording that THIS variant skipped the slot."""
    with db._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO skipped_entries (date, entry_number, skip_reason) "
            "VALUES (?, ?, ?)",
            (date, entry_number, "MKT-032: call non-viable"),
        )


def _entry(date, n, **kw):
    row = {"date": date, "entry_number": n, "entry_time": "11:17:03 AM ET",
           "entry_type": "put_only", "total_credit": 105.0, "contracts": 7}
    row.update(kw)
    return row


def _stop(date, n, **kw):
    row = {"date": date, "entry_number": n, "side": "put", "stop_time": "15:39:05",
           "trigger_level": 1575.0, "actual_debit": 1910.0, "net_pnl": -1805.0}
    row.update(kw)
    return row


def _count(db, table, date, n):
    with db._connect() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE date=? AND entry_number=?",
            (date, n)).fetchone()[0]


class TestHomerSkipContamination:
    def test_entry_for_skipped_slot_is_dropped(self, tmp_path):
        # The exact 07-13 shape: A skipped entry 2 (MKT-032) but C traded it —
        # HOMER must NOT fabricate a trade_entries row in A's DB.
        db = _db(tmp_path)
        _mark_skipped(db, "2026-07-13", 2)
        assert db.insert_trade_entries([_entry("2026-07-13", 2)]) == 0
        assert _count(db, "trade_entries", "2026-07-13", 2) == 0

    def test_stop_for_skipped_slot_is_dropped(self, tmp_path):
        db = _db(tmp_path)
        _mark_skipped(db, "2026-07-13", 2)
        assert db.insert_trade_stops([_stop("2026-07-13", 2)]) == 0
        assert _count(db, "trade_stops", "2026-07-13", 2) == 0

    def test_genuine_gap_fill_still_inserts(self, tmp_path):
        # No skip for this slot → legitimate back-fill still lands the row.
        db = _db(tmp_path)
        assert db.insert_trade_entries([_entry("2026-07-13", 1, total_credit=315.0)]) == 1
        assert _count(db, "trade_entries", "2026-07-13", 1) == 1

    def test_mixed_batch_drops_only_skipped(self, tmp_path):
        # Entry 1 taken, entry 2 skipped → only entry 1 inserts.
        db = _db(tmp_path)
        _mark_skipped(db, "2026-07-13", 2)
        n = db.insert_trade_entries([
            _entry("2026-07-13", 1, total_credit=315.0),
            _entry("2026-07-13", 2),
        ])
        assert n == 1
        with db._connect() as conn:
            nums = [r[0] for r in conn.execute(
                "SELECT entry_number FROM trade_entries WHERE date=? ORDER BY entry_number",
                ("2026-07-13",))]
        assert nums == [1]

    def test_live_variant_db_unaffected(self, tmp_path):
        # On the live variant's own DB (no skip for its real trade) the guard is a
        # no-op — the insert lands exactly as before (why B/C DBs are clean).
        db = _db(tmp_path)
        assert db.insert_trade_entries([_entry("2026-07-13", 2)]) == 1
        assert _count(db, "trade_entries", "2026-07-13", 2) == 1
