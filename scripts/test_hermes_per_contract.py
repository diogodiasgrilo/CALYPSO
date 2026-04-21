"""Regression test: Phase 2 A-3 HERMES cheat_sheet per-contract fields.

Verifies:
  1. _compute_averages_per_contract normalizes mixed 1c/2c daily returns
  2. compute_cheat_sheet includes today's contracts_per_entry + net_pnl_per_contract
  3. cumulative dict has avg_win_pnl_per_contract / avg_loss_pnl_per_contract
  4. Null-safe fallback: records missing contracts_per_entry default to 1
  5. At contracts_per_entry=1, per-contract values == raw totals (no-op invariant)

Run: python scripts/test_hermes_per_contract.py
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def test_averages_per_contract_normalization():
    """Mixed 1c+2c historical days normalize correctly to per-contract averages."""
    print("\n[H3-1] _compute_averages_per_contract normalizes mixed contract counts")
    from services.hermes.data_collector import _compute_averages_per_contract

    # Three historical days:
    #  - Day A: +$100 at 1c → +$100/c
    #  - Day B: +$200 at 2c → +$100/c
    #  - Day C: -$150 at 1c → -$150/c
    daily_returns = [
        {"date": "2026-04-15", "net_pnl": 100.0, "contracts_per_entry": 1},
        {"date": "2026-04-16", "net_pnl": 200.0, "contracts_per_entry": 2},
        {"date": "2026-04-17", "net_pnl": -150.0, "contracts_per_entry": 1},
    ]
    avg_win_pc, avg_loss_pc = _compute_averages_per_contract(daily_returns)
    # avg_win per-contract = (100 + 100) / 2 = 100
    # avg_loss per-contract = -150 / 1 = -150
    assert avg_win_pc == 100.0, f"expected 100.0, got {avg_win_pc}"
    assert avg_loss_pc == -150.0, f"expected -150.0, got {avg_loss_pc}"
    print(f"  \u2713 avg_win/c={avg_win_pc}, avg_loss/c={avg_loss_pc} (mixed 1c/2c normalized)")


def test_averages_per_contract_null_safe():
    """Pre-Phase-2 records (missing contracts_per_entry key) default to 1."""
    print("\n[H3-2] Missing / None / 0 contracts_per_entry defaults to 1")
    from services.hermes.data_collector import _compute_averages_per_contract

    # Three pathological cases + one normal:
    daily_returns = [
        {"date": "A", "net_pnl": 100.0},                             # missing key
        {"date": "B", "net_pnl": 50.0, "contracts_per_entry": None}, # JSON null
        {"date": "C", "net_pnl": 30.0, "contracts_per_entry": 0},    # invalid 0
        {"date": "D", "net_pnl": 200.0, "contracts_per_entry": 2},   # normal 2c
    ]
    avg_win_pc, _ = _compute_averages_per_contract(daily_returns)
    # All three pathological cases → cpe=1, so per-c = their raw pnl
    # Day A: 100/1 = 100, Day B: 50/1 = 50, Day C: 30/1 = 30, Day D: 200/2 = 100
    # avg_win = (100 + 50 + 30 + 100) / 4 = 70
    assert avg_win_pc == 70.0, f"expected 70.0, got {avg_win_pc}"
    print(f"  \u2713 null-safe fallback preserved; avg_win/c = {avg_win_pc}")


def test_no_op_at_1c():
    """Invariant: at contracts_per_entry=1 everywhere, per-contract == raw totals."""
    print("\n[H3-3] At all 1c, per-contract averages == raw-total averages")
    from services.hermes.data_collector import (
        _compute_averages, _compute_averages_per_contract,
    )

    daily_returns = [
        {"date": "A", "net_pnl": 100.0, "contracts_per_entry": 1},
        {"date": "B", "net_pnl": -50.0, "contracts_per_entry": 1},
        {"date": "C", "net_pnl": 200.0, "contracts_per_entry": 1},
    ]
    raw_win, raw_loss = _compute_averages(daily_returns)
    pc_win, pc_loss = _compute_averages_per_contract(daily_returns)
    assert raw_win == pc_win, f"{raw_win} != {pc_win}"
    assert raw_loss == pc_loss, f"{raw_loss} != {pc_loss}"
    print(f"  \u2713 identical at 1c: win={raw_win}, loss={raw_loss}")


def test_cheat_sheet_includes_contracts():
    """compute_cheat_sheet exposes contracts_per_entry + net_pnl_per_contract."""
    print("\n[H3-4] compute_cheat_sheet cheat_sheet dict contains per-contract fields")
    from services.hermes.data_collector import compute_cheat_sheet

    # Minimal synthetic state + metrics
    data = {
        "state": {
            "contracts_per_entry": 2,
            "entries_completed": 2,
            "total_realized_pnl": 400.0,
            "total_commission": 40.0,
            "total_credit_received": 500.0,
            "call_stops_triggered": 0,
            "put_stops_triggered": 0,
            "double_stops": 0,
            "entries": [
                {"entry_number": 2, "contracts": 2,
                 "call_spread_credit": 100.0, "put_spread_credit": 150.0,
                 "call_side_stopped": False, "put_side_stopped": False,
                 "call_side_expired": True, "put_side_expired": True},
                {"entry_number": 3, "contracts": 2,
                 "call_spread_credit": 110.0, "put_spread_credit": 140.0,
                 "call_side_stopped": False, "put_side_stopped": False,
                 "call_side_expired": True, "put_side_expired": True},
            ],
        },
        "metrics": {
            "cumulative_pnl": 1000.0,
            "winning_days": 5,
            "losing_days": 3,
            "daily_returns": [
                {"date": "old", "net_pnl": 100.0, "contracts_per_entry": 1},
                {"date": "new", "net_pnl": 400.0, "contracts_per_entry": 2},
            ],
        },
    }
    cs = compute_cheat_sheet(data)
    assert "contracts_per_entry" in cs, "contracts_per_entry missing from cheat_sheet"
    assert cs["contracts_per_entry"] == 2, f"got {cs['contracts_per_entry']}"
    assert "net_pnl_per_contract" in cs, "net_pnl_per_contract missing"
    # net_pnl = 400 - 40 = 360; per-c = 360 / 2 = 180
    assert cs["net_pnl_per_contract"] == 180.0, f"got {cs['net_pnl_per_contract']}"
    # Cumulative must also have per-contract averages
    assert "avg_win_pnl_per_contract" in cs["cumulative"]
    assert "avg_loss_pnl_per_contract" in cs["cumulative"]
    print(f"  \u2713 contracts_per_entry={cs['contracts_per_entry']}")
    print(f"  \u2713 net_pnl_per_contract={cs['net_pnl_per_contract']}")
    print(f"  \u2713 cumulative.avg_win_pnl_per_contract={cs['cumulative']['avg_win_pnl_per_contract']}")


def main():
    tests = [
        test_averages_per_contract_normalization,
        test_averages_per_contract_null_safe,
        test_no_op_at_1c,
        test_cheat_sheet_includes_contracts,
    ]
    fails = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  \u2717 FAIL: {e}")
            fails += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            fails += 1
    print("\n" + "=" * 60)
    if fails == 0:
        print(f"ALL {len(tests)} A-3 HERMES TESTS PASSED")
        return 0
    print(f"{fails} / {len(tests)} tests FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
