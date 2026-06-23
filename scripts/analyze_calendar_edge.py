#!/usr/bin/env python3
"""Read a calendar variant's (D / E) dry-run EDGE off its dc_calendar.db.

Answers the MVL-D audit's gating question (its "V1 — edge sanity") from the
forward dry-run record alone: is a debit double calendar + 20%-debit stop
non-negative EV over a meaningful window? Transformed outcomes are reported
separately and EXCLUDED from the verdict (their dry-run P&L is a mid-pricing
artifact — audit §0.3). See bots/hydra/dc_edge.py for the full rationale.

Read-only. Run on the VM from /opt/calypso so the default relative DB path
resolves (the DBs are gitignored, VM-local):

    sudo -u calypso /opt/calypso/.venv/bin/python scripts/analyze_calendar_edge.py
    sudo -u calypso /opt/calypso/.venv/bin/python scripts/analyze_calendar_edge.py --variant e
    sudo -u calypso /opt/calypso/.venv/bin/python scripts/analyze_calendar_edge.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.dc_edge import analyze_calendar_edge, format_edge_report  # noqa: E402

_TITLES = {
    "d": "Strategy D — DC Time Machine",
    "e": "Strategy E — SPY Double Calendar",
}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--variant", choices=["d", "e"], default="d",
                   help="calendar variant to analyze (default: d)")
    p.add_argument("--db", default=None,
                   help="explicit dc_calendar.db path (overrides --variant/--data-dir)")
    p.add_argument("--data-dir", default="data",
                   help="data root holding variant_<v>/dc_calendar.db (default: data)")
    p.add_argument("--min-preliminary", type=int, default=10,
                   help="closed-trade floor below which the verdict is INSUFFICIENT_DATA")
    p.add_argument("--min-confident", type=int, default=30,
                   help="closed-trade floor for a CI-backed confident verdict")
    p.add_argument("--json", action="store_true", help="emit the raw result as JSON")
    args = p.parse_args(argv)

    db_path = args.db or os.path.join(args.data_dir, f"variant_{args.variant}", "dc_calendar.db")
    result = analyze_calendar_edge(
        db_path,
        min_preliminary=args.min_preliminary,
        min_confident=args.min_confident,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_edge_report(result, title=_TITLES.get(args.variant, _TITLES["d"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
