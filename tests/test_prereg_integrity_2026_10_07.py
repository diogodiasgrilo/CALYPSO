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


def _history_rich():
    """CI checks out with `fetch-depth: 1`, so `git log --since=` sees ONE
    squashed commit (observed: a single entry of 17,794 lines). Assertions
    about ranking and capping need real history, so they are skipped there and
    covered deterministically by the synthetic-repo tests below instead."""
    if not _git_ok():
        return False
    try:
        shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                                 cwd=str(ROOT), capture_output=True, text=True,
                                 timeout=10).stdout.strip()
        if shallow == "true":
            return False
    except Exception:
        return False
    cs = commits_since("2026-09-29", str(ROOT))
    return bool(cs) and len(cs) >= 6


needs_git = pytest.mark.skipif(not _git_ok(), reason="not a git checkout")
needs_history = pytest.mark.skipif(
    not _history_rich(), reason="shallow/insufficient git history (CI fetch-depth=1)")


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

    @needs_history
    def test_ranking_is_by_churn_descending(self):
        """Without this a 3-line logging tweak outranks a strike-selection
        rewrite and the real signal is buried, which is how a report like this
        ends up ignored."""
        out = _run([("e#4 slot prune", 2, 27, 0.40, "")], show_all=True)
        nums = [int(l.strip().split("L")[0]) for l in out.splitlines()
                if l.strip() and l.strip()[0].isdigit() and "L  " in l]
        assert len(nums) > 2
        assert nums == sorted(nums, reverse=True), nums

    @needs_history
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


def _make_repo(tmp_path, commits):
    """A throwaway git repo with controlled commits, so ranking and capping are
    tested against KNOWN churn rather than whatever history the environment
    happens to have. `commits` is [(date, subject, path, n_lines)]."""
    r = tmp_path / "repo"
    r.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    def git(*a, **kw):
        e = dict(**env, **kw.pop("extra", {}))
        subprocess.run(["git", *a], cwd=str(r), check=True,
                       capture_output=True, env={**__import__("os").environ, **e})
    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "base",
        extra={"GIT_AUTHOR_DATE": "2026-09-01T12:00:00", "GIT_COMMITTER_DATE": "2026-09-01T12:00:00"})
    for date, subj, path, n in commits:
        f = r / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(f"line {i}" for i in range(n)) + "\n")
        git("add", "-A")
        git("commit", "-q", "-m", subj,
            extra={"GIT_AUTHOR_DATE": f"{date}T12:00:00", "GIT_COMMITTER_DATE": f"{date}T12:00:00"})
    return str(r)


class TestTheLogicOnASyntheticRepo:
    """Deterministic in every environment, including CI's shallow checkout."""

    CUT = "2026-09-29"

    def _repo(self, tmp_path, n=8):
        # descending sizes so the expected ranking is unambiguous
        return _make_repo(tmp_path, [
            (f"2026-10-{(i % 28) + 1:02d}", f"change number {i}",
             "bots/hydra/strategy.py", 100 - i * 5)
            for i in range(n)
        ])

    def test_ranking_is_by_churn_descending(self, tmp_path):
        out = _run([("e#4 slot prune", 2, 27, 0.4, "")],
                   cutoff=self.CUT, root=self._repo(tmp_path), show_all=True)
        nums = [int(l.strip().split("L")[0]) for l in out.splitlines()
                if l.strip() and l.strip()[0].isdigit() and "L  " in l]
        assert len(nums) >= 6, nums
        assert nums == sorted(nums, reverse=True), nums

    def test_the_listing_caps_at_five(self, tmp_path):
        root = self._repo(tmp_path)
        few = _run([("e#4 slot prune", 2, 27, 0.4, "")], cutoff=self.CUT, root=root)
        many = _run([("e#4 slot prune", 2, 27, 0.4, "")], cutoff=self.CUT,
                    root=root, show_all=True)
        count = lambda t: sum(1 for l in t.splitlines() if "L  " in l)
        assert count(few) == 5
        assert count(many) == 8
        assert "+3 more" in few

    def test_a_commit_ON_the_cutoff_is_excluded(self, tmp_path):
        root = _make_repo(tmp_path, [("2026-09-29", "on the cutoff",
                                      "bots/hydra/strategy.py", 40)])
        out = _run([("e#4 slot prune", 2, 27, 0.4, "")], cutoff=self.CUT, root=root)
        assert "clean" in out.lower(), "a commit dated ON the cut-off must not count"

    def test_a_non_economic_commit_is_ignored(self, tmp_path):
        root = _make_repo(tmp_path, [("2026-10-02", "docs only", "docs/README.md", 50)])
        out = _run([("e#4 slot prune", 2, 27, 0.4, "")], cutoff=self.CUT, root=root)
        assert "clean" in out.lower()
