"""Two "lifetime" figures over different windows are not comparable.

Found by asking, after everything else was closed, whether anything I had just
changed could have broken something quietly. It had.

``_calendar_group_comparison`` rebases each member on its OWN
``variant_<id>_baseline_date`` and then scores the leaderboard on "lifetime
cumulative_pnl". It has always sent a ``baselines`` map with the comment *"so the
UI can caption 'since <date>'"* — **and the UI never read it.** While D and E sat
at 2026-07-11 and 2026-07-07 that was nearly harmless. The E metrics epoch
(2026-09-24) made the windows 2.5 months apart, so the page would have shown
D's months beside E's days under one heading with no indication.

The backend's own winner guard already refuses to crown a variant with zero
closed outcomes, so the LEADER is safe. The SCORES beside it were not.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COMP = ROOT / "dashboard" / "frontend" / "src" / "components" / "comparison" / "CalendarComparison.tsx"
ROUTER = ROOT / "dashboard" / "backend" / "routers" / "strategies.py"


class TestTheBackendStillSendsTheWindows:

    def test_baselines_are_in_the_leaderboard_payload(self):
        assert '"baselines": baselines' in ROUTER.read_text()

    def test_each_member_is_rebased_on_its_own_date(self):
        src = ROUTER.read_text()
        assert 'getattr(settings, f"variant_{vid}_baseline_date", "")' in src

    def test_a_variant_with_no_closed_outcomes_cannot_win(self):
        """The guard that keeps the LEADER honest while a rebased variant is
        still empty. Pre-existing; pinned here because the epoch makes it live."""
        src = ROUTER.read_text()
        assert "_decide_calendar_winner(scores, outcome_counts)" in src


class TestTheUINowReadsThem:

    def test_the_component_consumes_baselines(self):
        src = COMP.read_text()
        assert "baselines?: Record<string, string>;" in src
        assert "lb.baselines" in src

    def test_there_is_a_caption_helper(self):
        assert "function spanCaption" in COMP.read_text()

    def test_it_warns_when_the_windows_differ(self):
        """The case that matters: a bigger number over a longer window reads as
        a better strategy unless the page says otherwise."""
        src = COMP.read_text()
        assert "different windows" in src and "not like-for-like" in src


class TestTheCaptionLogic:
    """The helper is pure string logic, so it is executed rather than grepped."""

    def _caption(self, baselines: dict) -> str:
        import shutil, subprocess
        node = shutil.which("node")
        if not node:
            pytest.skip("node unavailable")
        src = COMP.read_text()
        body = src[src.index("function spanCaption"):]
        body = body[: body.index("\ninterface ")]
        script = ROOT / "tests" / "_caption_probe.mjs"
        script.write_text(
            body.replace("function spanCaption(baselines: Record<string, string>): string",
                         "function spanCaption(baselines)")
                .replace("const parts = Object.entries(baselines).filter(([, d]) => d);",
                         "const parts = Object.entries(baselines).filter(([, d]) => d);")
            + f"\nconsole.log(JSON.stringify(spanCaption({json.dumps(baselines)})));\n")
        try:
            r = subprocess.run([node, "--no-warnings", str(script)],
                               capture_output=True, text=True, timeout=30)
            assert r.returncode == 0, r.stderr
            return json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            script.unlink(missing_ok=True)

    def test_matching_windows_read_as_a_quiet_since(self):
        out = self._caption({"D": "2026-07-11", "E": "2026-07-11"})
        assert out == "since 2026-07-11"

    def test_the_REAL_situation_warns(self):
        """D's months against E's days — the state shipped on 2026-09-24."""
        out = self._caption({"D": "2026-07-11", "E": "2026-09-24"})
        assert "different windows" in out
        assert "D 2026-07-11" in out and "E 2026-09-24" in out

    def test_no_baselines_produces_nothing(self):
        assert self._caption({}) == ""

    def test_a_blank_baseline_is_ignored_rather_than_captioned(self):
        assert self._caption({"D": "", "E": ""}) == ""


class TestTheTwoSurfacesAgreeOnEsWindow:
    """The bot, the dashboard rebase, and the comparison caption must all name
    the same date, or one of the three is lying about the same variant."""

    def test_all_three_match(self):
        from dashboard.backend.config import Settings
        bot = json.loads(
            (ROOT / "bots" / "hydra" / "config" / "config_variant_e.json").read_text()
        )["strategy"]["metrics_epoch_date"]
        assert Settings().variant_e_baseline_date == bot
