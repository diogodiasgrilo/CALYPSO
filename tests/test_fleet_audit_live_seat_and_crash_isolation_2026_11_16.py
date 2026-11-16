"""The gating fleet audit skipped the live seat, and a crash impersonated a break.

Found 2026-11-16 during a routine Monday pre-market sweep:
`calypso_health_checks.service` had been FAILED since Friday 2026-11-13, and
the failure was not the P&L identity break its exit code implied. It was

    sqlite3.OperationalError: unable to open database file
      audit_fleet_correctness.py:76  in audit()

a crash. Three separate defects fell out of it:

  1. `VARIANTS` did not contain `bl`. `bl` has held the LIVE PAPER SEAT since
     2026-10-09, so the audit that gates real money was checking eight
     variants and skipping the only one that trades. Third occurrence of a
     fleet list written without the live seat (emergency stop 2026-09-19; GCS
     DB backup ab63407), which is why the test below asserts the live seat
     generically rather than the string "bl".

  2. The crash aborted main()'s loop, so every variant AFTER the failing one
     was never audited — silently. Four of nine were reported on; five were
     not, and nothing said so.

  3. exit 1 means "identity break". A crash also exits 1. So "the data
     disagrees with itself" and "we never looked" arrived as the same signal,
     and the unit sat red for three days while the data was in fact fine.

These tests EXECUTE main() and audit() against real databases built with the
production schema. The file they extend already carries the lesson (see
TestTheAuditACTUALLYRUNS: six source-grep tests passed while audit() died with
an UnboundLocalError) and the defect here is exactly that shape again — so
nothing below greps the source for a constant it could have asserted by
running the thing.
"""
from __future__ import annotations

import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import audit_fleet_correctness as afc  # noqa: E402


def _norm(vid: str) -> str:
    """Taxonomy id -> VARIANTS entry. Variant A is "a" there and "" here.

    The same mismatch already bit `_is_known_irreparable`, whose register was
    keyed on "a" while the loop passed "" — so every variant-A entry silently
    missed and the gate stayed red on three registered rows.
    """
    return "" if vid == "a" else vid


def _wal_db_without_shm(root: Path, vid: str):
    """The PRODUCTION failure, reproduced: a WAL database a reader cannot open.

    This is the one that matters. Every `chmod 0` case in this file fails at
    `sqlite3.connect`, which the old code already survived — it returned None
    and reported a row. The 2026-11-13 crash was different in kind: connect
    SUCCEEDED and the first `execute` raised, because a read-only connection to
    a WAL database must be able to create the `-shm` sidecar, and under
    `ProtectSystem=strict` /opt/calypso is read-only, so it cannot.

    Staged by capturing a real `-wal` while writers hold it, letting the clean
    close checkpoint it away, then putting those bytes back: the database has
    already absorbed them, so a reader must run WAL recovery, which needs a
    writable `-shm`. With the directory r-x it gets exactly

        OperationalError: unable to open database file

    Two things here are load-bearing, both learned by getting them wrong:

    * The wal bytes must be REAL. A hand-written zero-filled file has no valid
      frames, so SQLite skips recovery, needs no `-shm`, and reproduces
      nothing — the first version of this fixture was green against the
      original broken code.
    * The schema is raw sqlite3, not `DataRecorder.ensure_schema()`. The
      recorder's `with self._connect() as conn` is a TRANSACTION context
      manager, not a close, so its connection lingers, the clean close never
      happens and the wal stays consistent. Hand-rolling a schema is normally
      the wrong instinct here (see TestTheGateStillGates) — it is right in this
      one fixture because this database is never successfully READ. Its whole
      job is to fail at open, so its columns are irrelevant.
    """
    d = root / "data" / (f"variant_{vid}" if vid else "")
    d.mkdir(parents=True, exist_ok=True)
    db = d / "backtesting.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE daily_summaries (date TEXT, gross_pnl REAL, "
                "net_pnl REAL, commission REAL, entries_placed INT)")
    con.execute("INSERT INTO daily_summaries VALUES ('2026-11-13',0,0,0,0)")
    con.commit()
    hold = sqlite3.connect(db)
    hold.execute("INSERT INTO daily_summaries VALUES ('2026-11-12',0,0,0,0)")
    hold.commit()
    wal = Path(str(db) + "-wal")
    assert wal.exists(), "WAL mode did not engage — no -wal while writers are open"
    wal_bytes = wal.read_bytes()
    assert len(wal_bytes) > 32, f"-wal is {len(wal_bytes)}B — no frames to recover"
    con.close()
    hold.close()
    wal.write_bytes(wal_bytes)
    shm = Path(str(db) + "-shm")
    if shm.exists():
        shm.unlink()
    return d, db


def _locked_dir(d: Path):
    """Context-manager-ish pair: make `d` unwritable, restore on exit."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions — cannot stage the "
                        "read-only mount this reproduces")
        before = stat.S_IMODE(d.stat().st_mode)
        os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
        try:
            yield
        finally:
            os.chmod(d, before)

    return _cm()


def _clean_variant(root: Path, vid: str, date: str = "2026-11-13"):
    """A variant DB on the real schema whose identities all hold."""
    from shared.data_recorder import DataRecorder
    d = root / "data" / (f"variant_{vid}" if vid else "")
    d.mkdir(parents=True, exist_ok=True)
    db = str(d / "backtesting.db")
    DataRecorder(db).ensure_schema()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO daily_summaries (date, gross_pnl, net_pnl, commission, "
                "entries_placed, entries_stopped, entries_expired) VALUES (?,?,?,?,?,?,?)",
                (date, 0.0, 0.0, 0.0, 0, 0, 0))
    con.commit()
    con.close()
    return Path(db)


def _breaking_variant(root: Path, vid: str, date: str = "2026-11-13"):
    """Real gross, zero trade_entries rows -> an I5 identity break."""
    from shared.data_recorder import DataRecorder
    d = root / "data" / (f"variant_{vid}" if vid else "")
    d.mkdir(parents=True, exist_ok=True)
    db = str(d / "backtesting.db")
    DataRecorder(db).ensure_schema()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO daily_summaries (date, gross_pnl, net_pnl, commission, "
                "entries_placed, entries_stopped, entries_expired) VALUES (?,?,?,?,?,?,?)",
                (date, -12.50, -12.50, 0.0, 0, 0, 0))
    con.commit()
    con.close()
    return Path(db)


class TestTheLiveSeatIsAudited:
    """The money path must be in the list the gate iterates."""

    def test_bl_is_in_VARIANTS(self):
        assert "bl" in afc.VARIANTS, (
            "the live paper seat is not audited by the check that gates real "
            "money — it was absent from 2026-10-09 to 2026-11-16")

    def test_the_DECLARED_live_seat_is_in_VARIANTS(self):
        """Asserted against the taxonomy, not the literal 'bl'.

        The defect was not "someone forgot bl", it was "the fleet list and the
        live seat drift apart". Pinning the string would pass again on the day
        the seat moves to a variant nobody added here. `shared.strategy_taxonomy`
        is the declared single source of truth for which variant is live, and it
        reads no config from disk, so this is deterministic in CI.
        """
        from shared.strategy_taxonomy import STRATEGIES
        live = {k for k, v in STRATEGIES.items() if v.status == "live"}
        assert live, "the taxonomy declares no live seat at all"
        missing = {k for k in live if _norm(k) not in afc.VARIANTS}
        assert not missing, (
            f"live seat(s) {missing} absent from VARIANTS {afc.VARIANTS} — the "
            "gating audit does not look at the variant that trades")

    def test_EVERY_paper_strategy_is_covered(self):
        """The generic control — the one that would have caught this.

        Scoped to the PAPER account on purpose: `bm` is the real-money variant
        and has no paper database to audit, so demanding it would make this red
        for a reason that is not a defect. Paper membership comes from the
        taxonomy rather than a second hand-written list, because a hand-written
        list is the thing that failed.
        """
        from shared.strategy_taxonomy import PAPER, ids_for_account_kind
        missing = {v for v in ids_for_account_kind(PAPER)
                   if _norm(v) not in afc.VARIANTS}
        assert not missing, (
            f"paper variants the taxonomy knows but the audit never looks at: "
            f"{missing}")

    def test_the_live_seat_is_actually_SCORED_not_merely_listed(self, tmp_path):
        """Listing it is worthless if its rows never reach the verdict.

        So: give ONLY the live seat a break and assert the gate trips. If `bl`
        were in VARIANTS but its findings were dropped, this fails.
        """
        _breaking_variant(tmp_path, "bl")
        rc = afc.main(["--root", str(tmp_path)])
        assert rc == 1, f"a break on the LIVE SEAT did not fail the unit (rc={rc})"


class TestACrashCannotVoidTheFleet:
    """One variant's bad day must not stop the others being audited."""

    def test_an_unreadable_db_does_not_abort_the_run(self, tmp_path, capsys):
        """The 2026-11-13 shape: variant 2 of 3 unreadable, 3 of 3 must still report."""
        _clean_variant(tmp_path, "bl")
        bad = _clean_variant(tmp_path, "b")
        _clean_variant(tmp_path, "c")
        os.chmod(bad, 0)                      # present, unreadable
        try:
            rc = afc.main(["--root", str(tmp_path)])
            out = capsys.readouterr().out
        finally:
            os.chmod(bad, stat.S_IRUSR | stat.S_IWUSR)
        assert "UNAUDITABLE" in out, out
        assert "BL" in out and "C" in out, (
            "a variant after the failing one was never reported — the crash "
            f"aborted the loop:\n{out}")
        assert rc == 2, f"an unauditable variant must fail the unit with 2, got {rc}"

    def test_audit_raising_is_contained_per_variant(self, tmp_path, capsys, monkeypatch):
        """Defence in depth: even if audit() raises past _ro()'s probe."""
        _clean_variant(tmp_path, "bl")
        _clean_variant(tmp_path, "c")
        real = afc.audit

        def boom(root, vid, today):
            if vid == "bl":
                raise RuntimeError("synthetic explosion")
            return real(root, vid, today)

        monkeypatch.setattr(afc, "audit", boom)
        rc = afc.main(["--root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "UNAUDITABLE" in out and "synthetic explosion" in out, out
        assert "C" in out, f"variants after the raise were skipped:\n{out}"
        assert rc == 2, rc

    def test_an_unauditable_row_is_marked_loudly(self, tmp_path, capsys):
        _clean_variant(tmp_path, "bl")
        bad = _clean_variant(tmp_path, "c")
        os.chmod(bad, 0)
        try:
            afc.main(["--root", str(tmp_path)])
            out = capsys.readouterr().out
        finally:
            os.chmod(bad, stat.S_IRUSR | stat.S_IWUSR)
        line = [ln for ln in out.splitlines() if "UNAUDITABLE" in ln][0]
        assert line.startswith("  !!"), (
            f"an unaudited variant was not marked as a problem: {line!r}")


class TestUnreadableIsNotAbsent:
    """SKIP is a benign row. A file that exists but cannot be read is not benign."""

    def test_a_missing_db_is_SKIP(self, tmp_path):
        tags = {t for _n, t, _m in afc.audit(tmp_path, "g", "2026-11-16")}
        assert tags == {"SKIP"}, tags

    def test_a_present_but_unreadable_db_is_UNAUDITABLE(self, tmp_path):
        db = _clean_variant(tmp_path, "g")
        os.chmod(db, 0)
        try:
            tags = {t for _n, t, _m in afc.audit(tmp_path, "g", "2026-11-16")}
        finally:
            os.chmod(db, stat.S_IRUSR | stat.S_IWUSR)
        assert tags == {"UNAUDITABLE"}, (
            f"an unreadable database reported as {tags} — SKIP does not gate, "
            "so this would have passed silently")

    def test_a_missing_db_does_NOT_fail_the_unit(self, tmp_path):
        """The negative control for the above: absence is legitimately benign.
        Variants that have never run have no DB, and that must stay exit 0."""
        assert afc.main(["--root", str(tmp_path)]) == 0


class TestRoProbesAtOpenTime:
    """`sqlite3.connect` is lazy; the probe is what makes _ro() honest."""

    def test_ro_returns_a_usable_connection(self, tmp_path):
        db = _clean_variant(tmp_path, "bl")
        con = afc._ro(db)
        assert con is not None
        assert con.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()[0] == 1
        con.close()

    def test_ro_returns_None_for_an_unreadable_file(self, tmp_path):
        db = _clean_variant(tmp_path, "bl")
        os.chmod(db, 0)
        try:
            assert afc._ro(db) is None, (
                "_ro handed back a handle to a database it cannot read — the "
                "failure then lands on the first execute, which is the crash")
        finally:
            os.chmod(db, stat.S_IRUSR | stat.S_IWUSR)

    def test_ro_returns_None_for_a_non_database(self, tmp_path):
        junk = tmp_path / "not.db"
        junk.write_bytes(b"this is not a sqlite file" * 10)
        assert afc._ro(junk) is None

    def test_ro_returns_None_for_a_missing_file(self, tmp_path):
        assert afc._ro(tmp_path / "nope.db") is None


class TestTheExitCodesStayDistinct:
    """0 / 1 / 2 must mean three different things, and keep meaning them."""

    def test_clean_is_zero(self, tmp_path):
        _clean_variant(tmp_path, "bl")
        _clean_variant(tmp_path, "c")
        assert afc.main(["--root", str(tmp_path)]) == 0

    def test_break_is_one(self, tmp_path):
        _breaking_variant(tmp_path, "c")
        assert afc.main(["--root", str(tmp_path)]) == 1

    def test_unauditable_only_is_two(self, tmp_path):
        bad = _clean_variant(tmp_path, "c")
        os.chmod(bad, 0)
        try:
            assert afc.main(["--root", str(tmp_path)]) == 2
        finally:
            os.chmod(bad, stat.S_IRUSR | stat.S_IWUSR)

    def test_a_break_OUTRANKS_an_unauditable_variant(self, tmp_path):
        """Both at once must report the actionable one."""
        _breaking_variant(tmp_path, "bl")
        bad = _clean_variant(tmp_path, "c")
        os.chmod(bad, 0)
        try:
            rc = afc.main(["--root", str(tmp_path)])
        finally:
            os.chmod(bad, stat.S_IRUSR | stat.S_IWUSR)
        assert rc == 1, (
            f"rc={rc}: a real identity break was downgraded to 'could not "
            "audit' — the louder, actionable signal must win")

    def test_the_verdict_line_distinguishes_them(self, tmp_path, capsys):
        bad = _clean_variant(tmp_path, "c")
        os.chmod(bad, 0)
        try:
            afc.main(["--root", str(tmp_path)])
            out = capsys.readouterr().out
        finally:
            os.chmod(bad, stat.S_IRUSR | stat.S_IWUSR)
        assert "NOT PROVEN" in out, out
        assert "all identities hold" not in out, (
            "a run that audited nothing claimed every identity holds:\n" + out)


class TestTheAlertCarriesTheGap:
    """A gap nobody is told about is the silence this whole fix is about."""

    def test_unauditable_triggers_the_alert(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(afc, "_alert_breaks",
                            lambda rows, today: sent.append((rows, today)))
        bad = _clean_variant(tmp_path, "c")
        os.chmod(bad, 0)
        try:
            afc.main(["--root", str(tmp_path), "--alert"])
        finally:
            os.chmod(bad, stat.S_IRUSR | stat.S_IWUSR)
        assert sent, "nobody was told the audit could not run"
        assert any(t == "UNAUDITABLE" for _n, t, _m in sent[0][0])

    def test_a_clean_run_alerts_nobody(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(afc, "_alert_breaks",
                            lambda rows, today: sent.append(rows))
        _clean_variant(tmp_path, "bl")
        afc.main(["--root", str(tmp_path), "--alert"])
        assert not sent, "a clean audit alerted"


class TestTheRealProductionFailure:
    """The 2026-11-13 crash itself, staged end to end.

    Everything else here uses `chmod 0`, which fails at connect. This class is
    the only one that reproduces a failure arriving at EXECUTE — which is what
    actually happened, and what the probe in `_ro()` exists for. Without this,
    removing the probe left 20 of 21 tests green.
    """

    def test_the_repro_really_does_fail_at_execute_not_connect(self, tmp_path):
        """A control on the fixture: if this stops reproducing, the tests below
        become vacuous and would pass against the original broken code."""
        d, db = _wal_db_without_shm(tmp_path, "bl")
        with _locked_dir(d):
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            with pytest.raises(sqlite3.OperationalError, match="unable to open database"):
                con.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()

    def test_ro_returns_None_instead_of_a_doomed_handle(self, tmp_path):
        d, db = _wal_db_without_shm(tmp_path, "bl")
        with _locked_dir(d):
            assert afc._ro(db) is None, (
                "_ro handed back a handle whose first execute raises — that is "
                "the 2026-11-13 crash")

    def test_the_fleet_run_survives_it(self, tmp_path, capsys):
        """The whole point: the live seat is unauditable, C is still audited."""
        d, _db = _wal_db_without_shm(tmp_path, "bl")
        _clean_variant(tmp_path, "c")
        with _locked_dir(d):
            rc = afc.main(["--root", str(tmp_path)])
            out = capsys.readouterr().out
        assert "UNAUDITABLE" in out, out
        assert "C" in out, f"the crash still aborted the fleet:\n{out}"
        assert rc == 2, f"expected 2 (could not audit), got {rc}"

    def test_it_does_not_masquerade_as_an_identity_break(self, tmp_path, capsys):
        d, _db = _wal_db_without_shm(tmp_path, "bl")
        with _locked_dir(d):
            rc = afc.main(["--root", str(tmp_path)])
            out = capsys.readouterr().out
        assert rc != 1, (
            "an unreadable database still exits 1 — indistinguishable from the "
            "data disagreeing with itself, which is what cost three days")
        assert "BREAKS FOUND" not in out, out


class TestTheSAMEDefectInTheSiblingAudit:
    """`scripts/audit_telemetry_health.py` had both halves of it too.

    Found by checking the other script in the same systemd unit rather than
    stopping at the one that was failing loudly — the lesson of the 2026-10-28
    variant-E incident, where H's VIX-history bug was fixed on H and left in
    place for every other caller, and E then died of it for 18 days.

    Its version was in fact worse:

      * the same `VARIANTS` list, also missing the live seat;
      * `_ro` with NO `exists()` check and NO `try` at all;
      * no per-variant containment in `main()`;
      * and the unit prefixes this check with `-`, so unlike the fleet audit
        its crashes did not even turn the unit red. They were simply absent.
    """

    def test_its_VARIANTS_includes_the_live_seat(self):
        from scripts import audit_telemetry_health as ath
        from shared.strategy_taxonomy import STRATEGIES
        live = {k for k, v in STRATEGIES.items() if v.status == "live"}
        missing = {k for k in live if _norm(k) not in ath.VARIANTS}
        assert not missing, (
            f"live seat(s) {missing} absent from the telemetry audit's VARIANTS "
            f"{ath.VARIANTS} — the live seat's instruments were never checked")

    def test_its_VARIANTS_covers_every_paper_strategy(self):
        from scripts import audit_telemetry_health as ath
        from shared.strategy_taxonomy import PAPER, ids_for_account_kind
        missing = {v for v in ids_for_account_kind(PAPER)
                   if _norm(v) not in ath.VARIANTS}
        assert not missing, missing

    def test_the_two_audits_cover_the_SAME_fleet(self):
        """Two hand-written lists of the same thing drift. Pin them together."""
        from scripts import audit_telemetry_health as ath
        assert set(ath.VARIANTS) == set(afc.VARIANTS), (
            f"the two audits disagree about the fleet: "
            f"telemetry-only={set(ath.VARIANTS) - set(afc.VARIANTS)}, "
            f"fleet-only={set(afc.VARIANTS) - set(ath.VARIANTS)}")

    def test_its_ro_probes_at_open_time(self, tmp_path):
        from scripts import audit_telemetry_health as ath
        d, db = _wal_db_without_shm(tmp_path, "bl")
        with _locked_dir(d):
            assert ath._ro(db) is None

    def test_its_ro_handles_a_missing_file(self, tmp_path):
        from scripts import audit_telemetry_health as ath
        assert ath._ro(tmp_path / "nope.db") is None

    def test_its_ro_still_returns_a_working_connection(self, tmp_path):
        from scripts import audit_telemetry_health as ath
        db = _clean_variant(tmp_path, "bl")
        con = ath._ro(db)
        assert con is not None
        con.execute("SELECT 1 FROM daily_summaries")
        con.close()

    def test_an_unreadable_db_reports_UNAUDITABLE_not_silence(self, tmp_path):
        from scripts import audit_telemetry_health as ath
        d, _db = _wal_db_without_shm(tmp_path, "bl")
        with _locked_dir(d):
            found = ath.audit_variant(tmp_path, "bl", 14)
        assert found, "an unreadable database produced NO findings — which the "\
                      "caller prints as 'ok'"
        assert found[0][0] == "UNAUDITABLE", found

    def test_a_crash_does_not_abort_its_fleet_loop(self, tmp_path, capsys, monkeypatch):
        from scripts import audit_telemetry_health as ath
        real = ath.audit_variant

        def boom(root, vid, days):
            if vid == "bl":
                raise RuntimeError("synthetic explosion")
            return real(root, vid, days)

        monkeypatch.setattr(ath, "audit_variant", boom)
        _clean_variant(tmp_path, "c")
        ath.main(["--root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "UNAUDITABLE" in out and "synthetic explosion" in out, out
        assert "\n  C " in out, f"variants after the raise were skipped:\n{out}"


class TestTheUnitCanActuallyReadAWalDatabase:
    """The code change alone would have left the audit SKIPPING most nights.

    Measured before fixing: 8 of the last 13 nightly runs crashed. Without the
    sandbox exception the honest outcome is "UNAUDITABLE, exit 2" on most of
    those nights — truthful, but still not an audit. Both halves are needed.

    These assertions read the unit FILE, which is the one thing here that
    cannot be executed in-process. `systemd-analyze verify` on the VM is the
    real check and is part of the deploy, not of this suite — so these pin the
    intent and the deploy pins the syntax.
    """

    UNIT = Path(__file__).resolve().parents[1] / "deploy" / "calypso_health_checks.service"

    def test_it_grants_write_access_to_the_data_dir(self):
        body = self.UNIT.read_text()
        assert "ReadWritePaths=/opt/calypso/data" in body, (
            "without this the audit cannot create SQLite's -shm sidecar under "
            "ProtectSystem=strict, which crashed 8 of 13 runs")

    def test_the_grant_is_narrow(self):
        body = self.UNIT.read_text()
        rw = [ln.strip() for ln in body.splitlines()
              if ln.strip().startswith("ReadWritePaths=")]
        assert rw == ["ReadWritePaths=/opt/calypso/data"], (
            f"the sandbox exception widened beyond the data directory: {rw}")
        assert "ProtectSystem=strict" in body, "the sandbox itself was removed"

    def test_it_no_longer_claims_to_write_nothing(self):
        """The description was load-bearing documentation and is now false."""
        body = self.UNIT.read_text()
        assert "writes nothing" not in body, (
            "the unit still advertises 'writes nothing' while holding "
            "ReadWritePaths — the next reader will trust the claim, not the "
            "directive")
