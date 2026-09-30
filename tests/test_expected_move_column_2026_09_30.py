"""`trade_entries.expected_move` wrote NULL on all 315 rows.

The payload read `getattr(self, '_last_expected_move', None)` and **nothing in
the repository ever assigned that attribute** — while `_check_whipsaw_filter`
computed the identical quantity a few lines away and threw it away. Now there
is one definition, `_expected_daily_move()`, computed on demand so the column
does not depend on a side effect having run first.

The filter is a LIVE decision path on B: it skips entries. So the first
question is not "does the column fill" but "does the skip decision still come
out the same". That is what the equivalence grid below is for — it reimplements
the ORIGINAL inline formula and requires the refactored filter to agree on
every cell.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _s(spx_open, vix_open, spx_high, spx_low, mult=1.75):
    s = HydraStrategy.__new__(HydraStrategy)
    s.market_data = SimpleNamespace(spx_open=spx_open, vix_open=vix_open,
                                    spx_high=spx_high, spx_low=spx_low)
    s.whipsaw_range_skip_mult = mult
    return s


def _original_would_skip(spx_open, vix_open, spx_high, spx_low, mult):
    """The formula exactly as it stood before the refactor."""
    if not spx_open or spx_open <= 0 or not vix_open or vix_open <= 0:
        return None
    if spx_low == float("inf") or spx_low <= 0 or spx_high <= 0:
        return None
    expected_move = spx_open * (vix_open / 100) / (252 ** 0.5)
    return (spx_high - spx_low) > mult * expected_move


class TestTheSkipDecisionIsUnchanged:
    """A live filter. Behaviour equivalence comes before the new column."""

    @pytest.mark.parametrize("spx_open", [6800.0, 7700.0])
    @pytest.mark.parametrize("vix_open", [12.0, 16.5, 22.0, 31.0])
    @pytest.mark.parametrize("range_pts", [5.0, 40.0, 80.0, 140.0, 300.0])
    @pytest.mark.parametrize("mult", [1.5, 1.75, 2.5])
    def test_grid_agrees_with_the_original_formula(self, spx_open, vix_open,
                                                   range_pts, mult):
        high = spx_open + range_pts * 0.6
        low = spx_open - range_pts * 0.4
        got = _s(spx_open, vix_open, high, low, mult)._check_whipsaw_filter()
        expected_skip = _original_would_skip(spx_open, vix_open, high, low, mult)
        assert bool(got) == bool(expected_skip), (
            f"skip decision CHANGED at spx={spx_open} vix={vix_open} "
            f"range={range_pts} mult={mult}: refactor says "
            f"{'SKIP' if got else 'allow'}, original says "
            f"{'SKIP' if expected_skip else 'allow'}")

    def test_a_disabled_filter_never_skips(self):
        s = _s(7700.0, 16.0, 7900.0, 7500.0, mult=None)
        s.whipsaw_range_skip_mult = None
        assert s._check_whipsaw_filter() is None

    def test_missing_vix_does_not_block_an_entry(self):
        """"No data" must mean "do not block", not "skip"."""
        assert _s(7700.0, 0.0, 7750.0, 7650.0)._check_whipsaw_filter() is None

    def test_no_range_yet_does_not_block(self):
        assert _s(7700.0, 16.0, 7700.0, float("inf"))._check_whipsaw_filter() is None


class TestTheValueItself:
    def test_it_matches_the_vix_implied_one_day_move(self):
        em = _s(7700.0, 16.0, 7750.0, 7650.0)._expected_daily_move()
        expected = 7700.0 * 0.16 / (252 ** 0.5)
        assert em == pytest.approx(expected), f"{em} != {expected}"
        assert em == pytest.approx(77.6, abs=0.5), em

    def test_higher_vix_means_a_larger_move(self):
        lo = _s(7700.0, 12.0, 7750.0, 7650.0)._expected_daily_move()
        hi = _s(7700.0, 24.0, 7750.0, 7650.0)._expected_daily_move()
        assert hi > lo * 1.9, (
            f"EM should scale with VIX: {lo} -> {hi}; if it does not, the "
            f"column is not measuring implied move")

    def test_unknown_inputs_give_none_not_zero(self):
        """NULL reads as "not computable"; 0.0 reads as "a flat market"."""
        assert _s(0.0, 16.0, 7750.0, 7650.0)._expected_daily_move() is None
        assert _s(7700.0, None, 7750.0, 7650.0)._expected_daily_move() is None
        s = HydraStrategy.__new__(HydraStrategy)
        s.market_data = None
        assert s._expected_daily_move() is None


class TestThePayloadNoLongerReadsAnAttributeNobodyWrites:
    def test_no_code_reads_the_dead_attribute(self):
        """The root cause: a READ of an attribute nothing assigns.

        Targets the code pattern, not the name — the explanation of this bug
        lives in a docstring that necessarily mentions it, and a test that
        cannot tell prose from code fails on its own documentation. Which is
        exactly what this one did on first run.
        """
        import re
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run(
            ["grep", "-rn", "--include=*.py", "_last_expected_move", "bots/", "shared/"],
            cwd=root, capture_output=True, text=True).stdout

        offending = []
        for line in out.splitlines():
            body = line.split(":", 2)[-1]
            stripped = body.strip()
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            # a read via getattr, or any assignment to it
            if re.search(r"getattr\s*\(\s*self\s*,\s*['\"]_last_expected_move", body) \
                    or re.search(r"_last_expected_move\s*=", body):
                offending.append(line)

        assert not offending, (
            "the dead attribute is back in CODE (not just prose):\n"
            + "\n".join(offending))

    def test_the_payload_calls_the_method(self):
        import inspect
        src = inspect.getsource(HydraStrategy._record_entry_to_db) \
            if hasattr(HydraStrategy, "_record_entry_to_db") else ""
        if not src:
            pytest.skip("entry recorder not found under that name")
        assert "_expected_daily_move()" in src
