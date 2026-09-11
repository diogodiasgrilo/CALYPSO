#!/usr/bin/env python3
"""
The counterfactual for skipped entries: would the trades we DIDN'T take have won?

Primarily this answers the long-open GEX question — *the strike adjuster vetoed
more entries than it placed; was that veto worth it?* — but it works for any skip
reason that recorded its strikes.

WHY THIS IS AN OFFLINE SCRIPT, NOT A SETTLEMENT HOOK
----------------------------------------------------
Both inputs are already persisted: the proposed strikes (from 2026-09-11) and the
full intraday `market_ticks` series. So the outcome can be recomputed at ANY
time, retroactively, and re-run whenever the loss model changes. Putting it in
the bot's settlement path would add risk to the trading process to compute
something that was never time-sensitive. `DataRecorder.update_skipped_entry_backtest`
exists for writing the result back and remains available, but nothing here
requires the bot to be involved.

WHAT IS MEASURED vs WHAT IS MODELLED — read this before quoting a number
-----------------------------------------------------------------------
MEASURED (hard facts from market_ticks):
  * whether SPX BREACHED a proposed short strike after the skip time.
    Call side breached if max(SPX after skip) >= short_call.
    Put  side breached if min(SPX after skip) <= short_put.
MODELLED (an assumption, and the weakest link):
  * the dollar outcome. Unbreached -> we keep the estimated credit, which is
    close to measured. Breached -> we model the loss as B's ACTING stop, i.e.
    `pct_of_width x width x 100 x contracts` (A2, default 40%). That is a MODEL:
    the real loss depends on when the stop fired and what the spread cost to
    close, neither of which exists for a trade that never happened.

So treat the BREACH RATE as evidence and the DOLLARS as an estimate. A breach is
also not automatically a loss — SPX can touch a short strike and settle back
inside — which is why both are reported separately rather than collapsed.

THE HISTORICAL SET IS NOT RECOVERABLE. Strikes were only recorded from
2026-09-11; variant B's 95 earlier live-era GEX vetoes have none and never will.
This measures forward only, and says so rather than quietly reporting a small n
as if it were the whole history.

USAGE (on the VM, as calypso)
  .venv/bin/python -m scripts.analyze_skipped_entry_outcomes --variant b
  .venv/bin/python -m scripts.analyze_skipped_entry_outcomes --variant b --reason GEX
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.analysis_eras import LIVE_ERA_SINCE, era_banner  # noqa: E402

#: Strikes were only recorded from this date (commit ec71967). Rows before it
#: carry NULL strikes and are structurally unmeasurable — not "missing data" that
#: might turn up later.
STRIKES_RECORDED_SINCE = "2026-09-11"


def _rows(db, since, reason):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = ("SELECT date, entry_number, skip_time, skip_reason, spx_at_skip, "
         "theoretical_short_call, theoretical_long_call, "
         "theoretical_short_put, theoretical_long_put, "
         "estimated_call_credit, estimated_put_credit "
         "FROM skipped_entries WHERE date >= ?")
    args = [since]
    if reason:
        q += " AND skip_reason LIKE ?"
        args.append(f"%{reason}%")
    try:
        return [dict(r) for r in con.execute(q + " ORDER BY date, entry_number", args)]
    finally:
        con.close()


def _spx_extremes_after(db, date_str, after_time):
    """(min, max) SPX from `after_time` to the end of that day, or (None, None).

    Bounded to AFTER the skip: what SPX did earlier in the session cannot breach
    a position that would have been opened later.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        r = con.execute(
            "SELECT MIN(spx_price), MAX(spx_price) FROM market_ticks "
            "WHERE date(timestamp) = ? AND timestamp >= ? AND spx_price > 0",
            (date_str, after_time or f"{date_str} 00:00:00"),
        ).fetchone()
        return (r[0], r[1]) if r else (None, None)
    finally:
        con.close()


def evaluate(row, lo, hi, *, pct_of_width, contracts):
    """Outcome for one skipped entry. Returns None when unmeasurable."""
    sc, sp = row.get("theoretical_short_call"), row.get("theoretical_short_put")
    if not sc and not sp:
        return None            # strikes never recorded — structurally unmeasurable
    if lo is None or hi is None:
        return None            # no tick coverage after the skip

    call_breach = bool(sc) and hi is not None and hi >= sc
    put_breach = bool(sp) and lo is not None and lo <= sp
    credit = ((row.get("estimated_call_credit") or 0.0)
              + (row.get("estimated_put_credit") or 0.0)) * 100 * contracts

    width = 0.0
    if sc and row.get("theoretical_long_call"):
        width = abs(row["theoretical_long_call"] - sc)
    elif sp and row.get("theoretical_long_put"):
        width = abs(sp - row["theoretical_long_put"])

    if call_breach or put_breach:
        # MODELLED — B's acting A2 stop caps the loss at pct x width x 100 x qty.
        modelled = -(pct_of_width * width * 100 * contracts) if width else None
    else:
        modelled = credit      # close to measured: the credit is kept

    return {
        "date": row["date"], "entry": row["entry_number"],
        "short_call": sc, "short_put": sp, "width": width,
        "spx_lo": lo, "spx_hi": hi,
        "call_breach": call_breach, "put_breach": put_breach,
        "breached": call_breach or put_breach,
        "credit": credit, "modelled_pnl": modelled,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Counterfactual outcomes for skipped entries.")
    p.add_argument("--variant", default="b")
    p.add_argument("--db", default=None)
    p.add_argument("--root", default=".")
    p.add_argument("--reason", default=None, help="substring filter, e.g. 'GEX'")
    p.add_argument("--since", default=LIVE_ERA_SINCE)
    p.add_argument("--pct-of-width", type=float, default=0.40,
                   help="B's acting A2 stop fraction (loss MODEL)")
    p.add_argument("--contracts", type=int, default=7)
    a = p.parse_args(argv)

    db = a.db or (os.path.join(a.root, "data", "backtesting.db") if a.variant == "a"
                  else os.path.join(a.root, "data", f"variant_{a.variant}", "backtesting.db"))
    if not os.path.exists(db):
        print(f"ERROR: no db at {db}", file=sys.stderr)
        return 2

    since = None if str(a.since).lower() in ("all", "none") else a.since
    print(era_banner(since))
    rows = _rows(db, since or "0000-00-00", a.reason)
    print(f"\nskipped entries matched: {len(rows)}"
          + (f"  (reason ~ {a.reason!r})" if a.reason else ""))

    measurable, unmeasurable = [], 0
    for r in rows:
        lo, hi = _spx_extremes_after(db, r["date"], r.get("skip_time"))
        res = evaluate(r, lo, hi, pct_of_width=a.pct_of_width, contracts=a.contracts)
        if res is None:
            unmeasurable += 1
        else:
            measurable.append(res)

    print(f"  measurable (strikes recorded + tick coverage): {len(measurable)}")
    print(f"  UNMEASURABLE (no strikes recorded)           : {unmeasurable}")
    if unmeasurable:
        print(f"  → strikes are only recorded from {STRIKES_RECORDED_SINCE}; earlier")
        print(f"    rows carry NULL strikes and can never be measured.")

    if not measurable:
        print("\nNo measurable skips yet. This measures FORWARD only — come back "
              "after a few sessions.")
        return 0

    print(f"\n  {'date':12} {'e#':>3} {'SC':>7} {'SP':>7} {'SPX lo/hi':>17} "
          f"{'breach':>8} {'modelled $':>11}")
    for m in measurable:
        br = ("CALL" if m["call_breach"] else "") + ("PUT" if m["put_breach"] else "")
        pnl_s = "—" if m["modelled_pnl"] is None else f"{m['modelled_pnl']:,.0f}"
        rng = f"{m['spx_lo']:.1f}/{m['spx_hi']:.1f}"
        print(f"  {m['date']:12} {m['entry']:>3} {str(m['short_call'] or '-'):>7} "
              f"{str(m['short_put'] or '-'):>7} {rng:>17} "
              f"{br or '-':>8} {pnl_s:>11}")

    n = len(measurable)
    br = sum(1 for m in measurable if m["breached"])
    pnl = [m["modelled_pnl"] for m in measurable if m["modelled_pnl"] is not None]
    print("\n" + "=" * 66)
    print(f"  MEASURED : {br}/{n} vetoed entries would have had a short breached "
          f"({br/n*100:.0f}%)")
    if pnl:
        print(f"  MODELLED : net ${sum(pnl):,.2f} over {len(pnl)} entries "
              f"(${sum(pnl)/len(pnl):,.2f}/entry)")
        print(f"             positive => vetoing COST us; negative => vetoing SAVED us")
    print(f"\n  The breach rate is MEASURED. The dollars are MODELLED on a "
          f"{a.pct_of_width:.0%}-of-width")
    print(f"  stop at {a.contracts}c — the real loss depends on when the stop fired and "
          f"what the")
    print(f"  spread cost to close, neither of which exists for a trade never taken.")
    print(f"  A breach is also not automatically a loss: SPX can touch a short and "
          f"settle back inside.")
    if n < 20:
        print(f"\n  ⚠️  n={n}. Directional at best. Do not act on this yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
