"""
Calendar schema v2 — record every mark at MID and FULL TOUCH (2026-09-07).

WHY THIS EXISTS
---------------
`dc_calendar_snapshots` v1 stored only the post-haircut leg price. The bid/ask
that produced it was discarded. That made it IMPOSSIBLE to answer the single
question that decides strategies D and E: "what would this have done at a
different fill assumption?"

That question became urgent on 2026-09-06, when a forensic found D had run 19
trades at full touch (agg=1.0) because `dry_run_fill_model: 0.5` was written as
a bare scalar under the `double_calendar` sub-block — a form the pre-0043298
reader silently ignored. ~74% of D's -$4,877 same-day-era loss was modeled
crossing cost, at double the intended setting, and it could not be re-priced
because the spread was gone.

THE FIX
-------
`_dc_fill_price` gains `agg`/`slip` overrides, so the same quote can be priced
at mid (agg=0, slip=0) and full touch (agg=1, slip=0) alongside the acting
fill. Both are recorded. Any aggressiveness is then an EXACT linear
interpolation:

    value(a) = mid_value + a * (touch_value - mid_value)

INVARIANTS PINNED HERE
----------------------
1. The override must not change the acting fill (record-only, zero behaviour change).
2. mid/touch must bracket the acting value, and interpolation must be exact.
3. A v1 database must migrate additively, preserving every existing row.
4. Old rows keep NULL — never 0.0, which would read as "worth nothing at mid".
5. A failure computing the record-only detail must never affect a trading decision.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.calendar_strategy_base import CalendarStrategyBase  # noqa: E402
from bots.hydra.dc_recorder import DCDataRecorder  # noqa: E402


def _q(bid, ask):
    """A quote row shaped the way _dc_read_leg_quotes returns them."""
    return {"mid": (bid + ask) / 2.0, "raw": {"bid": bid, "ask": ask}, "realtime": True}


def _strat(agg=0.5, slip=0.0):
    s = CalendarStrategyBase.__new__(CalendarStrategyBase)
    s._dc_fill_agg = agg
    s._dc_fill_slippage = slip
    s.contracts_per_entry = 1
    return s


# Four legs with deliberately DIFFERENT spread widths so a bug that reuses one
# leg's spread everywhere cannot pass.
QUOTES = {
    "long_call":  _q(10.00, 10.60),   # mid 10.30, half-spread 0.30
    "short_call": _q(6.00, 6.40),     # mid 6.20,  half-spread 0.20
    "long_put":   _q(9.00, 9.50),     # mid 9.25,  half-spread 0.25
    "short_put":  _q(5.00, 5.20),     # mid 5.10,  half-spread 0.10
}


class TestFillPriceOverride:
    def test_override_does_not_change_the_acting_fill(self):
        """Record-only means record-only: omitting the kwargs must give exactly
        what the configured aggressiveness gave before this change."""
        s = _strat(agg=0.5)
        for action in ("buy", "sell"):
            for name, q in QUOTES.items():
                assert s._dc_fill_price(q, action) == s._dc_fill_price(q, action, agg=0.5, slip=0.0)

    def test_agg_zero_is_exactly_mid(self):
        s = _strat(agg=1.0)
        for name, q in QUOTES.items():
            assert s._dc_fill_price(q, "buy", agg=0.0, slip=0.0) == pytest.approx(q["mid"])
            assert s._dc_fill_price(q, "sell", agg=0.0, slip=0.0) == pytest.approx(q["mid"])

    def test_agg_one_is_exactly_the_touch(self):
        s = _strat(agg=0.0)
        for name, q in QUOTES.items():
            raw = q["raw"]
            assert s._dc_fill_price(q, "buy", agg=1.0, slip=0.0) == pytest.approx(raw["ask"])
            assert s._dc_fill_price(q, "sell", agg=1.0, slip=0.0) == pytest.approx(raw["bid"])

    def test_slip_override_suppresses_configured_slippage(self):
        """Otherwise the recorded 'mid' would silently carry a slippage buffer
        and the interpolation identity would not hold."""
        s = _strat(agg=1.0, slip=0.07)
        q = QUOTES["long_call"]
        assert s._dc_fill_price(q, "buy", agg=0.0, slip=0.0) == pytest.approx(q["mid"])
        # ...whereas the configured path DOES include it
        assert s._dc_fill_price(q, "buy") == pytest.approx(q["raw"]["ask"] + 0.07)


class TestCalendarValueAt:
    def test_mid_and_touch_bracket_the_acting_value(self):
        s = _strat(agg=0.5)
        mid = s._dc_calendar_value_at(QUOTES, 1, 0.0)
        touch = s._dc_calendar_value_at(QUOTES, 1, 1.0)
        acting = s._dc_calendar_value_at(QUOTES, 1, 0.5)
        # Liquidation value: crossing the spread makes it WORSE, so touch < mid.
        assert touch < acting < mid

    def test_interpolation_is_exact(self):
        """The whole point: any aggressiveness recoverable from the two stored
        endpoints. If this drifts, historical re-pricing is silently wrong."""
        s = _strat()
        mid = s._dc_calendar_value_at(QUOTES, 1, 0.0)
        touch = s._dc_calendar_value_at(QUOTES, 1, 1.0)
        for a in (0.0, 0.25, 0.5, 0.75, 1.0):
            direct = s._dc_calendar_value_at(QUOTES, 1, a)
            interpolated = mid + a * (touch - mid)
            assert direct == pytest.approx(interpolated), f"broken at agg={a}"

    def test_matches_the_calendar_value_formula(self):
        """Must mirror CalendarEntry.calendar_value exactly, or the recorded
        mid/touch aren't comparable to the stored calendar_value."""
        s = _strat()
        # At mid: (long-short) per side, summed, x100 x contracts.
        expected = ((10.30 - 6.20) + (9.25 - 5.10)) * 100 * 1
        assert s._dc_calendar_value_at(QUOTES, 1, 0.0) == pytest.approx(expected)

    def test_scales_with_contracts(self):
        s = _strat()
        one = s._dc_calendar_value_at(QUOTES, 1, 0.0)
        three = s._dc_calendar_value_at(QUOTES, 3, 0.0)
        assert three == pytest.approx(one * 3)

    def test_returns_none_on_a_missing_leg(self):
        """A partial tick must never be recorded as a real value."""
        s = _strat()
        partial = {k: v for k, v in QUOTES.items() if k != "short_put"}
        assert s._dc_calendar_value_at(partial, 1, 0.0) is None

    def test_returns_none_on_an_unusable_quote(self):
        s = _strat()
        broken = dict(QUOTES)
        broken["long_put"] = {"mid": 0.0, "raw": {}}
        assert s._dc_calendar_value_at(broken, 1, 0.0) is None


class TestNetDebitAt:
    def test_net_debit_direction_is_opposite_to_liquidation(self):
        """Opening you BUY longs / SELL shorts, so crossing makes the debit
        BIGGER. This is the mirror of the liquidation case above — a copy-paste
        bug that reused the liquidation actions would fail here."""
        s = _strat()
        mid = s._dc_net_debit_at(QUOTES, 1, 0.0)
        touch = s._dc_net_debit_at(QUOTES, 1, 1.0)
        assert touch > mid

    def test_round_trip_cost_equals_the_birth_toll(self):
        """The 'birth toll' the forensic measured = open at touch, immediately
        marked at touch. Pin the identity so the recorded numbers can be
        reasoned about."""
        s = _strat()
        debit_touch = s._dc_net_debit_at(QUOTES, 1, 1.0)
        value_touch = s._dc_calendar_value_at(QUOTES, 1, 1.0)
        toll = debit_touch - value_touch
        full_spread = sum(q["raw"]["ask"] - q["raw"]["bid"] for q in QUOTES.values()) * 100
        assert toll == pytest.approx(full_spread)

    def test_at_mid_the_birth_toll_is_zero(self):
        s = _strat()
        assert s._dc_net_debit_at(QUOTES, 1, 0.0) == pytest.approx(
            s._dc_calendar_value_at(QUOTES, 1, 0.0))


class TestSchemaMigration:
    def _make_v1(self, path):
        """Build a genuine v1 DB with a row in each table, the way a real
        pre-2026-09-07 dc_calendar.db looks."""
        c = sqlite3.connect(path)
        c.executescript("""
            CREATE TABLE dc_calendar_entries (
                date TEXT, entry_number INTEGER, strategy_id TEXT, structure TEXT,
                entry_time TEXT, spx_at_entry REAL,
                short_expiry TEXT, long_expiry TEXT, short_dte INTEGER, long_dte INTEGER,
                call_strike REAL, put_strike REAL, net_debit REAL, contracts INTEGER,
                short_call_uic INTEGER, long_call_uic INTEGER,
                short_put_uic INTEGER, long_put_uic INTEGER,
                PRIMARY KEY (date, entry_number)
            );
            CREATE TABLE dc_calendar_snapshots (
                timestamp TEXT, entry_number INTEGER, dc_phase TEXT,
                net_debit REAL, calendar_value REAL, unrealized_pnl REAL,
                short_call_price REAL, long_call_price REAL,
                short_put_price REAL, long_put_price REAL
            );
            CREATE TABLE dc_transformations (date TEXT, entry_number INTEGER);
            CREATE TABLE dc_outcomes (entry_date TEXT, entry_number INTEGER);
            CREATE TABLE dc_schema_info (version INTEGER);
            INSERT INTO dc_schema_info (version) VALUES (1);
        """)
        c.execute("INSERT INTO dc_calendar_entries (date, entry_number, net_debit) "
                  "VALUES ('2026-08-18', 1, 945.0)")
        c.execute("INSERT INTO dc_calendar_snapshots (timestamp, entry_number, calendar_value) "
                  "VALUES ('2026-08-18T10:00:00', 1, 900.0)")
        c.commit(); c.close()

    def test_v1_db_migrates_and_preserves_every_row(self, tmp_path):
        p = str(tmp_path / "dc_calendar.db")
        self._make_v1(p)
        DCDataRecorder(p)

        c = sqlite3.connect(p)
        assert c.execute("SELECT version FROM dc_schema_info").fetchone()[0] == 2
        # existing data intact
        assert c.execute("SELECT net_debit FROM dc_calendar_entries").fetchone()[0] == 945.0
        assert c.execute("SELECT calendar_value FROM dc_calendar_snapshots").fetchone()[0] == 900.0
        # new columns present
        ecols = {r[1] for r in c.execute("PRAGMA table_info(dc_calendar_entries)")}
        scols = {r[1] for r in c.execute("PRAGMA table_info(dc_calendar_snapshots)")}
        assert {"mid_net_debit", "touch_net_debit"} <= ecols
        assert {"mid_calendar_value", "touch_calendar_value", "fill_agg", "fill_slip"} <= scols
        c.close()

    def test_old_rows_keep_NULL_not_zero(self, tmp_path):
        """0.0 would read as 'the calendar was worth nothing at mid'. The
        information was never captured; NULL is the honest value."""
        p = str(tmp_path / "dc_calendar.db")
        self._make_v1(p)
        DCDataRecorder(p)
        c = sqlite3.connect(p)
        assert c.execute("SELECT mid_calendar_value FROM dc_calendar_snapshots").fetchone()[0] is None
        assert c.execute("SELECT mid_net_debit FROM dc_calendar_entries").fetchone()[0] is None
        c.close()

    def test_migration_is_idempotent(self, tmp_path):
        p = str(tmp_path / "dc_calendar.db")
        self._make_v1(p)
        for _ in range(3):
            DCDataRecorder(p)
        c = sqlite3.connect(p)
        cols = [r[1] for r in c.execute("PRAGMA table_info(dc_calendar_snapshots)")]
        assert cols.count("mid_calendar_value") == 1
        assert c.execute("SELECT COUNT(*) FROM dc_calendar_snapshots").fetchone()[0] == 1
        c.close()

    def test_fresh_db_is_created_at_v2_with_the_columns(self, tmp_path):
        p = str(tmp_path / "fresh.db")
        DCDataRecorder(p)
        c = sqlite3.connect(p)
        assert c.execute("SELECT version FROM dc_schema_info").fetchone()[0] == 2
        scols = {r[1] for r in c.execute("PRAGMA table_info(dc_calendar_snapshots)")}
        assert {"mid_calendar_value", "touch_calendar_value", "fill_agg", "fill_slip"} <= scols
        c.close()


class TestSnapshotWrite:
    def _entry(self, **kw):
        e = SimpleNamespace(
            entry_number=1, dc_phase=SimpleNamespace(value="calendar"),
            net_debit=945.0, calendar_value=900.0, unrealized_pnl=-45.0,
            short_call_price=6.2, long_call_price=10.3,
            short_put_price=5.1, long_put_price=9.25,
        )
        for k, v in kw.items():
            setattr(e, k, v)
        return e

    def test_records_mid_touch_and_the_resolved_agg(self, tmp_path):
        p = str(tmp_path / "dc.db")
        r = DCDataRecorder(p)
        r.record_snapshot(self._entry(
            _dc_mark_mid_value=825.0, _dc_mark_touch_value=770.0,
            _dc_mark_fill_agg=0.5, _dc_mark_fill_slip=0.0,
        ), "2026-09-07T10:00:00")
        c = sqlite3.connect(p)
        row = c.execute("SELECT mid_calendar_value, touch_calendar_value, fill_agg, fill_slip "
                        "FROM dc_calendar_snapshots").fetchone()
        assert row == (825.0, 770.0, 0.5, 0.0)
        c.close()

    def test_entry_without_the_detail_writes_NULL_and_does_not_raise(self, tmp_path):
        """A tick that failed the sanity guard carries no mid/touch. It must
        still record, with NULL — not crash, and not fabricate a 0.0."""
        p = str(tmp_path / "dc.db")
        r = DCDataRecorder(p)
        r.record_snapshot(self._entry(), "2026-09-07T10:00:00")
        c = sqlite3.connect(p)
        row = c.execute("SELECT calendar_value, mid_calendar_value, fill_agg "
                        "FROM dc_calendar_snapshots").fetchone()
        assert row[0] == 900.0      # the real mark still recorded
        assert row[1] is None
        assert row[2] is None
        c.close()


class TestRecordOnlyNeverAffectsDecisions:
    def test_a_raising_helper_does_not_break_the_mark_refresh(self, monkeypatch):
        """The detail is analysis-only. If computing it throws, marks must still
        commit and _dc_refresh_marks must still return True — otherwise a
        recording bug could freeze a live position's P&L (which is exactly what
        the 2026-09-03 mark-sanity incident did, 1,138 rejected marks in a day)."""
        s = _strat(agg=0.5)
        s._dc_read_leg_quotes = MagicMock(return_value=dict(QUOTES))
        s.dc_require_realtime_quotes = False
        s._dc_calendar_value_at = MagicMock(side_effect=RuntimeError("boom"))

        legs = {n: SimpleNamespace(uic=100 + i, price=0.0)
                for i, n in enumerate(("short_call", "long_call", "short_put", "long_put"))}
        entry = SimpleNamespace(legs=legs, contracts=1, entry_number=1,
                                dc_phase=SimpleNamespace(value="calendar"))
        # DCPhase.CALENDAR gate in the sanity guard compares by identity; a
        # non-matching phase object simply skips the guard, which is fine here.

        assert s._dc_refresh_marks(entry) is True
        # marks committed at the ACTING aggressiveness despite the failure
        assert legs["long_call"].price == pytest.approx(
            s._dc_fill_price(QUOTES["long_call"], "sell"))
        assert entry._dc_mark_mid_value is None
