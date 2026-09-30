"""Repairs must be derived, idempotent, and must not touch the live seat.

Two of the seven identity breaks are recoverable; five are not, and the five are
in a register instead. The tests that matter here are the ones that stop this
script from growing into a way to make inconvenient numbers go away:

  * nothing is repaired unless an identity that holds elsewhere says what the
    value must be;
  * running it twice must not double-apply;
  * every registered "cannot repair" row must be pre-2026-07-24, because the
    live-seat record is the go-live evidence and nothing in it may be excused.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import repair_identity_breaks as rib  # noqa: E402
from scripts.audit_fleet_correctness import KNOWN_IRREPARABLE  # noqa: E402

LIVE_SEAT_START = "2026-07-24"


def _db(tmp_path, vid):
    from shared.data_recorder import DataRecorder
    d = tmp_path / "data" if vid == "a" else tmp_path / "data" / f"variant_{vid}"
    d.mkdir(parents=True)
    p = str(d / "backtesting.db")
    DataRecorder(p).ensure_schema()
    return p


class TestTheRegisterNeverExcusesTheLiveSeat:
    def test_every_known_irreparable_row_predates_the_live_seat(self):
        late = [(v, d) for (v, d) in KNOWN_IRREPARABLE if d >= LIVE_SEAT_START]
        assert not late, (
            f"{late} is on or after {LIVE_SEAT_START}, when B took the live seat. "
            f"The live record is the go-live evidence — a break in it must be "
            f"investigated, never registered as known-unrepairable.")

    def test_every_entry_carries_a_real_explanation(self):
        for key, reason in KNOWN_IRREPARABLE.items():
            assert len(reason) > 40, f"{key} has a stub reason: {reason!r}"

    def test_the_register_stays_small(self):
        assert len(KNOWN_IRREPARABLE) <= 8, (
            f"the register grew to {len(KNOWN_IRREPARABLE)} — each entry silences "
            f"a gating break, so growth needs a diagnosis, not a default")


class TestTheNetRepairIsDerivedNotGuessed:
    def _seed(self, p, gross, net, comm):
        con = sqlite3.connect(p)
        con.execute("INSERT INTO daily_summaries (date, gross_pnl, net_pnl, commission) "
                    "VALUES ('2026-04-01',?,?,?)", (gross, net, comm))
        con.commit(); con.close()

    def test_it_proposes_gross_minus_commission(self, tmp_path):
        p = _db(tmp_path, "a")
        self._seed(p, 1235.0, 330.0, 40.0)
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        plan = rib._a_0401(con); con.close()
        assert plan is not None
        assert plan["new"] == 1195.0, plan

    def test_it_does_nothing_when_the_identity_already_holds(self, tmp_path):
        p = _db(tmp_path, "a")
        self._seed(p, 1235.0, 1195.0, 40.0)
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        assert rib._a_0401(con) is None
        con.close()

    def test_it_does_nothing_when_inputs_are_missing(self, tmp_path):
        p = _db(tmp_path, "a")
        self._seed(p, None, 330.0, None)
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        assert rib._a_0401(con) is None
        con.close()


class TestTheMissingSummaryIsReconstructedFromRealRows:
    def _seed(self, p):
        con = sqlite3.connect(p)
        con.execute("INSERT INTO trade_entries (date, entry_number, total_credit, contracts) "
                    "VALUES ('2026-06-11', 1, 140.0, 7)")
        con.execute("INSERT INTO trade_stops (date, entry_number, side, net_pnl) "
                    "VALUES ('2026-06-11', 1, 'put', 70.0)")
        con.commit(); con.close()

    def test_it_builds_a_row_from_the_rows_that_exist(self, tmp_path):
        p = _db(tmp_path, "c")
        self._seed(p)
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        plan = rib._c_0611(con); con.close()
        assert plan is not None
        assert "gross=70.0" in plan["new"], plan["new"]
        assert "placed=1" in plan["new"]

    def test_it_refuses_when_there_are_no_entries_to_build_from(self, tmp_path):
        p = _db(tmp_path, "c")
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        assert rib._c_0611(con) is None, (
            "it invented a summary for a day with no recorded trades")
        con.close()

    def test_it_does_nothing_when_the_row_already_exists(self, tmp_path):
        p = _db(tmp_path, "c")
        self._seed(p)
        con = sqlite3.connect(p)
        con.execute("INSERT INTO daily_summaries (date, gross_pnl) VALUES ('2026-06-11', 70.0)")
        con.commit(); con.close()
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        assert rib._c_0611(con) is None
        con.close()


class TestItIsIdempotent:
    def test_a_second_write_changes_nothing(self, tmp_path, capsys):
        pa = _db(tmp_path, "a")
        con = sqlite3.connect(pa)
        con.execute("INSERT INTO daily_summaries (date, gross_pnl, net_pnl, commission) "
                    "VALUES ('2026-04-01', 1235.0, 330.0, 40.0)")
        con.commit(); con.close()

        assert rib.main(["--root", str(tmp_path), "--write"]) == 0
        first = capsys.readouterr().out
        assert "REPAIRED 1" in first, first

        assert rib.main(["--root", str(tmp_path), "--write"]) == 0
        second = capsys.readouterr().out
        assert "REPAIRED 0" in second, (
            "a second run found work to do — the repair is not idempotent and "
            "would keep rewriting the same row and logging fresh audit entries")

        con = sqlite3.connect(pa)
        n = con.execute("SELECT COUNT(*) FROM data_corrections").fetchone()[0]
        v = con.execute("SELECT net_pnl FROM daily_summaries WHERE date='2026-04-01'").fetchone()[0]
        con.close()
        assert v == 1195.0
        assert n == 1, f"{n} audit rows for one repair"


class TestVariantAIsActuallyMatched:
    """The bug this class exists for.

    `VARIANTS` uses "" for variant A, not "a" — the audit builds A's path as
    `data/` rather than `data/variant_a/`. The register was keyed on "a", so
    every A entry missed silently and the gate stayed red on three rows that
    were supposed to be registered. Nothing failed; the rows just kept
    printing as breaks.

    Unit-testing the register's contents could never catch that. Only asking
    the lookup the question the audit asks it can.
    """

    def test_the_empty_vid_resolves_to_variant_a(self):
        from scripts.audit_fleet_correctness import _is_known_irreparable
        assert _is_known_irreparable("", "2026-03-20: summary says 5 entries"), (
            "variant A's rows are not matched — the audit passes '' as its vid "
            "and the register is keyed on 'a'")

    def test_every_registered_variant_a_row_is_reachable(self):
        from scripts.audit_fleet_correctness import _is_known_irreparable, KNOWN_IRREPARABLE
        for (vid, date) in KNOWN_IRREPARABLE:
            probe = "" if vid == "a" else vid
            assert _is_known_irreparable(probe, f"{date}: whatever the message says"), (
                f"({vid}, {date}) is in the register but unreachable with the "
                f"vid the audit actually passes ({probe!r})")

    def test_an_unregistered_row_is_still_a_break(self):
        from scripts.audit_fleet_correctness import _is_known_irreparable
        assert _is_known_irreparable("", "2026-05-05: some other day") is None
