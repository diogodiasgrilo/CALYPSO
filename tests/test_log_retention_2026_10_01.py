"""Log retention has to be long enough to answer "did this happen before?"

Seven days was shorter than the questions. On 2026-09-30 an investigation into
unwound partial-fill legs could see only five sessions, and the single piece of
evidence that mattered survived purely because the incident happened that week.
A drift investigation the same day hit the same wall.

These tests pin the depth and the floor. The floor matters more than it looks:
`backupCount=0` means "keep no backups", so a malformed env value that fell
through to 0 would silently delete every rotated file — turning a retention
knob into a log-destruction knob.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reload(monkeypatch, value=None):
    if value is None:
        monkeypatch.delenv("CALYPSO_LOG_RETENTION_DAYS", raising=False)
    else:
        monkeypatch.setenv("CALYPSO_LOG_RETENTION_DAYS", value)
    import shared.logger_service as ls
    return importlib.reload(ls)


class TestTheDefault:
    def test_it_is_90_days(self, monkeypatch):
        assert _reload(monkeypatch).LOG_RETENTION_DAYS == 90

    def test_it_is_not_7_any_more(self, monkeypatch):
        """The specific regression: 7 days is less than the lookback a real
        investigation needs."""
        assert _reload(monkeypatch).LOG_RETENTION_DAYS > 7


class TestTheOverride:
    def test_a_host_can_raise_it(self, monkeypatch):
        assert _reload(monkeypatch, "180").LOG_RETENTION_DAYS == 180

    def test_a_host_can_lower_it_but_not_below_the_floor(self, monkeypatch):
        assert _reload(monkeypatch, "14").LOG_RETENTION_DAYS == 14
        assert _reload(monkeypatch, "3").LOG_RETENTION_DAYS == 7

    @pytest.mark.parametrize("bad", ["0", "-5", "", "ninety", "9.5", "  "])
    def test_a_malformed_value_never_destroys_logs(self, monkeypatch, bad):
        """backupCount=0 means KEEP NOTHING. A bad env value must never reach
        the handler as 0, or a typo becomes a log wipe."""
        v = _reload(monkeypatch, bad).LOG_RETENTION_DAYS
        assert v >= 7, f"{bad!r} resolved to {v}, which would shrink retention"


class TestTheHandlerUsesIt:
    def test_the_handler_is_wired_to_the_constant(self, monkeypatch):
        """Pins that the handler reads the constant rather than carrying its
        own literal — otherwise the env override is decorative."""
        import inspect
        ls = _reload(monkeypatch)
        src = inspect.getsource(ls.TradeLoggerService._setup_logging) \
            if hasattr(ls.TradeLoggerService, "_setup_logging") else inspect.getsource(ls)
        assert "backupCount=LOG_RETENTION_DAYS" in src.replace(" ", "").replace("\n", "") \
            or "backupCount=LOG_RETENTION_DAYS" in src, (
            "the rotating handler no longer takes backupCount from "
            "LOG_RETENTION_DAYS, so the constant and the override do nothing")

    def test_a_literal_7_is_gone_from_the_handler_call(self, monkeypatch):
        import io
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "shared/logger_service.py"),
                      encoding="utf-8").read()
        assert "backupCount=7" not in src, "the hardcoded 7-day retention is back"
