"""bots/hydra/main.py's SIGTERM diagnostic log (2026-08-03).

Found in the full-day audit: hydra/hydra_variant_{b,d,e} needed a forced
SIGKILL during a same-day restart, and — unlike calypso-broker's confirmed
ensure_connected-retry hang (fixed in shared/ib_retry.py) — the exact reason
was NOT fully root-caused. This diagnostic logs a snapshot of what the
strategy was doing at the moment SIGTERM arrives, purely so a recurrence's
logs pinpoint the cause instead of requiring another round of journalctl
archaeology. Must never itself block or alter shutdown behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bots.hydra.main as main_module  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_module_globals():
    """shutdown_requested / _active_strategy are module-level globals —
    reset around every test so this file can't leak state into others."""
    orig_shutdown = main_module.shutdown_requested
    orig_strategy = main_module._active_strategy
    main_module.shutdown_requested = False
    main_module._active_strategy = None
    yield
    main_module.shutdown_requested = orig_shutdown
    main_module._active_strategy = orig_strategy


class TestSignalHandler:
    def test_sets_shutdown_requested(self):
        main_module.signal_handler(15, None)
        assert main_module.shutdown_requested is True

    def test_logs_snapshot_when_strategy_present(self, caplog):
        main_module._active_strategy = SimpleNamespace(
            state="MONITORING",
            _entry_in_progress=True,
            _current_entry=SimpleNamespace(entry_number=3),
        )
        with caplog.at_level("INFO"):
            main_module.signal_handler(15, None)
        diag_lines = [r.getMessage() for r in caplog.records if "SIGTERM-DIAG" in r.message]
        assert diag_lines, "expected a SIGTERM-DIAG log line"
        assert "MONITORING" in diag_lines[0]
        assert "3" in diag_lines[0]  # the in-progress entry_number

    def test_no_active_strategy_does_not_raise(self):
        main_module._active_strategy = None
        # Must not raise — this is the common case (SIGTERM before
        # build_strategy() has run, e.g. during IBKR connect()).
        main_module.signal_handler(15, None)
        assert main_module.shutdown_requested is True

    def test_strategy_missing_expected_attrs_does_not_raise(self):
        # A bare object() has none of state/_entry_in_progress/_current_entry
        # — the getattr(..., None) defaults must handle this cleanly, not
        # raise AttributeError (which would mean SIGTERM handling itself
        # crashes — worse than no diagnostic at all).
        main_module._active_strategy = object()
        main_module.signal_handler(15, None)
        assert main_module.shutdown_requested is True

    def test_diagnostic_snapshot_never_blocks_shutdown_flag(self):
        # Even if reading strategy attributes raises (a property that
        # throws), shutdown_requested must still end up True — the
        # try/except around the diagnostic must not swallow the flag-set.
        class _Explodes:
            @property
            def state(self):
                raise RuntimeError("boom")

        main_module._active_strategy = _Explodes()
        main_module.signal_handler(15, None)
        assert main_module.shutdown_requested is True

    def test_repeated_signals_stay_idempotent(self):
        main_module._active_strategy = SimpleNamespace(
            state="WAITING", _entry_in_progress=False, _current_entry=None,
        )
        main_module.signal_handler(15, None)
        main_module.signal_handler(15, None)
        assert main_module.shutdown_requested is True
