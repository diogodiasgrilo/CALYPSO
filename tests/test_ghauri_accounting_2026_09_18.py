"""Variant F's day accounting was wrong in three ways at once.

The 🔴 open finding (§A-bis-2, raised 2026-09-16, undiagnosed until now) was
that F's daily summary books MORE than the trade could possibly earn:

    trade_entries    2026-09-15 e#1  put 7550/7540   total_credit  $127.50
    trade_stops      2026-09-15 e#1  put early_close debit $0.00 -> +$127.50
    daily_summaries  2026-09-15      entries_placed=0  gross $195.00 net $192.70

A short spread cannot earn more than the credit it collected (CLAUDE.md lesson
#14), so $195.00 on a $127.50 credit is impossible. The log gives the real
trade: TP fired at a spread value of $60.00, so the true gross is
127.50 − 60.00 = **$67.50**.

ROOT CAUSE 1 — the P&L is booked TWICE.

``_close_entry_early`` books the side's P&L itself, via
``_book_early_close_side_pnl``, UNCONDITIONALLY. In dry-run there is no
simulated fill, so ``side_close_cost`` arrives as 0 and that helper books the
FULL credit — as if the position closed for free. Brandon has always corrected
for this by booking the NEGATIVE close cost afterwards:

    brandon/strategy.py:1313   self._book_realized_pnl(-close_cost_call, entry)
    brandon/strategy.py:1330   self._book_realized_pnl(-close_cost_put, entry)

Ghauri was written against Brandon's shape but books ``credit - close_cost``
instead of ``-close_cost`` — a second full booking rather than a correction:

    2 × credit − cost  =  2 × 127.50 − 60.00  =  195.00      <- exactly observed

Its docstring claims it is "matching Brandon's own ``if self.dry_run:`` branch,
since the live booking path inside ``_close_entry_early`` doesn't fire in
dry-run". Both halves are false: the path does fire, and the amount differs.

ROOT CAUSE 2/3 — F's bespoke entry path drops bookkeeping.

``GhauriMeanReversionStrategy._initiate_entry`` deliberately bypasses
``HydraStrategy._initiate_entry`` (its own docstring says so) and re-implements
only part of it. An AST sweep of every ``_initiate_entry`` that appends to
``daily_state.entries`` shows F alone missing two things every sibling does:

    strategy.py (A/B/C)                YES entries_completed  YES open_commission
    strangle_strategy.py (G)           YES                    YES
    double_calendar_strategy.py (D)    YES                    YES
    spy_double_calendar_strategy.py(E) YES                    YES
    ghauri_strategy.py (F)             no                     no

Consequences beyond the wrong number:

  * ``entries_placed`` is 0 on every F trading day, and cumulative
    ``total_entries`` stays 0 — so **every analysis keyed on
    ``entries_placed > 0`` excludes F entirely** (``complementarity.py``,
    ``variant_performance.py``'s TRADED-day win rate). F looks like it has
    never traded.
  * The OPEN commission is never charged. Only the close commission lands (from
    ``strategy.py:3709`` inside ``_close_entry_early``), which is why the day
    showed exactly $2.30 = 2 legs × $1.15 rather than $4.60 for the round trip.

True figures for 2026-09-15, all three corrected:

    gross      127.50 − 60.00            =  $67.50   (reported $195.00)
    commission 2×1.15 open + 2×1.15 close =  $4.60   (reported $2.30)
    net                                   =  $62.90  (reported $192.70)

F is dry-run-locked, so no money moved — but its lifetime P&L is built from
these rows, and it is a go-live candidate.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HYDRA = ROOT / "bots" / "hydra"
GHAURI = HYDRA / "ghauri_strategy.py"
BRANDON = HYDRA / "brandon" / "strategy.py"

#: Modules whose `_initiate_entry` opens a real position, and whether the
#: strategy COLLECTS a credit (so `total_credit_received` applies). D and E are
#: net-debit calendars — they legitimately have no credit to accumulate.
ENTRY_MODULES = {
    "strategy.py": True,                    # A/B/C — credit iron condor
    "ghauri_strategy.py": True,             # F     — credit vertical
    "strangle_strategy.py": True,           # G     — credit strangle
    "double_calendar_strategy.py": False,   # D     — net debit
    "spy_double_calendar_strategy.py": False,  # E  — net debit
}


def _strip_py_comments(src: str) -> str:
    """Drop ``#`` comments and docstrings — search code, not prose.

    Not optional. The comment added alongside the fix explains that
    "entries_completed feeds daily_summaries.entries_placed", so a bare
    substring test for ``entries_completed`` matched the COMMENT and stayed
    green when the increment itself was deleted. Mutation testing caught it;
    it is the seventh time this trap has fired in this repo.
    """
    out = []
    for line in src.splitlines():
        stripped = line.split("#", 1)[0]
        out.append(stripped)
    return "\n".join(out)


def _initiate_entry_source(module: str) -> str:
    path = HYDRA / module
    src = path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_initiate_entry":
            seg = ast.get_source_segment(src, node)
            if seg and "daily_state.entries.append(" in seg:
                # Drop the function's own docstring too — it is prose like any
                # comment, and D's runs to several paragraphs.
                body = ast.get_docstring(node)
                if body:
                    seg = seg.replace(body, "")
                return _strip_py_comments(seg)
    raise AssertionError(f"{module} has no _initiate_entry that appends an entry")


# ── The fleet-wide invariant F broke ───────────────────────────────────────

@pytest.mark.parametrize("module", sorted(ENTRY_MODULES))
def test_initiate_entry_counts_the_entry(module):
    """Anything that opens a position must count it.

    `daily_summaries.entries_placed` reads `daily_state.entries_completed`, and
    every traded-day analysis filters on `entries_placed > 0`. A strategy that
    appends an entry without incrementing the counter is invisible to all of
    them while still booking P&L — which is how F accumulated a lifetime P&L
    over, by its own records, zero entries.
    """
    seg = _initiate_entry_source(module)
    assert re.search(r"entries_completed\s*\+=", seg), (
        f"{module}:_initiate_entry appends an entry but never increments "
        f"daily_state.entries_completed"
    )


@pytest.mark.parametrize("module", sorted(ENTRY_MODULES))
def test_initiate_entry_charges_the_opening_commission(module):
    """The close commission is charged by _close_entry_early regardless; only
    the OPEN side depends on the entry path, so a bespoke one silently halves
    the round-trip cost."""
    seg = _initiate_entry_source(module)
    assert re.search(r"open_commission\s*=", seg), (
        f"{module}:_initiate_entry never charges the opening commission — the "
        f"day's net overstates by one side of the round trip"
    )


@pytest.mark.parametrize(
    "module", sorted(m for m, credit in ENTRY_MODULES.items() if credit))
def test_credit_strategies_accumulate_the_credit_received(module):
    """Only for credit-shaped strategies; D and E are net-debit calendars with
    no credit to record, which is why they are excluded rather than exempted."""
    seg = _initiate_entry_source(module)
    assert re.search(r"total_credit_received\s*\+=", seg), (
        f"{module}:_initiate_entry does not accumulate total_credit_received, "
        f"so the cumulative credit-collected metric under-reports"
    )


# ── The double-booking itself, tested behaviourally ────────────────────────

def _ghauri(tmp_path):
    from bots.hydra.ghauri_strategy import GhauriMeanReversionStrategy

    s = GhauriMeanReversionStrategy(MagicMock(), {"strategy": {}}, MagicMock(), dry_run=True)
    s.daily_state.total_realized_pnl = 0.0
    return s


def _entry(credit: float, value: float):
    e = MagicMock()
    e.entry_number = 1
    e.put_spread_credit = credit
    e.call_spread_credit = 0.0
    e.put_spread_value = value
    e.call_spread_value = 0.0
    e.contracts = 1
    e.put_side_expired = False
    e.actual_put_stop_debit = 0
    return e


def test_take_profit_books_the_net_not_a_second_full_credit(tmp_path, monkeypatch):
    """THE DEFECT, reproduced.

    `_close_entry_early` is replaced by a stand-in that does exactly what the
    real one does in dry-run — marks the side closed and books the side P&L
    through the REAL `_book_early_close_side_pnl` with a close cost of 0, which
    books the whole credit. Using the real helper rather than a hardcoded
    number means this test still fails if that helper's behaviour changes.

    Whatever it booked, the TP handler must leave the total at credit − cost.
    """
    s = _ghauri(tmp_path)
    entry = _entry(credit=127.50, value=60.00)

    def fake_close(e, skip_sides=None):
        e.put_side_expired = True
        s._book_early_close_side_pnl(e, "put", e.put_spread_credit, 0.0)
        return 2, 0, []

    monkeypatch.setattr(s, "_close_entry_early", fake_close)
    monkeypatch.setattr(s, "alert_service", MagicMock())

    s._ghauri_close_for_take_profit(entry, "put", MagicMock(profit_captured_pct=0.529, reason="tp"))

    assert s.daily_state.total_realized_pnl == pytest.approx(67.50), (
        f"expected credit − cost = 67.50, got "
        f"{s.daily_state.total_realized_pnl} — 195.00 means the credit was "
        f"booked twice (2 × 127.50 − 60.00)"
    )


def test_take_profit_at_zero_close_cost_still_books_only_the_credit(tmp_path, monkeypatch):
    """Boundary control. A side that genuinely closes for nothing must book the
    credit ONCE — a fix that always subtracted would zero this out."""
    s = _ghauri(tmp_path)
    entry = _entry(credit=127.50, value=0.0)

    def fake_close(e, skip_sides=None):
        e.put_side_expired = True
        s._book_early_close_side_pnl(e, "put", e.put_spread_credit, 0.0)
        return 2, 0, []

    monkeypatch.setattr(s, "_close_entry_early", fake_close)
    monkeypatch.setattr(s, "alert_service", MagicMock())
    s._ghauri_close_for_take_profit(entry, "put", MagicMock(profit_captured_pct=1.0, reason="tp"))
    assert s.daily_state.total_realized_pnl == pytest.approx(127.50)


def test_take_profit_does_not_book_when_the_close_failed(tmp_path, monkeypatch):
    """NO-OP CONTROL. If the legs are still open the handler returns early and
    must book nothing at all."""
    s = _ghauri(tmp_path)
    entry = _entry(credit=127.50, value=60.00)
    monkeypatch.setattr(s, "_close_entry_early", lambda e, skip_sides=None: (0, 2, []))
    monkeypatch.setattr(s, "alert_service", MagicMock())
    s._ghauri_close_for_take_profit(entry, "put", MagicMock(profit_captured_pct=0.5, reason="tp"))
    assert s.daily_state.total_realized_pnl == 0.0


# ── Controls: the siblings this was copied from must be unchanged ──────────

def test_brandon_still_books_only_the_negative_close_cost():
    """Brandon's branch is the CORRECT shape and the reference for the fix. If
    it ever changes to `credit - cost`, B and C acquire the same bug."""
    src = BRANDON.read_text()
    import re
    sites = re.findall(r"_book_realized_pnl\(\s*-close_cost_\w+", src)
    assert len(sites) >= 2, (
        f"expected Brandon's dry-run TP/breach corrections to book a negative "
        f"close cost; found {len(sites)}"
    )
    assert "_book_realized_pnl(credit - close_cost" not in src, (
        "Brandon now books credit − cost on top of _close_entry_early's own "
        "booking — the exact double-count this file exists to prevent"
    )


def test_ghauri_books_the_correction_not_a_second_credit():
    """Source-level companion to the behavioural test above: pins the SHAPE, so
    a refactor that reintroduces `credit - close_cost` fails even if the
    behavioural test is restructured."""
    src = GHAURI.read_text()
    assert "_book_realized_pnl(credit - close_cost" not in src, (
        "Ghauri books credit − close_cost again; _close_entry_early has "
        "already booked the credit"
    )
    assert "_book_realized_pnl(-close_cost" in src, (
        "Ghauri no longer applies the negative-close-cost correction"
    )


def test_strangle_does_not_use_the_early_close_path_at_all():
    """G was checked and is NOT exposed: it never calls _close_entry_early, so
    it has nothing to correct. Recorded so a future reader does not 'fix' it by
    symmetry."""
    src = (HYDRA / "strangle_strategy.py").read_text()
    assert "_close_entry_early" not in src
    assert "_book_realized_pnl" not in src
