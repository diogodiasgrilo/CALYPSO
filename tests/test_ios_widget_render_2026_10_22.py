"""Bridge the iOS widget's Node test suite into pytest, so CI runs it.

The widget is JavaScript for Scriptable, which does not exist off-device, so
its behaviour is exercised against stubbed Scriptable globals by
`dashboard/scriptable/test/widget.test.mjs` (25 assertions, 7 mutation
controls). Without this bridge that suite would only ever run when someone
remembered to, which is the same category of neglect that let the widget
authenticate with a credential retired on 2026-08-03 and go unnoticed until
2026-10-22.

Skips (does not fail) when node is unavailable, so a machine without it can
still run the Python suite.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SUITE = os.path.join(ROOT, "dashboard/scriptable/test/widget.test.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ios_widget_behavioural_suite_passes():
    assert os.path.exists(SUITE), "the widget's Node suite is missing"
    r = subprocess.run(["node", SUITE], capture_output=True, text=True,
                       cwd=ROOT, timeout=120)
    assert r.returncode == 0, (
        f"iOS widget suite failed:\n{r.stdout}\n{r.stderr}")
    # Assert a FLOOR on the assertion count, not just the exit code: a suite
    # gutted down to one trivial test would otherwise still exit 0 and look
    # like a passing gate.
    assert "25 passed, 0 failed" in r.stdout or (
        "0 failed" in r.stdout and
        int(r.stdout.split(" passed")[0].split("\n")[-1]) >= 25), r.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_widget_script_itself_parses():
    """A syntax error would ship a blank widget to the home screen."""
    r = subprocess.run(
        ["node", "--check", os.path.join(ROOT, "dashboard/scriptable/HYDRA_Widget.js")],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
