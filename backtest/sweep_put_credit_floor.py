"""
Sweep: put_credit_floor — MKT-029 hard floor for put credit after graduated fallback.

Current live: $2.15 (= min_put_credit $2.25 - $0.10).
Lower = accepts weaker put credits on fallback, more entries placed.
Higher = stricter floor, fewer entries but higher quality.

Run: python -m backtest.sweep_put_credit_floor
"""
import csv
import statistics
from datetime import date, datetime as dt
from pathlib import Path
from typing import List

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)

# Values to test: from very loose to very strict
FLOOR_VALUES = [1.50, 1.75, 2.00, 2.10, 2.15, 2.20, 2.25, 2.40]


def build_cfg(floor_val: float) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date      = START_DATE
    cfg.end_date        = END_DATE
    cfg.use_real_greeks = True
    cfg.put_credit_floor = floor_val
    return cfg


def summarise(results: List[DayResult], label: str) -> dict:
    daily_pnls = [r.net_pnl for r in results]
    total_pnl  = sum(daily_pnls)
    total_days = len(results)
    win_days   = sum(1 for p in daily_pnls if p > 0)
    placed     = sum(r.entries_placed for r in results)
    skipped    = sum(r.entries_skipped for r in results)
    total_stops = sum(r.stops_hit for r in results)
    full_ic    = sum(sum(1 for e in r.entries if e.entry_type == "full_ic") for r in results)
    call_only  = sum(sum(1 for e in r.entries if e.entry_type == "call_only") for r in results)
    put_only   = sum(sum(1 for e in r.entries if e.entry_type == "put_only") for r in results)

    mean  = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum += p; peak = max(peak, cum); max_dd = max(max_dd, peak - cum)
    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    return {
        "label": label, "days": total_days, "win_rate": win_days / total_days * 100 if total_days else 0,
        "total_pnl": total_pnl, "mean_daily": mean, "sharpe": sharpe,
        "max_dd": max_dd, "calmar": calmar,
        "placed": placed, "skipped": skipped, "total_stops": total_stops,
        "stop_rate": total_stops / placed * 100 if placed else 0,
        "full_ic": full_ic, "call_only": call_only, "put_only": put_only,
    }


METRICS = [
    ("Win rate %",     "win_rate",    "{:.1f}%"),
    ("Total net P&L",  "total_pnl",   "${:,.0f}"),
    ("Mean daily P&L", "mean_daily",  "${:.2f}"),
    ("Sharpe",         "sharpe",      "{:.3f}"),
    ("Max drawdown",   "max_dd",      "${:,.0f}"),
    ("Calmar",         "calmar",      "{:.3f}"),
    ("Placed",         "placed",      "{:.0f}"),
    ("Skipped",        "skipped",     "{:.0f}"),
    ("Total stops",    "total_stops", "{:.0f}"),
    ("Stop rate %",    "stop_rate",   "{:.1f}%"),
    ("Full IC",        "full_ic",     "{:.0f}"),
    ("Call-only",      "call_only",   "{:.0f}"),
    ("Put-only",       "put_only",    "{:.0f}"),
]


if __name__ == "__main__":
    all_stats = []
    print(f"Sweep: put_credit_floor  |  Values: {FLOOR_VALUES}  |  Real Greeks | {START_DATE} → {END_DATE}")
    for v in FLOOR_VALUES:
        label = f"${v:.2f}"
        print(f"  Running put_credit_floor = {label}...")
        results = run_backtest(build_cfg(v), verbose=False)
        all_stats.append(summarise(results, label))

    col_w = 10
    header = f"  {'Metric':<22}"
    for s in all_stats: header += f"  {s['label']:>{col_w}}"
    print(); print(header)
    print("─" * (24 + (col_w + 2) * len(all_stats)))
    for metric, key, fmt in METRICS:
        row = f"  {metric:<22}"
        for s in all_stats: row += f"  {fmt.format(s[key]):>{col_w}}"
        print(row)

    best_sh = max(range(len(all_stats)), key=lambda i: all_stats[i]["sharpe"])
    best_pnl = max(range(len(all_stats)), key=lambda i: all_stats[i]["total_pnl"])
    live_idx = next((i for i, s in enumerate(all_stats) if s["label"] == "$2.15"), None)
    print(f"\n  Best Sharpe:    {all_stats[best_sh]['label']}  ({all_stats[best_sh]['sharpe']:.3f})")
    print(f"  Best P&L:       {all_stats[best_pnl]['label']}  (${all_stats[best_pnl]['total_pnl']:,.0f})")
    if live_idx is not None:
        print(f"  Live ($2.15):   Sharpe {all_stats[live_idx]['sharpe']:.3f}  P&L ${all_stats[live_idx]['total_pnl']:,.0f}")

    out_dir = Path("backtest/results"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"put_credit_floor_sweep_{ts}.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[k for _, k, _ in METRICS] + ["label"], extrasaction="ignore")
        writer.writeheader(); writer.writerows(all_stats)
    print(f"  Results saved → {csv_path}")
