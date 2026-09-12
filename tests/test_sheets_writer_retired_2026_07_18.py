"""2026-07-18: Google Sheets WRITE path retirement.

The agents (CLIO/HERMES/HOMER) and the dashboard now read
data/variant_c/backtesting.db; all variants run google_sheets.enabled=false.
GoogleSheetsLogger force-disables the writer regardless of config (kept as
dormant code, symmetric with the retained read-side shared/sheets_reader.py).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.logger_service import GoogleSheetsLogger


def test_writer_force_disabled_even_when_config_enabled():
    # Kill-switch: enabled=true is IGNORED, and _initialize() (gspread/network)
    # is never reached — construction stays I/O-free.
    lg = GoogleSheetsLogger({"google_sheets": {"enabled": True, "spreadsheet_name": "X"}})
    assert lg.enabled is False
    assert lg.spreadsheet is None
    assert lg.worksheets == {}


def test_writer_disabled_by_default():
    lg = GoogleSheetsLogger({})
    assert lg.enabled is False


def test_log_trade_noops_when_retired():
    # With the writer retired, log_trade must not raise and must report no-write.
    lg = GoogleSheetsLogger({"google_sheets": {"enabled": True}})
    # log_trade guards on `not self.enabled` first → returns falsy without touching
    # any worksheet. Pass a minimal object; it must never be dereferenced.
    result = lg.log_trade(object())
    assert not result
