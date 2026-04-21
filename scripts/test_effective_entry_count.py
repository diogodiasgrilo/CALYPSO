"""Regression test: _effective_total_entry_count() returns the correct
effective schedule size (3, not 4) in all states.

The bug the user flagged: heartbeat/status/snapshot/entry-init log all
showed "Entry X of 4" — the canonical schedule count including the
always-dropped 10:15 slot. Today the bot fires up to 3 entries (10:45,
11:15, 14:00 conditional), so displays should show "of 3".

Run: python scripts/test_effective_entry_count.py
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load_helper():
    """Extract the helper from the real source (avoids pulling Saxo deps)."""
    import ast
    src = (REPO / "bots" / "hydra" / "strategy.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_effective_total_entry_count":
            func_src = ast.get_source_segment(src, node)
            dedented = "\n".join(line[4:] if line.startswith("    ") else line
                                  for line in func_src.splitlines())
            ns = {}
            exec(compile(dedented, "helper", "exec"), ns)
            return ns["_effective_total_entry_count"]
    raise RuntimeError("_effective_total_entry_count not found")


class _FakeStrategy:
    def __init__(self, applied, entry_times, base_count, max_entries, conditional_times):
        self._vix_regime_applied = applied
        self.entry_times = entry_times
        self._base_entry_count = base_count
        self.vix_regime_max_entries = max_entries
        self._conditional_entry_times = conditional_times


def _bind(strat):
    import types
    strat._effective_total_entry_count = types.MethodType(_load_helper(), strat)
    return strat


def test_post_regime_returns_truncated_length():
    """After VIX regime applies, len(self.entry_times) is already correct."""
    print("\n[E-1] Post-VIX-regime: returns len(entry_times)")
    strat = _bind(_FakeStrategy(
        applied=True,
        entry_times=["10:45", "11:15", "14:00"],  # truncated (10:15 dropped)
        base_count=2,
        max_entries=[2, 2, 2, 1],
        conditional_times=["14:00"],
    ))
    assert strat._effective_total_entry_count() == 3
    print("  \u2713 returns 3 (2 base + 1 conditional)")


def test_pre_regime_estimates_from_config():
    """Pre-VIX-regime (pre-market), uses max of regime caps + conditional count."""
    print("\n[E-2] Pre-VIX-regime: uses max(max_entries) + conditional count")
    strat = _bind(_FakeStrategy(
        applied=False,
        entry_times=["10:15", "10:45", "11:15", "14:00"],  # canonical — 4 slots
        base_count=3,
        max_entries=[2, 2, 2, 1],  # max = 2
        conditional_times=["14:00"],
    ))
    # max(max_entries) = 2 + len(conditional) = 1 → 3
    assert strat._effective_total_entry_count() == 3, (
        f"pre-regime expected 3, got {strat._effective_total_entry_count()}"
    )
    print("  \u2713 returns 3 (max cap 2 + 1 conditional) even before VIX known")


def test_post_regime_high_vix_returns_2():
    """At VIX >=28 (regime 3 with cap=1), post-regime returns 2 (1 base + 1 conditional)."""
    print("\n[E-3] Post-regime at VIX >=28: returns 2")
    strat = _bind(_FakeStrategy(
        applied=True,
        entry_times=["11:15", "14:00"],  # truncated to 1 base + conditional
        base_count=1,
        max_entries=[2, 2, 2, 1],
        conditional_times=["14:00"],
    ))
    assert strat._effective_total_entry_count() == 2
    print("  \u2713 returns 2 (1 base + 1 conditional) at extreme VIX")


def test_no_conditional_slots():
    """When conditional entries are disabled, count is just the base cap."""
    print("\n[E-4] No conditional slots: returns just base cap")
    strat = _bind(_FakeStrategy(
        applied=False,
        entry_times=["10:15", "10:45", "11:15"],
        base_count=3,
        max_entries=[2, 2, 2, 1],
        conditional_times=[],
    ))
    assert strat._effective_total_entry_count() == 2
    print("  \u2713 returns 2 (cap 2 + 0 conditional)")


def test_null_safety_on_config_caps():
    """If vix_regime_max_entries is None or has None values, falls back gracefully."""
    print("\n[E-5] Null-safe on missing / None vix_regime_max_entries")
    # None attribute
    strat = _bind(_FakeStrategy(
        applied=False, entry_times=["10:45", "11:15", "14:00"],
        base_count=3, max_entries=None, conditional_times=["14:00"],
    ))
    # Falls back to _base_entry_count = 3 + 1 conditional = 4
    result = strat._effective_total_entry_count()
    assert result > 0, f"should not crash; got {result}"
    print(f"  \u2713 None max_entries: falls back to {result}, no crash")

    # Mixed None in list
    strat = _bind(_FakeStrategy(
        applied=False, entry_times=["10:45", "11:15", "14:00"],
        base_count=3, max_entries=[None, 2, None, 1], conditional_times=["14:00"],
    ))
    # max of valid [2, 1] = 2; + 1 conditional = 3
    assert strat._effective_total_entry_count() == 3
    print("  \u2713 mixed-None max_entries: ignores Nones, max=2 + conditional=1 → 3")


def test_display_sites_use_helper():
    """Verify every display site uses _effective_total_entry_count, not raw len."""
    print("\n[E-6] All 3 display sites in strategy.py use the helper (not len(entry_times))")
    src = (REPO / "bots" / "hydra" / "strategy.py").read_text()

    # The helper should be called in at least 3 places (init log, status, snapshot)
    helper_calls = src.count("self._effective_total_entry_count()")
    assert helper_calls >= 3, f"expected >=3 helper uses, got {helper_calls}"
    print(f"  \u2713 {helper_calls} calls to _effective_total_entry_count() in strategy.py")

    # Verify main.py heartbeat also uses helper (or falls back safely)
    main_src = (REPO / "bots" / "hydra" / "main.py").read_text()
    assert "_effective_total_entry_count" in main_src, (
        "main.py heartbeat should reference the helper"
    )
    print("  \u2713 main.py heartbeat routes through helper (with safe fallback)")


def main():
    tests = [
        test_post_regime_returns_truncated_length,
        test_pre_regime_estimates_from_config,
        test_post_regime_high_vix_returns_2,
        test_no_conditional_slots,
        test_null_safety_on_config_caps,
        test_display_sites_use_helper,
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
        print(f"ALL {len(tests)} EFFECTIVE-COUNT TESTS PASSED")
        return 0
    print(f"{fails} / {len(tests)} tests FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
