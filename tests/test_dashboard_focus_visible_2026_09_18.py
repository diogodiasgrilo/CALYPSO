"""Removing the browser's focus ring obliges you to draw your own (WCAG 2.4.7).

Found 2026-09-18 auditing the login gate, using uiaudit/diag-focus.mjs — which
focuses each control and diffs its computed style against the unfocused state,
rather than grepping for `outline-none`. Both fields on the sign-in form came
back byte-identical focused and unfocused, and a real keyboard Tab landed on the
username field with `outline: none`. A keyboard user could not see which field
they were in, on the first screen of the application.

`outline-none` is legitimate — the app restyles focus rather than accepting the
UA ring — but only when something replaces it. Four places use it:

    StrategyPicker.tsx   focus:border-info               works (probe: #58a6ff)
    History.tsx          focus:border-text-secondary     works (~200ms fade)
    LoginGate.tsx        nothing                         FIXED here
    CommandPalette.tsx   nothing                         allowed, see below

Getting to that list took three corrections to the probe itself, each of which
is worth knowing because they are the failure modes of any focus audit:

  1. It compared raw computed values, so the password field scored "fine"
     because its outline-WIDTH changed 3px -> 1px while outline-STYLE stayed
     `none` — a ring the browser never draws.
  2. It sampled immediately after .focus(), before `transition-colors` had
     advanced, and so reported History's year <select> as broken when it in
     fact fades to a visible border over ~200ms. A false positive.
  3. It sampled the autoFocus'd username field while it was ALREADY focused,
     so before and after matched trivially and it stayed red even after being
     fixed.

This test is the cheap source-level guard that runs in CI on every push; the
browser probe is the thorough one that has to be run deliberately.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "dashboard" / "frontend" / "src"

#: Places allowed to strip the ring without drawing one, with the reason.
#: Anything added here should be justified in the same breath.
ALLOWED = {
    "components/shared/CommandPalette.tsx":
        "borderless transparent input, auto-focused as the only text field in a "
        "modal, with combobox ARIA — the dialog is the focus context and "
        "keyboard movement is through the listbox, which draws its own "
        "selection highlight. A ring here would be noise, not information.",
}


def _strip_comments(src: str) -> str:
    """Search code, not the prose explaining it.

    Required here, not optional: the comment added above ``inputClass`` writes
    ``outline-none`` in backticks, which a literal scanner reads as a template
    literal with no focus style and reports as a violation of the very rule the
    comment documents. That is the FIFTH time this trap has fired in this repo,
    which is why every source-scanning test here now strips first.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _string_literals(src: str) -> list[str]:
    """Every double-quoted or backticked literal, newlines included.

    Class lists in this codebase are written all three ways — a plain attribute
    string, a shared `const inputClass = "..."`, and multi-line template
    literals — so scanning literals rather than `className=` attributes is what
    actually covers them. LoginGate's offender was a bare const, which an
    attribute-only scan would have missed entirely.
    """
    pairs = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"|`([^`\\]*(?:\\.[^`\\]*)*)`',
                       src, flags=re.S)
    return [dq or bt for dq, bt in pairs]


def test_outline_none_always_has_a_replacement():
    """THE RULE. Strip the ring, draw a ring."""
    offenders = []
    for path in sorted(SRC.rglob("*.tsx")) + sorted(SRC.rglob("*.ts")):
        rel = str(path.relative_to(SRC))
        if rel in ALLOWED:
            continue
        for lit in _string_literals(_strip_comments(path.read_text())):
            if "outline-none" not in lit:
                continue
            if re.search(r"focus(-visible)?:", lit):
                continue
            offenders.append(f"{rel}: {' '.join(lit.split())[:100]}")

    assert not offenders, (
        "outline-none with no focus style — a keyboard user cannot see where "
        "they are:\n  " + "\n  ".join(offenders)
    )


def test_the_login_inputs_specifically_draw_focus():
    """The defect that prompted the rule, pinned directly so a refactor that
    moves the class somewhere the scanner misses still fails."""
    src = _strip_comments((SRC / "components" / "auth" / "LoginGate.tsx").read_text())
    m = re.search(r"const inputClass\s*=\s*\n?\s*\"([^\"]+)\"", src)
    assert m, "inputClass literal not found"
    classes = m.group(1)
    assert "outline-none" in classes, "premise changed — re-read this test"
    assert re.search(r"focus(-visible)?:", classes), (
        f"login inputs strip the focus ring and draw nothing: {classes!r}"
    )


def test_allowlist_entries_still_exist_and_are_explained():
    """An allowlist that outlives its files silently weakens the rule."""
    for rel, reason in ALLOWED.items():
        assert (SRC / rel).exists(), f"allowlisted file is gone: {rel}"
        assert len(reason) > 60, f"allowlist entry for {rel} needs a real reason"
        assert "outline-none" in _strip_comments((SRC / rel).read_text()), (
            f"{rel} no longer uses outline-none — drop it from ALLOWED so the "
            f"rule covers it again"
        )


def test_the_scanner_reads_bare_consts_not_just_attributes():
    """Guards the scanner itself. The real offender was a `const inputClass =`
    with no className= anywhere near it; a scan keyed on the attribute would
    have reported a clean run over a broken login form."""
    sample = 'const x =\n  "w-full outline-none transition-colors";\n<div className="a b" />'
    lits = _string_literals(sample)
    assert any("outline-none" in s for s in lits), lits
