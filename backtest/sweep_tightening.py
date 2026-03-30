"""
Sweep: Put tightening retries (MKT-040) and call tightening.

Tests all combinations of:
- Put tightening: 0, 1, 2, 3 retries (0 = disabled, go straight to call-only)
- Call tightening: 0, 1, 2, 3 retries (0 = current live, never retry calls)
- Step size: 5pt (fixed, same as live)

Also tests step sizes 5pt vs 10pt for the winning retry counts.

Run: python -m backtest.sweep_tightening
"""
import csv, statistics
from datetime import date, datetime as dt
from pathlib import Path
from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)

# Grid: put retries × call retries
PUT_RETRIES  = [0, 1, 2, 3]
CALL_RETRIES = [0, 1, 2, 3]
STEP = 5  # fixed step size


def build_cfg(put_retries, call_retries, step=5):
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.use_real_greeks = True
    cfg.put_tighten_retries = put_retries
    cfg.put_tighten_step = step
    cfg.call_tighten_retries = call_retries
    cfg.call_tighten_step = step
    return cfg


def summarise(results, label, put_r, call_r):
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
    return {"label": label, "put_retries": put_r, "call_retries": call_r,
            "total_pnl": total, "sharpe": sharpe, "max_dd": dd, "calmar": calmar,
            "win_rate": wins/n*100 if n else 0, "placed": placed, "skipped": skipped,
            "total_stops": stops, "stop_rate": stops/placed*100 if placed else 0,
            "full_ic": full_ic, "call_only": call_only, "put_only": put_only,
            "call_stops": call_stops, "put_stops": put_stops}


if __name__ == "__main__":
    CALL_RETRIES_ACTUAL = CALL_RETRIES

    total = len(PUT_RETRIES) * len(CALL_RETRIES_ACTUAL)
    print(f"Sweep: Tightening retries | {total} combos | Real Greeks | {START_DATE} → {END_DATE}")
    print(f"Put retries:  {PUT_RETRIES}")
    print(f"Call retries: {CALL_RETRIES_ACTUAL}")
    print(f"Step size:    {STEP}pt\n")

    all_stats = []
    i = 0
    for pr in PUT_RETRIES:
        for cr in CALL_RETRIES_ACTUAL:
            i += 1
            label = f"P{pr}r_C{cr}r"
            print(f"  [{i}/{total}] put_retries={pr} call_retries={cr}")
            cfg = build_cfg(pr, cr, STEP)
            results = run_backtest(cfg, verbose=False)
            all_stats.append(summarise(results, label, pr, cr))

    # Results table
    by_sharpe = sorted(all_stats, key=lambda s: s["sharpe"], reverse=True)
    print(f"\n{'='*100}")
    print(f"  RESULTS (sorted by Sharpe)")
    print(f"{'='*100}")
    print(f"  {'Put':>4} {'Call':>5} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>10} {'Calmar':>7} {'Win%':>6} {'Placed':>7} {'Skip':>5} {'IC':>5} {'CO':>5} {'PO':>5} {'CStop':>6} {'PStop':>6}")
    print(f"  {'─'*100}")
    for s in by_sharpe:
        live = " ◀LIVE" if s["put_retries"] == 2 and s["call_retries"] == 0 else ""
        print(f"  {s['put_retries']:>3}r {s['call_retries']:>4}r {s['sharpe']:>7.3f} ${s['total_pnl']:>9,.0f} ${s['max_dd']:>9,.0f} {s['calmar']:>7.3f} {s['win_rate']:>5.1f}% {s['placed']:>7} {s['skipped']:>5} {s['full_ic']:>5} {s['call_only']:>5} {s['put_only']:>5} {s['call_stops']:>6} {s['put_stops']:>6}{live}")

    # Also test step sizes for the best put retry count
    best_pr = by_sharpe[0]["put_retries"]
    print(f"\n{'='*100}")
    print(f"  STEP SIZE COMPARISON (put_retries={best_pr}, call_retries=0)")
    print(f"{'='*100}")

    step_stats = []
    for step in [3, 5, 10, 15]:
        label = f"step={step}pt"
        print(f"  Running {label}...")
        cfg = build_cfg(best_pr, 0, step)
        results = run_backtest(cfg, verbose=False)
        s = summarise(results, label, best_pr, 0)
        s["step"] = step
        step_stats.append(s)

    print(f"\n  {'Step':>6} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>10} {'Calmar':>7} {'Placed':>7} {'IC':>5} {'CO':>5}")
    print(f"  {'─'*60}")
    for s in sorted(step_stats, key=lambda s: s["sharpe"], reverse=True):
        live = " ◀LIVE" if s["step"] == 5 else ""
        print(f"  {s['step']:>4}pt {s['sharpe']:>7.3f} ${s['total_pnl']:>9,.0f} ${s['max_dd']:>9,.0f} {s['calmar']:>7.3f} {s['placed']:>7} {s['full_ic']:>5} {s['call_only']:>5}{live}")

    out = Path("backtest/results"); out.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    p = out / f"tightening_sweep_{ts}.csv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_stats[0].keys()), extrasaction="ignore")
        w.writeheader(); w.writerows(all_stats + step_stats)
    print(f"\n  Results saved → {p}")
