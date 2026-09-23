"""A percentile must not out-rank the sample it was computed from.

Found 2026-09-23 while answering the operator's question "are we free of bugs?"
— which is the honest answer's own evidence that the answer was no.

**THE DEFECT.** ``iv_percentile()`` had no minimum-sample guard. One prior day of
history returns 0.0 or 100.0: a value with the exact shape of a percentile, the
full authority of one, and no information in it at all. The entry gate then
compares that to the source's "< ~35%" threshold and decides whether variant H
trades. Nothing looks broken from the outside, which is the property that makes
it dangerous rather than merely wrong.

**THE SECOND HALF.** H's config asks for ``iv_percentile_lookback_days: 252``
while the live-seat database holds ~94 days, so the gate was applying a
four-month percentile wearing a one-year label — and nothing recorded the sample
size, so the stored ``iv_percentile`` could never be interpreted afterwards. The
value and its ``n`` are now stored together, in ``ls_skipped`` AND in
``ls_entries``, which previously carried no IV column at all: only the VETOED
side was recorded, making the 35% line untestable against its own outcomes.

Every test here CALLS the code. None of them grep it — see the memory
`feedback_source_grep_tests_are_blind`.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.long_strangle_chain import (  # noqa: E402
    iv_percentile,
    iv_percentile_with_n,
)
from bots.hydra.ls_recorder import LongStrangleDataRecorder  # noqa: E402

H_CFG = json.loads(
    (ROOT / "bots" / "hydra" / "config" / "config_variant_h.json").read_text()
)["strategy"]["long_strangle"]


class TestThePercentileRefusesAnUnderpoweredSample:

    @pytest.mark.parametrize("n", [1, 2, 5, 59])
    def test_below_the_floor_it_returns_unknown_not_a_number(self, n):
        """None means "unknown", and the gate treats unknown as a SKIP. The
        alternative is a confident-looking 0.0 that admits every entry."""
        assert iv_percentile(15.0, [14.0] * n, min_history=60) is None

    def test_at_the_floor_it_computes(self):
        assert iv_percentile(15.0, [14.0] * 60, min_history=60) == pytest.approx(100.0)

    def test_the_one_day_case_that_started_this(self):
        """A single prior day. Ungated, this returns a clean 0.0 — comfortably
        under the source's 35% threshold, so H would have entered on the
        strength of one observation."""
        assert iv_percentile(10.0, [20.0], min_history=1) == 0.0   # the old behaviour
        assert iv_percentile(10.0, [20.0], min_history=60) is None  # the fix

    def test_the_default_stays_permissive_for_other_callers(self):
        """The floor is opt-in: `min_history` defaults to 1 so this helper's
        other uses are unchanged. The GATE passes the config value."""
        assert iv_percentile(10.0, [20.0]) == 0.0

    def test_zero_and_negative_readings_never_count_toward_the_sample(self):
        """A DB gap writes 0.0, not NULL. Those must not pad the sample up to
        the floor — that would defeat the guard with missing data."""
        assert iv_percentile(15.0, [0.0] * 100, min_history=60) is None
        assert iv_percentile_with_n(15.0, [0.0] * 100)[1] == 0


class TestTheSampleSizeIsReportedWithTheValue:
    """A stored percentile is uninterpretable alone: one year and three days
    produce the same shape."""

    def test_it_returns_both(self):
        pct, n = iv_percentile_with_n(15.0, [14.0, 16.0, 13.0, 17.0])
        assert pct == pytest.approx(50.0)
        assert n == 4

    def test_n_counts_the_USABLE_series_not_the_raw_rows(self):
        pct, n = iv_percentile_with_n(15.0, [14.0, 0.0, None, 16.0], min_history=1)
        assert n == 2
        assert pct == pytest.approx(50.0)

    def test_an_unusable_sample_still_reports_its_size(self):
        """The skip reason quotes `n`, so it has to survive the None."""
        pct, n = iv_percentile_with_n(15.0, [14.0] * 3, min_history=60)
        assert pct is None and n == 3

    def test_the_real_94_day_situation_is_computable_and_labelled_short(self):
        """94 days against a 252-day lookback: passes the 60-day floor, and the
        caller can see it is a partial window because n < lookback."""
        hist = [14.0 + (i % 7) * 0.4 for i in range(94)]
        pct, n = iv_percentile_with_n(14.9, hist, min_history=60)
        assert pct is not None
        assert n == 94 < int(H_CFG["iv_percentile_lookback_days"])


class TestTheShippedConfigIsCoherent:

    def test_the_floor_exists_and_is_below_the_lookback(self):
        floor = int(H_CFG["iv_percentile_min_history_days"])
        want = int(H_CFG["iv_percentile_lookback_days"])
        assert 0 < floor <= want, (
            "a floor above the lookback could never be satisfied — H would "
            "skip every entry forever")

    def test_the_floor_is_large_enough_to_mean_something(self):
        assert int(H_CFG["iv_percentile_min_history_days"]) >= 30

    def test_the_knob_carries_its_reasoning(self):
        raw = (ROOT / "bots" / "hydra" / "config" / "config_variant_h.json").read_text()
        assert "_comment_iv_percentile_min_history_days" in raw


class TestTheRecorderKeepsTheReadingAndItsSample:

    def _rec(self, tmp_path):
        return LongStrangleDataRecorder(str(tmp_path / "long_strangle.db"))

    def test_a_skipped_entry_stores_both(self, tmp_path):
        r = self._rec(tmp_path)
        r.record_skip("2026-09-24", 1, "09:45:00", "too rich",
                      iv_percentile=41.0, iv_percentile_n=94)
        row = sqlite3.connect(r.db_path).execute(
            "SELECT iv_percentile, iv_percentile_n FROM ls_skipped").fetchone()
        assert row == (41.0, 94)

    def test_a_PLACED_entry_stores_both(self, tmp_path):
        """`ls_entries` had no IV column at all before v2, so the threshold
        could only ever be judged from the entries it REJECTED."""
        from bots.hydra.long_strangle_entry import LongStrangleEntry
        r = self._rec(tmp_path)
        e = LongStrangleEntry(entry_number=1)
        e.long_call_strike, e.long_put_strike, e.contracts = 7760.0, 7720.0, 1
        r.record_entry(e, date="2026-09-24", spx_at_entry=7740.0,
                       iv_percentile=11.9, iv_percentile_n=94)
        row = sqlite3.connect(r.db_path).execute(
            "SELECT iv_percentile, iv_percentile_n FROM ls_entries").fetchone()
        assert row == (11.9, 94)

    def test_an_EXISTING_v1_database_gains_the_columns(self, tmp_path):
        """THE MIGRATION TEST. `CREATE TABLE IF NOT EXISTS` is a no-op on a
        database that already exists, so a new column in the schema reaches a
        fresh DB and never a live one — and H's was created 2026-09-23 and
        already holds a row. Without the ALTER, the shipped code would write to
        columns that do not exist on the only database that matters."""
        db = tmp_path / "long_strangle.db"
        con = sqlite3.connect(db)
        con.executescript(
            "CREATE TABLE ls_entries (date TEXT, entry_number INTEGER,"
            " PRIMARY KEY (date, entry_number));"
            "CREATE TABLE ls_skipped (date TEXT, entry_number INTEGER,"
            " iv_percentile REAL);"
            "CREATE TABLE ls_schema_info (version INTEGER);"
            "INSERT INTO ls_schema_info VALUES (1);"
            "INSERT INTO ls_skipped VALUES ('2026-09-23', 1, 11.9);"
        )
        con.commit(); con.close()

        LongStrangleDataRecorder(str(db))   # opening it runs the migration

        con = sqlite3.connect(db)
        for table in ("ls_entries", "ls_skipped"):
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            assert "iv_percentile_n" in cols, table
        assert "iv_percentile" in {
            r[1] for r in con.execute("PRAGMA table_info(ls_entries)")}
        # The pre-existing observation survives, and reads NULL rather than 0 —
        # "not measured then" must stay distinguishable from "measured as zero".
        assert con.execute(
            "SELECT iv_percentile, iv_percentile_n FROM ls_skipped").fetchone() == (11.9, None)
        assert con.execute("SELECT version FROM ls_schema_info").fetchone()[0] == 2

    def test_the_migration_is_idempotent(self, tmp_path):
        db = str(tmp_path / "long_strangle.db")
        LongStrangleDataRecorder(db)
        LongStrangleDataRecorder(db)
        LongStrangleDataRecorder(db)
        cols = [r[1] for r in sqlite3.connect(db).execute(
            "PRAGMA table_info(ls_skipped)")]
        assert cols.count("iv_percentile_n") == 1

    def test_every_added_column_is_listed_for_migration(self, tmp_path):
        """DERIVED. A column added to the CREATE statement but forgotten in
        `_ADDED_COLUMNS` reaches fresh databases only — the exact silent
        divergence this class exists to prevent. Compares a FRESH schema against
        a v1-shaped one and requires every difference to be declared."""
        fresh = str(tmp_path / "fresh.db")
        LongStrangleDataRecorder(fresh)
        con = sqlite3.connect(fresh)
        declared = {(t, c) for t, c, _ in LongStrangleDataRecorder._ADDED_COLUMNS}
        v1 = {
            "ls_entries": {
                "date", "entry_number", "entry_time", "spx_at_entry", "vix_at_entry",
                "expiry", "call_strike", "put_strike", "call_debit", "put_debit",
                "total_debit", "contracts", "long_call_uic", "long_put_uic",
                "em_source", "expected_move", "call_premium_mid", "put_premium_mid",
                "skew_gap_pct"},
            "ls_skipped": {
                "date", "entry_number", "skip_time", "skip_reason", "spx", "vix",
                "proposed_call_strike", "proposed_put_strike", "proposed_debit",
                "em_source", "expected_move", "iv_percentile", "skew_gap_pct"},
        }
        for table, original in v1.items():
            now = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            for col in now - original:
                assert (table, col) in declared, (
                    f"{table}.{col} is in the schema but not in _ADDED_COLUMNS — "
                    f"it will never reach an existing database")


class TestTheGateActuallySkipsOnAnUnderpoweredSample:
    """End-to-end through `_iv_percentile_gate`, because the floor only matters
    if the GATE honours it. The helper returning None is necessary, not
    sufficient — a caller treating None as 'pass' would restore the whole bug."""

    def _strategy(self, history, vix=14.89, **cfg_over):
        from bots.hydra.long_strangle_strategy import LongStrangleStrategy
        s = LongStrangleStrategy.__new__(LongStrangleStrategy)
        cfg = {
            "iv_percentile_filter_enabled": True,
            "iv_percentile_source": "vix",
            "iv_percentile_lookback_days": 252,
            "iv_percentile_min_history_days": 60,
            "iv_percentile_max": 35,
            "profit_target_pct_of_debit": 50,
            "profit_target_pct_of_debit_iv_expanding": 100,
        }
        cfg.update(cfg_over)
        s._ls_config = lambda: cfg
        s._vix_history_for_percentile = lambda: history
        s.current_vix = vix
        return s

    def _entry(self):
        from bots.hydra.long_strangle_entry import LongStrangleEntry
        return LongStrangleEntry(entry_number=1)

    def test_three_days_of_history_SKIPS(self):
        s, e = self._strategy([14.0, 15.0, 16.0]), self._entry()
        reason = s._iv_percentile_gate(e)
        assert reason is not None, "an unknown percentile must never pass the gate"
        assert "below the 60-day minimum" in reason

    def test_the_skip_reason_states_the_sample_it_had(self):
        s, e = self._strategy([14.0] * 7), self._entry()
        assert "7 prior days" in s._iv_percentile_gate(e)

    def test_ninety_four_days_PASSES_and_records_the_short_window(self):
        """The real situation. It should trade — and the row must show the
        percentile came from 94 days, not the 252 the config requested."""
        hist = [14.0 + (i % 7) * 0.4 for i in range(94)]
        s, e = self._strategy(hist, vix=14.0), self._entry()
        assert s._iv_percentile_gate(e) is None
        assert e.ls_iv_percentile_n == 94
        assert e.ls_iv_percentile is not None

    def test_a_rejection_names_the_window_actually_used(self):
        hist = [14.0 + (i % 7) * 0.4 for i in range(94)]
        s, e = self._strategy(hist, vix=99.0), self._entry()   # far above the max
        reason = s._iv_percentile_gate(e)
        assert "94d of 252d requested" in reason
        assert "NOT an option-IV percentile" in reason

    def test_the_sample_size_is_recorded_even_when_the_gate_SKIPS(self):
        """The counterfactual is the point of the skip row; without n it cannot
        be read later."""
        s, e = self._strategy([14.0] * 12), self._entry()
        s._iv_percentile_gate(e)
        assert e.ls_iv_percentile_n == 12

    def test_a_disabled_filter_is_untouched_by_any_of_this(self):
        s = self._strategy([14.0], iv_percentile_filter_enabled=False)
        assert s._iv_percentile_gate(self._entry()) is None

    def test_the_100pct_target_ALSO_honours_the_floor(self):
        """Raising the take-profit to +100% on a three-day percentile would be
        the same bug deciding when to take money off the table."""
        s = self._strategy([10.0, 11.0, 12.0], vix=13.0)   # low AND rising
        assert s._profit_target_pct() == 50.0

    def test_the_100pct_target_still_fires_on_a_real_sample(self):
        """The no-op control: the floor must not disable the rule outright."""
        hist = [20.0] * 93 + [12.0]              # cheap today, and rising vs 12.0
        s = self._strategy(hist, vix=13.0)
        assert s._profit_target_pct() == 100.0
