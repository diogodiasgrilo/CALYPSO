"""Dashboard cumulative-rebase regression (2026-06-04).

The A/B/C cumulative track record can be rebased to a baseline date so the
single P&L card + the Comparison lifetime totals + the running-cumulative
curves all start from a chosen day (WHERE date >= baseline), zeroing prior
history — without mutating the DB. These tests pin that:

  * get_cumulative_overrides(baseline) rebases EVERY aggregate consistently
    (P&L sum, win/loss day counts, entry/stop/credit totals) — never a rebased
    P&L mixed with a lifetime count;
  * get_all_summaries(baseline) / get_daily_pnls(baseline) return only >= baseline;
  * _cumulative_series over the filtered summaries starts at the baseline day;
  * empty baseline == full history (byte-identical to before the feature).
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.backend.services.db_reader import BacktestingDBReader

BASELINE = "2026-06-05"


def _cumulative_series(summaries):
    """Mirror of variants.py:_cumulative_series (running net-P&L total).

    Inlined rather than imported so this test doesn't pull in fastapi (the
    dashboard router deps aren't installed in the bot's test venv); the logic is
    a plain running sum, so duplicating it here keeps the test hermetic.
    """
    out, running = [], 0.0
    for s in summaries:
        net = s.get("net_pnl") or 0.0
        running += net
        out.append({"date": s["date"], "net_pnl": net, "cumulative": round(running, 2)})
    return out


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE daily_summaries (date TEXT PRIMARY KEY, net_pnl REAL);
        CREATE TABLE trade_entries (
            date TEXT, entry_number INTEGER, total_credit REAL,
            call_spread_width REAL, put_spread_width REAL, contracts INTEGER
        );
        CREATE TABLE trade_stops (date TEXT, entry_number INTEGER, side TEXT, net_pnl REAL);
        """
    )
    # 2 pre-baseline days, 1 boundary-1 day, 2 post-baseline days.
    conn.executemany(
        "INSERT INTO daily_summaries (date, net_pnl) VALUES (?, ?)",
        [
            ("2026-06-02", 100.0),   # pre
            ("2026-06-03", 200.0),   # pre
            ("2026-06-04", -50.0),   # pre (the skewed day — a LOSS, to exercise losing_days)
            ("2026-06-05", 300.0),   # >= baseline
            ("2026-06-06", 400.0),   # >= baseline
        ],
    )
    conn.executemany(
        "INSERT INTO trade_entries "
        "(date, entry_number, total_credit, call_spread_width, put_spread_width, contracts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            # capital = max(call_w, put_w) * 100 * contracts
            ("2026-06-02", 1, 10.0, 10.0, 10.0, 1),    # 1000   (pre)
            ("2026-06-03", 1, 20.0, 20.0, 20.0, 1),    # 2000   (pre)
            ("2026-06-05", 1, 30.0, 0.0, 5.0, 10),     # 5000   (>= baseline; put-only)
            ("2026-06-06", 1, 40.0, 5.0, 5.0, 10),     # 5000   (>= baseline)
            ("2026-06-06", 2, 50.0, 5.0, 0.0, 10),     # 5000   (>= baseline; call-only)
        ],
    )
    conn.executemany(
        "INSERT INTO trade_stops (date, entry_number, side, net_pnl) VALUES (?, ?, ?, ?)",
        [
            # No exit_reason column in this fixture (pre-v11 shape) -- net_pnl < 0
            # is the fallback "genuine stop" definition, so each must be a real loss.
            ("2026-06-03", 1, "call", -150.0),
            ("2026-06-04", 1, "put", -75.0),
            ("2026-06-05", 1, "put", -200.0),
        ],
    )
    conn.commit()
    conn.close()


def _reader(tmp_path: Path) -> BacktestingDBReader:
    db = tmp_path / "backtesting.db"
    _seed_db(db)
    return BacktestingDBReader(db)


def test_total_stops_excludes_take_profit_when_exit_reason_present(tmp_path):
    """2026-09-02: the dashboard's total_stops must use the same canonical
    "genuine stop" definition as the bot's own self-heal (exit_reason IN
    stop_loss/gex_breach) once that column exists — a Brandon take-profit
    WIN must not inflate the stop count just because it also writes a
    trade_stops row."""
    db = tmp_path / "backtesting.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE daily_summaries (date TEXT PRIMARY KEY, net_pnl REAL);
        CREATE TABLE trade_entries (
            date TEXT, entry_number INTEGER, total_credit REAL,
            call_spread_width REAL, put_spread_width REAL, contracts INTEGER
        );
        CREATE TABLE trade_stops (
            date TEXT, entry_number INTEGER, side TEXT, net_pnl REAL, exit_reason TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO trade_stops (date, entry_number, side, net_pnl, exit_reason) VALUES (?, ?, ?, ?, ?)",
        [
            ("2026-06-10", 1, "put", -100.0, "stop_loss"),
            ("2026-06-10", 2, "call", -80.0, "gex_breach"),
            ("2026-06-10", 3, "put", 60.0, "take_profit"),   # a WIN -- must NOT count
            ("2026-06-10", 4, "put", -30.0, "early_close"),  # EOD flatten -- must NOT count
        ],
    )
    conn.commit()
    conn.close()
    o = asyncio.run(BacktestingDBReader(db).get_cumulative_overrides())
    assert o["total_stops"] == 2  # stop_loss + gex_breach only


def test_full_history_when_no_baseline(tmp_path):
    r = _reader(tmp_path)
    o = asyncio.run(r.get_cumulative_overrides(""))
    assert o["cumulative_pnl"] == 950.0            # 100+200-50+300+400
    assert o["winning_days"] == 4
    assert o["losing_days"] == 1
    assert o["total_entries"] == 5
    assert o["total_credit_collected"] == 150.0
    assert o["total_stops"] == 3
    assert o["last_updated"] == "2026-06-06"
    # Capital deployed + ROI over full history.
    assert o["capital_deployed"] == 18000.0     # 1000+2000+5000+5000+5000
    assert o["avg_capital_per_day"] == 4500.0    # 18000 / 4 entry-days
    assert o["roi_pct"] == round(950.0 / 18000.0 * 100, 2)   # 5.28%
    # No-arg call is identical to empty-string (default param).
    assert asyncio.run(r.get_cumulative_overrides()) == o


def test_overrides_rebased_to_baseline(tmp_path):
    r = _reader(tmp_path)
    o = asyncio.run(r.get_cumulative_overrides(BASELINE))
    assert o["cumulative_pnl"] == 700.0            # 300+400 only
    assert o["winning_days"] == 2                  # 06-05, 06-06
    assert o["losing_days"] == 0                   # the -50 (06-04) is pre-baseline
    assert o["total_entries"] == 3                 # 06-05 + two 06-06
    assert o["total_credit_collected"] == 120.0    # 30+40+50
    assert o["total_stops"] == 1                   # only 06-05's stop
    assert o["last_updated"] == "2026-06-06"
    # Capital + ROI rebase too: only the 3 post-baseline entries (3×5000).
    assert o["capital_deployed"] == 15000.0
    assert o["avg_capital_per_day"] == 7500.0      # 15000 / 2 entry-days
    assert o["roi_pct"] == round(700.0 / 15000.0 * 100, 2)   # 4.67%


def test_empty_window_returns_zero_not_null(tmp_path):
    # Baseline AFTER all data (e.g. baseline=tomorrow): the rebased window is
    # empty. The sums must COALESCE to 0.0 (not SQL NULL) so apply_db_cumulative
    # OVERRIDES the stale metrics file instead of leaking it through. This is the
    # exact "card shows the old metrics cumulative since <future date>" bug.
    r = _reader(tmp_path)
    o = asyncio.run(r.get_cumulative_overrides("2026-06-10"))
    assert o["cumulative_pnl"] == 0.0          # not None
    assert o["capital_deployed"] == 0.0        # not None
    assert o["total_credit_collected"] == 0.0  # not None
    assert o["winning_days"] == 0
    assert o["losing_days"] == 0
    assert o["roi_pct"] == 0.0
    assert o["avg_capital_per_day"] == 0.0


def test_no_baseline_empty_db_preserves_null_fallback(tmp_path):
    # WITHOUT a baseline, a genuinely-empty DB keeps the legacy NULL so the
    # metrics-file fallback in apply_db_cumulative still applies (no regression).
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE daily_summaries (date TEXT, net_pnl REAL);"
        "CREATE TABLE trade_entries (date TEXT, total_credit REAL, "
        "call_spread_width REAL, put_spread_width REAL, contracts INTEGER);"
        "CREATE TABLE trade_stops (date TEXT, net_pnl REAL);"
    )
    conn.commit()
    conn.close()
    o = asyncio.run(BacktestingDBReader(db).get_cumulative_overrides())
    assert o["cumulative_pnl"] is None         # NULL → metrics-file fallback
    assert o["capital_deployed"] is None


def test_summaries_and_pnls_rebased(tmp_path):
    r = _reader(tmp_path)
    full = asyncio.run(r.get_all_summaries())
    assert [s["date"] for s in full] == [
        "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05", "2026-06-06",
    ]
    rebased = asyncio.run(r.get_all_summaries(BASELINE))
    assert [s["date"] for s in rebased] == ["2026-06-05", "2026-06-06"]

    assert asyncio.run(r.get_daily_pnls()) == [100.0, 200.0, -50.0, 300.0, 400.0]
    assert asyncio.run(r.get_daily_pnls(BASELINE)) == [300.0, 400.0]


def test_cumulative_curve_starts_at_baseline(tmp_path):
    r = _reader(tmp_path)
    rebased = asyncio.run(r.get_all_summaries(BASELINE))
    series = _cumulative_series(rebased)
    assert [(p["date"], p["cumulative"]) for p in series] == [
        ("2026-06-05", 300.0),
        ("2026-06-06", 700.0),   # running total starts fresh at the baseline day
    ]
