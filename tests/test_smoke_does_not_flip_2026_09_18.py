"""Running the Gate-3 evidence test must not put a variant live.

FOUND 2026-09-18 by pulling on a scheduling question — "the paper smoke expires
in 7 days" — rather than by looking for a bug.

``deploy/broker-paper-smoke.service`` carried:

    ExecStartPost=+/opt/calypso/scripts/flip_a_live.sh

systemd runs an ExecStartPost ONLY when ExecStart exits 0, so a PASSING smoke
flipped variant A to ``dry_run:false`` and restarted it. In June that was the
point — the smoke existed to gate A's go-live.

It has been a landmine since the 2026-07-24 B<->C swap. **B is the live paper
seat now**, and ``LIVE_READINESS_CHECKLIST.md`` Gate 3 tells an operator, in
those words, to collect evidence by running:

    sudo systemctl start broker-paper-smoke

On a pass that would have put **A live alongside B** — two strategies placing
real orders on one account, triggered by a command documented as a test.

``flip_ac_live.sh`` was given a Guard 0 at the swap ("refuse while B holds the
live seat"). ``flip_a_live.sh``, which is the one actually wired to the smoke,
was not. Its guards only checked that the broker was up, that a fresh smoke
sentinel existed, and that A was currently dry-run — every one of which a
passing smoke satisfies.

TWO FIXES, deliberately both:

  * the coupling is removed — proving the order path works and putting a variant
    live are different decisions, and the second has its own runbook;
  * flip_a_live.sh gains the Guard 0 its sibling already had, as the second line.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SERVICE = ROOT / "deploy" / "broker-paper-smoke.service"
FLIP_A = ROOT / "scripts" / "flip_a_live.sh"
FLIP_AC = ROOT / "scripts" / "flip_ac_live.sh"


def _uncommented(path: Path) -> str:
    """Directive lines only. A systemd unit's comments routinely quote the very
    directive being discussed, so a substring search over the raw file matches
    the explanation rather than the setting."""
    return "\n".join(l for l in path.read_text().splitlines()
                     if l.strip() and not l.strip().startswith("#"))


def test_the_smoke_does_not_flip_anything_live():
    """THE DEFECT. A test must not have a side effect that changes what trades."""
    live = _uncommented(SERVICE)
    assert "ExecStartPost" not in live, (
        "broker-paper-smoke.service has an ExecStartPost again — running the "
        "Gate-3 evidence command would have a side effect beyond measuring"
    )
    assert "flip_" not in live, (
        "the smoke unit references a flip script in a live directive"
    )


def test_the_smoke_still_actually_places_an_order():
    """CONTROL. Decoupling must not have neutered the test itself — Gate 3's
    evidence requires a real round trip, not a check-only run."""
    live = _uncommented(SERVICE)
    assert "broker_paper_smoke.py" in live
    assert "--place" in live, (
        "the smoke no longer places an order, so it cannot be Gate-3 evidence"
    )


@pytest.mark.parametrize("script", [FLIP_A, FLIP_AC])
def test_every_flip_script_refuses_while_b_holds_the_live_seat(script):
    """Both scripts put a variant live on the account B already trades. Only
    flip_ac_live.sh had this guard; the one wired to the smoke did not."""
    src = script.read_text()
    assert "config_variant_b.json" in src, (
        f"{script.name} never inspects B's config, so it cannot know whether B "
        f"is live"
    )
    assert re.search(r'b_dry.*=.*"False"|"False".*b_dry', src, re.S), (
        f"{script.name} does not abort on B being live"
    )
    body = src[src.index("config_variant_b.json"):]
    assert "exit 1" in body[:800], (
        f"{script.name} checks B's state but does not abort on it"
    )


def test_the_checklist_command_is_the_one_that_was_dangerous():
    """Pins the premise. If Gate 3 stops naming this unit, the finding above is
    about a command nobody runs — worth knowing either way."""
    checklist = (ROOT / "docs" / "migration" / "LIVE_READINESS_CHECKLIST.md").read_text()
    assert "systemctl start broker-paper-smoke" in checklist, (
        "Gate 3 no longer tells an operator to start broker-paper-smoke — "
        "re-check whether this file still describes a live path"
    )


def test_flip_scripts_are_not_wired_to_any_timer_or_unit():
    """The flip must stay an operator action. Anything that runs it on a
    schedule or as a side effect reintroduces exactly this class of defect."""
    offenders = []
    # Unit FILES only. The first version globbed "*" and flagged deploy/README.md,
    # which merely documents the coupling — a doc describing a landmine is not a
    # landmine.
    units = list((ROOT / "deploy").glob("*.service")) + list((ROOT / "deploy").glob("*.timer"))
    for unit in units:
        if not unit.is_file():
            continue
        live = "\n".join(l for l in unit.read_text().splitlines()
                         if l.strip() and not l.strip().startswith("#"))
        if re.search(r"flip_\w*live\.sh", live):
            offenders.append(unit.name)
    assert not offenders, (
        f"unit(s) invoke a flip script automatically: {offenders} — going live "
        f"is a decision, not a side effect"
    )
