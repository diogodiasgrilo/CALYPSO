#!/usr/bin/env python3
"""Full-history HYDRA simulation with TODAY's live config.

Runs the backtest engine over every trading day with 1-min options + Greeks
data in the local cache (2022-05 → 2026-04, ~947 days / ~4 years) using the
exact `live_config()` that matches what's deployed on the VM as of 2026-04-19:

  - 2 effective base entries (E#1 dropped at all VIX via regime max_entries [2,2,2,1])
  - Entry times 10:15 / 10:45 / 11:15 ET + conditional E6 at 14:00
  - VIX regime breakpoints [18, 22, 28] with per-zone credit floors
    call [1.00, 0.50, 0.30, 0.30]  put [1.25, 0.75, 0.50, 0.40]
  - MKT-024 wider starting OTM 3.5× / 4.0×
  - MKT-027 VIX-scaled spread width 25-110pt
  - MKT-011 credit gate + MKT-029 graduated fallback
  - MKT-040 call-only when put non-viable
  - MKT-032/MKT-039 put-only only when VIX < 15
  - Stop = credit + asymmetric buffer (call $0.75, put $1.75)
  - MKT-042 buffer decay 2.50× → 1× over 4 hours
  - MKT-043 calm entry filter (15pt / 3min / 5min max delay)
  - Whipsaw filter 1.75× expected move
  - MKT-045 chain snap, MKT-046 10s anti-spike
  - Upday-035 E6 put-only at +0.25%
  - Downday-035 E6 call-only at -0.25%
  - Base-entry down-day call-only: DISABLED
  - MKT-038 FOMC T+1 call-only: DISABLED
  - FOMC T+1 skip: ENABLED (blackout)
  - FOMC announcement skip: DISABLED (trades Day 2 normally)

CAVEAT: This is a ThetaData replay, which historically runs ~3x more
optimistic than live Saxo execution (see CLAUDE.md lesson 72). A
heuristic live projection multiplies by 0.34 to estimate real-world P&L.
"""
import sys
import statistics
from datetime import date
from pathlib import Path
from dataclasses import replace
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import live_config
from backtest.engine import run_backtest
from shared.event_calendar import (
    get_fomc_announcement_dates,
    get_fomc_dates,
)

# Use full cache range
START = date(2022, 5, 16)
END = date(2026, 4, 10)

print("=" * 95)
print("FULL-HISTORY HYDRA SIMULATION — today's live config (2026-04-19 deployment)")
print(f"Period: {START} → {END}  (~4 years)")
print("=" * 95)

cfg = replace(
    live_config(),
    start_date=START,
    end_date=END,
    data_resolution="1min",
    # live flags (all already set in live_config but explicit for clarity)
    conditional_upday_e6_enabled=True,
    conditional_upday_e7_enabled=False,
    conditional_downday_e6_enabled=True,
    conditional_downday_e7_enabled=False,
    conditional_downday_threshold_pct=0.0025,
    conditional_e6_enabled=False,
    conditional_e7_enabled=False,
    base_entry_downday_callonly_pct=None,
    fomc_t1_callonly_enabled=False,
    fomc_t1_skip_enabled=True,
    fomc_announcement_skip=False,
)

print("\nRunning backtest… (this may take several minutes for ~947 days)")
results = run_backtest(cfg, verbose=True)
print(f"\nSimulation finished. {len(results)} trading days with data.\n")


# ── Aggregate metrics ────────────────────────────────────────────────────
total_pnl = sum(r.net_pnl for r in results)
total_gross = sum(r.gross_pnl for r in results)   # DayResult.gross_pnl is a property
total_commission = total_gross - total_pnl

wins = [r.net_pnl for r in results if r.net_pnl > 0]
losses = [r.net_pnl for r in results if r.net_pnl < 0]
flats = [r.net_pnl for r in results if r.net_pnl == 0]

total_entries = 0
full_ic = 0
call_only = 0
put_only = 0
skipped = 0
stopped_entries = 0

reason_counts = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0, "stops": 0})
slot_stats = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0, "stops": 0})


def _ms_to_hhmm(ms: int) -> str:
    """Convert ms-of-day to HH:MM ET string."""
    h = (ms // 3_600_000) % 24
    m = (ms // 60_000) % 60
    return f"{h:02d}:{m:02d}"


for r in results:
    for e in r.entries:
        if e.entry_type == "skipped":
            skipped += 1
            continue
        total_entries += 1
        if e.entry_type == "full_ic":
            full_ic += 1
        elif e.entry_type == "call_only":
            call_only += 1
        elif e.entry_type == "put_only":
            put_only += 1

        # Engine writes call_outcome/put_outcome as "stopped" (lowercase),
        # not "STOP". Also valid: "expired", "early_exit", "skipped", "".
        is_stop = (e.call_outcome == "stopped" or e.put_outcome == "stopped")
        if is_stop:
            stopped_entries += 1

        # Per-slot (keyed by HH:MM derived from entry_time_ms — the engine does
        # not expose a pre-formatted entry_time_str)
        t = _ms_to_hhmm(e.entry_time_ms)
        slot_stats[t]["n"] += 1
        slot_stats[t]["pnl"] += e.net_pnl
        if e.net_pnl > 0: slot_stats[t]["wins"] += 1
        if is_stop: slot_stats[t]["stops"] += 1

        # Per-reason: the engine sets `skip_reason` on PLACED entries too,
        # using it as an "override reason" (e.g. "downday-035", "upday-035",
        # "mkt-038", "mkt-040", "replacement"). Empty string means a
        # vanilla full-IC placement.
        reason = e.skip_reason if e.skip_reason else "normal"
        reason_counts[reason]["n"] += 1
        reason_counts[reason]["pnl"] += e.net_pnl
        if e.net_pnl > 0: reason_counts[reason]["wins"] += 1
        if is_stop: reason_counts[reason]["stops"] += 1


# Daily series for Sharpe / drawdown
daily_pnls = [r.net_pnl for r in results]
mean = statistics.mean(daily_pnls) if daily_pnls else 0.0
stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0.0
daily_sharpe = mean / stdev if stdev > 0 else 0.0
ann_sharpe = daily_sharpe * (252 ** 0.5)

# Max drawdown
cum = 0.0
peak = 0.0
max_dd = 0.0
max_dd_date = None
for r in results:
    cum += r.net_pnl
    if cum > peak:
        peak = cum
    dd = cum - peak
    if dd < max_dd:
        max_dd = dd
        max_dd_date = r.date

# Yearly + monthly breakdown
yearly = defaultdict(lambda: {"pnl": 0.0, "days": 0, "wins": 0, "losses": 0})
monthly = defaultdict(lambda: {"pnl": 0.0, "days": 0})
for r in results:
    y = r.date.year
    m = r.date.strftime("%Y-%m")
    yearly[y]["days"] += 1
    yearly[y]["pnl"] += r.net_pnl
    if r.net_pnl > 0: yearly[y]["wins"] += 1
    elif r.net_pnl < 0: yearly[y]["losses"] += 1
    monthly[m]["days"] += 1
    monthly[m]["pnl"] += r.net_pnl


# ── Print report ────────────────────────────────────────────────────────
print("=" * 95)
print("HEADLINE METRICS  (1 contract / entry, ThetaData backtest)")
print("=" * 95)
print(f"Trading days simulated:         {len(results):>8,}")
print(f"Calendar years covered:         {(END - START).days / 365.25:>8.2f}")
print(f"Winning days:                   {len(wins):>8,}  ({100*len(wins)/max(len(results),1):.1f}%)")
print(f"Losing days:                    {len(losses):>8,}  ({100*len(losses)/max(len(results),1):.1f}%)")
print(f"Flat days (including T+1 skip): {len(flats):>8,}")
print()
print(f"Net P&L (after $2.50/leg comm):  ${total_pnl:>+11,.0f}")
print(f"Gross P&L (pre-commission):      ${total_gross:>+11,.0f}")
print(f"Commission paid:                 ${total_commission:>11,.0f}")
print()
print(f"Average net P&L / trading day:   ${mean:>+10.2f}")
print(f"Stdev of daily net P&L:          ${stdev:>10.2f}")
print(f"Daily Sharpe (risk-free = 0):    {daily_sharpe:>10.3f}")
print(f"Annualized Sharpe (√252):        {ann_sharpe:>10.3f}")
print()
print(f"Max drawdown (running peak):     ${max_dd:>+11,.0f}  on {max_dd_date}")
print(f"Peak cumulative P&L:             ${peak:>+11,.0f}")
print(f"Final cumulative P&L:            ${cum:>+11,.0f}")


print()
print("=" * 95)
print("ENTRY COMPOSITION")
print("=" * 95)
print(f"Total entries placed:      {total_entries:>8,}")
print(f"  Full IC:                 {full_ic:>8,}  ({100*full_ic/max(total_entries,1):.1f}%)")
print(f"  Call-only:               {call_only:>8,}  ({100*call_only/max(total_entries,1):.1f}%)")
print(f"  Put-only:                {put_only:>8,}  ({100*put_only/max(total_entries,1):.1f}%)")
print(f"Entries skipped (MKT-011): {skipped:>8,}")
print(f"Total entry-sides stopped: {stopped_entries:>8,}  ({100*stopped_entries/max(total_entries,1):.1f}% of placed)")


print()
print("=" * 95)
print("PER-ENTRY-SLOT BREAKDOWN")
print("=" * 95)
print(f"{'Slot':<10} {'N':>5} {'Wins':>5} {'Stops':>5} {'WR%':>6} {'P&L':>11} {'Avg':>8}")
for slot in sorted(slot_stats.keys()):
    s = slot_stats[slot]
    wr = 100 * s["wins"] / max(s["n"], 1)
    avg = s["pnl"] / max(s["n"], 1)
    print(f"{slot:<10} {s['n']:>5} {s['wins']:>5} {s['stops']:>5} {wr:>5.1f}% ${s['pnl']:>+9,.0f} ${avg:>+6.0f}")


print()
print("=" * 95)
print("OVERRIDE REASON (entry tag) BREAKDOWN")
print("=" * 95)
print(f"{'Reason':<20} {'N':>5} {'Wins':>5} {'Stops':>5} {'WR%':>6} {'P&L':>11} {'Avg':>8}")
for reason in sorted(reason_counts.keys(), key=lambda k: -reason_counts[k]["pnl"]):
    s = reason_counts[reason]
    wr = 100 * s["wins"] / max(s["n"], 1)
    avg = s["pnl"] / max(s["n"], 1)
    print(f"{reason:<20} {s['n']:>5} {s['wins']:>5} {s['stops']:>5} {wr:>5.1f}% ${s['pnl']:>+9,.0f} ${avg:>+6.0f}")


print()
print("=" * 95)
print("YEARLY BREAKDOWN")
print("=" * 95)
print(f"{'Year':<6} {'Days':>5} {'Wins':>5} {'Losses':>7} {'WR%':>6} {'Net P&L':>12} {'Live-adj (34%)':>16}")
for y in sorted(yearly.keys()):
    s = yearly[y]
    wr = 100 * s["wins"] / max(s["days"], 1)
    live = s["pnl"] * 0.34
    print(f"{y:<6} {s['days']:>5} {s['wins']:>5} {s['losses']:>7} {wr:>5.1f}% ${s['pnl']:>+10,.0f} ${live:>+14,.0f}")


print()
print("=" * 95)
print("LIVE-ADJUSTED PROJECTION  (ThetaData is ~3x optimistic vs Saxo, rough rule × 0.34)")
print("=" * 95)
live_total = total_pnl * 0.34
years = (END - START).days / 365.25
print(f"Backtest net P&L:               ${total_pnl:>+11,.0f}")
print(f"Live-adjusted (× 0.34):          ${live_total:>+11,.0f}")
print(f"Per calendar year (backtest):    ${total_pnl/years:>+11,.0f}")
print(f"Per calendar year (live-adj):    ${live_total/years:>+11,.0f}")
print()
print("Note: 0.34 factor comes from CLAUDE.md lesson 72 — empirical ratio between")
print("ThetaData cached quotes and Saxo live execution, CALIBRATED ONLY on the")
print("Feb-Apr 2026 window (~3 months). Over 4 years this is an unstable estimate:")
print("  - Pre-2024 ThetaData has gappier quotes → live may be a LARGER discount")
print("  - Saxo margin policy, VIX regime, and commission rates have changed over time")
print("  - Strategy components active today (MKT-045/046, Downday-035, T+1 skip) didn't")
print("    exist in 2022-2024 — we're testing a retrofitted strategy on old markets")
print("The 0.34 projection should be read as a directional signal, not a dollar figure.")


# Save full monthly breakdown to a file for inspection
import csv
out = Path(__file__).parent.parent / "docs" / "full_history_monthly_breakdown.csv"
with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["month", "trading_days", "net_pnl", "live_adj_34pct"])
    for m in sorted(monthly.keys()):
        s = monthly[m]
        w.writerow([m, s["days"], f"{s['pnl']:.0f}", f"{s['pnl']*0.34:.0f}"])
print(f"\n✓ Monthly breakdown saved to {out.relative_to(Path(__file__).parent.parent)}")
