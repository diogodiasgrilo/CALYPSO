"""
Remaining parameter sweeps with slippage + new buffer baseline.

Base: put=$2.00, call=$1.00, decay=3.0x/4h, slip=$0.30, mk=10%
      VIX regime credit overrides REMOVED (Phase 6 finding)

Phase 1: vix_regime.breakpoints
Phase 2: vix_regime.max_entries
Phase 3: base_entry_downday_callonly_pct
Phase 4: max_spread_width (fine-grained)
Phase 5: call_credit_floor
Phase 6: put_credit_floor
Phase 7: put_only_max_vix
Phase 8: downday_theoretical_put_credit

Run: python -m backtest.sweep_remaining
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

SLIPPAGE = 30.0
MARKUP   = 0.10


def build_cfg(**overrides):
    cfg = live_config()
    cfg.start_date = FULL_START
    cfg.end_date = FULL_END
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.stop_slippage_per_leg = SLIPPAGE
    cfg.stop_spread_markup_pct = MARKUP
    # New buffer baseline
    cfg.put_stop_buffer = 200.0
    cfg.call_stop_buffer = 100.0
    cfg.buffer_decay_start_mult = 3.0
    cfg.buffer_decay_hours = 4.0
    # Remove VIX regime credit overrides (Phase 6 finding)
    cfg.vix_regime_min_call_credit = [None, None, None, None]
    cfg.vix_regime_min_put_credit = [None, None, None, None]
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
    return {
        "label": label, "sharpe": sharpe, "total_pnl": total,
        "max_dd": dd, "win_rate": wins / days * 100 if days else 0,
        "stop_rate": stops / placed * 100 if placed else 0,
        "stops": stops, "placed": placed,
    }


def _run(args):
    label, cfg = args
    return summarise(run_backtest(cfg, verbose=False), label)


def run_phase(title, configs, n_workers):
    print(f"\n{title}")
    print(f"  {len(configs)} configs, {n_workers} workers")
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

    print(f"\n  {'Label':<40} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>8} {'WR%':>6} {'StopR':>6} {'Stops':>6}")
    print(f"  {'-'*85}")
    for s in all_stats:
        cur = " *" if "CURRENT" in s["label"] else ""
        print(f"  {s['label']:<40} {s['sharpe']:>7.3f} {s['total_pnl']:>10,.0f} {s['max_dd']:>8,.0f} {s['win_rate']:>5.1f}% {s['stop_rate']:>5.1f}% {s['stops']:>6}{cur}")
    best = all_stats[0]
    print(f"\n  Best: {best['label']} (Sharpe {best['sharpe']:.3f})")
    return all_stats


if __name__ == "__main__":
    n = 8
    all_results = []

    print(f"Remaining Sweeps (new baseline + slippage)")
    print(f"Base: put=$2.00, call=$1.00, decay=3.0x/4h, slip=$0.30, mk=10%")
    print(f"VIX regime credit overrides: REMOVED")

    # ═══ Phase 1: VIX regime breakpoints ═══
    bp_combos = [
        ("[14,20,30] CURRENT", [14.0, 20.0, 30.0]),
        ("[12,18,28]",         [12.0, 18.0, 28.0]),
        ("[16,22,32]",         [16.0, 22.0, 32.0]),
        ("[14,18,25]",         [14.0, 18.0, 25.0]),
        ("[15,20,30]",         [15.0, 20.0, 30.0]),
        ("[14,20,25]",         [14.0, 20.0, 25.0]),
        ("[12,20,30]",         [12.0, 20.0, 30.0]),
    ]
    configs = [(label, build_cfg(vix_regime_breakpoints=bp)) for label, bp in bp_combos]
    p1 = run_phase("PHASE 1: VIX Regime Breakpoints", configs, n)
    all_results.extend(p1)

    # ═══ Phase 2: VIX regime max_entries ═══
    me_combos = [
        ("[2,n,n,1] CURRENT",     [2, None, None, 1]),
        ("[3,n,n,1]",             [3, None, None, 1]),
        ("[2,n,n,2]",             [2, None, None, 2]),
        ("[3,n,n,2]",             [3, None, None, 2]),
        ("[n,n,n,1] (no low cap)", [None, None, None, 1]),
        ("[n,n,n,n] (no caps)",   [None, None, None, None]),
        ("[1,n,n,1]",             [1, None, None, 1]),
        ("[2,n,n,n] (no high cap)", [2, None, None, None]),
    ]
    configs = [(label, build_cfg(vix_regime_max_entries=me)) for label, me in me_combos]
    p2 = run_phase("PHASE 2: VIX Regime Max Entries", configs, n)
    all_results.extend(p2)

    # ═══ Phase 3: base_entry_downday_callonly_pct ═══
    # Values are in percentage (0.57 = 0.57% drop triggers call-only)
    dd_values = [None, 0.30, 0.40, 0.50, 0.57, 0.60, 0.70, 0.80, 1.00, 1.50]
    configs = []
    for v in dd_values:
        label = f"downday={v:.2f}%" if v else "downday=OFF"
        if v == 0.57:
            label += " CURRENT"
        configs.append((label, build_cfg(base_entry_downday_callonly_pct=v)))
    p3 = run_phase("PHASE 3: Base Entry Down-Day Call-Only Threshold", configs, n)
    all_results.extend(p3)

    # ═══ Phase 4: max_spread_width (fine-grained) ═══
    sw_values = [75, 85, 95, 100, 110, 120, 130, 150, 175, 200]
    configs = []
    for v in sw_values:
        label = f"max_spread={v}pt" + (" CURRENT" if v == 110 else "")
        configs.append((label, build_cfg(max_spread_width=v)))
    p4 = run_phase("PHASE 4: Max Spread Width (fine-grained)", configs, n)
    all_results.extend(p4)

    # ═══ Phase 5: call_credit_floor ═══
    ccf_values = [0.25, 0.50, 0.60, 0.75, 1.00, 1.25, 1.50]
    configs = []
    for v in ccf_values:
        label = f"call_floor=${v:.2f}" + (" CURRENT" if v == 0.75 else "")
        configs.append((label, build_cfg(call_credit_floor=v)))
    p5 = run_phase("PHASE 5: Call Credit Floor", configs, n)
    all_results.extend(p5)

    # ═══ Phase 6: put_credit_floor ═══
    pcf_values = [1.25, 1.50, 1.75, 2.00, 2.25, 2.50, 2.75]
    configs = []
    for v in pcf_values:
        label = f"put_floor=${v:.2f}" + (" CURRENT" if v == 2.00 else "")
        configs.append((label, build_cfg(put_credit_floor=v)))
    p6 = run_phase("PHASE 6: Put Credit Floor", configs, n)
    all_results.extend(p6)

    # ═══ Phase 7: put_only_max_vix ═══
    vix_values = [10.0, 12.0, 14.0, 15.0, 18.0, 20.0, 25.0, 30.0]
    configs = []
    for v in vix_values:
        label = f"put_vix_gate={v:.0f}" + (" CURRENT" if v == 15.0 else "")
        configs.append((label, build_cfg(put_only_max_vix=v)))
    p7 = run_phase("PHASE 7: Put-Only Max VIX", configs, n)
    all_results.extend(p7)

    # ═══ Phase 8: downday_theoretical_put_credit ═══
    theo_values = [150.0, 200.0, 225.0, 250.0, 260.0, 275.0, 300.0, 350.0, 400.0]
    configs = []
    for v in theo_values:
        label = f"theo_put=${v/100:.2f}" + (" CURRENT" if v == 260.0 else "")
        configs.append((label, build_cfg(downday_theoretical_put_credit=v)))
    p8 = run_phase("PHASE 8: Theoretical Put Credit (call-only stop)", configs, n)
    all_results.extend(p8)

    # Save
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"remaining_sweep_{ts}.csv"
    keys = list(all_results[0].keys())
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_results)
    print(f"\nResults saved -> {csv_path}")
