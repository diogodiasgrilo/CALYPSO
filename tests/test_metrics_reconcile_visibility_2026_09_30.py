"""A guard whose failure is invisible is not a guard.

`_reconcile_cumulative_metrics_from_db` is documented as the root-cause fix for
metrics-vs-DB drift: "any drift is corrected on the next close and cannot
accumulate." On 2026-09-30 that claim was false in production — A, B and C all
drifted from their databases (B by $2,091.40, understating the live seat's
lifetime on the dashboard and the iOS widget) while emitting **zero**
METRICS-RECONCILE lines across three weeks of journald.

Two things kept it invisible, and both are fixed here:

  * a failure was logged at `logger.debug`, which the bot never emits at INFO;
  * a CLEAN run logged nothing at all, so silence meant either "ran, nothing to
    do" or "never ran" — opposite facts, indistinguishable.

These tests pin the visibility, not the arithmetic. The arithmetic already had
tests; what it lacked was any way to find out that it was not running.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _src():
    return inspect.getsource(HydraStrategy._reconcile_cumulative_metrics_from_db)


class TestFailureIsLoud:
    def test_the_exception_handler_is_not_debug(self):
        """Targets the CALL, not the word.

        First version asserted `"logger.debug" not in tail` and failed on the
        comment explaining that it USED to be logger.debug — the same trap as
        the `_last_expected_move` docstring earlier the same day. A test that
        cannot tell prose from code fails on its own documentation.
        """
        tail = _src()
        tail = tail[tail.index("except Exception"):]
        calls = [l for l in tail.splitlines()
                 if "logger.debug(" in l and not l.strip().startswith("#")]
        assert not calls, (
            "the self-heal's failure is logged at DEBUG again: "
            f"{calls}. The bot runs at INFO, so it would be silent — which is "
            "exactly how A/B/C drifted for weeks without a single log line.")

    def test_it_logs_a_warning_with_a_traceback(self):
        src = _src()
        tail = src[src.index("except Exception"):]
        assert "logger.warning" in tail
        assert "exc_info=True" in tail, (
            "no traceback on the failure path — the next reader gets a message "
            "without the line that raised")

    def test_the_failure_message_says_the_guard_did_not_run(self):
        """'failed (non-fatal)' reads as harmless. It is not: the drift guard
        silently stopped guarding."""
        tail = _src()
        tail = tail[tail.index("except Exception"):]
        assert "did NOT run" in tail


class TestSilenceMeansSomething:
    def test_a_clean_run_logs(self):
        src = _src()
        assert "clean — metrics agree with the DB" in src, (
            "a clean reconcile is silent again, so an absence of "
            "METRICS-RECONCILE lines is ambiguous between 'ran fine' and "
            "'never ran' — the ambiguity that hid this for weeks")

    def test_the_clean_line_carries_the_numbers_to_check(self):
        src = _src()
        i = src.index("clean — metrics agree")
        window = src[i:i + 400]
        for token in ("cumulative_pnl", "daily_returns rows", "summary days"):
            assert token in window, f"the clean line omits {token!r}"

    def test_clean_and_drift_paths_are_mutually_exclusive(self):
        """Both must not fire on the same settlement — one says agree, the
        other says corrected, and printing both would be nonsense."""
        src = _src()
        assert "if not (corrected or abs(drift) > 0.01 or stops_drift):" in src
        assert "if corrected or abs(drift) > 0.01 or stops_drift:" in src
