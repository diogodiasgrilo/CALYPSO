"""2026-08-18 shutdown-hang investigation: strategy processes (hydra/hydra_
variant_{b,c,d,e}) were repeatedly observed taking 44-82+ seconds — sometimes
past the 100s systemd TimeoutStopSec, needing a forced SIGKILL — to actually
exit after logging a complete "Shutdown complete" sequence. journalctl showed
lingering child threads named grpc_global_tim / event_engine / lifeguard at
kill time.

Root-cause investigation (workflow-driven, code audit + live VM evidence +
grpcio-internals research) found those three threads are grpc-core
PROCESS-GLOBAL singletons, lazily spawned by the first channel any client
library constructs (Secret Manager, used by every variant at startup;
Pub/Sub, constructed even on alert-DISABLED variants D/E per the L-C2
comment in AlertService._initialize). They can only be torn down by grpc's
own internal shutdown sequence at real interpreter exit, NOT by closing an
individual client/channel — confirmed via grpcio 1.80.0's actual compiled
source strings. Client-hygiene fixes here are real, worthwhile cleanup; they
are NOT a proven elimination of the underlying grpc-core teardown latency.

Round-1 adversarial review (3 independent reviewers + adversarial
verification of every finding) caught 6 real issues in the first version of
this fix, ALL addressed in this revision:
  1. future.cancel() on a Pub/Sub publisher Future is a hard, unconditional
     no-op (verified against the installed library source) — REMOVED rather
     than left in place implying a mitigation that doesn't exist.
  2. AlertService.close()'s transport.close() call had no timeout, breaking
     this codebase's own established convention (Sheets/Secret Manager 10s,
     ib_oauth 30s) — FIXED to use the same bounded thread+join pattern as
     shared/logger_service.py's _sheets_call_with_timeout.
  3. An unsynchronized read-then-use of self._publisher in send_alert vs.
     close()'s mutation of it — NARROWED (not fully lock-protected; not
     reachable today, see the code comments for why a full lock wasn't
     added) by snapshotting into a local before use.
  4. HIGH: shared/secret_manager.py's `with SecretManagerServiceClient() as
     client:` blocks could mask an ALREADY-SUCCESSFUL fetch/update as a
     failure if channel teardown itself raised (reproduced empirically) —
     FIXED by not using the client as a context manager: the RPC and the
     close are now independent try blocks, so a close-time failure can
     never override an already-decided RPC outcome.
  5. Test gap matching #4 — only the happy path (close succeeds) was
     tested. This file's TestSecretManagerClientClosed now explicitly
     covers "RPC succeeds, close raises" for both functions.
  6. bots/hydra/main.py's close_alert_service_safely/log_shutdown_
     diagnostics were only wired into the main loop's finally: block, not
     the ~5 earlier startup-failure return paths — FIXED, all early-return
     paths now call both (passing None for `strategy` where it doesn't
     exist yet in scope).
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import alert_service as alert_service_module  # noqa: E402
from shared.alert_service import AlertPriority, AlertService, AlertType  # noqa: E402
import shared.secret_manager as secret_manager  # noqa: E402
import bots.hydra.main as main_module  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_dead_letter_file(tmp_path, monkeypatch):
    monkeypatch.setattr(alert_service_module, "FAILED_ALERTS_PATH", str(tmp_path / "failed_alerts.jsonl"))
    yield


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(alert_service_module.time, "sleep", lambda _s: None)


def _live_svc(monkeypatch, on_gcp: bool = True) -> AlertService:
    """Matches tests/test_alert_publish_reliability.py's _live_svc helper."""
    monkeypatch.delenv("ALERT_DRY_RUN", raising=False)
    monkeypatch.setattr(alert_service_module, "is_running_on_gcp", lambda: on_gcp)
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_B")
    svc._initialized = True
    svc._publisher = MagicMock()
    svc._topic_path = "projects/test/topics/calypso-alerts"
    return svc


def _mock_future(result=None, exc: Exception = None) -> MagicMock:
    future = MagicMock()
    if exc is not None:
        future.result.side_effect = exc
    else:
        future.result.return_value = result or "mid-123"
    return future


# ─────────────────────────── AlertService.close() ───────────────────────────
class TestAlertServiceClose:
    def test_transport_close_actually_invoked(self, monkeypatch):
        svc = _live_svc(monkeypatch)
        publisher_mock = svc._publisher
        svc.close()
        publisher_mock.transport.close.assert_called_once()

    def test_publisher_reset_to_none_before_close_call_returns(self, monkeypatch):
        # Snapshot-then-clear ordering (round-1 review, finding #3): the
        # instance attribute must already be None by the time close()
        # returns, regardless of how long the background close thread takes.
        svc = _live_svc(monkeypatch)
        svc.close()
        assert svc._publisher is None
        assert svc._initialized is False

    def test_noop_when_publisher_was_never_constructed(self, monkeypatch):
        monkeypatch.delenv("ALERT_DRY_RUN", raising=False)
        monkeypatch.setattr(alert_service_module, "is_running_on_gcp", lambda: False)
        svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_A")
        assert svc._publisher is None
        svc.close()  # must not raise
        assert svc._publisher is None

    def test_swallows_transport_close_exception(self, monkeypatch):
        svc = _live_svc(monkeypatch)
        svc._publisher.transport.close.side_effect = RuntimeError("channel already gone")
        svc.close()  # must not raise
        assert svc._publisher is None  # still cleared despite the exception

    def test_idempotent_second_call_is_a_noop(self, monkeypatch):
        svc = _live_svc(monkeypatch)
        svc.close()
        svc.close()  # must not raise on an already-closed service

    def test_does_not_block_past_its_own_timeout(self, monkeypatch):
        # Round-1 review finding #2: the original close() called
        # transport.close() with NO timeout, unconditionally, on the
        # shutdown-critical path. This is the direct regression test.
        #
        # Two things had to be fixed to make this test actually prove what
        # it claims (both caught by re-running this exact test against a
        # deliberately-reverted close() as its own negative control):
        #   (a) the blocking call must be genuinely indefinite (Event.wait()
        #       with NO argument), not a fixed sleep -- a fixed 2s sleep
        #       against a 5s bound just returns in ~2s regardless of
        #       whether the bound works at all, silently passing either way.
        #   (b) Event.wait()/threading are unaffected by the _no_real_sleep
        #       autouse fixture (which only patches the shared `time`
        #       module's .sleep -- a time.sleep()-based simulation here
        #       would have been silently neutered by that same fixture).
        # CLOSE_TIMEOUT_S is monkeypatched to keep this test fast (no need
        # to actually wait out the real 5s bound to prove it applies).
        monkeypatch.setattr(AlertService, "CLOSE_TIMEOUT_S", 0.2)
        svc = _live_svc(monkeypatch)

        def _slow_close():
            threading.Event().wait()  # blocks forever (never set) -- genuine hang

        svc._publisher.transport.close.side_effect = _slow_close
        started = time.monotonic()
        svc.close()  # must return at ~CLOSE_TIMEOUT_S, not hang forever
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"close() blocked for {elapsed:.2f}s instead of honoring its ~0.2s bound"
        assert svc._publisher is None

    def test_concurrent_close_does_not_crash_an_in_flight_send(self, monkeypatch):
        # Round-1 review finding #3: send_alert snapshots self._publisher
        # into a local before using it, so a close() that runs after the
        # snapshot but before the publish call completes doesn't turn a
        # would-have-succeeded send into an AttributeError.
        svc = _live_svc(monkeypatch)
        publisher_mock = svc._publisher
        future = _mock_future(result="mid-ok")

        # Simulate close() having already run (self._publisher flipped to
        # None) by the time send_alert reaches the publish call -- send_alert
        # already captured `publisher` as a local before this point in the
        # real code, so it must still succeed using its own snapshot.
        def _publish_after_concurrent_close(*args, **kwargs):
            svc._publisher = None  # simulate another thread's close()
            return future

        publisher_mock.publish.side_effect = _publish_after_concurrent_close
        result = svc.send_alert(
            AlertType.POSITION_OPENED, "Position Opened", "entry #1",
            priority=AlertPriority.MEDIUM,
        )
        assert result is True  # used its own snapshot, unaffected by the concurrent close


# ─────────── send_alert: the ineffective future.cancel() call is gone ───────
class TestNoIneffectiveFutureCancel:
    def test_cancel_is_never_called_on_a_failed_future(self, monkeypatch):
        # google.cloud.pubsub_v1.publisher.futures.Future.cancel() is a
        # hard, unconditional no-op (verified against the installed
        # library source during round-1 review) — the call was removed
        # rather than left in as dead, misleading code. This guards
        # against silently re-adding it under the same mistaken belief.
        svc = _live_svc(monkeypatch)
        future = _mock_future(exc=TimeoutError())
        svc._publisher.publish.side_effect = [future]
        result = svc.send_alert(
            AlertType.POSITION_OPENED, "Position Opened", "entry #1",
            priority=AlertPriority.MEDIUM,
        )
        assert result is False
        future.cancel.assert_not_called()

    def test_retry_still_succeeds_after_first_attempt_fails(self, monkeypatch):
        # The retry-then-succeed path (tests/test_alert_publish_reliability.py
        # covers this in depth) must still work correctly now that the
        # cancel() call is gone from the except branch.
        svc = _live_svc(monkeypatch)
        f1 = _mock_future(exc=TimeoutError())
        f2 = _mock_future(result="mid-ok")
        svc._publisher.publish.side_effect = [f1, f2]
        result = svc.send_alert(
            AlertType.CIRCUIT_BREAKER, "Circuit Breaker", "halted",
            priority=AlertPriority.CRITICAL,
        )
        assert result is True
        f1.cancel.assert_not_called()
        f2.cancel.assert_not_called()


# ───────── secret_manager: close is bounded + can't mask a success ──────────
class TestSecretManagerClientClosed:
    def _install_fake_client_module(self, monkeypatch, client):
        fake_module = SimpleNamespace(SecretManagerServiceClient=MagicMock(return_value=client))
        monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", fake_module)
        monkeypatch.setitem(sys.modules, "google.cloud", SimpleNamespace(secretmanager=fake_module))

    def test_get_secret_closes_transport_on_success(self, monkeypatch):
        monkeypatch.setattr(secret_manager, "is_running_on_gcp", lambda: True)
        monkeypatch.setattr(secret_manager, "get_project_id", lambda: "test-project")
        client = MagicMock()
        client.access_secret_version.return_value = SimpleNamespace(
            payload=SimpleNamespace(data=b"secret-value")
        )
        self._install_fake_client_module(monkeypatch, client)

        result = secret_manager.get_secret("some-secret")

        assert result == "secret-value"
        client.transport.close.assert_called_once()

    def test_get_secret_survives_close_raising_after_successful_fetch(self, monkeypatch):
        # THE regression test for the HIGH-severity round-1 finding: the
        # original `with SecretManagerServiceClient() as client:` version
        # let an exception from __exit__/transport.close() — raised AFTER
        # the RPC had already succeeded — propagate past the pending
        # `return`, get caught by the outer except, and turn a real secret
        # value into a reported None. Reproduced empirically against the
        # pre-fix code; this asserts the fixed behavior.
        monkeypatch.setattr(secret_manager, "is_running_on_gcp", lambda: True)
        monkeypatch.setattr(secret_manager, "get_project_id", lambda: "test-project")
        client = MagicMock()
        client.access_secret_version.return_value = SimpleNamespace(
            payload=SimpleNamespace(data=b"secret-value")
        )
        client.transport.close.side_effect = RuntimeError("channel close blew up")
        self._install_fake_client_module(monkeypatch, client)

        result = secret_manager.get_secret("some-secret")

        assert result == "secret-value", (
            "a close-time failure masked an already-successful fetch as None"
        )

    def test_get_secret_close_does_not_block_past_its_timeout(self, monkeypatch):
        # See TestAlertServiceClose.test_does_not_block_past_its_own_timeout's
        # comment for why this needs an INDEFINITE block (not a fixed sleep)
        # and a monkeypatched timeout constant to be a real, fast-running
        # proof rather than an accidentally-always-green test.
        monkeypatch.setattr(secret_manager, "SECRET_MANAGER_CLOSE_TIMEOUT_S", 0.2)
        monkeypatch.setattr(secret_manager, "is_running_on_gcp", lambda: True)
        monkeypatch.setattr(secret_manager, "get_project_id", lambda: "test-project")
        client = MagicMock()
        client.access_secret_version.return_value = SimpleNamespace(
            payload=SimpleNamespace(data=b"secret-value")
        )
        client.transport.close.side_effect = lambda: threading.Event().wait()  # never set
        self._install_fake_client_module(monkeypatch, client)

        started = time.monotonic()
        result = secret_manager.get_secret("some-secret")
        elapsed = time.monotonic() - started

        assert result == "secret-value"
        assert elapsed < 1.0, f"get_secret() blocked for {elapsed:.2f}s instead of honoring its ~0.2s bound"

    def test_get_secret_survives_close_thread_failing_to_spawn(self, monkeypatch):
        # Round-2 review finding: _close_secret_manager_client's own
        # threading.Thread(...)/.start() calls were originally unguarded --
        # only the RPC-close call INSIDE the thread was try/excepted. Under
        # OS-level thread exhaustion (a real condition -- and made more
        # plausible by this same investigation's own finding that a close()
        # can genuinely hang and leave its daemon thread running), a failure
        # to even spawn the close thread reproduced the identical
        # success-masked-as-failure bug through a different trigger.
        # Reproduced empirically during round-2 review; this is that repro.
        monkeypatch.setattr(secret_manager, "is_running_on_gcp", lambda: True)
        monkeypatch.setattr(secret_manager, "get_project_id", lambda: "test-project")
        client = MagicMock()
        client.access_secret_version.return_value = SimpleNamespace(
            payload=SimpleNamespace(data=b"secret-value")
        )
        self._install_fake_client_module(monkeypatch, client)
        monkeypatch.setattr(
            secret_manager.threading, "Thread",
            MagicMock(side_effect=RuntimeError("can't start new thread")),
        )

        result = secret_manager.get_secret("some-secret")

        assert result == "secret-value", (
            "a failure to even SPAWN the close thread masked an already-successful fetch"
        )

    def test_get_secret_real_rpc_failure_still_reported_correctly(self, monkeypatch):
        # Negative-control companion: a GENUINE RPC failure (not a
        # close-time-only failure) must still be reported as None, exactly
        # as before this fix -- proves the fix didn't accidentally start
        # swallowing real failures too.
        monkeypatch.setattr(secret_manager, "is_running_on_gcp", lambda: True)
        monkeypatch.setattr(secret_manager, "get_project_id", lambda: "test-project")
        client = MagicMock()
        client.access_secret_version.side_effect = RuntimeError("permission denied")
        self._install_fake_client_module(monkeypatch, client)

        result = secret_manager.get_secret("some-secret")

        assert result is None
        client.transport.close.assert_called_once()  # still cleaned up despite the RPC failure

    def test_update_secret_closes_transport_on_success(self, monkeypatch):
        monkeypatch.setattr(secret_manager, "is_running_on_gcp", lambda: True)
        monkeypatch.setattr(secret_manager, "get_project_id", lambda: "test-project")
        client = MagicMock()
        client.access_secret_version.side_effect = Exception("no prior version")
        client.add_secret_version.return_value = SimpleNamespace(name="v2")
        self._install_fake_client_module(monkeypatch, client)

        result = secret_manager.update_secret("some-secret", "new-value")

        assert result is True
        client.transport.close.assert_called_once()

    def test_update_secret_survives_close_raising_after_successful_update(self, monkeypatch):
        # Same regression class as get_secret's equivalent test above.
        monkeypatch.setattr(secret_manager, "is_running_on_gcp", lambda: True)
        monkeypatch.setattr(secret_manager, "get_project_id", lambda: "test-project")
        client = MagicMock()
        client.access_secret_version.side_effect = Exception("no prior version")
        client.add_secret_version.return_value = SimpleNamespace(name="v2")
        client.transport.close.side_effect = RuntimeError("channel close blew up")
        self._install_fake_client_module(monkeypatch, client)

        result = secret_manager.update_secret("some-secret", "new-value")

        assert result is True, (
            "a close-time failure masked an already-successful update as False"
        )


# ───────────────── main.py: close_alert_service_safely ──────────────────────
class TestCloseAlertServiceSafely:
    def test_calls_close_on_strategy_alert_service(self):
        alert_service = MagicMock()
        strategy = SimpleNamespace(alert_service=alert_service)
        trade_logger = MagicMock()
        main_module.close_alert_service_safely(strategy, trade_logger)
        alert_service.close.assert_called_once()

    def test_noop_when_strategy_is_none(self):
        trade_logger = MagicMock()
        main_module.close_alert_service_safely(None, trade_logger)  # must not raise
        trade_logger.log_event.assert_not_called()

    def test_swallows_close_exception_and_logs_it(self):
        alert_service = MagicMock()
        alert_service.close.side_effect = RuntimeError("boom")
        strategy = SimpleNamespace(alert_service=alert_service)
        trade_logger = MagicMock()
        main_module.close_alert_service_safely(strategy, trade_logger)  # must not raise
        assert trade_logger.log_event.called
        logged = " ".join(str(c) for c in trade_logger.log_event.call_args_list)
        assert "boom" in logged or "non-fatal" in logged


# ───────────────── main.py: log_shutdown_diagnostics ─────────────────────────
class TestLogShutdownDiagnostics:
    def test_logs_a_shutdown_diag_line(self):
        trade_logger = MagicMock()
        main_module.log_shutdown_diagnostics(trade_logger)
        logged = " ".join(str(c) for c in trade_logger.log_event.call_args_list)
        assert "SHUTDOWN-DIAG" in logged

    def test_includes_python_thread_names(self):
        trade_logger = MagicMock()
        main_module.log_shutdown_diagnostics(trade_logger)
        logged = " ".join(str(c) for c in trade_logger.log_event.call_args_list)
        assert "MainThread" in logged

    def test_survives_proc_self_status_unavailable(self, monkeypatch):
        # Simulate non-Linux / /proc unavailable -- must degrade gracefully,
        # not crash the shutdown sequence.
        real_open = main_module.__builtins__["open"] if isinstance(main_module.__builtins__, dict) else main_module.__builtins__.open

        def _raising_open(path, *a, **kw):
            if path == "/proc/self/status":
                raise OSError("no /proc on this platform")
            return real_open(path, *a, **kw)

        monkeypatch.setattr(main_module, "open", _raising_open, raising=False)
        trade_logger = MagicMock()
        main_module.log_shutdown_diagnostics(trade_logger)  # must not raise
        logged = " ".join(str(c) for c in trade_logger.log_event.call_args_list)
        assert "SHUTDOWN-DIAG" in logged
        assert "unknown" in logged

    def test_never_raises_even_if_threading_enumerate_explodes(self, monkeypatch):
        monkeypatch.setattr(
            main_module.threading, "enumerate",
            MagicMock(side_effect=RuntimeError("thread registry corrupted")),
        )
        trade_logger = MagicMock()
        main_module.log_shutdown_diagnostics(trade_logger)  # must not raise
        assert trade_logger.log_event.called


# ───── main.py: wiring reaches every early-return path, not just the ────────
# ───── main loop's finally: block (round-1 review finding #6) ───────────────
class TestShutdownHelpersWiredIntoEveryEarlyReturnPath:
    def test_both_helpers_called_at_every_early_return_site(self):
        # Structural test (not a full run_bot() exercise -- no test in this
        # repo drives run_bot() directly, it's too large/stateful to unit
        # test as a whole). Asserts the property round-1 review's finding
        # #6 was actually about: every trade_logger.shutdown() call site in
        # main.py -- the finally: block PLUS the ~6 early-return paths
        # (broker-build failure, 3x broker-connect failure branches,
        # strategy-init failure, L-M10 instrument-probe failure) -- is
        # preceded by both close_alert_service_safely(...) and
        # log_shutdown_diagnostics(...). A future edit that adds a new
        # early-return path without this wiring will fail this test only
        # if it also reintroduces the specific count mismatch; paired with
        # a manual code read for anything genuinely new.
        import inspect
        source = inspect.getsource(main_module)
        run_bot_source, show_status_source = source.split("def show_status(", 1)

        # run_bot(): every trade_logger.shutdown() site must be preceded by
        # both helpers. show_status() is a separate, non-systemd-managed CLI
        # diagnostic path (python -m bots.hydra.main --status) -- its own
        # trade_logger.shutdown() calls are deliberately NOT wired to these
        # helpers (out of scope: no systemd unit forcibly SIGKILLs it the
        # way a long-running trading process can be), asserted separately
        # below so a future change to either function is caught precisely.
        run_bot_shutdown_sites = run_bot_source.count("trade_logger.shutdown()")
        run_bot_close_calls = run_bot_source.count("close_alert_service_safely(")
        run_bot_diag_calls = run_bot_source.count("log_shutdown_diagnostics(")
        assert run_bot_shutdown_sites == 7, (
            f"expected 7 trade_logger.shutdown() sites in run_bot(), found "
            f"{run_bot_shutdown_sites} -- if this changed, the assertions below "
            f"need re-auditing against the new code, not just the new count"
        )
        assert run_bot_close_calls == run_bot_shutdown_sites + 1, (  # +1 for the def line
            f"expected close_alert_service_safely called at all {run_bot_shutdown_sites} "
            f"run_bot() shutdown sites (+1 def line), found {run_bot_close_calls} occurrences"
        )
        assert run_bot_diag_calls == run_bot_shutdown_sites + 1, (
            f"expected log_shutdown_diagnostics called at all {run_bot_shutdown_sites} "
            f"run_bot() shutdown sites (+1 def line), found {run_bot_diag_calls} occurrences"
        )

        # show_status(): confirm it's genuinely out of scope (2 shutdown
        # sites, neither wired) rather than silently missing coverage.
        assert show_status_source.count("trade_logger.shutdown()") == 2
        assert "close_alert_service_safely(" not in show_status_source
        assert "log_shutdown_diagnostics(" not in show_status_source
