"""A routine, self-healing broker pause was logged as an ERROR.

On 2026-10-23 at 09:30 variants a, f and h each logged:

    ERROR | Broker session unavailable (morning re-auth gate) — pausing
            trading and waiting for the broker to self-heal (NOT exiting).

An error sweep of that day therefore reported three faults that were nothing
of the kind. In broker mode this path is explicitly NOT a failure: the strategy
pauses, the broker self-heals in its own maintenance loop, the bot does not
exit, and the paired alert is LOW ("paused, not exiting"). The message says so
in its own text while being filed at the level that means "something broke".

ERROR is retained for the LEGACY direct-IBClient paths, where the same outage
really does end in a restart.

The point is not tidiness — an error level that cries wolf is how a real error
gets skimmed past. This is the same class as the derive-don't-assert log
findings: the record has to mean what it says.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

SRC = open(os.path.join(ROOT, "bots/hydra/main.py")).read()


def _call_at(marker: str) -> str:
    i = SRC.index(marker)
    return SRC[max(0, i - 400):i + 400]


class TestTheSelfHealingPauseIsNotAnError:
    def test_the_morning_gate_logs_at_WARNING(self):
        w = _call_at("Broker session unavailable (morning re-auth ")
        assert 'level="WARNING"' in w, "morning re-auth pause still at ERROR"

    def test_the_intraday_recheck_logs_at_WARNING(self):
        w = _call_at("Broker session unavailable (intraday re-check)")
        assert 'level="WARNING"' in w, "intraday pause still at ERROR"

    def test_neither_broker_mode_pause_uses_log_error(self):
        for marker in ("Broker session unavailable (morning re-auth ",
                       "Broker session unavailable (intraday re-check)"):
            i = SRC.index(marker)
            # the logging call immediately preceding the message
            before = SRC[max(0, i - 220):i]
            assert "log_error(" not in before, f"{marker!r} still log_error"

    def test_it_is_NOT_silently_downgraded_to_INFO(self):
        """A pause that stops trading must still be visible. log_event defaults
        to INFO, so dropping the level kwarg would hide it entirely — which an
        intermediate version of this change did."""
        for marker in ("Broker session unavailable (morning re-auth ",
                       "Broker session unavailable (intraday re-check)"):
            w = _call_at(marker)
            assert 'level="WARNING"' in w


class TestRealFailuresKeepTheirLevel:
    def test_ERROR_is_still_used_elsewhere_in_the_loop(self):
        """The control: this must not have become a blanket downgrade."""
        assert SRC.count("trade_logger.log_error(") >= 15, (
            "log_error has been stripped from the main loop wholesale")

    def test_the_LEGACY_path_still_errors(self):
        """Outside broker mode the same outage ends in a restart, so ERROR is
        correct there. Both branches mention the same gate, so assert the
        legacy one still carries an ERROR call after the broker-mode block."""
        i = SRC.index("Broker session unavailable (morning re-auth ")
        after = SRC[i:i + 1800]
        assert "Legacy direct-IBClient" in after
        assert "log_error(" in after, "legacy re-auth path no longer errors"

    def test_the_paired_alert_stays_LOW_and_says_paused(self):
        # Wider window than _call_at: the explanatory comment added with the
        # level change sits between the alert call and the log call.
        i = SRC.index("Broker session unavailable (morning re-auth ")
        w = SRC[max(0, i - 1200):i]
        assert "paused=True" in w, (
            "the alert must still state it paused rather than exited")
