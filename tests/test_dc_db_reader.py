"""DCDBReader — shape-distinct, debit-native calendar DB reader (Phase 5).

Pins the audit AUD-3-F2 / AUD-4-F4 corrections:
  * P&L rolled up by close_date (NOT entry_date) so the cumulative curve never
    mutates retroactively;
  * capital basis = SUM(net_debit) (not spread-width notional);
  * the cumulative-overrides output carries NO credit/buffer/spread-width keys;
  * read-only + missing-DB tolerance (empty/zero, never crash).
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.backend.services.dc_db_reader import DCDBReader


def _seed(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE dc_outcomes (
            entry_date TEXT, close_date TEXT, entry_number INTEGER, strategy_id TEXT,
            terminal_state TEXT, realized_pnl REAL, spx_at_close REAL,
            close_commission REAL, transform_credit REAL, net_debit REAL,
            PRIMARY KEY (entry_date, entry_number)
        );
        """
    )
    conn.executemany(
        "INSERT INTO dc_outcomes (entry_date, close_date, entry_number, strategy_id, "
        "terminal_state, realized_pnl, spx_at_close, close_commission, transform_credit, net_debit) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            # Two calendars ENTERED on the same earlier day but CLOSING on
            # different later days — proves close_date (not entry_date) keying.
            ("2026-06-09", "2026-06-12", 1, "x_1", "settled", 100.0, 5000.0, 2.0, 0.0, 200.0),
            ("2026-06-09", "2026-06-13", 2, "x_2", "stopped", -40.0, 4990.0, 2.0, 0.0, 150.0),
            # A third closing on 06-13 too → two outcomes roll into one close-day.
            ("2026-06-10", "2026-06-13", 1, "x_3", "settled", 60.0, 5010.0, 2.0, 0.0, 100.0),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def reader(tmp_path):
    db = tmp_path / "dc_calendar.db"
    _seed(db)
    return DCDBReader(db)


def test_daily_summaries_keyed_on_close_date(reader):
    rows = asyncio.run(reader.get_daily_calendar_summaries())
    # Oldest-first; one row per CLOSE date. 06-12 = 100; 06-13 = -40 + 60 = 20.
    assert [(r["date"], r["net_pnl"]) for r in rows] == [
        ("2026-06-12", 100.0),
        ("2026-06-13", 20.0),
    ]
    # 06-13 aggregated TWO outcomes.
    assert rows[1]["outcomes"] == 2


def test_cumulative_overrides_debit_native(reader):
    o = asyncio.run(reader.get_cumulative_overrides_calendar())
    assert o["cumulative_pnl"] == pytest.approx(120.0)        # 100 - 40 + 60
    assert o["capital_at_risk"] == pytest.approx(450.0)       # 200 + 150 + 100 = SUM(net_debit)
    assert o["total_calendars"] == 3
    assert o["entry_days"] == 2                                # 06-09, 06-10
    # win/loss DAYS keyed on close_date: 06-12 (+100) win, 06-13 (+20) win → 2/0
    assert o["winning_days"] == 2
    assert o["losing_days"] == 0
    assert o["close_days"] == 2
    assert o["roi_pct"] == pytest.approx(round(120.0 / 450.0 * 100, 2))
    assert o["last_updated"] == "2026-06-13"


def test_cumulative_overrides_has_no_ic_keys(reader):
    o = asyncio.run(reader.get_cumulative_overrides_calendar())
    forbidden = {
        "total_credit", "total_credit_collected", "total_credit_received",
        "buffer", "call_stop_buffer", "put_stop_buffer", "capital_deployed",
        "call_spread_width", "put_spread_width",
    }
    assert not (set(o.keys()) & forbidden)


def test_outcomes_recent(reader):
    outs = asyncio.run(reader.get_calendar_outcomes(limit=10))
    assert len(outs) == 3
    # net_debit present (debit basis); no credit/width columns selected.
    assert all("net_debit" in r for r in outs)
    assert all("call_spread_width" not in r for r in outs)


def test_missing_db_is_empty_not_crash(tmp_path):
    r = DCDBReader(tmp_path / "does_not_exist.db")
    assert asyncio.run(r.is_available()) is False
    assert asyncio.run(r.get_daily_calendar_summaries()) == []
    assert asyncio.run(r.get_calendar_outcomes()) == []
    o = asyncio.run(r.get_cumulative_overrides_calendar())
    assert o["cumulative_pnl"] == 0.0
    assert o["capital_at_risk"] == 0.0
    assert o["winning_days"] == 0


def test_read_only_connection_rejects_writes(reader):
    # The reader opens ?mode=ro + PRAGMA query_only=TRUE. A write must fail.
    conn = reader._get_connection()
    assert conn is not None
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO dc_outcomes (entry_date, entry_number) VALUES ('x', 99)")


def test_baseline_hides_pre_baseline_entries(reader):
    # baseline 2026-06-10 keys on ENTRY_date: the two 06-09-entered calendars are
    # hidden even though they CLOSE on 06-12/06-13 (>= baseline); only x_3, ENTERED
    # 06-10, survives. This is the D/E cull semantic (a pre-fix calendar can't
    # reappear by closing after the baseline).
    o = asyncio.run(reader.get_cumulative_overrides_calendar("2026-06-10"))
    assert o["cumulative_pnl"] == pytest.approx(60.0)     # only x_3
    assert o["capital_at_risk"] == pytest.approx(100.0)   # only x_3's net_debit
    assert o["total_calendars"] == 1
    assert o["entry_days"] == 1
    assert o["winning_days"] == 1 and o["losing_days"] == 0

    outs = asyncio.run(reader.get_calendar_outcomes(limit=10, baseline_date="2026-06-10"))
    assert [r["entry_date"] for r in outs] == ["2026-06-10"]

    daily = asyncio.run(reader.get_daily_calendar_summaries(baseline_date="2026-06-10"))
    assert [(r["date"], r["net_pnl"]) for r in daily] == [("2026-06-13", 60.0)]


def test_baseline_in_future_zeroes_everything(reader):
    # A baseline AFTER every entry (the 2026-07-07 D/E case before any post-fix
    # trade exists) → empty window → authoritative zeros/empties, NOT full history.
    o = asyncio.run(reader.get_cumulative_overrides_calendar("2026-07-07"))
    assert o["cumulative_pnl"] == 0.0
    assert o["capital_at_risk"] == 0.0
    assert o["total_calendars"] == 0
    assert o["entry_days"] == 0
    assert o["winning_days"] == 0
    assert asyncio.run(reader.get_calendar_outcomes(baseline_date="2026-07-07")) == []
    assert asyncio.run(reader.get_daily_calendar_summaries(baseline_date="2026-07-07")) == []
