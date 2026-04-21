"""V-1 unit test for the 2-contract scaling plan.

Verifies the core invariant: stop_level(contracts=N) == N * stop_level(contracts=1)
for every HYDRA entry type.

Run locally:
    python scripts/test_2contract_stop_invariants.py

Exits 0 on success, 1 on any ratio mismatch. No Saxo/IO required.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# Add repo root to path so `bots.hydra` imports work when run as script.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# Minimal synthetic entry object — mimics HydraIronCondorEntry fields used by
# _calculate_stop_levels_hydra + _get_effective_stop_level. We don't import the
# real dataclass because it pulls Saxo client dependencies.
@dataclass
class FakeEntry:
    entry_number: int = 1
    contracts: int = 1
    call_only: bool = False
    put_only: bool = False
    call_spread_credit: float = 0.0
    put_spread_credit: float = 0.0
    call_side_stop: float = 0.0
    put_side_stop: float = 0.0
    override_reason: Optional[str] = None
    entry_time: Optional[datetime] = None

    @property
    def total_credit(self) -> float:
        return self.call_spread_credit + self.put_spread_credit


# Minimal synthetic strategy — only the fields _calculate_stop_levels_hydra reads.
class FakeStrategy:
    def __init__(self, contracts_per_entry: int, *, call_buf_pc=0.75, put_buf_pc=1.75,
                 theo_put_pc=2.60, decay_mult: Optional[float] = None, decay_hours: Optional[float] = None):
        self.contracts_per_entry = contracts_per_entry
        self.call_stop_buffer = call_buf_pc * 100         # per-contract $ × 100 multiplier
        self.put_stop_buffer = put_buf_pc * 100
        self.downday_theoretical_put_credit = theo_put_pc * 100
        self.buffer_decay_start_mult = decay_mult
        self.buffer_decay_hours = decay_hours

    # Import the actual functions via runtime compile — avoids importing the whole
    # HydraStrategy module (which pulls Saxo deps).
    pass


def _copy_func_from_hydra(func_name: str):
    """Extract a method body from bots/hydra/strategy.py and rebind it to FakeStrategy.

    Simpler alternative: read the function source, compile via exec with FakeStrategy
    as the class, then grab the method. We do this narrow approach because importing
    HydraStrategy directly drags in Saxo/Google Sheets dependencies the test doesn't
    need.
    """
    import ast
    src_path = REPO_ROOT / "bots" / "hydra" / "strategy.py"
    src = src_path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            # Extract just this function
            func_src = ast.get_source_segment(src, node)
            # Remove leading indentation (function is inside class, so 4-space indented)
            dedented = "\n".join(line[4:] if line.startswith("    ") else line
                                  for line in func_src.splitlines())
            # Build a fake logger that ignores everything
            ns = {
                "logger": _SilentLogger(),
                "getattr": getattr,
                "max": max,
                "min": min,
                "datetime": datetime,
                "timedelta": timedelta,
                "get_us_market_time": lambda: datetime.now(),
            }
            exec(compile(dedented, str(src_path), "exec"), ns)
            return ns[func_name]
    raise RuntimeError(f"Function {func_name} not found in {src_path}")


class _SilentLogger:
    def info(self, *a, **k): pass
    def debug(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def critical(self, *a, **k): pass
    def error(self, *a, **k): pass


def _bind_methods(strat):
    """Attach extracted functions as bound methods on the fake strategy."""
    import types
    calc_func = _copy_func_from_hydra("_calculate_stop_levels_hydra")
    eff_func = _copy_func_from_hydra("_get_effective_stop_level")
    strat._calculate_stop_levels_hydra = types.MethodType(calc_func, strat)
    strat._get_effective_stop_level = types.MethodType(eff_func, strat)
    return strat


def calc_stop(contracts: int, entry: FakeEntry) -> tuple[float, float]:
    """Run _calculate_stop_levels_hydra on a fake entry, return (call_stop, put_stop)."""
    strat = _bind_methods(FakeStrategy(contracts))
    entry.contracts = contracts
    strat._calculate_stop_levels_hydra(entry)
    return (entry.call_side_stop, entry.put_side_stop)


def calc_effective_stop(contracts: int, entry: FakeEntry, side: str,
                        entry_time: datetime) -> float:
    """Run _get_effective_stop_level with MKT-042 decay active."""
    strat = _bind_methods(FakeStrategy(
        contracts,
        decay_mult=2.50,
        decay_hours=4.0,
    ))
    entry.contracts = contracts
    entry.entry_time = entry_time

    # Compute the base stop first
    strat._calculate_stop_levels_hydra(entry)
    return strat._get_effective_stop_level(entry, side)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_full_ic_scaling():
    """Full IC: credit doubles at 2c, buffer must also double."""
    print("\n[Test 1] Full IC scaling")
    # Fresh entry per call (calc_stop mutates)
    e1 = FakeEntry(call_spread_credit=400.0, put_spread_credit=400.0)
    e2 = FakeEntry(call_spread_credit=800.0, put_spread_credit=800.0)

    call_1c, put_1c = calc_stop(1, e1)
    call_2c, put_2c = calc_stop(2, e2)

    # Expected: 1c call stop = 800 (total) + 75 (call_buf) = 875
    #           2c call stop = 1600 (total × 2) + 150 (call_buf × 2) = 1750
    #           ratio 2.000
    ratio_call = call_2c / call_1c
    ratio_put = put_2c / put_1c
    print(f"  1c: call=${call_1c:.2f}, put=${put_1c:.2f}")
    print(f"  2c: call=${call_2c:.2f}, put=${put_2c:.2f}")
    print(f"  ratio: call={ratio_call:.3f}, put={ratio_put:.3f}")

    assert abs(ratio_call - 2.0) < 0.001, f"Full IC call ratio {ratio_call:.4f} != 2.000"
    assert abs(ratio_put - 2.0) < 0.001, f"Full IC put ratio {ratio_put:.4f} != 2.000"
    assert call_1c == 875.0, f"Full IC call 1c expected $875, got ${call_1c}"
    assert call_2c == 1750.0, f"Full IC call 2c expected $1750, got ${call_2c}"
    print("  ✓ full IC scaling correct")


def test_call_only_scaling():
    """Call-only (MKT-040): credit + theo_put × n + buffer × n."""
    print("\n[Test 2] Call-only scaling (MKT-040)")
    e1 = FakeEntry(call_only=True, call_spread_credit=150.0)
    e2 = FakeEntry(call_only=True, call_spread_credit=300.0)

    call_1c, _ = calc_stop(1, e1)
    call_2c, _ = calc_stop(2, e2)

    # Expected: 1c = 150 + 260 + 75 = 485
    #           2c = 300 + 520 + 150 = 970
    ratio = call_2c / call_1c
    print(f"  1c: call=${call_1c:.2f}")
    print(f"  2c: call=${call_2c:.2f}")
    print(f"  ratio: {ratio:.3f}")

    assert abs(ratio - 2.0) < 0.001, f"Call-only ratio {ratio:.4f} != 2.000"
    assert call_1c == 485.0, f"Call-only 1c expected $485, got ${call_1c}"
    assert call_2c == 970.0, f"Call-only 2c expected $970, got ${call_2c}"
    print("  ✓ call-only scaling correct")


def test_put_only_scaling():
    """Put-only (MKT-039): credit + put_buffer × n."""
    print("\n[Test 3] Put-only scaling (MKT-039)")
    e1 = FakeEntry(put_only=True, put_spread_credit=200.0)
    e2 = FakeEntry(put_only=True, put_spread_credit=400.0)

    _, put_1c = calc_stop(1, e1)
    _, put_2c = calc_stop(2, e2)

    # Expected: 1c = 200 + 175 = 375
    #           2c = 400 + 350 = 750
    ratio = put_2c / put_1c
    print(f"  1c: put=${put_1c:.2f}")
    print(f"  2c: put=${put_2c:.2f}")
    print(f"  ratio: {ratio:.3f}")

    assert abs(ratio - 2.0) < 0.001, f"Put-only ratio {ratio:.4f} != 2.000"
    assert put_1c == 375.0, f"Put-only 1c expected $375, got ${put_1c}"
    assert put_2c == 750.0, f"Put-only 2c expected $750, got ${put_2c}"
    print("  ✓ put-only scaling correct")


def test_min_stop_floor_scaling():
    """MIN_STOP_LEVEL floor ($50 per contract) must scale with contracts."""
    print("\n[Test 4] MIN_STOP_LEVEL floor scaling")
    # Put-only with near-zero credit — should engage the floor
    e1 = FakeEntry(put_only=True, put_spread_credit=10.0)
    e2 = FakeEntry(put_only=True, put_spread_credit=20.0)

    _, put_1c = calc_stop(1, e1)
    _, put_2c = calc_stop(2, e2)

    # 1c: credit clamped to 50 (MIN_STOP_LEVEL), then + 175 buffer = 225
    # 2c: credit clamped to 100 (MIN_STOP_LEVEL × 2), then + 350 buffer = 450
    ratio = put_2c / put_1c
    print(f"  1c: put=${put_1c:.2f}")
    print(f"  2c: put=${put_2c:.2f}")
    print(f"  ratio: {ratio:.3f}")

    assert abs(ratio - 2.0) < 0.001, f"MIN_STOP_LEVEL scaling ratio {ratio:.4f} != 2.000"
    print("  ✓ MIN_STOP_LEVEL floor scales with contracts")


def test_mkt042_buffer_decay():
    """MKT-042: decayed stop must also scale 2× at 2 contracts."""
    print("\n[Test 5] MKT-042 buffer decay scaling")
    entry_time = datetime.now() - timedelta(hours=2.0)  # mid-decay

    e1 = FakeEntry(call_spread_credit=400.0, put_spread_credit=400.0)
    e2 = FakeEntry(call_spread_credit=800.0, put_spread_credit=800.0)

    eff_call_1c = calc_effective_stop(1, e1, "call", entry_time)
    eff_call_2c = calc_effective_stop(2, e2, "call", entry_time)
    eff_put_1c = calc_effective_stop(1, e1, "put", entry_time)
    eff_put_2c = calc_effective_stop(2, e2, "put", entry_time)

    ratio_call = eff_call_2c / eff_call_1c
    ratio_put = eff_put_2c / eff_put_1c
    print(f"  1c effective: call=${eff_call_1c:.2f}, put=${eff_put_1c:.2f}")
    print(f"  2c effective: call=${eff_call_2c:.2f}, put=${eff_put_2c:.2f}")
    print(f"  ratio: call={ratio_call:.3f}, put={ratio_put:.3f}")

    assert abs(ratio_call - 2.0) < 0.001, f"MKT-042 call decay ratio {ratio_call:.4f} != 2.000"
    assert abs(ratio_put - 2.0) < 0.001, f"MKT-042 put decay ratio {ratio_put:.4f} != 2.000"
    print("  ✓ MKT-042 buffer decay scales correctly mid-decay")


def test_mkt042_decay_at_zero_and_full():
    """MKT-042 at t=0 (max decay_factor=1) and t=beyond (decay_factor=0) both scale."""
    print("\n[Test 6] MKT-042 decay at boundaries")
    for label, offset_hours in [("start (full decay)", 0.0), ("end (no decay)", 5.0)]:
        entry_time = datetime.now() - timedelta(hours=offset_hours)
        e1 = FakeEntry(call_spread_credit=400.0, put_spread_credit=400.0)
        e2 = FakeEntry(call_spread_credit=800.0, put_spread_credit=800.0)

        eff_1c = calc_effective_stop(1, e1, "call", entry_time)
        eff_2c = calc_effective_stop(2, e2, "call", entry_time)
        ratio = eff_2c / eff_1c
        print(f"  {label}: 1c=${eff_1c:.2f}, 2c=${eff_2c:.2f}, ratio={ratio:.3f}")
        assert abs(ratio - 2.0) < 0.001, f"Decay boundary {label} ratio {ratio:.4f} != 2.000"
    print("  ✓ MKT-042 decay scales at both boundaries")


def test_1c_behavior_unchanged():
    """Sanity: at contracts_per_entry=1, everything should match current production values."""
    print("\n[Test 7] 1c regression (must match pre-fix values)")
    e = FakeEntry(call_spread_credit=400.0, put_spread_credit=400.0)
    call, put = calc_stop(1, e)
    # Expected values at 1c before and after the fix:
    #   full IC call stop = 800 + 75 = 875
    #   full IC put stop  = 800 + 175 = 975
    assert call == 875.0, f"1c regression: call stop expected $875, got ${call}"
    assert put == 975.0, f"1c regression: put stop expected $975, got ${put}"
    print(f"  full IC @ 1c: call=${call}, put=${put}  ✓ unchanged")

    e = FakeEntry(call_only=True, call_spread_credit=150.0)
    call, _ = calc_stop(1, e)
    assert call == 485.0, f"1c call-only regression: expected $485, got ${call}"
    print(f"  call-only @ 1c: call=${call}  ✓ unchanged")

    e = FakeEntry(put_only=True, put_spread_credit=200.0)
    _, put = calc_stop(1, e)
    assert put == 375.0, f"1c put-only regression: expected $375, got ${put}"
    print(f"  put-only @ 1c: put=${put}  ✓ unchanged")


def main():
    tests = [
        test_full_ic_scaling,
        test_call_only_scaling,
        test_put_only_scaling,
        test_min_stop_floor_scaling,
        test_mkt042_buffer_decay,
        test_mkt042_decay_at_zero_and_full,
        test_1c_behavior_unchanged,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ FAIL: {e}")
            failures += 1
        except Exception as e:
            print(f"  ✗ ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failures += 1

    print("\n" + "=" * 60)
    if failures == 0:
        print(f"ALL {len(tests)} TESTS PASSED — stop-level invariants hold at 2 contracts")
        return 0
    print(f"{failures} / {len(tests)} tests FAILED — DO NOT FLIP TO 2 CONTRACTS")
    return 1


if __name__ == "__main__":
    sys.exit(main())
