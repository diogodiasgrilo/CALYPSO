#!/usr/bin/env python3
"""Sweep max_spread_width — answer: does narrowing spreads hurt or help EV?

Runs the backtest engine at varying max_spread_width values over the last ~90
trading days using today's live config as baseline. Isolates the spread-width
variable (all other params equal). Reports per-config:

  - Net P&L total over period
  - EV per entry (net P&L / entries)
  - Stop rate (entries stopped / entries placed)
  - Avg credit per entry
  - Avg margin held per entry (= spread_width × 100 − avg_credit)
  - 2-contract check: margin for 3 entries vs $51,816 available
  - Sharpe (annualized)

Test configs:
  1. 50pt  — aggressive narrow (best capital efficiency per empirical)
  2. 60pt  — narrow
  3. 75pt  — middle, fits 2c×3 comfortably
  4. 90pt  — fits 2c×3 tightly
  5. 110pt — current live (baseline)
  6. 150pt — stretched — more data, tests if wider is better

Runs against ~90 recent trading days to keep runtime under 10 minutes.
"""
import sys
import statistics
from datetime import date
from pathlib import Path
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import live_config
from backtest.engine import run_backtest

# 90 trading days ≈ 4.5 calendar months
START = date(2025, 12, 1)
END = date(2026, 4, 10)  # end of clean data window

WIDTHS = [50, 60, 75, 90, 110, 150]

ACCOUNT_MARGIN_2C = 51_816  # your current Saxo MarginAvailableForTrading

print("=" * 110)
print(f"max_spread_width sweep  |  {START} → {END}  |  live config baseline")
print("=" * 110)

results_by_width = {}

for w in WIDTHS:
    cfg = replace(
        live_config(),
        start_date=START,
        end_date=END,
        data_resolution="1min",
        max_spread_width=w,
        # keep everything else default-live
        conditional_upday_e6_enabled=True,
        conditional_downday_e6_enabled=True,
        conditional_downday_threshold_pct=0.0025,
        base_entry_downday_callonly_pct=None,
        fomc_t1_callonly_enabled=False,
        fomc_t1_skip_enabled=True,
        fomc_announcement_skip=False,
    )
    print(f"\n[Running w={w}pt…]", flush=True)
    results = run_backtest(cfg, verbose=False)
    results_by_width[w] = results
    print(f"  {len(results)} days simulated", flush=True)

# ── Aggregate per width ────────────────────────────────────────────────

print()
print("=" * 110)
print("RESULTS")
print("=" * 110)

hdr = f"{'Width':<8}{'N_days':<8}{'Entries':<10}{'StopRt%':<10}{'AvgCred':<10}{'AvgMargin':<12}{'NetPnL':<12}{'EV/entry':<12}{'Sharpe':<10}{'2c×3 fits?':<15}"
print(hdr)
print("-" * len(hdr))

for w in WIDTHS:
    results = results_by_width[w]
    if not results:
        continue

    n_days = len(results)
    total_entries = 0
    stopped = 0
    total_credit = 0.0
    total_margin_held_days = 0.0  # sum of max intraday margin across days

    for r in results:
        for e in r.entries:
            if e.entry_type == "skipped":
                continue
            total_entries += 1
            entry_credit = (e.call_credit or 0) + (e.put_credit or 0)
            total_credit += entry_credit
            if e.call_outcome == "stopped" or e.put_outcome == "stopped":
                stopped += 1

    avg_cred = total_credit / total_entries if total_entries else 0
    # margin per entry at 1c: spread_width × 100 − credit (per Saxo IC model)
    avg_margin = (w * 100) - avg_cred
    stop_rt = (stopped / total_entries * 100) if total_entries else 0
    net_pnl = sum(r.net_pnl for r in results)
    ev_per_entry = net_pnl / total_entries if total_entries else 0

    daily_pnls = [r.net_pnl for r in results]
    mean = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    daily_sharpe = (mean / stdev) if stdev > 0 else 0
    ann_sharpe = daily_sharpe * (252 ** 0.5)

    # 2 contracts × 3 entries margin needed
    margin_2c_3 = 3 * 2 * avg_margin
    fits_2c = "YES" if margin_2c_3 < ACCOUNT_MARGIN_2C else "NO"
    fits_str = f"{fits_2c} (${margin_2c_3:,.0f})"

    print(f"{w:<8}{n_days:<8}{total_entries:<10}{stop_rt:<10.1f}${avg_cred:<9.0f}${avg_margin:<11,.0f}${net_pnl:<11,.0f}${ev_per_entry:<11.1f}{ann_sharpe:<10.2f}{fits_str:<15}")

print()
print("Notes:")
print("- Values are from ThetaData backtest — historically ~3x optimistic vs live Saxo (apply × 0.34 for realistic live projection)")
print("- AvgMargin = theoretical (spread_width × 100 − avg_credit per contract)")
print("- 2c×3 fits check: 3 entries × 2 contracts margin vs $51,816 Saxo available")
print("- For live comparison: current 110pt is baseline; narrower widths test 'does long leg help more' hypothesis")
