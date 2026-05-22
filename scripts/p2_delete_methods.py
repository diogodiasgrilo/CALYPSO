"""P2-full: delete verified-unreachable methods from base_strategy.py.

Usage: python scripts/p2_delete_methods.py <method> [<method> ...]

Removes each named method (def line through end, including any decorator
lines and a single trailing blank line) from `class MEICStrategy` in
bots/hydra/base_strategy.py. Re-parses per deletion so line numbers stay
correct. Verifies the file still parses afterward.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "bots/hydra/base_strategy.py"


def find_method(src: str, name: str) -> tuple[int, int]:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MEICStrategy":
            for item in node.body:
                if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == name):
                    start = item.lineno
                    if item.decorator_list:
                        start = min(d.lineno for d in item.decorator_list)
                    return start, item.end_lineno
    raise SystemExit(f"method not found: {name}")


def main() -> None:
    names = sys.argv[1:]
    if not names:
        raise SystemExit("give one or more method names")
    src = BASE.read_text()
    for name in names:
        lines = src.splitlines(keepends=True)
        lo, hi = find_method(src, name)
        # absorb one trailing blank line if present
        if hi < len(lines) and lines[hi].strip() == "":
            hi += 1
        del lines[lo - 1:hi]
        src = "".join(lines)
        print(f"  deleted {name}  ({hi - lo + 1} lines)")
    # sanity: still parses
    ast.parse(src)
    BASE.write_text(src)
    print(f"OK — {len(names)} methods removed, file parses.")


if __name__ == "__main__":
    main()
