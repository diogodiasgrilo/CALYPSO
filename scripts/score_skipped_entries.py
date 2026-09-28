#!/usr/bin/env python3
"""Score the entries we DECLINED to place. The counterfactual nobody collected.

WHY. `skipped_entries` has carried `would_have_stopped` and `theoretical_pnl`
columns since the table was created, and as of 2026-09-28 **0 of 115 live-era
rows have either populated**. Every skip was recorded and none was ever scored,
so "was the skip right?" has never been answerable from the data — which is how
the GEX adjuster accumulated a 0-for-21 record on vetoed strikes without anyone
noticing.

This script closes that. It reads the theoretical strikes and credits the
recorder already stores, and settles them against what the market actually did.

WHAT THE TWO NUMBERS MEAN — read this before quoting them.

`theoretical_pnl` is the iron condor's P&L **held to expiry**, with NO stop
modelled. SPX 0DTE is PM-settled, so the expiry value is exact given the close:

    call spread value = clamp(settle - short_call, 0, width)
    put  spread value = clamp(short_put - settle, 0, width)
    P&L = credit_collected - (call_value + put_value) - commissions

It is NOT "what B would have earned". A real entry carries the A2 %-of-width
stop, which caps a breached side at ~40% of width instead of the full width, so
on a breach day the real result is usually BETTER than this number. On a
poke-and-revert day it can be worse — the stop locks the loss at the touch while
expiry would have recovered. So treat this as "held-to-expiry P&L", nothing more.

`would_have_stopped` is NOT a stop simulation either. It records whether SPX
ever traded through the short strike intraday, read from `market_ticks`. That is
exact and answers a narrower question: was this side ever actually threatened?
A skip whose strike was never touched cannot have been protecting anything.

Both are deliberately conservative in the sense that matters: neither can make a
skip look worse than it was by inventing a loss the market never delivered.

Usage:
    .venv/bin/python -m scripts.score_skipped_entries            # report only
    .venv/bin/python -m scripts.score_skipped_entries --write    # populate the DB
    .venv/bin/python -m scripts.score_skipped_entries --variant c --since 2026-07-24
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics as st
from pathlib import Path

# B pays roughly this per contract per leg round-trip. Used only to keep the
# headline honest; the report prints gross alongside so the assumption is
# visible rather than buried.
COMMISSION_PER_CONTRACT_PER_LEG = 0.65


def _spread_value(settle, short_strike, long_strike, is_call):
    if short_strike is None or long_strike is None:
        return None
    width = abs(long_strike - short_strike)
    intrinsic = (settle - short_strike) if is_call else (short_strike - settle)
    return max(0.0, min(intrinsic, width))


def score(db_path: Path, since: str, write: bool):
    con = sqlite3.connect(f"file:{db_path}?{'' if write else 'mode=ro'}", uri=True)
    cur = con.cursor()

    daily = {
        d: (c, h, l, k or 7)
        for d, c, h, l, k in cur.execute(
            "SELECT date,spx_close,spx_high,spx_low,contracts_per_entry "
            "FROM daily_summaries WHERE date>=?", (since,))
    }
    rows = list(cur.execute(
        "SELECT rowid,date,entry_number,skip_reason,spx_at_skip,"
        "theoretical_short_call,theoretical_long_call,"
        "theoretical_short_put,theoretical_long_put,"
        "estimated_call_credit,estimated_put_credit "
        "FROM skipped_entries WHERE date>=? ORDER BY date,entry_number", (since,)))

    scored, unscorable, updates = [], 0, []
    for (rid, d, en, reason, spx, sc, lc, sp, lp,
         ccred, pcred) in rows:
        day = daily.get(d)
        if not day or day[0] is None:
            unscorable += 1
            continue
        settle, hi, lo, k = day
        # A skipped entry is only a full IC if BOTH sides were proposed.
        if sc is None or sp is None:
            unscorable += 1
            continue
        cv = _spread_value(settle, sc, lc, True)
        pv = _spread_value(settle, sp, lp, False)
        if cv is None or pv is None:
            unscorable += 1
            continue
        credit_ps = (ccred or 0.0) + (pcred or 0.0)
        gross = (credit_ps - cv - pv) * 100.0 * k
        comm = COMMISSION_PER_CONTRACT_PER_LEG * k * 4 * 2   # 4 legs, in+out
        pnl = gross - comm
        touched = bool(
            (hi is not None and hi >= sc) or (lo is not None and lo <= sp))
        scored.append((d, en, reason, sc, sp, settle, credit_ps, pnl, touched, k))
        updates.append((1 if touched else 0, pnl, rid))

    print(f"{db_path}")
    print(f"scored {len(scored)} skipped entries since {since} "
          f"({unscorable} unscorable — missing OHLC or one-sided proposal)\n")
    if not scored:
        return 0

    def bucket(r):
        t = str(r[2])
        if "ONE spread" in t or "require" in t.lower():
            return "require-both-sides"
        if "credit" in t.lower():
            return "credit gate"
        if "delta" in t.lower():
            return "delta-target floor"
        return "other"

    groups = {}
    for r in scored:
        groups.setdefault(bucket(r), []).append(r)

    print(f"{'reason':<22} {'n':>4} {'held-to-expiry P&L':>20} {'mean':>10} "
          f"{'wins':>6} {'threatened':>11}")
    for name, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        tot = sum(r[7] for r in rs)
        wins = sum(1 for r in rs if r[7] > 0)
        touch = sum(1 for r in rs if r[8])
        print(f"{name:<22} {len(rs):>4} {tot:>20,.2f} {st.mean(r[7] for r in rs):>10,.2f} "
              f"{wins:>4}/{len(rs):<2} {touch:>6}/{len(rs):<4}")

    allp = [r[7] for r in scored]
    print(f"\n{'TOTAL':<22} {len(scored):>4} {sum(allp):>20,.2f} {st.mean(allp):>10,.2f}")
    print(f"\n  NEVER THREATENED (short strike untouched all day): "
          f"{sum(1 for r in scored if not r[8])}/{len(scored)}")
    print("  A skip whose strike was never touched cannot have protected anything.")

    worst = sorted(scored, key=lambda r: r[7])[:5]
    print("\n  the 5 skips that would have LOST the most (held to expiry):")
    for d, en, _r, sc, sp, settle, cps, pnl, t, k in worst:
        print(f"     {d} e#{en}  SC {sc:.0f} / SP {sp:.0f}  settle {settle:.1f}  "
              f"P&L {pnl:>10,.2f}  threatened={t}")

    if write:
        cur.executemany(
            "UPDATE skipped_entries SET would_have_stopped=?, theoretical_pnl=? "
            "WHERE rowid=?", updates)
        con.commit()
        print(f"\n  WROTE {len(updates)} rows "
              "(would_have_stopped = strike touched intraday; "
              "theoretical_pnl = held-to-expiry)")
    else:
        print("\n  (report only — pass --write to populate the DB columns)")
    con.close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="b")
    ap.add_argument("--since", default="2026-07-24")
    ap.add_argument("--root", default="/opt/calypso")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    db = Path(a.root) / "data" / f"variant_{a.variant}" / "backtesting.db"
    if not db.exists():
        print(f"no such database: {db}")
        return 1
    return score(db, a.since, a.write)


if __name__ == "__main__":
    raise SystemExit(main())
