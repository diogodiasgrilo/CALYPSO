"""`margin_utilization_pct` was NULL on every IBKR-era row, with its inputs in hand.

The code claimed "IBKR's balance does not surface per-position margin-used /
utilization". It does: `fullinitmarginreq`, `netliquidation`,
`fullexcessliquidity` and `buyingpower` are all in the account/summary payload
that `_read_account_balance` already returns under `_raw`. What IBKR does not
surface is a pre-computed PERCENTAGE — not the same thing, and the conflation
left the column empty while the numbers sat unread in the same dict.

The property that matters most here is NOT that the percentage appears. It is
that the BUYING-POWER GATE does not move. This is telemetry; it decides nothing.
`_check_buying_power` picks `available` from a field-priority list, so adding a
name that appears in that list could change which field it reads and therefore
whether a trade is allowed. A telemetry fix that alters a trading decision is a
worse bug than the one it fixes.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.strategy import HydraStrategy  # noqa: E402

# The field-priority list `_check_buying_power` selects `available` from.
GATE_FIELDS = ["MarginAvailableForTrading", "SpendingPower",
               "CashAvailableForTrading", "NetEquityForMargin"]


def _strategy(balance):
    s = HydraStrategy.__new__(HydraStrategy)
    s.broker = SimpleNamespace(get_balance=lambda: balance)
    return s


def _ibkr(used=0.0, netliq=1_006_320.9375, tradable=1_003_712.75, wrap=True):
    def f(v):
        return {"amount": v} if wrap else v
    return {
        "tradable": tradable,
        "raw_summary": {
            "fullinitmarginreq": f(used),
            "netliquidation": f(netliq),
            "fullexcessliquidity": f(1_003_712.75),
            "buyingpower": f(4_014_851.0),
        },
    }


class TestTheGateCannotMove:
    def test_no_new_field_enters_the_priority_list(self):
        """The whole safety argument. Adding NetEquityForMargin (or any other
        gate field) could change which value `available` takes."""
        out = _strategy(_ibkr()).__class__._read_account_balance(_strategy(_ibkr()))
        added = [k for k in out if k in GATE_FIELDS and k != "MarginAvailableForTrading"]
        assert not added, (
            f"{added} entered the balance dict. Those names are in "
            f"_check_buying_power's field-priority list, so this telemetry "
            f"change could alter which field the BP gate reads — and whether a "
            f"trade is allowed.")

    def test_margin_available_is_still_the_tradable_value(self):
        out = _strategy(_ibkr(tradable=1234.5))._read_account_balance()
        assert out["MarginAvailableForTrading"] == 1234.5

    def test_only_diagnostic_keys_were_added(self):
        out = _strategy(_ibkr(used=50_000.0))._read_account_balance()
        assert set(out) <= {"MarginAvailableForTrading", "_raw",
                            "MarginUsedByCurrentPositions", "MarginUtilizationPct"}, set(out)


class TestTheDerivation:
    def test_it_computes_used_over_netliquidation(self):
        out = _strategy(_ibkr(used=50_000.0, netliq=1_000_000.0))._read_account_balance()
        assert out["MarginUtilizationPct"] == pytest.approx(5.0)
        assert out["MarginUsedByCurrentPositions"] == 50_000.0

    def test_a_flat_account_reads_zero_not_null(self):
        """0% on a flat account is a real measurement, not a missing one."""
        out = _strategy(_ibkr(used=0.0))._read_account_balance()
        assert out["MarginUtilizationPct"] == 0.0

    def test_it_handles_bare_numbers_as_well_as_amount_dicts(self):
        out = _strategy(_ibkr(used=25_000.0, netliq=500_000.0, wrap=False))._read_account_balance()
        assert out["MarginUtilizationPct"] == pytest.approx(5.0)


class TestAbsentMeansNullNotZero:
    def test_a_missing_summary_yields_no_percentage(self):
        """NULL means 'not measured'. 0.0 would read as 'measured, nothing
        used' — which is what the 85 legacy Saxo-era rows wrongly say."""
        out = _strategy({"tradable": 100.0})._read_account_balance()
        assert "MarginUtilizationPct" not in out
        assert "MarginUsedByCurrentPositions" not in out

    def test_zero_netliquidation_does_not_divide_by_zero(self):
        """Must skip the division AND still return a usable balance.

        First version asserted only `"MarginUtilizationPct" not in out`, which
        an EMPTY dict also satisfies — and removing the `total > 0` guard does
        exactly that: ZeroDivisionError is swallowed by the method's own
        except, `{}` comes back, and the assertion passed while the BP gate
        lost its margin field entirely. A mutation control caught it. The
        balance must survive, not merely lack the percentage.
        """
        out = _strategy(_ibkr(used=10.0, netliq=0.0))._read_account_balance()
        assert out.get("MarginAvailableForTrading") == 1_003_712.75, (
            "the balance came back empty — a swallowed exception, not a "
            "skipped division. The BP gate would lose its only field.")
        assert "MarginUtilizationPct" not in out

    def test_a_garbage_value_is_ignored_not_coerced(self):
        bal = _ibkr(used=10_000.0)
        bal["raw_summary"]["netliquidation"] = {"amount": "not-a-number"}
        out = _strategy(bal)._read_account_balance()
        assert "MarginUtilizationPct" not in out

    def test_a_broker_failure_still_returns_empty(self):
        s = HydraStrategy.__new__(HydraStrategy)
        def boom():
            raise RuntimeError("broker down")
        s.broker = SimpleNamespace(get_balance=boom)
        assert s._read_account_balance() == {}


class TestNonFiniteValuesAreNotMeasurements:
    """`float("nan")` SUCCEEDS — which is how NaN reached the column.

    Found by fuzzing this function after it shipped, in a post-hoc audit of the
    day's changes. Every other guard here passed it: `used is not None` is True
    for NaN, `total > 0` is True, and `round(nan, 4)` is nan. NaN then lands in
    `margin_utilization_pct`, where it is not a measurement of anything.
    """

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_used_margin_yields_no_percentage(self, bad):
        out = _strategy(_ibkr(used=bad, netliq=1_000_000.0))._read_account_balance()
        assert "MarginUtilizationPct" not in out, (
            f"used={bad} produced {out.get('MarginUtilizationPct')!r}")
        assert "MarginUsedByCurrentPositions" not in out
        assert out.get("MarginAvailableForTrading") is not None, (
            "the balance came back empty — the gate would lose its field")

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_a_non_finite_netliquidation_yields_no_percentage(self, bad):
        out = _strategy(_ibkr(used=50_000.0, netliq=bad))._read_account_balance()
        assert "MarginUtilizationPct" not in out, out.get("MarginUtilizationPct")

    def test_the_percentage_is_always_finite_when_present(self):
        out = _strategy(_ibkr(used=50_000.0, netliq=1_000_000.0))._read_account_balance()
        import math as _m
        assert _m.isfinite(out["MarginUtilizationPct"])
