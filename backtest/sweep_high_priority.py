"""
High-priority re-test with slippage + new buffer baseline.

Uses the NEW optimal buffers (put=$2.00, call=$1.00, decay=3.0x/4h)
as the base, then sweeps parameters that interact with stop buffers.

Phase 1: VIX regime put_stop_buffer override (currently $1.25 when VIX<14)
Phase 2: spread_vix_multiplier (currently 6.0)
Phase 3: entry_times (2 vs 3 entries)
Phase 4: whipsaw_range_skip_mult (currently 1.75)
Phase 5: calm_entry params (never swept before)

Run: python -m backtest.sweep_high_priority
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

# Calibrated slippage
SLIPPAGE = 30.0    # $0.30/leg
MARKUP   = 0.10    # 10%

# NEW buffer baseline (from final_reopt results)
NEW_PUT_BUFFER = 200.0    # $2.00
NEW_CALL_BUFFER = 100.0   # $1.00
NEW_DECAY_MULT = 3.0
NEW_DECAY_HOURS = 4.0


def build_cfg(**overrides):
    cfg = live_config()
    cfg.start_date = FULL_START
    cfg.end_date = FULL_END
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.stop_slippage_per_leg = SLIPPAGE
    cfg.stop_spread_markup_pct = MARKUP
    # Apply new buffer baseline
    cfg.put_stop_buffer = NEW_PUT_BUFFER
    cfg.call_stop_buffer = NEW_CALL_BUFFER
    cfg.buffer_decay_start_mult = NEW_DECAY_MULT
    cfg.buffer_decay_hours = NEW_DECAY_HOURS
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

    print(f"\n  {'Label':<35} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>8} {'WR%':>6} {'StopR':>6} {'Stops':>6}")
    print(f"  {'-'*80}")
    for s in all_stats:
        cur = " *" if "CURRENT" in s["label"] or "current" in s["label"].lower() else ""
        print(f"  {s['label']:<35} {s['sharpe']:>7.3f} {s['total_pnl']:>10,.0f} {s['max_dd']:>8,.0f} {s['win_rate']:>5.1f}% {s['stop_rate']:>5.1f}% {s['stops']:>6}{cur}")

    best = all_stats[0]
    print(f"\n  Best: {best['label']} (Sharpe {best['sharpe']:.3f})")
    return all_stats


if __name__ == "__main__":
    n = 8
    all_results = []

    print(f"High-Priority Re-test (new buffer baseline + slippage)")
    print(f"Base: put=$2.00, call=$1.00, decay=3.0x/4h, slip=$0.30, mk=10%")
    print(f"Period: {FULL_START} -> {FULL_END}")

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: VIX regime put_stop_buffer
    # Currently [1.25, null, null, null] = $1.25 when VIX<14
    # With new base put buffer at $2.00, should the low-VIX override change?
    # ═══════════════════════════════════════════════════════════════
    configs = []
    for low_vix_buf in [None, 100.0, 125.0, 150.0, 175.0, 200.0]:
        # None = no override (use main $2.00 for all regimes)
        regime_put = [low_vix_buf, None, None, None] if low_vix_buf else [None, None, None, None]
        label = f"VIX<14 put=${low_vix_buf/100:.2f}" if low_vix_buf else "VIX<14 no override (use $2.00)"
        if low_vix_buf == 125.0:
            label += " CURRENT"
        configs.append((label, build_cfg(vix_regime_put_stop_buffer=regime_put)))
    p1 = run_phase("PHASE 1: VIX Regime Put Buffer Override (VIX<14)", configs, n)
    all_results.extend(p1)

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: spread_vix_multiplier
    # Currently 6.0 → spread = round(VIX×6.0/5)×5, floor 25, cap 110
    # ═══════════════════════════════════════════════════════════════
    configs = []
    for mult in [4.0, 5.0, 5.5, 6.0, 6.5, 7.0, 8.0]:
        label = f"spread_mult={mult:.1f}" + (" CURRENT" if mult == 6.0 else "")
        configs.append((label, build_cfg(spread_vix_multiplier=mult)))
    p2 = run_phase("PHASE 2: Spread VIX Multiplier", configs, n)
    all_results.extend(p2)

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: entry_times (number and timing of entries)
    # Test pure base entry count — E6 disabled to isolate effect.
    # Also test with E6 ON for the current 3-entry schedule.
    # VIX regime max_entries still active (matches live behavior).
    # ═══════════════════════════════════════════════════════════════
    configs = []
    entry_schedules = [
        ("1 base [10:45]", ["10:45"], False),
        ("2 base [10:15,11:15]", ["10:15", "11:15"], False),
        ("2 base [10:30,11:00]", ["10:30", "11:00"], False),
        ("3 base [10:15,10:45,11:15] no E6", ["10:15", "10:45", "11:15"], False),
        ("3 base [10:15,10:45,11:15]+E6 CURRENT", ["10:15", "10:45", "11:15"], True),
        ("3 base [10:30,11:00,11:30]", ["10:30", "11:00", "11:30"], False),
        ("4 base [10:15,10:45,11:15,11:45]", ["10:15", "10:45", "11:15", "11:45"], False),
    ]
    for label, times, e6_on in entry_schedules:
        configs.append((label, build_cfg(
            entry_times=times,
            conditional_upday_e6_enabled=e6_on,
        )))
    p3 = run_phase("PHASE 3: Entry Times (E6 off unless labeled)", configs, n)
    all_results.extend(p3)

    # ═══════════════════════════════════════════════════════════════
    # Phase 4: whipsaw_range_skip_mult
    # Currently 1.75 = skip when intraday range > 1.75× expected move
    # ═══════════════════════════════════════════════════════════════
    configs = []
    for ws in [1.25, 1.50, 1.75, 2.00, 2.25, 2.50, 3.00, None]:
        label = f"whipsaw={ws:.2f}" if ws else "whipsaw=OFF"
        if ws == 1.75:
            label += " CURRENT"
        configs.append((label, build_cfg(whipsaw_range_skip_mult=ws)))
    p4 = run_phase("PHASE 4: Whipsaw Filter", configs, n)
    all_results.extend(p4)

    # ═══════════════════════════════════════════════════════════════
    # Phase 5: calm_entry params (never swept before)
    # Currently: threshold=15pt, lookback=3min, max_delay=5min
    # ═══════════════════════════════════════════════════════════════
    configs = []
    calm_combos = [
        ("calm=OFF", {"calm_entry_threshold_pts": None}),
        ("calm=10pt/3m/5m", {"calm_entry_threshold_pts": 10.0, "calm_entry_lookback_min": 3, "calm_entry_max_delay_min": 5}),
        ("calm=15pt/3m/5m CURRENT", {"calm_entry_threshold_pts": 15.0, "calm_entry_lookback_min": 3, "calm_entry_max_delay_min": 5}),
        ("calm=20pt/3m/5m", {"calm_entry_threshold_pts": 20.0, "calm_entry_lookback_min": 3, "calm_entry_max_delay_min": 5}),
        ("calm=15pt/5m/5m", {"calm_entry_threshold_pts": 15.0, "calm_entry_lookback_min": 5, "calm_entry_max_delay_min": 5}),
        ("calm=15pt/3m/10m", {"calm_entry_threshold_pts": 15.0, "calm_entry_lookback_min": 3, "calm_entry_max_delay_min": 10}),
        ("calm=10pt/5m/10m", {"calm_entry_threshold_pts": 10.0, "calm_entry_lookback_min": 5, "calm_entry_max_delay_min": 10}),
        ("calm=20pt/5m/10m", {"calm_entry_threshold_pts": 20.0, "calm_entry_lookback_min": 5, "calm_entry_max_delay_min": 10}),
    ]
    for label, overrides in calm_combos:
        configs.append((label, build_cfg(**overrides)))
    p5 = run_phase("PHASE 5: Calm Entry Filter", configs, n)
    all_results.extend(p5)

    # ═══════════════════════════════════════════════════════════════
    # Phase 6: VIX regime credit gate overrides
    # Currently VIX 14-20 uses min_call=$1.35, min_put=$2.10
    # With new base gates $2.00/$2.75, these LOWER the gates in that
    # regime — test if removing or adjusting them is better
    # ═══════════════════════════════════════════════════════════════
    configs = []
    regime_credit_combos = [
        ("VIX14-20 c$1.35/p$2.10 CURRENT",
         [None, 1.35, None, None], [None, 2.10, None, None]),
        ("VIX14-20 no override (use base)",
         [None, None, None, None], [None, None, None, None]),
        ("VIX14-20 c$1.75/p$2.50",
         [None, 1.75, None, None], [None, 2.50, None, None]),
        ("VIX14-20 c$2.00/p$2.75 (=base)",
         [None, 2.00, None, None], [None, 2.75, None, None]),
        ("VIX14-20 c$1.00/p$1.75 (looser)",
         [None, 1.00, None, None], [None, 1.75, None, None]),
    ]
    for label, call_overrides, put_overrides in regime_credit_combos:
        configs.append((label, build_cfg(
            vix_regime_min_call_credit=call_overrides,
            vix_regime_min_put_credit=put_overrides,
        )))
    p6 = run_phase("PHASE 6: VIX Regime Credit Gate Overrides (VIX 14-20)", configs, n)
    all_results.extend(p6)

    # Save
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"high_priority_{ts}.csv"
    keys = list(all_results[0].keys())
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_results)
    print(f"\nResults saved -> {csv_path}")
