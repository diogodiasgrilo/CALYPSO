"""
Sweep: call_starting_otm_multiplier and put_starting_otm_multiplier (MKT-024).

These control how far OTM the strike scan starts before tightening inward.
Higher = starts further OTM (wider, safer but may not find credit).
Lower = starts closer to ATM (more credit but less cushion).

Current live: call=3.5×, put=4.0× (put higher due to skew).

Run: python -m backtest.sweep_otm_multipliers
"""
import csv, statistics
from datetime import date, datetime as dt
from pathlib import Path
from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)

# Sweep both dimensions
CALL_MULTS = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
PUT_MULTS  = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5]

def build_cfg(call_mult, put_mult):
    cfg = live_config(); cfg.start_date = START_DATE; cfg.end_date = END_DATE
    cfg.use_real_greeks = True
    cfg.call_starting_otm_multiplier = call_mult
    cfg.put_starting_otm_multiplier = put_mult
    return cfg

def summarise(results, label, call_m, put_m):
    pnls = [r.net_pnl for r in results]; total = sum(pnls); n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    skipped = sum(r.entries_skipped for r in results)
    stops = sum(r.stops_hit for r in results)
    call_stops = sum(sum(1 for e in r.entries if e.call_outcome == "stopped") for r in results)
    put_stops = sum(sum(1 for e in r.entries if e.put_outcome == "stopped") for r in results)
    mean = statistics.mean(pnls) if pnls else 0
    std = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = mean / std * (252**0.5) if std > 0 else 0
    peak = cum = dd = 0.0
    for p in pnls: cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
    calmar = mean * 252 / dd if dd > 0 else 0
    return {"label": label, "call_mult": call_m, "put_mult": put_m,
            "total_pnl": total, "sharpe": sharpe, "max_dd": dd, "calmar": calmar,
            "win_rate": wins/n*100 if n else 0, "placed": placed, "skipped": skipped,
            "total_stops": stops, "stop_rate": stops/placed*100 if placed else 0,
            "call_stops": call_stops, "put_stops": put_stops}

if __name__ == "__main__":
    total_combos = len(CALL_MULTS) * len(PUT_MULTS)
    print(f"Sweep: OTM multipliers | {total_combos} combos | Real Greeks | {START_DATE} → {END_DATE}")
    print(f"Call mults: {CALL_MULTS}")
    print(f"Put mults:  {PUT_MULTS}")

    all_stats = []
    i = 0
    for cm in CALL_MULTS:
        for pm in PUT_MULTS:
            i += 1
            label = f"C{cm}×P{pm}×"
            print(f"  [{i}/{total_combos}] call={cm}× put={pm}×")
            results = run_backtest(build_cfg(cm, pm), verbose=False)
            all_stats.append(summarise(results, label, cm, pm))

    # Top 15 by Sharpe
    by_sharpe = sorted(all_stats, key=lambda s: s["sharpe"], reverse=True)
    print(f"\n{'='*90}")
    print(f"  TOP 15 BY SHARPE")
    print(f"{'='*90}")
    print(f"  {'#':<3} {'Call':>5} {'Put':>5} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>10} {'Calmar':>7} {'Win%':>6} {'Placed':>7} {'Stops':>6}")
    print(f"  {'─'*72}")
    for j, s in enumerate(by_sharpe[:15], 1):
        live = " ◀LIVE" if s["call_mult"] == 3.5 and s["put_mult"] == 4.0 else ""
        print(f"  {j:<3} {s['call_mult']:>4.1f}× {s['put_mult']:>4.1f}× {s['sharpe']:>7.3f} ${s['total_pnl']:>9,.0f} ${s['max_dd']:>9,.0f} {s['calmar']:>7.3f} {s['win_rate']:>5.1f}% {s['placed']:>7} {s['total_stops']:>6}{live}")

    # Marginal analysis: best call mult (averaged across put mults)
    from collections import defaultdict
    by_call = defaultdict(list)
    for s in all_stats: by_call[s["call_mult"]].append(s)
    print(f"\n  By CALL multiplier (avg across all put mults):")
    for cm in sorted(by_call.keys()):
        stats = by_call[cm]
        avg_sh = statistics.mean([s["sharpe"] for s in stats])
        avg_pnl = statistics.mean([s["total_pnl"] for s in stats])
        print(f"    {cm:.1f}×: avg Sharpe={avg_sh:.3f}  avg P&L=${avg_pnl:,.0f}")

    by_put = defaultdict(list)
    for s in all_stats: by_put[s["put_mult"]].append(s)
    print(f"\n  By PUT multiplier (avg across all call mults):")
    for pm in sorted(by_put.keys()):
        stats = by_put[pm]
        avg_sh = statistics.mean([s["sharpe"] for s in stats])
        avg_pnl = statistics.mean([s["total_pnl"] for s in stats])
        print(f"    {pm:.1f}×: avg Sharpe={avg_sh:.3f}  avg P&L=${avg_pnl:,.0f}")

    # Live rank
    live = next((s for s in all_stats if s["call_mult"] == 3.5 and s["put_mult"] == 4.0), None)
    if live:
        rank = next(j for j, s in enumerate(by_sharpe, 1) if s["label"] == live["label"])
        print(f"\n  LIVE (C3.5×/P4.0×): Sharpe={live['sharpe']:.3f}  P&L=${live['total_pnl']:,.0f}  Rank #{rank}/{total_combos}")
    best = by_sharpe[0]
    print(f"  BEST ({best['label']}): Sharpe={best['sharpe']:.3f}  P&L=${best['total_pnl']:,.0f}")

    out = Path("backtest/results"); out.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    p = out / f"otm_multipliers_sweep_{ts}.csv"
    with open(p,"w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_stats[0].keys()), extrasaction="ignore")
        w.writeheader(); w.writerows(all_stats)
    print(f"\n  Results saved → {p}")
