"""
Sweep: put_stop_buffer — buffer added to put-side stop level.

Full IC: put_stop = total_credit + put_stop_buffer
Put-only: put_stop = put_credit + put_stop_buffer

Current live: $1.00 (= 100.0 in config units, $1.00 × 100).

Run: python -m backtest.sweep_put_stop_buffer
"""
import csv, statistics
from datetime import date, datetime as dt
from pathlib import Path
from typing import List
from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)

# Values in config units (dollars × 100). $0 to $10 range.
BUFFER_VALUES = [0.0, 25.0, 50.0, 75.0, 100.0, 150.0, 200.0, 300.0, 500.0, 750.0, 1000.0]

def build_cfg(v):
    cfg = live_config(); cfg.start_date = START_DATE; cfg.end_date = END_DATE
    cfg.use_real_greeks = True; cfg.put_stop_buffer = v; return cfg

def summarise(results, label):
    pnls = [r.net_pnl for r in results]; total = sum(pnls); n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    stops = sum(r.stops_hit for r in results)
    call_stops = sum(sum(1 for e in r.entries if e.call_outcome == "stopped") for r in results)
    put_stops = sum(sum(1 for e in r.entries if e.put_outcome == "stopped") for r in results)
    mean = statistics.mean(pnls) if pnls else 0
    std = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = mean / std * (252**0.5) if std > 0 else 0
    peak = cum = dd = 0.0
    for p in pnls: cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
    calmar = mean * 252 / dd if dd > 0 else 0
    return {"label": label, "total_pnl": total, "sharpe": sharpe, "max_dd": dd,
            "calmar": calmar, "win_rate": wins/n*100 if n else 0, "placed": placed,
            "total_stops": stops, "stop_rate": stops/placed*100 if placed else 0,
            "call_stops": call_stops, "put_stops": put_stops, "days": n}

METRICS = [("Win rate %","win_rate","{:.1f}%"),("Total P&L","total_pnl","${:,.0f}"),
    ("Mean daily","sharpe","{:.3f}"),("Max DD","max_dd","${:,.0f}"),("Calmar","calmar","{:.3f}"),
    ("Placed","placed","{:.0f}"),("Total stops","total_stops","{:.0f}"),
    ("Stop rate","stop_rate","{:.1f}%"),("Call stops","call_stops","{:.0f}"),
    ("Put stops","put_stops","{:.0f}")]

if __name__ == "__main__":
    all_stats = []
    print(f"Sweep: put_stop_buffer | Real Greeks | {START_DATE} → {END_DATE}")
    for v in BUFFER_VALUES:
        label = f"${v/100:.2f}"; print(f"  Running {label}...")
        all_stats.append(summarise(run_backtest(build_cfg(v), verbose=False), label))
    col_w = 10; header = f"  {'Metric':<22}"
    for s in all_stats: header += f"  {s['label']:>{col_w}}"
    print(); print(header); print("─" * (24 + (col_w+2)*len(all_stats)))
    for metric, key, fmt in METRICS:
        row = f"  {metric:<22}"
        for s in all_stats: row += f"  {fmt.format(s[key]):>{col_w}}"
        print(row)
    best = max(all_stats, key=lambda s: s["sharpe"])
    live = next((s for s in all_stats if s["label"] == "$1.00"), None)
    print(f"\n  Best Sharpe: {best['label']} ({best['sharpe']:.3f})")
    if live: print(f"  Live ($1.00): Sharpe {live['sharpe']:.3f}  P&L ${live['total_pnl']:,.0f}")
    out = Path("backtest/results"); out.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    p = out / f"put_stop_buffer_sweep_{ts}.csv"
    with open(p,"w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_stats[0].keys()), extrasaction="ignore")
        w.writeheader(); w.writerows(all_stats)
    print(f"  Results saved → {p}")
