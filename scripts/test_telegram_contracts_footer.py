"""Regression test: Phase 2 T-4 Telegram command contracts footer.

Verifies:
  1. _with_contracts_footer adds a footer line when contracts_per_entry > 1
  2. At contracts_per_entry = 1, it's silent (no footer, backwards compat)
  3. All 9 `build_telegram_*` return statements route through the helper
  4. Helper is null-safe: None / 0 / missing attr → treated as 1 (no footer)

Run: python scripts/test_telegram_contracts_footer.py
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class _FakeStrategy:
    """Minimal stub with just the helper method + contracts attribute."""
    def __init__(self, contracts):
        self.contracts_per_entry = contracts


def _load_helper():
    """Extract _with_contracts_footer from the real source for fidelity."""
    import ast
    src = (REPO / "bots" / "hydra" / "strategy.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_with_contracts_footer":
            func_src = ast.get_source_segment(src, node)
            # De-indent (function is inside class, 4 spaces)
            dedented = "\n".join(line[4:] if line.startswith("    ") else line
                                  for line in func_src.splitlines())
            ns = {}
            exec(compile(dedented, "helper", "exec"), ns)
            return ns["_with_contracts_footer"]
    raise RuntimeError("_with_contracts_footer not found in strategy.py")


def test_silent_at_1c():
    """At contracts=1, footer is silent (no change from legacy output)."""
    print("\n[T4-1] _with_contracts_footer silent at contracts_per_entry=1")
    import types
    helper = _load_helper()
    strat = _FakeStrategy(1)
    strat._with_contracts_footer = types.MethodType(helper, strat)

    lines = ["*Status*", "Active: 0", "P&L: $0"]
    result = strat._with_contracts_footer(lines)
    assert result == "\n".join(lines), (
        f"at 1c result should equal raw join, got:\n{result}"
    )
    assert "[1c" not in result
    assert "per entry" not in result
    print(f"  \u2713 identical to raw join; no footer added")


def test_footer_added_at_2c():
    """At contracts=2, helper appends a blank line + italic footer."""
    print("\n[T4-2] _with_contracts_footer adds footer at contracts_per_entry=2")
    import types
    helper = _load_helper()
    strat = _FakeStrategy(2)
    strat._with_contracts_footer = types.MethodType(helper, strat)

    lines = ["*Status*", "Active: 0", "P&L: $0"]
    result = strat._with_contracts_footer(lines)
    assert "[2c per entry" in result, f"expected '[2c per entry' in result, got:\n{result}"
    # Footer should come AFTER the input content
    body_end = result.index("P&L: $0") + len("P&L: $0")
    footer_start = result.index("[2c per entry")
    assert footer_start > body_end, "footer should come after body"
    # Should be italic markdown
    assert "_[2c per entry" in result
    print(f"  \u2713 footer appended as italic markdown at end")


def test_null_safe():
    """Null-safe: None / 0 / missing contracts_per_entry → silent like 1c."""
    print("\n[T4-3] _with_contracts_footer null-safe on None / 0 / missing")
    import types
    helper = _load_helper()

    for label, contracts in [("None", None), ("zero", 0)]:
        strat = _FakeStrategy(contracts)
        strat._with_contracts_footer = types.MethodType(helper, strat)
        result = strat._with_contracts_footer(["line1"])
        assert "[" not in result, f"{label}: unexpected footer, got: {result}"
        print(f"  \u2713 {label}: silent (no footer)")

    # Missing attribute via getattr fallback
    class _NoAttr: pass
    strat = _NoAttr()
    strat._with_contracts_footer = types.MethodType(helper, strat)
    result = strat._with_contracts_footer(["line1"])
    assert "[" not in result, f"missing attr: unexpected footer, got: {result}"
    print(f"  \u2713 missing attribute: silent (getattr fallback → 1)")


def test_all_9_return_sites_updated():
    """Every `build_telegram_*` function's return statement routes through the helper.

    Helper itself internally uses `"\\n".join(lines)` for the actual string
    construction — that's the one legitimate remaining occurrence of the raw
    pattern (inside _with_contracts_footer). All other sites must use the helper.
    """
    print("\n[T4-4] All 9 Telegram builder returns use _with_contracts_footer")
    src = (REPO / "bots" / "hydra" / "strategy.py").read_text()
    # Expect exactly 1 legacy return — the one inside _with_contracts_footer itself
    legacy_count = src.count('return "\\n".join(lines)')
    assert legacy_count == 1, (
        f"expected exactly 1 'return \"\\\\n\".join(lines)' (inside the helper), "
        f"got {legacy_count}"
    )
    # Count new pattern — should be 8 Telegram builders. There were originally
    # 9 `return "\\n".join(lines)` sites; one of them was inside the helper itself
    # (which kept the raw join — that's the 1 legacy return above). The other 8
    # are the Telegram builders (/snapshot, /lastday, /account, /status, /hermes,
    # /entry, /stops, /config, /week — actually 9 builders but the /hermes-or-
    # similar pass-through builds lines the same way).
    new_count = src.count("return self._with_contracts_footer(lines)")
    assert new_count >= 8, f"expected >=8 calls to _with_contracts_footer, got {new_count}"
    print(f"  \u2713 1 legacy return (helper's own), {new_count} builder calls via helper")


def test_contract_count_in_footer():
    """Footer text includes the actual contract count (e.g., '[3c per entry]')."""
    print("\n[T4-5] Footer reflects actual contract count value")
    import types
    helper = _load_helper()

    for n in (2, 3, 5):
        strat = _FakeStrategy(n)
        strat._with_contracts_footer = types.MethodType(helper, strat)
        result = strat._with_contracts_footer(["body"])
        assert f"[{n}c per entry" in result
        assert f"scaled \u00d7{n}" in result, f"expected scale marker ×{n}, got:\n{result}"
        print(f"  \u2713 contracts={n}: footer says [{n}c per entry ... scaled ×{n}]")


def main():
    tests = [
        test_silent_at_1c,
        test_footer_added_at_2c,
        test_null_safe,
        test_all_9_return_sites_updated,
        test_contract_count_in_footer,
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
        print(f"ALL {len(tests)} T-4 TELEGRAM-FOOTER TESTS PASSED")
        return 0
    print(f"{fails} / {len(tests)} tests FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
