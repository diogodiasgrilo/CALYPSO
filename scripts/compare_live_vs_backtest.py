#!/usr/bin/env python3
"""Side-by-side comparison: LIVE VM DB results vs ThetaData backtest replay
for the exact same date range, using today's (2026-04-19) locked config.

Live results reflect HYDRA's actual trades under whatever config was deployed
each day (evolving — base-downday ON→OFF, MKT-038 ON→OFF, Downday-035 added,
E#1 dropped etc. happened at various points).

Backtest replays ONE fixed config (today's live_config()) over the same days.

This exposes:
  (a) Whether today's locked strategy would have produced different P&L
  (b) The ThetaData → Saxo calibration ratio on a matched window
  (c) Day-by-day divergence for anomaly hunting
"""
import sys
import sqlite3
from datetime import date
from pathlib import Path
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import live_config
from backtest.engine import run_backtest

VM_DB = Path("/tmp/vm_backtesting.db")
if not VM_DB.exists():
    print(f"ERROR: {VM_DB} not found. Run: gcloud compute ssh calypso-bot "
          f"--zone=us-east1-b --command=\"sudo -u calypso cat "
          f"/opt/calypso/data/backtesting.db\" > {VM_DB}")
    sys.exit(1)

# ── 1. Pull live VM DB P&L ───────────────────────────────────────────────
conn = sqlite3.connect(str(VM_DB))
live_rows = conn.execute(
    "SELECT date, net_pnl, gross_pnl, commission, entries_placed, "
    "entries_stopped, config_version "
    "FROM daily_summaries ORDER BY date"
).fetchall()

live_by_date = {date.fromisoformat(r[0]): {
    "net": r[1], "gross": r[2], "comm": r[3],
    "entries": r[4], "stops": r[5], "config": r[6] or "?"
} for r in live_rows}

first_live = min(live_by_date.keys())
last_live = max(live_by_date.keys())
print(f"Live VM DB: {len(live_by_date)} days ({first_live} → {last_live})")

# ThetaData cache ends 2026-04-10
BACKTEST_END = date(2026, 4, 10)
compare_end = min(last_live, BACKTEST_END)
print(f"Backtest window (limited by ThetaData cache): {first_live} → {compare_end}")

# ── 2. Run backtest over matched window with today's config ──────────────
cfg = replace(live_config(),
              start_date=first_live,
              end_date=compare_end,
              data_resolution="1min")

print("\nRunning backtest with today's locked config...")
bt_results = run_backtest(cfg, verbose=False)
bt_by_date = {r.date: r for r in bt_results}
print(f"Backtest produced {len(bt_by_date)} days.")


# ── 3. Compare day-by-day ────────────────────────────────────────────────
print()
print("=" * 105)
print("DAY-BY-DAY COMPARISON  (Live = actual, Backtest = today's-config replay on ThetaData)")
print("=" * 105)
header = f"{'Date':<12} {'Live net':>10} {'Backtest':>10} {'Delta':>10} {'Live E':>6} {'BT E':>5} {'Live/BT ratio':>15} {'Live cfg':<15}"
print(header)
print("-" * 105)

sum_live_net = 0.0
sum_bt_net = 0.0
sum_live_gross = 0.0
sum_bt_gross = 0.0
matched_days = 0
only_live = 0
only_bt = 0
both_positive = 0
both_negative = 0
signs_disagree = 0

common_dates = sorted(set(live_by_date.keys()) & set(bt_by_date.keys()))
only_live_dates = sorted(set(live_by_date.keys()) - set(bt_by_date.keys()))
only_bt_dates = sorted(set(bt_by_date.keys()) - set(live_by_date.keys()))

for d in common_dates:
    live = live_by_date[d]
    bt = bt_by_date[d]
    bt_gross = sum(e.gross_pnl for e in bt.entries)
    delta = bt.net_pnl - live["net"]
    ratio = (live["net"] / bt.net_pnl) if bt.net_pnl != 0 else float("nan")
    sum_live_net += live["net"]
    sum_bt_net += bt.net_pnl
    sum_live_gross += live["gross"]
    sum_bt_gross += bt_gross
    matched_days += 1
    if live["net"] > 0 and bt.net_pnl > 0: both_positive += 1
    elif live["net"] < 0 and bt.net_pnl < 0: both_negative += 1
    elif (live["net"] > 0) != (bt.net_pnl > 0): signs_disagree += 1

    ratio_str = f"{ratio:+6.2f}" if abs(bt.net_pnl) > 10 else "n/a"
    cfg_str = (live["config"] or "?")[:14]
    print(f"{str(d):<12} ${live['net']:>+8.0f} ${bt.net_pnl:>+8.0f} ${delta:>+8.0f} "
          f"{live['entries']:>6} {len([e for e in bt.entries if e.entry_type != 'skipped']):>5} "
          f"{ratio_str:>15} {cfg_str:<15}")

for d in only_live_dates:
    live = live_by_date[d]
    only_live += 1
    print(f"{str(d):<12} ${live['net']:>+8.0f}     (no backtest — beyond ThetaData cache)")
for d in only_bt_dates:
    bt = bt_by_date[d]
    only_bt += 1
    print(f"{str(d):<12}     (no live)      ${bt.net_pnl:>+8.0f}     (in backtest but not VM DB)")


# ── 4. Aggregate ─────────────────────────────────────────────────────────
print()
print("=" * 105)
print("AGGREGATE COMPARISON")
print("=" * 105)
print(f"Matched days (both have data):                    {matched_days}")
print(f"  Both positive (green):                          {both_positive}")
print(f"  Both negative (red):                            {both_negative}")
print(f"  Signs disagree (one +, other -):                {signs_disagree}")
print(f"  Near-zero on one or both:                       {matched_days - both_positive - both_negative - signs_disagree}")
print(f"Days in live but beyond backtest cache:           {only_live}")
print(f"Days in backtest but not live (should be 0):      {only_bt}")
print()
print(f"Over the {matched_days} matched days:")
print(f"  Live total NET P&L:                             ${sum_live_net:>+8.0f}")
print(f"  Live total GROSS P&L:                           ${sum_live_gross:>+8.0f}")
print(f"  Backtest total NET P&L (today's config):        ${sum_bt_net:>+8.0f}")
print(f"  Backtest total GROSS P&L:                       ${sum_bt_gross:>+8.0f}")
print()
print(f"  Delta (backtest − live, NET):                   ${sum_bt_net - sum_live_net:>+8.0f}")
print(f"  Delta (backtest − live, GROSS):                 ${sum_bt_gross - sum_live_gross:>+8.0f}")
if sum_bt_net != 0:
    ratio = sum_live_net / sum_bt_net
    print(f"  Live / Backtest ratio:                          {ratio:>+.3f}  (calibrated over this window)")

# Also include beyond-cache live days in full YTD total
beyond_sum = sum(live_by_date[d]["net"] for d in only_live_dates)
full_live_total = sum_live_net + beyond_sum
print()
print(f"Full live YTD total (incl. beyond-cache {only_live} days): ${full_live_total:>+8.0f}")

print()
print("=" * 105)
print("INTERPRETATION")
print("=" * 105)
print("The delta reveals TWO distinct effects mixed together:")
print()
print("  1. CONFIG EVOLUTION: live used an evolving mix of configs (base-downday ON then OFF,")
print("     MKT-038 ON then replaced by T+1 skip, Downday-035 added, E#1 drop applied, etc).")
print("     Backtest uses TODAY's locked config retroactively — strictly deterministic.")
print()
print("  2. THETADATA → SAXO execution calibration (the 0.34 rule). Even with identical config,")
print("     ThetaData backtest tends to be ~3x more optimistic than Saxo live.")
print()
print("If backtest > live by a lot: today's config WOULD have done better on those days")
print("                             (PLUS backtest's natural optimism bias).")
print("If backtest ≈ live:          today's config is roughly what we ran, and calibration is clean.")
print("If backtest < live:          live had lucky fills or we had config wins backtest doesn't capture.")
