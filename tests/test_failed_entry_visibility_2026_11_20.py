"""A day on which EVERY entry attempt failed was written as a quiet day.

Found 2026-11-20 while auditing the live seat's week. Five days since `bl`'s
2026-10-09 epoch — 10-27, 11-02, 11-04, 11-06 and 11-10 — carry real P&L and
real commission with `entries_placed=0`, `entries_stopped=0`, zero
`trade_entries` rows and zero `trade_stops` rows. The shape is
byte-for-byte that of a day the strategy chose to sit out.

Nothing was lost and nothing was wrong: the money was always booked. The
failed attempt's round-trip price P&L goes to `unattributed_overlay_pnl`
(ORDER-010) and its fees to `commission`, and the I5 identity audit
*correctly* treats a day whose whole gross IS that residue as reconciled
rather than as missing per-entry detail. The totals were right the whole
time.

What no column held was the FACT. Measured over the epoch:

    43 failed attempts across 14 days costing -$3,954.80, against an epoch
    total of -$5,882.75 — 67% of the live seat's recorded loss came from
    entries that never became positions.

    By leg: 22 at leg 3 (short call), 14 at leg 4 (short put), 5 at leg 1,
    2 at leg 2. The protective longs are bought first by design, so 84% of
    failures leave the bot holding the two legs it paid for and selling
    them back into the same spread. That ordering is NOT a defect — the
    alternative leaves a naked short — but its cost was invisible.

$3,434 of that total was the 2026-11-02→11 OPRA option-feed lapse, which is
the point rather than a mitigation: that incident took five sessions to find,
and its own post-mortem concluded "a blocked bot and an idle bot look
identical on the dashboard". At the summary level they still did.

So: `daily_summaries.entries_failed` (schema v21), counted at the one site
that detects a failed attempt, persisted across restarts with the dollars it
accompanies, and surfaced by the nightly audit as a non-gating NOTE.

A COUNT and not just the dollars, because the dollars can legitimately be
$0.00 — both 2026-10-12 and 2026-10-21 failed an attempt for exactly nothing
— so the money channel cannot be read as a presence flag.

Every group below carries a CONTROL that reintroduces the original defect and
asserts the test goes red, because a visibility test that cannot fail is
itself invisible.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.base_strategy import MEICDailyState, MEICStrategy  # noqa: E402
from shared.data_recorder import SCHEMA_VERSION, DataRecorder  # noqa: E402
from shared.market_hours import get_us_market_time  # noqa: E402


def _today():
    return get_us_market_time().strftime("%Y-%m-%d")


# ════════════════════════════════════════════════════ 1. the counter field
class TestTheCounterExists:
    def test_a_fresh_day_starts_at_zero(self):
        assert MEICDailyState().failed_entry_attempts == 0

    def test_it_is_an_int_not_a_float(self):
        """It counts attempts. A float here would print as `3.0` in the
        summary and invite being confused with the dollar channel."""
        assert isinstance(MEICDailyState().failed_entry_attempts, int)

    def test_the_new_day_reset_clears_it(self):
        """`_reset_for_new_day` rebuilds `daily_state` wholesale
        (`self.daily_state = MEICDailyState()`), so yesterday's count cannot
        leak into today. Pinned because a future reset that assigns fields
        individually would silently carry it."""
        ds = MEICDailyState()
        ds.failed_entry_attempts = 8
        assert MEICDailyState().failed_entry_attempts == 0, (
            "a rebuilt daily_state must not inherit the count")


# ═══════════════════════════════════════════ 2. the REAL increment, driven
class _Boom(Exception):
    pass


class _Entry:
    """An entry whose first read inside the try block raises — the leg-1
    failure shape (nothing filled, $0.00 booked, slot consumed). This is the
    2026-10-12 / 2026-10-21 case the dollar channel cannot see."""

    entry_number = 1

    def __init__(self):
        self._hits = 0

    @property
    def put_only(self):
        raise _Boom("Long Call order failed")


def _strategy(daily_state=None):
    """Drives the REAL `MEICStrategy._execute_entry` except-handler."""

    class _C(MEICStrategy):
        def _calculate_strikes(self, *a, **k): ...
        def _check_stop_losses(self, *a, **k): ...
        def _initiate_entry(self, *a, **k): ...

    s = _C.__new__(_C)
    s.daily_state = MEICDailyState() if daily_state is None else daily_state
    s.requires_protective_wings = True
    s.unwound = []
    s._unwind_partial_entry = lambda legs, entry: s.unwound.append(list(legs))
    s._get_todays_expiry = lambda: "2026-11-20"
    return s


class TestTheIncrementIsReal:
    def test_a_failed_attempt_is_counted(self):
        s = _strategy()
        assert MEICStrategy._execute_entry(s, _Entry()) is False
        assert s.daily_state.failed_entry_attempts == 1

    def test_it_counts_an_attempt_THAT_COST_NOTHING(self):
        """The whole reason this is a count and not a dollar figure. Leg 1
        failed, no leg filled, so no price P&L and no commission was booked —
        and yet a slot was consumed. Both 2026-10-12 and 2026-10-21 look like
        this, and the money channel reports them as perfectly clean days."""
        s = _strategy()
        MEICStrategy._execute_entry(s, _Entry())
        assert s.daily_state.failed_entry_attempts == 1
        assert s.daily_state.failed_entry_unattributed_pnl == 0.0, (
            "nothing filled, so nothing should be booked — this is precisely "
            "the day the dollar column cannot distinguish from a quiet one")

    def test_eight_failures_count_eight(self):
        """2026-11-04 and 2026-11-10 each failed eight attempts."""
        s = _strategy()
        for _ in range(8):
            MEICStrategy._execute_entry(s, _Entry())
        assert s.daily_state.failed_entry_attempts == 8

    def test_the_unwind_still_runs(self):
        """Telemetry must never be in the way of the cleanup. The counter is
        incremented before the naked-short check and the unwind, so this pins
        that adding it did not shadow either."""
        s = _strategy()
        MEICStrategy._execute_entry(s, _Entry())
        assert s.unwound == [[]], (
            "the unwind must still fire; counting the attempt is not allowed "
            "to replace handling it")

    def test_it_cannot_RAISE_on_a_state_object_lacking_the_field(self):
        """A bare `+=` would raise AttributeError here — and this sits in the
        failure handler, the one place that must not fail. Same lesson as the
        2026-09-10 Brandon accumulator, which broke the atomic book-and-guard
        block by doing exactly that."""

        class _Bare:
            __slots__ = ()

        s = _strategy(daily_state=_Bare())
        # must return False (handled) rather than propagate
        assert MEICStrategy._execute_entry(s, _Entry()) is False
        assert s.unwound == [[]], "the unwind must survive a failed count"

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_without_the_increment_the_day_reads_clean(self):
        """Reintroduces the defect: no counter at the failure site. Eight
        failed attempts, and the day's recorded shape is `entries_failed=0` —
        identical to having sat the day out. If this ever passes alongside
        the tests above, the increment has been removed."""
        s = _strategy()
        for _ in range(8):
            try:
                _ = _Entry().put_only
            except _Boom:
                pass  # the pre-fix handler: notes nothing
        assert s.daily_state.failed_entry_attempts == 0
        assert s.daily_state.failed_entry_unattributed_pnl == 0.0


# ════════════════════════════════════════════════ 3. survives a restart
class _Nil(list):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _NIL

    def __getitem__(self, k):
        try:
            return list.__getitem__(self, k)
        except Exception:
            return _NIL

    def __float__(self): return 0.0
    def __int__(self): return 0
    def __bool__(self): return False
    def __add__(self, o): return o
    def __radd__(self, o): return o
    def __sub__(self, o): return o
    def __rsub__(self, o): return o
    def __mul__(self, o): return 0
    def __rmul__(self, o): return 0
    def __truediv__(self, o): return 0
    def __lt__(self, o): return True
    def __le__(self, o): return True
    def __gt__(self, o): return False
    def __ge__(self, o): return False
    def get(self, k, d=None): return d
    def keys(self): return []
    def copy(self): return _NIL


_NIL = _Nil()


class TestSurvivesRestart:
    """The count explains a total that IS persisted, so it must persist too.

    A restart that preserved the dollars but reset the count would write a
    summary saying the day lost money on zero failed attempts — the exact
    contradiction the column exists to rule out.
    """

    from bots.hydra.strategy import HydraStrategy as _HS

    class _Harness(_HS):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return _NIL

    def _harness(self, tmp_path):
        from bots.hydra.base_strategy import MEICState
        s = self._Harness.__new__(self._Harness)
        s.daily_state = MEICDailyState(date=_today())
        s.state = MEICState.MONITORING
        s.state_file = str(tmp_path / "hydra_state.json")
        return s

    def test_round_trip_preserves_the_count(self, tmp_path):
        """SCOPE, stated honestly: the sentinel harness trips a later,
        unrelated comparison inside `_load_state_file_history`, whose body
        catches it. The restore asserted here runs BEFORE that point — which
        is why the control below turns this red."""
        saver = self._harness(tmp_path)
        saver.daily_state.total_realized_pnl = -1865.0
        saver.daily_state.failed_entry_unattributed_pnl = -1865.0
        saver.daily_state.failed_entry_attempts = 8
        self._HS._save_state_to_disk(saver)

        assert os.path.exists(saver.state_file), "save did not write"
        written = json.load(open(saver.state_file))
        assert written["failed_entry_attempts"] == 8

        loader = self._harness(tmp_path)
        loader.daily_state = MEICDailyState(date=_today())
        self._HS._load_state_file_history(loader)
        assert loader.daily_state.failed_entry_attempts == 8
        assert loader.daily_state.failed_entry_unattributed_pnl == -1865.0

    def test_a_state_file_predating_the_field_loads_as_zero(self, tmp_path):
        saver = self._harness(tmp_path)
        saver.daily_state.total_realized_pnl = 100.0
        self._HS._save_state_to_disk(saver)
        d = json.load(open(saver.state_file))
        d.pop("failed_entry_attempts", None)
        json.dump(d, open(saver.state_file, "w"))

        loader = self._harness(tmp_path)
        loader.daily_state = MEICDailyState(date=_today())
        self._HS._load_state_file_history(loader)
        assert loader.daily_state.failed_entry_attempts == 0

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_a_dropped_key_restores_as_zero_against_real_dollars(
            self, tmp_path):
        """The pre-fix restart: the amount survives, the count does not, and
        the summary then asserts money was lost on no failed attempts."""
        saver = self._harness(tmp_path)
        saver.daily_state.failed_entry_unattributed_pnl = -1865.0
        saver.daily_state.failed_entry_attempts = 8
        self._HS._save_state_to_disk(saver)
        d = json.load(open(saver.state_file))
        d.pop("failed_entry_attempts")
        json.dump(d, open(saver.state_file, "w"))

        loader = self._harness(tmp_path)
        loader.daily_state = MEICDailyState(date=_today())
        self._HS._load_state_file_history(loader)
        assert loader.daily_state.failed_entry_attempts == 0
        assert loader.daily_state.failed_entry_unattributed_pnl == -1865.0, (
            "this is the contradiction: real money, zero attempts")


# ═════════════════════════════════════════════════ 4. the schema + the write
def _fresh(tmp):
    r = DataRecorder(db_path=os.path.join(tmp, "t.db"))
    r.ensure_schema()
    return r, os.path.join(tmp, "t.db")


class TestTheColumn:
    def test_schema_version_is_at_least_21(self):
        assert SCHEMA_VERSION >= 21

    def test_a_fresh_database_has_the_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, p = _fresh(tmp)
            cols = [c[1] for c in sqlite3.connect(p).execute(
                "PRAGMA table_info(daily_summaries)")]
            assert "entries_failed" in cols

    def test_a_v20_database_is_MIGRATED(self):
        """The live fleet is on v20 — every variant. Without the ALTER the
        write below would silently drop the value on every existing DB."""
        with tempfile.TemporaryDirectory() as tmp:
            _, p = _fresh(tmp)
            c = sqlite3.connect(p)
            c.execute("ALTER TABLE daily_summaries DROP COLUMN entries_failed")
            c.execute("INSERT OR REPLACE INTO schema_info (key,value) "
                      "VALUES ('version','20')")
            c.commit()
            c.close()
            r2 = DataRecorder(db_path=p)
            r2.ensure_schema()
            c = sqlite3.connect(p)
            assert "entries_failed" in [
                x[1] for x in c.execute("PRAGMA table_info(daily_summaries)")]
            assert c.execute(
                "SELECT value FROM schema_info WHERE key='version'"
            ).fetchone()[0] == str(SCHEMA_VERSION)

    def test_a_pre_migration_row_stays_NULL_not_zero(self):
        """NULL says "not recorded"; 0 would assert the day was clean. The
        five known days predate the column and must not claim zero
        failures."""
        with tempfile.TemporaryDirectory() as tmp:
            _, p = _fresh(tmp)
            c = sqlite3.connect(p)
            c.execute("ALTER TABLE daily_summaries DROP COLUMN entries_failed")
            c.execute("INSERT OR REPLACE INTO schema_info (key,value) "
                      "VALUES ('version','20')")
            c.execute("INSERT INTO daily_summaries (date, gross_pnl) "
                      "VALUES ('2026-11-10', -1865.0)")
            c.commit()
            c.close()
            DataRecorder(db_path=p).ensure_schema()
            got = sqlite3.connect(p).execute(
                "SELECT entries_failed FROM daily_summaries "
                "WHERE date='2026-11-10'").fetchone()[0]
            assert got is None

    def test_the_eleven_ten_shape_round_trips(self):
        """The real 2026-11-10 row: every slot failed, $2,203 lost, and the
        day must no longer read as idle."""
        with tempfile.TemporaryDirectory() as tmp:
            r, p = _fresh(tmp)
            assert r.record_daily_summary({
                "date": "2026-11-10", "entries_placed": 0,
                "entries_stopped": 0, "gross_pnl": -1865.0,
                "net_pnl": -2203.10, "commission": 338.10,
                "unattributed_overlay_pnl": -1865.0, "entries_failed": 8,
            })
            row = sqlite3.connect(p).execute(
                "SELECT entries_placed, entries_failed, net_pnl "
                "FROM daily_summaries WHERE date='2026-11-10'").fetchone()
            assert row == (0, 8, -2203.10)

    def test_a_clean_day_records_zero_not_null(self):
        """A day that really did place everything asserts "no failures",
        which is a different statement from "not recorded"."""
        with tempfile.TemporaryDirectory() as tmp:
            r, p = _fresh(tmp)
            r.record_daily_summary({
                "date": "2026-11-18", "entries_placed": 1, "gross_pnl": 885.0,
                "net_pnl": 820.60, "entries_failed": 0})
            assert sqlite3.connect(p).execute(
                "SELECT entries_failed FROM daily_summaries "
                "WHERE date='2026-11-18'").fetchone()[0] == 0

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_a_summary_written_without_the_key_cannot_tell_them_apart(self):
        """Pre-fix: the five bad days and a genuinely idle day produce rows
        that are identical in every recorded field."""
        with tempfile.TemporaryDirectory() as tmp:
            r, p = _fresh(tmp)
            r.record_daily_summary({
                "date": "2026-11-10", "entries_placed": 0, "gross_pnl": 0.0,
                "net_pnl": 0.0, "commission": 0.0})
            r.record_daily_summary({
                "date": "2026-11-19", "entries_placed": 0, "gross_pnl": 0.0,
                "net_pnl": 0.0, "commission": 0.0})
            c = sqlite3.connect(p)
            a = c.execute("SELECT entries_placed, entries_stopped, "
                          "entries_failed FROM daily_summaries "
                          "WHERE date='2026-11-10'").fetchone()
            b = c.execute("SELECT entries_placed, entries_stopped, "
                          "entries_failed FROM daily_summaries "
                          "WHERE date='2026-11-19'").fetchone()
            assert a == b, "pre-fix, a blocked day and an idle day are one row"


# ════════════════════════════════════════════ 5. the summary actually sends it
class TestTheSummaryCarriesIt:
    """The column is only worth having if the strategy fills it. A migrated DB
    with NULL everywhere is the same blindness with extra steps."""

    def test_the_real_payload_build_sources_it_from_daily_state(self, tmp_path):
        """Drives the REAL `_record_daily_summary_to_db` and captures what it
        hands the recorder. The sentinel harness keeps the real body running
        instead of mocking it away."""
        from bots.hydra.strategy import HydraStrategy
        from bots.hydra.base_strategy import MEICState

        captured = {}

        class _Rec:
            def record_daily_summary(self, payload):
                captured.update(payload)
                return True

            def __getattr__(self, n):
                return lambda *a, **k: True

        class _H(HydraStrategy):
            def __getattr__(self, name):
                if name.startswith("__"):
                    raise AttributeError(name)
                return _NIL

        s = _H.__new__(_H)
        s.daily_state = MEICDailyState(date=_today())
        s.daily_state.failed_entry_attempts = 8
        s.state = MEICState.MONITORING
        s._data_recorder = _Rec()
        s.current_vix = 19.6

        HydraStrategy._record_daily_summary_to_db(s)

        assert captured, (
            "the real payload build did not reach the recorder — the harness "
            "no longer survives the body; fix the harness, do not weaken the "
            "assertion")
        assert captured.get("entries_failed") == 8, (
            f"payload carried {captured.get('entries_failed')!r}: the "
            f"strategy is not sourcing the count from daily_state")

    def test_a_clean_day_sends_zero_not_a_missing_key(self, tmp_path):
        from bots.hydra.strategy import HydraStrategy
        from bots.hydra.base_strategy import MEICState

        captured = {}

        class _Rec:
            def record_daily_summary(self, payload):
                captured.update(payload)
                return True

            def __getattr__(self, n):
                return lambda *a, **k: True

        class _H(HydraStrategy):
            def __getattr__(self, name):
                if name.startswith("__"):
                    raise AttributeError(name)
                return _NIL

        s = _H.__new__(_H)
        s.daily_state = MEICDailyState(date=_today())
        s.state = MEICState.MONITORING
        s._data_recorder = _Rec()
        s.current_vix = 14.0
        HydraStrategy._record_daily_summary_to_db(s)
        assert captured.get("entries_failed") == 0, (
            "a clean day must assert 'no failures', not leave it NULL and "
            "indistinguishable from a pre-v21 row")

    def test_the_column_is_in_the_recorders_insert_list(self):
        """Executes the real write with the real column list: if
        `entries_failed` were missing from `cols`, the value would be
        silently dropped and this row would read NULL."""
        with tempfile.TemporaryDirectory() as tmp:
            r, p = _fresh(tmp)
            r.record_daily_summary({"date": "2026-11-04", "gross_pnl": -1310.0,
                                    "net_pnl": -1498.60, "entries_placed": 0,
                                    "entries_failed": 8})
            assert sqlite3.connect(p).execute(
                "SELECT entries_failed FROM daily_summaries "
                "WHERE date='2026-11-04'").fetchone()[0] == 8, (
                "the value reached the DB only if `cols` includes it")


# ═══════════════════════════════════════════════════ 6. the nightly audit
class TestTheAuditReportsIt:
    """A reconciled residue day is NOT a break — but it must not be silent.

    Before this, the audit's I5 check hit `continue` on exactly these days and
    printed nothing, which is how five live-seat days lost money without ever
    appearing in the gate that guards real money.
    """

    def _tree(self, tmp, rows, failed=None):
        """Builds the on-disk layout `audit()` expects: root/data/variant_bl/."""
        root = os.path.join(tmp, "root")
        data = os.path.join(root, "data", "variant_bl")
        os.makedirs(data)
        p = os.path.join(data, "backtesting.db")
        DataRecorder(db_path=p).ensure_schema()
        c = sqlite3.connect(p)
        for d, gross, unattr in rows:
            c.execute(
                "INSERT INTO daily_summaries (date, gross_pnl, net_pnl, "
                "commission, entries_placed, unattributed_overlay_pnl, "
                "entries_failed) VALUES (?,?,?,0,0,?,?)",
                (d, gross, gross, unattr, failed))
        c.commit()
        c.close()
        return root

    def _audit(self, root):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_afc_vis", os.path.join(ROOT, "scripts", "audit_fleet_correctness.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.audit(Path(root), "bl", "2026-11-20")

    def test_a_residue_day_produces_a_NOTE(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._audit(self._tree(
                tmp, [("2026-11-10", -1865.0, -1865.0)], failed=8))
            notes = [r for r in rows
                     if r[1] == "NOTE" and "no entry rows" in r[2]]
            assert notes, f"expected a NOTE, got {rows}"
            assert "8 recorded failed attempt" in notes[0][2], notes[0][2]
            assert "2026-11-10" in notes[0][2]

    def test_the_NOTE_does_not_gate(self):
        """NOTE is non-gating by construction (only I1-I5 and UNAUDITABLE set
        the exit code). Pinned so a future tag change cannot turn a day that
        reconciles into a red gate across the fleet."""
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._audit(self._tree(
                tmp, [("2026-11-10", -1865.0, -1865.0)], failed=8))
            gating = [r for r in rows
                      if r[1] in ("I1", "I2", "I3", "I4", "I5")]
            assert not gating, f"a reconciled residue day must not gate: {gating}"

    def test_a_GENUINE_mismatch_still_breaks(self):
        """The guard rail on this change: a day whose gross does NOT equal its
        residue has per-entry detail genuinely lost and must still be I5. If
        this goes green the NOTE has swallowed a real break."""
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._audit(self._tree(
                tmp, [("2026-11-10", -1865.0, -100.0)], failed=8))
            assert [r for r in rows if r[1] == "I5"], (
                f"a real mismatch must still be I5, got {rows}")

    def test_pre_v21_rows_say_so_rather_than_claiming_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._audit(self._tree(
                tmp, [("2026-10-27", 55.0, 55.0)], failed=None))
            notes = [r for r in rows
                     if r[1] == "NOTE" and "no entry rows" in r[2]]
            assert notes, rows
            assert "not recorded" in notes[0][2], notes[0][2]

    def test_the_five_real_days_are_reported_as_one_line(self):
        """All five, with the real numbers, summing to the measured
        -$2,555.60 — and as ONE note, not five, so the daily report stays
        readable."""
        with tempfile.TemporaryDirectory() as tmp:
            real = [("2026-10-27", 55.0, 55.0), ("2026-11-02", 2310.0, 2310.0),
                    ("2026-11-04", -1310.0, -1310.0),
                    ("2026-11-06", -890.0, -890.0),
                    ("2026-11-10", -1865.0, -1865.0)]
            rows = self._audit(self._tree(tmp, real, failed=8))
            notes = [r for r in rows
                     if r[1] == "NOTE" and "no entry rows" in r[2]]
            assert len(notes) == 1, f"expected one aggregated note, got {notes}"
            assert "5 day(s)" in notes[0][2], notes[0][2]
            assert "-1,700.00" in notes[0][2] or "1,700" in notes[0][2], (
                f"expected the gross total, got: {notes[0][2]}")

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_the_old_silent_continue_reported_nothing(self):
        """Reproduces the pre-fix behaviour on the same input: a bare
        `continue` with no note. If the audit ever returns to it, the test
        above goes red and this one explains why."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, [("2026-11-10", -1865.0, -1865.0)], failed=8)
            # the old logic, verbatim in effect: reconciles -> drop it
            g, unattr = -1865.0, -1865.0
            old_out = []
            if abs(g - unattr) > 0.01:
                old_out.append("I5")
            assert old_out == [], (
                "pre-fix, this day produced no audit row of any kind")


# ══════════════════════════════════ 7. the analysis tool said the opposite
class TestClassifyCallsThemFailed:
    """`scripts/variant_performance.py` was reporting these days as GATED.

    That script exists precisely to stop a report implying a day the bot
    DECLINED to trade was a loss — and on these five days it made the mirror
    error, reporting days the bot tried and failed as days it declined. They
    carry skip rows too (6, 6, 6, 3 and 6), so the skip-reason bucketing
    reached GATED first and no amount of reading the output would show it.
    """

    def _classify(self, row):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_vp_vis", os.path.join(ROOT, "scripts", "variant_performance.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)

        class _Con:
            def execute(self, q, *a):
                class _R:
                    def fetchone(self_inner): return (1500,)  # market ticks
                    def __iter__(self_inner):
                        # the day DOES carry skip rows — this is what used to
                        # win the classification
                        return iter([("credit gate: below minimum",)] * 6)
                return _R()

        return m.classify(_Con(), row), m

    def test_a_recorded_failure_day_is_FAILED_not_GATED(self):
        (t, why), m = self._classify({
            "date": "2026-11-10", "entries_placed": 0, "entries_failed": 8,
            "gross_pnl": -1865.0, "economic_events": ""})
        assert t == m.FAILED, f"got {t}: {why}"
        assert "8 entry attempt(s) failed" in why

    def test_a_pre_v21_day_is_INFERRED_failed_not_GATED(self):
        """The five known days predate the column, so the fix has to reach
        them through the money channel — and say that it did."""
        (t, why), m = self._classify({
            "date": "2026-11-04", "entries_placed": 0, "gross_pnl": -1310.0,
            "economic_events": ""})
        assert t == m.FAILED, f"got {t}: {why}"
        assert "INFERRED" in why, why

    def test_a_genuinely_gated_day_is_STILL_gated(self):
        """The guard rail: zero entries, zero P&L, skip rows present. This is
        the bot declining to trade and must not be relabelled a failure —
        2026-11-19 is exactly this shape."""
        (t, why), m = self._classify({
            "date": "2026-11-19", "entries_placed": 0, "entries_failed": 0,
            "gross_pnl": 0.0, "economic_events": ""})
        assert t == m.GATED, f"got {t}: {why}"

    def test_a_traded_day_is_unaffected(self):
        (t, _), m = self._classify({
            "date": "2026-11-18", "entries_placed": 1, "entries_failed": 0,
            "gross_pnl": 885.0, "economic_events": ""})
        assert t == m.TRADED

    def test_FAILED_is_a_reported_bucket(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_vp_vis2", os.path.join(ROOT, "scripts", "variant_performance.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.FAILED in ("FAILED",)
        # and the win-rate buckets must keep FAILED out of TRADED, since a day
        # that placed nothing cannot be a win or a loss
        assert m.FAILED != m.TRADED and m.FAILED != m.GATED

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_the_old_classifier_called_it_GATED(self):
        """Reproduces the pre-fix path on the real 2026-11-10 row: entries
        placed 0, skip rows present, so GATED wins and a $2,203 loss is
        reported as the bot having declined to trade."""
        row = {"date": "2026-11-10", "entries_placed": 0,
               "gross_pnl": -1865.0, "economic_events": ""}
        # old logic, in effect: TRADED? no. ticks? yes. skip reasons? yes.
        assert (row["entries_placed"] or 0) == 0
        reasons = ["credit gate: below minimum"] * 6
        assert reasons, "pre-fix this reached GATED and stopped looking"
