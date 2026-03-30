"""
Sweep: one_sided_entries_enabled — allow call-only/put-only entries or skip them.

When True: if one side's credit is non-viable, place the other side alone.
When False: skip the entire entry if either side is non-viable.

Current live: True.

Run: python -m backtest.sweep_one_sided
"""
import csv, statistics
from datetime import date, datetime as dt
from pathlib import Path
from typing import List
from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)

def build_cfg(enabled):
    cfg = live_config(); cfg.start_date = START_DATE; cfg.end_date = END_DATE
    cfg.use_real_greeks = True; cfg.one_sided_entries_enabled = enabled; return cfg

def summarise(results, label):
    pnls = [r.net_pnl for r in results]; total = sum(pnls); n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    skipped = sum(r.entries_skipped for r in results)
    stops = sum(r.stops_hit for r in results)
    full_ic = sum(sum(1 for e in r.entries if e.entry_type == "full_ic") for r in results)
    call_only = sum(sum(1 for e in r.entries if e.entry_type == "call_only") for r in results)
    put_only = sum(sum(1 for e in r.entries if e.entry_type == "put_only") for r in results)
    mean = statistics.mean(pnls) if pnls else 0
    std = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = mean / std * (252**0.5) if std > 0 else 0
    peak = cum = dd = 0.0
    for p in pnls: cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
    calmar = mean * 252 / dd if dd > 0 else 0
    return {"label": label, "total_pnl": total, "sharpe": sharpe, "max_dd": dd,
            "calmar": calmar, "win_rate": wins/n*100 if n else 0,
            "placed": placed, "skipped": skipped, "total_stops": stops,
            "stop_rate": stops/placed*100 if placed else 0,
            "full_ic": full_ic, "call_only": call_only, "put_only": put_only}

if __name__ == "__main__":
    for label, enabled in [("ON (live)", True), ("OFF", False)]:
        cfg = build_cfg(enabled)
        results = run_backtest(cfg, verbose=False)
        s = summarise(results, label)
        print(f"\n{label}:")
        print(f"  Sharpe={s['sharpe']:.3f}  P&L=${s['total_pnl']:,.0f}  MaxDD=${s['max_dd']:,.0f}  Calmar={s['calmar']:.3f}")
        print(f"  Win={s['win_rate']:.1f}%  Placed={s['placed']}  Skipped={s['skipped']}  Stops={s['total_stops']} ({s['stop_rate']:.1f}%)")
        print(f"  Full IC={s['full_ic']}  Call-only={s['call_only']}  Put-only={s['put_only']}")
