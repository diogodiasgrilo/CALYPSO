"""The epoch archive zeroes SEVEN counters; the self-heal restored FIVE.

Found 2026-10-13 while checking why the new live seat `bl` reported
``total_entries: 0`` against a database holding a real entry worth $125.

``_archive_pre_epoch_metrics`` runs ONCE, at the epoch cutover, and zeroes
cumulative_pnl, total_entries, total_credit_collected, total_stops,
double_stops, winning_days and losing_days.
``_reconcile_cumulative_metrics_from_db`` runs EVERY settlement and re-derived
five of those from the database — but not ``total_entries`` or
``total_credit_collected``. For those two the zeroing was therefore
**permanent**, and the counters silently came to mean "since the archive ran",
a date that appears nowhere in the file and is NOT the epoch.

The docstring justified the exclusion with "no confirmed drift found there".
Measured fleet-wide, epoch-honoured, the day this was written:

    variant   metrics                DB (since epoch)
    B           9 / $3,522.50        123 / $39,887.50     13.7x
    C           3 /   $927.50         77 / $19,962.50     25.7x
    bl          0 /      $0.00          1 /    $125.00
    G          60 / $15,867.70         60 / $15,867.70    exact

G is the control that makes it a mechanism rather than a coincidence: it is the
only 0DTE variant with **no** ``metrics_epoch_date``, was therefore never
archived, and its counters are exact to the cent.

This is the THIRD defect in the "increment once, idempotent-by-date, never
revisited" family (cumulative_pnl 2026-07-20, the stop counters 2026-09-02).
So the test that matters most here is NOT the one that checks the two new
fields — it is ``TestEveryZeroedCounterIsRestored``, which runs a real archive
followed by a real self-heal and asserts that *every* counter the archive
zeroes comes back. That closes the class; a fourth counter added to the
zero-list without a re-derivation fails it.

⚠️ The guard tested in ``TestEmptyTableIsNotAZero`` is load-bearing and is the
one way this fix could have caused a worse bug than it fixed: D, E and H record
their entries in their OWN databases (``dc_calendar.db`` / ``long_strangle.db``)
and have ZERO ``trade_entries`` rows in the database read here, while holding
84, 85 and 14 ``daily_summaries`` days. Re-deriving blind would have destroyed
D's and H's real counts to "fix" variants that were never broken.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from shared.data_recorder import DataRecorder  # noqa: E402


def _strategy(tmp_path, *, daily_rows=(), entry_rows=(), stop_rows=(),
              metrics=None, epoch=None):
    """Real DataRecorder, real SQLite file, real method under test."""
    rec = DataRecorder(str(tmp_path / "bt.db"))
    rec.ensure_schema()
    for r in daily_rows:
        rec.record_daily_summary(r)
    for r in entry_rows:
        rec.record_entry(r)
    for r in stop_rows:
        rec.record_stop(r)
    s = HydraStrategy.__new__(HydraStrategy)
    s._data_recorder = rec
    s.metrics_file = str(tmp_path / "hydra_metrics.json")
    s.cumulative_metrics = metrics if metrics is not None else {}
    s.strategy_config = {"metrics_epoch_date": epoch} if epoch else {}
    return s


def _day(date, net):
    return {"date": date, "gross_pnl": net, "net_pnl": net, "commission": 0}


def _entry(date, n, credit=100.0):
    return {"date": date, "entry_number": n, "total_credit": credit, "contracts": 7}


def _dr(date, net, cap=7000):
    return {"date": date, "net_pnl": net, "capital_deployed": cap}


# ════════════════════════ the reported incident ════════════════════════
class TestTheBlIncident:
    """bl: one real entry worth $125 in the DB, 0 / $0.00 in the metrics file."""

    def test_zeroed_entry_counters_are_restored_from_the_db(self, tmp_path):
        s = _strategy(
            tmp_path,
            daily_rows=[_day("2026-10-12", -246.35)],
            entry_rows=[_entry("2026-10-12", 7, 125.0)],
            metrics={"cumulative_pnl": -246.35, "total_entries": 0,
                     "total_credit_collected": 0.0, "total_stops": 0,
                     "double_stops": 0, "daily_returns": [_dr("2026-10-12", -246.35)]},
            epoch="2026-10-09",
        )
        s._reconcile_cumulative_metrics_from_db("2026-10-12")
        assert s.cumulative_metrics["total_entries"] == 1
        assert s.cumulative_metrics["total_credit_collected"] == pytest.approx(125.0)
        # and it must survive the write to disk, not just live in memory
        saved = json.load(open(s.metrics_file))
        assert saved["total_entries"] == 1
        assert saved["total_credit_collected"] == pytest.approx(125.0)

    def test_the_b_shape_13x_understatement(self, tmp_path):
        """B's real shape: a big post-epoch DB against a counter reset later."""
        entries = [_entry(f"2026-08-{d:02d}", n, 325.0)
                   for d in range(1, 11) for n in range(1, 4)]   # 30 entries
        s = _strategy(
            tmp_path,
            daily_rows=[_day(f"2026-08-{d:02d}", 10.0) for d in range(1, 11)],
            entry_rows=entries,
            metrics={"cumulative_pnl": 100.0, "total_entries": 9,
                     "total_credit_collected": 3522.5, "total_stops": 0,
                     "double_stops": 0,
                     "daily_returns": [_dr(f"2026-08-{d:02d}", 10.0) for d in range(1, 11)]},
            epoch="2026-07-24",
        )
        s._reconcile_cumulative_metrics_from_db("2026-08-10")
        assert s.cumulative_metrics["total_entries"] == 30
        assert s.cumulative_metrics["total_credit_collected"] == pytest.approx(30 * 325.0)

    def test_negative_control_the_old_code_fails_this(self, tmp_path):
        """Proves the assertions above are not vacuous.

        Reproduces the DEFECT by doing exactly what the pre-fix code did —
        reconcile everything EXCEPT the two entry counters — and asserts the
        stale values survive. If someone reverts the fix, the tests above fail;
        if someone makes this test's premise impossible, this one fails."""
        s = _strategy(
            tmp_path,
            daily_rows=[_day("2026-10-12", -246.35)],
            entry_rows=[_entry("2026-10-12", 7, 125.0)],
            metrics={"cumulative_pnl": 0.0, "total_entries": 0,
                     "total_credit_collected": 0.0, "total_stops": 0,
                     "double_stops": 0, "daily_returns": [_dr("2026-10-12", -246.35)]},
            epoch="2026-10-09",
        )
        before = dict(s.cumulative_metrics)
        s._reconcile_cumulative_metrics_from_db("2026-10-12")
        # cumulative_pnl was ALWAYS healed (2026-07-20) — so if this did not
        # move, the harness is wrong and every other assertion here is void.
        assert before["cumulative_pnl"] != s.cumulative_metrics["cumulative_pnl"]
        assert s.cumulative_metrics["cumulative_pnl"] == pytest.approx(-246.35)
        # and the two NEW fields moved in the same call
        assert s.cumulative_metrics["total_entries"] == 1


# ════════════════════════ the load-bearing guard ════════════════════════
class TestEmptyTableIsNotAZero:
    """D/E/H keep entries in another database. 0 rows != 0 entries."""

    def test_an_empty_table_must_not_zero_a_nonzero_count(self, tmp_path):
        s = _strategy(
            tmp_path,
            daily_rows=[_day("2026-10-12", -99.0)],
            entry_rows=[],                      # D/E/H: structurally empty here
            metrics={"cumulative_pnl": -99.0, "total_entries": 16,
                     "total_credit_collected": 0.0, "total_stops": 0,
                     "double_stops": 0, "daily_returns": [_dr("2026-10-12", -99.0)]},
            epoch="2026-07-11",
        )
        s._reconcile_cumulative_metrics_from_db("2026-10-12")
        assert s.cumulative_metrics["total_entries"] == 16, (
            "an absent signal was read as a zero signal — D's and H's real "
            "entry counts would be destroyed")

    def test_a_fresh_variant_at_zero_is_left_at_zero_without_drama(self, tmp_path):
        """0 over 0 is a no-op, and must not be reported as a self-heal."""
        s = _strategy(
            tmp_path,
            daily_rows=[_day("2026-10-12", 0.0)],
            entry_rows=[],
            metrics={"cumulative_pnl": 0.0, "total_entries": 0,
                     "total_credit_collected": 0.0, "total_stops": 0,
                     "double_stops": 0, "daily_returns": []},
        )
        s._reconcile_cumulative_metrics_from_db("2026-10-12")
        assert s.cumulative_metrics["total_entries"] == 0
        assert s.cumulative_metrics["total_credit_collected"] == 0.0

    def test_rows_present_means_the_db_wins_even_when_it_is_lower(self, tmp_path):
        """F's shape: metrics 11 against a DB holding 10. The DB is authoritative
        in BOTH directions — a guard that only ever increased would re-introduce
        F's double-booking (fixed 2026-09-18) as an un-healable overstatement."""
        s = _strategy(
            tmp_path,
            daily_rows=[_day("2026-10-12", 5.0)],
            entry_rows=[_entry("2026-10-12", n, 130.0) for n in range(1, 11)],
            metrics={"cumulative_pnl": 5.0, "total_entries": 11,
                     "total_credit_collected": 1172.5, "total_stops": 0,
                     "double_stops": 0, "daily_returns": [_dr("2026-10-12", 5.0)]},
        )
        s._reconcile_cumulative_metrics_from_db("2026-10-12")
        assert s.cumulative_metrics["total_entries"] == 10
        assert s.cumulative_metrics["total_credit_collected"] == pytest.approx(1300.0)


# ════════════════════════ the epoch must be honoured ════════════════════════
class TestEpochHonoured:
    def test_pre_epoch_entries_are_excluded(self, tmp_path):
        """Counting pre-epoch entries while cumulative_pnl excludes their days
        would describe two different eras in one file — the exact error
        corrected in 82bacf4."""
        s = _strategy(
            tmp_path,
            daily_rows=[_day("2026-07-01", 100.0), _day("2026-08-01", 50.0)],
            entry_rows=[_entry("2026-07-01", 1), _entry("2026-07-01", 2),
                        _entry("2026-08-01", 1)],
            metrics={"cumulative_pnl": 0.0, "total_entries": 0,
                     "total_credit_collected": 0.0, "total_stops": 0,
                     "double_stops": 0, "daily_returns": [_dr("2026-08-01", 50.0)]},
            epoch="2026-07-24",
        )
        s._reconcile_cumulative_metrics_from_db("2026-08-01")
        assert s.cumulative_metrics["total_entries"] == 1, "pre-epoch entries leaked in"
        assert s.cumulative_metrics["total_credit_collected"] == pytest.approx(100.0)

    def test_without_an_epoch_the_whole_history_counts(self, tmp_path):
        s = _strategy(
            tmp_path,
            daily_rows=[_day("2026-07-01", 100.0), _day("2026-08-01", 50.0)],
            entry_rows=[_entry("2026-07-01", 1), _entry("2026-07-01", 2),
                        _entry("2026-08-01", 1)],
            metrics={"cumulative_pnl": 0.0, "total_entries": 0,
                     "total_credit_collected": 0.0, "total_stops": 0,
                     "double_stops": 0, "daily_returns": []},
        )
        s._reconcile_cumulative_metrics_from_db("2026-08-01")
        assert s.cumulative_metrics["total_entries"] == 3


# ════════════════════ the class-closing test ════════════════════
ZEROED_BY_THE_ARCHIVE = ("cumulative_pnl", "total_entries", "total_credit_collected",
                         "total_stops", "double_stops", "winning_days", "losing_days")


class TestEveryZeroedCounterIsRestored:
    """THE test. Runs a real archive, then a real self-heal, and requires every
    counter the archive zeroes to come back correct. This is what makes the fix
    a closed class instead of a third instance."""

    def _run_full_cycle(self, tmp_path):
        epoch = "2026-07-24"
        # 4 pre-epoch entries over 2 days, 3 post-epoch entries over 2 days
        entry_rows = [_entry("2026-07-01", 1), _entry("2026-07-01", 2),
                      _entry("2026-07-02", 1), _entry("2026-07-02", 2),
                      _entry("2026-07-25", 1), _entry("2026-07-25", 2),
                      _entry("2026-07-26", 1)]
        s = _strategy(
            tmp_path,
            daily_rows=[_day("2026-07-01", 100.0), _day("2026-07-02", 200.0),
                        _day("2026-07-25", -50.0), _day("2026-07-26", 75.0)],
            entry_rows=entry_rows,
            stop_rows=[{"date": "2026-07-25", "entry_number": 1, "side": "put",
                        "net_pnl": -50.0, "exit_reason": "stop_loss"}],
            metrics={"cumulative_pnl": 325.0, "total_entries": 7,
                     "total_credit_collected": 700.0, "total_stops": 4,
                     "double_stops": 1, "winning_days": 3, "losing_days": 1,
                     "daily_returns": [_dr("2026-07-01", 100.0), _dr("2026-07-02", 200.0),
                                       _dr("2026-07-25", -50.0), _dr("2026-07-26", 75.0)]},
            epoch=epoch,
        )
        s._archive_pre_epoch_metrics()
        # the archive really did zero them (otherwise the test proves nothing)
        assert all(not s.cumulative_metrics[k] for k in ZEROED_BY_THE_ARCHIVE), \
            "the archive no longer zeroes these — this test's premise is gone"
        s._reconcile_cumulative_metrics_from_db("2026-07-26")
        return s

    def test_no_zeroed_counter_is_left_at_zero_after_the_next_settlement(self, tmp_path):
        s = self._run_full_cycle(tmp_path)
        cm = s.cumulative_metrics
        # post-epoch truth: 2 days (-50, +75) = +25, 3 entries, $300, 1 stop, 1 win, 1 loss
        assert cm["cumulative_pnl"] == pytest.approx(25.0)
        assert cm["total_entries"] == 3
        assert cm["total_credit_collected"] == pytest.approx(300.0)
        assert cm["total_stops"] == 1
        assert cm["winning_days"] == 1
        assert cm["losing_days"] == 1
        # double_stops is legitimately 0 here (only one side stopped) — assert it
        # was RE-DERIVED rather than left at the archive's zero by checking the
        # one that cannot be confused: it must not still hold the stale 1.
        assert cm["double_stops"] == 0

    def test_the_two_formerly_unrestored_counters_are_the_regression_target(self, tmp_path):
        """Named explicitly so a revert fails loudly on the exact fields."""
        s = self._run_full_cycle(tmp_path)
        assert s.cumulative_metrics["total_entries"] != 0
        assert s.cumulative_metrics["total_credit_collected"] != 0.0

    def test_the_archive_preserves_the_credit_it_zeroes(self, tmp_path):
        """It was in the zero-list but not the archive block, so it was the one
        counter DESTROYED at every cutover — against the function's own promise
        that the earlier era is 'preserved rather than deleted'."""
        s = self._run_full_cycle(tmp_path)
        pre = s.cumulative_metrics["pre_epoch"]
        assert "total_credit_collected" in pre, "the credit history vanished"
        assert pre["total_credit_collected"] == pytest.approx(700.0)


# ════════════════════ the same-day recording-gap detector ════════════════════
class TestRecordingGapDetector:
    """Added 2026-10-13 alongside the heal, because the heal removed the only
    (weak) signal that a trade_entries row had gone missing: before this,
    metrics-vs-DB divergence was a hint; now they agree by construction.

    Every instance found was historical or structural — A 2026-03-20 (5 vs 3)
    and 2026-04-09 (3 vs 2), both Saxo-era; F 2026-09-14 (1 vs 0), pre
    accounting fix; B 2026-06-05 (4 vs 0), a HOMER-backfilled day where every
    other table including market_ticks is empty too. All measured eras are
    clean, so this is defence in depth.
    """

    def test_warns_when_a_row_is_missing_for_today(self, tmp_path, caplog):
        import logging
        s = _strategy(
            tmp_path,
            # summary claims 3 placed; only 2 rows exist
            daily_rows=[{"date": "2026-10-13", "gross_pnl": 0.0, "net_pnl": 0.0,
                         "commission": 0, "entries_placed": 3}],
            entry_rows=[_entry("2026-10-13", 1), _entry("2026-10-13", 2)],
            metrics={"cumulative_pnl": 0.0, "total_entries": 2,
                     "total_credit_collected": 200.0, "total_stops": 0,
                     "double_stops": 0, "daily_returns": [_dr("2026-10-13", 0.0)]},
        )
        with caplog.at_level(logging.WARNING):
            s._reconcile_cumulative_metrics_from_db("2026-10-13")
        assert "RECORDING-GAP" in caplog.text
        assert "3 entr(ies) placed" in caplog.text and "2 row(s)" in caplog.text

    def test_silent_when_the_day_is_consistent(self, tmp_path, caplog):
        import logging
        s = _strategy(
            tmp_path,
            daily_rows=[{"date": "2026-10-13", "gross_pnl": 0.0, "net_pnl": 0.0,
                         "commission": 0, "entries_placed": 2}],
            entry_rows=[_entry("2026-10-13", 1), _entry("2026-10-13", 2)],
            metrics={"cumulative_pnl": 0.0, "total_entries": 2,
                     "total_credit_collected": 200.0, "total_stops": 0,
                     "double_stops": 0, "daily_returns": [_dr("2026-10-13", 0.0)]},
        )
        with caplog.at_level(logging.WARNING):
            s._reconcile_cumulative_metrics_from_db("2026-10-13")
        assert "RECORDING-GAP" not in caplog.text

    def test_silent_for_a_variant_that_records_elsewhere(self, tmp_path, caplog):
        """D/E/H hold ZERO trade_entries rows. Without the gate they would warn
        at EVERY settlement, which is how a real warning gets ignored."""
        import logging
        s = _strategy(
            tmp_path,
            daily_rows=[{"date": "2026-10-13", "gross_pnl": 0.0, "net_pnl": 0.0,
                         "commission": 0, "entries_placed": 1}],
            entry_rows=[],                       # different recorder
            metrics={"cumulative_pnl": 0.0, "total_entries": 16,
                     "total_credit_collected": 0.0, "total_stops": 0,
                     "double_stops": 0, "daily_returns": [_dr("2026-10-13", 0.0)]},
        )
        with caplog.at_level(logging.WARNING):
            s._reconcile_cumulative_metrics_from_db("2026-10-13")
        assert "RECORDING-GAP" not in caplog.text

    def test_a_historical_gap_on_another_day_does_not_warn_today(self, tmp_path, caplog):
        """TODAY only. An era-wide check would warn every settlement forever
        over A's Saxo-era days, which nobody can ever clear."""
        import logging
        s = _strategy(
            tmp_path,
            daily_rows=[{"date": "2026-03-20", "gross_pnl": 0.0, "net_pnl": 0.0,
                         "commission": 0, "entries_placed": 5},
                        {"date": "2026-10-13", "gross_pnl": 0.0, "net_pnl": 0.0,
                         "commission": 0, "entries_placed": 1}],
            entry_rows=[_entry("2026-03-20", 1), _entry("2026-03-20", 2),
                        _entry("2026-03-20", 3), _entry("2026-10-13", 1)],
            metrics={"cumulative_pnl": 0.0, "total_entries": 4,
                     "total_credit_collected": 400.0, "total_stops": 0,
                     "double_stops": 0, "daily_returns": [_dr("2026-10-13", 0.0)]},
        )
        with caplog.at_level(logging.WARNING):
            s._reconcile_cumulative_metrics_from_db("2026-10-13")
        assert "RECORDING-GAP" not in caplog.text, (
            "fired on a historical gap — this must check TODAY only")
