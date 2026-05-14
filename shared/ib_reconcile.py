"""Order + position reconciliation on (re)connect — Phase A.7.

Critical behavior difference from Saxo: IB orders are **broker-side
persistent**. If our bot crashes mid-order, the order is still live on
IBKR's books when we reconnect. We must NOT blindly resubmit; we must
cross-check against our state file and reconcile.

Three cases enumerated in the migration plan §4.4:

  • only_broker: order exists on IBKR's books but NOT in our state file.
    Likely cause: bot restart raced with order placement, and we don't
    know what we just placed. Default action: CANCEL (safer than leaving
    an order live when we don't know what it is).

  • only_state: order is in our state file but NOT on IBKR's books.
    Likely cause: filled/cancelled while we were down. Default action:
    query the trade/execution history to determine final disposition,
    update state accordingly.

  • both: order exists in both places. Default action: re-attach. Update
    our state with broker-side status (filled qty, status, last update).

This module provides the policy + plumbing. Callers (IBClient on
connect; future StateManager) drive it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional


logger = logging.getLogger(__name__)


class ReconcileAction(str, Enum):
    """What to do for each reconciled order.

    CANCEL: order is on broker but not in our state — cancel for safety
    REATTACH: order is in both places — update our state with broker info
    LOOKUP_FILL: order is in our state but not on broker — query execution
                 history to determine final disposition
    SKIP: no action needed (e.g., already-terminal state)
    """
    CANCEL = "cancel"
    REATTACH = "reattach"
    LOOKUP_FILL = "lookup_fill"
    SKIP = "skip"


@dataclass(frozen=True)
class OrderRecord:
    """Minimal record of an order as our state file or broker knows it.

    Kept deliberately small — just the keys the reconcile algorithm needs.
    Full broker order dicts go untouched in callers.
    """
    order_id: str
    status: str = ""        # broker-reported (PreSubmitted, Submitted, Filled, etc.)
    filled: float = 0.0
    remaining: float = 0.0
    side: str = ""          # BUY / SELL
    quantity: float = 0.0   # original order size

    @classmethod
    def from_broker_dict(cls, d: dict) -> "OrderRecord":
        """Extract from ibind's live_orders response shape."""
        # ibind wraps in various ways — keys vary slightly. Be liberal.
        oid = (
            d.get("orderId") or d.get("order_id") or d.get("orderID")
            or d.get("id") or ""
        )
        return cls(
            order_id=str(oid),
            status=str(d.get("status") or d.get("order_status") or ""),
            filled=float(d.get("filled") or d.get("filledQuantity") or 0.0),
            remaining=float(d.get("remaining") or d.get("remainingQuantity") or 0.0),
            side=str(d.get("side") or ""),
            quantity=float(d.get("totalSize") or d.get("quantity") or 0.0),
        )

    @classmethod
    def from_state_dict(cls, d: dict) -> "OrderRecord":
        """Extract from our bot's state-file shape.

        Kept lenient so different bots' state schemas can feed in.
        """
        return cls(
            order_id=str(d.get("order_id") or d.get("orderId") or ""),
            status=str(d.get("status") or ""),
            filled=float(d.get("filled") or 0.0),
            remaining=float(d.get("remaining") or 0.0),
            side=str(d.get("side") or ""),
            quantity=float(d.get("quantity") or d.get("contracts") or 0.0),
        )


@dataclass
class ReconcileDecision:
    """A single reconcile-result item with rationale."""
    order_id: str
    action: ReconcileAction
    reason: str
    broker_record: Optional[OrderRecord] = None
    state_record: Optional[OrderRecord] = None


@dataclass
class ReconcileResult:
    """Aggregate output of a reconcile run."""
    only_broker: list[ReconcileDecision] = field(default_factory=list)
    only_state: list[ReconcileDecision] = field(default_factory=list)
    both: list[ReconcileDecision] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.only_broker) + len(self.only_state) + len(self.both)

    def summary(self) -> str:
        return (
            f"reconcile: {self.total} orders — "
            f"only_broker={len(self.only_broker)} "
            f"only_state={len(self.only_state)} "
            f"both={len(self.both)}"
        )


# Terminal order statuses — these don't need cancel-on-only_broker because
# they're already done.
TERMINAL_STATUSES = frozenset({
    "Filled", "Cancelled", "ApiCancelled",
    "filled", "cancelled", "api_cancelled",
    "Inactive", "inactive",
})


def classify_orders(
    broker_orders: Iterable[dict],
    state_orders: Iterable[dict],
) -> ReconcileResult:
    """Cross-reference broker open orders against our state file orders.

    Pure function — no I/O. Caller provides both sides as iterables of
    dicts; we extract OrderRecords and classify each into one of three
    categories with a default action.

    Decision policy (overridable by caller):
      • only_broker, status NOT terminal → CANCEL (safety: don't leave
        unknown orders live)
      • only_broker, status IS terminal → SKIP (already done)
      • only_state, broker has nothing → LOOKUP_FILL
      • both → REATTACH

    Args:
        broker_orders: from IBClient.get_open_orders() — list of dicts
        state_orders: from caller's state file — list of dicts. Each must
            have at least 'order_id' (or 'orderId').

    Returns:
        ReconcileResult with three buckets of ReconcileDecision objects.
    """
    broker_by_id: dict[str, OrderRecord] = {}
    for d in broker_orders or []:
        rec = OrderRecord.from_broker_dict(d)
        if rec.order_id:
            broker_by_id[rec.order_id] = rec

    state_by_id: dict[str, OrderRecord] = {}
    for d in state_orders or []:
        rec = OrderRecord.from_state_dict(d)
        if rec.order_id:
            state_by_id[rec.order_id] = rec

    result = ReconcileResult()
    all_ids = set(broker_by_id) | set(state_by_id)

    for oid in sorted(all_ids):
        in_broker = oid in broker_by_id
        in_state = oid in state_by_id

        if in_broker and in_state:
            result.both.append(ReconcileDecision(
                order_id=oid,
                action=ReconcileAction.REATTACH,
                reason="present in both broker and state — re-attach + sync status",
                broker_record=broker_by_id[oid],
                state_record=state_by_id[oid],
            ))
        elif in_broker:
            br = broker_by_id[oid]
            if br.status in TERMINAL_STATUSES:
                result.only_broker.append(ReconcileDecision(
                    order_id=oid,
                    action=ReconcileAction.SKIP,
                    reason=f"only on broker but already terminal ({br.status})",
                    broker_record=br,
                ))
            else:
                result.only_broker.append(ReconcileDecision(
                    order_id=oid,
                    action=ReconcileAction.CANCEL,
                    reason=(
                        f"only on broker (status={br.status!r}) and not in our "
                        "state — unknown order, cancelling for safety"
                    ),
                    broker_record=br,
                ))
        else:  # in_state only
            st = state_by_id[oid]
            if st.status in TERMINAL_STATUSES:
                result.only_state.append(ReconcileDecision(
                    order_id=oid,
                    action=ReconcileAction.SKIP,
                    reason=f"only in state but already terminal ({st.status})",
                    state_record=st,
                ))
            else:
                result.only_state.append(ReconcileDecision(
                    order_id=oid,
                    action=ReconcileAction.LOOKUP_FILL,
                    reason=(
                        f"in state (status={st.status!r}) but not on broker — "
                        "likely filled or cancelled while we were down; "
                        "query trade history for final disposition"
                    ),
                    state_record=st,
                ))

    return result


def apply_decisions(
    decisions: ReconcileResult,
    *,
    cancel_fn,
    lookup_fn=None,
    reattach_fn=None,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Execute the decisions returned by classify_orders.

    Args:
        decisions: from classify_orders()
        cancel_fn: callable(order_id) → bool. Required. Invoked for CANCEL
            actions. Typically IBClient.cancel_order.
        lookup_fn: callable(order_id) → dict. Optional. Invoked for
            LOOKUP_FILL actions. Should return the latest known disposition
            (queries trade history, etc.). If None, LOOKUP_FILL items are
            logged but no action is taken (state stays inconsistent until
            next manual review).
        reattach_fn: callable(order_id, broker_record) → None. Optional.
            Invoked for REATTACH actions. Typically updates the bot's
            state file with the broker's current view. If None, REATTACH
            items are logged only.
        dry_run: if True, NO side effects — just logs what would happen.

    Returns:
        dict mapping action → list of order_ids that succeeded for that
        action. E.g.:
            {"cancel": ["abc", "def"], "lookup": ["ghi"], "reattach": ["jkl"]}
    """
    results = {"cancel": [], "lookup": [], "reattach": [], "skip": []}

    for d in decisions.only_broker:
        if d.action == ReconcileAction.CANCEL:
            logger.warning(
                "reconcile CANCEL %s — %s", d.order_id, d.reason,
            )
            if not dry_run:
                ok = cancel_fn(d.order_id)
                if ok:
                    results["cancel"].append(d.order_id)
                else:
                    logger.error("reconcile CANCEL %s FAILED", d.order_id)
            else:
                results["cancel"].append(d.order_id)
        else:
            logger.info("reconcile SKIP %s — %s", d.order_id, d.reason)
            results["skip"].append(d.order_id)

    for d in decisions.only_state:
        if d.action == ReconcileAction.LOOKUP_FILL:
            logger.warning(
                "reconcile LOOKUP_FILL %s — %s", d.order_id, d.reason,
            )
            if not dry_run and lookup_fn is not None:
                lookup_fn(d.order_id)
            results["lookup"].append(d.order_id)
        else:
            logger.info("reconcile SKIP %s — %s", d.order_id, d.reason)
            results["skip"].append(d.order_id)

    for d in decisions.both:
        logger.info(
            "reconcile REATTACH %s (broker status=%s, state status=%s)",
            d.order_id, d.broker_record.status if d.broker_record else "?",
            d.state_record.status if d.state_record else "?",
        )
        if not dry_run and reattach_fn is not None and d.broker_record:
            reattach_fn(d.order_id, d.broker_record)
        results["reattach"].append(d.order_id)

    return results
