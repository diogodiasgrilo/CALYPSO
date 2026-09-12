"""2026-09-03: fixed a misleading log line found during a routine daily audit.

log_daily_summary() (bots/hydra/base_strategy.py) unconditionally logged "Daily
summary logged to Google Sheets" whenever self.trade_logger was truthy, without
checking whether the underlying call actually wrote anything. Since
google_sheets.enabled=false on every variant today (post Sheets->DB migration),
this fired every single day on every variant, always false. Root cause traced one
level deeper: TradeLoggerService.log_daily_summary (shared/logger_service.py) had
NO return statement in either branch (implicit None always), discarding the real
success signal GoogleSheetsLogger.log_daily_summary already reports -- so there
was nothing for the caller to correctly gate on until that was fixed too.
"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from shared.logger_service import TradeLoggerService


class TestTradeLoggerServiceReturnValue:
    def _svc(self, enabled, google_returns=True):
        svc = TradeLoggerService.__new__(TradeLoggerService)
        svc.google_logger = SimpleNamespace(
            enabled=enabled,
            log_daily_summary=MagicMock(return_value=google_returns),
        )
        return svc

    def test_returns_true_when_enabled_and_write_succeeds(self):
        svc = self._svc(enabled=True, google_returns=True)
        assert svc.log_daily_summary({"date": "2026-09-03"}) is True

    def test_returns_false_when_disabled(self):
        # The real-world case on every variant today.
        svc = self._svc(enabled=False)
        assert svc.log_daily_summary({"date": "2026-09-03"}) is False
        svc.google_logger.log_daily_summary.assert_not_called()

    def test_propagates_a_failed_write_too(self):
        svc = self._svc(enabled=True, google_returns=False)
        assert svc.log_daily_summary({"date": "2026-09-03"}) is False

    def test_negative_control_old_no_return_would_always_be_none(self):
        """Pins the actual regression: before the fix, both branches fell off the
        end of the function with no return, so the caller always saw None
        (falsy) -- indistinguishable from "genuinely disabled" even when Sheets
        WAS enabled and the write succeeded. Source-level check since the whole
        point is a MISSING return statement, which behavior alone can't
        discriminate from "returns False on purpose" without this."""
        import inspect
        source = inspect.getsource(TradeLoggerService.log_daily_summary)
        assert "return self.google_logger.log_daily_summary(summary)" in source
        assert "return False" in source


class TestLogDailySummaryWiresTheGate:
    """log_daily_summary (base_strategy.py) is heavy (Sheets/alerts/state-save
    side effects) to invoke directly for a purely cosmetic logging fix -- pin the
    wiring at the source level instead, matching this file's existing pattern for
    hook-wiring regressions (see TestLogDailySummaryWiresTheHook,
    test_metrics_drift_fix_2026_09_02.py)."""

    def test_info_log_is_gated_on_the_real_return_value(self):
        import inspect
        import bots.hydra.base_strategy as base_mod
        source = inspect.getsource(base_mod.MEICStrategy.log_daily_summary)
        assert "if self.trade_logger.log_daily_summary(sheets_summary):" in source
        assert '"Daily summary logged to Google Sheets' in source
        # The old unconditional call+log (no gating) must be gone.
        assert "self.trade_logger.log_daily_summary(sheets_summary)\n            logger.info(" not in source
