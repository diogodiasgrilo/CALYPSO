"""
HERMES and HOMER must not analyse a day that has not settled (2026-09-10).

THE ORIGINAL BUG. Both agents are timer-driven. They fired at 19:00 and 19:30 ET
while settlement completed between 21:45 and 22:37 ET — so both ran roughly
three hours early, EVERY trading day. HOMER's output is committed to git, so the
trading journal has been carrying unsettled numbers. Both unit files said
"post-settlement" the whole time; the schedules simply never matched.

WHY A TIMER MOVE ALONE IS NOT ENOUGH. Moving them to 23:00 / 23:30 fixes the
common case, but a fixed clock time is a guess against a variable event: 0DTE
SPX is PM-settled and IBKR's position feed clears on its own schedule. If
completion ever slips past the timer, we are silently back to the original bug.

THE SIGNAL is a `daily_summaries` row for the date, read from the SAME database
the agent reads (via resolve_agent_source). The bot writes that row only after
check_after_hours_settlement() returns True, so it is the bot's own statement
that the day is done. Checking a separately-configured path would let the gate
pass while the agent still gets nothing.

FAIL CLOSED is normally a dangerous default — a silent skip can mean the job
never runs again — but it is recoverable here: HOMER's detect_missing_days()
back-fills skipped days on a later run, whereas committing wrong numbers to git
is not recoverable in the same way.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.settlement_gate import (  # noqa: E402
    OVERRIDE_ENV,
    require_settled,
    settlement_is_complete,
)

DATE = "2026-09-10"


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    """Every test starts with the operator override OFF."""
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)


def _db(tmp_path, dates=()):
    p = tmp_path / "backtesting.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE daily_summaries (date TEXT, net_pnl REAL)")
    for d in dates:
        con.execute("INSERT INTO daily_summaries VALUES (?, 0.0)", (d,))
    con.commit()
    con.close()
    return str(p)


def _cfg(db):
    return {"homer": {"data_source": "db", "read_db": db},
            "hermes": {"data_source": "db", "read_db": db}}


class TestTheGateItself:
    def test_a_settled_day_passes(self, tmp_path):
        ok, why = settlement_is_complete(
            _cfg(_db(tmp_path, [DATE])), agent="homer", date_str=DATE)
        assert ok is True
        assert DATE in why

    def test_an_unsettled_day_is_blocked(self, tmp_path):
        """The actual bug: the row is not there yet because the bot is still
        settling."""
        ok, why = settlement_is_complete(
            _cfg(_db(tmp_path, [])), agent="homer", date_str=DATE)
        assert ok is False
        assert "no daily_summaries row" in why

    def test_another_days_row_does_not_count(self, tmp_path):
        """Yesterday having settled says nothing about today — and yesterday's
        row is ALWAYS present by the time today's agent runs, so getting this
        wrong would make the gate permanently useless."""
        ok, _ = settlement_is_complete(
            _cfg(_db(tmp_path, ["2026-09-09"])), agent="homer", date_str=DATE)
        assert ok is False

    def test_a_missing_database_is_blocked_not_waved_through(self, tmp_path):
        ok, why = settlement_is_complete(
            _cfg(str(tmp_path / "nope.db")), agent="homer", date_str=DATE)
        assert ok is False
        assert "not found" in why

    def test_an_unreadable_database_is_blocked(self, tmp_path):
        p = tmp_path / "corrupt.db"
        p.write_text("this is not a database")
        ok, why = settlement_is_complete(
            _cfg(str(p)), agent="homer", date_str=DATE)
        assert ok is False
        assert "could not read" in why

    def test_the_reason_is_always_populated(self, tmp_path):
        """When the gate blocks a run, the reason is the only thing an operator
        has to go on."""
        for cfg in (_cfg(_db(tmp_path, [DATE])), _cfg(str(tmp_path / "x.db"))):
            _, why = settlement_is_complete(cfg, agent="homer", date_str=DATE)
            assert why and isinstance(why, str)


class TestItReadsTheAGENTS_OwnDatabase:
    def test_each_agent_resolves_its_own_read_db(self, tmp_path):
        """A gate that checks a DIFFERENT database than the agent reads could
        pass while the agent still gets nothing."""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        settled = _db(tmp_path / "a", [DATE])
        unsettled = _db(tmp_path / "b", [])
        cfg = {
            "homer": {"data_source": "db", "read_db": settled},
            "hermes": {"data_source": "db", "read_db": unsettled},
        }
        assert settlement_is_complete(cfg, agent="homer", date_str=DATE)[0] is True
        assert settlement_is_complete(cfg, agent="hermes", date_str=DATE)[0] is False


class TestLegacySheetsModeIsNotBlocked:
    def test_sheets_mode_passes(self):
        """There is no cheap settlement signal in Sheets mode, and blocking a
        path that is not in use would be a regression, not a safeguard."""
        ok, why = settlement_is_complete(
            {"homer": {"data_source": "sheets"}}, agent="homer", date_str=DATE)
        assert ok is True
        assert "not db" in why


class TestTheOperatorOverride:
    def test_override_bypasses_a_blocked_gate(self, tmp_path, monkeypatch):
        monkeypatch.setenv(OVERRIDE_ENV, "1")
        ok, why = settlement_is_complete(
            _cfg(_db(tmp_path, [])), agent="homer", date_str=DATE)
        assert ok is True
        assert "bypassed" in why

    @pytest.mark.parametrize("val", ["", "0", "false", "False"])
    def test_falsey_values_do_NOT_bypass(self, tmp_path, monkeypatch, val):
        """An env var left set to 0 must not silently disable the gate."""
        monkeypatch.setenv(OVERRIDE_ENV, val)
        ok, _ = settlement_is_complete(
            _cfg(_db(tmp_path, [])), agent="homer", date_str=DATE)
        assert ok is False


class TestRequireSettledWrapper:
    def test_it_returns_true_when_settled(self, tmp_path):
        assert require_settled(
            _cfg(_db(tmp_path, [DATE])), agent="homer", date_str=DATE) is True

    def test_it_returns_false_when_not(self, tmp_path):
        assert require_settled(
            _cfg(_db(tmp_path, [])), agent="homer", date_str=DATE) is False


class TestTheAgentsActuallyCallIt:
    """Wiring guards. The gate is worthless if it is never invoked."""

    def test_hermes_calls_the_gate(self):
        import inspect
        import services.hermes.main as h
        src = inspect.getsource(h.main)
        assert "require_settled" in src
        assert 'agent="hermes"' in src

    def test_homer_calls_the_gate(self):
        import inspect
        import services.homer.main as h
        src = inspect.getsource(h.main)
        assert "require_settled" in src
        assert 'agent="homer"' in src

    def test_homer_gates_AFTER_the_backfill_branch(self):
        """--backfill deliberately reprocesses historical days and must not be
        blocked on TODAY having settled."""
        import inspect
        import services.homer.main as h
        src = inspect.getsource(h.main)
        assert src.index("_run_backfill()") < src.index("require_settled")

    def test_homer_does_not_gate_a_dry_run(self):
        """--dry-run writes nothing and commits nothing, so an operator can
        still inspect an in-progress day."""
        import inspect
        import services.homer.main as h
        assert "not args.dry_run and not require_settled" in inspect.getsource(h.main)
