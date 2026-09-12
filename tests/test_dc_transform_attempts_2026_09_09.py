"""
Calendar schema v3 — record EVERY transform-gate evaluation, and attribute
snapshots to a trade (2026-09-09).

WHY
---
D's transform gate is the strategy's entire thesis (the calendar leg is
0-for-23). It is evaluated on every monitoring tick — order 600 times per trade
— but only the evaluations that FIRED were ever persisted. Two rows, in the
whole history of the strategy. Everything else went to a rotating log, of which
roughly 955 of ~4,700 evaluations survived.

That collapsed the central question into a 2-of-8 binary while a continuous
distance-to-gate series was being computed and thrown away. `dc_transform_attempts`
keeps it, including the six leg prices and the credit at mid AND full touch, so
the gate is recomputable offline at ANY fill aggressiveness — the one input that
decides this strategy and has never been validated against a real order.

Separately, `dc_calendar_snapshots` carried only `entry_number`, which is
ALWAYS 1 for D and E (verified against the live DBs: the distinct set is
literally [1]). 59,305 D rows and 69,660 E rows could not be attributed to a
specific trade except by guessing from timestamps.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.calendar_entry import CalendarEntry, DCPhase  # noqa: E402
from bots.hydra.dc_recorder import DCDataRecorder  # noqa: E402
from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy  # noqa: E402


def _q(mid, bid=None, ask=None):
    bid = mid - 0.1 if bid is None else bid
    ask = mid + 0.1 if ask is None else ask
    return {"mid": mid, "raw": {"bid": bid, "ask": ask}, "realtime": True}


def _entry(net_debit=200.0, sid="dctm_20260909_001"):
    e = CalendarEntry(entry_number=1)
    e.strategy_id = sid
    e.net_debit = net_debit
    e.contracts = 1
    e.short_call_strike = e.long_call_strike = 7700.0
    e.short_put_strike = e.long_put_strike = 7580.0
    e.legs["short_call"].expiry = "2026-09-11"
    for k, u in (("short_call", 1), ("long_call", 2), ("short_put", 3), ("long_put", 4)):
        e.legs[k].uic = u
    return e


def _strat(quotes, db_path, wing=5.0, agg=0.5):
    s = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
    s.dc_wing_width = wing
    s.contracts_per_entry = 1
    s._dc_fill_agg = agg
    s._dc_fill_slippage = 0.0
    s.commission_per_leg = 0.0
    s._get_option_uic = MagicMock(side_effect=[101, 102])
    s._dc_read_leg_quotes = MagicMock(return_value=quotes)
    s._dc_recorder = DCDataRecorder(db_path)
    s.daily_state = SimpleNamespace(total_commission=0.0)
    return s


def _run(s, e):
    mod = __import__("bots.hydra.double_calendar_strategy", fromlist=["x"])
    with patch.object(mod, "get_us_market_time") as t:
        t.return_value = MagicMock(isoformat=lambda: "2026-09-09T10:00:00",
                                   strftime=lambda f: "2026-09-09")
        return s._dc_attempt_transform(e)


def _attempts(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("SELECT * FROM dc_transform_attempts")]
    c.close()
    return rows


FIRES = {
    "long_call": _q(30.0), "long_put": _q(30.0),
    "wing_call": _q(20.0), "wing_put": _q(20.0),
    "short_call": _q(22.0), "short_put": _q(22.0),
}
BELOW = {   # credit (2+2-1-1)*100 = 200 < threshold 200+500 = 700
    "long_call": _q(2.0), "long_put": _q(2.0),
    "wing_call": _q(1.0), "wing_put": _q(1.0),
    "short_call": _q(3.0), "short_put": _q(3.0),
}
ARB = {     # credit passes, but put side 5.384 > 5.0 wing — impossible
    "long_call": _q(40.0), "long_put": _q(45.0),
    "wing_call": _q(25.634), "wing_put": _q(43.766),
    "short_call": _q(28.0), "short_put": _q(49.15),
}


class TestEveryEvaluationIsRecorded:
    def test_a_firing_transform_records_outcome_fired(self, tmp_path):
        db = str(tmp_path / "dc.db")
        s = _strat(FIRES, db)
        assert _run(s, _entry()) is True
        rows = _attempts(db)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "fired"

    def test_a_below_threshold_evaluation_is_recorded_too(self, tmp_path):
        """THE point of this table: the ~4,700 non-firing evaluations that used
        to vanish into a rotating log."""
        db = str(tmp_path / "dc.db")
        s = _strat(BELOW, db)
        assert _run(s, _entry()) is False
        rows = _attempts(db)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "below_threshold"
        # Priced at the ACTING agg=0.5, not mid: the ±0.10 spreads mean longs
        # sell at mid-0.05 and wings buy at mid+0.05, so
        #   ((2.0-0.05)*2 - (1.0+0.05)*2) * 100 = $180, not the $200 mid.
        assert rows[0]["transform_credit"] == pytest.approx(180.0)
        assert rows[0]["threshold"] == pytest.approx(700.0)
        assert rows[0]["margin"] == pytest.approx(-520.0)

    def test_an_arb_rejection_is_recorded(self, tmp_path):
        db = str(tmp_path / "dc.db")
        s = _strat(ARB, db)
        assert _run(s, _entry(net_debit=895.0)) is False
        rows = _attempts(db)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "arb_rejected"

    def test_repeated_evaluations_accumulate_rows(self, tmp_path):
        """A distance-to-gate SERIES, not one bit per trade."""
        db = str(tmp_path / "dc.db")
        for _ in range(5):
            s = _strat(BELOW, db)
            _run(s, _entry())
        assert len(_attempts(db)) == 5


class TestGateIsRecomputableOffline:
    def test_mid_and_touch_credit_are_stored_and_bracket_the_acting_credit(self, tmp_path):
        db = str(tmp_path / "dc.db")
        s = _strat(BELOW, db, agg=0.5)
        _run(s, _entry())
        r = _attempts(db)[0]
        # Credit = sell longs - buy wings. Crossing further LOWERS it.
        assert r["touch_credit"] < r["transform_credit"] < r["mid_credit"]

    def test_interpolation_reproduces_the_acting_credit(self, tmp_path):
        """The identity that makes historical re-pricing exact. If this drifts,
        every offline 'what if agg were X' answer is silently wrong."""
        db = str(tmp_path / "dc.db")
        s = _strat(BELOW, db, agg=0.5)
        _run(s, _entry())
        r = _attempts(db)[0]
        predicted = r["mid_credit"] + r["fill_agg"] * (r["touch_credit"] - r["mid_credit"])
        assert predicted == pytest.approx(r["transform_credit"])

    def test_all_six_leg_prices_are_stored(self, tmp_path):
        db = str(tmp_path / "dc.db")
        s = _strat(BELOW, db)
        _run(s, _entry())
        r = _attempts(db)[0]
        for col in ("long_call_px", "long_put_px", "wing_call_px", "wing_put_px",
                    "short_call_px", "short_put_px"):
            assert r[col] is not None, col

    def test_the_recorded_agg_is_the_acting_one(self, tmp_path):
        db = str(tmp_path / "dc.db")
        s = _strat(BELOW, db, agg=0.25)
        _run(s, _entry())
        assert _attempts(db)[0]["fill_agg"] == pytest.approx(0.25)


class TestTelemetryNeverBreaksTrading:
    def test_a_raising_recorder_does_not_stop_the_transform(self, tmp_path):
        """Fire-and-forget. A recording failure must never cost a trade —
        precedent: the 2026-09-03 mark-sanity incident froze a live position's
        P&L for a whole session."""
        db = str(tmp_path / "dc.db")
        s = _strat(FIRES, db)
        s._dc_recorder.record_transform_attempt = MagicMock(
            side_effect=RuntimeError("disk full"))
        assert _run(s, _entry()) is True

    def test_no_recorder_at_all_is_fine(self, tmp_path):
        s = _strat(FIRES, str(tmp_path / "dc.db"))
        s._dc_recorder = None
        assert _run(s, _entry()) is True


class TestSnapshotAttribution:
    def test_snapshot_records_strategy_id_and_entry_date(self, tmp_path):
        db = str(tmp_path / "dc.db")
        r = DCDataRecorder(db)
        e = SimpleNamespace(
            entry_number=1, strategy_id="dctm_20260901_001",
            dc_phase=SimpleNamespace(value="calendar"),
            net_debit=895.0, calendar_value=900.0, unrealized_pnl=5.0,
            short_call_price=1.0, long_call_price=2.0,
            short_put_price=1.0, long_put_price=2.0,
        )
        r.record_snapshot(e, "2026-09-01T10:00:00")
        c = sqlite3.connect(db)
        row = c.execute("SELECT strategy_id, entry_date FROM "
                        "dc_calendar_snapshots").fetchone()
        c.close()
        assert row[0] == "dctm_20260901_001"
        assert row[1] == "20260901"   # derived from the id, not guessed

    def test_missing_strategy_id_writes_NULL_not_a_blank(self, tmp_path):
        db = str(tmp_path / "dc.db")
        r = DCDataRecorder(db)
        e = SimpleNamespace(
            entry_number=1, dc_phase=SimpleNamespace(value="calendar"),
            net_debit=0.0, calendar_value=0.0, unrealized_pnl=0.0,
            short_call_price=0.0, long_call_price=0.0,
            short_put_price=0.0, long_put_price=0.0,
        )
        r.record_snapshot(e, "2026-09-01T10:00:00")
        c = sqlite3.connect(db)
        row = c.execute("SELECT strategy_id, entry_date FROM "
                        "dc_calendar_snapshots").fetchone()
        c.close()
        assert row == (None, None)


class TestOutcomeOverwriteGuard:
    def test_unique_index_on_strategy_id_exists_after_migration(self, tmp_path):
        """dc_outcomes' PK is (entry_date, entry_number) and entry_number is
        pinned at 1, so INSERT OR REPLACE would silently overwrite if two
        entries ever shared an entry_date."""
        p = str(tmp_path / "dc.db")
        c = sqlite3.connect(p)
        c.executescript("""
            CREATE TABLE dc_calendar_entries (date TEXT, entry_number INTEGER);
            CREATE TABLE dc_calendar_snapshots (timestamp TEXT, entry_number INTEGER);
            CREATE TABLE dc_transformations (date TEXT, entry_number INTEGER);
            CREATE TABLE dc_outcomes (entry_date TEXT, entry_number INTEGER,
                                      strategy_id TEXT);
            CREATE TABLE dc_schema_info (version INTEGER);
            INSERT INTO dc_schema_info (version) VALUES (1);
        """)
        c.commit(); c.close()

        DCDataRecorder(p)

        c = sqlite3.connect(p)
        idx = {r[1] for r in c.execute("PRAGMA index_list(dc_outcomes)")}
        assert "idx_dc_outcomes_strategy_id" in idx
        # and it actually prevents a duplicate
        c.execute("INSERT INTO dc_outcomes VALUES ('2026-09-01', 1, 'sid_a')")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO dc_outcomes VALUES ('2026-09-02', 1, 'sid_a')")
        c.close()
