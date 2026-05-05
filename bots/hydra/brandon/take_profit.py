"""Take-profit at N% of credit captured.

Brandon Jones rule: close the iron condor when its mark price has decayed to
(1 - threshold) of the credit received. Default threshold 0.80 means close
when 80% of the credit has been captured (current cost-to-close <= 20% of
the credit received).

The module is pure: callers pass in dollar amounts, get a decision back.
Integration in strategy.py happens at the per-tick monitoring loop, before
the credit+buffer stop check.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TakeProfitDecision:
    should_close: bool
    profit_captured_pct: float
    threshold_value: float
    reason: str


def evaluate(
    credit_received: float,
    current_value: float,
    threshold: float = 0.80,
) -> TakeProfitDecision:
    """Decide whether to close a credit position for take-profit.

    Args:
        credit_received: Initial credit collected, dollars (must be > 0).
        current_value: Current cost-to-close, dollars (>= 0).
        threshold: Captured-profit ratio that triggers close. 0.80 = close
            when 80% of credit captured. Must satisfy 0 < threshold < 1.

    Returns:
        TakeProfitDecision. should_close is False on degenerate inputs
        (no credit, negative value, invalid threshold) so the caller never
        accidentally closes a position because of a bookkeeping bug.
    """
    if not (0.0 < threshold < 1.0):
        return TakeProfitDecision(False, 0.0, 0.0, f"invalid threshold {threshold!r}")
    if credit_received <= 0:
        return TakeProfitDecision(False, 0.0, 0.0, "no credit received yet")
    if current_value < 0:
        return TakeProfitDecision(False, 0.0, 0.0, f"invalid current_value {current_value!r}")

    # current_value == 0 with credit_received > 0 is overwhelmingly likely to
    # mean "spread_value not yet refreshed by the bot's price loop" rather
    # than "spread genuinely worth nothing." Refuse to fire in that case —
    # next tick will populate it with a real mark and the TP rule can decide
    # honestly. Otherwise a TP fires the moment an entry is placed (stale
    # default of 0.0 on IronCondorEntry.{call,put}_spread_value) and closes
    # the position before any real price tick has happened.
    if current_value == 0 and credit_received > 0:
        return TakeProfitDecision(
            False, 0.0, 0.0,
            "current_value is 0 with non-zero credit — likely stale, holding for next price tick",
        )

    threshold_value = (1.0 - threshold) * credit_received
    captured = (credit_received - current_value) / credit_received

    if captured >= threshold:
        return TakeProfitDecision(
            True,
            captured,
            threshold_value,
            (
                f"TP fired: SV ${current_value:.2f} <= trigger ${threshold_value:.2f} "
                f"({captured:.1%} captured >= {threshold:.0%})"
            ),
        )
    return TakeProfitDecision(
        False,
        captured,
        threshold_value,
        (
            f"holding: SV ${current_value:.2f} > trigger ${threshold_value:.2f} "
            f"({captured:.1%} captured < {threshold:.0%})"
        ),
    )


def evaluate_iron_condor(
    call_credit: float,
    put_credit: float,
    call_value: float,
    put_value: float,
    threshold: float = 0.80,
) -> TakeProfitDecision:
    """Evaluate TP for a full IC by summing both credit spreads.

    Brandon's rule operates on total IC profit, not per side. Sides that have
    already been closed (by stop, expiry, or skip) should pass 0 for both
    their credit and value so they drop out of both sums cleanly.
    """
    return evaluate(
        credit_received=call_credit + put_credit,
        current_value=call_value + put_value,
        threshold=threshold,
    )
