"""P2-full helper: verify the unreachable-method deletion set with AST.

Reachability analysis (2026-05-21) flagged 40 methods of `MEICStrategy`
in bots/hydra/base_strategy.py as unreachable. This script confirms each
is safe to delete by the override-shadowing rule:

  - A base method that IS overridden by HydraStrategy / BrandonHydraStrategy
    is "shadowed": every `self.X()` on the real instance dispatches to the
    override, so the base def is dead — UNLESS some override calls
    `super().X()` that resolves to the base.
  - A base method that is NOT overridden is dead only if it has no real
    call site outside the dead set.

Read-only. Prints a report; deletes nothing.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = REPO / "bots/hydra/base_strategy.py"
HYDRA = REPO / "bots/hydra/strategy.py"
BRANDON = REPO / "bots/hydra/brandon/strategy.py"

DEAD = [
    "_batch_update_entry_prices", "_calculate_strikes",
    "_check_hourly_reconciliation", "_check_state_consistency",
    "_check_stop_losses", "_get_effective_stop_level",
    "_get_vix_adjusted_spread_width", "_initiate_entry",
    "_is_daily_loss_limit_reached", "_log_entry", "_log_safety_event",
    "_parse_entry_times", "_process_expired_credits",
    "_recover_positions_from_saxo", "_register_position",
    "_reset_for_new_day", "_save_state_to_disk",
    "check_after_hours_settlement", "log_account_summary",
    "log_performance_metrics",
    "_handle_missing_positions", "_check_if_position_merged",
    "_calculate_stop_levels", "_check_minimum_credit_gate",
    "_validate_entry_credit", "_is_clock_reliable",
    "_recover_from_state_file_uics", "_reconstruct_entry_from_positions",
    "_group_positions_by_entry", "_parse_spx_option_position",
    "_update_entry_prices", "_extract_mid_price",
    "_simulate_entry_prices", "_calculate_side_pnl", "_get_option_price",
    "handle_price_update", "_extract_price_from_ws",
    "update_ws_price_cache", "_get_cached_price",
    "get_dashboard_metrics_safe",
]


def class_methods(path: Path, classname: str) -> dict[str, tuple[int, int]]:
    tree = ast.parse(path.read_text())
    out: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == classname:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[item.name] = (item.lineno, item.end_lineno)
    return out


def call_sites(path: Path):
    """Yield (method_name, lineno, is_super) for every `self.X()` /
    `super().X()` / `obj.X()` attribute *call* — comments excluded (AST)."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func
            is_super = (
                isinstance(attr.value, ast.Call)
                and isinstance(attr.value.func, ast.Name)
                and attr.value.func.id == "super"
            )
            yield attr.attr, attr.lineno, is_super
    # also bare attribute references passed as callbacks (not Call)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self":
                yield ("&ref:" + node.attr, node.lineno, False)


def main() -> None:
    base_m = class_methods(BASE, "MEICStrategy")
    hydra_m = class_methods(HYDRA, "HydraStrategy")
    brandon_m = class_methods(BRANDON, "BrandonHydraStrategy")

    print("=== method ranges (MEICStrategy) ===")
    dead_ranges = {}
    for name in DEAD:
        if name not in base_m:
            print(f"  MISSING: {name}")
            continue
        dead_ranges[name] = base_m[name]
    total = sum(hi - lo + 1 for lo, hi in dead_ranges.values())
    print(f"  {len(dead_ranges)} methods, {total} lines\n")

    spans = sorted(dead_ranges.values())

    def in_dead(line: int) -> bool:
        return any(lo <= line <= hi for lo, hi in spans)

    # collect call sites per file
    base_calls = list(call_sites(BASE))
    hydra_calls = list(call_sites(HYDRA))
    brandon_calls = list(call_sites(BRANDON))

    print("=== per-method verdict ===")
    unsafe = []
    for name in DEAD:
        if name not in dead_ranges:
            continue
        overridden_in = []
        if name in hydra_m:
            overridden_in.append("HydraStrategy")
        if name in brandon_m:
            overridden_in.append("BrandonHydraStrategy")

        # super() calls to this name — which class do they resolve to?
        super_hits = []
        for attr, ln, is_super in hydra_calls:
            if attr == name and is_super:
                super_hits.append(f"strategy.py:{ln} (super→MEICStrategy=BASE)")
        for attr, ln, is_super in brandon_calls:
            if attr == name and is_super:
                # Brandon super = HydraStrategy; resolves to base only if Hydra lacks it
                tgt = "HydraStrategy" if name in hydra_m else "MEICStrategy=BASE"
                super_hits.append(f"brandon/strategy.py:{ln} (super→{tgt})")
        for attr, ln, is_super in base_calls:
            if attr == name and is_super and not in_dead(ln):
                super_hits.append(f"base_strategy.py:{ln} (super→grandparent)")

        base_to_base = any(
            "BASE" in s for s in super_hits
        )

        if overridden_in:
            if base_to_base:
                verdict = "UNSAFE — super() resolves to BASE"
                unsafe.append(name)
            else:
                verdict = f"SAFE (shadowed by {', '.join(overridden_in)})"
        else:
            # not overridden — any real call site outside dead spans?
            live = []
            for attr, ln, _ in base_calls:
                if attr == name and not in_dead(ln):
                    live.append(f"base:{ln}")
            for attr, ln, _ in hydra_calls:
                if attr == name:
                    live.append(f"hydra:{ln}")
            for attr, ln, _ in brandon_calls:
                if attr == name:
                    live.append(f"brandon:{ln}")
            if live:
                verdict = f"UNSAFE — live callers: {live}"
                unsafe.append(name)
            else:
                verdict = "SAFE (no override, no live caller)"
        sh = f"  [super: {super_hits}]" if super_hits else ""
        print(f"  {name:40s} {verdict}{sh}")

    print()
    if unsafe:
        print(f"!!! {len(unsafe)} UNSAFE — do NOT delete: {unsafe}")
    else:
        print("ALL 40 SAFE TO DELETE.")


if __name__ == "__main__":
    main()
