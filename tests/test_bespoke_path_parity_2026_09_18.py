"""What do the hand-rolled entry/exit paths skip that the shared one does?

Variant F was bitten twice by the same root cause in the same method: its
``_initiate_entry`` deliberately bypasses ``HydraStrategy._initiate_entry`` and
re-implements only the parts someone remembered.

    2026-09-15   no entry was ever written to SQLite — F's whole recorded
                 history was stops, so it read as pure loss in every report
    2026-09-18   entries_completed / open_commission / total_credit_received
                 were never updated, so F booked P&L over zero entries and was
                 excluded from every `entries_placed > 0` analysis

Both were found by accident. This file is the systematic version: diff each
bespoke path against the shared one and check what is missing, rather than wait
to trip over the next omission.

WHAT THE SWEEP FOUND (2026-09-18), after discarding the legitimately
strategy-specific differences — a calendar has no `put_only`, a strangle has no
`one_sided_entries`, a net-debit structure has no `total_credit_received`:

  1. F never sets ``entry.is_complete``. Every sibling does. It works ONLY by
     accident: ``active_entries`` falls through to an "any leg has a position
     id?" fallback, which happens to catch F's entries. Monitoring the live
     seat's positions should not rest on a fallback intended for partial fills.

  2. F records an ORDER PLACEMENT FAILURE as a SKIP. `entries_failed` is never
     incremented and the row lands in `skipped_entries` — the counterfactual
     table used to ask "what would this entry have done". A broker failure is
     not a strategic skip, and mixing them corrupts that analysis in both
     directions.

  3. ``_execute_early_close`` (MKT-018) calls ``_close_entry_early`` without the
     dry-run cost correction its two sibling callers perform. Dormant today —
     ``early_close_enabled`` is False on all seven variants and defaults False
     in code — so this is a trap armed for whoever re-enables it, not a live
     bug.

AND A CLEAN NEGATIVE, which is the more valuable half: every OTHER site that
combines ``_close_entry_early`` with ``_book_realized_pnl`` books ``-close_cost``
— a correction, not a second booking. F's double-count was the only one in the
fleet. Brandon's four sites (live on B and C) are correct.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HYDRA = ROOT / "bots" / "hydra"

STRATEGY_FILES = [
    "strategy.py", "ghauri_strategy.py", "strangle_strategy.py",
    "double_calendar_strategy.py", "spy_double_calendar_strategy.py",
    "calendar_strategy_base.py", "brandon/strategy.py",
]


def _functions(path: Path):
    src = path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, node)
            if seg:
                yield node.name, seg


# ── The fleet-wide rule this audit produced ────────────────────────────────

def test_every_early_close_caller_corrects_the_dry_run_booking():
    """THE RULE.

    ``_close_entry_early`` books the side itself, through
    ``_book_early_close_side_pnl``, unconditionally. In dry-run there is no
    simulated fill, so ``side_close_cost`` arrives as 0 and it books the FULL
    credit — as though the position closed for free.

    Every caller must therefore either book ``-close_cost`` itself, or call
    ``_eod_flatten_dry_run_correct``. A caller that does neither overstates the
    day by the entire cost of buying the short back. That is not hypothetical:
    it is the 2026-08-20 finding on variants A and C (A's day flipped from a
    reported +$24.85 to roughly -$40, C overstated by ~$490), and it is half of
    the 2026-09-18 Ghauri defect.
    """
    offenders = []
    for f in STRATEGY_FILES:
        for name, seg in _functions(HYDRA / f):
            if name == "_close_entry_early" or "_close_entry_early(" not in seg:
                continue
            corrects = bool(re.search(r"_book_realized_pnl\(\s*-", seg))
            eod_helper = "_eod_flatten_dry_run_correct" in seg
            if not (corrects or eod_helper):
                offenders.append(f"{f}:{name}")
    assert not offenders, (
        "caller(s) of _close_entry_early with no dry-run cost correction — the "
        "day's P&L is overstated by the full close cost:\n  "
        + "\n  ".join(offenders)
    )


def test_no_caller_books_a_second_full_credit():
    """The inverse error, and the one that actually shipped.

    Booking ``credit - close_cost`` on top of _close_entry_early's own booking
    counts the credit TWICE: 2 x credit - cost. Ghauri stored $195.00 for a
    $127.50 credit closed at a $60.00 mark. A correction is always NEGATIVE.
    """
    offenders = []
    for f in STRATEGY_FILES:
        for name, seg in _functions(HYDRA / f):
            if name == "_close_entry_early" or "_close_entry_early(" not in seg:
                continue
            for arg in re.findall(r"_book_realized_pnl\(\s*([^,)]+)", seg):
                if not arg.strip().startswith("-"):
                    offenders.append(f"{f}:{name} books {arg.strip()!r}")
    assert not offenders, (
        "a caller books a POSITIVE amount after _close_entry_early already "
        "booked the credit — that is a double-count:\n  " + "\n  ".join(offenders)
    )


# ── Entry-path parity ──────────────────────────────────────────────────────

def _initiate_entry(module: str) -> str:
    for name, seg in _functions(HYDRA / module):
        if name == "_initiate_entry" and "daily_state.entries.append(" in seg:
            return seg
    raise AssertionError(f"{module}: no _initiate_entry that appends an entry")


def test_ghauri_marks_its_entry_complete():
    """`active_entries` — which decides what gets MONITORED — branches on
    is_complete, and only falls through to an "any leg has a position id?"
    check for PARTIAL entries. F's entries were being monitored by that
    fallback, which is a safety net for half-filled entries, not the intended
    route for a fully-placed one."""
    seg = _initiate_entry("ghauri_strategy.py")
    assert re.search(r"is_complete\s*=\s*True", seg), (
        "F never marks a successful entry complete; monitoring works only via "
        "the partial-entry fallback in active_entries"
    )


def test_a_placement_failure_is_not_recorded_as_a_skip():
    """`skipped_entries` is the COUNTERFACTUAL table — 'what would this entry
    have done if the credit gate had let it through'. An order that was
    attempted and failed at the broker is not a counterfactual; it is an
    execution incident, and it belongs in entries_failed."""
    seg = _initiate_entry("ghauri_strategy.py")
    fail_branch = re.search(
        r"if not success:(.*?)return f?\"[^\"]*", seg, flags=re.S)
    assert fail_branch, "could not locate F's placement-failure branch"
    body = fail_branch.group(1)
    assert "_record_skipped_entry" not in body, (
        "a broker placement failure is recorded in the skipped-entry "
        "counterfactual table"
    )
    assert "entries_failed" in body or "_record_failed_entry" in body, (
        "a broker placement failure is invisible: entries_failed is never "
        "incremented"
    )


# ── Guard the premise of finding 3 ─────────────────────────────────────────

def test_mkt018_early_close_defaults_off():
    """Finding 3 is dormant only because this default holds. If it ever flips,
    the correction above becomes load-bearing on a live seat."""
    src = (HYDRA / "strategy.py").read_text()
    m = re.search(r'early_close_enabled\s*=\s*bool\(\s*strategy_config\.get\(\s*"early_close_enabled",\s*(\w+)',
                  src)
    assert m, "could not find the early_close_enabled default"
    assert m.group(1) == "False", (
        f"MKT-018 now defaults to {m.group(1)} — _execute_early_close's dry-run "
        f"correction is no longer dormant"
    )


@pytest.mark.parametrize("module", [
    "ghauri_strategy.py", "strangle_strategy.py",
    "double_calendar_strategy.py", "spy_double_calendar_strategy.py",
])
def test_bespoke_entry_paths_persist_their_entry(module):
    """The 2026-09-15 F bug generalised: a bespoke path that never writes the
    entry anywhere leaves a strategy whose only record is its losses.

    D and E write to their own isolated dc_calendar.db via `_dc_recorder`
    rather than the shared recorder, which is why they are checked for EITHER.
    """
    seg = _initiate_entry(module)
    assert ("_record_entry_to_db" in seg or "record_calendar_entry" in seg
            or "_dc_recorder" in seg), (
        f"{module}:_initiate_entry opens a position and records it nowhere"
    )
