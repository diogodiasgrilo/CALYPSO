#!/usr/bin/env python3
"""Repair variant `bl`'s 2026-10-14 record, corrupted by a day-stale settlement SPX.

WHAT HAPPENED. `_settlement_spx_level()` returned **7805.45** at 21:45 ET when
2026-10-14's real close was **7720.97** (7805.45 sits beside 10-13's close of
7804.40 — a prior-session frozen quote). Four call sides, all 25-75pt OTM, were
each booked as a ~$3,400 ITM loss instead of a worthless expiry:

    Entry #1 call side settled ITM (SPX=7805.45 vs short 7780, 5.00pt
    intrinsic): booking $-3395.00 (credit $105.00 - settlement $3500.00)

The code defect is fixed in `7b4d833` (the settlement level is now corroborated
against the recorded intraday close). That fix is NOT retroactive, and the
metrics self-heal re-derives from `daily_summaries`, so the bad number is
restored every night until the database itself is corrected. Hence this script.

WHAT IT CORRECTS, AND WHAT IT DELIBERATELY DOES NOT.

Each of the four sides was booked `credit - 3500` when the correct booking is
`credit`. The error is therefore exactly +$3,500 per side, +$14,000 in total,
and entries 1-4 must end up with `realized_pnl == total_credit` — both of their
sides expired worthless, so the full credit is kept. That is an invariant this
script asserts rather than a number it invents.

Entries 5 and 6 are UNTOUCHED. Their call sides were closed by MKT-047-RECHECK
at 15:50, not expired, so their bookings (+$120 / +$115) never involved the
settlement price.

It does NOT try to reconcile to IBKR's per-conid realized figure (-$1,324.87
that evening). That number covered only the legs closed before expiry
processing, and our per-entry model has never matched IBKR's per-conid view
exactly on any day — that is the long-standing BROKER-RECONCILE drift, a
separate question. The goal here is the number our own accounting would have
produced with the correct price, nothing more.

SAFETY. Dry-run unless `--apply`. Takes a real SQLite `.backup()` first (never
a file copy — a WAL-mode database loses committed rows that way). Verifies every
stored value against what was measured before writing, and refuses if anything
differs: if reality has moved, the operator should look rather than let a script
guess. Idempotent — re-running after a successful repair is a no-op.

AFTERWARDS. `daily_summaries` is the authoritative source the metrics self-heal
reads, so `hydra_metrics.json` (cumulative_pnl, the 10-14 daily_returns row,
win/loss) corrects itself at the next settlement. No metrics file is edited
here. HOMER rewrites the journal from the same database on its next run.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.db_backup import safe_db_backup  # noqa: E402

DB = "/opt/calypso/data/variant_bl/backtesting.db"
DATE = "2026-10-14"

# Measured from the live database 2026-10-15, before any change.
# entry -> (total_credit, realized_now, realized_corrected)
EXPECTED = {
    1: (455.00, -3045.00, 455.00),
    2: (200.00, -3300.00, 200.00),
    3: (240.00, -3260.00, 240.00),
    4: (385.00, -3115.00, 385.00),
    5: (280.00, 120.00, 120.00),   # untouched — call side closed, not expired
    6: (185.00, 115.00, 115.00),   # untouched
}
GROSS_NOW, GROSS_FIXED = -12485.00, 1515.00
COMMISSION = 225.40
NET_NOW, NET_FIXED = -12710.40, 1289.60
TRUE_CLOSE = 7720.97
TOL = 0.02


def _close(a, b, tol=TOL):
    return a is not None and abs(float(a) - float(b)) <= tol


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"FAIL: no database at {args.db}")
        return 2

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = dict(
        (r[0], (r[1], r[2]))
        for r in con.execute(
            "SELECT entry_number, total_credit, realized_pnl FROM trade_entries "
            "WHERE date = ? ORDER BY entry_number", (DATE,))
    )
    ds = con.execute(
        "SELECT gross_pnl, commission, net_pnl, spx_close, entries_placed, "
        "entries_expired FROM daily_summaries WHERE date = ?", (DATE,)).fetchone()
    con.close()

    if ds is None:
        print(f"FAIL: no daily_summaries row for {DATE}")
        return 2

    gross, comm, net, spx_close, placed, expired = ds
    print(f"=== {DATE} as stored ===")
    print(f"  spx_close (correct)  : {spx_close}")
    print(f"  gross / comm / net   : {gross:.2f} / {comm:.2f} / {net:.2f}")
    print(f"  entries placed/expired: {placed}/{expired}")
    for n in sorted(rows):
        print(f"  entry {n}: credit {rows[n][0]:>8.2f}  realized {rows[n][1]:>10.2f}")

    # ---- idempotency: already repaired? ----
    if _close(gross, GROSS_FIXED) and all(
            _close(rows.get(n, (None, None))[1], EXPECTED[n][2]) for n in EXPECTED):
        print("\nALREADY REPAIRED — nothing to do.")
        return 0

    # ---- preconditions ----
    problems = []
    if not _close(spx_close, TRUE_CLOSE):
        problems.append(f"spx_close {spx_close} != {TRUE_CLOSE}")
    if not _close(gross, GROSS_NOW):
        problems.append(f"gross_pnl {gross} != {GROSS_NOW}")
    if not _close(net, NET_NOW):
        problems.append(f"net_pnl {net} != {NET_NOW}")
    if not _close(comm, COMMISSION):
        problems.append(f"commission {comm} != {COMMISSION}")
    if set(rows) != set(EXPECTED):
        problems.append(f"entry numbers {sorted(rows)} != {sorted(EXPECTED)}")
    else:
        for n, (credit, now, _fixed) in EXPECTED.items():
            if not _close(rows[n][0], credit):
                problems.append(f"entry {n} credit {rows[n][0]} != {credit}")
            if not _close(rows[n][1], now):
                problems.append(f"entry {n} realized {rows[n][1]} != {now}")

    if problems:
        print("\nREFUSING — the database does not match what was measured:")
        for p in problems:
            print("   -", p)
        print("\nLook at it by hand rather than letting this script guess.")
        return 3

    # ---- the correction, derived not asserted ----
    delta = sum(EXPECTED[n][2] - EXPECTED[n][1] for n in EXPECTED)
    assert _close(delta, 14000.00), f"expected +14000 total, got {delta}"
    assert _close(GROSS_NOW + delta, GROSS_FIXED)
    for n in (1, 2, 3, 4):
        assert _close(EXPECTED[n][2], EXPECTED[n][0]), (
            f"entry {n}: a fully-worthless expiry must keep exactly its credit")

    print(f"\n=== correction ===")
    print(f"  4 call sides x +$3,500 (credit-3500 -> credit) = +${delta:,.2f}")
    print(f"  gross {GROSS_NOW:,.2f} -> {GROSS_FIXED:,.2f}")
    print(f"  net   {NET_NOW:,.2f} -> {NET_FIXED:,.2f}  (commission {COMMISSION} unchanged)")
    for n in (1, 2, 3, 4):
        print(f"  entry {n}: {EXPECTED[n][1]:>10.2f} -> {EXPECTED[n][2]:>8.2f}")
    print("  entries 5,6: unchanged (call sides were CLOSED, not expired)")

    if not args.apply:
        print("\nDRY RUN — re-run with --apply to write.")
        return 0

    backup = safe_db_backup(args.db, "pre_stale_settlement_repair")
    print(f"\nbacked up to {backup}")

    w = sqlite3.connect(args.db)
    try:
        with w:
            for n in (1, 2, 3, 4):
                w.execute(
                    "UPDATE trade_entries SET realized_pnl = ? WHERE date = ? "
                    "AND entry_number = ?", (EXPECTED[n][2], DATE, n))
            w.execute(
                "UPDATE daily_summaries SET gross_pnl = ?, net_pnl = ? WHERE date = ?",
                (GROSS_FIXED, NET_FIXED, DATE))
    finally:
        w.close()

    # ---- verify ----
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    g2, n2 = con.execute(
        "SELECT gross_pnl, net_pnl FROM daily_summaries WHERE date = ?", (DATE,)).fetchone()
    tot = con.execute(
        "SELECT COALESCE(SUM(realized_pnl),0) FROM trade_entries WHERE date = ?",
        (DATE,)).fetchone()[0]
    con.close()

    ok = _close(g2, GROSS_FIXED) and _close(n2, NET_FIXED) and _close(tot, GROSS_FIXED)
    print(f"\nafter: gross {g2:.2f} net {n2:.2f} sum(realized) {tot:.2f}")
    print("  per-entry sum == gross:", _close(tot, g2))
    print("VERIFIED" if ok else "*** VERIFY FAILED — restore from the backup above ***")
    if ok:
        print("\nmetrics correct themselves at the next settlement (the self-heal "
              "re-derives from daily_summaries). No metrics file was edited.")
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
