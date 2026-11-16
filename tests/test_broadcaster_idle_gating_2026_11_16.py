"""WS broadcaster: don't poll data sources with nobody connected — 2026-11-16.

The broadcast loops ran their full read-and-compare work regardless of client
count. ``manager.broadcast`` already early-returns with zero connections, so the
SENDS were free — but the READS were not: a state-file ``stat()`` every second
and two SQLite queries every 30s, forever, on a VM that also runs nine trading
processes.

THE PART THAT NEEDED CARE — the gating is SELECTIVE, not blanket:

  * ``_poll_logs`` is NOT gated. It is not only a broadcast source: it advances
    the log tailer's FILE OFFSET and feeds ``live_ohlc.process_log_lines``, which
    is the last-resort chart series when the market-data DB has nothing. Gating
    it would put a hole in that series and then dump the whole idle backlog at
    the next client to connect. ``test_log_poll_is_deliberately_not_gated`` is
    the test that pins that exception, and it is the control that proves the
    gating is targeted rather than applied everywhere it would fit.
  * ``_heartbeat`` is NOT gated — already free, and it is what keeps a live
    connection from being reaped, so it must not depend on connection state.

Trackers (``_last_stop_count``, ``_last_ohlc_bar_count``,
``read_if_changed``'s mtime memo) are left deliberately stale while idle: the
first round after a client connects sees "changed" and broadcasts once, which is
the state that client needs. Its FIRST payload never depends on any of this — it
comes from a fresh ``get_snapshot()`` in the WS handler.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")

from dashboard.backend.config import settings  # noqa: E402
from dashboard.backend.ws.broadcaster import Broadcaster  # noqa: E402

TICK = 0.001
RUN_FOR = 0.08  # ~80 rounds at TICK, so "zero calls" cannot be a timing fluke


class _FakeManager:
    def __init__(self, clients: int = 0):
        self.client_count = clients
        self.sent: list[dict] = []

    async def broadcast(self, message: dict) -> None:
        self.sent.append(message)


class _CountingStateReader:
    def __init__(self):
        self.reads = 0

    def read_if_changed(self):
        self.reads += 1
        return None

    def get_stop_events(self):
        return []


class _CountingTailer:
    def __init__(self):
        self.reads = 0

    def read_new_lines(self):
        self.reads += 1
        return []


@pytest.fixture(autouse=True)
def _fast_intervals(monkeypatch):
    for name in ("state_poll_interval", "metrics_poll_interval", "db_poll_interval",
                 "log_poll_interval", "market_status_interval"):
        monkeypatch.setattr(settings, name, TICK, raising=False)
    yield


async def _run_briefly(coro_fn):
    task = asyncio.create_task(coro_fn())
    await asyncio.sleep(RUN_FOR)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _broadcaster(clients: int) -> tuple[Broadcaster, _FakeManager]:
    mgr = _FakeManager(clients)
    return Broadcaster(mgr), mgr  # type: ignore[arg-type]


# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_poll_does_no_reads_with_nobody_connected():
    b, mgr = _broadcaster(clients=0)
    reader = _CountingStateReader()
    b.state_reader = reader  # type: ignore[assignment]
    await _run_briefly(b._poll_state)
    assert reader.reads == 0
    assert mgr.sent == []


@pytest.mark.asyncio
async def test_state_poll_reads_as_soon_as_someone_is_connected():
    """CONTROL — the same loop, the same interval, one client. If this did not
    read, the test above would pass for the wrong reason (a broken loop rather
    than a gated one)."""
    b, _ = _broadcaster(clients=1)
    reader = _CountingStateReader()
    b.state_reader = reader  # type: ignore[assignment]
    await _run_briefly(b._poll_state)
    assert reader.reads > 0


@pytest.mark.asyncio
async def test_a_client_arriving_mid_flight_resumes_the_loop():
    """The gate is re-evaluated every round, so it is a pause, not a stop."""
    b, mgr = _broadcaster(clients=0)
    reader = _CountingStateReader()
    b.state_reader = reader  # type: ignore[assignment]
    task = asyncio.create_task(b._poll_state())
    await asyncio.sleep(RUN_FOR)
    assert reader.reads == 0
    mgr.client_count = 1
    await asyncio.sleep(RUN_FOR)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert reader.reads > 0


@pytest.mark.asyncio
async def test_log_poll_is_deliberately_not_gated():
    """THE EXCEPTION, and the control that proves the gating is selective. The
    log tailer holds a file offset and feeds the last-resort OHLC builder, so it
    must keep reading with nobody connected."""
    b, _ = _broadcaster(clients=0)
    tailer = _CountingTailer()
    b.log_tailer = tailer  # type: ignore[assignment]
    await _run_briefly(b._poll_logs)
    assert tailer.reads > 0


@pytest.mark.asyncio
async def test_market_status_and_agent_polls_are_gated():
    b, mgr = _broadcaster(clients=0)
    await _run_briefly(b._poll_market_status)
    assert mgr.sent == []


@pytest.mark.asyncio
async def test_skip_while_idle_returns_immediately_when_clients_exist():
    """It must not sleep on the busy path — that would halve every loop's
    effective cadence."""
    b, _ = _broadcaster(clients=2)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await b._skip_while_idle(5.0) is False
    assert loop.time() - t0 < 0.5


@pytest.mark.asyncio
async def test_skip_while_idle_sleeps_and_reports_idle_with_no_clients():
    b, _ = _broadcaster(clients=0)
    assert await b._skip_while_idle(TICK) is True
