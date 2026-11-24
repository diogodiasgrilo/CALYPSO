"""The trading loop can go blind for minutes and nothing said so.

`run_strategy_check()` performs entries AND stop monitoring sequentially on one
thread, so a slow entry attempt is a blind spell for every stop check, MKT-046
confirmation window and A2 evaluation in it.

Measured 2026-10-21 on the live seat `bl`: a single **569-second** gap in
`market_ticks` (13:14:56 -> 13:24:25), spanning three entry-#3 attempts against
a broker serving 75s ReadTimeouts, while entry #1 was open at 72% call cushion.
Nothing in the log mentioned it. It was recoverable only as an absence — a hole
in a table — which is the worst possible shape for an operational signal.

Fleet-wide since 2026-09-01 (RTH gaps >120s): b 96 spells / 410 min, bl 17 /
98 min. Splitting by whether a gap begins within 120s of one of that variant's
own entry slots shows entry attempts are the cause, and that the cost is
bimodal rather than a slow drift:

    bl  at a slot   median  29s  p90  66s  p99 553s  max 569s
    bl  elsewhere   median  28s  p90  38s  p99  64s  max 374s

So the threshold is set BETWEEN two measured modes, not guessed: 120s is ~2x
the healthy p90, leaving a normal day silent.

⚠️ The risk is LATENT. Across 73 spells >180s and 63 stops on b+bl, no stop has
fired inside one or within 180s after one. At ~2.9% of RTH blind, ~1.6
coincidences were expected by chance, so observing zero is luck rather than
evidence of safety. That is precisely why this is instrumentation and not a
re-architecture: we need to know if it ever does coincide.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

SRC = open(os.path.join(ROOT, "bots/hydra/main.py")).read()

# ⚠️ CONVERTED 2026-11-23. Seven tests in this file asserted on main.py's inline
# SOURCE TEXT — `"if _open > 0:" in tail`, `"{_cover}" in tail`,
# `"{_iter_elapsed:.0f}s" in tail`. When the report builder was extracted into
# `monitor_starved_report()` (behaviour unchanged), all seven broke, while the
# invariants they were meant to protect still held.
#
# Worse, they could not see the bug the extraction was done to fix: exposure was
# being read AFTER the blocking call, so a flat book reported "1 open entry" and
# escalated to a safety event. The text was exactly as these tests demanded and
# the number was still wrong.
#
# They are now behavioural assertions against the function. Same invariants,
# same names, able to fail for the right reason. See
# tests/test_monitor_starved_and_gex_units_2026_11_23.py and the
# `source_grep_tests_are_blind` note.
from bots.hydra.main import monitor_starved_report  # noqa: E402


class TestTheThresholdIsBetweenTheMeasuredModes:
    def test_it_exists_at_module_level(self):
        from bots.hydra import main
        assert isinstance(main.MONITOR_STARVED_WARN_S, float)

    def test_it_sits_above_the_healthy_p90_and_below_the_degraded_tail(self):
        """66s healthy p90, 553s degraded p99. A threshold inside that window
        reports only genuine starvation; one below 66s would fire on normal
        entries and be ignored within a week."""
        from bots.hydra import main
        assert 66.0 < main.MONITOR_STARVED_WARN_S < 553.0


class TestItIsWiredAroundTheRightCall:
    def test_it_times_run_strategy_check_specifically(self):
        """Timing the whole iteration instead would fold in the deliberate
        sleep at the bottom of the loop and never mean anything."""
        # The checkpoint counter is captured between the start mark and the
        # call (2026-10-26); allow bookkeeping lines but NOT another call.
        # Ordering, not an exact text shape: bookkeeping lines (the checkpoint
        # counter 2026-10-26, the exposure sample 2026-11-23) legitimately sit
        # between the start mark and the call, and a brittle regex over them is
        # what made this test break on a behaviour-preserving refactor.
        i_start = SRC.index("_iter_started = time.monotonic()")
        i_call = SRC.index("action = strategy.run_strategy_check()")
        i_end = SRC.index("_iter_elapsed = time.monotonic() - _iter_started")
        assert i_start < i_call < i_end, "the timer must bracket the call"
        bracket = SRC[i_start:i_end]
        assert "broker." not in bracket, "a broker call is inside the timer"
        # exactly ONE call is timed, so the window cannot silently widen
        assert bracket.count("strategy.run_strategy_check()") == 1
        assert "time.sleep" not in bracket, "the loop's sleep is inside the timer"

    def test_it_uses_a_MONOTONIC_clock(self):
        """Wall-clock would report a spurious multi-hour spell across a DST
        shift or an ntp step, and this bot already runs on ET with DST."""
        i = SRC.index("_iter_started = ")
        assert "time.monotonic()" in SRC[i:i + 60]
        assert "datetime.now" not in SRC[i:i + 60]


class TestSeverityTracksWhetherPositionsAreOPEN:
    """An identical block on a flat book carries no stop risk. Warning on both
    would train the reader to ignore the one that matters."""

    def test_open_positions_WARN_and_log_a_safety_event(self):
        level, _ = monitor_starved_report(569, 1, 1, 0)
        assert level == "warning"
        # the caller must still raise a queryable safety event on that branch
        i = SRC.index('_level == "warning"')
        assert "MONITOR_STARVED" in SRC[i:i + 700], "not a queryable safety event"
        assert "logger.warning(_msg)" in SRC[i:i + 700]

    def test_a_flat_book_is_INFO_not_a_warning(self):
        level, _ = monitor_starved_report(569, 0, 0, 0)
        assert level == "info"
        assert "logger.info(_msg)" in SRC, "the info branch is gone"

    def test_the_message_states_the_DURATION_and_the_position_count(self):
        """Both numbers are the finding. 'The loop was slow' is not actionable;
        '569s with 1 open entry' is."""
        _, msg = monitor_starved_report(569, 1, 1, 0)
        assert "569s" in msg
        assert "1 open entry" in msg


class TestObservabilityCannotBreakTheTradingLoop:
    """The 2026-10-06 swallowed-exception work is the precedent: a counter that
    raises is worse than no counter. Both reads here are defended."""

    def test_the_position_count_read_is_guarded(self):
        """BOTH reads now — before and after the call (2026-11-23)."""
        for var in ("_open_before = len(", "_open_after = len("):
            i = SRC.index(var)
            assert "except Exception" in SRC[i:i + 300], f"{var} is unguarded"

    def test_an_unreadable_book_cannot_invent_a_count(self):
        """The guards set -1. That must not render as exposure or as a delta."""
        level, msg = monitor_starved_report(200, -1, -1, 0)
        assert level == "info", "a failed read must not escalate to a warning"
        assert "opened during it" not in msg

    def test_the_safety_event_call_is_guarded(self):
        i = SRC.index('"MONITOR_STARVED",')
        assert "except Exception" in SRC[i:i + 420]

    def test_negative_control_the_old_loop_had_no_timer_at_all(self):
        """Proves this file is testing an addition: the bare call with no
        surrounding timer must no longer appear."""
        assert not re.search(
            r"\n\s*# Run strategy check\n\s*action = strategy\.run_strategy_check\(\)",
            SRC), "the untimed call shape is what allowed a silent 569s spell"

class TestItDerivesCoverageRatherThanAssertingBlindness:
    """The message claimed "no stop check ... ran in that window". That was
    true until the ENTRY-CHECKPOINT (2026-10-25) started running stop checks
    INSIDE run_strategy_check — after which a long iteration no longer implies
    a blind one, and the line asserted something it could not know.

    Seen live 2026-10-26: variant A blocked 191s and the message still claimed
    total blindness, with nothing in the logs saying whether the checkpoint had
    fired. Fourth occurrence of assert-instead-of-derive in this project.

    ⚠️ The checkpoint does NOT shorten this number. MONITOR-STARVED times the
    OUTER iteration; the checkpoint runs inside it. Expecting the durations to
    fall was my own error — what changes is the coverage during them.
    """

    def test_it_counts_checkpoints_across_the_iteration(self):
        import re
        assert re.search(r"_cp_before = getattr\(strategy, \"entry_checkpoints_run\", 0\)",
                         SRC), "no before-count"
        assert "_cp_ran = getattr(strategy, \"entry_checkpoints_run\", 0) - _cp_before" in SRC

    def test_the_blind_claim_is_conditional_on_zero_checkpoints(self):
        _, blind = monitor_starved_report(191, 1, 1, 0)
        assert "no stop check" in blind, "blindness is not claimed when it should be"
        _, covered = monitor_starved_report(191, 1, 1, 3)
        assert "no stop check" not in covered, (
            "blindness is still asserted unconditionally")
        assert "ENTRY-CHECKPOINT" in covered, "a covered window does not say so"

    def test_the_message_uses_the_derived_clause(self):
        """The clause must come from the checkpoint count, not be hardcoded."""
        _, a = monitor_starved_report(191, 1, 1, 0)
        _, b = monitor_starved_report(191, 1, 1, 5)
        assert a != b, "the message does not vary with checkpoint coverage"
        assert "5 stop check(s) ran inside it" in b

    def test_the_counter_increments_in_the_checkpoint(self):
        import inspect
        from bots.hydra.base_strategy import MEICStrategy
        src = inspect.getsource(MEICStrategy._entry_checkpoint)
        assert "entry_checkpoints_run" in src

    def test_a_strategy_without_the_counter_still_reports(self):
        """getattr default 0 -> reads as blind, which is the safe reading for
        a strategy that has no checkpoint at all."""
        assert 'getattr(strategy, "entry_checkpoints_run", 0)' in SRC
