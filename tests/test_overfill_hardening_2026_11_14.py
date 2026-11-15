"""Three fixes for the 2026-11-13 over-fill (+2 untracked long calls).

THE INCIDENT, from `bl`'s log, entry #5 / Call 7595 / conid 935332834:

    14:16:22  Cancelling working order ...522 (5/7 filled)   <- rung 1, honest
    14:16:33  Attempt 2 @ $0.55                             (sized for 2)
    14:17:08  Cancelling working order ...523 (0/2 filled)   <- LIED; it filled 2
    14:17:12  Attempt 3 @ $0.70                             (sized for 2 AGAIN)
    14:17:14  "Filled Call 7595.0 7/7"        broker actually held NINE
    14:32:02  POS-003: expected 7, broker shows 9

MEASURED BEFORE FIXING (every retained log, 2026-11-14):
  * 266 of 332 rung cancels (80%) read ZERO and so skipped the terminal wait.
  * 7 distinct over-fills, +29 untracked contracts: +7, +6, +6, +5, +2, +2, +1.
  * Cost of removing the scope: strict path 6.0s median / 9.0s p90 from cancel
    to next rung vs 3.0s / 6.0s for the fast path => ~+3s per cancelled rung.
  * 2026-11-12 is the CONTROL for fix 2: zero orphan legs, per-leg sum matched
    IBKR to one cent (-1066.68 vs -1066.69), and our gross was STILL $123.31
    adrift — so "orphans are the drift" was false.

FIX 1 (prevent)  every cancelled rung waits for terminal, not just partials.
FIX 2 (record)   price the surplus into a day-scoped QTY-LEDGER; POS-003 is the
                 only check that sees it, and only intraday (post-expiry every
                 leg reads q=0.0).
FIX 3 (observe)  a deferred A2 decline says UNRESOLVED instead of repeating the
                 first pass's line. Deliberately NOT a timer change: n=1.
"""
import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bots.hydra.base_strategy import MEICStrategy
from bots.hydra.strategy import HydraStrategy


# ───────────────────────────── FIX 1: the scoping ─────────────────────────────

def _rung_cancel_source():
    """The block that decides whether a cancelled rung waits for terminal."""
    src = open(os.path.join(ROOT, "bots/hydra/base_strategy.py")).read()
    i = src.index("ORDER-010-SETTLE: order")
    return src[max(0, i - 4000):i]


def test_the_wait_is_scoped_to_SIZING_A_REMAINDER_not_to_this_rungs_fill():
    """The corrected scope.

    ⚠️ My first attempt removed the scoping ENTIRELY. That was wrong twice:
      * it added the full ORDER_SETTLE_TIMEOUT_S to 255 full-leg rungs that
        cannot over-fill (a cancelled order rarely reaches terminal inside the
        budget, so each wait costs the whole 6s), lengthening the window in
        which entry placement BLOCKS the stop monitor — against a measured 54s
        stop warning time; and
      * on a terse status read it aborted legs, which is the exact cost the
        original comment named, and it broke `test_order010_settle`'s pinned
        "unsettled is treated as a FAILED CANCEL".

    The hazard is sizing a REMAINDER, so the test is `filled_so_far > 0` — do
    we already hold a partial the next rung will complete — not `snapshot > 0`,
    did THIS rung fill. Friday differed in exactly that gap: snapshot 0,
    filled_so_far 5.
    """
    seg = _rung_cancel_source()
    assert "if snapshot > 0 or filled_so_far > 0:" in seg, (
        "the wait must cover a zero-read rung that is completing a partial")


def test_a_FULL_leg_rung_deliberately_keeps_the_cheap_path():
    """Not an oversight — a cost/hazard trade, stated so it is not 'tidied' away.

    Asserts the REASON survives, because the failure mode here is a future
    reader seeing an odd-looking condition and "simplifying" it in either
    direction: to `snapshot > 0` (reinstating the 2026-11-13 over-fill) or to
    no scope at all (255 extra full-budget waits inside the window that blocks
    the stop monitor).
    """
    seg = _rung_cancel_source()
    assert "no remainder to mis-size" in seg
    assert "abort the leg" in seg, "the terse-read cost must stay stated"


# an honest zero-fill must still be trusted, or we lose entries
class _Stub:
    ORDER_SETTLE_TIMEOUT_S = 0.3
    ORDER_SETTLE_POLL_S = 0.01
    _extract_filled_quantity = staticmethod(MEICStrategy._extract_filled_quantity)
    _await_terminal_fill = MEICStrategy._await_terminal_fill

    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.reads = 0

    def _get_order_status(self, order_id):
        self.reads += 1
        return self._statuses.pop(0) if self._statuses else (self._statuses or [{}]) and {}


def test_an_HONEST_zero_fill_is_still_settled_so_entries_are_not_lost():
    """The cost guard: a rung that truly filled nothing must not fail closed.

    stable = cancel_confirmed and saw_any and not moved — exactly this case.
    If this breaks, fix 1 starts costing entries, which is what the original
    scoping was (wrongly) protecting against.
    """
    s = _Stub([{"filled_quantity": 0}] * 50)
    filled, settled = MEICStrategy._await_terminal_fill(
        s, "oid", 0, cancel_confirmed=True)
    assert (filled, settled) == (0, True)


def test_a_zero_read_that_LATER_REVEALS_a_fill_is_caught():
    """THE FRIDAY CASE: rung reads 0, then the truth (2) surfaces."""
    s = _Stub([{"filled_quantity": 0},
               {"filled_quantity": 0},
               {"filled_quantity": 2, "status": "Cancelled"}])
    filled, settled = MEICStrategy._await_terminal_fill(
        s, "oid", 0, cancel_confirmed=True)
    assert filled == 2, "the late fill must be seen, not the stale 0"
    assert settled is True


def test_a_zero_read_that_MOVES_without_terminal_fails_CLOSED():
    """Moved but never terminal => we cannot say. Must not size a remainder."""
    s = _Stub([{"filled_quantity": 0}, {"filled_quantity": 2}] * 40)
    filled, settled = MEICStrategy._await_terminal_fill(
        s, "oid", 0, cancel_confirmed=True)
    assert filled == 2
    assert settled is False, "a moving count with no terminal state is unknown"


def test_the_monotonic_clamp_still_holds_on_this_path():
    """Monday's fix must survive: a DROP is an artefact, never information."""
    s = _Stub([{"filled_quantity": 5}, {"filled_quantity": 0},
               {"filled_quantity": 0, "status": "Cancelled"}])
    filled, _ = MEICStrategy._await_terminal_fill(
        s, "oid", 0, cancel_confirmed=True)
    assert filled == 5, "a 0 after a 5 must not win"


# ───────────────────────────── FIX 2: the ledger ──────────────────────────────

class _Recorder:
    BOT_NAME = "TEST"
    contracts_per_entry = 7
    _record_qty_mismatch = HydraStrategy._record_qty_mismatch


class _Now:
    @staticmethod
    def strftime(f):
        return "2026-11-13 14:32:02"


def _pos(conid, qty, avg):
    return {"instrument_id": conid, "quantity": qty,
            "raw": {"avgPrice": avg, "conid": conid}}


def test_the_surplus_is_recorded_and_PRICED():
    r = _Recorder()
    HydraStrategy._record_qty_mismatch(
        r, {935332834: (7, 9)}, [_pos(935332834, 9, 0.634125)], _Now())
    e = r._qty_mismatch_ledger["935332834"]
    assert (e["expected"], e["actual"], e["excess"]) == (7, 9, 2)
    assert e["est_unbooked"] == pytest.approx(126.83, abs=0.01), (
        "2c x $0.634125 x 100 = the premium that sat outside the books")
    assert e["first_seen"] == "2026-11-13 14:32:02"


class _Later:
    @staticmethod
    def strftime(f):
        return "2026-11-13 15:32:23"


def test_the_FIRST_sighting_WINS_against_a_later_differing_read():
    """POS-003 re-runs hourly; the first sighting must survive a later one.

    ⚠️ The first version looped the SAME reading 5x and asserted len == 1 —
    which is true with or without the guard, because the ledger is dict-keyed
    and a repeat just overwrites the same key. It PASSED against the removed
    guard. Caught by its own mutation control.

    The real invariant is attribution: `first_seen` and the original excess
    must not be rewritten by a later, different reading (on 2026-11-13 POS-003
    saw this conid at 14:32 and again at 15:32).
    """
    r = _Recorder()
    HydraStrategy._record_qty_mismatch(
        r, {935332834: (7, 9)}, [_pos(935332834, 9, 0.634125)], _Now())
    # a LATER cycle reads it differently
    HydraStrategy._record_qty_mismatch(
        r, {935332834: (7, 14)}, [_pos(935332834, 14, 9.99)], _Later())
    e = r._qty_mismatch_ledger["935332834"]
    assert len(r._qty_mismatch_ledger) == 1
    assert e["first_seen"] == "2026-11-13 14:32:02", "first sighting was overwritten"
    assert e["actual"] == 9 and e["excess"] == 2, "a later read replaced the original"
    assert e["est_unbooked"] == pytest.approx(126.83, abs=0.01)


def test_it_is_callable_the_way_PRODUCTION_calls_it():
    """BOUND, through an instance — not as an unbound function.

    ⚠️ This is the control that was missing. The method was first inserted
    directly above `_actual_position_quantities`, which carries a
    `@staticmethod` — so the insertion landed BETWEEN the decorator and its
    def. `_record_qty_mismatch` silently became a staticmethod (its `self`
    turned into the first positional arg) and `_actual_position_quantities`
    lost its decorator, breaking SEVEN settlement tests with
    "takes 1 positional argument but 2 were given".
    //
    Every test here still passed, because they all called
    `HydraStrategy._record_qty_mismatch(r, ...)` UNBOUND, which works either
    way. Calling through an instance is what distinguishes them.
    """
    inst = HydraStrategy.__new__(HydraStrategy)
    inst._record_qty_mismatch({7: (7, 9)}, [_pos(7, 9, 1.25)], _Now())
    assert inst._qty_mismatch_ledger["7"]["est_unbooked"] == pytest.approx(250.0)


def test_the_neighbouring_staticmethod_kept_its_decorator():
    """The other half of the same slip."""
    assert isinstance(
        HydraStrategy.__dict__["_actual_position_quantities"], staticmethod), (
        "_actual_position_quantities lost @staticmethod — an insertion landed "
        "between the decorator and its def")
    assert not isinstance(
        HydraStrategy.__dict__["_record_qty_mismatch"], staticmethod), (
        "_record_qty_mismatch stole a @staticmethod it does not want")


def test_a_missing_price_records_the_quantity_rather_than_guessing():
    r = _Recorder()
    HydraStrategy._record_qty_mismatch(
        r, {1: (7, 9)}, [{"instrument_id": 1, "quantity": 9, "raw": {}}], _Now())
    e = r._qty_mismatch_ledger["1"]
    assert e["excess"] == 2 and e["est_unbooked"] is None


def test_an_UNDER_fill_is_recorded_too_with_a_negative_excess():
    r = _Recorder()
    HydraStrategy._record_qty_mismatch(
        r, {1: (7, 5)}, [_pos(1, 5, 1.00)], _Now())
    assert r._qty_mismatch_ledger["1"]["excess"] == -2


def test_it_never_raises_into_reconciliation():
    r = _Recorder()
    HydraStrategy._record_qty_mismatch(r, {1: ("junk", None)}, None, _Now())
    HydraStrategy._record_qty_mismatch(r, None, None, _Now())   # must not raise


def test_the_ledger_is_PERSISTED_and_CLEARED_in_the_right_functions():
    """AST, not substring: the 2026-11-09 banner bug was a field in the wrong dict."""
    src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
    tree = ast.parse(src)
    for fn, needle in (("_save_state_to_disk", "qty_mismatch_ledger"),
                       ("_reset_for_new_day", "_qty_mismatch_ledger")):
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == fn)
        seg = ast.get_source_segment(src, node)
        assert needle in seg, f"{fn} does not touch the ledger"


def test_over_and_under_fills_are_NOT_netted_into_one_signed_number():
    """Found in my own after-audit, not by a failing test.

    `est_unbooked` is a MAGNITUDE (abs(excess) x avgPrice x 100). The first
    version summed it and printed `-abs(...)`, asserting a cost — but an
    OVER-fill (premium paid, never booked) and an UNDER-fill (booked, not
    held) pull opposite ways. Netting them into one signed figure states a
    direction the data cannot support, in a line whose whole job is to inform
    a P&L judgement.
    """
    src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
    assert "-abs(qty_unbooked)" not in src, "the unsigned total is back"
    assert "qty_mismatch_over_premium" in src
    assert "qty_mismatch_under_premium" in src
    assert "NOT netted" in src


def test_the_DECOMPOSED_line_no_longer_claims_orphans_are_THE_drift():
    """2026-11-12: zero orphans, per-leg matched IBKR to a cent, $123 still adrift."""
    src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
    assert "see them. This is the drift." not in src, (
        "the overclaim is back — it sent a real investigation down a blind alley")
    assert "RESIDUAL" in src and "do not attribute" in src.lower()


def test_the_reconcile_reports_the_qty_ledger_separately_from_orphans():
    src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
    assert "QTY-MISMATCH" in src
    assert "STRIKE membership only" in src, (
        "the orphan split's blind spot must be stated where it is relied on")


# ──────────────────────────── FIX 3: the handoff ──────────────────────────────

def test_a_deferred_decline_is_logged_as_UNRESOLVED_not_as_fine():
    src = open(os.path.join(ROOT, "bots/hydra/base_strategy.py")).read()
    node = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == "_correct_over_fill")
    seg = ast.get_source_segment(src, node)
    assert "A2-UNRESOLVED" in seg
    assert "Do not read this as 'fine'." in seg
    # and it must be the allow_defer=False branch, not the deferring one
    i = seg.index("if allow_defer:")
    assert "A2-UNRESOLVED" in seg[i:], "must sit in the else of the defer check"


def test_the_120s_window_was_deliberately_NOT_tuned():
    """Guard against a future 'obvious' fix that overfits one observation."""
    src = open(os.path.join(ROOT, "bots/hydra/base_strategy.py")).read()
    assert "DELIBERATELY NOT raising the 120s settle window" in src
    assert "n=1" in src or "SINGLE observation" in src
