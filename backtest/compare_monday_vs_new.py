"""
Side-by-side comparison: Monday (Mar 24) config vs new optimised config.

Run: python -m backtest.compare_monday_vs_new
"""
import csv
import statistics
from collections import defaultdict
from datetime import date, datetime as dt
from pathlib import Path
from typing import List

from backtest.config import BacktestConfig, live_config
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)


def monday_config() -> BacktestConfig:
    """Exact config running on HYDRA Monday morning March 24, 2026."""
    return BacktestConfig(
        entry_times=["10:15", "10:45", "11:15", "11:45", "12:15"],
        conditional_e6_enabled=False,
        conditional_e7_enabled=True,
        conditional_upday_e6_enabled=True,
        conditional_upday_e7_enabled=False,
        downday_threshold_pct=0.3,
        upday_threshold_pct=0.40,
        fomc_t1_callonly_enabled=True,
        call_starting_otm_multiplier=3.5,
        put_starting_otm_multiplier=4.0,
        spread_vix_multiplier=3.5,
        call_min_spread_width=50,
        put_min_spread_width=50,
        max_spread_width=50,
        min_call_credit=1.25,
        min_put_credit=2.25,
        put_credit_floor=2.15,
        call_stop_buffer=10.0,
        put_stop_buffer=100.0,
        one_sided_entries_enabled=True,
        put_only_max_vix=25.0,
        downday_theoretical_put_credit=1000.0,  # $10.00
        base_entry_downday_callonly_pct=None,    # disabled
        start_date=START_DATE,
        end_date=END_DATE,
        use_real_greeks=True,
    )


def new_config() -> BacktestConfig:
    """New optimised config after 2026-03-27 sweeps."""
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.use_real_greeks = True
    return cfg


def analyse(results: List[DayResult], label: str) -> dict:
    daily_pnls = [r.net_pnl for r in results]
    total_pnl = sum(daily_pnls)
    total_days = len(results)
    win_days = sum(1 for p in daily_pnls if p > 0)
    loss_days = sum(1 for p in daily_pnls if p < 0)

    mean = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0
    median_pnl = statistics.median(daily_pnls)

    neg_pnls = [p for p in daily_pnls if p < 0]
    downside_dev = (sum(p**2 for p in neg_pnls) / len(daily_pnls)) ** 0.5 if neg_pnls else 0
    sortino = mean / downside_dev * (252 ** 0.5) if downside_dev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    placed = sum(r.entries_placed for r in results)
    skipped = sum(r.entries_skipped for r in results)
    total_stops = sum(r.stops_hit for r in results)
    full_ic = sum(sum(1 for e in r.entries if e.entry_type == "full_ic") for r in results)
    call_only = sum(sum(1 for e in r.entries if e.entry_type == "call_only") for r in results)
    put_only = sum(sum(1 for e in r.entries if e.entry_type == "put_only") for r in results)

    best_day = max(daily_pnls)
    worst_day = min(daily_pnls)

    # Streaks
    win_streak = loss_streak = max_win = max_loss = 0
    for p in daily_pnls:
        if p > 0:
            win_streak += 1; loss_streak = 0
            max_win = max(max_win, win_streak)
        elif p < 0:
            loss_streak += 1; win_streak = 0
            max_loss = max(max_loss, loss_streak)
        else:
            win_streak = loss_streak = 0

    # Monthly
    monthly = defaultdict(lambda: {"pnl": 0, "days": 0, "wins": 0})
    for r in results:
        key = f"{r.date.year}-{r.date.month:02d}"
        monthly[key]["pnl"] += r.net_pnl
        monthly[key]["days"] += 1
        if r.net_pnl > 0: monthly[key]["wins"] += 1

    winning_months = sum(1 for v in monthly.values() if v["pnl"] > 0)
    best_month_key = max(monthly, key=lambda k: monthly[k]["pnl"])
    worst_month_key = min(monthly, key=lambda k: monthly[k]["pnl"])

    # Seasonal
    seasonal = defaultdict(list)
    for key, v in monthly.items():
        m = int(key.split("-")[1])
        seasonal[m].append(v["pnl"])

    # Day of week
    dow_pnl = defaultdict(lambda: {"total": 0, "days": 0})
    for r in results:
        wd = r.date.weekday()
        dow_pnl[wd]["total"] += r.net_pnl
        dow_pnl[wd]["days"] += 1

    # 2026 months
    m2026 = {}
    for target_month in [1, 2, 3]:
        mr = [r for r in results if r.date.year == 2026 and r.date.month == target_month]
        if mr:
            mp = [r.net_pnl for r in mr]
            m_mean = statistics.mean(mp)
            m_std = statistics.stdev(mp) if len(mp) > 1 else 0
            m_sharpe = m_mean / m_std * (252**0.5) if m_std > 0 else 0
            m_peak = m_cum = m_dd = 0.0
            for p in mp:
                m_cum += p; m_peak = max(m_peak, m_cum); m_dd = max(m_dd, m_peak - m_cum)
            m_wins = sum(1 for p in mp if p > 0)
            m_stops = sum(r.stops_hit for r in mr)
            m_placed = sum(r.entries_placed for r in mr)
            m2026[target_month] = {
                "pnl": sum(mp), "days": len(mp), "wins": m_wins,
                "sharpe": m_sharpe, "max_dd": m_dd,
                "placed": m_placed, "stops": m_stops,
            }

    return {
        "label": label,
        "days": total_days, "total_pnl": total_pnl,
        "mean_daily": mean, "median_daily": median_pnl,
        "stdev": stdev, "sharpe": sharpe, "sortino": sortino,
        "max_dd": max_dd, "calmar": calmar,
        "win_days": win_days, "loss_days": loss_days,
        "win_rate": win_days / total_days * 100 if total_days else 0,
        "best_day": best_day, "worst_day": worst_day,
        "placed": placed, "skipped": skipped,
        "total_stops": total_stops,
        "stop_rate": total_stops / placed * 100 if placed else 0,
        "full_ic": full_ic, "call_only": call_only, "put_only": put_only,
        "max_win_streak": max_win, "max_loss_streak": max_loss,
        "winning_months": winning_months, "total_months": len(monthly),
        "best_month": f"{best_month_key} (${monthly[best_month_key]['pnl']:,.0f})",
        "worst_month": f"{worst_month_key} (${monthly[worst_month_key]['pnl']:,.0f})",
        "seasonal": seasonal, "dow_pnl": dow_pnl,
        "m2026": m2026, "monthly": monthly,
        "results": results,
    }


def prt(msg):
    print(msg)


if __name__ == "__main__":
    prt(f"\n{'='*80}")
    prt(f"  HYDRA: Monday (Mar 24) Config vs New Optimised Config")
    prt(f"  Period: {START_DATE} → {END_DATE}  |  Real Greeks strict mode")
    prt(f"{'='*80}")

    prt("\n  Running Monday config...")
    mon_results = run_backtest(monday_config(), verbose=False)
    mon = analyse(mon_results, "Monday (Mar 24)")

    prt("  Running new optimised config...")
    new_results = run_backtest(new_config(), verbose=False)
    new = analyse(new_results, "New Optimised")

    # ── Config differences ───────────────────────────────────────────────────
    prt(f"\n{'='*80}")
    prt(f"  CONFIG DIFFERENCES")
    prt(f"{'='*80}")
    prt(f"  {'Parameter':<35} {'Monday':<20} {'New':<20}")
    prt(f"  {'─'*75}")
    prt(f"  {'Spread width':<35} {'Fixed 50pt':<20} {'VIX-scaled 25-100pt':<20}")
    prt(f"  {'spread_vix_multiplier':<35} {'3.5':<20} {'4.0':<20}")
    prt(f"  {'E7 downday call-only':<35} {'ON':<20} {'OFF':<20}")
    prt(f"  {'E6 upday put-only':<35} {'ON':<20} {'OFF':<20}")
    prt(f"  {'downday_theoretical_put_credit':<35} {'$10.00':<20} {'$1.50':<20}")
    prt(f"  {'base_entry_downday_callonly_pct':<35} {'Disabled':<20} {'0.60%':<20}")
    prt(f"  {'upday_threshold_pct':<35} {'0.40%':<20} {'N/A (E6 off)':<20}")
    prt(f"  {'min_call_credit':<35} {'$1.25':<20} {'$1.25':<20}")
    prt(f"  {'min_put_credit':<35} {'$2.25':<20} {'$2.25':<20}")

    # ── Head-to-head comparison ──────────────────────────────────────────────
    prt(f"\n{'='*80}")
    prt(f"  HEAD-TO-HEAD COMPARISON")
    prt(f"{'='*80}")

    def row(metric, key, fmt, higher_better=True):
        v1 = mon[key]; v2 = new[key]
        s1 = fmt.format(v1); s2 = fmt.format(v2)
        if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
            diff = v2 - v1
            if key == "max_dd":
                arrow = "▼" if diff < 0 else "▲" if diff > 0 else "="
                diff_s = f"{'+'if diff>0 else ''}{fmt.format(diff)} {arrow}"
            elif higher_better:
                arrow = "▲" if diff > 0 else "▼" if diff < 0 else "="
                diff_s = f"{'+'if diff>0 else ''}{fmt.format(diff)} {arrow}"
            else:
                arrow = "▼" if diff < 0 else "▲" if diff > 0 else "="
                diff_s = f"{'+'if diff>0 else ''}{fmt.format(diff)} {arrow}"
        else:
            diff_s = ""
        prt(f"  {metric:<28} {s1:>14} {s2:>14}  {diff_s}")

    prt(f"  {'Metric':<28} {'Monday':>14} {'New':>14}  {'Δ Change'}")
    prt(f"  {'─'*75}")
    row("Trading days",        "days",           "{:,.0f}")
    row("Total net P&L",       "total_pnl",      "${:,.0f}")
    row("Mean daily P&L",      "mean_daily",     "${:,.2f}")
    row("Median daily P&L",    "median_daily",   "${:,.2f}")
    row("Std dev daily",       "stdev",          "${:,.2f}", False)
    row("Sharpe (annualised)", "sharpe",         "{:.3f}")
    row("Sortino (annualised)","sortino",        "{:.3f}")
    row("Max drawdown",        "max_dd",         "${:,.0f}", False)
    row("Calmar ratio",        "calmar",         "{:.3f}")
    row("Win rate %",          "win_rate",       "{:.1f}%")
    row("Best day",            "best_day",       "${:,.0f}")
    row("Worst day",           "worst_day",      "${:,.0f}")
    row("Entries placed",      "placed",         "{:,.0f}")
    row("Entries skipped",     "skipped",        "{:,.0f}", False)
    row("Total stops",         "total_stops",    "{:,.0f}", False)
    row("Stop rate %",         "stop_rate",      "{:.1f}%", False)
    row("Full IC",             "full_ic",        "{:,.0f}")
    row("Call-only",           "call_only",      "{:,.0f}")
    row("Put-only",            "put_only",       "{:,.0f}")
    row("Max win streak",      "max_win_streak", "{:.0f}")
    row("Max loss streak",     "max_loss_streak","{:.0f}", False)
    row("Winning months",      "winning_months", "{:.0f}")

    prt(f"\n  Best month (Mon):  {mon['best_month']}")
    prt(f"  Best month (New):  {new['best_month']}")
    prt(f"  Worst month (Mon): {mon['worst_month']}")
    prt(f"  Worst month (New): {new['worst_month']}")

    # ── 2026 Drill-Down ──────────────────────────────────────────────────────
    prt(f"\n{'='*80}")
    prt(f"  2026 MONTHLY DRILL-DOWN (side by side)")
    prt(f"{'='*80}")
    month_names = {1: "Jan", 2: "Feb", 3: "Mar"}
    for m in [1, 2, 3]:
        m1 = mon["m2026"].get(m)
        m2 = new["m2026"].get(m)
        if not m1 or not m2:
            prt(f"\n  {month_names[m]} 2026: No data")
            continue
        prt(f"\n  ── {month_names[m]} 2026 ──")
        prt(f"  {'Metric':<24} {'Monday':>12} {'New':>12} {'Δ':>12}")
        prt(f"  {'─'*62}")
        pnl_diff = m2["pnl"] - m1["pnl"]
        prt(f"  {'Net P&L':<24} ${m1['pnl']:>11,.0f} ${m2['pnl']:>11,.0f} ${pnl_diff:>+11,.0f}")
        prt(f"  {'Win rate':<24} {m1['wins']}/{m1['days']:>3} = {m1['wins']/m1['days']*100:>3.0f}%  {m2['wins']}/{m2['days']:>3} = {m2['wins']/m2['days']*100:>3.0f}%")
        prt(f"  {'Sharpe':<24} {m1['sharpe']:>12.3f} {m2['sharpe']:>12.3f} {m2['sharpe']-m1['sharpe']:>+12.3f}")
        prt(f"  {'Max DD':<24} ${m1['max_dd']:>11,.0f} ${m2['max_dd']:>11,.0f} ${m2['max_dd']-m1['max_dd']:>+11,.0f}")
        prt(f"  {'Entries placed':<24} {m1['placed']:>12} {m2['placed']:>12} {m2['placed']-m1['placed']:>+12}")
        prt(f"  {'Stops':<24} {m1['stops']:>12} {m2['stops']:>12} {m2['stops']-m1['stops']:>+12}")
        sr1 = m1['stops']/m1['placed']*100 if m1['placed'] else 0
        sr2 = m2['stops']/m2['placed']*100 if m2['placed'] else 0
        prt(f"  {'Stop rate':<24} {sr1:>11.1f}% {sr2:>11.1f}% {sr2-sr1:>+11.1f}%")

    # ── Seasonal comparison ──────────────────────────────────────────────────
    prt(f"\n{'='*80}")
    prt(f"  SEASONAL COMPARISON (avg monthly P&L)")
    prt(f"{'='*80}")
    month_labels = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    prt(f"  {'Month':<6} {'Monday':>10} {'New':>10} {'Δ':>10}")
    prt(f"  {'─'*38}")
    for m in range(1, 13):
        s1 = mon["seasonal"].get(m, [])
        s2 = new["seasonal"].get(m, [])
        avg1 = statistics.mean(s1) if s1 else 0
        avg2 = statistics.mean(s2) if s2 else 0
        diff = avg2 - avg1
        prt(f"  {month_labels[m]:<6} ${avg1:>9,.0f} ${avg2:>9,.0f} ${diff:>+9,.0f}")

    # ── Day-of-week comparison ───────────────────────────────────────────────
    prt(f"\n{'='*80}")
    prt(f"  DAY-OF-WEEK COMPARISON (avg daily P&L)")
    prt(f"{'='*80}")
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    prt(f"  {'Day':<6} {'Monday':>10} {'New':>10} {'Δ':>10}")
    prt(f"  {'─'*38}")
    for wd in range(5):
        d1 = mon["dow_pnl"].get(wd, {"total": 0, "days": 1})
        d2 = new["dow_pnl"].get(wd, {"total": 0, "days": 1})
        avg1 = d1["total"] / d1["days"] if d1["days"] else 0
        avg2 = d2["total"] / d2["days"] if d2["days"] else 0
        prt(f"  {dow_names[wd]:<6} ${avg1:>9,.2f} ${avg2:>9,.2f} ${avg2-avg1:>+9,.2f}")

    # ── Monthly P&L comparison ───────────────────────────────────────────────
    prt(f"\n{'='*80}")
    prt(f"  FULL MONTHLY P&L COMPARISON")
    prt(f"{'='*80}")
    all_months = sorted(set(list(mon["monthly"].keys()) + list(new["monthly"].keys())))
    prt(f"  {'Month':<10} {'Monday':>10} {'New':>10} {'Δ':>10} {'Winner':>10}")
    prt(f"  {'─'*52}")
    mon_wins = new_wins = ties = 0
    for key in all_months:
        p1 = mon["monthly"].get(key, {"pnl": 0})["pnl"]
        p2 = new["monthly"].get(key, {"pnl": 0})["pnl"]
        diff = p2 - p1
        if diff > 50: winner = "NEW ✓"; new_wins += 1
        elif diff < -50: winner = "MON ✓"; mon_wins += 1
        else: winner = "~tie"; ties += 1
        prt(f"  {key:<10} ${p1:>9,.0f} ${p2:>9,.0f} ${diff:>+9,.0f} {winner:>10}")

    prt(f"\n  Score: New wins {new_wins} months, Monday wins {mon_wins} months, {ties} ties")

    # ── Rolling 60-day comparison ────────────────────────────────────────────
    prt(f"\n{'='*80}")
    prt(f"  ROLLING 60-DAY P&L (worst periods)")
    prt(f"{'='*80}")

    for label, res_list in [("Monday", mon_results), ("New", new_results)]:
        pnls = [r.net_pnl for r in res_list]
        worst_60 = float('inf')
        worst_60_end = 0
        for i in range(59, len(pnls)):
            s = sum(pnls[i-59:i+1])
            if s < worst_60:
                worst_60 = s
                worst_60_end = i
        start_date = res_list[worst_60_end-59].date
        end_date = res_list[worst_60_end].date
        prt(f"  {label:<12}: ${worst_60:>9,.0f}  ({start_date} → {end_date})")

    # ── Summary verdict ──────────────────────────────────────────────────────
    prt(f"\n{'='*80}")
    prt(f"  VERDICT")
    prt(f"{'='*80}")
    sh_diff = new["sharpe"] - mon["sharpe"]
    pnl_diff = new["total_pnl"] - mon["total_pnl"]
    dd_diff = new["max_dd"] - mon["max_dd"]
    prt(f"  Sharpe:  {mon['sharpe']:.3f} → {new['sharpe']:.3f}  ({sh_diff:+.3f}, {sh_diff/mon['sharpe']*100:+.1f}%)")
    prt(f"  P&L:     ${mon['total_pnl']:,.0f} → ${new['total_pnl']:,.0f}  (${pnl_diff:+,.0f})")
    prt(f"  Max DD:  ${mon['max_dd']:,.0f} → ${new['max_dd']:,.0f}  (${dd_diff:+,.0f})")
    prt(f"  Months:  New wins {new_wins}/{len(all_months)}, Monday wins {mon_wins}/{len(all_months)}")
    if new["sharpe"] > mon["sharpe"] and new["total_pnl"] > mon["total_pnl"]:
        prt(f"\n  >>> NEW CONFIG WINS on both Sharpe AND P&L <<<")
    elif new["sharpe"] > mon["sharpe"]:
        prt(f"\n  >>> NEW CONFIG WINS on Sharpe (P&L: Monday better by ${-pnl_diff:,.0f}) <<<")
    else:
        prt(f"\n  >>> MONDAY CONFIG WINS on Sharpe <<<")
    prt("")
