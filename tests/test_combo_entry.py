"""Tests for the defined-risk COMBO entry path (06-04 doubled-spread fix).

Root cause being fixed: base_strategy._execute_entry used to leg an iron condor
in as 4 single-leg orders. On IBKR a standalone SHORT leg is rejected as naked
at order-check time ("EQUITY WITH LOAN VALUE ... MUST EXCEED THE INITIAL MARGIN
... MARGIN DEFICIT"); the entry retry then re-placed legs and orphaned a doubled
spread. Fix: place each vertical spread as ONE defined-risk BAG combo via
broker.place_vertical_spread_and_wait so IBKR nets the long hedge (spread margin
~$5k, not ~$1M naked) and the combo fills atomically (no naked-short window).

Two layers tested:
  • bots/hydra/base_strategy._execute_entry — routes each active side through
    the combo helper; full IC = 2 combos, one-sided = 1 combo; non-fill fails
    closed with no *_uic set + no orphan.
  • shared/ib_client.place_vertical_spread_and_wait — submits the combo, polls
    to fill, extracts per-leg conids + fill prices, computes net_credit in the
    dollar convention the entry code expects ((short-long)*100*contracts).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import IronCondorEntry
from bots.hydra.strategy import HydraStrategy


# ─── _execute_entry combo routing ──────────────────────────────────────────


def _strategy_for_execute(*, est_call=300.0, est_put=350.0):
    """A bare HydraStrategy wired so _execute_entry runs against a mock broker.

    est_call / est_put are per-IC-unit dollar credit estimates (×100, NOT
    ×contracts) — the shape _estimate_entry_credit returns.
    """
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = False
    s.contracts_per_entry = 10
    s.broker = MagicMock()
    # Credit estimate (per-IC dollars). _combo_credit_limit_for_side divides /100.
    s._estimate_entry_credit = MagicMock(return_value=(est_call, est_put))
    # Today's expiry as a date-ish ISO string (the real method returns "%Y-%m-%d").
    s._get_todays_expiry = MagicMock(return_value="2026-06-04")
    # Registration is a no-op on IBKR (no per-leg id); stub it out.
    s._register_position = MagicMock()
    s._verify_entry_fill_prices = MagicMock()
    s._handle_naked_short = MagicMock()
    s._unwind_partial_entry = MagicMock()
    return s


def _combo_fill(*, short_conid, long_conid, short_px, long_px, contracts):
    """A normalized place_vertical_spread_and_wait fill result."""
    return {
        "status": "filled",
        "filled": True,
        "short_conid": short_conid,
        "long_conid": long_conid,
        "short_fill_price": short_px,
        "long_fill_price": long_px,
        "net_credit": (short_px - long_px) * 100.0 * contracts,
        "order_id": "OID",
        "raw": {},
    }


def _full_ic_entry():
    e = IronCondorEntry(entry_number=2)
    e.strategy_id = "hydra_test_e2"
    e.contracts = 10
    e.short_call_strike = 5500.0
    e.long_call_strike = 5550.0
    e.short_put_strike = 5400.0
    e.long_put_strike = 5350.0
    return e


class TestExecuteEntryFullIC:
    def test_full_ic_places_exactly_two_combos_one_C_one_P(self):
        s = _strategy_for_execute()
        s.broker.place_vertical_spread_and_wait.side_effect = [
            _combo_fill(short_conid=111, long_conid=112,
                        short_px=4.00, long_px=1.00, contracts=10),  # call
            _combo_fill(short_conid=211, long_conid=212,
                        short_px=4.50, long_px=1.00, contracts=10),  # put
        ]
        entry = _full_ic_entry()

        assert s._execute_entry(entry) is True

        calls = s.broker.place_vertical_spread_and_wait.call_args_list
        assert len(calls) == 2
        rights = [c.kwargs["right"] for c in calls]
        assert rights == ["C", "P"]
        assert all(c.kwargs["action"] == "SELL" for c in calls)
        # contracts threaded from entry.contracts
        assert all(c.kwargs["contracts"] == 10 for c in calls)

    def test_full_ic_sets_uics_and_credits_from_results(self):
        s = _strategy_for_execute()
        s.broker.place_vertical_spread_and_wait.side_effect = [
            _combo_fill(short_conid=111, long_conid=112,
                        short_px=4.00, long_px=1.00, contracts=10),
            _combo_fill(short_conid=211, long_conid=212,
                        short_px=4.50, long_px=1.00, contracts=10),
        ]
        entry = _full_ic_entry()
        s._execute_entry(entry)

        assert entry.short_call_uic == 111
        assert entry.long_call_uic == 112
        assert entry.short_put_uic == 211
        assert entry.long_put_uic == 212
        # net_credit in dollars: (4.00-1.00)*100*10 = 3000; (4.50-1.00)*100*10 = 3500
        assert entry.call_spread_credit == pytest.approx(3000.0)
        assert entry.put_spread_credit == pytest.approx(3500.0)
        # IBKR has no per-leg position id
        assert entry.short_call_position_id is None
        assert entry.long_put_position_id is None

    def test_credit_limit_is_per_share_below_mid(self):
        # est_call=300 (per-IC dollars) → mid 3.00/share → limit 3.00-0.10=2.90
        s = _strategy_for_execute(est_call=300.0, est_put=350.0)
        s.broker.place_vertical_spread_and_wait.side_effect = [
            _combo_fill(short_conid=111, long_conid=112,
                        short_px=4.0, long_px=1.0, contracts=10),
            _combo_fill(short_conid=211, long_conid=212,
                        short_px=4.5, long_px=1.0, contracts=10),
        ]
        entry = _full_ic_entry()
        s._execute_entry(entry)
        calls = s.broker.place_vertical_spread_and_wait.call_args_list
        call_limit = calls[0].kwargs["net_credit_limit"]
        put_limit = calls[1].kwargs["net_credit_limit"]
        assert call_limit == pytest.approx(2.90)   # 3.00 - 0.10
        assert put_limit == pytest.approx(3.40)    # 3.50 - 0.10


class TestExecuteEntryOneSided:
    def test_put_only_places_one_combo_right_P(self):
        s = _strategy_for_execute()
        s.broker.place_vertical_spread_and_wait.return_value = _combo_fill(
            short_conid=211, long_conid=212, short_px=4.5, long_px=1.0,
            contracts=10,
        )
        entry = _full_ic_entry()
        entry.put_only = True               # HYDRA put-only flag
        entry.call_side_skipped = True      # call side already routed out

        assert s._execute_entry(entry) is True

        calls = s.broker.place_vertical_spread_and_wait.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["right"] == "P"
        # Put side filled, call side untouched (skipped + zero credit)
        assert entry.short_put_uic == 211
        assert entry.short_call_uic is None
        assert entry.call_side_skipped is True
        assert entry.call_spread_credit == 0.0
        assert entry.put_spread_credit == pytest.approx(3500.0)

    def test_call_only_places_one_combo_right_C(self):
        s = _strategy_for_execute()
        s.broker.place_vertical_spread_and_wait.return_value = _combo_fill(
            short_conid=111, long_conid=112, short_px=4.0, long_px=1.0,
            contracts=10,
        )
        entry = _full_ic_entry()
        entry.call_only = True
        entry.put_side_skipped = True

        assert s._execute_entry(entry) is True

        calls = s.broker.place_vertical_spread_and_wait.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["right"] == "C"
        assert entry.short_call_uic == 111
        assert entry.short_put_uic is None
        assert entry.put_side_skipped is True
        assert entry.put_spread_credit == 0.0
        assert entry.call_spread_credit == pytest.approx(3000.0)

    def test_gex_skipped_call_via_zeroed_strikes_routes_put_only(self):
        # Brandon GEX SKIP zeroes the call strikes (no put_only flag necessarily).
        s = _strategy_for_execute()
        s.broker.place_vertical_spread_and_wait.return_value = _combo_fill(
            short_conid=211, long_conid=212, short_px=4.5, long_px=1.0,
            contracts=10,
        )
        entry = _full_ic_entry()
        entry.short_call_strike = 0.0
        entry.long_call_strike = 0.0

        assert s._execute_entry(entry) is True
        calls = s.broker.place_vertical_spread_and_wait.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["right"] == "P"


class TestOneSidedMethodsRouteThroughCombo:
    """B2 — _execute_put_spread_only / _execute_call_spread_only must place the
    single active vertical as ONE combo (delegating to _execute_entry), NOT as
    standalone single-leg orders (which IBKR rejects as naked). They also do NOT
    fall back to _place_option_order on any path."""

    def _strategy(self):
        s = _strategy_for_execute()
        # If the old single-leg path were ever taken, this would record a call.
        s._place_option_order = MagicMock(
            side_effect=AssertionError("single-leg path must not be used (B2)"))
        s._handle_naked_short = MagicMock()
        s._unwind_partial_entry = MagicMock()
        return s

    def test_put_spread_only_places_one_put_combo(self):
        s = self._strategy()
        s.broker.place_vertical_spread_and_wait.return_value = _combo_fill(
            short_conid=211, long_conid=212, short_px=4.5, long_px=1.0,
            contracts=10)
        entry = _full_ic_entry()
        assert s._execute_put_spread_only(entry) is True
        calls = s.broker.place_vertical_spread_and_wait.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["right"] == "P"
        assert calls[0].kwargs["action"] == "SELL"
        # one-sided invariant set by the method itself
        assert entry.put_only is True
        assert entry.call_side_skipped is True
        assert entry.short_put_uic == 211
        assert entry.call_spread_credit == 0.0
        assert entry.put_spread_credit == pytest.approx(3500.0)
        s._place_option_order.assert_not_called()

    def test_call_spread_only_places_one_call_combo(self):
        s = self._strategy()
        s.broker.place_vertical_spread_and_wait.return_value = _combo_fill(
            short_conid=111, long_conid=112, short_px=4.0, long_px=1.0,
            contracts=10)
        entry = _full_ic_entry()
        assert s._execute_call_spread_only(entry) is True
        calls = s.broker.place_vertical_spread_and_wait.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["right"] == "C"
        assert entry.call_only is True
        assert entry.put_side_skipped is True
        assert entry.short_call_uic == 111
        assert entry.put_spread_credit == 0.0
        assert entry.call_spread_credit == pytest.approx(3000.0)
        s._place_option_order.assert_not_called()

    def test_put_only_threads_credit_estimate(self):
        s = self._strategy()
        s.broker.place_vertical_spread_and_wait.return_value = _combo_fill(
            short_conid=211, long_conid=212, short_px=4.5, long_px=1.0,
            contracts=10)
        entry = _full_ic_entry()
        # est_put=350 per-IC → 3.50/share → limit 3.50-0.10=3.40 (no net bid stub)
        s._execute_put_spread_only(entry, credit_estimates=(300.0, 350.0))
        calls = s.broker.place_vertical_spread_and_wait.call_args_list
        assert calls[0].kwargs["net_credit_limit"] == pytest.approx(3.40)
        # estimate threaded into the fill positivity guard too
        assert calls[0].kwargs["estimated_credit_per_share"] == pytest.approx(3.50)


class TestExecuteEntryFailClosed:
    def test_combo_not_filled_returns_false_and_no_uic(self):
        s = _strategy_for_execute()
        # Call combo times out unfilled.
        s.broker.place_vertical_spread_and_wait.return_value = {
            "status": "timed_out", "filled": False,
            "short_conid": None, "long_conid": None,
            "short_fill_price": None, "long_fill_price": None,
            "net_credit": 0.0, "order_id": "OID", "raw": {},
        }
        # FIX 5(b): a timed_out RETURN now reconciles against get_positions
        # before failing. Stub the reconcile read to show NO live legs →
        # genuine non-fill → fail closed (no adopt).
        s.broker.qualify_contract = MagicMock(side_effect=[111, 112])
        s._read_open_positions = MagicMock(return_value=[])
        entry = _full_ic_entry()

        assert s._execute_entry(entry) is False
        # No conids set on the failed side (fail-closed, no orphan).
        assert entry.short_call_uic is None
        assert entry.long_call_uic is None
        # Atomic combo → nothing to unwind, but the path is exercised safely.
        # (no naked short because the short leg never filled standalone)
        s._handle_naked_short.assert_not_called()

    def test_put_fails_after_call_fills_unwinds_call_only(self):
        s = _strategy_for_execute()
        s.broker.place_vertical_spread_and_wait.side_effect = [
            _combo_fill(short_conid=111, long_conid=112,
                        short_px=4.0, long_px=1.0, contracts=10),  # call fills
            {"status": "rejected", "filled": False, "short_conid": None,
             "long_conid": None, "short_fill_price": None,
             "long_fill_price": None, "net_credit": 0.0, "order_id": "OID2",
             "raw": {}},                                            # put fails
        ]
        # FIX 5(b): the put's timed_out/rejected RETURN reconciles first; stub
        # the reconcile read to show NO live put legs → genuine non-fill.
        s.broker.qualify_contract = MagicMock(side_effect=[211, 212])
        s._read_open_positions = MagicMock(return_value=[])
        entry = _full_ic_entry()

        assert s._execute_entry(entry) is False
        # The filled call combo is handed to _unwind_partial_entry (both legs).
        s._unwind_partial_entry.assert_called_once()
        unwound_legs = s._unwind_partial_entry.call_args.args[0]
        leg_names = {lg[0] for lg in unwound_legs}
        assert leg_names == {"long_call", "short_call"}
        # No naked short: the short_call combo includes its long hedge.
        s._handle_naked_short.assert_not_called()


class TestExecuteEntryBrokerErrorReconcile:
    """B3c — a BrokerError (HTTP timeout) from the combo call must NOT blindly
    orphan: reconcile against get_positions and ADOPT legs that are actually
    live, else fail closed."""

    def _brokererror(self):
        from shared.broker_client import BrokerError
        return BrokerError("broker unreachable for place_vertical_spread_and_wait")

    def test_broker_error_with_live_legs_adopts(self):
        s = _strategy_for_execute()
        # Combo call raises (e.g. HTTP timeout) — but the legs DID fill.
        s.broker.place_vertical_spread_and_wait.side_effect = self._brokererror()
        # Reconcile resolves the call leg conids and finds them live.
        s.broker.qualify_contract = MagicMock(side_effect=[111, 112, 211, 212])
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 111, "quantity": -10,
             "raw": {"avgPrice": 4.00}},
            {"instrument_id": 112, "quantity": 10,
             "raw": {"avgPrice": 1.00}},
            {"instrument_id": 211, "quantity": -10,
             "raw": {"avgPrice": 4.50}},
            {"instrument_id": 212, "quantity": 10,
             "raw": {"avgPrice": 1.00}},
        ])
        entry = _full_ic_entry()
        # Put side fills normally after the call side is adopted.
        # (side_effect already raised once; set a return for the put call.)
        s.broker.place_vertical_spread_and_wait.side_effect = [
            self._brokererror(),  # call combo HTTP-timeouts
            _combo_fill(short_conid=211, long_conid=212,
                        short_px=4.5, long_px=1.0, contracts=10),  # put fills
        ]
        assert s._execute_entry(entry) is True
        # Call side ADOPTED from live positions (not orphaned, not skipped).
        assert entry.short_call_uic == 111
        assert entry.long_call_uic == 112
        # net_credit from adopted avgPrice diff: (4.00-1.00)*100*10 = 3000.
        assert entry.call_spread_credit == pytest.approx(3000.0)

    def test_broker_error_with_absent_legs_fails_closed(self):
        s = _strategy_for_execute()
        s.broker.place_vertical_spread_and_wait.side_effect = self._brokererror()
        s.broker.qualify_contract = MagicMock(side_effect=[111, 112])
        # Legs are NOT present at the broker → genuine non-fill.
        s._read_open_positions = MagicMock(return_value=[])
        entry = _full_ic_entry()
        assert s._execute_entry(entry) is False
        # No conids adopted (fail closed, no phantom).
        assert entry.short_call_uic is None
        assert entry.long_call_uic is None


# ─── place_vertical_spread_and_wait (IBClient unit) ─────────────────────────


def _ib_for_combo():
    """A bare IBClient with only the methods place_vertical_spread_and_wait uses
    stubbed — no live session, no real ibind."""
    from shared.ib_client import IBClient
    ib = IBClient.__new__(IBClient)
    ib.place_vertical_spread = MagicMock(
        return_value={"order_id": "OID", "order_status": "submitted"}
    )
    return ib


class TestPlaceVerticalSpreadAndWait:
    def test_polls_to_fill_and_extracts_legs_and_credit(self):
        from shared.ib_client import _normalize_position_dict  # noqa: F401
        ib = _ib_for_combo()
        # First poll: still working; second poll: filled.
        ib.get_order_status = MagicMock(side_effect=[
            {"status": "submitted"},
            {"status": "filled", "average_price": 3.50},
        ])
        # qualify_contract resolves short then long conid.
        ib.qualify_contract = MagicMock(side_effect=[711, 712])
        # REAL position shape (B1): avgCost is PER-CONTRACT (×100), avgPrice is
        # PER-SHARE. A $4.20/share short reports avgCost≈420, avgPrice=4.20.
        # The combo math must read avgPrice; reading avgCost would be 100× off.
        ib.get_positions = MagicMock(return_value=[
            {"conid": 711, "position": -10, "avgCost": 420.0, "avgPrice": 4.20},  # short
            {"conid": 712, "position": 10, "avgCost": 70.0, "avgPrice": 0.70},    # long
        ])

        out = ib.place_vertical_spread_and_wait(
            expiry="2026-06-04", short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=3.40,
            poll_interval_s=0.0, timeout_seconds=5.0,
        )

        assert out["filled"] is True
        assert out["status"] == "filled"
        assert out["short_conid"] == 711
        assert out["long_conid"] == 712
        # Per-share fill prices come from avgPrice, NOT avgCost.
        assert out["short_fill_price"] == pytest.approx(4.20)
        assert out["long_fill_price"] == pytest.approx(0.70)
        # net_credit dollars = (4.20 - 0.70) * 100 * 10 = 3500
        assert out["net_credit"] == pytest.approx(3500.0)
        # Polled at least twice (working → filled).
        assert ib.get_order_status.call_count >= 2

    def test_reads_avgprice_not_avgcost_no_100x_bug(self):
        """REGRESSION GUARD (B1): if the credit math ever reads avgCost
        (per-contract, ×100) instead of avgPrice (per-share), net_credit comes
        out 100× too big. This test would FAIL in that case."""
        ib = _ib_for_combo()
        ib.get_order_status = MagicMock(
            return_value={"status": "filled", "average_price": 3.50}
        )
        ib.qualify_contract = MagicMock(side_effect=[711, 712])
        ib.get_positions = MagicMock(return_value=[
            # $2.35/share short, $0.11/share long → net 2.24/share.
            # avgCost is the ×100 per-contract value the OLD bug consumed.
            {"conid": 711, "position": -10, "avgCost": 235.0, "avgPrice": 2.35},
            {"conid": 712, "position": 10, "avgCost": 11.0, "avgPrice": 0.11},
        ])
        out = ib.place_vertical_spread_and_wait(
            expiry="2026-06-04", short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=2.20,
            poll_interval_s=0.0, timeout_seconds=5.0,
        )
        # Correct (avgPrice): (2.35 - 0.11) * 100 * 10 = 2240.
        assert out["net_credit"] == pytest.approx(2240.0)
        # The 100× bug (avgCost) would give (235 - 11) * 100 * 10 = 224000.
        assert out["net_credit"] < 10000  # categorically rules out the bug

    def test_instant_terminal_fill_short_circuits_poll(self):
        ib = _ib_for_combo()
        ib.place_vertical_spread.return_value = {
            "order_id": "OID", "order_status": "filled",
        }
        ib.get_order_status = MagicMock(
            return_value={"status": "filled", "average_price": 3.5}
        )
        ib.qualify_contract = MagicMock(side_effect=[711, 712])
        ib.get_positions = MagicMock(return_value=[
            {"conid": 711, "position": -10, "avgCost": 420.0, "avgPrice": 4.20},
            {"conid": 712, "position": 10, "avgCost": 70.0, "avgPrice": 0.70},
        ])
        out = ib.place_vertical_spread_and_wait(
            expiry="2026-06-04", short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=3.40,
            poll_interval_s=0.0,
        )
        assert out["filled"] is True
        assert out["net_credit"] == pytest.approx(3500.0)

    def test_rejected_combo_returns_filled_false_no_credit(self):
        ib = _ib_for_combo()
        ib.place_vertical_spread.return_value = {
            "order_id": "OID", "order_status": "rejected",
        }
        ib.qualify_contract = MagicMock()
        ib.get_positions = MagicMock()
        out = ib.place_vertical_spread_and_wait(
            expiry="2026-06-04", short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=3.40,
            poll_interval_s=0.0,
        )
        assert out["filled"] is False
        assert out["status"] == "rejected"
        assert out["net_credit"] == 0.0
        assert out["short_conid"] is None
        # No conid resolution / position read on a non-fill.
        ib.qualify_contract.assert_not_called()
        ib.get_positions.assert_not_called()

    def test_timed_out_combo_cancels_working_order(self):
        """B3b — a combo that never reaches a terminal state must be CANCELLED
        before returning filled=False, so a fill seconds after the poll deadline
        cannot leave an untracked working order."""
        ib = _ib_for_combo()
        # place returns submitted; poll always shows 'submitted' (non-terminal)
        # → loop exhausts → timed_out.
        ib.get_order_status = MagicMock(return_value={"status": "submitted"})
        ib.cancel_order = MagicMock(return_value=True)
        out = ib.place_vertical_spread_and_wait(
            expiry="2026-06-04", short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=3.40,
            poll_interval_s=0.0, timeout_seconds=0.05,
        )
        assert out["filled"] is False
        assert out["status"] == "timed_out"
        ib.cancel_order.assert_called_once_with("OID")

    def test_timed_out_cancel_failure_is_swallowed(self):
        """B3b — if the timeout cancel raises (breaker/transient), the method
        still returns filled=False (does not crash); the caller's get_positions
        reconcile is the backstop."""
        ib = _ib_for_combo()
        ib.get_order_status = MagicMock(return_value={"status": "submitted"})
        ib.cancel_order = MagicMock(side_effect=RuntimeError("breaker open"))
        out = ib.place_vertical_spread_and_wait(
            expiry="2026-06-04", short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=3.40,
            poll_interval_s=0.0, timeout_seconds=0.05,
        )
        assert out["filled"] is False
        assert out["status"] == "timed_out"

    def test_falls_back_to_combo_avg_price_when_leg_cost_missing(self):
        ib = _ib_for_combo()
        ib.get_order_status = MagicMock(
            return_value={"status": "filled", "average_price": 3.50}
        )
        ib.qualify_contract = MagicMock(side_effect=[711, 712])
        # Positions don't carry the leg conids → per-leg avg_cost unavailable.
        ib.get_positions = MagicMock(return_value=[])
        out = ib.place_vertical_spread_and_wait(
            expiry="2026-06-04", short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=3.40,
            poll_interval_s=0.0,
        )
        assert out["filled"] is True
        # Falls back to combo avg fill price (3.50/share) * 100 * 10 = 3500.
        assert out["net_credit"] == pytest.approx(3500.0)
        assert out["short_fill_price"] is None

    def test_nonpositive_net_falls_back_to_estimate(self):
        """B1 positivity guard: a FILLED combo whose per-leg avgPrice diff is
        non-positive (bad/missing read) must fall back to the MKT-011 estimate,
        never book 0/negative credit into the stop math."""
        ib = _ib_for_combo()
        ib.get_order_status = MagicMock(
            return_value={"status": "filled"}  # no combo avg either
        )
        ib.qualify_contract = MagicMock(side_effect=[711, 712])
        # Degenerate read: short avgPrice <= long avgPrice → net <= 0.
        ib.get_positions = MagicMock(return_value=[
            {"conid": 711, "position": -10, "avgPrice": 0.50},
            {"conid": 712, "position": 10, "avgPrice": 0.70},
        ])
        out = ib.place_vertical_spread_and_wait(
            expiry="2026-06-04", short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=3.40,
            poll_interval_s=0.0, timeout_seconds=5.0,
            estimated_credit_per_share=3.40,
        )
        assert out["filled"] is True
        # Fell back to estimate: 3.40 * 100 * 10 = 3400 (NOT a -200 negative).
        assert out["net_credit"] == pytest.approx(3400.0)

    def test_nonpositive_net_no_estimate_books_zero(self):
        """B1: non-positive net AND no usable estimate → 0.0 (fail-safe, never
        negative); the side is still flagged filled for the caller to handle."""
        ib = _ib_for_combo()
        ib.get_order_status = MagicMock(return_value={"status": "filled"})
        ib.qualify_contract = MagicMock(side_effect=[711, 712])
        ib.get_positions = MagicMock(return_value=[
            {"conid": 711, "position": -10, "avgPrice": 0.50},
            {"conid": 712, "position": 10, "avgPrice": 0.70},
        ])
        out = ib.place_vertical_spread_and_wait(
            expiry="2026-06-04", short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=3.40,
            poll_interval_s=0.0, timeout_seconds=5.0,
        )
        assert out["net_credit"] == 0.0

    def test_accepts_date_expiry(self):
        from datetime import date
        ib = _ib_for_combo()
        ib.place_vertical_spread.return_value = {
            "order_id": "OID", "order_status": "rejected",
        }
        out = ib.place_vertical_spread_and_wait(
            expiry=date(2026, 6, 4), short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=3.40, poll_interval_s=0.0,
        )
        # The date is coerced and passed through to place_vertical_spread.
        assert out["status"] == "rejected"
        passed_expiry = ib.place_vertical_spread.call_args.kwargs["expiry"]
        assert passed_expiry == date(2026, 6, 4)

    def test_invalid_right_raises(self):
        ib = _ib_for_combo()
        from shared.ib_client import IBClientError
        with pytest.raises(IBClientError):
            ib.place_vertical_spread_and_wait(
                expiry="2026-06-04", short_strike=5400, long_strike=5350,
                right="X", contracts=10, net_credit_limit=3.40,
            )


# ─── FIX 1 — Brandon override signature forwards credit_estimates ────────────


class TestBrandonExecuteEntrySignature:
    """FIX 1 (2026-06-05) — BrandonHydraStrategy._execute_entry override must be
    3-arg (entry, credit_estimates=None) and forward credit_estimates to super.
    The OLD 2-arg override raised TypeError for EVERY entry on a live Brandon
    instance (variant C) because the router calls _execute_entry(entry,
    credit_estimates=...) → zero entries placed → live outage."""

    def _brandon(self):
        from bots.hydra.brandon.strategy import BrandonHydraStrategy
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        # Make the Brandon-specific pre-logic (_brandon_apply_strike_adjuster) a
        # no-op so we isolate the signature/forwarding behavior.
        s.brandon_gex_enabled = False
        s.brandon_strike_adjuster_enabled = False
        # Wire the parent combo path against a mock broker (same harness as the
        # base _execute_entry tests).
        s.dry_run = False
        s.contracts_per_entry = 10
        s.broker = MagicMock()
        s._estimate_entry_credit = MagicMock(return_value=(300.0, 350.0))
        s._get_todays_expiry = MagicMock(return_value="2026-06-04")
        s._register_position = MagicMock()
        s._verify_entry_fill_prices = MagicMock()
        s._handle_naked_short = MagicMock()
        s._unwind_partial_entry = MagicMock()
        return s

    def test_execute_entry_accepts_and_forwards_credit_estimates(self):
        s = self._brandon()
        s.broker.place_vertical_spread_and_wait.side_effect = [
            _combo_fill(short_conid=111, long_conid=112,
                        short_px=4.0, long_px=1.0, contracts=10),
            _combo_fill(short_conid=211, long_conid=212,
                        short_px=4.5, long_px=1.0, contracts=10),
        ]
        entry = _full_ic_entry()
        # The exact router call shape — must NOT raise TypeError on the kwarg.
        assert s._execute_entry(entry, credit_estimates=(50.0, 60.0)) is True
        # credit_estimates (per-IC dollars) forwarded → limit = est/100 − 0.10.
        calls = s.broker.place_vertical_spread_and_wait.call_args_list
        assert calls[0].kwargs["net_credit_limit"] == pytest.approx(0.40)  # 0.50-0.10
        assert calls[1].kwargs["net_credit_limit"] == pytest.approx(0.50)  # 0.60-0.10

    def test_override_signature_has_credit_estimates_param(self):
        # Direct guard against the 2-arg regression: the override must declare
        # credit_estimates so the keyword call binds.
        import inspect
        from bots.hydra.brandon.strategy import BrandonHydraStrategy
        sig = inspect.signature(BrandonHydraStrategy._execute_entry)
        assert "credit_estimates" in sig.parameters


# ─── FIX 3 — reconcile-adopt sign/magnitude/positivity guards ────────────────


class TestReconcileAdoptGuards:
    """FIX 3 (2026-06-05) — _reconcile_combo_at_broker must fail closed on a
    netted/partial/wrong residual: short must be SHORT (qty<0), long must be
    LONG (qty>0), |qty|==contracts on BOTH; and a non-positive adopted credit
    must fall back to the MKT-011 estimate (or fail closed if none)."""

    def _brokererror(self):
        from shared.broker_client import BrokerError
        return BrokerError("broker unreachable for place_vertical_spread_and_wait")

    def test_wrong_sign_short_leg_fails_closed(self):
        s = _strategy_for_execute()
        s.broker.qualify_contract = MagicMock(side_effect=[111, 112])
        # Short call leg shows POSITIVE quantity (long, not short) → not our combo.
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 111, "quantity": 10, "raw": {"avgPrice": 4.0}},
            {"instrument_id": 112, "quantity": 10, "raw": {"avgPrice": 1.0}},
        ])
        s.broker.place_vertical_spread_and_wait.side_effect = self._brokererror()
        entry = _full_ic_entry()
        entry.call_only = True
        entry.put_side_skipped = True
        assert s._execute_entry(entry) is False
        assert entry.short_call_uic is None
        assert entry.long_call_uic is None

    def test_wrong_magnitude_fails_closed(self):
        s = _strategy_for_execute()
        s.broker.qualify_contract = MagicMock(side_effect=[111, 112])
        # |qty| != contracts (5 vs 10) → partial/netted residual → fail closed.
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 111, "quantity": -5, "raw": {"avgPrice": 4.0}},
            {"instrument_id": 112, "quantity": 5, "raw": {"avgPrice": 1.0}},
        ])
        s.broker.place_vertical_spread_and_wait.side_effect = self._brokererror()
        entry = _full_ic_entry()
        entry.call_only = True
        entry.put_side_skipped = True
        assert s._execute_entry(entry) is False
        assert entry.short_call_uic is None

    def test_nonpositive_adopted_credit_no_estimate_fails_closed(self):
        s = _strategy_for_execute()
        s.broker.qualify_contract = MagicMock(side_effect=[111, 112])
        # Valid signs/magnitudes BUT short avgPrice <= long avgPrice → net <= 0,
        # and NO MKT-011 estimate threaded (credit_estimates=None) → fail closed
        # (do NOT book a bogus credit).
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 111, "quantity": -10, "raw": {"avgPrice": 0.50}},
            {"instrument_id": 112, "quantity": 10, "raw": {"avgPrice": 0.70}},
        ])
        s.broker.place_vertical_spread_and_wait.side_effect = self._brokererror()
        entry = _full_ic_entry()
        entry.call_only = True
        entry.put_side_skipped = True
        # No credit_estimates → est_per_share None → non-positive net fails closed.
        assert s._execute_entry(entry) is False
        assert entry.short_call_uic is None

    def test_nonpositive_adopted_credit_falls_back_to_estimate(self):
        s = _strategy_for_execute()
        s.broker.qualify_contract = MagicMock(side_effect=[111, 112])
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 111, "quantity": -10, "raw": {"avgPrice": 0.50}},
            {"instrument_id": 112, "quantity": 10, "raw": {"avgPrice": 0.70}},
        ])
        s.broker.place_vertical_spread_and_wait.side_effect = self._brokererror()
        entry = _full_ic_entry()
        entry.call_only = True
        entry.put_side_skipped = True
        # MKT-011 est_call=300 per-IC → 3.00/share threaded → adopted credit
        # falls back to 3.00*100*10 = 3000 rather than the negative avgPrice diff.
        assert s._execute_entry(entry, credit_estimates=(300.0, 350.0)) is True
        assert entry.short_call_uic == 111
        assert entry.call_spread_credit == pytest.approx(3000.0)


# ─── FIX 5(b) — reconcile on a clean timed_out RETURN (not just exception) ────


class TestTimedOutReturnReconciles:
    """FIX 5(b) (2026-06-05) — a clean timed_out RETURN (filled=False, NO raised
    exception) is also a cancel/fill race window: place_vertical_spread_and_wait
    cancels on timeout, but a fill that lost the race leaves live legs. The
    strategy must reconcile + ADOPT them (with FIX 3 guards), not orphan."""

    def _timed_out(self):
        return {
            "status": "timed_out", "filled": False,
            "short_conid": None, "long_conid": None,
            "short_fill_price": None, "long_fill_price": None,
            "net_credit": 0.0, "order_id": "OID", "raw": {},
        }

    def test_timed_out_return_with_live_legs_adopts(self):
        s = _strategy_for_execute()
        # Single-sided (call-only) so exactly one combo is placed and it returns
        # timed_out (no exception) — but the legs DID fill at the broker.
        s.broker.place_vertical_spread_and_wait.return_value = self._timed_out()
        s.broker.qualify_contract = MagicMock(side_effect=[111, 112])
        s._read_open_positions = MagicMock(return_value=[
            {"instrument_id": 111, "quantity": -10, "raw": {"avgPrice": 4.0}},
            {"instrument_id": 112, "quantity": 10, "raw": {"avgPrice": 1.0}},
        ])
        entry = _full_ic_entry()
        entry.call_only = True
        entry.put_side_skipped = True
        assert s._execute_entry(entry) is True
        # Adopted from the live legs (net (4-1)*100*10 = 3000).
        assert entry.short_call_uic == 111
        assert entry.long_call_uic == 112
        assert entry.call_spread_credit == pytest.approx(3000.0)

    def test_timed_out_return_with_no_legs_fails_closed(self):
        s = _strategy_for_execute()
        s.broker.place_vertical_spread_and_wait.return_value = self._timed_out()
        s.broker.qualify_contract = MagicMock(side_effect=[111, 112])
        s._read_open_positions = MagicMock(return_value=[])
        entry = _full_ic_entry()
        entry.call_only = True
        entry.put_side_skipped = True
        assert s._execute_entry(entry) is False
        assert entry.short_call_uic is None


# ─── FIX 6 — net-bid pricing coverage (_combo_credit_limit_for_side) ──────────


class TestComboCreditLimitNetBid:
    """FIX 6 (2026-06-05) — net-bid-aware limit pricing. _combo_side_net_bid is
    STUBBED so the real chain read isn't needed; est_per_share is the MKT-011
    mid (per-share)."""

    def _strategy(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = False
        s.contracts_per_entry = 10
        s.broker = MagicMock()
        s._estimate_entry_credit = MagicMock(return_value=(0.0, 0.0))
        s._get_todays_expiry = MagicMock(return_value="2026-06-04")
        return s

    def _entry(self):
        return _full_ic_entry()

    def test_net_bid_above_mid_minus_slip_uses_mid(self):
        # net_bid 3.50 > mid(3.00)-slip(0.10)=2.90 → limit = 2.90.
        s = self._strategy()
        s._combo_side_net_bid = MagicMock(return_value=3.50)
        limit = s._combo_credit_limit_for_side(self._entry(), "call", 3.00)
        assert limit == pytest.approx(2.90)

    def test_net_bid_between_floor_and_mid_crosses_at_net_bid(self):
        # floor(0.05) < net_bid(1.20) < mid-slip(2.90) → limit = net_bid 1.20.
        s = self._strategy()
        s._combo_side_net_bid = MagicMock(return_value=1.20)
        limit = s._combo_credit_limit_for_side(self._entry(), "call", 3.00)
        assert limit == pytest.approx(1.20)

    def test_net_bid_at_or_below_floor_aborts_side(self):
        # FIX 4 — net_bid collapsed to/below floor → ABORT (None), do NOT clamp
        # to the floor and dump the spread.
        s = self._strategy()
        s._combo_side_net_bid = MagicMock(return_value=0.05)
        assert s._combo_credit_limit_for_side(self._entry(), "call", 3.00) is None
        s._combo_side_net_bid = MagicMock(return_value=-0.10)
        assert s._combo_credit_limit_for_side(self._entry(), "call", 3.00) is None

    def test_net_bid_none_uses_mid_only(self):
        # net_bid unavailable → mid-only fallback: limit = mid-slip = 2.90.
        s = self._strategy()
        s._combo_side_net_bid = MagicMock(return_value=None)
        limit = s._combo_credit_limit_for_side(self._entry(), "call", 3.00)
        assert limit == pytest.approx(2.90)

    def test_net_bid_none_small_positive_mid_clamps_to_floor(self):
        # mid-only path, mid-slip falls below floor but positive mid existed →
        # clamp to floor (this is the ONLY case the floor-clamp is reserved for).
        s = self._strategy()
        s._combo_side_net_bid = MagicMock(return_value=None)
        # mid 0.08/share → 0.08-0.10 = -0.02 < floor → clamped to 0.05.
        limit = s._combo_credit_limit_for_side(self._entry(), "call", 0.08)
        assert limit == pytest.approx(0.05)

    def test_no_usable_mid_aborts_regardless_of_net_bid(self):
        # est_per_share None and _estimate_entry_credit returns 0 → no mid → None.
        s = self._strategy()
        s._combo_side_net_bid = MagicMock(return_value=3.50)
        assert s._combo_credit_limit_for_side(self._entry(), "call", None) is None


class TestComboSideNetBid:
    """FIX 6 — _combo_side_net_bid returns None when a leg's bid/ask is missing
    (so the caller relies on the mid-only limit), and short_bid − long_ask when
    both legs quote."""

    def _strategy(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.broker = MagicMock()
        s._get_todays_expiry = MagicMock(return_value="2026-06-04")
        return s

    def test_returns_none_when_a_leg_quote_is_none(self):
        s = self._strategy()
        s._read_option_chain = MagicMock(return_value=(
            {5500.0: 111, 5550.0: 112}, {}))
        # Short quotes, but long_ask is None → cannot form a net bid → None.
        s._read_option_quotes_batch = MagicMock(return_value={
            111: {"bid": 4.0, "ask": 4.2},
            112: {"bid": 1.0, "ask": None},
        })
        entry = _full_ic_entry()
        assert s._combo_side_net_bid(entry, "call") is None

    def test_returns_short_bid_minus_long_ask(self):
        s = self._strategy()
        s._read_option_chain = MagicMock(return_value=(
            {5500.0: 111, 5550.0: 112}, {}))
        s._read_option_quotes_batch = MagicMock(return_value={
            111: {"bid": 4.0, "ask": 4.2},
            112: {"bid": 0.9, "ask": 1.1},
        })
        entry = _full_ic_entry()
        # short_bid 4.0 − long_ask 1.1 = 2.9.
        assert s._combo_side_net_bid(entry, "call") == pytest.approx(2.9)


# ─── broker allowlist ───────────────────────────────────────────────────────


class TestBrokerAllowlist:
    def test_combo_methods_allowlisted(self):
        from shared.broker_service import ALLOWED_METHODS
        assert "place_vertical_spread_and_wait" in ALLOWED_METHODS
        assert "place_vertical_spread" in ALLOWED_METHODS
        assert "place_iron_condor" in ALLOWED_METHODS

    def test_brokerclient_proxies_combo_method(self):
        from shared.broker_client import BrokerClient
        transport = MagicMock(return_value={"result": {"filled": True}})
        bc = BrokerClient(transport=transport)
        out = bc.place_vertical_spread_and_wait(
            expiry="2026-06-04", short_strike=5400, long_strike=5350,
            right="P", contracts=10, net_credit_limit=3.40,
        )
        assert out == {"filled": True}
        # Method name + kwargs threaded through the transport.
        method, args, kwargs = transport.call_args.args
        assert method == "place_vertical_spread_and_wait"
        assert kwargs["right"] == "P"
