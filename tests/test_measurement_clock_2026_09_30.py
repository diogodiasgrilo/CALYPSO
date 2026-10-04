"""The clock has to be able to accuse its author.

It exists because the gate it measures was prose: on 2026-09-30 I twice reported
changes as "purely additive, so it does not reset the clock" with no means to
check, and one of the three touched a live entry-skip decision.

So the property that matters is not "does it count" — it is **does a self-granted
exemption stay visible**. A single streak number would let a judgement call hide
inside it. These tests pin that the strict number ignores exemptions entirely and
that the two numbers diverge loudly when someone exempts themselves.

Driven against a REAL temporary git repository, because the classification reads
actual `git log --name-only` output and a mock would just re-state my assumptions
about its shape.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import measurement_clock as mc  # noqa: E402


class TestWhatCountsAsEconomic:
    def test_the_strategy_file_counts(self):
        assert mc.economic_paths(["bots/hydra/strategy.py"]) == ["bots/hydra/strategy.py"]

    def test_a_variant_config_counts(self):
        """Config is not code, but it changes contracts, caps and schedules."""
        assert mc.economic_paths(["bots/hydra/config/config_variant_b.json"])

    def test_the_broker_client_counts(self):
        assert mc.economic_paths(["shared/ib_client.py"])

    @pytest.mark.parametrize("p", [
        "docs/NEXT_STEPS.md", "tests/test_x.py", "scripts/analyze.py",
        "dashboard/backend/main.py", "services/homer/main.py",
        "shared/data_recorder.py", "bots/hydra/dc_status.py",
    ])
    def test_recording_and_rendering_do_not_count(self, p):
        assert mc.economic_paths([p]) == [], p

    def test_the_exclusion_list_is_what_spares_the_hydra_renderers(self):
        """`shared/data_recorder.py` is spared by OMISSION — no ECONOMIC
        pattern matches it, so its NOT_ECONOMIC entry is redundant and the
        case above passes for the wrong reason. A mutation control proved
        that. `bots/hydra/dc_status.py` is the real test of the mechanism:
        `^bots/hydra/.*\.py$` matches it, so only the exclusion can spare it.
        """
        assert mc.economic_paths(["bots/hydra/dc_status.py"]) == []
        assert any(r.match("bots/hydra/dc_status.py") for r in mc.ECONOMIC), (
            "dc_status is no longer matched by any ECONOMIC pattern, so this "
            "test has gone vacuous like the data_recorder case it replaces")

    def test_an_unknown_hydra_module_DOES_count(self):
        """Default-economic is the safe direction: a miss inflates the streak,
        and the streak is what the go-live gate reads."""
        assert mc.economic_paths(["bots/hydra/some_new_strategy.py"])


def _repo(tmp_path, commits, on=None):
    """A real git repo. `commits` = list of (path, message).

    `on` pins the commit date to a TRADING day (default: the most recent one).
    Without it, git stamps "now" — so on a Saturday or Sunday every commit in
    the fixture landed on a non-trading date, matched no trading day, and the
    streak assertions below silently inverted. These tests passed Mon-Fri and
    failed at weekends for reasons that had nothing to do with what they test.
    Pinning the date makes them deterministic whenever the suite runs.
    """
    import os
    if on is None:
        on = mc.trading_days_back(1)[0]
    stamp = "%sT12:00:00" % on.isoformat()
    env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    r = tmp_path / "r"
    r.mkdir()
    run = lambda *a: subprocess.run(a, cwd=r, capture_output=True, text=True,
                                    check=True, env=env)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    for path, msg in commits:
        f = r / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text((f.read_text() if f.exists() else "") + "x\n")
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", msg)
    return r


class TestTheStrictNumberCannotBeTalkedDown:
    def test_an_exemption_trailer_does_not_move_strict(self, tmp_path, monkeypatch):
        r = _repo(tmp_path, [
            ("README.md", "init"),
            ("bots/hydra/strategy.py",
             "feat: add a column\n\nTelemetry-Only: fills a NULL column"),
        ])
        monkeypatch.chdir(r)
        cs = mc.commits(30)
        econ = [c for c in cs if c["paths"]]
        assert len(econ) == 1, econ
        assert econ[0]["exempt"], "the trailer was not detected at all"

        # Call the REAL attribution rather than re-implementing it. These two
        # tests used to key on c["date"] themselves, which (a) meant they could
        # not catch a bug in main()'s keying — and the weekend blind spot was
        # exactly such a bug — and (b) made them fail on any weekend, because a
        # Saturday/Sunday commit date matched no trading day.
        by, _ = mc.attribute_to_trading_days(econ)
        days = mc.trading_days_back(30)
        s_strict, _, _ = mc.streak(days, by, honour_exemptions=False)
        s_claim, _, _ = mc.streak(days, by, honour_exemptions=True)

        assert s_strict == 0, (
            f"strict streak is {s_strict} — a self-granted trailer moved the "
            f"number the gate reads, which defeats the whole control")
        assert s_claim > s_strict, (
            "claimed streak did not rise above strict, so the exemption is "
            "invisible and the two-number design buys nothing")

    def test_without_a_trailer_both_numbers_agree(self, tmp_path, monkeypatch):
        r = _repo(tmp_path, [
            ("README.md", "init"),
            ("bots/hydra/strategy.py", "fix: change a stop threshold"),
        ])
        monkeypatch.chdir(r)
        cs = [c for c in mc.commits(30) if c["paths"]]
        by, _ = mc.attribute_to_trading_days(cs)
        days = mc.trading_days_back(30)
        assert mc.streak(days, by, False)[0] == mc.streak(days, by, True)[0] == 0

    def test_a_docs_only_commit_breaks_nothing(self, tmp_path, monkeypatch):
        r = _repo(tmp_path, [
            ("README.md", "init"),
            ("docs/NOTES.md", "docs: write things down"),
        ])
        monkeypatch.chdir(r)
        assert [c for c in mc.commits(30) if c["paths"]] == []


class TestTradingDays:
    def test_weekends_are_not_trading_days(self):
        for d in mc.trading_days_back(40):
            assert d.weekday() < 5, f"{d} is a {d.strftime('%A')}"

    def test_it_returns_the_number_asked_for(self):
        assert len(mc.trading_days_back(20)) == 20

    def test_they_are_strictly_newest_first(self):
        days = mc.trading_days_back(15)
        assert days == sorted(days, reverse=True)


class TestTheReportRuns:
    def test_json_mode_emits_the_gate_fields(self, capsys):
        assert mc.main(["--json", "--days", "20"]) == 0
        import json
        d = json.loads(capsys.readouterr().out)
        for k in ("target", "strict_streak", "claimed_streak", "met"):
            assert k in d, k
        assert d["target"] == 15
        assert d["met"] == (d["strict_streak"] >= 15)

    def test_the_human_report_runs_against_this_repo(self, capsys):
        assert mc.main(["--days", "20"]) == 0
        out = capsys.readouterr().out
        assert "STRICT streak" in out
