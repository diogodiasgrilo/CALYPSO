"""P4.3a: collapse broker-branched methods in base_strategy.py to IBKR-only.

For each named MEICStrategy method, finds the first
`if self.broker is not None:` block and deletes the Saxo code:

  - If the If has an `else:` branch  → delete the else branch (the
    `else:` line through the last else-body statement).
  - Else (the If body ends in `return` and Saxo code follows as
    sibling statements) → delete every statement after the If, to the
    end of the method.

The `if self.broker is not None:` guard itself is LEFT IN PLACE — P4.4
makes `broker` mandatory and removes the now-vacuous guards in one
sweep. This phase only removes Saxo *code*.

Re-parses per method so line numbers stay correct. Verifies the file
still parses. Read the diff before committing.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "bots/hydra/base_strategy.py"


def _is_broker_not_none(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Attribute)
        and test.left.attr == "broker"
        and isinstance(test.left.value, ast.Name)
        and test.left.value.id == "self"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.IsNot)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    )


def _parent_block(func: ast.AST, target: ast.If) -> list:
    """Return the statement list that directly contains `target`."""
    for node in ast.walk(func):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and target in block:
                return block
    raise SystemExit("could not locate the broker-If's parent block")


def collapse(src: str, method: str) -> tuple[str, str]:
    tree = ast.parse(src)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MEICStrategy":
            for item in node.body:
                if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == method):
                    func = item
    if func is None:
        raise SystemExit(f"method not found: {method}")

    # find the broker If anywhere in the method (may be nested in try:)
    broker_if = None
    for stmt in ast.walk(func):
        if isinstance(stmt, ast.If) and _is_broker_not_none(stmt.test):
            broker_if = stmt
            break
    if broker_if is None:
        raise SystemExit(f"{method}: no `if self.broker is not None:`")

    block = _parent_block(func, broker_if)
    idx = block.index(broker_if)

    lines = src.splitlines(keepends=True)

    if broker_if.orelse:
        # delete the else branch: find the `else:` line by scanning up
        # from the first else-body statement.
        first_else = broker_if.orelse[0].lineno
        else_line = None
        for ln in range(first_else - 1, broker_if.body[-1].end_lineno, -1):
            if lines[ln - 1].strip() == "else:":
                else_line = ln
                break
        if else_line is None:
            raise SystemExit(f"{method}: could not locate `else:` line")
        lo, hi = else_line, broker_if.orelse[-1].end_lineno
        mode = "else-branch"
    else:
        # delete every statement after the broker If within its block
        # (the block is the method body, or a try: body if nested)
        if idx + 1 >= len(block):
            return src, f"{method}: nothing after the broker If — skipped"
        lo = broker_if.end_lineno + 1
        hi = block[-1].end_lineno
        mode = "saxo-fallthrough"

    # absorb a single trailing blank line
    if hi < len(lines) and lines[hi].strip() == "":
        hi += 1
    del lines[lo - 1:hi]
    out = "".join(lines)
    ast.parse(out)  # sanity
    return out, f"{method}: deleted {mode} lines {lo}-{hi} ({hi - lo + 1} lines)"


def main() -> None:
    methods = sys.argv[1:]
    if not methods:
        raise SystemExit("give one or more method names")
    src = BASE.read_text()
    for m in methods:
        src, msg = collapse(src, m)
        print(f"  {msg}")
    ast.parse(src)
    BASE.write_text(src)
    print(f"OK — {len(methods)} methods collapsed, file parses.")


if __name__ == "__main__":
    main()
