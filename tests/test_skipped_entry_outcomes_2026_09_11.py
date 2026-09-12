"""
Counterfactual outcomes for skipped entries (2026-09-11).

Answers the long-open GEX question — *the adjuster vetoed more entries than it
placed; was that worth it?* — from data now being recorded (commit ec71967).

THE DISCIPLINE THIS FILE ENFORCES is the separation of MEASURED from MODELLED,
because collapsing them is how a plausible number becomes a decision:

  MEASURED: did SPX breach a proposed short strike AFTER the skip time.
            Call breached if max(SPX after skip) >= short_call.
            Put  breached if min(SPX after skip) <= short_put.
  MODELLED: the dollars. Unbreached -> keep the estimated credit (near-measured).
            Breached -> loss modelled as B's acting A2 stop,
            pct_of_width x width x 100 x contracts. The REAL loss depends on when
            the stop fired and what the spread cost to close, neither of which
            exists for a trade never taken.

Two traps specifically tested:

  * **The window must start at the SKIP TIME.** What SPX did EARLIER in the
    session cannot breach a position that would have been opened later. Using
    the whole day would inflate the breach rate and make the veto look better
    than it was — i.e. bias the answer toward the conclusion we already lean to.
  * **A missing strike is UNMEASURABLE, not "no breach".** Scoring it as
    unbreached would silently count all 95 historical vetoes as wins.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.analyze_skipped_entry_outcomes import evaluate  # noqa: E402

K = dict(pct_of_width=0.40, contracts=7)


def _row(sc=7650, lc=7655, sp=7550, lp=7545, cc=1.0, pc=1.0):
    return {
        "date": "2026-09-11", "entry_number": 1, "skip_time": "2026-09-11 10:45:00",
        "theoretical_short_call": sc, "theoretical_long_call": lc,
        "theoretical_short_put": sp, "theoretical_long_put": lp,
        "estimated_call_credit": cc, "estimated_put_credit": pc,
    }


class TestBreachDetectionIsMeasured:
    def test_no_breach_when_spx_stayed_inside(self):
        r = evaluate(_row(), lo=7580, hi=7620, **K)
        assert r["breached"] is False
        assert r["call_breach"] is False and r["put_breach"] is False

    def test_call_breach_when_spx_reached_the_short_call(self):
        r = evaluate(_row(sc=7650), lo=7600, hi=7650, **K)
        assert r["call_breach"] is True and r["breached"] is True

    def test_put_breach_when_spx_reached_the_short_put(self):
        r = evaluate(_row(sp=7550), lo=7550, hi=7600, **K)
        assert r["put_breach"] is True and r["breached"] is True

    def test_touching_the_strike_counts_as_a_breach(self):
        """>= and <=, not > and <. A short AT the money is in trouble."""
        assert evaluate(_row(sc=7650), lo=7600, hi=7650.0, **K)["call_breach"]
        assert evaluate(_row(sp=7550), lo=7550.0, hi=7600, **K)["put_breach"]

    def test_one_tick_short_of_the_strike_is_not_a_breach(self):
        assert not evaluate(_row(sc=7650), lo=7600, hi=7649.99, **K)["call_breach"]

    def test_both_sides_can_breach(self):
        r = evaluate(_row(), lo=7540, hi=7660, **K)
        assert r["call_breach"] and r["put_breach"]


class TestAMissingStrikeIsUnmeasurableNotAWin:
    def test_no_strikes_at_all_returns_None(self):
        """The 95 historical vetoes. Scoring them as 'no breach' would count
        every one as a win and invert the conclusion."""
        assert evaluate(_row(sc=None, sp=None), lo=7500, hi=7700, **K) is None

    def test_a_one_sided_row_is_still_measurable(self):
        """The GEX adjuster zeroes the side it drops, so most rows carry only the
        SURVIVING side — which is the side that would have been placed."""
        r = evaluate(_row(sc=None, lc=None), lo=7540, hi=7600, **K)
        assert r is not None
        assert r["put_breach"] is True
        assert r["call_breach"] is False   # absent, not breached

    def test_missing_tick_coverage_returns_None(self):
        """No ticks after the skip means no evidence — not 'no breach'."""
        assert evaluate(_row(), lo=None, hi=None, **K) is None


class TestTheModelledDollarsAreLabelledAndSane:
    def test_unbreached_keeps_the_estimated_credit(self):
        r = evaluate(_row(cc=1.0, pc=1.0), lo=7580, hi=7620, **K)
        assert r["modelled_pnl"] == pytest.approx((1.0 + 1.0) * 100 * 7)

    def test_breached_models_the_loss_as_the_A2_stop(self):
        """5pt wings, 40%, 7c -> -(0.40 x 5 x 100 x 7) = -1400."""
        r = evaluate(_row(sc=7650, lc=7655), lo=7600, hi=7660, **K)
        assert r["modelled_pnl"] == pytest.approx(-(0.40 * 5 * 100 * 7))

    def test_the_loss_scales_with_the_stop_fraction(self):
        a = evaluate(_row(), lo=7600, hi=7660, pct_of_width=0.40, contracts=7)
        b = evaluate(_row(), lo=7600, hi=7660, pct_of_width=0.25, contracts=7)
        assert b["modelled_pnl"] > a["modelled_pnl"]   # smaller stop, smaller loss

    def test_no_width_means_no_modelled_loss_rather_than_zero(self):
        """A zero loss would read as a breakeven breach — strictly better than
        reality. None says 'not modellable'."""
        r = evaluate(_row(sc=7650, lc=None, sp=None, lp=None), lo=7600, hi=7660, **K)
        assert r["breached"] is True
        assert r["modelled_pnl"] is None

    def test_the_measured_and_modelled_fields_stay_separate(self):
        """Both are returned so a caller can report the breach rate without
        inheriting the model's assumptions."""
        r = evaluate(_row(), lo=7580, hi=7620, **K)
        assert set(("breached", "modelled_pnl", "credit")) <= set(r)


class TestTheWindowStartsAtTheSkip:
    """Regression guard for the subtlest bias available here."""

    def test_the_query_is_bounded_below_by_skip_time(self):
        src = (Path(__file__).resolve().parents[1]
               / "scripts" / "analyze_skipped_entry_outcomes.py").read_text()
        assert "timestamp >= ?" in src, (
            "the SPX extremes must be taken from the SKIP TIME forward — using "
            "the whole day would let a pre-skip move count as a breach and "
            "inflate the veto's apparent value"
        )

    def test_it_states_the_historical_set_is_unrecoverable(self):
        src = (Path(__file__).resolve().parents[1]
               / "scripts" / "analyze_skipped_entry_outcomes.py").read_text()
        assert "NOT RECOVERABLE" in src or "never will" in src

    def test_it_warns_on_a_small_sample(self):
        src = (Path(__file__).resolve().parents[1]
               / "scripts" / "analyze_skipped_entry_outcomes.py").read_text()
        assert "Directional at best" in src


class TestPersistingTheResult:
    """`--apply` gives DataRecorder.update_skipped_entry_backtest its caller.

    Until 2026-09-12 that method had ZERO callers repo-wide, so
    `would_have_stopped` / `theoretical_pnl` were structurally empty (198 rows,
    0 populated) and any outcome computed here lived only in stdout.

    The caller is HERE and not in the bot's settlement path, deliberately: both
    inputs are persisted, so the computation is not time-sensitive and putting it
    in the trading process would add risk for no benefit.
    """

    SRC = (Path(__file__).resolve().parents[1]
           / "scripts" / "analyze_skipped_entry_outcomes.py").read_text()

    def test_it_calls_the_previously_dead_writer(self):
        assert "update_skipped_entry_backtest" in self.SRC

    def test_it_is_DRY_RUN_by_default(self):
        """Writing to the live DB must be opt-in — this is the same discipline
        as scripts/backfill_overlay_residual.py."""
        assert '"--apply"' in self.SRC
        assert "DRY RUN — nothing written" in self.SRC

    def test_it_backs_the_database_up_before_writing(self):
        i = self.SRC.index("safe_db_backup(db,")
        j = self.SRC.index("rec.update_skipped_entry_backtest(")
        assert i < j, "the backup must happen BEFORE any write"

    def test_the_backup_goes_through_the_SHARED_helper(self):
        """The backup used to be inline here, and it was wrong in three ways —
        a non-WAL-safe file copy, an un-timestamped name, then a colliding
        second-granularity one. Two OTHER scripts had the same bugs. The WAL
        safety, the millisecond stamp and the never-overwrite guard are now
        properties of shared/db_backup.py and are pinned by
        tests/test_db_backup_2026_09_12.py; what this file pins is that the
        script actually delegates rather than growing a fourth copy."""
        assert "from shared.db_backup import safe_db_backup" in self.SRC
        assert "shutil.copy2(" not in self.SRC

    def test_it_records_the_MODEL_used(self):
        """theoretical_pnl is modelled, not measured. A reader of that column
        needs to know which stop assumption produced it."""
        assert "pct_of_width:.0%}-of-width stop at" in self.SRC

    def test_it_reports_failed_writes_rather_than_assuming_success(self):
        """DataRecorder swallows write errors by design so the trading loop is
        never affected — which means a silent failure is the default unless the
        caller counts."""
        assert "FAILED" in self.SRC
        assert "swallows write" in self.SRC

    def test_the_writer_signature_is_what_we_pass(self):
        """Introspected, not assumed — a wrong arg order here would silently
        write the breach flag into the P&L column."""
        import inspect
        from shared.data_recorder import DataRecorder
        params = list(inspect.signature(
            DataRecorder.update_skipped_entry_backtest).parameters)
        assert params == ["self", "date_str", "entry_number",
                          "would_have_stopped", "theoretical_pnl"]
        # ORDER, not mere presence: a test that only checked both args appeared
        # let an argument-swap mutant live, and a swap writes the breach flag into
        # theoretical_pnl and the dollars into would_have_stopped. Behaviour is
        # pinned by TestTheWriteActuallyLands below; this pins the call shape.
        i = self.SRC.index("rec.update_skipped_entry_backtest(")
        call = self.SRC[i:i + 200]
        assert call.index('m["date"]') < call.index('m["entry"]') \
            < call.index('bool(m["breached"])') < call.index('float(m["modelled_pnl"])')


class TestTheWriteActuallyLands:
    """Behavioural, against a real database — source-grep tests let two mutants
    live here (swapped writer arguments, and a deleted 'is not modellable'
    filter whose substring happened to appear elsewhere in the file). These run
    the script end to end and read the rows back."""

    @staticmethod
    def _db(tmp_path, rows):
        """A throwaway DB with `rows` = (entry_no, short_call, call_credit).
        SPX runs 7580..7650 after the skip, with a PRE-skip spike to 7750 that
        must never count."""
        import sqlite3
        from shared.data_recorder import DataRecorder
        db = str(tmp_path / "t.db")
        assert DataRecorder(db).ensure_schema()
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        D = "2026-09-11"
        for n, sc, cc in rows:
            con.execute(
                "INSERT INTO skipped_entries (date, entry_number, skip_time, "
                "skip_reason, spx_at_skip, vix_at_skip, theoretical_short_call, "
                "theoretical_long_call, estimated_call_credit, estimated_put_credit) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (D, n, f"{D} 10:45:00", "gex accel zone", 7600.0, 15.0,
                 sc, (sc + 5) if sc else None, cc, 0.0))
        for ts, px in ((f"{D} 09:50:00", 7750.0), (f"{D} 11:00:00", 7580.0),
                       (f"{D} 12:00:00", 7650.0)):
            con.execute("INSERT INTO market_ticks (timestamp, spx_price) VALUES (?,?)",
                        (ts, px))
        con.commit()
        con.close()
        return db

    @staticmethod
    def _read(db):
        import sqlite3
        con = sqlite3.connect(db)
        try:
            return con.execute(
                "SELECT entry_number, would_have_stopped, theoretical_pnl "
                "FROM skipped_entries ORDER BY entry_number").fetchall()
        finally:
            con.close()

    def _run(self, db, *extra):
        from scripts.analyze_skipped_entry_outcomes import main
        assert main(["--db", db, "--since", "2026-01-01", *extra]) == 0

    def test_apply_writes_the_flag_and_the_dollars_to_the_RIGHT_columns(self, tmp_path):
        """Kills the swapped-argument mutant. Entry 1's 7700 call is never
        touched (credit 1.00 x 100 x 7 = 700 kept); entry 2's 7620 call is
        breached by the 7650 high (-(0.40 x 5 x 100 x 7) = -1400)."""
        db = self._db(tmp_path, [(1, 7700, 1.0), (2, 7620, 0.5)])
        self._run(db, "--apply")
        assert self._read(db) == [(1, 0, 700.0), (2, 1, -1400.0)]

    def test_the_pre_skip_spike_does_not_count_as_a_breach(self, tmp_path):
        """The 7750 tick precedes the 10:45 skip, so it cannot breach a position
        that would have been opened later. Entry 1 must read unbreached."""
        db = self._db(tmp_path, [(1, 7700, 1.0)])
        self._run(db, "--apply")
        assert self._read(db)[0][1] == 0

    def test_a_dry_run_writes_nothing(self, tmp_path):
        db = self._db(tmp_path, [(1, 7700, 1.0), (2, 7620, 0.5)])
        self._run(db)
        assert self._read(db) == [(1, None, None), (2, None, None)]

    def test_an_unmodellable_row_is_left_NULL_not_zero(self, tmp_path):
        """Kills the deleted-filter mutant. A breached row with no long strike
        has no modellable loss; writing 0.0 would read as a breakeven breach —
        strictly better than reality — and would silently flatter the veto."""
        import sqlite3
        db = self._db(tmp_path, [(1, 7620, 0.5)])
        con = sqlite3.connect(db)
        con.execute("UPDATE skipped_entries SET theoretical_long_call = NULL")
        con.commit()
        con.close()
        self._run(db, "--apply")
        assert self._read(db) == [(1, None, None)]

    def test_it_is_idempotent(self, tmp_path):
        """Re-running must converge, not accumulate — the rows carry the same
        values after a second pass."""
        db = self._db(tmp_path, [(1, 7700, 1.0), (2, 7620, 0.5)])
        self._run(db, "--apply")
        first = self._read(db)
        self._run(db, "--apply")
        assert self._read(db) == first

    def test_each_run_keeps_its_OWN_backup_of_the_pre_write_state(self, tmp_path):
        """Un-timestamped, the second run's backup overwrote the first with a copy
        of the already-modified DB — destroying the original exactly when it
        would be wanted."""
        import glob
        import sqlite3
        db = self._db(tmp_path, [(1, 7700, 1.0)])
        self._run(db, "--apply")
        self._run(db, "--apply")
        backups = sorted(glob.glob(db + ".pre_skip_backfill_*"))
        assert len(backups) == 2
        con = sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
        try:
            written = con.execute("SELECT COUNT(*) FROM skipped_entries "
                                  "WHERE theoretical_pnl IS NOT NULL").fetchone()[0]
            total = con.execute("SELECT COUNT(*) FROM skipped_entries").fetchone()[0]
        finally:
            con.close()
        assert (total, written) == (1, 0), "the first backup must predate every write"
