#!/usr/bin/env python3
"""Backfill MISSING ``daily_returns`` rows so return-on-capital exists for a
strategy whose capital was computed as 0 before the capital-basis fix.

WHY THIS EXISTS
---------------
``_book_daily_cumulative`` appends a ``daily_returns`` row only when
``capital_deployed > 0``. Before the 2026-09-17 capital-basis fix, the base
implementation computed ``spread_width x 100 x contracts`` and skipped every
entry with no width — so a wingless strategy (G, the naked strangle) produced
capital 0 on every day, and **no row was ever appended**. G therefore had ZERO
``daily_returns`` against 26 entries over 13 traded days, which is why its
dashboard showed "—" for Return on Margin and Peak Margin / Day (defect D2).

The existing self-heal (``strategy.py:_reconcile_cumulative_metrics_from_db``)
CORRECTS rows — ``for row in dr`` — but never CREATES missing ones, so those 13
days would have stayed absent forever and the strategy would have had to
re-accumulate from scratch.

WHY A SCRIPT RATHER THAN EXTENDING THE SELF-HEAL
------------------------------------------------
The self-heal runs inside the live settlement path, which **B — the live paper
seat — executes every close**. Backfilling a dry-run variant's history is not
worth any risk of corrupting the live seat's metrics. This is explicit,
reviewable, and runs only when an operator asks.

WHAT IT RECONSTRUCTS
--------------------
The same PEAK-CONCURRENT margin sweep as
``base_strategy._calculate_capital_deployed``, rebuilt from the database:

  * each entry opens at ``trade_entries.entry_time``;
  * it closes at its earliest ``trade_stops.stop_time``, or — for a 0DTE
    strategy with no stop row — at the session close, because the position is
    held to expiry;
  * closes are processed BEFORE opens at the same instant, so a close and a
    same-second open do not double-count concurrent margin.

Per-entry margin uses the strategy's OWN ``capital_basis``:

  broker_margin   contracts x ``min_buying_power_per_strangle`` (default
                  $30,000) — the SAME floor the entry gate sized with, so the
                  reported return is a return on the capital the strategy
                  actually required, not a second invented definition.
  defined_risk    width x 100 x contracts, matching the base implementation.

SAFETY
------
  * ``--dry-run`` is the DEFAULT. Writing requires ``--write``.
  * The metrics file is backed up (timestamped) before any write.
  * Existing rows are NEVER touched — only dates absent from ``daily_returns``
    are added, so a re-run is idempotent and cannot double-count.
  * Days whose reconstructed capital is 0 are SKIPPED and reported, never
    written with an invented denominator.

Usage
-----
    python -m scripts.backfill_capital_deployed --variant g
    python -m scripts.backfill_capital_deployed --variant g --write
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402

#: Matches strangle_strategy._min_buying_power_per_unit's default.
DEFAULT_STRANGLE_BP = 30_000.0


def _to_dt(t) -> Optional[datetime]:
    if t is None or t == "":
        return None
    if isinstance(t, datetime):
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(t))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _entry_margin(row: dict, basis: str, strangle_bp: float) -> float:
    """Per-entry capital, by the strategy's capital basis."""
    contracts = int(row.get("contracts") or 1) or 1
    if basis == "broker_margin":
        return strangle_bp * contracts
    # defined_risk: the widest side defines the loss cap for an iron condor.
    width = max(
        float(row.get("call_spread_width") or 0.0),
        float(row.get("put_spread_width") or 0.0),
    )
    return width * 100.0 * contracts


def peak_concurrent_capital(
    entries: list[dict], stops: dict[int, datetime], session_close: datetime,
    basis: str, strangle_bp: float,
) -> float:
    """Mirror of base_strategy._calculate_capital_deployed's sweep-line."""
    events: list[tuple[datetime, int, float]] = []
    for e in entries:
        margin = _entry_margin(e, basis, strangle_bp)
        if margin <= 0:
            continue
        open_t = _to_dt(e.get("entry_time"))
        if open_t is None:
            continue
        # A 0DTE position with no stop row was held to expiry.
        close_t = stops.get(int(e.get("entry_number") or 0)) or session_close
        events.append((open_t, 0, margin))
        events.append((close_t, -1, margin))
    if not events:
        return 0.0
    # kind -1 (close) sorts before 0 (open) at the same instant.
    events.sort(key=lambda x: (x[0], x[1]))
    peak = running = 0.0
    for _, kind, margin in events:
        running += margin if kind == 0 else -margin
        peak = max(peak, running)
    return peak


def build_rows(db_path: Path, basis: str, strangle_bp: float,
               have_dates: set[str]) -> tuple[list[dict], list[str]]:
    """Reconstruct one row per missing traded date. Returns (rows, skipped)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    summaries = con.execute(
        "SELECT date, net_pnl, entries_placed, contracts_per_entry "
        "FROM daily_summaries WHERE entries_placed > 0 ORDER BY date"
    ).fetchall()

    rows: list[dict] = []
    skipped: list[str] = []
    for s in summaries:
        date = s["date"]
        if date in have_dates:
            continue
        entries = [dict(r) for r in con.execute(
            "SELECT * FROM trade_entries WHERE date = ?", (date,))]
        if not entries:
            skipped.append(f"{date}: no trade_entries rows")
            continue
        stops: dict[int, datetime] = {}
        for r in con.execute(
            "SELECT entry_number, stop_time FROM trade_stops WHERE date = ?", (date,)
        ):
            t = _to_dt(r["stop_time"])
            n = int(r["entry_number"] or 0)
            if t and (n not in stops or t < stops[n]):
                stops[n] = t
        # 0DTE session close, 16:00 ET == 20:00 UTC (21:00 UTC under EST; the
        # hour only matters as an upper bound for "held to expiry", and every
        # entry opens well before it either way).
        session_close = _to_dt(f"{date}T20:00:00+00:00")
        cap = peak_concurrent_capital(entries, stops, session_close, basis, strangle_bp)
        if cap <= 0:
            skipped.append(f"{date}: reconstructed capital 0 — not written")
            continue
        net = float(s["net_pnl"] or 0.0)
        rows.append({
            "date": date,
            "net_pnl": net,
            "capital_deployed": cap,
            "return_pct": net / cap,
            "contracts_per_entry": int(s["contracts_per_entry"] or 1) or 1,
            # Marks the row as reconstructed rather than observed at settlement.
            "backfilled": True,
        })
    con.close()
    return rows, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", required=True, help="strategy letter, e.g. g")
    ap.add_argument("--data-root", default="/opt/calypso/data",
                    help="root holding variant_<id>/ directories")
    ap.add_argument("--write", action="store_true",
                    help="actually write (default is a dry run)")
    args = ap.parse_args()

    vid = args.variant.lower()
    vdir = Path(args.data_root) / f"variant_{vid}"
    metrics_path = vdir / "hydra_metrics.json"
    db_path = vdir / "backtesting.db"
    for p in (metrics_path, db_path):
        if not p.exists():
            print(f"ERROR: {p} not found")
            return 2

    basis = getattr(tax.meta(vid), "capital_basis", "defined_risk")
    print(f"variant {vid.upper()} · capital_basis={basis}")

    strangle_bp = DEFAULT_STRANGLE_BP
    cfg_path = ROOT / "bots" / "hydra" / "config" / f"config_variant_{vid}.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text()).get("strategy", {})
            strangle_bp = float(cfg.get("min_buying_power_per_strangle", strangle_bp))
        except (ValueError, OSError):
            pass
    if basis == "broker_margin":
        print(f"  per-contract margin floor: ${strangle_bp:,.0f}")

    metrics = json.loads(metrics_path.read_text())
    existing = metrics.get("daily_returns") or []
    have = {r.get("date") for r in existing}
    print(f"  existing daily_returns rows: {len(existing)}")

    rows, skipped = build_rows(db_path, basis, strangle_bp, have)
    if not rows and not skipped:
        print("  nothing to backfill — every traded day already has a row.")
        return 0

    print(f"\n  would add {len(rows)} row(s):")
    for r in rows:
        print(f"    {r['date']}  net={r['net_pnl']:>10,.2f}  "
              f"capital={r['capital_deployed']:>10,.0f}  "
              f"return={r['return_pct'] * 100:>7.3f}%")
    for s in skipped:
        print(f"    SKIP {s}")

    if rows:
        total_cap = sum(r["capital_deployed"] for r in rows)
        total_net = sum(r["net_pnl"] for r in rows)
        print(f"\n  avg capital/day ${total_cap / len(rows):,.0f} · "
              f"net ${total_net:,.2f} · "
              f"ROI {total_net / total_cap * 100:.3f}% over {len(rows)} day(s)")

    if not args.write:
        print("\n  DRY RUN — re-run with --write to apply.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = metrics_path.with_suffix(f".json.pre_backfill_{stamp}")
    shutil.copy2(metrics_path, backup)
    print(f"\n  backed up -> {backup}")

    merged = existing + rows
    merged.sort(key=lambda r: r.get("date") or "")
    metrics["daily_returns"] = merged
    metrics_path.write_text(json.dumps(metrics, indent=2))
    print(f"  wrote {len(merged)} daily_returns rows to {metrics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
