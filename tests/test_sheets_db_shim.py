"""Tests for the DB-backed Sheets shim (Google-Sheets-retirement migration).

The critical test is the round-trip: a daily_summaries DB row -> the shim's
Sheet-labeled dict -> HOMER's forward `_build_summary_record` -> must reproduce
the original DB row's P&L-critical fields. That proves the shim's reverse column
map is the exact inverse of HOMER's forward map, so agents can read the DB shim
instead of the Sheet with no behavior change.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from shared.sheets_db_shim import DbSheetsReader

# (column, sqlite type) — mirror the real daily_summaries types so numeric
# columns come back as REAL, not TEXT.
_DAILY_COLS = [
    ("date", "TEXT"), ("spx_open", "REAL"), ("spx_close", "REAL"), ("spx_high", "REAL"),
    ("spx_low", "REAL"), ("vix_open", "REAL"), ("vix_close", "REAL"),
    ("entries_placed", "INTEGER"), ("entries_stopped", "INTEGER"), ("gross_pnl", "REAL"),
    ("net_pnl", "REAL"), ("commission", "REAL"), ("long_salvage_revenue", "REAL"),
    ("economic_events", "TEXT"), ("day_type", "TEXT"),
]


def _make_db(tmp_path, rows, stops=None):
    db = str(tmp_path / "bt.db")
    conn = sqlite3.connect(db)
    conn.execute(f"CREATE TABLE daily_summaries ({', '.join(c + ' ' + t for c, t in _DAILY_COLS)})")
    conn.execute(
        "CREATE TABLE trade_stops (date TEXT, entry_number INTEGER, side TEXT, exit_reason TEXT, net_pnl REAL)"
    )
    conn.execute("CREATE TABLE trade_entries (date TEXT, entry_number INTEGER)")
    for r in rows:
        cols = ", ".join(r.keys())
        ph = ", ".join("?" * len(r))
        conn.execute(f"INSERT INTO daily_summaries ({cols}) VALUES ({ph})", tuple(r.values()))
    for s in (stops or []):
        # (date, entry, side[, exit_reason[, net_pnl]]) — defaults stop_loss / -100.0
        row = (s[0], s[1], s[2], s[3] if len(s) > 3 else "stop_loss", s[4] if len(s) > 4 else -100.0)
        conn.execute(
            "INSERT INTO trade_stops (date, entry_number, side, exit_reason, net_pnl) VALUES (?, ?, ?, ?, ?)",
            row,
        )
    conn.commit()
    conn.close()
    return db


def test_daily_summary_shape_and_reconstructed_fields(tmp_path):
    db = _make_db(
        tmp_path,
        [
            dict(date="2026-07-14", spx_open=7528.0, spx_close=7544.8, spx_high=7556.5,
                 spx_low=7514.1, vix_open=16.72, vix_close=16.38, entries_placed=2,
                 entries_stopped=0, gross_pnl=805.0, net_pnl=740.6, commission=64.4,
                 long_salvage_revenue=0.0, economic_events="", day_type=None),
            dict(date="2026-07-16", spx_open=7558.34, spx_close=7532.35, spx_high=7570.5,
                 spx_low=7505.52, vix_open=16.17, vix_close=16.6, entries_placed=2,
                 entries_stopped=2, gross_pnl=-3395.0, net_pnl=-3459.4, commission=64.4,
                 long_salvage_revenue=0.0, economic_events="", day_type=None),
        ],
        stops=[
            ("2026-07-16", 1, "put", "stop_loss"),
            ("2026-07-16", 2, "put", "stop_loss"),
            ("2026-07-16", 1, "call", "early_close"),  # EOD flatten — must NOT be counted
        ],
    )
    r = DbSheetsReader(db)
    rows = r.read_tab_as_dicts("X", "Daily Summary")
    assert len(rows) == 2
    r0, r1 = rows
    assert r0["Date"] == "2026-07-14"
    assert float(r0["Daily P&L ($)"]) == 740.6
    assert float(r0["SPX Open"]) == 7528.0
    assert r0["Cumulative P&L ($)"] == 740.6                       # first day
    assert r1["Cumulative P&L ($)"] == round(740.6 - 3459.4, 2)    # running SUM(net_pnl)
    # only stop_loss/gex_breach count; the early_close call is excluded
    assert r1["Put Stops"] == 2 and r1["Call Stops"] == 0
    assert r.get_last_row_as_dict("X", "Daily Summary")["Date"] == "2026-07-16"
    # Trades/Positions are NOT served by the shim (HOMER reads them structured).
    assert r.read_tab_as_dicts("X", "Trades") == []
    assert r.entries_for_day("2026-07-16") == []                  # empty trade_entries in this db


def test_daily_summary_reconstructed_fields(tmp_path):
    """The DB-reconstructed Daily Summary fields (validated field-for-field vs C's
    live Sheet). One day: a won full_ic + a stopped put_only + a skip + VIX ticks."""
    db = str(tmp_path / "bt.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE daily_summaries (date TEXT, net_pnl REAL, gross_pnl REAL, "
                 "long_salvage_revenue REAL, contracts_per_entry INTEGER)")
    conn.execute("CREATE TABLE trade_entries (date TEXT, entry_number INTEGER, entry_type TEXT, "
                 "trend_signal TEXT, total_credit REAL, realized_pnl REAL)")
    conn.execute("CREATE TABLE trade_stops (date TEXT, entry_number INTEGER, side TEXT, "
                 "exit_reason TEXT, net_pnl REAL)")
    conn.execute("CREATE TABLE skipped_entries (date TEXT, entry_number INTEGER)")
    conn.execute("CREATE TABLE market_ticks (timestamp TEXT, vix_level REAL)")
    conn.execute("INSERT INTO daily_summaries VALUES ('2026-07-16', -1310, -1310, 0, 7)")
    conn.executemany("INSERT INTO trade_entries VALUES (?,?,?,?,?,?)", [
        ('2026-07-16', 1, 'full_ic', 'neutral', 300, 300),     # expired, won
        ('2026-07-16', 2, 'put_only', 'neutral', 280, -1610),  # stopped
    ])
    conn.execute("INSERT INTO trade_stops VALUES ('2026-07-16', 2, 'put', 'stop_loss', -1610)")
    conn.execute("INSERT INTO trade_stops VALUES ('2026-07-16', 1, 'call', 'early_close', 5)")  # not a stop
    conn.execute("INSERT INTO skipped_entries VALUES ('2026-07-16', 3)")
    conn.executemany("INSERT INTO market_ticks VALUES (?,?)", [
        ('2026-07-16 10:00:00', 16.0), ('2026-07-16 12:00:00', 17.2), ('2026-07-16 14:00:00', 15.8)])
    conn.commit()
    conn.close()

    r = DbSheetsReader(db).get_last_row_as_dict("X", "Daily Summary")
    assert r["Total Credit ($)"] == 580.0          # 300 + 280
    assert r["Full ICs"] == 1 and r["One-Sided Entries"] == 1
    assert r["Neutral Signals"] == 2
    assert r["Win Rate (%)"] == 50.0               # 1 of 2 profitable
    assert r["Put Stops"] == 1 and r["Call Stops"] == 0   # early_close call excluded
    assert r["Double Stops"] == 0
    assert r["Stop Loss Debits ($)"] == 1610.0     # -SUM(net_pnl) of real stops
    assert r["Expired Credits ($)"] == 300.0       # gross(-1310) + debits(1610) - salvage(0)
    assert r["Entries Skipped"] == 1
    assert r["Contracts"] == 7
    assert r["VIX High"] == 17.2 and r["VIX Low"] == 15.8


def test_positions_serves_recent_entries(tmp_path):
    db = str(tmp_path / "bt.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE daily_summaries (date TEXT, net_pnl REAL)")
    conn.execute("CREATE TABLE trade_stops (date TEXT, side TEXT, exit_reason TEXT)")
    conn.execute(
        "CREATE TABLE trade_entries (date TEXT, entry_number INTEGER, entry_type TEXT, "
        "short_call_strike REAL, long_call_strike REAL, short_put_strike REAL, "
        "long_put_strike REAL, call_credit REAL, put_credit REAL, total_credit REAL, realized_pnl REAL)"
    )
    conn.execute(
        "INSERT INTO trade_entries VALUES ('2026-07-16', 1, 'put_only', 0, 0, 7515, 7510, 0, 105, 105, -1785)"
    )
    conn.commit()
    conn.close()
    r = DbSheetsReader(db)
    pos = r.read_tab_as_dicts("X", "Positions", limit_rows=20)   # recent entries as raw context
    assert len(pos) == 1
    assert pos[0]["entry_type"] == "put_only" and pos[0]["short_put_strike"] == 7515
    assert r.read_tab_as_dicts("X", "Trades") == []              # Trades still not served here


def test_factory_db_mode_and_path_resolution():
    from shared.sheets_db_shim import make_agent_reader, DbSheetsReader
    # read_db is preferred over backtesting_db (so it never collides with HOMER's write path)
    r = make_agent_reader({"data_source": "db", "read_db": "data/variant_c/backtesting.db",
                           "backtesting_db": "data/backtesting.db"})
    assert isinstance(r, DbSheetsReader) and r.db_path == "data/variant_c/backtesting.db"
    # falls back to backtesting_db when read_db unset
    assert make_agent_reader({"data_source": "db", "backtesting_db": "data/x.db"}).db_path == "data/x.db"
    # default path when neither set
    assert make_agent_reader({"data_source": "db"}).db_path == "data/backtesting.db"


def test_factory_per_agent_override_and_isolation(monkeypatch):
    import shared.sheets_reader as sr
    sentinel = object()
    monkeypatch.setattr(sr, "SheetsReader", lambda cfg: sentinel)
    from shared.sheets_db_shim import make_agent_reader, DbSheetsReader
    # CLIO flipped to DB via its OWN section...
    cfg = {"clio": {"data_source": "db", "read_db": "data/variant_c/backtesting.db"}}
    r = make_agent_reader(cfg, agent="clio")
    assert isinstance(r, DbSheetsReader) and r.db_path == "data/variant_c/backtesting.db"
    # ...while HERMES (no per-agent flag) stays on Sheets — one-at-a-time rollout
    assert make_agent_reader(cfg, agent="hermes") is sentinel
    assert make_agent_reader(cfg) is sentinel  # no agent → top-level default (sheets)


def test_factory_default_is_sheets(monkeypatch):
    import shared.sheets_reader as sr
    sentinel = object()
    monkeypatch.setattr(sr, "SheetsReader", lambda cfg: sentinel)
    from shared.sheets_db_shim import make_agent_reader
    assert make_agent_reader({}) is sentinel                       # default
    assert make_agent_reader({"data_source": "sheets"}) is sentinel
    assert make_agent_reader({"data_source": "SHEETS"}) is sentinel  # case-insensitive


def test_roundtrip_matches_homer_forward_map(tmp_path):
    try:
        from services.homer.data_collector import _build_summary_record
    except Exception as e:  # pragma: no cover - import guard
        pytest.skip(f"HOMER import unavailable: {e}")

    orig = dict(date="2026-07-16", spx_open=7558.34, spx_close=7532.35, spx_high=7570.5,
                spx_low=7505.52, vix_open=16.17, vix_close=16.6, entries_placed=2,
                entries_stopped=2, gross_pnl=-3395.0, net_pnl=-3459.4, commission=64.4,
                long_salvage_revenue=0.0, economic_events="", day_type=None)
    db = _make_db(tmp_path, [orig], stops=[("2026-07-16", 1, "put"), ("2026-07-16", 2, "put")])
    sheet_row = DbSheetsReader(db).get_last_row_as_dict("X", "Daily Summary")

    stop_records = [{"entry_number": 1, "side": "put"}, {"entry_number": 2, "side": "put"}]
    rec = _build_summary_record(sheet_row, "2026-07-16", ticks=[], stop_records=stop_records)

    assert abs(rec["net_pnl"] - orig["net_pnl"]) < 0.01
    assert abs(rec["commission"] - orig["commission"]) < 0.01
    assert abs(rec["gross_pnl"] - orig["gross_pnl"]) < 0.01        # gross = net + commission
    assert rec["spx_open"] == orig["spx_open"]
    assert rec["vix_close"] == orig["vix_close"]
    assert rec["entries_placed"] == orig["entries_placed"]
    assert rec["entries_stopped"] == 2                            # from stop_records
