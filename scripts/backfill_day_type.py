#!/usr/bin/env python3
"""Fill `day_type` and `realized_volatility` on historical daily_summaries rows.

Both columns have been NULL since the schema gained them. HOMER derived
`day_type` from a Google Sheets "Notes" column and Sheets was retired on
2026-07-17, so the writer has been reading a source that no longer exists.
`fb3e4ec` fixed the forward path; this fills what is already on disk, which is
where every analysis actually looks.

The rules are NOT reimplemented here. This builds a shim that satisfies the
live classifier's reads and calls `HydraStrategy._classify_day_type` and
`._realized_volatility` unbound, overriding only `_classification_moment` so
they describe the historical row instead of today. A second implementation is
how a backfilled column and a live column come to quietly disagree, and then
nobody can tell which half of the series is wrong.

Inputs come from `market_ticks` — the same table the live path reads — so a
backfilled row and a row written live from the same session agree by
construction. Days with no recorded ticks are LEFT NULL rather than guessed:
"not measured" and "measured, and unremarkable" must stay distinguishable.

Dry-run by default. `--write` takes a real `.backup()` first and records every
change in `data_corrections`.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from shared.data_recorder import DataRecorder  # noqa: E402
from shared.event_calendar import get_economic_events_for_date  # noqa: E402
from shared.market_hours import get_us_market_time  # noqa: E402

ET = get_us_market_time().tzinfo


def _moment(date_str: str) -> datetime:
    """Noon ET on the row's date — inside the session, so an early-close check
    reads the day itself rather than an edge."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.replace(hour=12, minute=0, tzinfo=ET)


def classify_row(recorder: DataRecorder, date_str: str, fomc: bool):
    """Run the LIVE classifier over one historical row."""
    ohlc = recorder.get_spx_ohlc_for_date(date_str)
    if not ohlc or not all(v for v in ohlc[:1]):
        return None, None, "no recorded ticks"
    o, h, lo, c = ohlc

    shim = SimpleNamespace(
        market_data=SimpleNamespace(spx_open=o, spx_high=h, spx_low=lo),
        fomc_announcement_today=fomc,
        _resolve_spx_close=lambda: c,
        _classification_moment=lambda: _moment(date_str),
        _data_recorder=recorder,
    )
    try:
        events = get_economic_events_for_date(_moment(date_str).date())
    except Exception:
        events = []

    day_type = HydraStrategy._classify_day_type(shim, events)
    rv = HydraStrategy._realized_volatility(shim)
    return day_type, rv, None


def backfill(db_path: str, write: bool, label: str):
    if not os.path.exists(db_path):
        return None
    rec = DataRecorder(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_summaries)")}
    if "day_type" not in cols:
        conn.close()
        return None

    rows = conn.execute(
        "SELECT date, day_type, realized_volatility, economic_events "
        "FROM daily_summaries ORDER BY date").fetchall()
    conn.close()

    plan, skipped = [], []
    for r in rows:
        if r["day_type"] and r["realized_volatility"] is not None:
            continue
        ev = (r["economic_events"] or "")
        fomc = "fomc" in ev.lower()
        dt, rv, why = classify_row(rec, r["date"], fomc)
        if why:
            skipped.append((r["date"], why))
            continue
        plan.append((r["date"], r["day_type"], dt, r["realized_volatility"], rv))

    print(f"\n=== {label} ===")
    print(f"  rows: {len(rows)}   to fill: {len(plan)}   skipped (no ticks): {len(skipped)}")
    if plan:
        dist = {}
        for _, _, dt, _, _ in plan:
            dist[dt] = dist.get(dt, 0) + 1
        print("  day_type distribution: " + ", ".join(
            f"{k}={v}" for k, v in sorted(dist.items(), key=lambda kv: -kv[1])))
        rvs = [rv for *_, rv in plan if rv is not None]
        if rvs:
            rvs_sorted = sorted(rvs)
            print(f"  realized_vol: n={len(rvs)} "
                  f"min={rvs_sorted[0]:.1f} median={rvs_sorted[len(rvs)//2]:.1f} "
                  f"max={rvs_sorted[-1]:.1f}")
        for d, _, dt, _, rv in plan[:4]:
            print(f"    {d}  {dt:<12} rv={rv}")
        if len(plan) > 4:
            print(f"    … {len(plan)-4} more")

    if not write or not plan:
        return len(plan)

    # sqlite .backup(), never a file copy: a cp of a WAL-mode database
    # silently drops committed rows. Raises rather than returning on failure,
    # so we cannot proceed believing we have a rollback we do not have.
    from shared.db_backup import safe_db_backup
    bpath = safe_db_backup(db_path, "pre_day_type_backfill")
    print(f"  backup -> {bpath}")

    conn = sqlite3.connect(db_path)
    try:
        # Reuse the audit table's ONE definition. A local CREATE TABLE IF NOT
        # EXISTS with a second column list is how this failed the first time:
        # the table already existed with different columns, the CREATE was a
        # silent no-op, and the INSERT then referenced columns that were not
        # there. IF NOT EXISTS does not reconcile schemas, it just declines.
        from scripts.backfill_phantom_settlements import CORRECTIONS_DDL
        conn.execute(CORRECTIONS_DDL)
        now = datetime.utcnow().isoformat(timespec="seconds")
        reason = ("day_type/realized_volatility were NULL since schema creation; "
                  "HOMER derived day_type from a Google Sheets column retired "
                  "2026-07-17. Recomputed with the live classifier.")
        n = 0
        for d, old_dt, dt, old_rv, rv in plan:
            ohlc = rec.get_spx_ohlc_for_date(d)
            evidence = (f"market_ticks OHLC {ohlc}; classified by "
                        f"HydraStrategy._classify_day_type at {label}")
            conn.execute(
                "UPDATE daily_summaries SET day_type=COALESCE(day_type, ?), "
                "realized_volatility=COALESCE(realized_volatility, ?) WHERE date=?",
                (dt, rv, d))
            for field, ov, nv in (("day_type", old_dt, dt),
                                  ("realized_volatility", old_rv, rv)):
                conn.execute(
                    "INSERT INTO data_corrections "
                    "(applied_at,date,field,old_value,new_value,reason,evidence) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (now, d, field, str(ov), str(nv), reason, evidence))
            n += 1
        conn.commit()
        print(f"  WROTE {n} rows (+{n*2} audit entries)")
    finally:
        conn.close()
    return len(plan)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default="/opt/calypso/data")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    targets = [(os.path.join(a.data_root, "backtesting.db"), "variant a (main)")]
    vdir = a.data_root
    if os.path.isdir(vdir):
        for name in sorted(os.listdir(vdir)):
            if name.startswith("variant_") and "." not in name:
                targets.append((os.path.join(vdir, name, "backtesting.db"), name))

    total = 0
    for path, label in targets:
        r = backfill(path, a.write, label)
        if r:
            total += r
    print(f"\n{'WROTE' if a.write else 'WOULD FILL'} {total} rows across all variants")
    if not a.write:
        print("(dry run — pass --write to apply)")


if __name__ == "__main__":
    main()
