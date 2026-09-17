"""CI must actually run the gates, and must not need secrets.

Until 2026-09-17 this repository had NO continuous integration. The suite, the
typecheck, the linter and the 42-surface visual audit were run by hand when
someone remembered — which is precisely how the dashboard accumulated months of
silent drift before it was shown to investors.

These checks are deliberately about the WORKFLOW, not the code: a gate that
silently stops running is indistinguishable from no gate.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CI = ROOT / ".github" / "workflows" / "ci.yml"
BASELINE = ROOT / "dashboard" / "frontend" / ".eslint-baseline.json"


def test_ci_workflow_exists():
    assert CI.exists(), "no CI workflow — every gate is manual again"


@pytest.mark.parametrize("gate", [
    "pytest tests/",                       # the Python suite
    "tsc --noEmit",                        # frontend typecheck
    "check_eslint_baseline",               # the lint ratchet
    "make_synthetic_fixtures",             # secret-free fixtures
    "node audit.mjs",                      # the 42-surface visual audit
    "node viewports.mjs",                  # the responsive sweep
])
def test_ci_runs_every_gate(gate):
    """Checked against UNCOMMENTED lines only. A bare substring search also
    matches `# node audit.mjs`, so commenting a gate out would pass — which a
    mutant proved before this was tightened."""
    live = [
        l for l in CI.read_text().splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    assert any(gate in l for l in live), (
        f"CI no longer runs `{gate}` (or it is commented out) — that gate is "
        f"back to being manual."
    )


def test_ci_fails_on_horizontal_overflow():
    """Mobile overflow is a build failure, not a warning: the dashboard is
    PWA-installable and ships an iOS widget, so a phone is a supported
    surface."""
    src = CI.read_text()
    assert "OVERFLOW" in src and "exit 1" in src


def test_ci_needs_no_secrets():
    """The visual audit renders against synthetic fixtures, so CI never touches
    the VM and never handles real positions or P&L. A workflow that starts
    needing credentials is a workflow that will be disabled."""
    src = CI.read_text()
    assert "secrets." not in src, "CI now references a secret — it should not need one"
    assert "gcloud" not in src, "CI now shells out to gcloud — it should not need VM access"


def test_lint_baseline_exists_and_is_bounded():
    """The ratchet only tightens. If the baseline grows, someone deferred new
    debt without saying so."""
    import json

    assert BASELINE.exists(), "no eslint baseline — the ratchet cannot work"
    known = json.loads(BASELINE.read_text())["errors"]
    assert len(known) <= 20, (
        f"{len(known)} baselined lint errors — the ratchet is supposed to "
        f"tighten, not absorb new debt."
    )
    # Every entry is "<path>::<rule>" so a diff names the file AND the rule.
    for e in known:
        assert "::" in e, f"malformed baseline entry: {e}"


def test_synthetic_fixture_generator_is_importable():
    """It runs in CI before the frontend build; an import error there fails the
    job with a stack trace rather than a missing-fixture mystery."""
    import importlib

    m = importlib.import_module("scripts.make_synthetic_fixtures")
    assert hasattr(m, "main")
