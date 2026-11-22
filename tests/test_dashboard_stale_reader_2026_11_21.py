"""The iOS widget froze at the first poll after every dashboard restart.

Found 2026-11-21 while sweeping the fleet: `/api/health` reported
`state_date: 2026-11-17` on 2026-11-21, while every state file on disk said
2026-11-21. Chasing that turned up a wider defect behind it.

THE DEFECT, in one line:

    state = reader.get_cached() or reader.read_latest()

`get_cached()` returns `_last_data` "without touching disk". `read_latest()` is
the only thing that fills it at these call sites. So the FIRST request reads the
file, and `get_cached()` is non-None from then on — which makes the
`or read_latest()` arm unreachable **forever**. The reader is a module-level
singleton keyed on the resolved path, so "forever" means until the dashboard
process restarts.

`read_if_changed()` — the one method that re-reads on an mtime change — is
called in exactly ONE file, `ws/broadcaster.py`, on the broadcaster's OWN
readers. Nothing ever refreshes the readers held by `routers/widget.py` and
`routers/hydra.py`.

WHAT THAT COST. `/api/widget` is the only data source the Scriptable widget has
(`dashboard/scriptable/HYDRA_Widget.js` fetches `/api/widget` and
`/api/widget/fleet`), so the phone widget showed whatever was true at its first
poll after each dashboard restart. The dashboard had been up since 2026-11-16,
so it had been serving 5-day-old numbers — a plausible-looking P&L, no error, no
"stale" marker. The same pattern sat on `/api/hydra/state` and
`/api/hydra/summary` (landed 2026-09-17); those are legacy and the React app
does not poll them, but an operator or script hitting them got frozen data too.
`/api/hydra/entries` is reached by the frontend only with an explicit
`date_str`, which takes the DB path — so it was never affected.

THE FIX. One named method, `current()`, on both readers:

    fresh = self.read_if_changed()        # re-reads only when mtime moved
    return fresh if fresh is not None else self._last_data

and every "give me the file as it is now" call site uses it. Freshness costs a
`stat()` (0.003 ms) when nothing changed, and a 1,737-byte read + parse
(0.046 ms) when it did — measured on the live seat, so there is no case for
trading correctness away for it.

WHY NOT point the shared readers at `current()`. `main.py` used to hand the
broadcaster's OWN reader to `/api/health` and to `LiveStateProvider`. Calling
`read_if_changed()` on that instance from a request handler would CONSUME the
mtime change the broadcaster's `_poll_state` is waiting for, and the WebSocket
round that would have pushed it goes silent instead. So each of those two
consumers now holds its own reader over the same path, and the broadcaster's
change detection is left strictly alone. `test_the_broadcaster_signal_is_not_stolen`
is the pin on that, and it is the test to read first if any of this is revisited.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from dashboard.backend.services.state_reader import StateFileReader  # noqa: E402
from dashboard.backend.services.metrics_reader import MetricsFileReader  # noqa: E402


def _write(p: Path, payload: dict, *, bump: float = 0.0) -> None:
    """Write JSON and force a distinct mtime.

    Two writes inside one filesystem timestamp tick are indistinguishable to
    mtime-based change detection, which would make these tests pass or fail on
    timer granularity rather than on behaviour.
    """
    p.write_text(json.dumps(payload))
    if bump:
        os.utime(p, (time.time() + bump, time.time() + bump))


@pytest.fixture()
def state_file(tmp_path) -> Path:
    p = tmp_path / "hydra_state.json"
    _write(p, {"date": "2026-11-17", "entries": [], "total_realized_pnl": 100.0})
    return p


@pytest.fixture()
def metrics_file(tmp_path) -> Path:
    p = tmp_path / "hydra_metrics.json"
    _write(p, {"cumulative_pnl": 100.0})
    return p


# --------------------------------------------------------------------------
# 1. The defect itself, at reader level — with the old pattern as the control
# --------------------------------------------------------------------------

class TestTheReaderServesTheFileAsItIsNow:
    def test_current_sees_a_later_write(self, state_file):
        r = StateFileReader(state_file)
        assert r.current()["date"] == "2026-11-17"
        _write(state_file, {"date": "2026-11-21", "entries": []}, bump=2)
        assert r.current()["date"] == "2026-11-21"

    def test_CONTROL_the_old_pattern_freezes(self, state_file):
        """The exact expression that shipped. If this ever stops freezing, the
        test above proves nothing and this file is vacuous."""
        r = StateFileReader(state_file)
        old = lambda: r.get_cached() or r.read_latest()  # noqa: E731
        assert old()["date"] == "2026-11-17"
        _write(state_file, {"date": "2026-11-21", "entries": []}, bump=2)
        assert old()["date"] == "2026-11-17", "control: old pattern should freeze"

    def test_current_is_stable_when_the_file_does_not_move(self, state_file):
        r = StateFileReader(state_file)
        assert r.current()["date"] == r.current()["date"] == "2026-11-17"

    def test_current_does_not_reparse_an_unchanged_file(self, state_file):
        """Freshness must cost a stat, not a parse, or a 1s poll pays for it."""
        r = StateFileReader(state_file)
        r.current()
        calls = {"n": 0}
        inner = r._read_file

        def counting():
            calls["n"] += 1
            return inner()

        r._read_file = counting
        r.current()
        r.current()
        assert calls["n"] == 0, "unchanged file was re-parsed"

    def test_current_falls_back_to_cache_when_the_file_goes_unreadable(self, state_file):
        """A momentary bad read must not blank a live trading view."""
        r = StateFileReader(state_file)
        assert r.current()["date"] == "2026-11-17"
        state_file.write_text("{ truncated")
        os.utime(state_file, (time.time() + 2, time.time() + 2))
        assert r.current()["date"] == "2026-11-17"

    def test_current_is_None_when_there_is_nothing_to_serve(self, tmp_path):
        r = StateFileReader(tmp_path / "missing.json")
        assert r.current() is None

    def test_metrics_reader_has_the_same_fix(self, metrics_file):
        r = MetricsFileReader(metrics_file)
        assert r.current()["cumulative_pnl"] == 100.0
        _write(metrics_file, {"cumulative_pnl": 999.0}, bump=2)
        assert r.current()["cumulative_pnl"] == 999.0

    def test_CONTROL_metrics_old_pattern_freezes(self, metrics_file):
        r = MetricsFileReader(metrics_file)
        old = lambda: r.get_cached() or r.read_latest()  # noqa: E731
        assert old()["cumulative_pnl"] == 100.0
        _write(metrics_file, {"cumulative_pnl": 999.0}, bump=2)
        assert old()["cumulative_pnl"] == 100.0, "control: should freeze"


# --------------------------------------------------------------------------
# 2. The broadcaster must keep its change signal
# --------------------------------------------------------------------------

class TestTheBroadcasterIsNotDisturbed:
    def test_the_broadcaster_signal_is_not_stolen(self, state_file):
        """THE INVARIANT. A request-path reader must not consume the mtime
        change that `_poll_state` uses to decide whether to broadcast.

        Two readers over one path: the request path calls `current()`, then the
        broadcaster's `read_if_changed()` must STILL see the change.
        """
        request_side = StateFileReader(state_file)
        broadcaster_side = StateFileReader(state_file)

        request_side.current()
        broadcaster_side.read_if_changed()      # prime both

        _write(state_file, {"date": "2026-11-21", "entries": []}, bump=2)

        assert request_side.current()["date"] == "2026-11-21"
        got = broadcaster_side.read_if_changed()
        assert got is not None, "broadcaster lost the change — WS round goes silent"
        assert got["date"] == "2026-11-21"

    def test_CONTROL_one_shared_reader_DOES_steal_it(self, state_file):
        """Why they are separate instances. Sharing one reader is the thing the
        fix had to avoid, so prove sharing actually breaks it."""
        shared = StateFileReader(state_file)
        shared.current()
        shared.read_if_changed()

        _write(state_file, {"date": "2026-11-21", "entries": []}, bump=2)

        shared.current()                        # request handler runs first
        assert shared.read_if_changed() is None, (
            "control: a shared reader should swallow the broadcaster's signal"
        )

    def test_read_if_changed_still_returns_None_when_unchanged(self, state_file):
        """`_poll_state` treats None as 'nothing to broadcast'. Keep it."""
        r = StateFileReader(state_file)
        assert r.read_if_changed() is not None
        assert r.read_if_changed() is None


# --------------------------------------------------------------------------
# 3. The call sites — no `get_cached() or ... read_latest()` left anywhere
# --------------------------------------------------------------------------

class TestNoCallSiteStillUsesTheFrozenPattern:
    FILES = [
        "dashboard/backend/main.py",
        "dashboard/backend/routers/widget.py",
        "dashboard/backend/routers/hydra.py",
        "dashboard/backend/services/live_state.py",
    ]

    @staticmethod
    def _code_lines(rel: str) -> list[tuple[int, str]]:
        """Executable lines only — comments and docstrings stripped.

        Scanning raw text matched the explanatory comments the fix itself added
        and reported them as offenders. A source scan that cannot tell code from
        prose about code is not a gate (the same trap as 2026-11-20).
        """
        import io
        import tokenize

        src = open(os.path.join(ROOT, rel)).read()
        drop: set[int] = set()
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        for i, t in enumerate(toks):
            if t.type == tokenize.COMMENT:
                drop |= set(range(t.start[0], t.end[0] + 1))
            elif t.type == tokenize.STRING:
                # a STRING alone on its logical line is a docstring/bare literal
                prev = toks[i - 1].type if i else tokenize.NEWLINE
                if prev in (tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                            tokenize.NL, tokenize.ENCODING):
                    drop |= set(range(t.start[0], t.end[0] + 1))
        return [(n, ln) for n, ln in enumerate(src.splitlines(), 1)
                if n not in drop]

    def test_the_frozen_pattern_is_gone(self):
        offenders = []
        for rel in self.FILES:
            for n, line in self._code_lines(rel):
                if "get_cached()" in line and "read_latest()" in line:
                    offenders.append(f"{rel}:{n}")
        assert not offenders, f"frozen read pattern still present: {offenders}"

    def test_CONTROL_the_scan_can_still_see_real_code(self, tmp_path, monkeypatch):
        """The scan above must not be blind. Plant the real defect as CODE and
        confirm it is caught; plant it as a COMMENT and confirm it is not."""
        import tokenize  # noqa: F401
        bad = tmp_path / "bad.py"
        bad.write_text(
            '"""get_cached() or read_latest() in a docstring."""\n'
            "# get_cached() or read_latest() in a comment\n"
            "x = r.get_cached() or r.read_latest()\n"
        )
        monkeypatch.setattr(TestNoCallSiteStillUsesTheFrozenPattern,
                            "FILES", [str(bad)])
        monkeypatch.setattr(os.path, "join", lambda *a: a[-1])
        hits = [n for n, ln in self._code_lines(str(bad))
                if "get_cached()" in ln and "read_latest()" in ln]
        assert hits == [3], f"scan saw {hits}, expected only the code line"

    def test_each_file_actually_reads_current(self):
        """Paired with the test above so deleting the call site, rather than
        fixing it, cannot pass."""
        for rel in self.FILES:
            src = open(os.path.join(ROOT, rel)).read()
            assert ".current()" in src, f"{rel} no longer refreshes at all"

    def test_the_frozen_pattern_is_gone_REPO_WIDE(self):
        """Not just the four files that had it.

        `routers/metrics.py` and `routers/variants.py` also hold module-level
        readers; they happened to call `read_latest()` directly (always fresh),
        so they were never affected. This scan is what stops a SEVENTH site
        appearing anywhere in the dashboard.
        """
        offenders = []
        for base, dirs, files in os.walk(os.path.join(ROOT, "dashboard")):
            dirs[:] = [d for d in dirs
                       if d not in ("__pycache__", "node_modules", "dist")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(base, f), ROOT)
                for n, line in self._code_lines(rel):
                    if "get_cached()" in line and "read_latest()" in line:
                        offenders.append(f"{rel}:{n}")
        assert not offenders, f"frozen read pattern reappeared: {offenders}"

    def test_health_reader_is_not_the_broadcasters(self):
        """Runtime identity, not a grep. `_health_state_reader =
        broadcaster.state_reader` would pass every other test here and quietly
        reintroduce the stolen-signal race."""
        import dashboard.backend.main as m
        assert m._health_state_reader is not m.broadcaster.state_reader
        assert m._health_state_reader.file_path == m.broadcaster.state_reader.file_path, (
            "it must still watch the SAME file — a different path is a worse bug"
        )

    def test_live_state_is_not_given_the_broadcasters_reader(self):
        import io
        import tokenize
        rel = "dashboard/backend/main.py"
        src = open(os.path.join(ROOT, rel)).read()
        drop = set()
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        for i, t in enumerate(toks):
            if t.type == tokenize.COMMENT:
                drop |= set(range(t.start[0], t.end[0] + 1))
            elif t.type == tokenize.STRING:
                prev = toks[i - 1].type if i else tokenize.NEWLINE
                if prev in (tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                            tokenize.NL, tokenize.ENCODING):
                    drop |= set(range(t.start[0], t.end[0] + 1))
        code = "\n".join(ln for n, ln in enumerate(src.splitlines(), 1)
                          if n not in drop)
        assert "LiveStateProvider(broadcaster.state_reader" not in code, (
            "LiveStateProvider must hold its own reader (stolen-signal race)"
        )

    def test_the_broadcaster_still_owns_read_if_changed(self):
        """It is the only legitimate caller; a second one is a race."""
        hits = []
        for base, _dirs, files in os.walk(os.path.join(ROOT, "dashboard")):
            if "__pycache__" in base or "node_modules" in base:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(base, f)
                src = open(p).read()
                if "read_if_changed()" in src and "def read_if_changed" not in src:
                    hits.append(os.path.relpath(p, ROOT))
        assert hits == ["dashboard/backend/ws/broadcaster.py"], hits


# --------------------------------------------------------------------------
# 4. End to end through the real route functions
# --------------------------------------------------------------------------

class TestTheWidgetEndpointServesCurrentData:
    @pytest.mark.asyncio
    async def test_widget_reflects_a_new_state_write(self, tmp_path, monkeypatch):
        import dashboard.backend.routers.widget as w

        sp = tmp_path / "hydra_state.json"
        mp = tmp_path / "hydra_metrics.json"
        _write(sp, {"date": "2026-11-17", "entries": [], "state": "MONITORING",
                    "total_realized_pnl": 100.0, "total_commission": 0.0})
        _write(mp, {"cumulative_pnl": 100.0})

        monkeypatch.setattr(w, "live_state_file", lambda: sp)
        monkeypatch.setattr(w, "live_metrics_file", lambda: mp)
        w._state_readers.clear()
        w._metrics_readers.clear()

        first = await w.get_widget_data()
        assert first["net_pnl"] == 100.0

        _write(sp, {"date": "2026-11-21", "entries": [], "state": "MONITORING",
                    "total_realized_pnl": -250.0, "total_commission": 0.0}, bump=2)

        second = await w.get_widget_data()
        assert second["net_pnl"] == -250.0, (
            "the widget served a stale P&L — this is the defect"
        )

    @pytest.mark.asyncio
    async def test_hydra_state_route_reflects_a_new_write(self, tmp_path, monkeypatch):
        import dashboard.backend.routers.hydra as h

        sp = tmp_path / "hydra_state.json"
        _write(sp, {"date": "2026-11-17", "entries": [{"entry_number": 1}]})
        monkeypatch.setattr(h, "live_state_file", lambda: sp)
        h._state_readers.clear()

        r1 = await h.get_state(strategy_id="")
        assert r1["date"] == "2026-11-17"

        _write(sp, {"date": "2026-11-21", "entries": [{"entry_number": 9}]}, bump=2)

        r2 = await h.get_state(strategy_id="")
        assert r2["date"] == "2026-11-21"


# --------------------------------------------------------------------------
# 5. /api/health must tell the truth about freshness
# --------------------------------------------------------------------------

class TestHealthReportsRealFreshness:
    @pytest.mark.asyncio
    async def test_health_tracks_the_file_on_disk(self, tmp_path, monkeypatch):
        import dashboard.backend.main as m

        sp = tmp_path / "hydra_state.json"
        _write(sp, {"date": "2026-11-17", "entries": []})
        monkeypatch.setattr(m, "_health_state_reader",
                            StateFileReader(sp), raising=False)

        h1 = await m.health()
        assert h1["state_date"] == "2026-11-17"
        assert h1["state_loaded"] is True

        _write(sp, {"date": "2026-11-21", "entries": []}, bump=2)
        h2 = await m.health()
        assert h2["state_date"] == "2026-11-21", (
            "health froze — the symptom that started this investigation"
        )

    @pytest.mark.asyncio
    async def test_health_exposes_the_file_age(self, tmp_path, monkeypatch):
        """The gate in LIVE_READINESS_CHECKLIST reads this endpoint. A date
        alone cannot distinguish 'tracking' from 'frozen on a plausible date',
        which is exactly how this went unnoticed."""
        import dashboard.backend.main as m

        sp = tmp_path / "hydra_state.json"
        _write(sp, {"date": "2026-11-17", "entries": []})
        os.utime(sp, (time.time() - 3600, time.time() - 3600))
        monkeypatch.setattr(m, "_health_state_reader",
                            StateFileReader(sp), raising=False)

        h = await m.health()
        assert h["state_file_age_seconds"] is not None
        assert 3500 < h["state_file_age_seconds"] < 3700

    @pytest.mark.asyncio
    async def test_health_survives_a_missing_state_file(self, tmp_path, monkeypatch):
        import dashboard.backend.main as m
        monkeypatch.setattr(m, "_health_state_reader",
                            StateFileReader(tmp_path / "nope.json"), raising=False)
        h = await m.health()
        assert h["status"] == "ok"
        assert h["state_loaded"] is False
        assert h["state_date"] is None
        assert h["state_file_age_seconds"] is None
