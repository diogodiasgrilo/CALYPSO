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
  * REFUSES to write while the variant's bot is running. That process loaded
    the metrics file at ITS startup and rewrites the whole thing from memory at
    settlement, so a write underneath it is silently discarded hours later.
    Run after settlement, or pass ``--force`` and restart the bot at once.
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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402

#: Matches strangle_strategy._min_buying_power_per_unit's default.
DEFAULT_STRANGLE_BP = 30_000.0


def _unit_is_active(unit: str) -> bool:
    """True if the systemd unit is running. False when systemd is unavailable
    (e.g. a laptop) — the guard should never block a local dry run."""
    try:
        r = subprocess.run(["systemctl", "is-active", unit],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def _to_dt(t, date: str = "") -> Optional[datetime]:
    """Coerce a timestamp, tolerating the TWO formats the DB actually uses.

    ``trade_entries.entry_time`` is a full ``'2026-08-28 10:45:36'`` but
    ``trade_stops.stop_time`` is TIME-ONLY — ``'10:54:37'``. A parser that
    handles only the first silently returns None for every stop, so no position
    is ever marked closed and every entry appears held to the session close.
    That produced a suspiciously constant $60,000/day for G before this was
    caught; `date` supplies the missing day for the time-only form.
    """
    if t is None or t == "":
        return None
    if isinstance(t, datetime):
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    s = str(t).strip()
    if date and len(s) <= 8 and s.count(":") == 2:
        s = f"{date} {s}"
    try:
        d = datetime.fromisoformat(s)
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
        if close_t < open_t:
            # Defensive: a malformed stop earlier than its own entry would make
            # the sweep go negative. Treat it as held to close instead.
            close_t = session_close
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
        # MIRROR the live sweep's side preference exactly:
        #     close_time  or  call_stop_time  or  put_stop_time
        # i.e. the CALL side's stop wins when both sides stopped, which is not
        # necessarily the earlier one. Reproducing the live rule (rather than a
        # better one) keeps backfilled days and future live days on the SAME
        # definition; two definitions in one series would be worse than one
        # imperfect definition.
        #
        # Known conservatism, inherited deliberately: the whole entry's margin
        # is freed at that single moment, though a strangle's two naked legs are
        # independent and only half the margin actually frees when one stops.
        # Worth fixing in base_strategy._calculate_capital_deployed for BOTH
        # paths at once — not here, one-sidedly.
        by_side: dict[int, dict[str, datetime]] = {}
        for r in con.execute(
            "SELECT entry_number, side, stop_time FROM trade_stops WHERE date = ?", (date,)
        ):
            t = _to_dt(r["stop_time"], date)
            if not t:
                continue
            by_side.setdefault(int(r["entry_number"] or 0), {})[str(r["side"] or "")] = t
        stops: dict[int, datetime] = {}
        for n, sides in by_side.items():
            chosen = sides.get("call") or sides.get("put")
            if chosen:
                stops[n] = chosen
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
    ap.add_argument("--force", action="store_true",
                    help="write even though the variant's bot is running — you "
                         "MUST restart it immediately afterwards or the write "
                         "is discarded at the next settlement")
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

    # A RUNNING bot holds cumulative_metrics in memory from its OWN startup
    # (base_strategy.py:1366) and rewrites the whole file from that copy at
    # settlement (:6511). Writing underneath a running process is therefore
    # SILENTLY DISCARDED at the next close — the worst kind of failure, because
    # the script reports success and the rows vanish hours later.
    running = _unit_is_active(f"hydra_variant_{vid}")
    if running:
        print(
            f"\n  WARNING: hydra_variant_{vid} is RUNNING.\n"
            f"  It loaded hydra_metrics.json at startup and rewrites the whole\n"
            f"  file from memory at settlement, so anything written now is lost\n"
            f"  at tonight's close. Do ONE of:\n"
            f"    (a) run this AFTER the settlement completes, or\n"
            f"    (b) run it now and then: sudo systemctl restart hydra_variant_{vid}"
        )

    if not args.write:
        print("\n  DRY RUN — re-run with --write to apply.")
        return 0
    if running and not args.force:
        print("\n  REFUSING to write under a running bot — pass --force if you "
              "will restart it immediately afterwards.")
        return 3

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
