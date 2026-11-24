"""Two telemetry lines that reported the wrong thing, found in Monday's logs.

Both are the same family: a log line whose NUMBERS came from the wrong variable,
so the line was internally impossible and a reader acting on it would act wrongly.

── 1. MONITOR-STARVED counted exposure AFTER the blocking call ──────────────

`bots/hydra/main.py` timed `run_strategy_check()` and, when it blocked past the
threshold, read `daily_state.active_entries` to say how many entries were at
risk. It read it AFTERWARDS. So a block that WAS the placement of the day's
first entry reported "1 open entry" for a window in which the book was flat —
and then took the `> 0` branch, escalating to WARNING plus a MONITOR_STARVED
safety event, while the else-branch in the same block correctly says a flat book
"carries no stop risk".

Measured on the live seat 2026-11-23: three events, 161s / 185s / 246s. The 246s
one ran 13:44:58 -> 13:49:04 and e#1 filled at 13:49:02, so the position existed
for 2 of 246 seconds and nothing was left unmonitored. The other two had 2 and 3
entries genuinely open and 5 and 4 ENTRY-CHECKPOINT stop checks inside them.
All three rendered identically, so the one false alarm was indistinguishable
from the two real ones.

── 2. The GEX cap warning mixed CONTRACTS with STRIKES ──────────────────────

`bots/hydra/brandon/strategy.py` printed `contributed` — `len(profile.strikes)`,
a STRIKE count — into a slot labelled "hydrated", beside `candidates_found` and
the cap, which are CONTRACT counts:

    "286 candidates found, only 144 hydrated (cap=250)"

Impossible on its face: 144 is BELOW the cap, so the cap cannot be what limited
it. At ~2 contracts per strike, 144 strikes is ~288 contracts — essentially the
whole candidate set — so a 12.6% exclusion (36 of 286) read as a ~50% hydration
collapse. This is the SAME units defect the comment above `not_hydrated` records
fixing on 2026-09-11, repeated in the adjacent block.

── Why these are extracted and tested here ──────────────────────────────────

Both lived inline, where the only available assertion was a source grep — and a
grep cannot tell whether the right VARIABLE reached the right SLOT, which is the
entire defect. Each is now a named function, and the WIRING tests below parse
the call sites with `ast` to check the actual arguments, so passing the strike
count as `hydrated_contracts` is a test failure rather than a plausible line.
"""

from __future__ import annotations

import ast
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.main import monitor_starved_report  # noqa: E402
from bots.hydra.brandon.strategy import gex_cap_binding_message  # noqa: E402


# ==========================================================================
# 1. MONITOR-STARVED
# ==========================================================================

class TestExposureIsWhatWasOpenBEFORE:
    def test_the_live_seat_false_alarm_is_now_info(self):
        """THE REGRESSION, with Monday's real numbers: a 246s block that was
        itself the first entry's placement. Flat for the window => INFO."""
        level, msg = monitor_starved_report(246, 0, 1, 0)
        assert level == "info", "a flat-book block must not be a WARNING"
        assert "0 open entries AT RISK" in msg

    def test_it_still_says_the_block_opened_something(self):
        """Not ignored: the checkpoint cannot protect a position that does not
        exist yet, but hiding it would understate what the block did."""
        _, msg = monitor_starved_report(246, 0, 1, 0)
        assert "+1 opened during it" in msg

    def test_a_genuine_exposure_is_still_a_warning(self):
        """Monday's other two events must keep their severity."""
        for elapsed, before, after, cp in ((185, 2, 2, 5), (161, 3, 3, 4)):
            level, msg = monitor_starved_report(elapsed, before, after, cp)
            assert level == "warning", f"{before} open entries must warn"
            assert f"{before} open entries AT RISK" in msg

    def test_CONTROL_the_old_after_read_mislabels_it(self):
        """The defect, reproduced: feed the AFTER count as the exposure and the
        same event becomes a WARNING claiming an entry was at risk."""
        level, msg = monitor_starved_report(246, 1, 1, 0)   # as if read after
        assert level == "warning"
        assert "1 open entry AT RISK" in msg

    def test_checkpoint_coverage_is_reported_when_it_ran(self):
        _, msg = monitor_starved_report(185, 2, 2, 5)
        assert "5 stop check(s) ran inside it (ENTRY-CHECKPOINT)" in msg

    def test_blindness_is_reported_only_when_nothing_ran(self):
        _, msg = monitor_starved_report(246, 2, 2, 0)
        assert "no stop check" in msg
        _, msg2 = monitor_starved_report(246, 2, 2, 1)
        assert "no stop check" not in msg2

    def test_singular_and_plural(self):
        assert "1 open entry AT RISK" in monitor_starved_report(200, 1, 1, 0)[1]
        assert "2 open entries AT RISK" in monitor_starved_report(200, 2, 2, 0)[1]
        assert "0 open entries AT RISK" in monitor_starved_report(200, 0, 0, 0)[1]

    def test_flat_throughout_is_info_with_no_born_clause(self):
        level, msg = monitor_starved_report(200, 0, 0, 0)
        assert level == "info"
        assert "opened during it" not in msg

    def test_the_error_sentinel_cannot_invent_a_born_count(self):
        """`-1` means the read raised. It must not become a bogus delta."""
        for before, after in ((-1, 3), (3, -1), (-1, -1)):
            _, msg = monitor_starved_report(200, before, after, 0)
            assert "opened during it" not in msg, (before, after)

    def test_a_shrinking_book_is_not_reported_as_born(self):
        """Entries CLOSING during the block is normal (a stop fired)."""
        _, msg = monitor_starved_report(200, 3, 1, 4)
        assert "opened during it" not in msg
        assert "3 open entries AT RISK" in msg

    def test_elapsed_is_rendered_without_decimals(self):
        _, msg = monitor_starved_report(246.47, 0, 0, 0)
        assert "blocked for 246s" in msg


class TestTheLoopSamplesExposureBeforeTheCall:
    """Wiring. The helper is only correct if the caller feeds it the BEFORE
    count — which is the thing that was wrong."""

    @staticmethod
    def _main_tree() -> ast.Module:
        with open(os.path.join(ROOT, "bots/hydra/main.py"), encoding="utf-8") as fh:
            return ast.parse(fh.read())

    def test_open_before_is_assigned_earlier_in_the_source_than_the_call(self):
        src = open(os.path.join(ROOT, "bots/hydra/main.py"), encoding="utf-8").read()
        i_before = src.index("_open_before = len(")
        i_call = src.index("action = strategy.run_strategy_check()")
        i_after = src.index("_open_after = len(")
        assert i_before < i_call < i_after, (
            "exposure must be sampled BEFORE run_strategy_check() and the "
            "after-count AFTER it"
        )

    def test_the_report_is_called_with_before_then_after(self):
        """Order matters and swapping them restores the bug silently."""
        tree = self._main_tree()
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "monitor_starved_report"]
        assert len(calls) == 1, f"expected one call site, found {len(calls)}"
        names = [getattr(a, "id", None) for a in calls[0].args]
        assert names[1:3] == ["_open_before", "_open_after"], names

    def test_the_safety_event_uses_the_before_count(self):
        """The event body must not re-introduce the after-count."""
        src = open(os.path.join(ROOT, "bots/hydra/main.py"), encoding="utf-8").read()
        blk = src[src.index('"MONITOR_STARVED",'):]
        blk = blk[:blk.index("except Exception")]
        assert "_open_before" in blk and "_open_after" not in blk


# ==========================================================================
# 2. GEX cap message — contracts vs strikes
# ==========================================================================

class TestContractsAndStrikesStayApart:
    def test_mondays_real_numbers_render_sensibly(self):
        msg = gex_cap_binding_message(candidates_found=286, cap=250,
                                      hydrated_contracts=284, contributed_strikes=144)
        assert "286 candidate contract(s) qualified" in msg
        assert "cap=250" in msg
        assert "so 36 were EXCLUDED" in msg
        assert "284 contract(s) hydrated" in msg
        assert "144 strike(s)" in msg

    def test_the_strike_count_is_never_presented_as_hydrated_contracts(self):
        """THE DEFECT. 144 must not appear as the hydrated-contract figure."""
        msg = gex_cap_binding_message(candidates_found=286, cap=250,
                                      hydrated_contracts=284, contributed_strikes=144)
        assert "144 contract(s) hydrated" not in msg
        assert "only 144 hydrated" not in msg

    def test_CONTROL_the_old_line_was_self_contradictory(self):
        """Reproduce the bug by passing strikes where contracts belong, and
        show it yields a figure BELOW the cap — which is impossible if the cap
        is what bound. That impossibility is what made it detectable."""
        msg = gex_cap_binding_message(candidates_found=286, cap=250,
                                      hydrated_contracts=144,   # the bug
                                      contributed_strikes=144)
        assert "144 contract(s) hydrated" in msg
        assert 144 < 250, "the control's premise"

    def test_excluded_is_floored_at_zero(self):
        """Anchored on the preceding "so " deliberately: a bare
        `"0 were EXCLUDED" in msg` is SATISFIED by "-150 were EXCLUDED", because
        the 0 of 150 substring-matches. Removing the max(0, ...) floor passed
        this test until 2026-11-23, found by the mutation pass, not by review."""
        msg = gex_cap_binding_message(candidates_found=100, cap=250,
                                      hydrated_contracts=100, contributed_strikes=50)
        assert "so 0 were EXCLUDED" in msg
        assert "-" not in msg.split("EXCLUDED")[0], "negative exclusion leaked"

    def test_excluded_is_candidates_minus_cap(self):
        msg = gex_cap_binding_message(candidates_found=300, cap=250,
                                      hydrated_contracts=250, contributed_strikes=130)
        assert "so 50 were EXCLUDED" in msg

    def test_the_units_are_named_in_the_line(self):
        """So the next reader cannot repeat the comparison."""
        msg = gex_cap_binding_message(candidates_found=286, cap=250,
                                      hydrated_contracts=284, contributed_strikes=144)
        assert "different units" in msg and "~2 contracts per strike" in msg


class TestTheGexCallSitePassesTheRightUnits:
    def test_hydrated_contracts_is_the_contract_counter(self):
        """`with_greeks_or_iv` is contracts; `contributed` is strikes. Swapping
        them is the original bug and must fail here."""
        src = open(os.path.join(ROOT, "bots/hydra/brandon/strategy.py"),
                   encoding="utf-8").read()
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "gex_cap_binding_message"]
        assert len(calls) == 1, f"expected one call site, found {len(calls)}"
        kw = {k.arg: getattr(k.value, "id", None) for k in calls[0].keywords}
        assert kw.get("hydrated_contracts") == "with_greeks_or_iv", kw
        assert kw.get("contributed_strikes") == "contributed", kw
        assert kw.get("candidates_found") == "candidates_found", kw

    def test_the_call_is_fully_keyword_based(self):
        """Positional args here are how the units got crossed in the first
        place — four ints in a row with no names."""
        src = open(os.path.join(ROOT, "bots/hydra/brandon/strategy.py"),
                   encoding="utf-8").read()
        tree = ast.parse(src)
        call = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "gex_cap_binding_message"][0]
        assert not call.args, "pass every argument by keyword"
