"""2026-09-06: fix the shutdown hang that SIGKILLed the bots at systemd's
100s TimeoutStopSec.

ROOT CAUSE, corrected. The blocker was NOT the Pub/Sub commit thread that the
2026-09-05 traceback made it look like:
  * Thread-CommitBatchPublisher is daemon=True in the installed
    google-cloud-pubsub, and daemon threads cannot block threading._shutdown().
  * On 2026-09-03 all SEVEN units were SIGKILLed in the same second, and six of
    them (A/C/D/E/F/G) never publish and had no such thread at all.
The real blocker is CPython interpreter finalization (Py_FinalizeEx) with
grpc-core's process-global native threads resident — which
AlertService.close() has correctly documented since 2026-08-18 as
un-eliminable per-channel. The fix is therefore to skip finalization entirely,
AFTER graceful shutdown has already completed.

These tests pin the invariants that make os._exit() safe, plus the two
secondary hangs found alongside it.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


# ───────────────────── the invariant os._exit() depends on ─────────────────────
class TestNoAtexitHandlers:
    """os._exit() skips atexit handlers. That is safe ONLY while this codebase
    registers none — which is true today and must stay true. If someone adds
    one, this test fails loudly instead of the handler silently never running.

    Scanned via AST rather than grep so a commented-out or string-literal
    mention doesn't produce a false positive, and an aliased import
    (`from atexit import register as r`) doesn't produce a false negative.
    """

    ROOTS = ("shared", "bots", "services")

    def _offenders(self):
        bad = []
        for root in self.ROOTS:
            for path in (REPO / root).rglob("*.py"):
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except (SyntaxError, UnicodeDecodeError):
                    continue
                aliases = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module == "atexit":
                        for a in node.names:
                            if a.name == "register":
                                aliases.add(a.asname or a.name)
                    # threading._register_atexit is the same hazard class
                    if isinstance(node, ast.Attribute) and node.attr == "_register_atexit":
                        bad.append(f"{path.relative_to(REPO)}: threading._register_atexit")
                    if isinstance(node, ast.Call):
                        f = node.func
                        if (isinstance(f, ast.Attribute) and f.attr == "register"
                                and isinstance(f.value, ast.Name) and f.value.id == "atexit"):
                            bad.append(f"{path.relative_to(REPO)}: atexit.register(...)")
                        elif isinstance(f, ast.Name) and f.id in aliases:
                            bad.append(f"{path.relative_to(REPO)}: aliased atexit.register(...)")
        return bad

    def test_no_project_module_registers_an_atexit_handler(self):
        offenders = self._offenders()
        assert not offenders, (
            "os._exit() in bots/hydra/main.py._hard_exit SKIPS atexit handlers. "
            "Something now registers one, so it will silently never run:\n  "
            + "\n  ".join(offenders)
            + "\nEither remove it, or move that cleanup into run_bot()'s graceful "
              "shutdown (which completes BEFORE _hard_exit is reached)."
        )

    def test_the_scanner_would_actually_catch_one(self, tmp_path):
        """Negative control for the scanner itself — a test that can never fail
        is worse than no test."""
        probe = tmp_path / "probe.py"
        probe.write_text("import atexit\ndef f():\n    pass\natexit.register(f)\n")
        tree = ast.parse(probe.read_text())
        found = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "register" and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "atexit"
            for n in ast.walk(tree)
        )
        assert found


# ───────────────────── the hard-exit wiring ─────────────────────
class TestHardExitWiring:
    """main.py's __main__ block is not importable-and-callable, so pin it at
    the source level — this codebase's established pattern for wiring that is
    invisible at runtime until a real deploy (cf. TestMainPyUsesTheHook)."""

    def _src(self):
        return (REPO / "bots" / "hydra" / "main.py").read_text(encoding="utf-8")

    def test_hard_exit_is_defined_and_calls_os_exit(self):
        src = self._src()
        assert "def _hard_exit(" in src
        assert "os._exit(code)" in src

    def test_main_guard_routes_through_hard_exit(self):
        src = self._src()
        assert "finally:" in src and "_hard_exit(_exit_code)" in src
        # The bare `main()` tail must be gone, else the fix is inert.
        assert not re.search(r'if __name__ == "__main__":\s*\n\s*main\(\)\s*\Z', src)

    def test_systemexit_code_is_preserved(self):
        """main() has sys.exit(1) paths. Swallowing those into a clean 0 would
        hide real failures from systemd."""
        src = self._src()
        assert "except SystemExit as e:" in src
        assert "_exit_code = e.code if isinstance(e.code, int)" in src

    def test_flushes_before_exiting(self):
        """os._exit skips buffer flushing, so the fix must flush explicitly or
        it will truncate the final log lines it exists to preserve."""
        src = self._src()
        # Slice from the def to the os._exit call, so a long explanatory
        # docstring can't push the actual code out of the window.
        i = src.index("def _hard_exit(")
        j = src.index("os._exit(code)", i)
        body = src[i:j]
        assert "logging.shutdown()" in body, "must flush the logging system before _exit"
        assert "flush()" in body, "must flush stdout/stderr before _exit"


# ───────────────────── secondary hangs found alongside ─────────────────────
class TestSecondaryHangFixes:
    def test_gex_pool_shutdown_cancels_futures(self):
        """shutdown(wait=False) alone leaves workers registered in
        concurrent.futures._threads_queues, which _python_exit joins with NO
        timeout. A hydration worker stuck in a Polygon call would hang exit on
        its own, independently of the grpc stall."""
        src = (REPO / "bots" / "hydra" / "brandon" / "gex_provider.py").read_text()
        assert "pool.shutdown(wait=False, cancel_futures=True)" in src
        assert "pool.shutdown(wait=False)\n" not in src

    def test_logger_queue_join_is_bounded(self):
        """Queue.join() has no timeout; if the consumer died or wedged, the
        shutdown path blocks forever BEFORE reaching _hard_exit."""
        src = (REPO / "shared" / "logger_service.py").read_text()
        assert "self.log_queue.join()" not in src
        assert "unfinished_tasks" in src

    def test_alert_close_stops_publisher_before_closing_transport(self):
        """Closing the transport under an in-flight commit is what produced the
        truncated Thread-CommitBatchPublisher traceback on 2026-09-05."""
        src = (REPO / "shared" / "alert_service.py").read_text()
        assert "publisher.stop()" in src
        assert src.index("publisher.stop()") < src.index("publisher.transport.close()")


class TestImportSafety:
    def test_importing_main_does_not_exit(self):
        """_hard_exit lives under the __main__ guard. If that ever changed,
        importing the module (as the test suite does) would kill the process —
        a spectacular failure worth an explicit guard."""
        import bots.hydra.main as m
        assert hasattr(m, "_hard_exit")
        assert callable(m._hard_exit)
