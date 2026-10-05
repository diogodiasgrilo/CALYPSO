#!/usr/bin/env python3
"""Are the instruments that MATTER actually recording?

WHY THIS EXISTS. Every substantive finding in the 2026-09-30 → 2026-10-05
review had the same shape: the data was collected and nobody looked.

    day_type / realized_volatility   NULL for 10 weeks
    trade_entries.expected_move      NULL on all 315 rows
    trade_stops.quoted_mid_at_stop   NULL on 30% of early_close rows
    gex_decisions.cluster_*          NULL on all 298 rows
    shadow_entries                   569 rows / 96 days, never read
    _RateGate                        computed its wait, recorded nothing
    hydra_metrics.json               headline 91% pre-live SIMULATION

`audit_fleet_correctness.py` checks the numbers we have agree with each other.
Nothing checked that we have them. That is this.

WHY IT IS A CURATED LIST AND NOT A SCANNER. The first version of this script
scanned every column of every table for all-NULL / single-valued and produced
**118 findings**, essentially all false: `contracts` is legitimately always 7 on
B, `long_call_strike` is legitimately 0.0 on G (a naked strangle has no long
leg), fill prices are legitimately NULL on a dry-run variant. A report nobody
reads is the exact failure this exists to catch, so it was rewritten to check a
short list of instruments that are known to matter, each with the condition
under which it SHOULD be populated and a note on what its absence once cost.

Read-only. Opens every database with mode=ro.

Usage:
    python -m scripts.audit_telemetry_health
    python -m scripts.audit_telemetry_health --days 20 --alert
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VARIANTS = ["", "b", "c", "d", "e", "f", "g", "h"]

# Set when the dashboard baseline cannot be read, so a skipped check is
# reported rather than passing silently as 'ok'.
_BASELINE_UNAVAILABLE = ""

# (table, column, live_only, min_rows, why) — `live_only` means the instrument
# only exists on a variant placing REAL orders, so a dry-run NULL is correct.
INSTRUMENTS = [
    ("daily_summaries", "day_type", False, 5,
     "was NULL for 10 weeks; HOMER read a Google Sheets column retired 2026-07-17"),
    ("daily_summaries", "realized_volatility", False, 5,
     "same 10-week gap as day_type"),
    ("trade_entries", "expected_move", False, 5,
     "was NULL on all 315 rows; the payload read an attribute nothing assigned"),
    ("trade_entries", "delta_call", False, 5,
     "the 8-delta target is the strategy's core rule — unverifiable without it"),
    ("trade_entries", "otm_distance_call", False, 5,
     "needed to tell a market change from a strike-selection change"),
    ("trade_stops", "quoted_mid_at_stop", False, 3,
     "was NULL on 30% of early_close rows (0.0 treated as missing)"),
    ("trade_stops", "slippage_on_close", False, 3,
     "the -$197/stop leak is measured from this and nothing else"),
    ("trade_entries", "short_call_fill_price", True, 5,
     "the entry fill path; only a live seat has real fills"),
    ("close_leg_executions", "place_to_fill_ms", True, 1,
     "v19 close-cost split: ours vs the broker's latency"),
]


def _ro(p: Path):
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def _is_live(root: Path, vid: str) -> bool:
    name = "config.json" if not vid else f"config_variant_{vid}.json"
    p = root / "bots" / "hydra" / "config" / name
    try:
        return json.loads(p.read_text()).get("dry_run") is False
    except Exception:  # noqa: BLE001
        return False


def _epoch(root: Path, vid: str) -> str:
    """The lifetime-record epoch lives in the variant CONFIG, not the metrics
    file — reading it from the file made the first version of this script
    report a false headline mismatch on variant E."""
    name = "config.json" if not vid else f"config_variant_{vid}.json"
    p = root / "bots" / "hydra" / "config" / name
    try:
        cfg = json.loads(p.read_text())
        return str((cfg.get("strategy") or {}).get("metrics_epoch_date") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _dashboard_baseline(vid: str) -> str:
    """The date the DASHBOARD rebases this variant's lifetime card to.

    Lives in dashboard/backend/config.py, not the bot config — which is exactly
    why it can silently disagree with strategy.metrics_epoch_date.
    """
    try:
        from dashboard.backend.config import settings
        return str(getattr(settings, f"variant_{vid or 'a'}_baseline_date", "") or "").strip()
    except Exception as e:  # noqa: BLE001
        # LOUD, not silent. Swallowing this skips the single most valuable
        # check in the file and reports "ok" — which is the exact failure this
        # script exists to catch, committed inside the script itself. It bit
        # once already: run from /tmp without the repo on sys.path, every
        # ERA_MISMATCH silently vanished and the report looked clean.
        global _BASELINE_UNAVAILABLE
        _BASELINE_UNAVAILABLE = f"{type(e).__name__}: {e}"
        return ""


def audit_variant(root: Path, vid: str, days: int):
    sub = f"variant_{vid}" if vid else ""
    db = root / "data" / sub / "backtesting.db"
    if not db.exists():
        return []
    live = _is_live(root, vid)
    out = []
    con = _ro(db)
    try:
        have = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table, col, live_only, min_rows, why in INSTRUMENTS:
            if live_only and not live:
                continue
            if table not in have:
                out.append(("TABLE_MISSING", f"{table}.{col}", why))
                continue
            if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
                out.append(("COLUMN_MISSING", f"{table}.{col}", why))
                continue
            # Judge on the MOST RECENT day that produced rows, not on a
            # percentage over the window. The window conflates "broken now"
            # with "was broken, since fixed" — and that is precisely how a
            # useful check turns into noise. Measured: `expected_move` was NULL
            # on every row before 2026-09-30 and populated on every row after,
            # so a 14-day percentage reported 62% NULL for a bug that no longer
            # exists, while G's delta_call — genuinely NULL TODAY — looked
            # milder at 23%. The window figure is kept, but only as context.
            last = con.execute(
                f"SELECT MAX(substr(date,1,10)) FROM {table} "
                f"WHERE substr(date,1,10) >= date('now','-{days} day')").fetchone()[0]
            if not last:
                continue
            n, nn = con.execute(
                f'SELECT COUNT(*), COUNT("{col}") FROM {table} '
                f"WHERE substr(date,1,10) >= date('now','-{days} day')").fetchone()
            if n < min_rows:
                continue
            ln, lnn = con.execute(
                f'SELECT COUNT(*), COUNT("{col}") FROM {table} '
                f"WHERE substr(date,1,10) = ?", (last,)).fetchone()
            if ln and lnn == 0:
                out.append(("DEAD_NOW", f"{table}.{col}",
                            f"NULL on all {ln} row(s) of the latest day ({last}); "
                            f"{n - nn}/{n} NULL over {days}d — {why}"))
            elif ln and lnn < ln:
                out.append(("PARTIAL_NOW", f"{table}.{col}",
                            f"NULL on {ln - lnn} of {ln} row(s) on {last} — {why}"))
            elif nn < n:
                out.append(("HEALED", f"{table}.{col}",
                            f"latest day ({last}) is clean; {n - nn}/{n} NULL "
                            f"earlier in the window — historical, no action"))

        # TWO SURFACES, ONE NUMBER. The dashboard rebases the lifetime card to
        # the variant's `baseline_date`; the bot's metrics file rebases to
        # `strategy.metrics_epoch_date`. E's own config states the invariant in
        # prose — "the two must move together or the dashboard and the bot will
        # disagree about the same lifetime number" — and nothing enforced it.
        #
        # B violates it: baseline_date 2026-07-24 (set, with the comment "hide
        # pre-live dry-run history"), metrics_epoch_date unset. So the dashboard
        # correctly shows +$2,010 while the file says +$21,439, of which 91% is
        # pre-live SIMULATION. I quoted the file. This is that check.
        #
        # Note it is NOT a drift check: the file and the full-history DB sum
        # agree exactly (the settlement drift guard ensures that). The defect is
        # that they are measuring a different ERA from the dashboard.
        m = root / "data" / sub / "hydra_metrics.json"
        if m.exists():
            try:
                filev = float(json.loads(m.read_text()).get("cumulative_pnl") or 0.0)
                ep = _epoch(root, vid)
                base = _dashboard_baseline(vid)
                if base and not ep:
                    shown = con.execute(
                        "SELECT COALESCE(SUM(net_pnl),0) FROM daily_summaries "
                        "WHERE date>=?", (base,)).fetchone()[0]
                    if abs(filev - shown) > 1.0:
                        out.append((
                            "ERA_MISMATCH", "hydra_metrics.cumulative_pnl",
                            f"file {filev:+.2f} counts ALL history; the dashboard "
                            f"shows {shown:+.2f} from its baseline {base}. Set "
                            f"strategy.metrics_epoch_date={base} so both surfaces "
                            f"report the same era — quoting the file is how "
                            f"+$21,439 was reported for a +$2,010 live record"))
                elif ep:
                    dbv = con.execute(
                        "SELECT COALESCE(SUM(net_pnl),0) FROM daily_summaries "
                        "WHERE date>=?", (ep,)).fetchone()[0]
                    if abs(filev - dbv) > 1.0:
                        out.append(("HEADLINE_DRIFT", "hydra_metrics.cumulative_pnl",
                                    f"file {filev:+.2f} vs db {dbv:+.2f} since "
                                    f"epoch {ep}"))
            except Exception as e:  # noqa: BLE001
                out.append(("HEADLINE", "hydra_metrics.json", f"unreadable: {e}"))
    finally:
        con.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--root", default="/opt/calypso")
    ap.add_argument("--alert", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root)

    print(f"TELEMETRY HEALTH — do the instruments that matter record? ({a.days}d)")
    print("=" * 72)
    rows = []
    for vid in VARIANTS:
        name = (vid or "a").upper()
        found = audit_variant(root, vid, a.days)
        if not found:
            print(f"  {name:<2} ok")
            continue
        print(f"  {name:<2} {len(found)} finding(s):")
        for kind, where, detail in found:
            print(f"       [{kind}] {where}")
            print(f"              {detail}")
            rows.append((name, kind, where, detail))
    print()
    if _BASELINE_UNAVAILABLE:
        print(f"  ⚠️  the dashboard baseline could not be read "
              f"({_BASELINE_UNAVAILABLE}) — the ERA_MISMATCH check was SKIPPED, "
              f"not passed. Run with the repo on PYTHONPATH.")
    print(f"  {len(rows)} finding(s)")

    if a.alert and rows:
        try:
            from shared.alert_service import AlertService
            AlertService({"alerts": {"enabled": True}}, "TELEMETRY").send_alert(
                alert_type="data_quality",
                title=f"Telemetry health: {len(rows)} finding(s)",
                message="\n".join(f"{v} [{k}] {w}" for v, k, w, _ in rows[:12]),
            )
        except Exception as e:  # noqa: BLE001
            print(f"  (alert failed, non-fatal: {type(e).__name__}: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
