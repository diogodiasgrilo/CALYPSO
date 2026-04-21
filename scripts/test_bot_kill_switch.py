"""Regression test: all non-HYDRA bots refuse to start.

Per user directive (2026-04-21), HYDRA is the ONLY bot that runs. All other
bots (Iron Fly, Delta Neutral, Rolling Put Diagonal, MEIC) have a hardcoded
kill-switch in their main.py that exits immediately without touching any
trading state.

This test verifies:
  1. Each disabled bot's _check_disabled_kill_switch() exits with code 2
  2. DISABLED_FOR_SAFETY constant is True in each disabled bot
  3. HYDRA has NO kill-switch (must remain runnable)
  4. MEIC's strategy.py module is still importable (HYDRA parent class)
  5. HYDRA's strategy.py module is still importable

Run: python scripts/test_bot_kill_switch.py
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

DISABLED_BOTS = [
    ("iron_fly_0dte", "bots/iron_fly_0dte/main.py"),
    ("delta_neutral", "bots/delta_neutral/main.py"),
    ("rolling_put_diagonal", "bots/rolling_put_diagonal/main.py"),
    ("meic", "bots/meic/main.py"),
]


def test_disabled_constant_is_true():
    """Every disabled bot has DISABLED_FOR_SAFETY = True at module level.
    Uses AST parsing (not substring search) so the instruction banner's
    text 'Set DISABLED_FOR_SAFETY = False' inside a string literal doesn't
    confuse the check."""
    import ast
    print("\n[K-1] DISABLED_FOR_SAFETY = True in all 4 non-HYDRA bot main.py files")
    for name, path in DISABLED_BOTS:
        src = (REPO / path).read_text()
        tree = ast.parse(src)
        # Find top-level assignments to DISABLED_FOR_SAFETY
        values = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "DISABLED_FOR_SAFETY":
                        if isinstance(node.value, ast.Constant):
                            values.append(node.value.value)
        assert len(values) == 1, (
            f"{name}: expected exactly one top-level DISABLED_FOR_SAFETY = ... "
            f"assignment, found {len(values)}: {values}"
        )
        assert values[0] is True, (
            f"{name}: DISABLED_FOR_SAFETY must be True, got {values[0]}"
        )
        print(f"  \u2713 {name}: DISABLED_FOR_SAFETY = True")


def test_hydra_has_no_kill_switch():
    """HYDRA main.py must NOT have any kill-switch — it's the only bot that runs."""
    print("\n[K-2] HYDRA main.py has no kill-switch references")
    src = (REPO / "bots/hydra/main.py").read_text()
    forbidden = ["DISABLED_FOR_SAFETY", "_check_disabled_kill_switch"]
    for token in forbidden:
        assert token not in src, (
            f"HYDRA main.py has forbidden kill-switch token: {token}. "
            f"HYDRA is the only active bot and must not be disabled."
        )
    print("  \u2713 HYDRA main.py is free of kill-switch (correctly remains runnable)")


def test_kill_switch_exits_with_code_2():
    """Each disabled bot's kill-switch, when invoked, exits with code 2."""
    print("\n[K-3] Kill-switch exits with code 2 (non-zero, non-1)")
    for name, _ in DISABLED_BOTS:
        result = subprocess.run(
            [sys.executable, "-c",
             f"from bots.{name}.main import _check_disabled_kill_switch; "
             f"_check_disabled_kill_switch()"],
            cwd=str(REPO),
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 2, (
            f"{name}: expected exit code 2, got {result.returncode}\n"
            f"stdout: {result.stdout.decode()[:400]}\n"
            f"stderr: {result.stderr.decode()[:400]}"
        )
        # Verify banner appears in output
        combined = (result.stdout + result.stderr).decode()
        assert "DISABLED FOR SAFETY" in combined, (
            f"{name}: kill-switch banner missing from output"
        )
        print(f"  \u2713 {name}: exited with code 2, banner printed")


def test_meic_strategy_module_still_importable():
    """MEIC's strategy.py must remain importable — HYDRA inherits from MEICStrategy.
    The kill-switch must ONLY affect main.py entry point, not the library module."""
    print("\n[K-4] MEIC strategy.py module still importable (HYDRA parent class)")
    result = subprocess.run(
        [sys.executable, "-c",
         "from bots.meic.strategy import MEICStrategy, IronCondorEntry; print('OK')"],
        cwd=str(REPO),
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"MEIC strategy module import failed. exit={result.returncode}\n"
        f"stderr: {result.stderr.decode()[:600]}"
    )
    assert b"OK" in result.stdout
    print("  \u2713 MEICStrategy and IronCondorEntry still importable (not affected)")


def test_hydra_strategy_still_importable():
    """HYDRA must remain fully functional — verify its strategy module loads."""
    print("\n[K-5] HYDRA strategy module imports cleanly")
    result = subprocess.run(
        [sys.executable, "-c",
         "from bots.hydra.strategy import HydraStrategy, HydraIronCondorEntry; print('OK')"],
        cwd=str(REPO),
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"HYDRA strategy module import failed. exit={result.returncode}\n"
        f"stderr: {result.stderr.decode()[:600]}"
    )
    print("  \u2713 HydraStrategy imports cleanly — HYDRA unaffected by kill-switches")


def test_run_bot_also_blocked():
    """Defense in depth: run_bot() also refuses when DISABLED_FOR_SAFETY is True,
    in case something calls run_bot() directly without going through main()."""
    print("\n[K-6] run_bot() also blocked by kill-switch (defense in depth)")
    for name, path in DISABLED_BOTS:
        src = (REPO / path).read_text()
        # Both main() and run_bot() must call the check
        assert src.count("_check_disabled_kill_switch()") >= 2, (
            f"{name}: kill-switch must be called in BOTH main() and run_bot() "
            f"for defense in depth (found {src.count('_check_disabled_kill_switch()')} calls)"
        )
        print(f"  \u2713 {name}: kill-switch invoked in both main() and run_bot()")


def main():
    tests = [
        test_disabled_constant_is_true,
        test_hydra_has_no_kill_switch,
        test_kill_switch_exits_with_code_2,
        test_meic_strategy_module_still_importable,
        test_hydra_strategy_still_importable,
        test_run_bot_also_blocked,
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
        print(f"ALL {len(tests)} KILL-SWITCH TESTS PASSED")
        return 0
    print(f"{fails} / {len(tests)} tests FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
