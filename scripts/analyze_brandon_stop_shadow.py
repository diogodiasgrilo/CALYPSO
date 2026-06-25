#!/usr/bin/env python3
"""Aggregate the Brandon %-of-width stop SHADOW over a variant's full history.

Reconstructs, from the per-tick spread_snapshots, what the %-of-width stop WOULD
have realized on every recorded entry-side and compares it to the ACTUAL
credit+buffer outcome (trade_stops) — so the "flip the stop" decision rests on
the whole record, not a single day. Logic lives in the unit-tested
bots/hydra/stop_shadow.py; this is a thin CLI.

Read-only. Run on the VM from /opt/calypso (the variant DBs are VM-local):

    sudo -u calypso /opt/calypso/.venv/bin/python scripts/analyze_brandon_stop_shadow.py
    sudo -u calypso /opt/calypso/.venv/bin/python scripts/analyze_brandon_stop_shadow.py --variant b
    sudo -u calypso /opt/calypso/.venv/bin/python scripts/analyze_brandon_stop_shadow.py --width-max 10 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.stop_shadow import analyze, format_report  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--variant", default="c", help="variant id (default c — the live Brandon)")
    p.add_argument("--db", default=None, help="explicit backtesting.db path")
    p.add_argument("--data-dir", default="data", help="data root (default: data)")
    p.add_argument("--pcts", default="0.25,0.40,0.50,0.65",
                   help="comma-separated %-of-width thresholds to evaluate")
    p.add_argument("--width-max", type=float, default=None,
                   help="only analyze entry-sides with spread width <= this (pt) — e.g. 10 for narrow")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    db = args.db or os.path.join(args.data_dir, f"variant_{args.variant}", "backtesting.db")
    pcts = tuple(float(x) for x in args.pcts.split(",") if x.strip())
    result = analyze(db, pcts=pcts, width_max=args.width_max)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_report(result, title=f"Variant {args.variant.upper()}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
