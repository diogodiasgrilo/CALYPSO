"""A3 — never ask to close MORE than the broker actually holds.

Root cause of BOTH `EMERGENCY_CLOSE_FAILED` incidents of the IBKR era
(2026-09-24 and 2026-10-08, five attempts each — the only two occurrences).
IBKR answers an over-sized close with a confirmation prompt:

    "The closing order quantity is greater than your current position.
     Are you sure you want to submit this order?"

which has no entry in `DEFAULT_ORDER_ANSWERS`, so ibind raises
`ValueError: No answer found for question` and **the order is never submitted**.
`close_leg_executions` shows every attempt died in 1.7-5.3s — all of them
marketable, limit above ask — against 30-37s for a real fill on the same conid
minutes earlier. The caller read that as "did not fill", and on 10-08 the stop
then sold the protective long, leaving the live seat naked (CRITICAL #7b).

The prompt was CORRECT: we were closing more than we held. Entries #2 and #6
had IDENTICAL 7735/7730 put spreads (IBKR merges at the conid) and entry #6's
open had partly failed at its short-put leg.

Answering the prompt "yes" would be WRONG — it would over-close a flat position
into a LONG. Clamping to the broker's real quantity is the fix.
"""

import sys
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.strategy import HydraStrategy  # noqa: E402

CONID = 926792465


def _strat(broker_qty: Optional[int], *, close_ok=True):
    """`broker_qty` is the NET at the conid: negative = short, positive = long,
    None = the strict read failed (clamp must then be skipped)."""
    s = HydraStrategy.__new__(HydraStrategy)
    s.contracts_per_entry = 7
    s.dry_run = False
    s.placed = []          # (side, quantity) actually sent to the broker

    s._read_open_positions = lambda strict=False: ["<positions>"]

    def net_qty(uic, positions):
        if broker_qty is None:
            raise RuntimeError("strict position read failed")
        return broker_qty
    s._net_qty_at_conid = net_qty

    # the loop's own "already gone?" check — report still-open so we reach the
    # placement step (qty 0 is owned by that check, not by the clamp)
    s._position_is_open = lambda *a, **k: True

    # The loop calls _place_marketable_close and tests res["filled"] — stub THAT
    # seam, not _close_leg_order underneath it, or every test silently retries
    # five times with real 2s sleeps and takes two minutes.
    def fake_close(uic, side, quantity, attempt_num=1, **kw):
        s.placed.append((side, int(quantity)))
        return {"filled": close_ok, "filled_quantity": int(quantity) if close_ok else 0,
                "fill_price": 11.0 if close_ok else None, "order_id": "oid"}
    s._place_marketable_close = fake_close
    s._correct_over_fill = lambda *a, **k: None
    s._record_close_leg_execution = lambda *a, **k: None
    s.close_cost_context = lambda *a, **k: None
    return s


def _run(s, leg_name="short_put", contracts=7):
    try:
        s._close_position_with_retry_ib(
            None, leg_name, uic=CONID, entry_number=6, contracts=contracts)
    except Exception:
        pass
    return s.placed


class TestTheCloseIsClampedToWhatTheBrokerHolds:

    def test_the_2026_10_08_case_is_clamped(self):
        """Books say 7 short, broker holds 3. Asking for 7 is what triggered
        the unmappable prompt and left the order unsubmitted."""
        s = _strat(broker_qty=-3)
        placed = _run(s)
        assert placed, "nothing was placed at all"
        assert placed[0] == ("BUY", 3), placed

    def test_an_exact_match_is_NOT_clamped(self):
        s = _strat(broker_qty=-7)
        assert _run(s)[0] == ("BUY", 7)

    def test_a_MERGED_conid_does_not_RAISE_the_quantity(self):
        """Two entries share the conid so the broker holds 14, but this entry
        owns only 7. The clamp is a min, never a max — closing 14 would eat the
        co-located entry's short."""
        s = _strat(broker_qty=-14)
        assert _run(s)[0] == ("BUY", 7)

    def test_a_LONG_leg_clamps_on_the_positive_side(self):
        s = _strat(broker_qty=3)
        assert _run(s, leg_name="long_put")[0] == ("SELL", 3)

    def test_a_long_leg_is_unaffected_by_a_SHORT_broker_quantity(self):
        """Wrong-signed inventory means 0 available on this side; the clamp
        must not fire (and must not place a 0-quantity order)."""
        s = _strat(broker_qty=-5)
        placed = _run(s, leg_name="long_put")
        assert placed and placed[0][1] != 0, placed

    def test_it_logs_the_disagreement_loudly(self, caplog):
        s = _strat(broker_qty=-3)
        with caplog.at_level("WARNING"):
            _run(s)
        txt = " ".join(r.getMessage() for r in caplog.records)
        assert "A3:" in txt and "clamping close from 7 to 3" in txt


class TestItFailsSafeWhenItCannotKnow:

    def test_a_FAILED_position_read_does_not_clamp(self):
        """`_qty_before` is None — unknown, not zero. Guessing a clamp here
        could under-close a live breached short."""
        s = _strat(broker_qty=None)
        assert _run(s)[0] == ("BUY", 7)

    def test_zero_available_is_left_to_the_already_gone_check(self):
        """Clamping to 0 would place an invalid order; the loop's own strict
        'already gone?' check owns that case."""
        s = _strat(broker_qty=0)
        placed = _run(s)
        assert all(q != 0 for _, q in placed), placed


class TestTheClampCannotOverClose:

    @pytest.mark.parametrize("broker,want", [
        (-1, 1), (-2, 2), (-6, 6), (-7, 7), (-8, 7), (-100, 7),
    ])
    def test_never_more_than_the_entry_owns_nor_more_than_exists(self, broker, want):
        s = _strat(broker_qty=broker)
        assert _run(s)[0] == ("BUY", want)
