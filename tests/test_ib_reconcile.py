"""Tests for shared.ib_reconcile — Phase A.7.

Tests the classify_orders / apply_decisions pipeline that handles
broker-side persistent orders after a bot restart.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_reconcile import (
    OrderRecord,
    ReconcileAction,
    ReconcileDecision,
    ReconcileResult,
    apply_decisions,
    classify_orders,
)


# ─── OrderRecord parsing ────────────────────────────────────────────────────


class TestOrderRecordFromBroker:
    def test_extracts_canonical_fields(self):
        rec = OrderRecord.from_broker_dict({
            "orderId": "abc123", "status": "Submitted",
            "filled": 0, "remaining": 10, "side": "SELL", "totalSize": 10,
        })
        assert rec.order_id == "abc123"
        assert rec.status == "Submitted"
        assert rec.filled == 0
        assert rec.remaining == 10
        assert rec.side == "SELL"
        assert rec.quantity == 10

    def test_handles_alternate_key_names(self):
        # ibind's response shape varies; we should accept order_id OR orderId
        rec = OrderRecord.from_broker_dict({"order_id": "xyz"})
        assert rec.order_id == "xyz"

    def test_missing_id_returns_empty_string(self):
        rec = OrderRecord.from_broker_dict({"status": "Filled"})
        assert rec.order_id == ""

    def test_numeric_id_coerced_to_string(self):
        rec = OrderRecord.from_broker_dict({"orderId": 12345})
        assert rec.order_id == "12345"


class TestOrderRecordFromState:
    def test_extracts_state_shape(self):
        rec = OrderRecord.from_state_dict({
            "order_id": "abc", "status": "Submitted",
            "contracts": 10, "side": "SELL",
        })
        assert rec.order_id == "abc"
        assert rec.quantity == 10
        assert rec.side == "SELL"


# ─── classify_orders ────────────────────────────────────────────────────────


class TestClassifyOrders:
    def test_only_broker_active_status_yields_cancel(self):
        """Orphan order on broker that we don't know about → CANCEL for safety."""
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Submitted"}],
            state_orders=[],
        )
        assert len(result.only_broker) == 1
        d = result.only_broker[0]
        assert d.order_id == "abc"
        assert d.action == ReconcileAction.CANCEL
        assert "unknown order" in d.reason.lower()

    def test_only_broker_terminal_status_yields_skip(self):
        """Already-terminal broker-side order → SKIP (already done)."""
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Filled"}],
            state_orders=[],
        )
        assert len(result.only_broker) == 1
        assert result.only_broker[0].action == ReconcileAction.SKIP

    def test_only_state_active_yields_lookup(self):
        """We thought we had a live order, broker disagrees → look up trade history."""
        result = classify_orders(
            broker_orders=[],
            state_orders=[{"order_id": "abc", "status": "Submitted"}],
        )
        assert len(result.only_state) == 1
        d = result.only_state[0]
        assert d.action == ReconcileAction.LOOKUP_FILL

    def test_only_state_terminal_yields_skip(self):
        """Already-terminal state-side order → nothing to do."""
        result = classify_orders(
            broker_orders=[],
            state_orders=[{"order_id": "abc", "status": "Filled"}],
        )
        assert len(result.only_state) == 1
        assert result.only_state[0].action == ReconcileAction.SKIP

    def test_present_in_both_yields_reattach(self):
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Submitted"}],
            state_orders=[{"order_id": "abc", "status": "PreSubmitted"}],
        )
        assert len(result.both) == 1
        d = result.both[0]
        assert d.action == ReconcileAction.REATTACH
        assert d.broker_record is not None
        assert d.state_record is not None

    def test_total_count_matches_unique_ids(self):
        result = classify_orders(
            broker_orders=[
                {"orderId": "a", "status": "Submitted"},
                {"orderId": "b", "status": "Submitted"},
            ],
            state_orders=[
                {"order_id": "b", "status": "Submitted"},
                {"order_id": "c", "status": "Submitted"},
            ],
        )
        # 'a' = only_broker, 'b' = both, 'c' = only_state
        assert result.total == 3
        assert len(result.only_broker) == 1
        assert len(result.both) == 1
        assert len(result.only_state) == 1

    def test_summary_string_present(self):
        result = classify_orders([], [])
        assert "reconcile" in result.summary()
        assert "0" in result.summary()

    def test_empty_inputs(self):
        result = classify_orders([], [])
        assert result.total == 0

    def test_none_inputs_treated_as_empty(self):
        result = classify_orders(None, None)
        assert result.total == 0

    def test_orders_without_id_dropped(self):
        """Defensive: malformed broker/state entries without an ID can't
        be reconciled meaningfully — drop them rather than crash."""
        result = classify_orders(
            broker_orders=[{"status": "Submitted"}],  # no orderId
            state_orders=[],
        )
        assert result.total == 0


# ─── apply_decisions ────────────────────────────────────────────────────────


class TestApplyDecisions:
    def test_cancel_calls_cancel_fn(self):
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Submitted"}],
            state_orders=[],
        )
        cancel_fn = MagicMock(return_value=True)
        out = apply_decisions(result, cancel_fn=cancel_fn)
        cancel_fn.assert_called_once_with("abc")
        assert out["cancel"] == ["abc"]

    def test_cancel_fn_returning_false_excluded_from_output_and_logged_error(
        self, caplog,
    ):
        """When cancel_fn returns False (broker rejected the cancel), the
        order is NOT recorded in the cancel-success list AND an ERROR log
        is emitted so the operator can see it."""
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Submitted"}],
            state_orders=[],
        )
        cancel_fn = MagicMock(return_value=False)
        import logging
        with caplog.at_level(logging.ERROR):
            out = apply_decisions(result, cancel_fn=cancel_fn)
        # FAILED entry is NOT in cancel list (it didn't succeed)
        assert out["cancel"] == []
        # And we did log the failure at ERROR
        assert any("CANCEL" in r.message and "FAILED" in r.message
                   for r in caplog.records)

    def test_lookup_calls_lookup_fn(self):
        result = classify_orders(
            broker_orders=[],
            state_orders=[{"order_id": "abc", "status": "Submitted"}],
        )
        cancel_fn = MagicMock()
        lookup_fn = MagicMock()
        out = apply_decisions(
            result, cancel_fn=cancel_fn, lookup_fn=lookup_fn,
        )
        lookup_fn.assert_called_once_with("abc")
        assert out["lookup"] == ["abc"]

    def test_lookup_without_fn_logged_only(self):
        result = classify_orders(
            broker_orders=[],
            state_orders=[{"order_id": "abc", "status": "Submitted"}],
        )
        cancel_fn = MagicMock()
        out = apply_decisions(result, cancel_fn=cancel_fn, lookup_fn=None)
        # Still reported in output (so caller sees the gap)
        assert out["lookup"] == ["abc"]

    def test_reattach_calls_reattach_fn(self):
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Submitted"}],
            state_orders=[{"order_id": "abc", "status": "PreSubmitted"}],
        )
        cancel_fn = MagicMock()
        reattach_fn = MagicMock()
        out = apply_decisions(
            result, cancel_fn=cancel_fn, reattach_fn=reattach_fn,
        )
        reattach_fn.assert_called_once()
        # First positional arg is the order_id
        args = reattach_fn.call_args.args
        assert args[0] == "abc"
        assert isinstance(args[1], OrderRecord)
        assert out["reattach"] == ["abc"]

    def test_skip_action_no_side_effects(self):
        # Both order in terminal state on broker side — should SKIP, not cancel
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Filled"}],
            state_orders=[],
        )
        cancel_fn = MagicMock()
        out = apply_decisions(result, cancel_fn=cancel_fn)
        cancel_fn.assert_not_called()
        assert out["skip"] == ["abc"]
        assert out["cancel"] == []

    def test_dry_run_no_side_effects(self):
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Submitted"}],
            state_orders=[],
        )
        cancel_fn = MagicMock()
        out = apply_decisions(result, cancel_fn=cancel_fn, dry_run=True)
        cancel_fn.assert_not_called()
        # But the would-be cancel is still tracked in output
        assert out["cancel"] == ["abc"]

    def test_cancel_fn_raising_propagates(self):
        """If the caller's cancel handler crashes, apply_decisions does
        NOT swallow — the operator must see the failure. (No try/except
        is wrapped around cancel_fn; this pins the contract.)"""
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Submitted"}],
            state_orders=[],
        )
        cancel_fn = MagicMock(side_effect=RuntimeError("broker timeout"))
        with pytest.raises(RuntimeError, match="broker timeout"):
            apply_decisions(result, cancel_fn=cancel_fn)

    def test_reattach_fn_raising_propagates(self):
        """Same contract for reattach_fn — caller-side exceptions surface."""
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Submitted"}],
            state_orders=[{"order_id": "abc", "status": "PreSubmitted"}],
        )
        cancel_fn = MagicMock()
        reattach_fn = MagicMock(side_effect=RuntimeError("state file corrupted"))
        with pytest.raises(RuntimeError, match="state file corrupted"):
            apply_decisions(
                result, cancel_fn=cancel_fn, reattach_fn=reattach_fn,
            )


class TestOrderRecordMalformed:
    def test_non_numeric_filled_field_raises(self):
        """Defensive: if the broker / state file ever returns 'filled' as
        a non-numeric string, OrderRecord.from_broker_dict raises ValueError
        at parse time. This surfaces the data-quality issue rather than
        silently coercing to 0 (which would mis-classify the order's state)."""
        with pytest.raises(ValueError):
            OrderRecord.from_broker_dict({
                "orderId": "abc", "status": "Submitted",
                "filled": "not_a_number",
            })

    def test_alternate_id_keys_accepted(self):
        """orderID (capital D) and 'id' fallbacks per from_broker_dict."""
        rec1 = OrderRecord.from_broker_dict({"orderID": "abc"})
        assert rec1.order_id == "abc"
        rec2 = OrderRecord.from_broker_dict({"id": "def"})
        assert rec2.order_id == "def"


class TestReconcileEdgeCases:
    def test_reattach_fn_none_still_logs(self):
        """When reattach_fn is None, REATTACH items are still listed in
        the output (so caller can see the unhandled cases) but no side
        effect occurs."""
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Submitted"}],
            state_orders=[{"order_id": "abc", "status": "PreSubmitted"}],
        )
        out = apply_decisions(result, cancel_fn=MagicMock(), reattach_fn=None)
        assert out["reattach"] == ["abc"]

    def test_duplicate_orderid_in_broker_keeps_one(self):
        """Defensive: if broker_orders has the same id twice, the second
        entry overwrites the first in broker_by_id — no crash."""
        result = classify_orders(
            broker_orders=[
                {"orderId": "abc", "status": "Submitted"},
                {"orderId": "abc", "status": "Filled"},
            ],
            state_orders=[],
        )
        assert result.total == 1  # deduped by order_id
        # Last writer wins → status=Filled → SKIP
        assert result.only_broker[0].action == ReconcileAction.SKIP

    def test_rejected_status_classified_as_terminal(self):
        """Per the recent ib_reconcile fix: Rejected goes to the SKIP
        bucket (terminal) rather than CANCEL or LOOKUP_FILL."""
        result = classify_orders(
            broker_orders=[{"orderId": "abc", "status": "Rejected"}],
            state_orders=[],
        )
        assert result.only_broker[0].action == ReconcileAction.SKIP

    def test_expired_status_classified_as_terminal(self):
        result = classify_orders(
            broker_orders=[],
            state_orders=[{"order_id": "abc", "status": "Expired"}],
        )
        assert result.only_state[0].action == ReconcileAction.SKIP


# ─── End-to-end scenario test ──────────────────────────────────────────────


class TestEndToEndCrashRecovery:
    """The headline scenario: bot crashed mid-IC. On reconnect:
       - One order filled while we were down (only_state, active in state)
       - One order is still working on the broker (both)
       - One unknown order appeared (orphan on broker)
       - One we know was cancelled before crash (state-side terminal)
    """

    def test_full_crash_recovery_scenario(self):
        broker = [
            # The working order (we'll find it in state too)
            {"orderId": "still_working", "status": "Submitted",
             "side": "SELL", "filled": 0, "remaining": 10, "totalSize": 10},
            # An orphan we don't recognize
            {"orderId": "ghost_order", "status": "Submitted",
             "side": "BUY", "filled": 0, "remaining": 5, "totalSize": 5},
        ]
        state = [
            # The working order
            {"order_id": "still_working", "status": "Submitted",
             "side": "SELL", "contracts": 10},
            # An order we thought was open but isn't (filled while down)
            {"order_id": "filled_while_down", "status": "Submitted",
             "side": "BUY", "contracts": 10},
            # An order we knew was cancelled
            {"order_id": "old_cancelled", "status": "Cancelled",
             "side": "SELL", "contracts": 10},
        ]
        result = classify_orders(broker, state)

        # Verify each is in the right bucket
        assert len(result.both) == 1
        assert result.both[0].order_id == "still_working"
        assert result.both[0].action == ReconcileAction.REATTACH

        assert len(result.only_broker) == 1
        assert result.only_broker[0].order_id == "ghost_order"
        assert result.only_broker[0].action == ReconcileAction.CANCEL

        # only_state has both the filled-while-down (action=LOOKUP) and the
        # already-cancelled (action=SKIP)
        assert len(result.only_state) == 2
        actions_by_id = {d.order_id: d.action for d in result.only_state}
        assert actions_by_id["filled_while_down"] == ReconcileAction.LOOKUP_FILL
        assert actions_by_id["old_cancelled"] == ReconcileAction.SKIP

    def test_apply_decisions_calls_correct_handlers(self):
        broker = [
            {"orderId": "still_working", "status": "Submitted"},
            {"orderId": "ghost_order", "status": "Submitted"},
        ]
        state = [
            {"order_id": "still_working", "status": "Submitted"},
            {"order_id": "filled_while_down", "status": "Submitted"},
        ]
        result = classify_orders(broker, state)
        cancel_fn = MagicMock(return_value=True)
        lookup_fn = MagicMock()
        reattach_fn = MagicMock()
        out = apply_decisions(
            result, cancel_fn=cancel_fn,
            lookup_fn=lookup_fn, reattach_fn=reattach_fn,
        )
        # ghost_order → cancel
        cancel_fn.assert_called_once_with("ghost_order")
        # filled_while_down → lookup
        lookup_fn.assert_called_once_with("filled_while_down")
        # still_working → reattach
        reattach_fn.assert_called_once()
        assert reattach_fn.call_args.args[0] == "still_working"
        assert out == {
            "cancel": ["ghost_order"],
            "lookup": ["filled_while_down"],
            "reattach": ["still_working"],
            "skip": [],
        }
