#!/usr/bin/env python3
"""Comprehensive FOMC A/B backtest across all 3 day types.

Question: on FOMC Day 1, Day 2, and T+1, should we trade normally or skip?

Methodology:
  1. Single baseline run: live_config with MKT-038 OFF, no skips, no fomc_blackout.
     This gives us the "trade normal" P&L on every day.
  2. Measure per-day-type P&L contribution. For 0DTE each day is independent,
     so "skip day X" = "baseline total minus day X P&L".
  3. For T+1 we also compare vs MKT-038 ON (current live behavior) to show
     the MKT-038-specific delta.

2025 data: all 8 FOMC meetings available
2026 data (thru Apr 10): Jan 27-28-29, Mar 17-18-19

Audit step at the start: prints number of entries per FOMC day to verify
the VIX regime is correctly dropping E#1 (should see max 2 base + 1 conditional
= 3 entries per day, not 4).
"""
import sys
from datetime import date, timedelta
from pathlib import Path
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import live_config
from backtest.engine import run_backtest
from shared.event_calendar import (
    get_fomc_announcement_dates,
    get_fomc_dates,
)


START = date(2025, 1, 2)
END = date(2026, 4, 10)

# ── Identify FOMC days in window ─────────────────────────────────────────
# Day 2 = announcement days (from calendar function)
# Day 1 = the day before each Day 2
# T+1 = the day after each Day 2
day2_all = sorted(get_fomc_announcement_dates(2025) + get_fomc_announcement_dates(2026))
day2_in_window = [d for d in day2_all if START <= d <= END]
day1_in_window = [d - timedelta(days=1) for d in day2_in_window if START <= (d - timedelta(days=1)) <= END]
t1_in_window = [d + timedelta(days=1) for d in day2_in_window if START <= (d + timedelta(days=1)) <= END]

print("=" * 95)
print("COMPREHENSIVE FOMC A/B BACKTEST — Day 1, Day 2, T+1")
print(f"Period: {START} → {END}  (15 months)")
print("=" * 95)
print(f"\nDay 1 (pre-announcement) dates in window: {len(day1_in_window)}")
for d in day1_in_window:
    print(f"  {d} ({d.strftime('%a')})")
print(f"\nDay 2 (FOMC announcement, 2:00 PM) dates in window: {len(day2_in_window)}")
for d in day2_in_window:
    print(f"  {d} ({d.strftime('%a')})")
print(f"\nT+1 (day after announcement) dates in window: {len(t1_in_window)}")
for d in t1_in_window:
    print(f"  {d} ({d.strftime('%a')})")

day1_set = set(day1_in_window)
day2_set = set(day2_in_window)
t1_set = set(t1_in_window)


def baseline_cfg():
    """Current live config matching VM — MKT-038 OFF, no skips."""
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
        base_entry_downday_callonly_pct=None,
        fomc_announcement_skip=False,    # DON'T skip — measure actual P&L
        fomc_t1_callonly_enabled=False,  # DON'T force call-only — measure normal trading
    )


def mkt038_on_cfg():
    """Same as baseline but with MKT-038 ON (current live)."""
    return replace(
        baseline_cfg(),
        fomc_t1_callonly_enabled=True,
    )


# ── Run two scenarios ────────────────────────────────────────────────────
print("\n\n--- Run 1 of 2: BASELINE (MKT-038 OFF, trade normal every day) ---")
baseline = run_backtest(baseline_cfg(), verbose=False)

print("\n--- Run 2 of 2: MKT-038 ON (current live on T+1) ---")
mkt038_on = run_backtest(mkt038_on_cfg(), verbose=False)


# ── AUDIT: verify entry counts per FOMC day to catch regime misconfig ───
print("\n" + "=" * 95)
print("AUDIT: entry counts per FOMC-related day (should be ≤ 3: 2 base + 1 conditional)")
print("=" * 95)
print(f"{'Date':<12} {'Type':<8} {'Entries':>7} {'Full-IC':>7} {'Call-only':>9} {'Put-only':>8} {'Skipped':>7}  Notes")
print("-" * 85)
by_date = {r.date: r for r in baseline}

def label_day(d):
    if d in day1_set: return "Day 1"
    if d in day2_set: return "Day 2"
    if d in t1_set: return "T+1"
    return "—"

fomc_days_sorted = sorted(day1_set | day2_set | t1_set)
max_entries_seen = 0
for d in fomc_days_sorted:
    r = by_date.get(d)
    if r is None:
        print(f"{str(d):<12} {label_day(d):<8} [no data — weekend/holiday]")
        continue
    full_ic = sum(1 for e in r.entries if e.entry_type == "full_ic")
    call_only = sum(1 for e in r.entries if e.entry_type == "call_only")
    put_only = sum(1 for e in r.entries if e.entry_type == "put_only")
    skipped = sum(1 for e in r.entries if e.entry_type == "skipped")
    total = full_ic + call_only + put_only
    max_entries_seen = max(max_entries_seen, total)
    vix_str = f"VIX {r.vix_at_open:.1f}" if hasattr(r, 'vix_at_open') and r.vix_at_open else ""
    print(f"{str(d):<12} {label_day(d):<8} {total:>7} {full_ic:>7} {call_only:>9} {put_only:>8} {skipped:>7}  {vix_str}")

print(f"\nMax non-skipped entries on any FOMC day: {max_entries_seen}")
print(f"Expected max: 3 (E#2 + E#3 base + 1 conditional E6)")
if max_entries_seen > 3:
    print("⚠️  WARNING: more than 3 entries observed — VIX regime may not be dropping E#1 correctly!")
else:
    print("✅ OK: entry counts consistent with live config (E#1 dropped, max 2 base + 1 conditional)")


# ── Aggregate P&L per day type ───────────────────────────────────────────
def day_type_pnl(results, dates_set):
    total = 0.0
    days_seen = 0
    wins = 0
    losses = 0
    entries_total = 0
    for r in results:
        if r.date not in dates_set:
            continue
        days_seen += 1
        total += r.net_pnl
        if r.net_pnl > 0: wins += 1
        elif r.net_pnl < 0: losses += 1
        entries_total += sum(1 for e in r.entries if e.entry_type != "skipped")
    return total, days_seen, wins, losses, entries_total


baseline_total = sum(r.net_pnl for r in baseline)

d1_pnl, d1_n, d1_w, d1_l, d1_e = day_type_pnl(baseline, day1_set)
d2_pnl, d2_n, d2_w, d2_l, d2_e = day_type_pnl(baseline, day2_set)
t1_pnl, t1_n, t1_w, t1_l, t1_e = day_type_pnl(baseline, t1_set)

mkt038_on_total = sum(r.net_pnl for r in mkt038_on)
mkt038_on_t1_pnl, _, _, _, _ = day_type_pnl(mkt038_on, t1_set)


# ── Report: compare trade-normal vs skip for each day type ──────────────
print()
print("=" * 95)
print(f"BASELINE TOTAL P&L (trade every day, MKT-038 OFF): ${baseline_total:,.0f}")
print(f"MKT-038 ON TOTAL (trade every day, T+1 call-only):  ${mkt038_on_total:,.0f}")
print("=" * 95)

def report(label, dates_set, day_pnl, n, wins, losses, entries):
    if n == 0:
        print(f"\n{label}: no days seen in window")
        return
    print(f"\n--- {label} ---")
    print(f"Days seen: {n}   Entries placed: {entries}   Avg entries/day: {entries/n:.1f}")
    print(f"P&L contribution (trade normal): ${day_pnl:+,.0f}")
    print(f"Win/Loss days: {wins}W / {losses}L")
    print(f"P&L if we SKIPPED all {label} days:  ${baseline_total - day_pnl:+,.0f}")
    delta_skip_vs_trade = -day_pnl
    sgn = "+" if delta_skip_vs_trade >= 0 else ""
    print(f">>> Net delta of SKIPPING vs TRADING: ${sgn}{delta_skip_vs_trade:,.0f} over {n} days")
    live_adj = delta_skip_vs_trade * 0.34
    sgn2 = "+" if live_adj >= 0 else ""
    print(f"    Live-adjusted (34% rule): ${sgn2}{live_adj:,.0f}")

report("FOMC Day 1 (pre-announcement)", day1_set, d1_pnl, d1_n, d1_w, d1_l, d1_e)
report("FOMC Day 2 (announcement, 2 PM)", day2_set, d2_pnl, d2_n, d2_w, d2_l, d2_e)
report("FOMC T+1 (day after) — vs OFF baseline", t1_set, t1_pnl, t1_n, t1_w, t1_l, t1_e)

# Extra comparison for T+1: vs current live (MKT-038 ON)
print(f"\n    T+1 extra: current live MKT-038 ON = ${mkt038_on_t1_pnl:+,.0f} over {t1_n} days")
print(f"    Trade-normal vs MKT-038 ON:    ${d1_pnl-mkt038_on_t1_pnl if False else 0:+,.0f}  <-- ignore, separate run")
delta_038 = mkt038_on_total - baseline_total
sgn = "+" if delta_038 >= 0 else ""
print(f"    MKT-038 ON vs OFF (full window): ${sgn}{delta_038:,.0f}")


# ── Per-day breakdown ────────────────────────────────────────────────────
def per_day_table(label, dates_set):
    print(f"\n{label} — per-day P&L:")
    print(f"{'Date':<12} {'DoW':<5} {'P&L':>8} {'Entries':>7}  Composition")
    print("-" * 70)
    for d in sorted(dates_set):
        r = by_date.get(d)
        if r is None:
            print(f"{str(d):<12} {d.strftime('%a'):<5} {'(no data)':>8}")
            continue
        parts = []
        full_ic = sum(1 for e in r.entries if e.entry_type == "full_ic")
        call_only = sum(1 for e in r.entries if e.entry_type == "call_only")
        put_only = sum(1 for e in r.entries if e.entry_type == "put_only")
        skipped = sum(1 for e in r.entries if e.entry_type == "skipped")
        if full_ic: parts.append(f"{full_ic} IC")
        if call_only: parts.append(f"{call_only} call-only")
        if put_only: parts.append(f"{put_only} put-only")
        if skipped: parts.append(f"{skipped} skip")
        comp = ", ".join(parts) if parts else "no entries"
        entries_total = full_ic + call_only + put_only
        print(f"{str(d):<12} {d.strftime('%a'):<5} ${r.net_pnl:>+7.0f} {entries_total:>7}  {comp}")

per_day_table("FOMC Day 1", day1_set)
per_day_table("FOMC Day 2", day2_set)
per_day_table("FOMC T+1 (baseline)", t1_set)

print("\n" + "=" * 95)
print("SUMMARY — skip vs trade for each day type")
print("=" * 95)
print(f"{'Day type':<25} {'Trade P&L':>12} {'Skip delta':>12} {'Live-adjusted':>14} {'Rec':>10}")
print("-" * 80)

def summary_row(label, day_pnl, n):
    skip_delta = -day_pnl
    live = skip_delta * 0.34
    rec = "SKIP" if skip_delta > 500 else ("skip?" if skip_delta > 0 else "TRADE")
    print(f"{label:<25} ${day_pnl:>+10.0f} ${skip_delta:>+10.0f} ${live:>+12.0f} {rec:>10}")

summary_row("Day 1 (pre-announcement)", d1_pnl, d1_n)
summary_row("Day 2 (announcement)", d2_pnl, d2_n)
summary_row("T+1 (day after) — OFF", t1_pnl, t1_n)
summary_row("T+1 — current live (038 ON)", mkt038_on_t1_pnl, t1_n)
print()
