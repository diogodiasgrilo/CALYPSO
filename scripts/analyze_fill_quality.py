#!/usr/bin/env python3
"""
Measure entry fill quality: what we actually paid vs the mid at the moment of
fill, per leg — and how often the order ladder had to escalate to get there.

WHY THIS EXISTS
---------------
On 2026-09-11 variant B switched its two 0%-slippage entry rungs from crossing
the spread to resting on it (`entry_pricing.deliberate_rung_pricing`). The leak
being fixed was measured at ~$1,575 across 65 entries on the LONG legs alone —
about 38% of B's net, ~$79/trading day — caused not by a bias but by an
arithmetic accident: buys used `math.ceil`, and on a $0.05 book the mid is always
a half-tick, so every long limit landed exactly on the ask.

Resting is only better IF THE ORDER STILL FILLS. A passive rung that does not
fill escalates (5% -> 10% -> MARKET), which costs TIME, and an entry that fails
part-way pays the spread TWICE on the unwind. So a report that showed only the
price improvement would be measuring the benefit and hiding the cost. This tool
reports both.

SIGN CONVENTION — the thing most likely to be got backwards
-----------------------------------------------------------
    SHORT leg (we SOLD):    adverse when fill < mid   ->  gap = mid - fill
    LONG  leg (we BOUGHT):  adverse when fill > mid   ->  gap = fill - mid

A POSITIVE gap always means money lost to the spread, on both sides. Dollars are
gap x 100 x contracts.

ESCALATION IS INFERRED, NOT LOGGED
----------------------------------
`time_to_fill_ms` is a proxy. PROGRESSIVE_RETRY_SEQUENCE gives each rung a 30s
timeout (ORDER_TIMEOUT_SECONDS) plus a 1.2s inter-rung delay
(ORDER_RETRY_DELAY_SECONDS), so a fill much past ~31s means rung 1 did not take
it. The buckets below are labelled as the ESTIMATES they are — the column records
the whole leg-in, not a per-rung breakdown, so treat a shift in the distribution
as directional evidence rather than an exact rung count.

USAGE (on the VM, as calypso)
  .venv/bin/python -m scripts.analyze_fill_quality --variant b
  .venv/bin/python -m scripts.analyze_fill_quality --variant b --since 2026-09-11
  .venv/bin/python -m scripts.analyze_fill_quality --variant b --compare 2026-09-11
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.analysis_eras import LIVE_ERA_SINCE, era_banner  # noqa: E402

#: (column stem, is_long). Order is the order they are legged in.
LEGS = [
    ("long_call", True),
    ("long_put", True),
    ("short_call", False),
    ("short_put", False),
]

#: Rung timeout + inter-rung delay, from base_strategy's ORDER_TIMEOUT_SECONDS
#: and ORDER_RETRY_DELAY_SECONDS. Used only to bucket time_to_fill_ms.
_RUNG_S = 30.0 + 1.2


def _rows(db: str, since: str | None, until: str | None = None):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cols = ", ".join(
        ["date", "entry_number", "entry_time", "entry_type", "contracts",
         "total_credit", "time_to_fill_ms"]
        + [f"{s}_fill_price" for s, _ in LEGS]
        + [f"{s}_mid_at_fill" for s, _ in LEGS]
    )
    q = f"SELECT {cols} FROM trade_entries"
    where, args = [], []
    if since:
        where.append("date >= ?"); args.append(since)
    if until:
        where.append("date < ?"); args.append(until)
    if where:
        q += " WHERE " + " AND ".join(where)
    try:
        return [dict(r) for r in con.execute(q + " ORDER BY date, entry_number", tuple(args))]
    finally:
        con.close()


def _leg_gap(row, stem: str, is_long: bool):
    """Dollars lost to the spread on this leg, or None if not quotable.

    Positive = adverse (we paid up / sold down). Negative = price improvement.
    """
    fill = row.get(f"{stem}_fill_price")
    mid = row.get(f"{stem}_mid_at_fill")
    if fill in (None, 0) or mid in (None, 0):
        return None
    per_share = (fill - mid) if is_long else (mid - fill)
    return per_share * 100 * (row.get("contracts") or 1)


def analyze(db: str, since: str | None, until: str | None = None):
    rows = _rows(db, since, until)
    out = {
        "n_entries": len(rows),
        "dates": sorted({r["date"] for r in rows}),
        "legs": {s: {"n": 0, "total": 0.0, "adverse": 0, "improved": 0, "flat": 0}
                 for s, _ in LEGS},
        "total_gap": 0.0,
        "fill_ms": [],
    }
    for r in rows:
        for stem, is_long in LEGS:
            g = _leg_gap(r, stem, is_long)
            if g is None:
                continue
            L = out["legs"][stem]
            L["n"] += 1
            L["total"] += g
            if g > 0.005:
                L["adverse"] += 1
            elif g < -0.005:
                L["improved"] += 1
            else:
                L["flat"] += 1
            out["total_gap"] += g
        t = r.get("time_to_fill_ms")
        if t:
            out["fill_ms"].append(float(t))
    return out


def _buckets(ms_list):
    """Escalation ESTIMATE from leg-in duration. See the module docstring."""
    b = {"rung1 (<31s)": 0, "rung2 (31-62s)": 0, "rung3+ (>62s)": 0}
    for ms in ms_list:
        s = ms / 1000.0
        if s <= _RUNG_S:
            b["rung1 (<31s)"] += 1
        elif s <= _RUNG_S * 2:
            b["rung2 (31-62s)"] += 1
        else:
            b["rung3+ (>62s)"] += 1
    return b


def render(res, title: str) -> str:
    L = [f"\n{title}", "=" * len(title)]
    if not res["n_entries"]:
        return "\n".join(L + ["  no entries in window"])
    d = res["dates"]
    L.append(f"  {res['n_entries']} entries over {len(d)} day(s)  [{d[0]} .. {d[-1]}]")
    L.append("")
    L.append(f"  {'leg':<12} {'n':>4} {'adverse':>8} {'improved':>9} {'flat':>5} "
             f"{'total $':>12} {'$/entry':>9}")
    for stem, _ in LEGS:
        s = res["legs"][stem]
        if not s["n"]:
            continue
        L.append(f"  {stem:<12} {s['n']:>4} {s['adverse']:>8} {s['improved']:>9} "
                 f"{s['flat']:>5} {s['total']:>12,.2f} {s['total']/s['n']:>9,.2f}")
    L.append("")
    per_day = res["total_gap"] / max(1, len(d))
    L.append(f"  TOTAL GAP: ${res['total_gap']:,.2f}   (${per_day:,.2f}/day, "
             f"${res['total_gap']/res['n_entries']:,.2f}/entry)")
    L.append("  positive = money lost to the spread; negative = price improvement")
    if res["fill_ms"]:
        b = _buckets(res["fill_ms"])
        tot = sum(b.values())
        L.append("")
        L.append("  leg-in duration (ESTIMATED rung escalation — see docstring):")
        for k, v in b.items():
            L.append(f"    {k:<16} {v:>4}  ({v/tot*100:>5.1f}%)")
        med = sorted(res["fill_ms"])[len(res["fill_ms"]) // 2] / 1000.0
        L.append(f"    median {med:,.1f}s")
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Entry fill quality: paid vs mid, per leg.")
    p.add_argument("--variant", default="b")
    p.add_argument("--db", default=None)
    p.add_argument("--root", default=".")
    p.add_argument("--since", default=LIVE_ERA_SINCE,
                   help=f"floor rows at this date (default {LIVE_ERA_SINCE}, the "
                        f"live-era boundary). 'all' disables it.")
    p.add_argument("--compare", default=None, metavar="DATE",
                   help="split the window at DATE and show before vs after — use "
                        "the deploy date to measure a pricing change")
    a = p.parse_args(argv)

    db = a.db or (os.path.join(a.root, "data", "backtesting.db") if a.variant == "a"
                  else os.path.join(a.root, "data", f"variant_{a.variant}", "backtesting.db"))
    if not os.path.exists(db):
        print(f"ERROR: no db at {db}", file=sys.stderr)
        return 2
    since = None if str(a.since).lower() in ("all", "none") else a.since
    print(era_banner(since))

    if not a.compare:
        print(render(analyze(db, since), f"Variant {a.variant.upper()} — fill quality"))
        return 0

    # Two EXPLICIT windows rather than subtracting one aggregate from another.
    # The earlier version sliced fill_ms by length to "remove" the after-window,
    # which silently assumed an ordering the query never promised.
    before = analyze(db, since, until=a.compare)
    after = analyze(db, a.compare)

    print(render(before, f"BEFORE {a.compare}"))
    print(render(after, f"ON/AFTER {a.compare}"))
    if before["n_entries"] and after["n_entries"]:
        b_pe = before["total_gap"] / before["n_entries"]
        a_pe = after["total_gap"] / after["n_entries"]
        print(f"\n  PER-ENTRY GAP: ${b_pe:,.2f} -> ${a_pe:,.2f}  "
              f"(delta ${a_pe - b_pe:+,.2f}/entry)")
        print(f"  ⚠️  {after['n_entries']} entries after the split — treat anything "
              f"under ~30 as directional, not conclusive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
