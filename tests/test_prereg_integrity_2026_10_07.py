"""The pre-registration integrity check in `scripts/registered_test_eta.py`.

Counting out-of-sample observations says how MUCH data has accrued and nothing
about whether the system generating it stayed still — so the ETA report would
happily say "25/25, go read the result" on a sample collected across a change
to the behaviour under test.

The live example: `1ab9222` (2026-10-01) raised the Polygon chain page cap
4 -> 20, altering the delta ladder the GEX adjuster picks strikes from, INSIDE
the GEX test's out-of-sample window. It was caught by hand. These tests pin
that it is now caught automatically.

The check FLAGS; it does not rule. Deciding whether a flagged commit actually
invalidates a test needs a data check (for 1ab9222: truncated-chain days
contributed 2 decisions and 0 vetoes, so it was clean).
"""

import io
import contextlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.registered_test_eta import (  # noqa: E402
    TEST_DEPS, commits_since, integrity_report,
)

TESTS = [("GEX gate", 13, 25, 2.28, ""), ("e#4 slot prune", 2, 27, 0.40, "")]


def _run(tests=TESTS, cutoff="2026-09-29", root=str(ROOT), show_all=False):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        integrity_report(tests, cutoff, root, show_all=show_all)
    return buf.getvalue()


def _git_ok():
    try:
        return subprocess.run(["git", "rev-parse", "--git-dir"], cwd=str(ROOT),
                              capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


needs_git = pytest.mark.skipif(not _git_ok(), reason="not a git checkout")


class TestItSharesOneDefinitionOfEconomic:

    def test_classification_comes_from_measurement_clock(self):
        """If the two tools kept separate path lists they would silently drift,
        and the clock and the integrity check would disagree about what can
        change a trading decision."""
        import scripts.registered_test_eta as rte
        from scripts.measurement_clock import economic_paths as mc_econ
        assert rte.economic_paths is mc_econ


class TestEveryRegisteredTestHasADependencyMap:

    def test_the_three_registered_tests_are_covered(self):
        for name in ("GEX gate", "e#4 slot prune", "one-entry-a-day"):
            assert name in TEST_DEPS, f"{name} has no dependency map — it would be silently skipped"

    @pytest.mark.parametrize("name", list(TEST_DEPS))
    def test_each_map_is_well_formed(self, name):
        dep = TEST_DEPS[name]
        assert dep["direct"], f"{name}: empty direct list matches nothing"
        assert dep.get("measures"), f"{name}: must say what it measures"

    def test_the_GEX_map_actually_matches_the_gex_provider(self):
        """The whole point: 1ab9222 touched bots/hydra/brandon/gex_provider.py."""
        dep = TEST_DEPS["GEX gate"]
        path = "bots/hydra/brandon/gex_provider.py"
        assert any(r.search(path) for r in dep["direct"]), (
            "the GEX map would NOT have caught the chain-truncation commit")

    def test_the_slot_maps_match_strategy_py(self):
        for name in ("e#4 slot prune", "one-entry-a-day"):
            assert any(r.search("bots/hydra/strategy.py")
                       for r in TEST_DEPS[name]["direct"]), name


@needs_git
class TestItReadsRealHistory:

    def test_it_finds_economic_commits_after_the_cutoff(self):
        cs = commits_since("2026-09-29", str(ROOT))
        assert cs, "no commits found — the git parse is broken"
        assert all(c["date"] > "2026-09-29" for c in cs), "cut-off not applied strictly"
        assert all(c["paths"] for c in cs), "a commit with no economic path should be dropped"

    def test_churn_is_captured_per_path(self):
        cs = commits_since("2026-09-29", str(ROOT))
        assert any(sum(c["churn"].values()) > 0 for c in cs), "numstat never parsed"

    def test_it_FLAGS_the_chain_truncation_commit_for_the_GEX_test(self):
        """The regression this file exists for."""
        out = _run([("GEX gate", 13, 25, 2.28, "")], show_all=True)
        assert "1ab9222" in out, "the chain-truncation commit was NOT flagged"
        assert "REVIEW" in out

    def test_it_says_a_flag_is_not_a_verdict(self):
        out = " ".join(_run().split())        # the note wraps across lines
        assert "a flag is not a verdict" in out.lower()

    def test_ranking_is_by_churn_descending(self):
        """Without this a 3-line logging tweak outranks a strike-selection
        rewrite and the real signal is buried, which is how a report like this
        ends up ignored."""
        out = _run([("e#4 slot prune", 2, 27, 0.40, "")], show_all=True)
        nums = [int(l.strip().split("L")[0]) for l in out.splitlines()
                if l.strip() and l.strip()[0].isdigit() and "L  " in l]
        assert len(nums) > 2
        assert nums == sorted(nums, reverse=True), nums

    def test_the_listing_is_capped_unless_all_is_passed(self):
        few = _run([("e#4 slot prune", 2, 27, 0.40, "")], show_all=False)
        many = _run([("e#4 slot prune", 2, 27, 0.40, "")], show_all=True)
        count = lambda t: sum(1 for l in t.splitlines() if "L  " in l)
        assert count(few) == 5
        assert count(many) > count(few)
        assert "more" in few


class TestItFailsLoudRatherThanQuiet:

    def test_a_missing_repo_reports_UNKNOWN_not_clean(self, tmp_path):
        """The dangerous failure is a silent 'clean' on a check that never ran."""
        out = _run(root=str(tmp_path))
        low = out.lower()
        assert "not checked" in low or "unavailable" in low
        assert "all tests clean" not in low

    def test_a_future_cutoff_reports_clean_explicitly(self):
        out = _run(cutoff="2099-01-01")
        assert "clean" in out.lower()

    def test_an_unknown_test_name_is_skipped_not_crashed(self):
        out = _run([("not a registered test", 1, 2, 0.5, "")])
        assert "not a registered test" not in out
