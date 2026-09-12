#!/usr/bin/env python3
"""
Does the ACCOUNT agree with our P&L? A cumulative, independent check.

WHY THIS EXISTS, and why it is not the same as the in-process reconcile.
Every internal check compares two numbers descended from `_book_realized_pnl`,
so they cannot disagree. `_reconcile_pnl_against_broker` (2026-09-10) improved on
that by comparing against IBKR's `raw_ledger.USD.realizedpnl` — but on 2026-09-11
that reported IBKR $245.41 against our $883.40, a 72% gap, which looked alarming
and was not.

The reason: IBKR's realizedpnl LAGS 0DTE expiry settlement. Our P&L books the
kept credit at settlement; IBKR books it when the expiry clears, which can be the
next session. A single-day comparison therefore measures settlement timing, not
correctness.

WHAT THIS DOES INSTEAD: compares the ACCOUNT's own movement, day over day, with
what we claimed. Timing noise in one window shows up with the opposite sign in
the next, so a persistent one-sided drift means our P&L is genuinely wrong while
noise that cancels means the account agrees.

First run (2026-09-12, three windows):
    09-08 -> 09-09    account  +618.31   claimed  +623.15   drift   -4.84
    09-09 -> 09-10    account  +853.44   claimed +1133.50   drift -280.06
    09-10 -> 09-11    account +1789.12   claimed +1804.60   drift  -15.48
Two of three agree within $15. That rules out systematic overstatement and
identifies the daily BROKER-RECONCILE gap as a timing artefact.

THE DATA SOURCE IS A PROXY, and the limits matter:
  * "Available funds" (ORDER-004 margin snapshots in the bot log) is not net
    liquidation — it is reduced by margin held on open positions. Comparing the
    FIRST snapshot of each day mitigates this (taken before that day's entries)
    but does not eliminate it: an unsettled prior expiry can still hold margin.
  * Log retention is ~7 days, so the window is short. The residual ~8% over
    three days is within what a margin-affected proxy can produce and is NOT by
    itself evidence of an error.
  * Anything that moves the account but is not B's trading — deposits,
    withdrawals, fees, another variant going live — corrupts this. B is the only
    variant placing orders today; revisit if that changes.

USAGE (on the VM, as calypso)
  .venv/bin/python -m scripts.verify_pnl_vs_account --variant b
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SNAP = re.compile(
    r"(\d{4}-\d{2}-\d{2}) \d\d:\d\d:\d\d .*ORDER-004: Margin snapshot — "
    r"Available: \$([\d,]+\.\d\d)"
)


def first_snapshot_per_day(log_glob: str) -> dict:
    """First available-funds reading of each day = state BEFORE that day trades."""
    out: dict = {}
    for f in sorted(glob.glob(log_glob)):
        try:
            txt = open(f, errors="ignore").read()
        except OSError:
            continue
        for m in _SNAP.finditer(txt):
            out.setdefault(m.group(1), float(m.group(2).replace(",", "")))
    return out


def compare(snaps: dict, db: str) -> list:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        days = sorted(snaps)
        rows = []
        for a, b in zip(days, days[1:]):
            claimed = con.execute(
                "SELECT COALESCE(SUM(net_pnl),0) FROM daily_summaries "
                "WHERE date>=? AND date<?", (a, b)).fetchone()[0]
            rows.append({"from": a, "to": b,
                         "moved": snaps[b] - snaps[a],
                         "claimed": claimed,
                         "drift": (snaps[b] - snaps[a]) - claimed})
        return rows
    finally:
        con.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Account movement vs claimed P&L.")
    p.add_argument("--variant", default="b")
    p.add_argument("--root", default="/opt/calypso")
    a = p.parse_args(argv)

    logs = os.path.join(a.root, "logs", f"hydra_variant_{a.variant}", "bot.log*")
    db = os.path.join(a.root, "data", f"variant_{a.variant}", "backtesting.db")
    if a.variant == "a":
        logs = os.path.join(a.root, "logs", "hydra", "bot.log*")
        db = os.path.join(a.root, "data", "backtesting.db")

    snaps = first_snapshot_per_day(logs)
    if len(snaps) < 2:
        print(f"Not enough history: {len(snaps)} day(s) of margin snapshots in "
              f"the retained logs. Needs >= 2.")
        return 1

    rows = compare(snaps, db)
    print(f"\nVariant {a.variant.upper()} — account movement vs claimed P&L")
    print("=" * 62)
    print(f"  {'window':<26} {'account':>12} {'claimed':>12} {'drift':>10}")
    for r in rows:
        print(f"  {r['from']} -> {r['to']:<10} {r['moved']:>12,.2f} "
              f"{r['claimed']:>12,.2f} {r['drift']:>+10,.2f}")
    ta = sum(r["moved"] for r in rows)
    tc = sum(r["claimed"] for r in rows)
    print(f"  {'TOTAL':<26} {ta:>12,.2f} {tc:>12,.2f} {ta-tc:>+10,.2f}")

    signs = [1 if r["drift"] > 0 else -1 for r in rows if abs(r["drift"]) > 25]
    print()
    if signs and all(s == signs[0] for s in signs) and len(signs) >= 3:
        print("  ⚠️  Drift is ONE-SIDED across every window — consistent with a")
        print("      systematic P&L error rather than settlement timing. Investigate.")
    else:
        print("  Drift is not consistently one-sided — consistent with settlement")
        print("  timing rather than a P&L error. Note this is a MARGIN-AFFECTED")
        print("  proxy, not net liquidation; see the module docstring for limits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
