"""
Does the ACCOUNT agree with our P&L? (2026-09-12)

WHY A CUMULATIVE CHECK. `_reconcile_pnl_against_broker` compares one day against
IBKR's `raw_ledger.USD.realizedpnl`. On 2026-09-11 that read IBKR $245.41 against
our $883.40 — a 72% gap that looked like a serious P&L error and was not. IBKR's
realizedpnl LAGS 0DTE expiry settlement: we book the kept credit at settlement,
IBKR books it when the expiry clears, often the next session. A single-day
comparison therefore measures timing, not correctness.

Comparing the ACCOUNT's own day-over-day movement fixes that: timing noise in one
window reappears with the opposite sign in the next. A persistent ONE-SIDED drift
means our P&L is genuinely wrong; noise that cancels means the account agrees.

Real first run, which is why the conclusion was "timing, not error":
    09-08 -> 09-09   account  +618.31   claimed  +623.15   drift   -4.84
    09-09 -> 09-10   account  +853.44   claimed +1133.50   drift -280.06
    09-10 -> 09-11   account +1789.12   claimed +1804.60   drift  -15.48

THE SOURCE IS A PROXY. "Available funds" is reduced by margin held, so it is not
net liquidation. Taking the FIRST snapshot of each day (before that day's
entries) mitigates but does not remove this. The tool says so rather than
presenting the number as exact — a proxy quoted as a measurement is how the
fill-quality tool went wrong.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_pnl_vs_account import compare, first_snapshot_per_day  # noqa: E402

SRC = (Path(__file__).resolve().parents[1]
       / "scripts" / "verify_pnl_vs_account.py").read_text()


def _log(tmp_path, lines):
    p = tmp_path / "bot.log"
    p.write_text("\n".join(lines))
    return str(tmp_path / "bot.log*")


def _snap(date, t, amount):
    return (f"{date} {t} | INFO | bots.hydra.base_strategy | ORDER-004: "
            f"Margin snapshot — Available: ${amount} (via MarginAvailableForTrading)")


class TestItTakesTheFIRSTSnapshotOfEachDay:
    """The first reading is taken BEFORE that day's entries, so it is not yet
    reduced by that day's margin. A later reading would conflate P&L with margin
    held and make every window look wrong."""

    def test_first_of_the_day_wins(self, tmp_path):
        g = _log(tmp_path, [_snap("2026-09-08", "09:45:00", "1,007,274.19"),
                            _snap("2026-09-08", "10:15:00", "1,005,000.00"),
                            _snap("2026-09-08", "12:45:00", "1,003,000.00")])
        assert first_snapshot_per_day(g) == {"2026-09-08": 1007274.19}

    def test_it_parses_ibkrs_comma_formatting(self, tmp_path):
        g = _log(tmp_path, [_snap("2026-09-08", "09:45:00", "1,007,274.19")])
        assert first_snapshot_per_day(g)["2026-09-08"] == pytest.approx(1007274.19)

    def test_multiple_days_are_kept_separate(self, tmp_path):
        g = _log(tmp_path, [_snap("2026-09-08", "09:45:00", "1,000.00"),
                            _snap("2026-09-09", "09:45:00", "2,000.00")])
        assert sorted(first_snapshot_per_day(g)) == ["2026-09-08", "2026-09-09"]

    def test_unrelated_lines_are_ignored(self, tmp_path):
        g = _log(tmp_path, ["2026-09-08 09:00:00 | INFO | something else entirely",
                            _snap("2026-09-08", "09:45:00", "1,000.00")])
        assert first_snapshot_per_day(g) == {"2026-09-08": 1000.0}


def _db(tmp_path, rows):
    p = tmp_path / "t.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE daily_summaries (date TEXT, net_pnl REAL)")
    for d, v in rows:
        con.execute("INSERT INTO daily_summaries VALUES (?,?)", (d, v))
    con.commit(); con.close()
    return str(p)


class TestTheWindowArithmetic:
    def test_a_window_attributes_the_FROM_days_pnl(self, tmp_path):
        """The move from day A's opening snapshot to day B's captures day A's
        trading, not day B's."""
        snaps = {"2026-09-08": 1000.0, "2026-09-09": 1600.0}
        db = _db(tmp_path, [("2026-09-08", 600.0), ("2026-09-09", 999.0)])
        r = compare(snaps, db)[0]
        assert r["moved"] == pytest.approx(600.0)
        assert r["claimed"] == pytest.approx(600.0)   # 09-09 excluded
        assert r["drift"] == pytest.approx(0.0)

    def test_an_overstatement_shows_as_NEGATIVE_drift(self, tmp_path):
        """We claim more than the account moved."""
        snaps = {"2026-09-08": 1000.0, "2026-09-09": 1100.0}
        db = _db(tmp_path, [("2026-09-08", 500.0)])
        assert compare(snaps, db)[0]["drift"] == pytest.approx(-400.0)

    def test_an_understatement_shows_as_POSITIVE_drift(self, tmp_path):
        snaps = {"2026-09-08": 1000.0, "2026-09-09": 1500.0}
        db = _db(tmp_path, [("2026-09-08", 100.0)])
        assert compare(snaps, db)[0]["drift"] == pytest.approx(400.0)

    def test_a_losing_day_is_handled(self, tmp_path):
        snaps = {"2026-09-08": 1000.0, "2026-09-09": 700.0}
        db = _db(tmp_path, [("2026-09-08", -300.0)])
        assert compare(snaps, db)[0]["drift"] == pytest.approx(0.0)

    def test_the_real_2026_09_numbers_reproduce(self, tmp_path):
        """Regression pin on the run that produced the conclusion."""
        snaps = {"2026-09-08": 1007274.19, "2026-09-09": 1007892.50,
                 "2026-09-10": 1008745.94, "2026-09-11": 1010535.06}
        db = _db(tmp_path, [("2026-09-08", 623.15), ("2026-09-09", 1133.50),
                            ("2026-09-10", 1804.60)])
        rows = compare(snaps, db)
        assert [round(r["drift"], 2) for r in rows] == [-4.84, -280.06, -15.48]


class TestItIsHonestAboutBeingAProxy:
    """A proxy quoted as a measurement is how the fill-quality tool went wrong."""

    def test_it_states_available_funds_is_not_net_liquidation(self):
        assert "not net liquidation" in SRC or "is not net\n    liquidation" in SRC
        assert "reduced by margin held" in SRC

    def test_it_names_what_would_corrupt_the_comparison(self):
        assert "deposits" in SRC and "another variant going live" in SRC

    def test_it_explains_why_the_DAILY_check_looked_alarming(self):
        assert "LAGS 0DTE expiry" in SRC
        assert "$245.41" in SRC

    def test_it_only_flags_a_ONE_SIDED_drift(self):
        """Alternating drift is timing. Calling that an error would cry wolf on
        every settlement lag."""
        assert "ONE-SIDED" in SRC
        assert "settlement" in SRC.lower()
