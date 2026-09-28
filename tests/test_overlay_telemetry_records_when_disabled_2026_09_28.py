"""The overlay telemetry must record even when the hedge structures are OFF.

THE BUG. `_brandon_record_gex_decision(consumer="overlay", ...)` lived inside
the `else:` branch of the enabled/disabled test in `_brandon_check_overlay`.
That branch is only taken when the structure for the current window is
enabled.

The debit spread was disabled 2026-08-25 and the butterfly 2026-09-04. This
telemetry was added 2026-09-05 — the day AFTER — into a branch the disable had
already made dead. It recorded ZERO rows in 23 days while its own comment said
it existed to "show whether the corrected gate would have confirmed on the put
side where the live one never does". The one question it was built to answer
was the one it could not observe.

Meanwhile 3,316 `BRANDON-OVERLAY-WATCH` lines were written to the log over the
same period, so the observations were happening — just never persisted.

This is pure telemetry. `_has_accel_zone_on_side` is a pure function over an
already-fetched GEX profile: no broker call, no order path. The hedges stay
off; only the recording changes.
"""
from __future__ import annotations

import ast
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_SRC = Path(__file__).resolve().parents[1] / "bots" / "hydra" / "brandon" / "strategy.py"


def _find_overlay_record_calls(tree):
    """Every _brandon_record_gex_decision call whose consumer is "overlay"."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute)
                and f.attr == "_brandon_record_gex_decision"):
            continue
        for kw in node.keywords:
            if (kw.arg == "consumer" and isinstance(kw.value, ast.Constant)
                    and kw.value.value == "overlay"):
                out.append(node)
    return out


def _enclosing_branches(tree, target):
    """Every `if` whose body (not orelse) contains `target`."""
    encl = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for sub in ast.walk(ast.Module(body=node.orelse, type_ignores=[])):
            if sub is target:
                encl.append(("orelse", node))
        for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if sub is target:
                encl.append(("body", node))
    return encl


class TestOverlayTelemetryIsNotGatedOnTheHedgeBeingEnabled:
    def test_exactly_one_overlay_record_site(self):
        """Two sites would double-count every sample once a hedge is re-enabled."""
        tree = ast.parse(io.open(_SRC, encoding="utf-8").read())
        calls = _find_overlay_record_calls(tree)
        assert len(calls) == 1, (
            f"expected exactly 1 overlay telemetry call site, found {len(calls)}")

    def test_it_is_not_inside_a_structure_enabled_branch(self):
        """THE REGRESSION. Re-nesting it under the enabled test kills it again.

        Reintroduce the old placement and this fails: the call would sit in the
        `orelse` of `if not structure_enabled:`.
        """
        src = io.open(_SRC, encoding="utf-8").read()
        tree = ast.parse(src)
        call = _find_overlay_record_calls(tree)[0]
        for where, node in _enclosing_branches(tree, call):
            cond = ast.unparse(node.test)
            assert "structure_enabled" not in cond, (
                "overlay telemetry is nested under a `structure_enabled` test "
                f"({where} of `if {cond}:`) — it will record nothing whenever "
                "the hedge for that window is disabled, which is exactly the "
                "2026-09-05 bug that cost 23 days of data.")

    def test_gex_confirmed_is_computed_before_the_branch(self):
        """The recording needs the value, so it must be computed above the split."""
        src = io.open(_SRC, encoding="utf-8").read()
        i_assign = src.index("gex_confirmed = bool(")
        i_branch = src.index("if not structure_enabled:")
        assert i_assign < i_branch, (
            "gex_confirmed is computed after the enabled/disabled branch, so "
            "the disabled path cannot record it")

    def test_the_disabled_log_line_does_NOT_report_gex_confirmed(self):
        """Deliberately absent — and this pins it stays absent.

        A 2026-08-25 audit found that printing "gex_confirmed=True" on a
        window whose structure is disabled misleads an operator into thinking
        a hedge fired or could fire. I reintroduced it here on 2026-09-28 and
        `test_watch_log_reports_disabled_structure_instead_of_a_misleading_
        gex_confirmed` caught it. The value belongs in the DATABASE, where the
        arming-gate question is answered; the log line is for the operator,
        and for them the only relevant fact is that nothing will hedge.
        """
        src = io.open(_SRC, encoding="utf-8").read()
        i = src.index("DISABLED for this ")
        # the logger.info call that owns this literal
        start = src.rindex("logger.info(", 0, i)
        end = src.index(")", src.index('"', i))
        assert "gex_confirmed" not in src[start:end], (
            "gex_confirmed is back in the disabled-window log line")

    def test_the_record_cannot_break_the_monitoring_loop(self):
        """Telemetry runs inside the tick; it must not be able to raise."""
        src = io.open(_SRC, encoding="utf-8").read()
        i = src.index('consumer="overlay"')
        before = src[max(0, i - 400):i]
        assert "try:" in before, (
            "the overlay telemetry record is not wrapped in try/except — a "
            "recorder fault would propagate into the monitoring loop")
