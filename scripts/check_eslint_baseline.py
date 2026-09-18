#!/usr/bin/env python3
"""Ratchet for frontend lint debt: allow what exists, block what's new.

The baseline is now EMPTY (2026-09-18) — the ratchet is fully tight, and any new
error fails CI.

It did not start that way. It carried 15 deferred errors, and clearing them split
three ways rather than one:

    react-refresh/only-export-components   2   GENUINELY FIXED. Two modules
                                               exported a component AND a plain
                                               function, which breaks Fast
                                               Refresh. Split into
                                               groupByMonth.ts and
                                               icEntryModel.ts — better code
                                               regardless of the rule.
    react-hooks/set-state-in-effect       10   NOT defects. Every one is
                                               reset-then-load: setLoading(true)
                                               and clear the old rows before an
                                               async fetch. The extra render the
                                               rule objects to IS the loading
                                               state.
    react-hooks/immutability               3   NOT defects. Local running totals
                                               inside useMemo, whose entire
                                               lifetime is that synchronous
                                               callback, plus one socket event
                                               handler.

The 13 that are not defects became INLINE suppressions carrying their specific
reason at the site, which is strictly better than a JSON file listing them: the
justification is where the next reader will be looking. Contorting correct code
to satisfy an advisory rule would have been the worse trade.

So: compare the CURRENT set of (file, rule) pairs against a committed baseline.
Anything not in the baseline FAILS. Anything in the baseline that has been fixed
is reported as slack to reclaim — a ratchet only tightens.

    python -m scripts.check_eslint_baseline --update   # after fixing some
    python -m scripts.check_eslint_baseline            # the CI gate
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "dashboard" / "frontend"
BASELINE = FRONTEND / ".eslint-baseline.json"


def current_errors() -> set[tuple[str, str]]:
    """{(relative path, ruleId)} for every ERROR (severity 2)."""
    proc = subprocess.run(
        ["npx", "eslint", "src/", "-f", "json"],
        cwd=FRONTEND, capture_output=True, text=True,
    )
    out = proc.stdout.strip()
    if not out:
        print("eslint produced no output:", proc.stderr[:400], file=sys.stderr)
        raise SystemExit(2)
    found: set[tuple[str, str]] = set()
    for f in json.loads(out):
        rel = str(Path(f["filePath"]).relative_to(FRONTEND))
        for m in f.get("messages", []):
            if m.get("severity") == 2:
                found.add((rel, m.get("ruleId") or "unknown"))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true",
                    help="rewrite the baseline from the current state")
    args = ap.parse_args()

    found = current_errors()

    if args.update:
        BASELINE.write_text(json.dumps(
            {"errors": sorted(f"{p}::{r}" for p, r in found)}, indent=2) + "\n")
        print(f"baseline updated: {len(found)} known error(s)")
        return 0

    if not BASELINE.exists():
        print(f"no baseline at {BASELINE} — run with --update once", file=sys.stderr)
        return 2

    known = {tuple(e.split("::", 1))
             for e in json.loads(BASELINE.read_text())["errors"]}
    new = sorted(found - known)
    fixed = sorted(known - found)

    if fixed:
        print(f"  {len(fixed)} baselined error(s) no longer present — "
              f"run --update to tighten the ratchet:")
        for p, r in fixed[:10]:
            print(f"    fixed: {p}  {r}")

    if new:
        print(f"\n  {len(new)} NEW lint error(s) — not in the baseline:")
        for p, r in new:
            print(f"    {p}  {r}")
        print("\n  Fix them, or justify and re-baseline deliberately.")
        return 1

    print(f"  no new lint errors ({len(found)} known, unchanged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
