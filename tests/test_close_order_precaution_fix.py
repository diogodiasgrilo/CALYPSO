"""Close-order rejection fix (2026-06-12).

Variant C's E#2 take-profit retried 84× and never closed because the close path
(a) bid an absurd marketable-limit (ask + up to $3.00 ≈ 6× a $0.45 0DTE wing),
tripping IBKR's hard "Limit price too far outside of NBBO" rejection, and (b) hit
an UNMAPPED "...exceeds the Number of ticks constraint of 20. Are you sure?"
precaution that ibind.find_answer raised on. These tests pin both fixes: a small
tick-bounded cross, and a substring answer for the precaution.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("ibind")


# ─── (a) the cross is now small / tick-bounded ────────────────────────────
class TestCloseCrossBounded:
    def test_cross_cap_is_small(self):
        from bots.hydra.base_strategy import (
            CLOSE_LIMIT_CROSS_STEP, CLOSE_LIMIT_CROSS_CAP,
        )
        # Was STEP $0.50 / CAP $3.00 (bid ask+$3 on a $0.45 wing → IBKR reject).
        assert CLOSE_LIMIT_CROSS_CAP <= 0.30
        assert CLOSE_LIMIT_CROSS_STEP <= 0.10
        assert CLOSE_LIMIT_CROSS_CAP >= CLOSE_LIMIT_CROSS_STEP

    def test_buy_close_limit_stays_near_the_ask(self):
        # Replicate the _place_marketable_close BUY cross for a cheap 0DTE wing.
        from bots.hydra.base_strategy import (
            CLOSE_LIMIT_CROSS_STEP, CLOSE_LIMIT_CROSS_CAP,
        )
        ask = 0.45
        for attempt in range(1, 8):
            cross = min(CLOSE_LIMIT_CROSS_STEP * max(1, attempt), CLOSE_LIMIT_CROSS_CAP)
            limit = ask + cross
            # Never bid more than ~6 ticks ($0.30) over the ask — well inside
            # IBKR's 20-tick / NBBO tolerance (old code reached ask+$3.00).
            assert limit - ask <= 0.30
            assert limit <= ask + CLOSE_LIMIT_CROSS_CAP + 1e-9


# ─── (b) the precaution is now answered ───────────────────────────────────
class TestNumberOfTicksConstraintAnswered:
    def _answers(self):
        from shared.ib_client import DEFAULT_ORDER_ANSWERS
        return DEFAULT_ORDER_ANSWERS

    def test_ticks_constraint_prompt_is_confirmed(self):
        from ibind.client.ibkr_utils import find_answer
        q = ('o451: The following order "BUY 7 SPX (SPXW) JUN 12 \'26 7525 Call '
             '@ 2.35" price exceeds the Number of ticks constraint of 20.'
             'Are you sure you want to submit this order?')
        assert find_answer(q, self._answers()) is True

    def test_existing_answers_unbroken(self):
        from ibind.client.ibkr_utils import find_answer, QuestionType
        a = self._answers()
        # A few existing prompts still resolve to their intended answers.
        assert find_answer(str(QuestionType.PRICE_PERCENTAGE_CONSTRAINT), a) is True
        assert find_answer(str(QuestionType.MISSING_MARKET_DATA), a) is False
        assert find_answer(str(QuestionType.ORDER_SIZE_LIMIT), a) is True

    def test_unrelated_prompt_still_raises(self):
        # The substring key must be specific — it must NOT swallow other prompts.
        from ibind.client.ibkr_utils import find_answer
        with pytest.raises(ValueError):
            find_answer("Some entirely unrelated brand-new IBKR prompt text", self._answers())
