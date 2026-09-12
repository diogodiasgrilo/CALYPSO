"""HERMES misclassifying a genuine order-execution FAILURE as a routine,
uneventful "clean" put_only $0 trade (2026-07-31) — found during adversarial
review of the execution-failure fix (tests/test_entry_execution_failure.py).

A failed entry sets BOTH call_side_skipped and put_side_skipped=True (same as
a deliberate skip, for backward-compat with "no real position" checks), so
without checking execution_failed FIRST, `_classify_outcome` fell through to
"clean" and the entry_type branch mislabeled it "put_only" (the call_side_skipped
branch runs first) — HERMES's daily analyst report would have narrated a real
broker/execution problem as a normal, uneventful trade.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.hermes.data_collector import _classify_outcome, compute_cheat_sheet


def _failed_entry(entry_number=3):
    return {
        "entry_number": entry_number,
        "is_complete": True,
        "call_side_skipped": True,
        "put_side_skipped": True,
        "execution_failed": True,
        "skip_reason": "Execution failed after 3 attempts: Entry execution failed",
        "call_spread_credit": 0.0,
        "put_spread_credit": 0.0,
    }


class TestClassifyOutcome:
    def test_execution_failed_entry_classified_as_execution_failed(self):
        assert _classify_outcome(_failed_entry()) == "execution_failed"

    def test_execution_failed_wins_over_no_other_markers(self):
        # No close_reason, no *_side_stopped — the exact real-world shape of a
        # failed entry — must not fall through to "clean".
        e = _failed_entry()
        assert e.get("close_reason") is None
        assert not e.get("call_side_stopped")
        assert not e.get("put_side_stopped")
        assert _classify_outcome(e) == "execution_failed"

    def test_regular_skip_still_falls_through_to_clean(self):
        # A routine strategic skip (no execution_failed flag) must be
        # unaffected by this fix — still "clean" (0 credit, nothing happened).
        e = _failed_entry()
        e["execution_failed"] = False
        assert _classify_outcome(e) == "clean"

    def test_real_stop_unaffected(self):
        e = {"call_side_stopped": True, "put_side_stopped": False}
        assert _classify_outcome(e) == "call_stopped"


class TestComputeCheatSheetEntryType:
    def test_failed_entry_gets_execution_failed_type_not_put_only(self):
        data = {"state": {"entries": [_failed_entry()]}, "metrics": {}, "apollo_report": None}
        result = compute_cheat_sheet(data)
        outcomes = result["entry_outcomes"]
        assert len(outcomes) == 1
        assert outcomes[0]["entry_type"] == "execution_failed"
        assert outcomes[0]["outcome"] == "execution_failed"
        assert outcomes[0]["total_credit"] == 0.0

    def test_regular_put_only_entry_unaffected(self):
        # A real one-sided entry (call skipped, put placed) must still read
        # "put_only" — the fix must not touch the existing classification.
        entry = {
            "entry_number": 1, "is_complete": True,
            "call_side_skipped": True, "put_side_skipped": False,
            "call_spread_credit": 0.0, "put_spread_credit": 150.0,
            "put_side_stop": 300.0,
        }
        data = {"state": {"entries": [entry]}, "metrics": {}, "apollo_report": None}
        result = compute_cheat_sheet(data)
        assert result["entry_outcomes"][0]["entry_type"] == "put_only"
