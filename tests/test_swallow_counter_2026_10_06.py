"""Swallowed exceptions must be countable, because every bug this week hid in one.

33 `except ...: logger.debug(...)` handlers sit in the trading path. The swallow
is usually CORRECT — analytics must not break trading — but it left no trace at
INFO, the level the bots actually run at. What was hiding behind them:

    day_type classification failed      -> the column was NULL for 10 weeks
    realized_volatility failed          -> same
    DataRecorder stop write failed      -> silently lost an entire stop row
    Greeks fetch failed                 -> ~1 entry in 4 lost its delta

Raising all 33 to WARNING is the wrong fix: some fire every ~11 seconds and
would bury the signal in the noise that let them hide. Counting is the
alternative — a handler that never fires prints nothing; one that fires 47
times becomes a single line in the nightly report.
"""
import sqlite3
import threading

import pytest

from shared import swallow_counter as sc
from shared.data_recorder import DataRecorder


@pytest.fixture(autouse=True)
def _clean():
    sc.reset()
    yield
    sc.reset()


class TestTheCounter:
    def test_it_counts_per_site(self):
        sc.note("a", ValueError("x"))
        sc.note("a", ValueError("y"))
        sc.note("b", KeyError("k"))
        snap = sc.snapshot()
        assert snap["a"]["n"] == 2
        assert snap["b"]["n"] == 1
        assert sc.total() == 3

    def test_it_keeps_the_type_and_last_message(self):
        sc.note("a", ValueError("first"))
        sc.note("a", TypeError("second"))
        assert sc.snapshot()["a"]["type"] == "TypeError"
        assert sc.snapshot()["a"]["last"] == "second"

    def test_a_long_message_is_truncated(self):
        sc.note("a", ValueError("z" * 5000))
        assert len(sc.snapshot()["a"]["last"]) <= 200

    def test_snapshot_is_a_copy(self):
        """A caller mutating the snapshot must not corrupt the live tallies."""
        sc.note("a", ValueError("x"))
        snap = sc.snapshot()
        snap["a"]["n"] = 999
        assert sc.snapshot()["a"]["n"] == 1

    def test_reset_clears(self):
        sc.note("a", ValueError("x"))
        sc.reset()
        assert sc.snapshot() == {} and sc.total() == 0

    def test_it_NEVER_raises(self):
        """A counter that can throw would break the handler it is counting —
        turning an observability aid into an outage."""
        class Nasty(Exception):
            def __str__(self):
                raise RuntimeError("even str() explodes")
        sc.note("a", Nasty())          # must not propagate
        sc.note(None, ValueError("x"))  # unhashable-ish / odd site
        assert True

    def test_a_broken_logger_does_not_propagate(self):
        class BadLogger:
            def debug(self, *a, **k):
                raise RuntimeError("logging is down")
        sc.note("a", ValueError("x"), BadLogger(), "msg")
        assert sc.snapshot()["a"]["n"] == 1

    def test_it_is_thread_safe(self):
        """The greeks fetch and the Telegram poller both run off the main thread."""
        def work():
            for _ in range(200):
                sc.note("shared", ValueError("x"))
        ts = [threading.Thread(target=work) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert sc.snapshot()["shared"]["n"] == 1600


class TestPersistence:
    def test_a_snapshot_round_trips(self, tmp_path):
        rec = DataRecorder(str(tmp_path / "t.db"))
        assert rec.ensure_schema()
        sc.note("recorder.stop_write", ValueError("boom"))
        sc.note("recorder.stop_write", ValueError("boom2"))
        sc.note("enrich.day_type", KeyError("k"))
        assert rec.record_swallowed("2026-10-06", sc.snapshot())
        c = sqlite3.connect(str(tmp_path / "t.db"))
        rows = {r[0]: r for r in c.execute(
            "SELECT site, count, exc_type, last_message FROM swallowed_exceptions")}
        c.close()
        assert rows["recorder.stop_write"][1] == 2
        assert rows["recorder.stop_write"][2] == "ValueError"
        assert rows["enrich.day_type"][1] == 1

    def test_a_second_flush_same_day_REPLACES_rather_than_duplicating(self, tmp_path):
        """Settlement can run twice (a post-close restart re-runs the sweep).
        The second write must carry the larger count, not add a duplicate row."""
        rec = DataRecorder(str(tmp_path / "t.db"))
        rec.ensure_schema()
        sc.note("x", ValueError("a"))
        rec.record_swallowed("2026-10-06", sc.snapshot())
        sc.note("x", ValueError("b"))
        rec.record_swallowed("2026-10-06", sc.snapshot())
        c = sqlite3.connect(str(tmp_path / "t.db"))
        rows = c.execute("SELECT count FROM swallowed_exceptions WHERE site='x'").fetchall()
        c.close()
        assert rows == [(2,)], rows

    def test_an_empty_snapshot_writes_nothing(self, tmp_path):
        rec = DataRecorder(str(tmp_path / "t.db"))
        rec.ensure_schema()
        rec.record_swallowed("2026-10-06", {})
        c = sqlite3.connect(str(tmp_path / "t.db"))
        assert c.execute("SELECT COUNT(*) FROM swallowed_exceptions").fetchone()[0] == 0
        c.close()


def test_the_wired_handlers_use_stable_site_names():
    """Site names are the key the report groups on, so they must be literals —
    never the exception text, which varies per call and would fragment the
    tally into noise."""
    import pathlib, re
    src = pathlib.Path("bots/hydra/strategy.py").read_text()
    sites = re.findall(r'_swallow\.note\(\s*("?[^,"]*"?)\s*,', src)
    assert sites, "no handlers are wired to the counter"
    for s in sites:
        assert s.startswith('"') and s.endswith('"'), (
            f"site {s!r} is not a literal — a computed key fragments the tally")
