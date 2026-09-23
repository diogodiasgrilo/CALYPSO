"""Variant H's status reader — Playbook Step 8, the Telegram half.

Step 8's exit criterion is "the variant is observable from Telegram and the
dashboard; readers are read-only and tested."

Two properties carry most of the weight here:

* **It cannot write.** This module runs against a database the trading loop is
  actively writing. It opens `mode=ro` and contains no INSERT, and both are
  asserted — a status view that can corrupt the record it displays is worse than
  no status view.
* **It surfaces the peak, not just the exit.** A long strangle can touch +50% on
  a gamma spike and give it all back inside a minute, so "what it reached" and
  "what it captured" are different numbers. Every other variant in this fleet
  sells premium, where the peak barely matters. Here the gap between them is the
  measurement the whole strategy exists to produce, so it is computed and
  displayed rather than left for someone to derive later.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.long_strangle_entry import LongStrangleEntry  # noqa: E402
from bots.hydra.ls_recorder import LongStrangleDataRecorder  # noqa: E402
from bots.hydra.ls_status import (  # noqa: E402
    format_long_strangle_telegram,
    ls_status,
    read_entries,
    read_exits,
    read_marks,
    read_skips,
)

DATE = "2026-09-23"


def _executable_strings(path: Path) -> list:
    """String literals a module actually EXECUTES, docstrings excluded.

    A plain grep over the source also hits the docstring, which names the very
    things it forbids in order to explain why they are absent. Forbidding the
    explanation would push the reasoning out of the file — the opposite of
    useful — so the check parses the AST instead. (Same fix as
    tests/test_ls_recorder_2026_09_23.py.)
    """
    import ast
    tree = ast.parse(path.read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docs.add(d)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docs]


def _entry(n=1, call_px=1.00, put_px=1.45, contracts=2):
    e = LongStrangleEntry(entry_number=n)
    e.contracts = contracts
    e.long_call_strike, e.long_put_strike = 7785.0, 7745.0
    e.long_call_price, e.long_put_price = call_px, put_px
    e.call_debit = call_px * 100 * contracts
    e.put_debit = put_px * 100 * contracts
    e.long_call_uic, e.long_put_uic = 111, 222
    return e


@pytest.fixture
def db(tmp_path):
    """A day with one position that peaked, gave some back, and exited."""
    p = str(tmp_path / "long_strangle.db")
    r = LongStrangleDataRecorder(p)
    e = _entry()
    r.record_entry(e, DATE, 7765.0, vix_at_entry=14.5, em_source="straddle",
                   expected_move=22.0, skew_gap_pct=16.7)
    # Marks: +0%, +82% (the peak), +51% (where it exited).
    for i, (c, pu) in enumerate(((1.00, 1.45), (2.00, 2.45), (1.60, 2.10))):
        e.long_call_price, e.long_put_price = c, pu
        r.record_snapshot(e, DATE, f"10:0{i}", spx=7765.0 + i)
    e.long_call_price, e.long_put_price = 1.60, 2.10
    r.record_exit(e, DATE, exit_reason="profit_target_50", realized_pnl=250.0,
                  commissions=4.6, spx_at_exit=7800.0, minutes_held=42.0,
                  exit_time="10:27")
    r.record_skip(DATE, 2, "10:45", "skew 61.0% > 35.0% tolerance",
                  spx=7770.0, vix=14.6, proposed_call_strike=7790.0,
                  proposed_put_strike=7750.0, proposed_debit=300.0,
                  em_source="straddle", expected_move=20.0, skew_gap_pct=61.0)
    r.close()
    return p


@pytest.fixture
def db_open(tmp_path):
    """A day with one position still open."""
    p = str(tmp_path / "long_strangle.db")
    r = LongStrangleDataRecorder(p)
    e = _entry()
    r.record_entry(e, DATE, 7765.0, em_source="vix", expected_move=71.0)
    e.long_call_price, e.long_put_price = 1.30, 1.50
    r.record_snapshot(e, DATE, "10:05", spx=7770.0)
    r.close()
    return p


# ======================================================================
# It cannot touch the record it displays
# ======================================================================

class TestItIsReadOnly:
    def test_the_module_executes_no_write_statement(self):
        """Verifiable by reading the module, not by hoping the tests covered
        every path — but over EXECUTABLE string literals via the AST, since the
        docstring names these verbs on purpose to say they are absent."""
        live = [s.upper() for s in _executable_strings(
            ROOT / "bots" / "hydra" / "ls_status.py")]
        for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "CREATE TABLE"):
            assert not [x for x in live if verb in x], verb

    def test_the_connection_is_opened_read_only(self, db):
        src = (ROOT / "bots" / "hydra" / "ls_status.py").read_text()
        assert "mode=ro" in src
        # And prove it: the same URI the module uses must refuse a write.
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM ls_entries")
        con.close()

    def test_a_missing_database_is_empty_not_an_error(self, tmp_path):
        """H is not installed on the VM and may never have written a row. A
        status view must render that as "nothing yet", never as a failure."""
        missing = str(tmp_path / "nope.db")
        assert read_entries(missing, DATE) == []
        assert read_exits(missing, DATE) == []
        assert read_skips(missing, DATE) == []
        assert read_marks(missing, DATE) == {}
        assert ls_status(missing, DATE)["summary"]["open_count"] == 0

    def test_a_corrupt_database_degrades_rather_than_raising(self, tmp_path):
        p = tmp_path / "junk.db"
        p.write_bytes(b"this is not a database")
        assert read_entries(str(p), DATE) == []
        assert ls_status(str(p), DATE)["open"] == []

    def test_it_imports_without_the_broker_stack(self):
        """Pure stdlib, per the playbook — so it is importable by the dashboard
        and testable without a session."""
        src = (ROOT / "bots" / "hydra" / "ls_status.py").read_text()
        for banned in ("ib_client", "broker_client", "ibind", "HydraStrategy"):
            assert banned not in src, banned


# ======================================================================
# The peak versus the exit — the measurement
# ======================================================================

class TestThePeakIsSurfaced:
    def test_the_peak_is_the_best_mark_not_the_last(self, db):
        marks = read_marks(db, DATE)
        # The three marks on a $490 debit are +0.0%, +81.6% and +51.0%. The peak
        # is the middle one; the LAST is where it actually exited.
        assert marks[1]["peak_pct"] == pytest.approx(81.63, abs=0.1)
        assert marks[1]["last"]["pnl_pct_of_debit"] == pytest.approx(51.02, abs=0.1)
        assert marks[1]["ticks"] == 3

    def test_the_gap_between_peak_and_exit_is_computed(self, db):
        """THE number. A large positive gap means the target was reached and
        given back before the exit landed — which is what the source's
        80%-win-rate claim actually rests on."""
        closed = ls_status(db, DATE)["closed"][0]
        # Touched +81.6%, exited at +51.0% — 30.6 points of the debit reached
        # and not captured. On a +50% target that is most of a second target's
        # worth of move, and it is invisible in the realized P&L alone.
        assert closed["peak_minus_exit_pct"] == pytest.approx(30.61, abs=0.1)

    def test_the_gap_is_None_rather_than_zero_when_unknowable(self, tmp_path):
        """No snapshots means no peak. Reporting 0 would read as "it exited at
        its high", which is a claim, not an absence."""
        p = str(tmp_path / "ls.db")
        r = LongStrangleDataRecorder(p)
        e = _entry()
        r.record_entry(e, DATE, 7765.0)
        r.record_exit(e, DATE, "eod", 100.0)
        r.close()
        assert ls_status(p, DATE)["closed"][0]["peak_minus_exit_pct"] is None

    def test_the_trough_is_kept_too(self, db):
        """The worst mark bounds how much heat a winner took — and for a long
        strangle the answer is often "most of the debit" before it turned."""
        assert read_marks(db, DATE)[1]["trough_pct"] == pytest.approx(0.0, abs=0.1)


# ======================================================================
# The day at a glance
# ======================================================================

class TestTheStatusPayload:
    def test_a_closed_entry_lands_in_closed_not_open(self, db):
        st = ls_status(db, DATE)
        assert st["summary"]["closed_count"] == 1
        assert st["summary"]["open_count"] == 0
        assert st["closed"][0]["exit_reason"] == "profit_target_50"

    def test_an_entry_with_no_exit_row_is_open_and_carries_its_live_mark(self, db_open):
        """No sidecar: ls_snapshots is written every tick, so the last row IS
        the live mark."""
        st = ls_status(db_open, DATE)
        assert st["summary"]["open_count"] == 1
        mark = st["open"][0]["last_mark"]
        assert mark["total_value"] == pytest.approx(560.0)
        assert mark["spx"] == 7770.0

    def test_the_expected_move_source_travels_with_the_entry(self, db_open):
        """The choice is config-driven and unsettled, so which definition picked
        the strikes must be visible per entry — otherwise a reader cannot tell
        the two regimes apart."""
        st = ls_status(db_open, DATE)
        assert st["open"][0]["em_source"] == "vix"
        assert st["open"][0]["expected_move"] == 71.0

    def test_declined_entries_are_shown_with_their_reason(self, db):
        st = ls_status(db, DATE)
        assert st["summary"]["skipped_count"] == 1
        assert st["skipped"][0]["skip_reason"].startswith("skew")
        assert st["skipped"][0]["proposed_debit"] == 300.0

    def test_max_possible_loss_equals_the_debit_deployed(self, db):
        """Bounded by construction — the one claim no other variant here can
        make about its own day."""
        s = ls_status(db, DATE)["summary"]
        assert s["debit_deployed"] == pytest.approx(490.0)
        assert s["max_possible_loss"] == s["debit_deployed"]

    def test_another_day_is_not_mixed_in(self, db, tmp_path):
        assert ls_status(db, "2026-09-22")["summary"]["open_count"] == 0

    def test_recent_exits_across_days_are_available_for_history(self, db):
        rows = read_exits(db, date=None, limit=5)
        assert len(rows) == 1 and rows[0]["date"] == DATE


# ======================================================================
# Telegram rendering
# ======================================================================

class TestTheTelegramRender:
    def test_the_dry_run_disclaimer_leads(self, db):
        """Per the playbook. A P&L figure that looks real must not be mistaken
        for one."""
        out = format_long_strangle_telegram(ls_status(db, DATE))
        assert out.splitlines()[0].endswith("(dry-run — places NO real orders)")

    def test_it_says_the_debit_IS_the_max_loss(self, db):
        out = format_long_strangle_telegram(ls_status(db, DATE))
        assert "max possible loss, by construction" in out

    def test_a_give_back_from_the_peak_is_called_out(self, db):
        out = format_long_strangle_telegram(ls_status(db, DATE))
        assert "gave back 31pp from peak" in out

    def test_an_open_position_shows_its_live_mark_and_peak(self, db_open):
        out = format_long_strangle_telegram(ls_status(db_open, DATE))
        assert "OPEN" in out and "MTM" in out and "peak" in out

    def test_declined_entries_appear(self, db):
        out = format_long_strangle_telegram(ls_status(db, DATE))
        assert "Declined:" in out and "skew" in out

    def test_an_empty_day_says_so_rather_than_rendering_blank(self, tmp_path):
        p = str(tmp_path / "ls.db")
        LongStrangleDataRecorder(p).close()
        out = format_long_strangle_telegram(ls_status(p, DATE))
        assert "No activity today." in out

    def test_the_title_is_overridable_for_a_group_renderer(self, db):
        out = format_long_strangle_telegram(ls_status(db, DATE), title="H (paper)")
        assert "*H (paper)*" in out

    def test_it_never_raises_on_a_missing_database(self, tmp_path):
        out = format_long_strangle_telegram(ls_status(str(tmp_path / "x.db"), DATE))
        assert "No activity today." in out


# ======================================================================
# The /longstrangle Telegram command
# ======================================================================

class TestTheTelegramCommandIsWired:
    """Step 8's Telegram half: the variant must be observable from Telegram."""

    def _src(self, rel):
        return (ROOT / rel).read_text()

    def test_the_command_dispatches(self):
        src = self._src("bots/hydra/telegram_commands.py")
        assert 'text.startswith("/longstrangle")' in src
        assert "_handle_long_strangle" in src
        assert "long_strangle_callback" in src

    def test_it_appears_in_help(self):
        src = self._src("bots/hydra/telegram_commands.py")
        assert "/longstrangle" in src.split("def _handle_help")[-1] or \
               "/longstrangle \\u2014" in src

    def test_the_poller_is_handed_the_builder(self):
        """Variant A owns the poller and reads the other variants' files
        cross-variant — the same way /calendars reads D's and E's."""
        assert "build_telegram_longstrangle" in self._src("bots/hydra/main.py")

    def test_the_builder_exists_on_the_strategy(self):
        from bots.hydra.strategy import HydraStrategy
        assert callable(getattr(HydraStrategy, "build_telegram_longstrangle", None))

    def test_it_is_NOT_a_compare_selector(self):
        """H's group has one member and nothing else shares its P&L shape, so a
        head-to-head would have nothing to compare against — and a credit-shaped
        one would state H's numbers backwards."""
        import shared.strategy_taxonomy as tax
        assert tax.GROUPS["long_gamma_0dte"].comparable is False

    def test_a_missing_database_renders_a_sentence_not_an_exception(self, tmp_path):
        """Variant H is dry-run-locked and not installed on the VM, so this is
        the response the command actually gives today."""
        from types import SimpleNamespace
        from bots.hydra.strategy import HydraStrategy
        fake = SimpleNamespace(state_file=str(tmp_path / "hydra_state.json"))
        out = HydraStrategy.build_telegram_longstrangle(fake)
        assert "no long-strangle strategy has recorded anything yet" in out

    def test_it_renders_a_seeded_variant_h(self, tmp_path, db):
        from types import SimpleNamespace
        from bots.hydra.strategy import HydraStrategy
        vh = tmp_path / "variant_h"
        vh.mkdir()
        import shutil
        shutil.copy(db, vh / "long_strangle.db")
        fake = SimpleNamespace(state_file=str(tmp_path / "hydra_state.json"))
        out = HydraStrategy.build_telegram_longstrangle(fake)
        # The day is keyed on "today", which is not the fixture's date — so the
        # correct render is the strategy's header with an empty day, NOT a crash
        # and NOT another day's numbers leaking in.
        assert "(H)" in out and "places NO real orders" in out
