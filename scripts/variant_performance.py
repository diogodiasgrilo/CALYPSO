#!/usr/bin/env python3
"""
What a variant's record ACTUALLY is — with traded days separated from gated ones.

WHY THIS EXISTS, and it is not a style preference. On 2026-09-11 an ad-hoc query
reported variant B at a "49% win rate" over 35 live-paper days. That was wrong in
a way that mattered: **11 of those days placed no trades at all**, and counting
them as non-wins dragged 17-of-24 (71%) down to 17-of-35 (49%). The error was
caught only by disbelieving the arithmetic, which is not a process. A day the bot
DECLINED to trade is not a loss, and no report should be able to imply it is.

THE FOUR DAY TYPES, classified rather than lumped:

  TRADED      entries_placed > 0. The only days a win rate means anything on.
  GATED       0 entries, but skip rows exist — the bot WANTED to trade and its
              own filters stopped it. This is the interesting bucket: on B these
              are ~26% of sessions and the dominant cause is the GEX accel-zone
              veto. Whether that saved or cost money is a separate open question
              (see scripts/analyze_skipped_entry_outcomes.py).
  NO-ATTEMPT  0 entries, 0 skips, but the market traded — a policy day. FOMC T+0
              is the known case: the entry loop never runs, so there is nothing
              to skip and no skip row to find. Absence of skip rows here is
              CORRECT, not a telemetry gap; it looked like one until the
              `economic_events` column explained it.
  CLOSED      no market ticks at all — a holiday. Not a bot decision.

The distinction between GATED and NO-ATTEMPT is the one that carries information:
the first is the bot exercising judgement (auditable, possibly wrong), the second
is policy (not up for debate).

EXECUTION DRAG is reported alongside, because an edge is only what survives it.
B's entry fill gap runs ~$79/day against ~$185/day net — about 30% of the edge —
so a P&L figure quoted without it overstates what the strategy earns.

USAGE (on the VM, as calypso)
  .venv/bin/python -m scripts.variant_performance --variant b
  .venv/bin/python -m scripts.variant_performance --variant b --since all
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.analysis_eras import LIVE_ERA_SINCE, era_banner  # noqa: E402

TRADED, GATED, NO_ATTEMPT, CLOSED = "TRADED", "GATED", "NO-ATTEMPT", "CLOSED"


def classify(con, row) -> tuple[str, str]:
    """(day_type, why). See the module docstring for why these are separate."""
    if (row["entries_placed"] or 0) > 0:
        return TRADED, ""
    d = row["date"]
    ticks = con.execute(
        "SELECT COUNT(*) FROM market_ticks WHERE date(timestamp)=?", (d,)
    ).fetchone()[0]
    if not ticks:
        return CLOSED, "no market data (holiday)"
    reasons = [r[0] or "" for r in con.execute(
        "SELECT skip_reason FROM skipped_entries WHERE date=?", (d,))]
    if not reasons:
        ev = row["economic_events"] or ""
        return NO_ATTEMPT, (f"policy: {ev}" if ev else "entry loop never ran")
    # dominant gate
    buckets = {}
    for x in reasons:
        k = ("GEX accel-zone veto" if "GEX" in x
             else "FOMC blackout" if "FOMC" in x
             else "credit gate" if ("credit gate" in x.lower() or "minimum" in x)
             else "margin" if "margin" in x.lower()
             else "delta/hydration" if "delta" in x.lower()
             else "other")
        buckets[k] = buckets.get(k, 0) + 1
    top = max(buckets.items(), key=lambda kv: kv[1])
    return GATED, f"{len(reasons)} skips, mostly {top[0]} ({top[1]})"


def analyze(db: str, since: str | None) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM daily_summaries"
    args: tuple = ()
    if since:
        q += " WHERE date >= ?"
        args = (since,)
    rows = [dict(r) for r in con.execute(q + " ORDER BY date", args)]
    out = {"days": [], "by_type": {TRADED: [], GATED: [], NO_ATTEMPT: [], CLOSED: []}}
    for r in rows:
        t, why = classify(con, r)
        rec = {"date": r["date"], "type": t, "why": why,
               "net": r["net_pnl"] or 0.0, "gross": r["gross_pnl"] or 0.0,
               "entries": r["entries_placed"] or 0,
               "stops": r["entries_stopped"] or 0}
        out["days"].append(rec)
        out["by_type"][t].append(rec)
    con.close()
    return out


def render(res: dict, title: str) -> str:
    L = [f"\n{title}", "=" * len(title)]
    days = res["days"]
    if not days:
        return "\n".join(L + ["  no days in window"])
    bt = res["by_type"]
    traded = bt[TRADED]
    L.append(f"  {len(days)} sessions   "
             f"{len(traded)} traded · {len(bt[GATED])} gated · "
             f"{len(bt[NO_ATTEMPT])} no-attempt · {len(bt[CLOSED])} closed")
    L.append(f"  [{days[0]['date']} .. {days[-1]['date']}]")

    net_all = sum(d["net"] for d in days)
    L.append("")
    L.append(f"  NET P&L: ${net_all:,.2f}")
    if not traded:
        L.append("  no traded days — nothing further to report")
        return "\n".join(L)

    tn = [d["net"] for d in traded]
    wins = [x for x in tn if x > 0]
    losses = [x for x in tn if x < 0]
    L.append(f"    per SESSION      ${net_all/len(days):>9,.2f}   "
             f"(includes days the bot declined to trade)")
    L.append(f"    per TRADED day   ${sum(tn)/len(traded):>9,.2f}   "
             f"<- the number that describes the strategy")
    L.append("")
    L.append(f"  ON TRADED DAYS ONLY (n={len(traded)}):")
    if wins and losses:
        w, l = st.mean(wins), abs(st.mean(losses))
        L.append(f"    win rate      {len(wins)}/{len(traded)} = {len(wins)/len(traded)*100:.0f}%")
        L.append(f"    avg win       ${w:,.0f}")
        L.append(f"    avg loss      ${-l:,.0f}")
        L.append(f"    payoff ratio  {w/l:.2f}"
                 + ("   (losses BIGGER than wins — premium-selling shape)"
                    if w < l else ""))
        L.append(f"    expectancy    ${(len(wins)/len(traded))*w - (len(losses)/len(traded))*l:,.2f}/traded day")
    L.append(f"    best / worst  ${max(tn):,.0f} / ${min(tn):,.0f}")
    if len(tn) > 1:
        sd = st.pstdev(tn)
        L.append(f"    daily std dev ${sd:,.0f}")
        if sd:
            L.append(f"    Sharpe (ann.) {st.mean(tn)/sd*(252**0.5):.2f}"
                     f"   <- on {len(traded)} days; treat as provisional")
    eq = peak = dd = 0.0
    for d in days:
        eq += d["net"]; peak = max(peak, eq); dd = min(dd, eq - peak)
    L.append(f"    max drawdown  ${dd:,.2f}")
    L.append(f"    entries/stops {sum(d['entries'] for d in traded)}"
             f" / {sum(d['stops'] for d in traded)}")

    if bt[GATED]:
        g = bt[GATED]
        exp = sum(tn) / len(traded)
        L.append("")
        L.append(f"  GATED DAYS — the bot wanted to trade and stopped itself ({len(g)}):")
        for d in g:
            L.append(f"    {d['date']}  {d['why']}")
        L.append(f"    At ${exp:,.2f}/traded day these represent ${exp*len(g):,.2f} "
                 f"({exp*len(g)/sum(tn)*100:.0f}% of traded-day P&L)")
        L.append(f"    — IF they resembled an average day. They may have been the bad")
        L.append(f"      ones; that is exactly what the gates exist to avoid. Unknown")
        L.append(f"      until scripts/analyze_skipped_entry_outcomes.py has data.")

    for t in (NO_ATTEMPT, CLOSED):
        if bt[t]:
            L.append("")
            L.append(f"  {t} ({len(bt[t])}) — not a bot decision:")
            for d in bt[t]:
                L.append(f"    {d['date']}  {d['why']}")
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Variant performance, traded days separated.")
    p.add_argument("--variant", default="b")
    p.add_argument("--db", default=None)
    p.add_argument("--root", default=".")
    p.add_argument("--since", default=LIVE_ERA_SINCE)
    a = p.parse_args(argv)
    db = a.db or (os.path.join(a.root, "data", "backtesting.db") if a.variant == "a"
                  else os.path.join(a.root, "data", f"variant_{a.variant}", "backtesting.db"))
    if not os.path.exists(db):
        print(f"ERROR: no db at {db}", file=sys.stderr)
        return 2
    since = None if str(a.since).lower() in ("all", "none") else a.since
    print(era_banner(since))
    print(render(analyze(db, since), f"Variant {a.variant.upper()} — performance"))
    print("\n  Execution drag is NOT deducted above. Run scripts/analyze_fill_quality.py")
    print("  for what the entry spread costs — on B it has run ~$79/day against")
    print("  ~$185/day net, i.e. roughly 30% of the edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
