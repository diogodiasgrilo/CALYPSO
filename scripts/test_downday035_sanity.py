#!/usr/bin/env python3
"""Boundary-condition sanity test for Downday-035 threshold logic.

Tests _check_conditional_downday_filter() across exact boundary values.
Uses a bare-minimum mock instead of full strategy init to avoid Saxo auth.
"""
import sys
sys.path.insert(0, "/opt/calypso")

from unittest.mock import MagicMock

# Import HydraStrategy's method via direct import
from bots.hydra.strategy import HydraStrategy


class MockStrategy:
    """Minimal mock that satisfies _check_conditional_downday_filter()'s attribute needs."""
    pass


def make_strategy(threshold_pct: float, spx_open: float, current_price: float,
                  enabled: bool = True):
    s = MockStrategy()
    s.downday_callonly_conditional_enabled = enabled
    s.conditional_downday_threshold_pct = threshold_pct
    s.market_data = MagicMock()
    s.market_data.spx_open = spx_open
    s.current_price = current_price
    # Bind the method from the real class
    s._check_conditional_downday_filter = HydraStrategy._check_conditional_downday_filter.__get__(s)
    return s


def test(label, threshold, spx_open, current, expected):
    s = make_strategy(threshold, spx_open, current)
    result = s._check_conditional_downday_filter()
    pct = (current - spx_open) / spx_open * 100
    status = "PASS" if result == expected else "FAIL"
    print(f"  [{status}] {label}")
    print(f"         spx_open={spx_open}  current={current}  change={pct:+.4f}%")
    print(f"         threshold=-{threshold*100:.3f}%  expected={expected}  got={result}")
    return result == expected


print("=" * 70)
print("DOWNDAY-035 BOUNDARY CONDITION TESTS")
print("=" * 70)
print()
print("Threshold: -0.25% (conditional_downday_threshold_pct=0.0025)")
print("Convention: strict less-than (change < -threshold triggers)")
print()

passed = 0
total = 0

# Test 1: exactly at -0.25% should NOT trigger (strict)
total += 1
if test("exactly -0.25% (boundary, strict<)", 0.0025, 6700.0, 6683.25, False):
    passed += 1

# Test 2: slightly below -0.25% should trigger
total += 1
if test("at -0.26% (just past boundary)", 0.0025, 6700.0, 6682.58, True):
    passed += 1

# Test 3: -0.30% should trigger
total += 1
if test("at -0.30% (clear trigger)", 0.0025, 6700.0, 6679.90, True):
    passed += 1

# Test 4: -0.20% should NOT trigger
total += 1
if test("at -0.20% (below threshold)", 0.0025, 6700.0, 6686.60, False):
    passed += 1

# Test 5: -0.01% should NOT trigger
total += 1
if test("at -0.01% (essentially flat)", 0.0025, 6700.0, 6699.33, False):
    passed += 1

# Test 6: +0.30% (up day) should NOT trigger
total += 1
if test("at +0.30% (up day, wrong direction)", 0.0025, 6700.0, 6720.10, False):
    passed += 1

# Test 7: 0% exactly should NOT trigger
total += 1
if test("at 0.00% (exactly flat)", 0.0025, 6700.0, 6700.00, False):
    passed += 1

# Test 8: -1.00% (strong down day) should trigger
total += 1
if test("at -1.00% (strong down)", 0.0025, 6700.0, 6633.00, True):
    passed += 1

# Test 9: disabled flag → never triggers
total += 1
s = make_strategy(0.0025, 6700.0, 6600.0, enabled=False)
result = s._check_conditional_downday_filter()
if result == False:
    print(f"  [PASS] disabled flag at -1.5% → should not trigger")
    passed += 1
else:
    print(f"  [FAIL] disabled flag at -1.5% should not trigger, got {result}")

# Test 10: missing spx_open → returns False with warning
total += 1
s = make_strategy(0.0025, 0, 6600.0, enabled=True)
result = s._check_conditional_downday_filter()
if result == False:
    print(f"  [PASS] missing spx_open → returns False safely")
    passed += 1
else:
    print(f"  [FAIL] missing spx_open should return False, got {result}")

# Test 11: missing current_price → returns False
total += 1
s = make_strategy(0.0025, 6700.0, 0, enabled=True)
result = s._check_conditional_downday_filter()
if result == False:
    print(f"  [PASS] missing current_price → returns False safely")
    passed += 1
else:
    print(f"  [FAIL] missing current_price should return False, got {result}")

# Test 12: negative spx_open (invalid) → returns False
total += 1
s = make_strategy(0.0025, -100, 6600.0, enabled=True)
result = s._check_conditional_downday_filter()
if result == False:
    print(f"  [PASS] negative spx_open → returns False safely")
    passed += 1
else:
    print(f"  [FAIL] negative spx_open should return False, got {result}")

# Test 13: custom threshold 0.50%
total += 1
s = make_strategy(0.005, 6700.0, 6670.0, enabled=True)  # -0.45%
result = s._check_conditional_downday_filter()
if result == False:
    print(f"  [PASS] -0.45% with 0.50% threshold → should not trigger")
    passed += 1
else:
    print(f"  [FAIL] -0.45% with 0.50% threshold should not trigger, got {result}")

total += 1
s = make_strategy(0.005, 6700.0, 6660.0, enabled=True)  # -0.60%
result = s._check_conditional_downday_filter()
if result == True:
    print(f"  [PASS] -0.60% with 0.50% threshold → should trigger")
    passed += 1
else:
    print(f"  [FAIL] -0.60% with 0.50% threshold should trigger, got {result}")

print()
print("=" * 70)
print(f"RESULTS: {passed}/{total} passed")
print("=" * 70)

if passed == total:
    print("\n✓ All boundary conditions behave correctly.")
    sys.exit(0)
else:
    print(f"\n✗ {total - passed} test(s) failed — review before deploying.")
    sys.exit(1)
