"""
Sweep: MKT-029 graduated credit fallback floors.

When the primary credit gate fails (call < $1.25 or put < $2.25), MKT-029
re-scans at a lower "floor" threshold. This sweep tests whether the fallback
helps and what the optimal floor values are.

Floor = 0 means MKT-029 is disabled (no fallback, straight to one-sided/skip).
Floor = primary means fallback is same as primary (effectively no fallback).

Part 1: Sweep call_credit_floor (hold put_credit_floor at live $2.15)
Part 2: Sweep put_credit_floor (hold call_credit_floor at live $0.50)
Part 3: Test MKT-029 fully OFF vs fully ON vs live

Run: python -m backtest.sweep_credit_floors
"""
import csv, statistics
from datetime import date, datetime as dt
from pathlib import Path
from backtest.config import live_config
from backtest.engine import run_backtest

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)

CALL_FLOORS = [0.0, 0.10, 0.25, 0.50, 0.75, 1.00, 1.25]
PUT_FLOORS  = [0.0, 1.00, 1.50, 1.75, 2.00, 2.15, 2.25]


def build_cfg(call_floor, put_floor):
    cfg = live_config()
    cfg.start_date = START_DATE; cfg.end_date = END_DATE; cfg.use_real_greeks = True
    cfg.call_credit_floor = call_floor
    cfg.put_credit_floor = put_floor
    return cfg


def summarise(results, label):
    pnls = [r.net_pnl for r in results]; total = sum(pnls); n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    skipped = sum(r.entries_skipped for r in results)
    stops = sum(r.stops_hit for r in results)
    full_ic = sum(sum(1 for e in r.entries if e.entry_type == "full_ic") for r in results)
    call_only = sum(sum(1 for e in r.entries if e.entry_type == "call_only") for r in results)
    put_only = sum(sum(1 for e in r.entries if e.entry_type == "put_only") for r in results)
    call_stops = sum(sum(1 for e in r.entries if e.call_outcome == "stopped") for r in results)
    put_stops = sum(sum(1 for e in r.entries if e.put_outcome == "stopped") for r in results)
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
            "full_ic": full_ic, "call_only": call_only, "put_only": put_only,
            "call_stops": call_stops, "put_stops": put_stops}


def print_table(title, all_stats):
    print(f"\n{'='*100}")
    print(f"  {title}")
    print(f"{'='*100}")
    print(f"  {'Label':<22} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>10} {'Calmar':>7} {'Win%':>6} {'Placed':>7} {'Skip':>5} {'IC':>5} {'CO':>5} {'PO':>4} {'CStop':>6} {'PStop':>6}")
    print(f"  {'─'*100}")
    for s in sorted(all_stats, key=lambda s: s["sharpe"], reverse=True):
        print(f"  {s['label']:<22} {s['sharpe']:>7.3f} ${s['total_pnl']:>9,.0f} ${s['max_dd']:>9,.0f} {s['calmar']:>7.3f} {s['win_rate']:>5.1f}% {s['placed']:>7} {s['skipped']:>5} {s['full_ic']:>5} {s['call_only']:>5} {s['put_only']:>4} {s['call_stops']:>6} {s['put_stops']:>6}")


if __name__ == "__main__":
    print(f"Sweep: MKT-029 Credit Floors | Real Greeks | {START_DATE} → {END_DATE}")

    # Part 1: Call floor sweep
    print(f"\n--- Part 1: call_credit_floor (put_credit_floor=$2.15 fixed) ---")
    call_stats = []
    for cf in CALL_FLOORS:
        label = f"call_floor=${cf:.2f}" + (" ◀LIVE" if cf == 0.50 else "") + (" [OFF]" if cf == 0 else "")
        print(f"  Running {label}...")
        call_stats.append(summarise(run_backtest(build_cfg(cf, 2.15), verbose=False), label))
    print_table("Part 1: Call Credit Floor (put floor=$2.15 fixed)", call_stats)

    # Part 2: Put floor sweep
    print(f"\n--- Part 2: put_credit_floor (call_credit_floor=$0.50 fixed) ---")
    put_stats = []
    for pf in PUT_FLOORS:
        label = f"put_floor=${pf:.2f}" + (" ◀LIVE" if pf == 2.15 else "") + (" [OFF]" if pf == 0 else "")
        print(f"  Running {label}...")
        put_stats.append(summarise(run_backtest(build_cfg(0.50, pf), verbose=False), label))
    print_table("Part 2: Put Credit Floor (call floor=$0.50 fixed)", put_stats)

    # Part 3: MKT-029 fully OFF vs ON
    print(f"\n--- Part 3: MKT-029 fully OFF vs ON ---")
    combo_stats = []
    combos = [
        ("BOTH OFF (no fallback)", 0.0, 0.0),
        ("LIVE (C=$0.50, P=$2.15)", 0.50, 2.15),
        ("TIGHT (C=$0.25, P=$2.00)", 0.25, 2.00),
        ("SAME AS PRIMARY (no gap)", 1.25, 2.25),
    ]
    for label, cf, pf in combos:
        print(f"  Running {label}...")
        combo_stats.append(summarise(run_backtest(build_cfg(cf, pf), verbose=False), label))
    print_table("Part 3: MKT-029 ON vs OFF", combo_stats)

    # Save
    out = Path("backtest/results"); out.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    p = out / f"credit_floors_sweep_{ts}.csv"
    all_data = call_stats + put_stats + combo_stats
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_data[0].keys()), extrasaction="ignore")
        w.writeheader(); w.writerows(all_data)
    print(f"\n  Results saved → {p}")
