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


# ─────────────────────────────────────────────────────────────────────────────
# Test isolation — what CI's first real failure exposed
# ─────────────────────────────────────────────────────────────────────────────

CONFTEST = ROOT / "tests" / "conftest.py"


def test_shared_gex_cache_is_isolated_per_test():
    """gex_shared_cache defaults to /opt/calypso/data/shared, which is the LIVE
    cache on the VM and a creatable (therefore polluting) path on any Linux
    runner. On macOS it simply fails to create and the cache silently disables —
    which is why the suite was green locally and red in CI.

    Without this fixture, running the suite on the VM would read and write the
    production cache that variants B and C share at entry time."""
    assert CONFTEST.exists(), "no conftest — the shared GEX cache is unisolated"
    src = CONFTEST.read_text()
    assert "autouse=True" in src
    assert "CALYPSO_GEX_CACHE_DIR" in src
    assert "tmp_path_factory" in src, (
        "isolation must use a temp directory, not a fixed path shared between "
        "tests."
    )


def test_ci_installs_the_dev_requirements():
    """CI's very first run failed with 'No module named pytest': it installed
    requirements.txt, which deliberately leaves the test tooling commented out
    because the trading VM must not carry it."""
    live = [l for l in CI.read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]
    assert any("requirements-dev.txt" in l for l in live)
    dev = (ROOT / "requirements-dev.txt")
    assert dev.exists()
    body = dev.read_text()
    assert "-r requirements.txt" in body, (
        "the dev file must LAYER on the production pins, not duplicate them — "
        "duplicated versions drift."
    )
    assert "pytest==" in body and "httpx==" in body


# ─────────────────────────────────────────────────────────────────────────────
# The suite must be hermetic — no live network probes
# ─────────────────────────────────────────────────────────────────────────────

def test_gcp_probe_is_patched_everywhere_during_a_test():
    """secret_manager.is_running_on_gcp() falls through to a real HTTP GET
    against metadata.google.internal with a 1s timeout. Three test files
    construct an AlertService without patching it, so the suite was making live
    network calls whose latency depends on how the host resolves a name that
    does not exist — the leading suspect for two alert tests that failed on CI
    and then passed on a re-run with no code change."""
    import importlib

    from tests.conftest import _GCP_PROBE_SITES

    for name in _GCP_PROBE_SITES:
        mod = importlib.import_module(name)
        if not hasattr(mod, "is_running_on_gcp"):
            continue
        assert mod.is_running_on_gcp() is False, (
            f"{name}.is_running_on_gcp is not isolated — this test run can "
            f"make a live network call."
        )


def test_every_binding_site_is_listed():
    """`_GCP_PROBE_SITES` is a hand-maintained list, so a NEW module that does
    `from shared.secret_manager import is_running_on_gcp` would silently escape
    the isolation. Find them by source, not by memory."""
    import re

    from tests.conftest import _GCP_PROBE_SITES

    listed = set(_GCP_PROBE_SITES)
    pattern = re.compile(r"from\s+(?:shared\.secret_manager|\.secret_manager)\s+import[^\n]*is_running_on_gcp")
    missing = []
    for d in ("shared", "bots", "dashboard"):
        for p in (ROOT / d).rglob("*.py"):
            if not pattern.search(p.read_text(errors="ignore")):
                continue
            mod = str(p.relative_to(ROOT)).removesuffix(".py").replace("/", ".")
            mod = mod.removesuffix(".__init__")
            if mod not in listed:
                missing.append(mod)
    assert not missing, (
        f"module(s) bind is_running_on_gcp but are not isolated in conftest: "
        f"{sorted(missing)} — add them to _GCP_PROBE_SITES."
    )


# ── Added 2026-09-18 with the accessibility probes ─────────────────────────

def test_ci_workflow_is_valid_yaml():
    """Until PyYAML was added as a dev dependency, NOTHING checked this.

    A malformed workflow is the one failure CI cannot report on itself: the run
    never starts, so there is no red check to notice — you find out because the
    gate you were relying on quietly stopped running.
    """
    import yaml

    data = yaml.safe_load(CI.read_text())
    assert isinstance(data, dict) and "jobs" in data
    assert set(data["jobs"]) >= {"tests", "frontend", "visual"}
    for job_name, job in data["jobs"].items():
        assert job.get("steps"), f"job {job_name} has no steps"


@pytest.mark.parametrize("probe", ["diag-contrast", "diag-focus", "diag-keyboard", "diag-login"])
def test_ci_runs_the_accessibility_probes(probe):
    """The unit suite pins these as SOURCE rules — cheap, and blind to anything
    that only shows up once the page is painted. Each of these four caught
    something the source rules could not have, so they run on every push."""
    import yaml

    data = yaml.safe_load(CI.read_text())
    steps = data["jobs"]["visual"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert probe in runs, f"{probe}.mjs is not run by CI"


def test_accessibility_probes_exist_and_exit_nonzero_on_findings():
    """A probe that always exits 0 is a green tick over an unread report."""
    uiaudit = ROOT / "dashboard" / "frontend" / "uiaudit"
    for probe in ("diag-contrast", "diag-focus", "diag-keyboard", "diag-login"):
        path = uiaudit / f"{probe}.mjs"
        assert path.exists(), f"{probe}.mjs is wired into CI but missing"
        src = path.read_text()
        # Must exit on something OTHER than a literal 0. The first version of
        # this assertion was `process\.exit\(\s*\w+`, which happily matched
        # `process.exit(0)` — `0` is a word character — so a probe hard-wired
        # to always succeed passed the test that exists to prevent exactly
        # that. Caught by mutation testing.
        assert re.search(r"process\.exit\(\s*(?!0\s*\))", src), (
            f"{probe}.mjs exits 0 unconditionally, so CI shows a green tick "
            f"over an unread report"
        )
