"""
Final re-optimization with calibrated slippage ($0.30/leg, 10% markup).

Phase 1: Put buffer
Phase 2: Call buffer
Phase 3: Buffer decay
Phase 4: Combined best (if different from live)

Run: python -m backtest.sweep_final_reopt
"""
from backtest.config import live_config
from backtest.engine import run_backtest
from datetime import date
from concurrent.futures import ProcessPoolExecutor, as_completed
import statistics
from pathlib import Path
import csv
from datetime import datetime as dt

FULL_START = date(2022, 5, 16)
FULL_END   = date(2026, 4, 8)

# Calibrated slippage (consensus across 3/6/16 day windows)
SLIPPAGE = 30.0    # $0.30/leg
MARKUP   = 0.10    # 10%


def build_cfg(**overrides):
    cfg = live_config()
    cfg.start_date = FULL_START
    cfg.end_date = FULL_END
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.stop_slippage_per_leg = SLIPPAGE
    cfg.stop_spread_markup_pct = MARKUP
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def summarise(results, label):
    pnls = [r.net_pnl for r in results]
    total = sum(pnls)
    days = len(results)
    wins = sum(1 for p in pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    stops = sum(r.stops_hit for r in results)
    mean = statistics.mean(pnls) if pnls else 0
    stdev = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0
    peak = cum = dd = 0.0
    for p in pnls:
        cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
    calmar = mean * 252 / dd if dd > 0 else 0
    return {
        "label": label, "sharpe": sharpe, "total_pnl": total,
        "max_dd": dd, "win_rate": wins / days * 100 if days else 0,
        "stop_rate": stops / placed * 100 if placed else 0,
        "stops": stops, "placed": placed, "calmar": calmar,
    }


def _run(args):
    label, cfg = args
    return summarise(run_backtest(cfg, verbose=False), label)


def run_phase(title, configs, n_workers):
    print(f"\n{title}")
    print(f"  {len(configs)} configs, {n_workers} workers, slippage=$0.30/leg, markup=10%")
    all_stats = []
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_run, c): c[0] for c in configs}
        done = 0
        for f in as_completed(futures):
            s = f.result()
            all_stats.append(s)
            done += 1
            if done == len(configs):
                print(f"  [{done}/{len(configs)}] done")
    all_stats.sort(key=lambda s: s["sharpe"], reverse=True)

    print(f"\n  {'Label':<26} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>8} {'WR%':>6} {'StopR':>6} {'Stops':>6}")
    print(f"  {'-'*72}")
    for s in all_stats:
        live = " *" if "LIVE" in s["label"] else ""
        print(f"  {s['label']:<26} {s['sharpe']:>7.3f} {s['total_pnl']:>10,.0f} {s['max_dd']:>8,.0f} {s['win_rate']:>5.1f}% {s['stop_rate']:>5.1f}% {s['stops']:>6}{live}")

    best = all_stats[0]
    current = next((s for s in all_stats if "LIVE" in s["label"]), None)
    if current and best["label"] != current["label"]:
        print(f"\n  CHANGE RECOMMENDED: {best['label']} (Sharpe {best['sharpe']:.3f} vs LIVE {current['sharpe']:.3f})")
    elif current:
        print(f"\n  CURRENT CONFIG IS OPTIMAL (Sharpe {current['sharpe']:.3f})")

    return all_stats


if __name__ == "__main__":
    n = 8

    print(f"Final Re-optimization: slippage=$0.30/leg, markup=10%")
    print(f"Period: {FULL_START} -> {FULL_END} | 1-min data | Real Greeks")

    all_results = []

    # Phase 1: Put buffer
    configs = [(f"put=${pb/100:.2f}" + (" LIVE" if pb == 155 else ""), build_cfg(put_stop_buffer=float(pb)))
               for pb in [100, 125, 150, 155, 175, 200, 225, 250, 300, 400, 500]]
    p1 = run_phase("PHASE 1: Put Buffer", configs, n)
    all_results.extend(p1)

    # Phase 2: Call buffer
    configs = [(f"call=${cb/100:.2f}" + (" LIVE" if cb == 35 else ""), build_cfg(call_stop_buffer=float(cb)))
               for cb in [10, 20, 25, 35, 50, 75, 100, 125, 150]]
    p2 = run_phase("PHASE 2: Call Buffer", configs, n)
    all_results.extend(p2)

    # Phase 3: Buffer decay
    decay_combos = [
        ("no_decay", {"buffer_decay_start_mult": None, "buffer_decay_hours": None}),
        ("1.5x/2h", {"buffer_decay_start_mult": 1.5, "buffer_decay_hours": 2.0}),
        ("2.0x/2h", {"buffer_decay_start_mult": 2.0, "buffer_decay_hours": 2.0}),
        ("2.1x/2h LIVE", {"buffer_decay_start_mult": 2.1, "buffer_decay_hours": 2.0}),
        ("2.1x/3h", {"buffer_decay_start_mult": 2.1, "buffer_decay_hours": 3.0}),
        ("2.5x/2h", {"buffer_decay_start_mult": 2.5, "buffer_decay_hours": 2.0}),
        ("2.5x/3h", {"buffer_decay_start_mult": 2.5, "buffer_decay_hours": 3.0}),
        ("3.0x/2h", {"buffer_decay_start_mult": 3.0, "buffer_decay_hours": 2.0}),
        ("3.0x/3h", {"buffer_decay_start_mult": 3.0, "buffer_decay_hours": 3.0}),
        ("3.0x/4h", {"buffer_decay_start_mult": 3.0, "buffer_decay_hours": 4.0}),
    ]
    configs = [(label, build_cfg(**overrides)) for label, overrides in decay_combos]
    p3 = run_phase("PHASE 3: Buffer Decay", configs, n)
    all_results.extend(p3)

    # Phase 4: Combined best from each phase
    best_put = max(p1, key=lambda s: s["sharpe"])
    best_call = max(p2, key=lambda s: s["sharpe"])
    best_decay = max(p3, key=lambda s: s["sharpe"])

    # Extract values from labels
    bp = float(best_put["label"].split("$")[1].split()[0]) * 100
    bc = float(best_call["label"].split("$")[1].split()[0]) * 100

    # Parse decay
    if "no_decay" in best_decay["label"]:
        bd_mult, bd_hours = None, None
    else:
        parts = best_decay["label"].replace(" LIVE", "").split("x/")
        bd_mult = float(parts[0])
        bd_hours = float(parts[1].replace("h", ""))

    combined_label = f"COMBINED: put=${bp/100:.2f} call=${bc/100:.2f} decay={best_decay['label'].replace(' LIVE','')}"
    combined_overrides = {
        "put_stop_buffer": bp,
        "call_stop_buffer": bc,
    }
    if bd_mult is not None:
        combined_overrides["buffer_decay_start_mult"] = bd_mult
        combined_overrides["buffer_decay_hours"] = bd_hours
    else:
        combined_overrides["buffer_decay_start_mult"] = None
        combined_overrides["buffer_decay_hours"] = None

    live_label = "CURRENT LIVE"
    configs = [
        (combined_label, build_cfg(**combined_overrides)),
        (live_label, build_cfg()),  # Current live config with slippage
    ]
    p4 = run_phase("PHASE 4: Combined Best vs Current Live", configs, n)
    all_results.extend(p4)

    # Save
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"final_reopt_{ts}.csv"
    keys = list(all_results[0].keys())
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_results)
    print(f"\nResults saved -> {csv_path}")
