#!/usr/bin/env python3
"""Restore the four days the 2026-05-29 broker cutover buried under a zero.

WHAT IS BEING CORRECTED, and how confident each part is.

The cutover restarted A, B and C mid-session. All three lost position
monitoring within eight seconds of each other (spread_snapshots stop at
12:23:11 / 12:23:17 / 12:23:19) and each wrote a settlement summary of
0 entries / $0 gross / $0 net over a day that had real fills. B 2026-05-05 is
a fourth instance of the same shape.

  credits       HIGH  — `trade_entries.total_credit`, written at fill time,
                        alongside time_to_fill_ms of 2750-7494ms. Untouched by
                        the bug, which only zeroed daily_summaries.
  settled value HIGH  — arithmetic. SPX is cash-settled; every position closed
                        comfortably inside its shorts, so all expired worthless.
  held to expiry MED-HIGH — inferred from ZERO trade_stops rows on any of the
                        four days. A restarted process does not close positions
                        it has forgotten, and nothing flattens on shutdown
                        (MKT-047 fires at 15:50, not on SIGTERM).
  commission    MODELLED — $1.15 per contract-leg, the rate this repo adopted
                        that very day (commit 078049f, "model commission at
                        IBKR ~$1.15/leg"). Opening legs only: cash settlement
                        has no closing commission. THIS IS THE ONE ESTIMATE.

So gross is exact and net carries a modelled commission. Both are recorded, and
every write is logged to a `data_corrections` table with its before value, so
the change is traceable and reversible rather than silently absorbed.

NOT CORRECTED: B 2026-06-05. Its gross ($2,925) is already right — what is
missing there is the per-entry detail, and no source for it survives. Inventing
entries to satisfy a row count would be the same error that produced HOMER's
fabricated journal narrative.

Dry-run by default. `--apply` writes, and only after taking a SQLite .backup()
of each database (never a file copy — these are WAL-mode).

Usage:
    .venv/bin/python -m scripts.backfill_phantom_settlements
    .venv/bin/python -m scripts.backfill_phantom_settlements --apply
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

COMMISSION_PER_CONTRACT_LEG = 1.15
LEGS = 4

TARGETS = [
    ("A", "data/backtesting.db", "2026-05-29"),
    ("B", "data/variant_b/backtesting.db", "2026-05-05"),
    ("B", "data/variant_b/backtesting.db", "2026-05-29"),
    ("C", "data/variant_c/backtesting.db", "2026-05-29"),
]

CORRECTIONS_DDL = """
CREATE TABLE IF NOT EXISTS data_corrections (
    applied_at   TEXT NOT NULL,
    date         TEXT NOT NULL,
    field        TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT,
    reason       TEXT NOT NULL,
    evidence     TEXT NOT NULL
)
"""

REASON = ("2026-05-29 calypso-broker cutover: a restarted process wrote "
          "settlement from empty daily state over a day that had traded")


def reconstruct(con, date):
    """Exact settled P&L for a day whose positions all expired. Never guesses."""
    close = con.execute("SELECT spx_close FROM daily_summaries WHERE date=?",
                        (date,)).fetchone()
    if not close or close[0] is None:
        return None
    close = close[0]
    rows = con.execute(
        "SELECT entry_number, short_call_strike, long_call_strike, "
        "short_put_strike, long_put_strike, total_credit, contracts "
        "FROM trade_entries WHERE date=? ORDER BY entry_number", (date,)).fetchall()
    if not rows:
        return None
    stops = con.execute("SELECT COUNT(*) FROM trade_stops WHERE date=?",
                        (date,)).fetchone()[0]
    if stops:
        # A stopped day is NOT reconstructable this way — the exit price is not
        # recoverable from strikes and a settlement print. Refuse rather than
        # approximate.
        return {"refused": f"{stops} trade_stops row(s) — exit prices unknown"}

    gross = comm = 0.0
    detail = []
    for en, sc, lc, sp, lp, credit, k in rows:
        k = k or 1
        cw = abs((lc or sc) - sc) if sc else 0.0
        pw = abs(sp - (lp or sp)) if sp else 0.0
        cv = max(0.0, min(close - sc, cw)) if sc else 0.0
        pv = max(0.0, min(sp - close, pw)) if sp else 0.0
        settled = (cv + pv) * 100 * k
        c = COMMISSION_PER_CONTRACT_LEG * k * LEGS
        gross += (credit or 0.0) - settled
        comm += c
        detail.append((en, credit or 0.0, settled, c, k))
    return {"close": close, "gross": round(gross, 2), "commission": round(comm, 2),
            "net": round(gross - comm, 2), "entries": len(rows), "detail": detail}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--root", default="/opt/calypso")
    a = ap.parse_args()
    root = Path(a.root)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    total = 0.0
    print(f"{'APPLY' if a.apply else 'DRY RUN'} — phantom-settlement backfill\n")

    for vid, rel, date in TARGETS:
        db = root / rel
        if not db.exists():
            print(f"  {vid} {date}: MISSING {db}")
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        r = reconstruct(con, date)
        cur = con.execute("SELECT gross_pnl, net_pnl, commission, entries_placed "
                          "FROM daily_summaries WHERE date=?", (date,)).fetchone()
        con.close()
        if r is None:
            print(f"  {vid} {date}: no reconstructable data")
            continue
        if "refused" in r:
            print(f"  {vid} {date}: REFUSED — {r['refused']}")
            continue

        print(f"  {vid} {date}  SPX close {r['close']:.2f}")
        for en, credit, settled, c, k in r["detail"]:
            print(f"      e#{en} x{k}: credit {credit:8.2f}  settled {settled:7.2f}  "
                  f"comm {c:6.2f}")
        print(f"      NOW : gross {cur[0]:.2f}  net {cur[1]:.2f}  "
              f"comm {cur[2]:.2f}  entries {cur[3]}")
        print(f"      NEW : gross {r['gross']:.2f}  net {r['net']:.2f}  "
              f"comm {r['commission']:.2f}  entries {r['entries']}")
        total += r["net"] - (cur[1] or 0.0)

        if a.apply:
            bak = db.with_name(f"{db.stem}.pre-backfill-{stamp}.db")
            src = sqlite3.connect(str(db))
            dst = sqlite3.connect(str(bak))
            with dst:
                src.backup(dst)          # NOT a file copy — these are WAL-mode
            dst.close(); src.close()
            w = sqlite3.connect(str(db))
            try:
                w.execute(CORRECTIONS_DDL)
                ev = (f"trade_entries has {r['entries']} row(s) with fill timings; "
                      f"0 trade_stops; spx_close {r['close']:.2f} inside all shorts")
                for field, old, new in (("gross_pnl", cur[0], r["gross"]),
                                        ("net_pnl", cur[1], r["net"]),
                                        ("commission", cur[2], r["commission"]),
                                        ("entries_placed", cur[3], r["entries"]),
                                        ("entries_expired", None, r["entries"])):
                    w.execute("INSERT INTO data_corrections (applied_at,date,field,"
                              "old_value,new_value,reason,evidence) VALUES (?,?,?,?,?,?,?)",
                              (datetime.now().isoformat(timespec="seconds"), date,
                               field, str(old), str(new), REASON, ev))
                w.execute("UPDATE daily_summaries SET gross_pnl=?, net_pnl=?, "
                          "commission=?, entries_placed=?, entries_expired=?, "
                          "entries_stopped=0 WHERE date=?",
                          (r["gross"], r["net"], r["commission"], r["entries"],
                           r["entries"], date))
                w.commit()
            finally:
                w.close()
            print(f"      APPLIED · backup {bak.name} · logged to data_corrections")
        print()

    print(f"{'Applied' if a.apply else 'Would apply'} a net change of {total:+,.2f}")
    if not a.apply:
        print("(dry run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
