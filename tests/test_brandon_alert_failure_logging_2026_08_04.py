"""Brandon-stack alert-send-failure logging fix (round-2 review finding,
2026-08-04).

Round 1 of the alert-reliability fix's adversarial review only checked
bots/hydra/strategy.py and bots/hydra/base_strategy.py for the
logger.debug-swallows-an-alert-failure pattern. Round 2 found the same bug
class untouched in bots/hydra/brandon/strategy.py — the module that runs
LIVE on variant B and dry-run-shadow on C. All Brandon alert call sites
route through ONE chokepoint, `_brandon_send_telegram`, which itself
swallowed send_alert() failures at logger.debug with a bare `%s` (blank for
exceptions like concurrent.futures.TimeoutError()). Three call sites
(GEX-fallback HIGH, overlay-partial-fill CRITICAL — the naked-position
warning when a LIVE hedge doesn't fully fill, orphan-close HIGH) also wrap
their own call to that chokepoint in a try/except with the identical debug
swallow — dead code today (the chokepoint never raises), but fixed for
consistency/defense-in-depth in case that ever changes. A fifth, adjacent
site (BRANDON-OVERLAY settlement failure — not an alert at all, but the
same blank-message + misleading "non-fatal" pattern on a real P&L-mismatch
risk) was fixed alongside it.
"""
from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402


def _strat():
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s.BOT_NAME = "HYDRA_B"
    return s


class TestBrandonSendTelegramChokepoint:
    """The one place every Brandon alert actually calls send_alert() — the
    fix that matters most, since it's what real production failures hit."""

    def test_send_alert_failure_logged_at_error_not_debug(self, caplog):
        s = _strat()
        s.alert_service = MagicMock()
        s.alert_service.send_alert.side_effect = RuntimeError("boom-brandon")

        with caplog.at_level(logging.DEBUG, logger="bots.hydra.brandon.strategy"):
            s._brandon_send_telegram("msg", title="t", priority_name="CRITICAL",
                                     alert_type_name="CRITICAL_INTERVENTION")

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        debugs = [r for r in caplog.records if r.levelno == logging.DEBUG
                  and "BRANDON alert send failed" in r.message]
        assert any("BRANDON alert send failed" in r.message for r in errors), caplog.text
        assert debugs == [], "must not still be logged at DEBUG"
        assert any("RuntimeError" in r.message and "boom-brandon" in r.message
                   for r in errors), caplog.text

    def test_no_alert_service_is_a_silent_noop(self):
        """Not this bug — alert_service absent entirely is a deliberate no-op
        (e.g. a bare test double), not a failure to report."""
        s = _strat()
        s.alert_service = None
        s._brandon_send_telegram("msg")  # must not raise


class TestBrandonOuterCallSitesConsistency:
    """The 3 call sites' own try/except around _brandon_send_telegram is
    dead code today (the chokepoint above never raises), but fixed for
    consistency — verified here by forcing the chokepoint itself to raise,
    bypassing its internal handling."""

    def test_gex_fallback_alert_failure_logged_at_error(self, caplog):
        s = _strat()
        s._brandon_today_date = lambda: "2026-08-04"
        s._brandon_send_telegram = MagicMock(side_effect=RuntimeError("boom-gex"))

        with caplog.at_level(logging.DEBUG, logger="bots.hydra.brandon.strategy"):
            s._brandon_alert_gex_fallback()

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("GEX-FALLBACK" in r.message and "RuntimeError" in r.message
                   and "boom-gex" in r.message for r in errors), caplog.text
        assert not any(r.levelno == logging.DEBUG and "GEX-FALLBACK" in r.message
                       for r in caplog.records)

    def test_overlay_partial_alert_failure_logged_at_error(self, caplog):
        s = _strat()
        s._brandon_send_telegram = MagicMock(side_effect=RuntimeError("boom-overlay"))
        entry = SimpleNamespace(entry_number=3)
        proposal = SimpleNamespace(threatened_side="put")

        with caplog.at_level(logging.DEBUG, logger="bots.hydra.brandon.strategy"):
            s._brandon_alert_overlay_partial(entry, proposal, placed=1, expected=2)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("OVERLAY partial" in r.message and "RuntimeError" in r.message
                   and "boom-overlay" in r.message for r in errors), caplog.text
        assert not any(r.levelno == logging.DEBUG and "OVERLAY partial" in r.message
                       for r in caplog.records)

    def test_orphan_close_alert_failure_logged_at_error(self, caplog):
        s = _strat()
        s._brandon_mark_close_failed = MagicMock()
        s._BRANDON_FAILED_CLOSE_COOLDOWN_S = 90.0
        s._brandon_send_telegram = MagicMock(side_effect=RuntimeError("boom-orphan-close"))
        entry = SimpleNamespace(entry_number=5)

        with caplog.at_level(logging.DEBUG, logger="bots.hydra.brandon.strategy"):
            s._brandon_alert_orphan_close(entry, "put", "TP")

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("orphan-close" in r.message and "RuntimeError" in r.message
                   and "boom-orphan-close" in r.message for r in errors), caplog.text
        assert not any(r.levelno == logging.DEBUG and "orphan-close" in r.message
                       for r in caplog.records)


class TestBrandonOverlaySettlementFailureLogging:
    """Adjacent, non-alert site with the identical blank-message pattern,
    fixed alongside the alert sites — a failed settlement booking is the
    same class of bug as the 2026-07-21 dashboard/cumulative P&L mismatch,
    not the "(non-fatal)" the old message claimed."""

    def test_settlement_failure_logged_with_real_diagnostics(self, caplog):
        s = _strat()
        s._resolve_spx_close = lambda: 7500.0
        s._brandon_settle_hedges = MagicMock(side_effect=TimeoutError())  # blank str()
        s.log_daily_summary_super_called = False

        class _Base:
            def log_daily_summary(self_inner):
                s.log_daily_summary_super_called = True

        # Bind a bare super().log_daily_summary() stand-in via monkeypatching
        # the MRO's next call is awkward on a __new__-constructed instance —
        # instead directly verify the except branch logs correctly by calling
        # log_daily_summary via the class, catching the follow-on
        # AttributeError from the real super() call (there is no real base
        # class state here) — the assertion under test only concerns the log
        # line emitted in the except block, which runs before that.
        with caplog.at_level(logging.DEBUG, logger="bots.hydra.brandon.strategy"):
            try:
                BrandonHydraStrategy.log_daily_summary(s)
            except AttributeError:
                pass

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("BRANDON-OVERLAY settlement failed" in r.message
                   and "TimeoutError" in r.message for r in errors), caplog.text
