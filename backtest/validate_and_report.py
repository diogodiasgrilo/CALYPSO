"""
HYDRA Final Config Validation + Full Report

Phase 1: Overfitting validation (year-by-year, half-sample, bootstrap)
Phase 2: Full simulation report (6 CSV files matching previous format)

Run: python -m backtest.validate_and_report
"""
import csv
import math
import os
import random
import statistics
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from backtest.config import live_config
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)
OUTPUT_DIR = Path("backtest/results/final_report")


def final_config() -> "BacktestConfig":
    """The fully validated final config with VIX regime."""
    cfg = live_config()
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [1.25, None, None, None]
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    return cfg


def compute_metrics(daily_pnls: List[float]) -> Dict[str, Any]:
    n = len(daily_pnls)
    if n == 0:
        return {"days": 0, "total_pnl": 0, "mean": 0, "median": 0, "std": 0,
                "sharpe": 0, "sortino": 0, "max_dd": 0, "calmar": 0,
                "win_rate": 0, "wins": 0, "losses": 0, "flats": 0,
                "best_day": 0, "worst_day": 0}

    total = sum(daily_pnls)
    mean = statistics.mean(daily_pnls)
    median = statistics.median(daily_pnls)
    std = statistics.stdev(daily_pnls) if n > 1 else 0
    sharpe = mean / std * math.sqrt(252) if std > 0 else 0

    neg = [p for p in daily_pnls if p < 0]
    dd_dev = (sum(p ** 2 for p in neg) / n) ** 0.5 if neg else 0
    sortino = mean / dd_dev * math.sqrt(252) if dd_dev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    calmar = (mean * 252) / abs(max_dd) if max_dd != 0 else 0

    wins = sum(1 for p in daily_pnls if p > 0)
    losses = sum(1 for p in daily_pnls if p < 0)
    flats = n - wins - losses

    # Streaks
    best_streak = worst_streak = cur_win = cur_lose = 0
    for p in daily_pnls:
        if p > 0:
            cur_win += 1; cur_lose = 0
            best_streak = max(best_streak, cur_win)
        elif p < 0:
            cur_lose += 1; cur_win = 0
            worst_streak = max(worst_streak, cur_lose)
        else:
            cur_win = 0; cur_lose = 0

    return {
        "days": n, "total_pnl": total, "mean": mean, "median": median, "std": std,
        "sharpe": sharpe, "sortino": sortino, "max_dd": max_dd, "calmar": calmar,
        "win_rate": wins / n * 100 if n > 0 else 0,
        "wins": wins, "losses": losses, "flats": flats,
        "best_day": max(daily_pnls), "worst_day": min(daily_pnls),
        "best_streak": best_streak, "worst_streak": worst_streak,
    }


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1: OVERFITTING VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def phase1_validate(results: List[DayResult]) -> bool:
    """Run overfitting validation. Returns True if strategy passes."""
    print(f"\n{'═'*80}")
    print(f"PHASE 1: OVERFITTING VALIDATION")
    print(f"{'═'*80}")

    daily_pnls = [r.net_pnl for r in results]
    m = compute_metrics(daily_pnls)
    print(f"\nFull-sample: Sharpe {m['sharpe']:.3f}, P&L ${m['total_pnl']:+,.0f}, "
          f"MaxDD ${m['max_dd']:,.0f}, Win {m['win_rate']:.1f}%")

    all_pass = True

    # ── 1A: Year-by-year stability ────────────────────────────────────
    print(f"\n── 1A: Year-by-Year Stability ──")
    years = sorted(set(r.date.year for r in results))
    negative_years = 0
    for yr in years:
        yr_pnls = [r.net_pnl for r in results if r.date.year == yr]
        ym = compute_metrics(yr_pnls)
        status = "✓" if ym["total_pnl"] > 0 else "✗"
        if ym["total_pnl"] <= 0:
            negative_years += 1
        print(f"  {yr}: {ym['days']:3d} days  P&L ${ym['total_pnl']:>+8,.0f}  "
              f"Sharpe {ym['sharpe']:>6.3f}  Win {ym['win_rate']:.0f}%  {status}")

    if negative_years > 0:
        print(f"  ✗ FAIL: {negative_years} year(s) with negative P&L")
        all_pass = False
    else:
        print(f"  ✓ PASS: All {len(years)} years profitable")

    # ── 1B: Half-sample cross-validation ──────────────────────────────
    print(f"\n── 1B: Half-Sample Cross-Validation ──")
    midpoint = date(2024, 6, 30)
    first_half = [r.net_pnl for r in results if r.date <= midpoint]
    second_half = [r.net_pnl for r in results if r.date > midpoint]

    m1 = compute_metrics(first_half)
    m2 = compute_metrics(second_half)
    print(f"  First half  ({START_DATE} → {midpoint}): {m1['days']} days, "
          f"Sharpe {m1['sharpe']:.3f}, P&L ${m1['total_pnl']:+,.0f}")
    print(f"  Second half ({date(2024,7,1)} → {END_DATE}): {m2['days']} days, "
          f"Sharpe {m2['sharpe']:.3f}, P&L ${m2['total_pnl']:+,.0f}")

    if m1["sharpe"] > 0 and m2["sharpe"] > 0:
        print(f"  ✓ PASS: Both halves have positive Sharpe")
    else:
        print(f"  ✗ FAIL: One or both halves have negative Sharpe")
        all_pass = False

    # ── 1C: Day-of-week consistency ───────────────────────────────────
    print(f"\n── 1C: Day-of-Week Consistency ──")
    dow_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
    negative_days = 0
    for dow in range(5):
        dow_pnls = [r.net_pnl for r in results if r.date.weekday() == dow]
        if not dow_pnls:
            continue
        avg = statistics.mean(dow_pnls)
        wr = sum(1 for p in dow_pnls if p > 0) / len(dow_pnls) * 100
        status = "✓" if avg > 0 else "✗"
        if avg <= 0:
            negative_days += 1
        print(f"  {dow_names[dow]}: {len(dow_pnls):3d} days  Avg ${avg:>+7.0f}  Win {wr:.0f}%  {status}")

    if negative_days > 0:
        print(f"  ✗ WARNING: {negative_days} day(s) with negative average P&L")
    else:
        print(f"  ✓ PASS: All 5 weekdays have positive average P&L")

    # ── 1D: Bootstrap confidence intervals ────────────────────────────
    print(f"\n── 1D: Bootstrap Confidence Intervals (10,000 resamples) ──")
    random.seed(42)
    n = len(daily_pnls)
    bootstrap_sharpes = []
    for _ in range(10000):
        sample = random.choices(daily_pnls, k=n)
        s_mean = statistics.mean(sample)
        s_std = statistics.stdev(sample) if len(sample) > 1 else 0
        bs = s_mean / s_std * math.sqrt(252) if s_std > 0 else 0
        bootstrap_sharpes.append(bs)

    bootstrap_sharpes.sort()
    p5 = bootstrap_sharpes[int(0.05 * len(bootstrap_sharpes))]
    p25 = bootstrap_sharpes[int(0.25 * len(bootstrap_sharpes))]
    p50 = bootstrap_sharpes[int(0.50 * len(bootstrap_sharpes))]
    p75 = bootstrap_sharpes[int(0.75 * len(bootstrap_sharpes))]
    p95 = bootstrap_sharpes[int(0.95 * len(bootstrap_sharpes))]

    print(f"  Actual Sharpe: {m['sharpe']:.3f}")
    print(f"  Bootstrap:  5th={p5:.3f}  25th={p25:.3f}  50th={p50:.3f}  "
          f"75th={p75:.3f}  95th={p95:.3f}")

    if p5 > 0:
        print(f"  ✓ PASS: Statistically significant (5th percentile {p5:.3f} > 0)")
    else:
        print(f"  ✗ FAIL: NOT statistically significant (5th percentile {p5:.3f} ≤ 0)")
        all_pass = False

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n{'─'*40}")
    if all_pass:
        print(f"  ✓ ALL VALIDATION CHECKS PASSED")
    else:
        print(f"  ✗ SOME CHECKS FAILED — review above")

    return all_pass


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2: FULL REPORT (6 CSV FILES)
# ═══════════════════════════════════════════════════════════════════════════

def phase2_report(results: List[DayResult], cfg):
    """Generate 6 CSV files matching the previous report format."""
    print(f"\n{'═'*80}")
    print(f"PHASE 2: GENERATING FULL REPORT")
    print(f"{'═'*80}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    daily_pnls = [r.net_pnl for r in results]
    m = compute_metrics(daily_pnls)

    all_entries = [e for r in results for e in r.entries]
    placed = [e for e in all_entries if e.entry_type != "skipped"]
    full_ics = [e for e in placed if e.entry_type == "full_ic"]
    call_onlys = [e for e in placed if e.entry_type == "call_only"]
    put_onlys = [e for e in placed if e.entry_type == "put_only"]
    total_stops = sum(
        (1 if e.call_outcome == "stopped" else 0) +
        (1 if e.put_outcome == "stopped" else 0)
        for e in placed
    )

    # Realistic live Sharpe estimate (backtest × 0.83 based on historical ratio)
    live_sharpe = m["sharpe"] * 0.83

    # ── Report 1: Summary ─────────────────────────────────────────────
    path = OUTPUT_DIR / "HYDRA_Report_1_Summary.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["HYDRA Strategy Report - Summary"])
        w.writerow([])
        w.writerow(["Metric", "Value"])
        w.writerow(["Report Date", dt.now().strftime("%Y-%m-%d")])
        w.writerow(["Data Resolution", "1-Minute Bars + Greeks"])
        w.writerow(["Period", f"{START_DATE} to {END_DATE}"])
        w.writerow(["Trading Days", m["days"]])
        w.writerow(["Account Margin", 35000])
        w.writerow([])
        w.writerow(["PERFORMANCE"])
        w.writerow(["Total Net P&L", m["total_pnl"]])
        w.writerow(["Mean Daily P&L", round(m["mean"], 2)])
        w.writerow(["Median Daily P&L", round(m["median"], 2)])
        w.writerow(["Std Dev Daily", round(m["std"], 2)])
        w.writerow(["Sharpe Ratio (annualised)", round(m["sharpe"], 3)])
        w.writerow(["Sortino Ratio (annualised)", round(m["sortino"], 3)])
        w.writerow(["Max Drawdown", abs(m["max_dd"])])
        w.writerow(["Calmar Ratio", round(m["calmar"], 3)])
        w.writerow([])
        w.writerow(["WIN/LOSS"])
        w.writerow(["Win Rate", f"{m['win_rate']:.1f}%"])
        w.writerow(["Winning Days", m["wins"]])
        w.writerow(["Losing Days", m["losses"]])
        w.writerow(["Flat Days", m["flats"]])
        w.writerow(["Best Day", m["best_day"]])
        w.writerow(["Worst Day", m["worst_day"]])
        w.writerow(["Best Winning Streak", f"{m['best_streak']} days"])
        w.writerow(["Worst Losing Streak", f"{m['worst_streak']} days"])
        w.writerow([])
        w.writerow(["ENTRIES"])
        w.writerow(["Entries Placed", len(placed)])
        w.writerow(["Total Stops", total_stops])
        w.writerow(["Stop Rate", f"{total_stops / (len(placed) * 2) * 100:.1f}%" if placed else "0%"])
        w.writerow(["Full Iron Condors", len(full_ics)])
        w.writerow(["Call-Only Entries", len(call_onlys)])
        w.writerow(["Put-Only Entries", len(put_onlys)])
        w.writerow([])
        w.writerow(["RETURNS"])
        years_span = (END_DATE - START_DATE).days / 365.25
        annual_roc = (m["total_pnl"] / years_span) / 35000 * 100
        w.writerow(["Annualised ROC on 35K", f"{annual_roc:.0f}%"])
        w.writerow(["Realistic Live Sharpe", f"{live_sharpe:.3f} (jitter-adjusted)"])
    print(f"  ✓ {path}")

    # ── Report 2: Configuration ───────────────────────────────────────
    path = OUTPUT_DIR / "HYDRA_Report_2_Configuration.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["HYDRA Strategy Configuration"])
        w.writerow([])
        w.writerow(["Parameter", "Value", "Description"])
        w.writerow(["Entry Times", " / ".join(cfg.entry_times), f"{len(cfg.entry_times)} iron condor entries per day"])
        e6_status = "ON at 14:00" if cfg.conditional_upday_e6_enabled else "OFF"
        w.writerow(["E6 Conditional", e6_status, f"Put-only when SPX up >= {cfg.upday_threshold_pct:.2f}%"])
        w.writerow(["E7 Conditional", "OFF", "Disabled"])
        w.writerow(["Spread Width", f"VIX x {cfg.spread_vix_multiplier} cap {cfg.max_spread_width}pt", "VIX-scaled"])
        w.writerow(["Min Call Credit", cfg.min_call_credit])
        w.writerow(["Min Put Credit", cfg.min_put_credit])
        w.writerow(["Call Credit Floor", cfg.call_credit_floor, "MKT-029"])
        w.writerow(["Put Credit Floor", cfg.put_credit_floor, "MKT-029"])
        w.writerow(["Call Stop Buffer", cfg.call_stop_buffer / 100])
        w.writerow(["Put Stop Buffer", cfg.put_stop_buffer / 100])
        dd_pct = cfg.base_entry_downday_callonly_pct
        w.writerow(["Down-Day Call-Only", f"{dd_pct:.2f}%" if dd_pct else "OFF"])
        w.writerow(["Theo Put Credit", cfg.downday_theoretical_put_credit / 100])
        w.writerow(["Upday Threshold", f"{cfg.upday_threshold_pct:.2f}%"])
        w.writerow(["FOMC T+1 Call-Only", "ON" if cfg.fomc_t1_callonly_enabled else "OFF"])
        w.writerow(["FOMC Day Skip", "ON" if getattr(cfg, "fomc_announcement_skip", False) else "OFF"])
        w.writerow(["Whipsaw Filter", f"{cfg.whipsaw_range_skip_mult}x EM" if cfg.whipsaw_range_skip_mult else "OFF"])
        w.writerow(["Put-Only Max VIX", cfg.put_only_max_vix])
        w.writerow(["VIX Regime", "ON" if cfg.vix_regime_enabled else "OFF"])
        if cfg.vix_regime_enabled:
            w.writerow(["VIX Breakpoints", str(cfg.vix_regime_breakpoints)])
            w.writerow(["VIX Max Entries", str(cfg.vix_regime_max_entries)])
            w.writerow(["VIX Put Buffer Override", str(cfg.vix_regime_put_stop_buffer)])
    print(f"  ✓ {path}")

    # ── Report 3: Yearly ──────────────────────────────────────────────
    path = OUTPUT_DIR / "HYDRA_Report_3_Yearly.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["HYDRA Yearly Performance"])
        w.writerow([])
        w.writerow(["Year", "Net P&L", "Days", "Win Rate", "Avg Daily", "Sharpe",
                     "Sortino", "Max DD", "Calmar", "Best Day", "Worst Day", "ROC on 35K"])
        years = sorted(set(r.date.year for r in results))
        for yr in years:
            yr_pnls = [r.net_pnl for r in results if r.date.year == yr]
            ym = compute_metrics(yr_pnls)
            roc = ym["total_pnl"] / 35000 * 100
            w.writerow([yr, ym["total_pnl"], ym["days"], f"{ym['win_rate']:.0f}%",
                         round(ym["mean"], 2), round(ym["sharpe"], 3),
                         round(ym["sortino"], 3), abs(ym["max_dd"]),
                         round(ym["calmar"], 3), ym["best_day"], ym["worst_day"],
                         f"{roc:.0f}%"])
    print(f"  ✓ {path}")

    # ── Report 4: Monthly ─────────────────────────────────────────────
    path = OUTPUT_DIR / "HYDRA_Report_4_Monthly.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["HYDRA Monthly Performance"])
        w.writerow([])
        w.writerow(["Month", "Net P&L", "Days", "Win Rate", "Avg Daily", "Sharpe",
                     "Sortino", "Max DD", "Entries", "Stops", "Stop Rate",
                     "Full IC", "Call-Only", "Put-Only", "Best Day", "Worst Day", "Note"])

        months = sorted(set((r.date.year, r.date.month) for r in results))
        for yr, mo in months:
            mo_results = [r for r in results if r.date.year == yr and r.date.month == mo]
            mo_pnls = [r.net_pnl for r in mo_results]
            mm = compute_metrics(mo_pnls)

            mo_entries = [e for r in mo_results for e in r.entries if e.entry_type != "skipped"]
            mo_stops = sum(
                (1 if e.call_outcome == "stopped" else 0) +
                (1 if e.put_outcome == "stopped" else 0)
                for e in mo_entries
            )
            mo_fics = sum(1 for e in mo_entries if e.entry_type == "full_ic")
            mo_cos = sum(1 for e in mo_entries if e.entry_type == "call_only")
            mo_pos = sum(1 for e in mo_entries if e.entry_type == "put_only")
            stop_rate = f"{mo_stops / (len(mo_entries) * 2) * 100:.1f}%" if mo_entries else "0%"

            note = ""
            if (yr == 2022 and mo == 5) or (yr == 2026 and mo == 3):
                note = "Partial month"

            w.writerow([f"{yr}-{mo:02d}", mm["total_pnl"], mm["days"],
                         f"{mm['win_rate']:.0f}%", round(mm["mean"], 2),
                         round(mm["sharpe"], 3), round(mm["sortino"], 3),
                         abs(mm["max_dd"]), len(mo_entries), mo_stops, stop_rate,
                         mo_fics, mo_cos, mo_pos, mm["best_day"], mm["worst_day"], note])
    print(f"  ✓ {path}")

    # ── Report 5: Day-of-Week + Seasonal Patterns ─────────────────────
    path = OUTPUT_DIR / "HYDRA_Report_5_Patterns.csv"
    dow_names = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["HYDRA Seasonal and Day-of-Week Patterns"])
        w.writerow([])

        # Seasonal
        w.writerow(["SEASONAL (Average by Calendar Month)"])
        w.writerow(["Month", "Avg Monthly P&L", "Sharpe", "Sortino", "Years",
                     "Best Month", "Worst Month"])
        for mo in range(1, 13):
            # Group by year-month
            yearly_totals = []
            for yr in sorted(set(r.date.year for r in results)):
                mo_pnls = [r.net_pnl for r in results
                           if r.date.year == yr and r.date.month == mo]
                if mo_pnls:
                    yearly_totals.append(sum(mo_pnls))
            if not yearly_totals:
                continue
            avg = statistics.mean(yearly_totals)
            # Compute Sharpe from the daily P&Ls for this calendar month across all years
            all_mo_daily = [r.net_pnl for r in results if r.date.month == mo]
            sm = compute_metrics(all_mo_daily)
            w.writerow([date(2000, mo, 1).strftime("%B"),
                         round(avg, 1), round(sm["sharpe"], 3), round(sm["sortino"], 3),
                         len(yearly_totals), max(yearly_totals), min(yearly_totals)])

        w.writerow([])
        w.writerow(["DAY-OF-WEEK"])
        w.writerow(["Day", "Total P&L", "Avg Daily", "Win Rate", "Sharpe", "Sortino", "Days"])
        for dow in range(5):
            dow_pnls = [r.net_pnl for r in results if r.date.weekday() == dow]
            if not dow_pnls:
                continue
            dm = compute_metrics(dow_pnls)
            w.writerow([dow_names[dow], dm["total_pnl"], round(dm["mean"], 2),
                         f"{dm['win_rate']:.0f}%", round(dm["sharpe"], 3),
                         round(dm["sortino"], 3), dm["days"]])
    print(f"  ✓ {path}")

    # ── Report 6: Daily P&L ───────────────────────────────────────────
    path = OUTPUT_DIR / "HYDRA_Report_6_Daily.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Net P&L", "Cumulative P&L", "Entries Placed",
                     "Stops Hit", "Full IC", "Call-Only", "Put-Only"])
        cum = 0.0
        for r in results:
            cum += r.net_pnl
            entries = [e for e in r.entries if e.entry_type != "skipped"]
            stops = sum(
                (1 if e.call_outcome == "stopped" else 0) +
                (1 if e.put_outcome == "stopped" else 0)
                for e in entries
            )
            fics = sum(1 for e in entries if e.entry_type == "full_ic")
            cos = sum(1 for e in entries if e.entry_type == "call_only")
            pos = sum(1 for e in entries if e.entry_type == "put_only")
            w.writerow([r.date.isoformat(), r.net_pnl, cum,
                         len(entries), stops, fics, cos, pos])
    print(f"  ✓ {path}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print(f"{'═'*80}")
    print(f"HYDRA FINAL CONFIG — VALIDATION + FULL REPORT")
    print(f"Period: {START_DATE} → {END_DATE} | 1-min | Real Greeks")
    print(f"{'═'*80}")

    # Run the backtest once
    print(f"\nRunning full backtest...", flush=True)
    cfg = final_config()
    results = run_backtest(cfg, verbose=True)
    print(f"\nBacktest complete: {len(results)} trading days\n")

    # Phase 1: Validate
    passed = phase1_validate(results)

    if not passed:
        print(f"\n⚠ VALIDATION FAILED — generating report anyway for analysis")

    # Phase 2: Generate report
    phase2_report(results, cfg)

    print(f"\n{'═'*80}")
    print(f"COMPLETE — 6 CSV files saved to {OUTPUT_DIR}/")
    print(f"{'═'*80}")


if __name__ == "__main__":
    main()
