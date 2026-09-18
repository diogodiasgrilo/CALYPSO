"""Everything a mouse can do, a keyboard must be able to do.

Measured 2026-09-18 with uiaudit/diag-keyboard.mjs, which presses real keys
rather than reading JSX. Four defects, all confirmed against the running app
before anything was changed:

  1. MONTH CALENDAR CELLS are ``<div onClick>``. Clicking a day opens its detail
     view; from a keyboard there was no way to open ANY day.

  2. DAILY SUMMARY TABLE ROWS are ``<tr onClick>`` — the same action, the same
     problem, the other route to it.

  3. COMMAND PALETTE's Escape handler is bound to the search INPUT. Measured:
     Escape closes it with 0 Tabs, and does NOT close it after 3 Tabs, because
     focus has moved to a command button and the input never sees the key.

  4. DAY-DETAIL MODAL does not trap focus. Measured: 5 tab stops inside, then
     Tab walked the entire page behind it — header, strategy picker, mute,
     sign-out, all five nav links, the year picker, Export CSV — every one of
     them visually obscured by the backdrop (WCAG 2.4.3).

NOT defects, checked and dismissed rather than assumed: the DayDetailModal and
CommandPalette BACKDROPS are ``<div onClick={onClose}>``, and the palette's
inner dialog carries ``onClick={e => e.stopPropagation()}``. Backdrops should
not be tab stops, and both modals have real Escape handling, so a keyboard user
has a way out. They are allowlisted below with that reasoning.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "dashboard" / "frontend" / "src"
CAL = SRC / "components" / "history" / "MonthCalendar.tsx"
TABLE = SRC / "components" / "history" / "DailySummaryTable.tsx"
MODAL = SRC / "components" / "history" / "DayDetailModal.tsx"
PALETTE = SRC / "components" / "shared" / "CommandPalette.tsx"

NON_INTERACTIVE = ("div", "span", "td", "tr", "li", "section", "article")

#: (file, reason) for clickable non-interactive elements that are CORRECT.
CLICKABLE_ALLOWED = {
    "components/history/DayDetailModal.tsx":
        "the backdrop: click-outside-to-close. A backdrop must not be a tab "
        "stop, and the modal has a document-level Escape handler, so there is "
        "a keyboard route to the same outcome.",
    "components/shared/CommandPalette.tsx":
        "the backdrop (click-outside-to-close) and the inner dialog's "
        "stopPropagation guard. Neither is an action; the dialog carries "
        "role=dialog + aria-modal and its own key handling.",
}


def _strip_comments(src: str) -> str:
    """Search code, not the prose explaining it."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _opening_tags(src: str, names=NON_INTERACTIVE) -> list[tuple[str, int, str]]:
    """(tagName, line, full opening tag) for each element in ``names``.

    Scans to the closing ``>`` while tracking brace depth, because JSX handlers
    contain both braces and ``=>`` arrows; a regex ending at the first ``>``
    truncates the tag before its attributes. That exact mistake made an earlier
    test in this repo report compliant elements as violations.
    """
    out = []
    for m in re.finditer(rf"<({'|'.join(names)})\b", src):
        i, depth = m.end(), 0
        while i < len(src):
            ch = src[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == ">" and depth == 0 and src[i - 1] != "=":
                break
            i += 1
        out.append((m.group(1), src[: m.start()].count("\n") + 1, src[m.start():i]))
    return out


def test_clickable_non_interactive_elements_are_keyboard_reachable():
    """THE RULE. An onClick on a div/tr is invisible to a keyboard unless it is
    also given a role, a tab stop, and a key handler."""
    offenders = []
    for path in sorted(SRC.rglob("*.tsx")):
        rel = str(path.relative_to(SRC))
        if rel in CLICKABLE_ALLOWED:
            continue
        for tag_name, line, tag in _opening_tags(path.read_text()):
            if "onClick" not in tag:
                continue
            missing = [
                n for n, ok in (
                    ("role", re.search(r'role="(button|option|tab|link)"', tag) is not None),
                    ("tabIndex", "tabIndex" in tag),
                    ("onKeyDown", re.search(r"onKey(Down|Press|Up)", tag) is not None),
                ) if not ok
            ]
            if missing:
                offenders.append(f"{rel}:{line} <{tag_name}> missing {'+'.join(missing)}")
    assert not offenders, (
        "mouse-only action(s) — unreachable from a keyboard:\n  "
        + "\n  ".join(offenders)
    )


def test_calendar_cells_activate_on_enter_and_space():
    """A role=button element must respond to BOTH keys; Space alone scrolls the
    page and Enter alone breaks the platform convention."""
    src = CAL.read_text()
    cell = next(t for _, _, t in _opening_tags(src) if "onDayClick" in t)
    assert 'role="button"' in cell and "tabIndex" in cell

    # Comments stripped first. The handler's own comment explains "Space alone
    # scrolls the page", and an unstripped search for the word "Space" matched
    # THAT — so deleting the actual `e.key === " "` check left this test green.
    # Caught by mutation testing; sixth time this trap has fired in this repo.
    handler = _strip_comments(src[src.index("onKeyDown"):][:400])
    assert re.search(r'e\.key\s*===\s*"Enter"', handler), (
        f"calendar cell does not activate on Enter: {handler[:140]!r}"
    )
    assert re.search(r'e\.key\s*===\s*(" "|\' \')', handler), (
        f"calendar cell does not activate on Space — role=\"button\" must "
        f"respond to both: {handler[:140]!r}"
    )


def test_summary_table_rows_are_reachable():
    src = TABLE.read_text()
    rows = [t for name, _, t in _opening_tags(src) if name == "tr" and "onClick" in t]
    assert rows, "no clickable <tr> found — has the table been restructured?"
    for t in rows:
        assert 'role="button"' in t, t[:120]
        assert "tabIndex" in t, t[:120]
        assert re.search(r"onKey(Down|Press|Up)", t), t[:120]


def test_day_detail_modal_traps_focus_and_announces_itself():
    """Measured before the fix: Tab left the modal after 5 stops and walked the
    whole obscured page behind it."""
    src = MODAL.read_text()
    assert 'role="dialog"' in src, "modal is not announced as a dialog"
    assert 'aria-modal="true"' in src, "modal does not mark the rest of the page inert"
    # Either comparison direction: the guard is idiomatically written as an
    # early return (`if (e.key !== "Tab") return;`), which the first version of
    # this assertion rejected even though the handler was present and correct.
    assert re.search(r'e\.key\s*[!=]==\s*"Tab"', src), (
        "no Tab handling — nothing keeps focus inside the modal"
    )
    # BOTH directions must actively redirect focus. Asserting only that the
    # substring "shiftKey" appears is too weak: it occurs twice, so neutering
    # the forward branch alone left this test green under mutation.
    body = src[src.index("function trap"):]
    body = body[: body.index("\n    }")]
    fwd = re.search(r"!e\.shiftKey[^}]*?\.focus\(\)", body, flags=re.S)
    back = re.search(r"(?<!!)e\.shiftKey[^}]*?\.focus\(\)", body, flags=re.S)
    assert back, "Shift+Tab does not wrap focus — it escapes backwards out of the modal"
    assert fwd, "Tab does not wrap focus — it escapes forwards out of the modal"


def test_day_detail_modal_restores_focus_on_close():
    """Closing a modal must put focus back where it came from, or a keyboard
    user is dropped at the top of the document."""
    src = MODAL.read_text()
    assert re.search(r"(previouslyFocused|previousFocus|restoreFocus)", src), (
        "nothing captures the element that had focus before the modal opened"
    )


def test_command_palette_restores_focus_on_close():
    """Measured: focus sat on the strategy picker before Ctrl+K and on <body>
    after Escape. Both modals must hand focus back, not drop it."""
    src = PALETTE.read_text()
    assert re.search(r"previouslyFocused", src), (
        "nothing captures the element focused before the palette opened"
    )
    assert "isConnected" in src, (
        "focus is restored without checking the element still exists — a "
        "command that navigates away removes it, and re-focusing a detached "
        "node silently does nothing while fighting the route change"
    )


def test_command_palette_escape_works_from_anywhere_inside():
    """Escape was bound to the search input, so it stopped working as soon as
    Tab moved focus to a command button. Measured: closes after 0 Tabs, does
    not close after 3."""
    src = PALETTE.read_text()
    dialog = next(t for _, _, t in _opening_tags(src) if 'role="dialog"' in t)
    assert re.search(r"onKeyDown", dialog), (
        "the dialog container has no key handler, so Escape only fires while "
        "the search input holds focus"
    )


def test_palette_arrow_and_enter_stay_on_the_input():
    """Deliberate: the dialog-level handler must take Escape ONLY. Moving the
    arrow/Enter combobox keys up there too would hijack Enter from whichever
    command button actually has focus, activating the highlighted row instead
    of the focused one."""
    src = PALETTE.read_text()
    dialog = next(t for _, _, t in _opening_tags(src) if 'role="dialog"' in t)
    m = re.search(r"onKeyDown=\{([A-Za-z0-9_]+)\}", dialog)
    assert m, f"expected a named handler on the dialog: {dialog[:140]}"
    name = m.group(1)
    body = src[src.index(f"const {name}"):]
    body = body[: body.index("\n  );") if "\n  );" in body else min(len(body), 700)]
    assert "Escape" in body, f"{name} does not handle Escape"
    assert "ArrowDown" not in body, (
        f"{name} also handles ArrowDown — that belongs to the input's combobox "
        f"behaviour, not to the dialog"
    )


def test_allowlist_entries_are_still_backdrops():
    """An allowlisted file that grows a real clickable div would silently keep
    its exemption."""
    for rel, reason in CLICKABLE_ALLOWED.items():
        path = SRC / rel
        assert path.exists(), f"allowlisted file missing: {rel}"
        assert len(reason) > 80, f"allowlist entry for {rel} needs a real reason"
        clickable = [t for _, _, t in _opening_tags(path.read_text()) if "onClick" in t]
        assert clickable, f"{rel} no longer has clickable divs — drop it from the allowlist"
        for t in clickable:
            assert ("onClose" in t or "stopPropagation" in t), (
                f"{rel} has a clickable element that is neither a backdrop nor a "
                f"stopPropagation guard, and the whole file is exempt:\n  {t[:160]}"
            )
