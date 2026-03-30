"""
Full comprehensive backtest report with optimised live_config() parameters.

Outputs:
  - Overall metrics (P&L, Sharpe, MaxDD, Calmar, win rate, etc.)
  - Best/worst winning/losing streaks
  - Monthly breakdown (P&L, win rate, entries, stops)
  - Best/worst months
  - Seasonal analysis (average P&L by month-of-year)
  - 2026 monthly drill-down (Jan, Feb, Mar)
  - Day-of-week analysis

Run: python -m backtest.full_report
"""
import csv
import statistics
from collections import defaultdict
from datetime import date, datetime as dt
from pathlib import Path
from typing import List

from backtest.config import live_config
from backtest.engine import run_backtest, DayResult

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False


def run_report():
    cfg = live_config()
    cfg.start_date = date(2022, 5, 16)
    cfg.end_date = date(2026, 3, 27)
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"

    console = Console() if _RICH else None

    if _RICH:
        console.print("\n[bold cyan]HYDRA Full Backtest Report — Optimised Parameters[/]")
        console.print(f"Period: {cfg.start_date} → {cfg.end_date}  |  Real Greeks strict mode")
        console.print(f"E7={cfg.conditional_e7_enabled}  E6up={cfg.conditional_upday_e6_enabled}  "
                      f"downday_pct={cfg.base_entry_downday_callonly_pct}%  "
                      f"theo_put=${cfg.downday_theoretical_put_credit/100:.2f}  "
                      f"mult={cfg.spread_vix_multiplier}  "
                      f"call_gate=${cfg.min_call_credit}  put_gate=${cfg.min_put_credit}")
        console.print("[yellow]Running backtest...[/]\n")
    else:
        print(f"HYDRA Full Backtest Report | {cfg.start_date} → {cfg.end_date}")
        print("Running backtest...")

    results = run_backtest(cfg, verbose=False)
    prt = lambda msg: console.print(msg) if _RICH else print(msg)

    # ── Overall metrics ──────────────────────────────────────────────────────
    daily_pnls = [r.net_pnl for r in results]
    total_pnl = sum(daily_pnls)
    total_days = len(results)
    win_days = sum(1 for p in daily_pnls if p > 0)
    loss_days = sum(1 for p in daily_pnls if p < 0)
    flat_days = sum(1 for p in daily_pnls if p == 0)

    mean = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    # Sortino
    neg_pnls = [p for p in daily_pnls if p < 0]
    downside_dev = (sum(p**2 for p in neg_pnls) / len(daily_pnls)) ** 0.5 if neg_pnls else 0
    sortino = mean / downside_dev * (252 ** 0.5) if downside_dev > 0 else 0

    # Max drawdown + drawdown duration
    peak = cum = max_dd = 0.0
    dd_start = dd_end = dd_peak_idx = 0
    cum_series = []
    for i, p in enumerate(daily_pnls):
        cum += p
        cum_series.append(cum)
        if cum > peak:
            peak = cum
            dd_peak_idx = i
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
            dd_end = i

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    placed = sum(r.entries_placed for r in results)
    skipped = sum(r.entries_skipped for r in results)
    total_stops = sum(r.stops_hit for r in results)
    full_ic = sum(sum(1 for e in r.entries if e.entry_type == "full_ic") for r in results)
    call_only = sum(sum(1 for e in r.entries if e.entry_type == "call_only") for r in results)
    put_only = sum(sum(1 for e in r.entries if e.entry_type == "put_only") for r in results)

    best_day = max(daily_pnls)
    worst_day = min(daily_pnls)
    best_day_date = results[daily_pnls.index(best_day)].date
    worst_day_date = results[daily_pnls.index(worst_day)].date

    prt(f"\n{'='*70}")
    prt(f"  OVERALL METRICS  ({total_days} trading days, real Greeks)")
    prt(f"{'='*70}")
    prt(f"  Total net P&L:         ${total_pnl:>12,.2f}")
    prt(f"  Mean daily P&L:        ${mean:>12,.2f}")
    prt(f"  Median daily P&L:      ${statistics.median(daily_pnls):>12,.2f}")
    prt(f"  Std dev daily:         ${stdev:>12,.2f}")
    prt(f"  Sharpe (annualised):   {sharpe:>12.3f}")
    prt(f"  Sortino (annualised):  {sortino:>12.3f}")
    prt(f"  Max drawdown:          ${max_dd:>12,.2f}")
    prt(f"  Calmar ratio:          {calmar:>12.3f}")
    prt(f"  Win rate:              {win_days/total_days*100:>11.1f}%")
    prt(f"  Win/Loss/Flat:         {win_days} / {loss_days} / {flat_days}")
    prt(f"  Best day:              ${best_day:>12,.2f}  ({best_day_date})")
    prt(f"  Worst day:             ${worst_day:>12,.2f}  ({worst_day_date})")
    prt(f"  Entries placed:        {placed:>12,}")
    prt(f"  Entries skipped:       {skipped:>12,}")
    prt(f"  Total stops:           {total_stops:>12,}  ({total_stops/placed*100:.1f}% stop rate)")
    prt(f"  Full IC / Call / Put:  {full_ic} / {call_only} / {put_only}")

    # ── Streaks ──────────────────────────────────────────────────────────────
    def calc_streaks(pnls, results_list):
        win_streak = loss_streak = 0
        max_win_streak = max_loss_streak = 0
        best_ws_end = worst_ls_end = 0
        for i, p in enumerate(pnls):
            if p > 0:
                win_streak += 1
                loss_streak = 0
                if win_streak > max_win_streak:
                    max_win_streak = win_streak
                    best_ws_end = i
            elif p < 0:
                loss_streak += 1
                win_streak = 0
                if loss_streak > max_loss_streak:
                    max_loss_streak = loss_streak
                    worst_ls_end = i
            else:
                win_streak = 0
                loss_streak = 0
        ws_start = best_ws_end - max_win_streak + 1
        ls_start = worst_ls_end - max_loss_streak + 1
        ws_pnl = sum(pnls[ws_start:best_ws_end+1])
        ls_pnl = sum(pnls[ls_start:worst_ls_end+1])
        return (max_win_streak, results_list[ws_start].date, results_list[best_ws_end].date, ws_pnl,
                max_loss_streak, results_list[ls_start].date, results_list[worst_ls_end].date, ls_pnl)

    mws, ws_s, ws_e, ws_pnl, mls, ls_s, ls_e, ls_pnl = calc_streaks(daily_pnls, results)

    prt(f"\n  STREAKS")
    prt(f"  Best winning streak:   {mws} days  ({ws_s} → {ws_e})  P&L: ${ws_pnl:,.2f}")
    prt(f"  Worst losing streak:   {mls} days  ({ls_s} → {ls_e})  P&L: ${ls_pnl:,.2f}")

    # ── Monthly breakdown ────────────────────────────────────────────────────
    monthly = defaultdict(lambda: {"pnl": 0, "days": 0, "wins": 0, "losses": 0,
                                    "placed": 0, "stops": 0, "pnls": []})
    for r in results:
        key = f"{r.date.year}-{r.date.month:02d}"
        m = monthly[key]
        m["pnl"] += r.net_pnl
        m["days"] += 1
        m["pnls"].append(r.net_pnl)
        if r.net_pnl > 0: m["wins"] += 1
        elif r.net_pnl < 0: m["losses"] += 1
        m["placed"] += r.entries_placed
        m["stops"] += r.stops_hit

    sorted_months = sorted(monthly.keys())
    prt(f"\n{'='*70}")
    prt(f"  MONTHLY BREAKDOWN")
    prt(f"{'='*70}")
    prt(f"  {'Month':<10} {'P&L':>10} {'Days':>5} {'Win%':>6} {'Placed':>7} {'Stops':>6} {'StopR':>6}")
    prt(f"  {'─'*56}")
    for key in sorted_months:
        m = monthly[key]
        wr = m["wins"] / m["days"] * 100 if m["days"] else 0
        sr = m["stops"] / m["placed"] * 100 if m["placed"] else 0
        marker = " ★" if m["pnl"] > 2000 else " ▼" if m["pnl"] < -2000 else ""
        prt(f"  {key:<10} ${m['pnl']:>9,.0f} {m['days']:>5} {wr:>5.0f}% {m['placed']:>7} {m['stops']:>6} {sr:>5.1f}%{marker}")

    # Best and worst months
    best_month = max(sorted_months, key=lambda k: monthly[k]["pnl"])
    worst_month = min(sorted_months, key=lambda k: monthly[k]["pnl"])
    prt(f"\n  Best month:   {best_month}  ${monthly[best_month]['pnl']:,.0f}")
    prt(f"  Worst month:  {worst_month}  ${monthly[worst_month]['pnl']:,.0f}")

    # Winning months count
    winning_months = sum(1 for k in sorted_months if monthly[k]["pnl"] > 0)
    prt(f"  Winning months: {winning_months}/{len(sorted_months)} ({winning_months/len(sorted_months)*100:.0f}%)")

    # ── Seasonal analysis (avg P&L by calendar month) ────────────────────────
    seasonal = defaultdict(list)
    for key in sorted_months:
        month_num = int(key.split("-")[1])
        seasonal[month_num].append(monthly[key]["pnl"])

    prt(f"\n{'='*70}")
    prt(f"  SEASONAL ANALYSIS (avg monthly P&L by calendar month)")
    prt(f"{'='*70}")
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for m in range(1, 13):
        if m in seasonal:
            vals = seasonal[m]
            avg = statistics.mean(vals)
            n = len(vals)
            bar = "█" * max(1, int(abs(avg) / 100)) if avg > 0 else ""
            neg_bar = "░" * max(1, int(abs(avg) / 100)) if avg < 0 else ""
            prt(f"  {month_names[m]:<4} (n={n}): ${avg:>8,.0f}  {bar}{neg_bar}")

    # ── Day-of-week analysis ─────────────────────────────────────────────────
    dow_data = defaultdict(lambda: {"pnl": 0, "days": 0, "wins": 0})
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    for r in results:
        wd = r.date.weekday()
        dow_data[wd]["pnl"] += r.net_pnl
        dow_data[wd]["days"] += 1
        if r.net_pnl > 0: dow_data[wd]["wins"] += 1

    prt(f"\n{'='*70}")
    prt(f"  DAY-OF-WEEK ANALYSIS")
    prt(f"{'='*70}")
    for wd in range(5):
        d = dow_data[wd]
        if d["days"] > 0:
            avg = d["pnl"] / d["days"]
            wr = d["wins"] / d["days"] * 100
            prt(f"  {dow_names[wd]}:  total ${d['pnl']:>9,.0f}  avg ${avg:>7,.2f}  win {wr:.0f}%  ({d['days']} days)")

    # ── 2026 monthly drill-down ──────────────────────────────────────────────
    prt(f"\n{'='*70}")
    prt(f"  2026 DRILL-DOWN (Jan / Feb / Mar)")
    prt(f"{'='*70}")

    for target_month in [1, 2, 3]:
        month_results = [r for r in results if r.date.year == 2026 and r.date.month == target_month]
        if not month_results:
            prt(f"\n  {month_names[target_month]} 2026: No data")
            continue

        m_pnls = [r.net_pnl for r in month_results]
        m_total = sum(m_pnls)
        m_days = len(month_results)
        m_wins = sum(1 for p in m_pnls if p > 0)
        m_losses = sum(1 for p in m_pnls if p < 0)
        m_mean = statistics.mean(m_pnls)
        m_stdev = statistics.stdev(m_pnls) if len(m_pnls) > 1 else 0
        m_sharpe = m_mean / m_stdev * (252 ** 0.5) if m_stdev > 0 else 0
        m_placed = sum(r.entries_placed for r in month_results)
        m_stops = sum(r.stops_hit for r in month_results)
        m_best = max(m_pnls)
        m_worst = min(m_pnls)
        m_best_date = month_results[m_pnls.index(m_best)].date
        m_worst_date = month_results[m_pnls.index(m_worst)].date

        # Month max drawdown
        m_peak = m_cum = m_maxdd = 0.0
        for p in m_pnls:
            m_cum += p
            m_peak = max(m_peak, m_cum)
            m_maxdd = max(m_maxdd, m_peak - m_cum)

        # Full IC / call-only / put-only
        m_fic = sum(sum(1 for e in r.entries if e.entry_type == "full_ic") for r in month_results)
        m_co = sum(sum(1 for e in r.entries if e.entry_type == "call_only") for r in month_results)
        m_po = sum(sum(1 for e in r.entries if e.entry_type == "put_only") for r in month_results)

        prt(f"\n  ── {month_names[target_month]} 2026 ──")
        prt(f"  Net P&L:     ${m_total:>9,.2f}  ({m_days} days)")
        prt(f"  Win rate:    {m_wins}/{m_days} = {m_wins/m_days*100:.0f}%  (W:{m_wins} L:{m_losses})")
        prt(f"  Mean daily:  ${m_mean:>9,.2f}  (std: ${m_stdev:,.2f})")
        prt(f"  Sharpe:      {m_sharpe:.3f}")
        prt(f"  Max DD:      ${m_maxdd:>9,.2f}")
        prt(f"  Best day:    ${m_best:>9,.2f}  ({m_best_date})")
        prt(f"  Worst day:   ${m_worst:>9,.2f}  ({m_worst_date})")
        prt(f"  Entries:     {m_placed} placed, {m_stops} stops ({m_stops/m_placed*100:.1f}%)")
        prt(f"  Types:       IC={m_fic}  call-only={m_co}  put-only={m_po}")

        # Daily detail for 2026
        prt(f"  {'Date':<12} {'P&L':>9} {'Placed':>7} {'Stops':>6} {'IC':>3} {'CO':>3} {'PO':>3}")
        prt(f"  {'─'*48}")
        for r in month_results:
            fic = sum(1 for e in r.entries if e.entry_type == "full_ic")
            co = sum(1 for e in r.entries if e.entry_type == "call_only")
            po = sum(1 for e in r.entries if e.entry_type == "put_only")
            marker = " ★" if r.net_pnl > 300 else " ▼" if r.net_pnl < -300 else ""
            prt(f"  {r.date!s:<12} ${r.net_pnl:>8,.2f} {r.entries_placed:>7} {r.stops_hit:>6} {fic:>3} {co:>3} {po:>3}{marker}")

    # ── Equity curve summary ─────────────────────────────────────────────────
    final_equity = cum_series[-1] if cum_series else 0
    peak_equity = max(cum_series) if cum_series else 0
    prt(f"\n{'='*70}")
    prt(f"  EQUITY CURVE")
    prt(f"{'='*70}")
    prt(f"  Final cumulative P&L:  ${final_equity:>12,.2f}")
    prt(f"  Peak cumulative P&L:   ${peak_equity:>12,.2f}")
    prt(f"  Current drawdown:      ${peak_equity - final_equity:>12,.2f}")
    prt(f"  Max drawdown:          ${max_dd:>12,.2f}")

    # ── CSV export ───────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"full_report_{ts}.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "net_pnl", "entries_placed", "entries_skipped",
                         "stops_hit", "full_ic", "call_only", "put_only", "cumulative_pnl"])
        cum = 0
        for r in results:
            cum += r.net_pnl
            fic = sum(1 for e in r.entries if e.entry_type == "full_ic")
            co = sum(1 for e in r.entries if e.entry_type == "call_only")
            po = sum(1 for e in r.entries if e.entry_type == "put_only")
            writer.writerow([r.date, f"{r.net_pnl:.2f}", r.entries_placed,
                            r.entries_skipped, r.stops_hit, fic, co, po, f"{cum:.2f}"])
    prt(f"\n  Daily CSV saved → {csv_path}")
    prt("")


if __name__ == "__main__":
    run_report()
