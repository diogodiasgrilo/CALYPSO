#!/usr/bin/env python3
"""A/B backtest: MKT-038 FOMC T+1 call-only force — under CURRENT live config.

Scenarios over 2025-01-01 → 2026-04-10 (15 months), giving us 10 FOMC T+1 days.

  A. fomc_t1_callonly_enabled = False  (feature OFF — trade full IC as usual)
  B. fomc_t1_callonly_enabled = True   (current live — force call-only on T+1)

Question: on T+1 days, does forcing call-only add or destroy P&L compared
to trading normal full ICs (with MKT-011 credit gate still active)?

2025 FOMC T+1 dates (from shared/event_calendar.py):
  Jan 30, Mar 20, May 8, Jun 19, Jul 31, Sep 18, Nov 7, Dec 18

2026 FOMC T+1 dates:
  Jan 29, Mar 19
"""
import sys
from datetime import date
from pathlib import Path
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import live_config
from backtest.engine import run_backtest
from shared.event_calendar import get_fomc_announcement_dates


START = date(2025, 1, 2)
END = date(2026, 4, 10)

# Compute expected T+1 dates in window (day-after each announcement)
from datetime import timedelta
all_announcements = get_fomc_announcement_dates(2025) + get_fomc_announcement_dates(2026)
t1_in_window = []
for ann in all_announcements:
    tplus1 = ann + timedelta(days=1)
    # Skip if falls on a weekend — live code uses plain next-day, so match that
    if START <= tplus1 <= END:
        t1_in_window.append(tplus1)

print("=" * 90)
print("MKT-038 FOMC T+1 CALL-ONLY — A/B BACKTEST")
print(f"Period: {START} → {END}  (15 months)")
print(f"FOMC T+1 days in window: {len(t1_in_window)}")
for d in t1_in_window:
    print(f"  {d} ({d.strftime('%a')})")
print("=" * 90)


def summarize(results, t1_set):
    t1_pnl = 0.0
    t1_entries = 0
    t1_days = 0
    t1_wins = 0
    t1_losses = 0
    t1_call_only = 0
    t1_full_ic = 0
    t1_stopped_entries = 0

    total_pnl = sum(r.net_pnl for r in results)
    n_days = len(results)

    for day in results:
        if day.date not in t1_set:
            continue
        t1_days += 1
        t1_pnl += day.net_pnl
        if day.net_pnl > 0:
            t1_wins += 1
        elif day.net_pnl < 0:
            t1_losses += 1
        for e in day.entries:
            if e.entry_type == "skipped":
                continue
            t1_entries += 1
            if e.entry_type == "call_only":
                t1_call_only += 1
            elif e.entry_type == "full_ic":
                t1_full_ic += 1
            if (e.call_outcome == "STOP" or e.put_outcome == "STOP"):
                t1_stopped_entries += 1

    return {
        "total_pnl": total_pnl,
        "n_days": n_days,
        "t1_days_seen": t1_days,
        "t1_pnl": t1_pnl,
        "t1_entries": t1_entries,
        "t1_call_only": t1_call_only,
        "t1_full_ic": t1_full_ic,
        "t1_wins": t1_wins,
        "t1_losses": t1_losses,
        "t1_stopped_entries": t1_stopped_entries,
    }


def shared_cfg():
    return replace(
        live_config(),
        start_date=START,
        end_date=END,
        data_resolution="1min",
        conditional_upday_e6_enabled=True,
        conditional_upday_e7_enabled=False,
        conditional_downday_e6_enabled=True,
        conditional_downday_e7_enabled=False,
        conditional_downday_threshold_pct=0.0025,
        conditional_e6_enabled=False,
        conditional_e7_enabled=False,
        base_entry_downday_callonly_pct=None,   # just disabled live
    )


t1_set = set(t1_in_window)

# ── Scenario A: MKT-038 OFF ──────────────────────────────────────────────
cfg_a = replace(shared_cfg(), fomc_t1_callonly_enabled=False)
print("\n--- Scenario A: fomc_t1_callonly_enabled = False (MKT-038 OFF) ---")
results_a = run_backtest(cfg_a, verbose=False)
agg_a = summarize(results_a, t1_set)

# ── Scenario B: MKT-038 ON ───────────────────────────────────────────────
cfg_b = replace(shared_cfg(), fomc_t1_callonly_enabled=True)
print("\n--- Scenario B: fomc_t1_callonly_enabled = True (MKT-038 ON, live) ---")
results_b = run_backtest(cfg_b, verbose=False)
agg_b = summarize(results_b, t1_set)


# ── Print overall comparison ─────────────────────────────────────────────
print()
print("=" * 90)
print("OVERALL P&L (entire window — only T+1 days differ, rest is identical)")
print("=" * 90)
print(f"{'Metric':<40} | {'A: OFF':>14} | {'B: ON':>14} | {'Delta':>12}")
print("-" * 90)

def row(label, a, b, fmt="{:.0f}"):
    d = b - a
    sgn = "+" if d >= 0 else ""
    va = fmt.format(a); vb = fmt.format(b); vd = f"{sgn}{fmt.format(d)}"
    print(f"{label:<40} | {va:>14} | {vb:>14} | {vd:>12}")

row("Total P&L (full window)", agg_a["total_pnl"], agg_b["total_pnl"], "${:.2f}")
row("Trading days simulated", agg_a["n_days"], agg_b["n_days"], "{:.0f}")

print()
print("=" * 90)
print(f"FOCUSED: FOMC T+1 DAYS ONLY  ({agg_a['t1_days_seen']} days seen)")
print("=" * 90)
row("T+1 P&L total", agg_a["t1_pnl"], agg_b["t1_pnl"], "${:.2f}")
row("T+1 winning days", agg_a["t1_wins"], agg_b["t1_wins"], "{:.0f}")
row("T+1 losing days", agg_a["t1_losses"], agg_b["t1_losses"], "{:.0f}")
row("T+1 entries (non-skipped)", agg_a["t1_entries"], agg_b["t1_entries"], "{:.0f}")
row("T+1 full-IC entries", agg_a["t1_full_ic"], agg_b["t1_full_ic"], "{:.0f}")
row("T+1 call-only entries", agg_a["t1_call_only"], agg_b["t1_call_only"], "{:.0f}")
row("T+1 stopped entries (either side)", agg_a["t1_stopped_entries"], agg_b["t1_stopped_entries"], "{:.0f}")

print()
delta = agg_b["total_pnl"] - agg_a["total_pnl"]
print(f">>> Net P&L delta from MKT-038: ${delta:+.2f} over {agg_a['t1_days_seen']} T+1 days <<<")
if agg_a["t1_days_seen"] > 0:
    print(f"    Per-T+1-day delta: ${delta / agg_a['t1_days_seen']:+.2f}")
print(f"    Live-adjusted (34% rule): ${delta * 0.34:+.2f} over the same span")

# ── Per-day detail ───────────────────────────────────────────────────────
print()
print("=" * 90)
print("PER-T+1-DAY BREAKDOWN")
print("=" * 90)
print(f"{'Date':<12} {'OFF P&L':>10} {'ON P&L':>10} {'Delta':>10}  Notes")
print("-" * 80)
# Build lookup by date
by_date_a = {r.date: r for r in results_a}
by_date_b = {r.date: r for r in results_b}
for d in sorted(t1_in_window):
    ra = by_date_a.get(d)
    rb = by_date_b.get(d)
    if ra is None or rb is None:
        print(f"{str(d):<12} (no data — weekend/holiday?)")
        continue
    delta_d = rb.net_pnl - ra.net_pnl
    sgn = "+" if delta_d >= 0 else ""
    # Summarize entry composition in B (MKT-038 ON)
    call_only_ct = sum(1 for e in rb.entries if e.entry_type == "call_only")
    full_ic_ct = sum(1 for e in rb.entries if e.entry_type == "full_ic")
    skip_ct = sum(1 for e in rb.entries if e.entry_type == "skipped")
    note = f"B: {call_only_ct} call-only, {full_ic_ct} IC, {skip_ct} skip"
    print(f"{str(d):<12} ${ra.net_pnl:>+8.0f} ${rb.net_pnl:>+8.0f} ${sgn}{delta_d:>+7.0f}  {note}")
