"""Diagnostics fix for two more sites in the same sweep as
test_alert_publish_reliability.py: services/argus/notify.py's own alert-send
failure, and shared/data_recorder.py's _safe_write — both logged a bare
f"...: {e}" with no exception-type prefix, which is blank for exceptions
whose str() is empty.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.data_recorder import DataRecorder, _describe_exception as dr_describe_exception  # noqa: E402


def test_data_recorder_describe_exception_never_blank():
    e = TimeoutError()
    assert str(e) == ""
    assert dr_describe_exception(e) != ""
    assert "TimeoutError" in dr_describe_exception(e)


def test_safe_write_logs_type_annotated_message_on_blank_exception(caplog, tmp_path):
    recorder = DataRecorder(str(tmp_path / "test.db"))

    def _raise():
        raise TimeoutError()  # empty str() — the exact failure mode found live

    with caplog.at_level(logging.WARNING, logger="shared.data_recorder"):
        result = recorder._safe_write("record_entry", _raise)

    assert result is False
    assert any(
        "TimeoutError" in r.message and "record_entry" in r.message
        for r in caplog.records
    ), caplog.text


def test_argus_notify_logs_type_annotated_message_on_send_failure(monkeypatch):
    """services/argus/notify.py's main(): a send_alert failure must log the
    exception type, not a blank string, before falling back to stdout.

    Patches only AlertService.send_alert to raise (the real
    shared.alert_service.describe_exception is left in place — that's the
    function under test here, via notify.py's own except-block usage)."""
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "services", "argus")))
    import importlib
    import notify as argus_notify  # noqa
    importlib.reload(argus_notify)

    monkeypatch.setattr(sys, "stdin", io.StringIO("ARGUS FAIL: disk at 95%"))
    monkeypatch.setattr(
        argus_notify, "load_config",
        lambda: {"alerts": {"enabled": True}},
    )
    monkeypatch.setattr(argus_notify, "_recently_alerted", lambda fp: False)
    monkeypatch.setattr(argus_notify, "_record_alert", lambda fp: None)

    from shared.alert_service import AlertService

    with patch.object(AlertService, "send_alert", side_effect=TimeoutError()):
        with caplog_capture() as records:
            argus_notify.main()

    error_records = [r for r in records if r.levelno == logging.ERROR]
    assert any("TimeoutError" in r.getMessage() for r in error_records), [
        r.getMessage() for r in error_records
    ]


def caplog_capture():
    """Small helper — pytest's caplog fixture isn't available as a bare
    function, so capture via a temporary handler for this one cross-module
    (sys.modules patched) test."""
    class _Ctx:
        def __enter__(self):
            self.records = []
            self.handler = _ListHandler(self.records)
            self.logger = logging.getLogger("argus.notify")
            self.prev_level = self.logger.level
            self.logger.addHandler(self.handler)
            self.logger.setLevel(logging.DEBUG)
            return self.records

        def __exit__(self, *exc):
            self.logger.removeHandler(self.handler)
            self.logger.setLevel(self.prev_level)

    return _Ctx()


class _ListHandler(logging.Handler):
    def __init__(self, records):
        super().__init__()
        self.records = records

    def emit(self, record):
        self.records.append(record)
