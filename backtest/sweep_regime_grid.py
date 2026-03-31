"""
HYDRA VIX Regime + Day-of-Week Exhaustive Grid Sweep

Phase 1: For each VIX regime bin, independently sweep max_entries × put_stop_buffer
Phase 2: Day-of-week: sweep all skip combos + entry cap combos
Phase 3: Cross-interactions: best VIX regime × best DoW combo

Run: python -m backtest.sweep_regime_grid
"""
import csv
import itertools
import multiprocessing as mp
import os
import statistics
import time
from copy import deepcopy
from datetime import date, datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Tuple

from backtest.config import live_config
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)
N_WORKERS = min(8, os.cpu_count() or 4)

LOG_FILE = Path("backtest/results") / f"regime_grid_{dt.now().strftime('%Y%m%d_%H%M%S')}.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    print(msg, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")


def _metrics(results: List[DayResult]) -> Dict[str, Any]:
    daily = [r.net_pnl for r in results]
    n = len(daily)
    if n == 0:
        return {"days": 0, "net_pnl": 0, "sharpe": 0, "sortino": 0,
                "max_dd": 0, "win_rate": 0, "avg_daily": 0}
    total = sum(daily)
    mean = statistics.mean(daily)
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * (252 ** 0.5) if std > 0 else 0
    neg = [p for p in daily if p < 0]
    dd_dev = (sum(p ** 2 for p in neg) / n) ** 0.5 if neg else 0
    sortino = mean / dd_dev * (252 ** 0.5) if dd_dev > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p; peak = max(peak, cum); max_dd = min(max_dd, cum - peak)
    wins = sum(1 for p in daily if p > 0)
    return {
        "days": n, "net_pnl": total, "sharpe": sharpe, "sortino": sortino,
        "max_dd": max_dd, "win_rate": wins / n * 100 if n > 0 else 0,
        "avg_daily": mean,
    }


def _run_one(args: Tuple) -> Tuple:
    idx, label, cfg = args
    results = run_backtest(cfg, verbose=False)
    m = _metrics(results)
    return (idx, label, m)


def _base():
    cfg = live_config()
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    return cfg


def _run_sweep(configs, title):
    """Run a list of (label, cfg) tuples and return sorted results."""
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)
            idx, label, m = res
            log(f"    [{len(results)}/{len(tasks)}] {label:50s} Sharpe {m['sharpe']:.3f}  "
                f"P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}")
    results.sort(key=lambda x: x[0])
    return results


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1: VIX REGIME — SWEEP EACH BIN INDEPENDENTLY
# ═══════════════════════════════════════════════════════════════════════════

def phase1_vix_regime():
    log(f"\n{'═'*80}")
    log(f"PHASE 1: VIX REGIME — INDEPENDENT BIN OPTIMIZATION")
    log(f"Breakpoints: [14, 20, 30] → 4 bins")
    log(f"For each bin: sweep max_entries × put_stop_buffer")
    log(f"{'═'*80}")

    # Test breakpoints first
    log(f"\n── 1A: Breakpoint optimization ──")
    log(f"Testing different breakpoint sets\n")
    bp_configs = []
    breakpoint_sets = [
        [14.0, 20.0, 30.0],  # current
        [15.0, 22.0, 30.0],
        [13.0, 18.0, 28.0],
        [14.0, 22.0, 32.0],
        [15.0, 20.0, 25.0],
        [12.0, 20.0, 30.0],
        [14.0, 25.0],         # 3 bins only
        [20.0],               # 2 bins only
        [15.0, 25.0],         # 3 bins
    ]
    for bp in breakpoint_sets:
        cfg = _base()
        cfg.vix_regime_enabled = True
        cfg.vix_regime_breakpoints = bp
        n_bins = len(bp) + 1
        # Simple default: 2 entries for first and last bins, 3 for middle
        max_e = [2] + [None] * (n_bins - 2) + [1] if n_bins >= 3 else [None] * n_bins
        cfg.vix_regime_max_entries = max_e
        cfg.vix_regime_put_stop_buffer = [None] * n_bins
        cfg.vix_regime_call_stop_buffer = [None] * n_bins
        bp_configs.append((f"BP={bp} max_e={max_e}", cfg))

    # Also no regime at all
    cfg = _base()
    bp_configs.append(("No VIX regime (baseline)", cfg))

    bp_results = _run_sweep(bp_configs, "Breakpoints")
    best_bp_idx = max(range(len(bp_results)), key=lambda i: bp_results[i][2]["sharpe"])
    log(f"\n  Best breakpoints: {bp_results[best_bp_idx][1]}")

    # Use [14, 20, 30] for the per-bin sweep (most granular)
    BREAKPOINTS = [14.0, 20.0, 30.0]

    # Sweep each bin independently
    entries_options = [1, 2, 3]  # None = 3 in base config
    buffer_options = [None, 1.25, 1.55, 2.00, 2.50, 3.00]  # None = use default $1.55
    # None means "no override" = keep base $1.55

    best_per_bin = {}

    for bin_idx, bin_label in enumerate(["VIX<14", "VIX 14-20", "VIX 20-30", "VIX≥30"]):
        log(f"\n── 1B-{bin_idx}: {bin_label} ──")
        log(f"Sweep: max_entries × put_stop_buffer\n")

        configs = []
        for max_e in entries_options:
            for buf in buffer_options:
                cfg = _base()
                cfg.vix_regime_enabled = True
                cfg.vix_regime_breakpoints = BREAKPOINTS

                # Set all bins to "no override" (None)
                me_list = [None, None, None, None]
                psb_list = [None, None, None, None]
                csb_list = [None, None, None, None]

                # Override only this bin
                me_list[bin_idx] = max_e
                psb_list[bin_idx] = buf

                cfg.vix_regime_max_entries = me_list
                cfg.vix_regime_put_stop_buffer = psb_list
                cfg.vix_regime_call_stop_buffer = csb_list

                buf_str = f"${buf:.2f}" if buf is not None else "default"
                configs.append((f"{bin_label}: {max_e}e + {buf_str} buf", cfg))

        # Also test 0 entries (skip bin entirely)
        for buf in [None]:
            cfg = _base()
            cfg.vix_regime_enabled = True
            cfg.vix_regime_breakpoints = BREAKPOINTS
            me_list = [None, None, None, None]
            me_list[bin_idx] = 0
            cfg.vix_regime_max_entries = me_list
            cfg.vix_regime_put_stop_buffer = [None, None, None, None]
            cfg.vix_regime_call_stop_buffer = [None, None, None, None]
            configs.append((f"{bin_label}: SKIP (0e)", cfg))

        results = _run_sweep(configs, bin_label)

        # Find best
        by_sharpe = sorted(results, key=lambda x: x[2]["sharpe"], reverse=True)
        log(f"\n  Top 5 for {bin_label}:")
        for _, label, m in by_sharpe[:5]:
            log(f"    {label:50s} Sharpe {m['sharpe']:.3f}  P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}")

        best = by_sharpe[0]
        best_per_bin[bin_idx] = best
        log(f"  ★ Best for {bin_label}: {best[1]}")

    # Now combine the best per-bin into one config
    log(f"\n── 1C: Combined best-per-bin ──")
    log(f"Combining optimal settings from each bin\n")

    # Parse best settings per bin (need to extract max_e and buf from each)
    # For simplicity, re-run with the identified best settings
    return best_per_bin


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2: DAY-OF-WEEK — EXHAUSTIVE SWEEP
# ═══════════════════════════════════════════════════════════════════════════

def phase2_dow():
    log(f"\n{'═'*80}")
    log(f"PHASE 2: DAY-OF-WEEK — EXHAUSTIVE SWEEP")
    log(f"{'═'*80}")

    day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}

    # 2A: All skip combinations (2^5 = 32, minus skip-all and skip-none = 30)
    log(f"\n── 2A: Skip day combinations ──\n")
    configs = []
    configs.append(("No skip (baseline)", _base()))
    for n_skip in range(1, 4):  # skip 1, 2, or 3 days (skipping 4+ leaves too few days)
        for combo in itertools.combinations(range(5), n_skip):
            cfg = _base()
            cfg.skip_weekdays = list(combo)
            label = "Skip " + "+".join(day_names[d] for d in combo)
            configs.append((label, cfg))

    skip_results = _run_sweep(configs, "Skip combos")
    by_sharpe = sorted(skip_results, key=lambda x: x[2]["sharpe"], reverse=True)
    log(f"\n  Top 10 skip combos:")
    for _, label, m in by_sharpe[:10]:
        log(f"    {label:40s} Sharpe {m['sharpe']:.3f}  P&L ${m['net_pnl']:+>9,.0f}  "
            f"MaxDD ${m['max_dd']:>7,.0f}  Win {m['win_rate']:.1f}%  {m['days']} days")

    best_skip = by_sharpe[0]

    # 2B: Entry cap per day (test capping each day to 1 or 2)
    log(f"\n── 2B: Entry cap per day ──\n")
    configs = []
    configs.append(("No cap (baseline)", _base()))
    for dow in range(5):
        for cap in [1, 2]:
            cfg = _base()
            cfg.dow_max_entries = {dow: cap}
            configs.append((f"{day_names[dow]}={cap}e", cfg))

    # Multi-day caps
    for combo in [(0, 4), (2, 3), (0, 3, 4)]:
        for cap in [2]:
            cfg = _base()
            cfg.dow_max_entries = {d: cap for d in combo}
            label = "+".join(day_names[d] for d in combo) + f"={cap}e"
            configs.append((label, cfg))

    cap_results = _run_sweep(configs, "Entry caps")
    by_sharpe_cap = sorted(cap_results, key=lambda x: x[2]["sharpe"], reverse=True)
    log(f"\n  Top 10 entry caps:")
    for _, label, m in by_sharpe_cap[:10]:
        log(f"    {label:40s} Sharpe {m['sharpe']:.3f}  P&L ${m['net_pnl']:+>9,.0f}  "
            f"MaxDD ${m['max_dd']:>7,.0f}  Win {m['win_rate']:.1f}%")

    best_cap = by_sharpe_cap[0]

    # 2C: Best skip + best cap combined
    log(f"\n── 2C: Best skip + best cap combined ──\n")
    configs = []
    # Get skip days from best_skip label
    best_skip_label = best_skip[1]
    best_cap_label = best_cap[1]

    # Parse skip days from label
    if "No skip" in best_skip_label:
        best_skip_days = []
    else:
        best_skip_days = [k for k, v in day_names.items()
                          if v in best_skip_label.replace("Skip ", "")]

    # Parse cap from label (format: "Wed+Thu=2e" or "Fri=2e" or "No cap (baseline)")
    if "No cap" in best_cap_label:
        best_cap_dict = {}
    else:
        best_cap_dict = {}
        # Extract the cap value from the =Ne part
        if "=" in best_cap_label:
            cap_val = int(best_cap_label.split("=")[1].replace("e", ""))
            # Find which days are named before the =
            days_part = best_cap_label.split("=")[0]
            for dow, name in day_names.items():
                if name in days_part:
                    best_cap_dict[dow] = cap_val

    cfg = _base()
    cfg.skip_weekdays = best_skip_days
    cfg.dow_max_entries = best_cap_dict
    configs.append((f"Combined: {best_skip_label} + {best_cap_label}", cfg))

    # Also test top 3 skips × top 3 caps
    top_skips = by_sharpe[:3]
    top_caps = by_sharpe_cap[:3]
    for _, skip_label, _ in top_skips:
        for _, cap_label, _ in top_caps:
            if "No skip" in skip_label and "No cap" in cap_label:
                continue  # baseline already tested

            skip_days = []
            if "Skip" in skip_label:
                skip_days = [k for k, v in day_names.items()
                             if v in skip_label.replace("Skip ", "")]

            cap_dict = {}
            if "No cap" not in cap_label and "=" in cap_label:
                cap_val = int(cap_label.split("=")[1].replace("e", ""))
                days_part = cap_label.split("=")[0]
                for dow, name in day_names.items():
                    if name in days_part:
                        cap_dict[dow] = cap_val

            # Don't cap a day we're already skipping
            cap_dict = {k: v for k, v in cap_dict.items() if k not in skip_days}

            if not skip_days and not cap_dict:
                continue

            cfg = _base()
            cfg.skip_weekdays = skip_days
            cfg.dow_max_entries = cap_dict
            configs.append((f"{skip_label} + {cap_label}", cfg))

    combined_results = _run_sweep(configs, "Skip+Cap combos")
    by_sharpe_combined = sorted(combined_results, key=lambda x: x[2]["sharpe"], reverse=True)
    log(f"\n  Top 5 combined:")
    for _, label, m in by_sharpe_combined[:5]:
        log(f"    {label:50s} Sharpe {m['sharpe']:.3f}  P&L ${m['net_pnl']:+>9,.0f}  "
            f"MaxDD ${m['max_dd']:>7,.0f}")

    return by_sharpe[0], by_sharpe_cap[0], by_sharpe_combined[0]


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3: CROSS-INTERACTIONS
# ═══════════════════════════════════════════════════════════════════════════

def phase3_cross(best_per_bin, best_dow):
    log(f"\n{'═'*80}")
    log(f"PHASE 3: CROSS-INTERACTIONS")
    log(f"Best VIX regime × Best DoW settings")
    log(f"{'═'*80}\n")

    day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}

    # Parse best DoW settings
    _, best_dow_label, _ = best_dow
    if "No skip" in best_dow_label and "No cap" in best_dow_label:
        best_skip_days = []
        best_cap_dict = {}
    else:
        # Parse from the combined label
        best_skip_days = []
        best_cap_dict = {}
        if "Skip" in best_dow_label:
            skip_part = best_dow_label.split(" + ")[0] if " + " in best_dow_label else best_dow_label
            best_skip_days = [k for k, v in day_names.items()
                              if v in skip_part.replace("Skip ", "")]
        if "=" in best_dow_label:
            for part in best_dow_label.split(" + "):
                part = part.strip()
                if "=" in part and "Skip" not in part:
                    cap_val = int(part.split("=")[1].replace("e", ""))
                    days_part = part.split("=")[0]
                    for dow, name in day_names.items():
                        if name in days_part:
                            best_cap_dict[dow] = cap_val

    configs = []

    # Baseline (no features)
    configs.append(("Baseline (no features)", _base()))

    # Best DoW only
    cfg = _base()
    cfg.skip_weekdays = best_skip_days
    cfg.dow_max_entries = best_cap_dict
    configs.append((f"DoW only: {best_dow_label}", cfg))

    # Best VIX regime only (combine best per-bin settings)
    # Parse the best entries/buffer for each bin from the results
    # For now, use the labels to reconstruct
    cfg = _base()
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    me_list = [None, None, None, None]
    psb_list = [None, None, None, None]
    for bin_idx, (_, label, _) in best_per_bin.items():
        # Parse entries from label like "VIX<14: 2e + $1.55 buf"
        if "SKIP" in label:
            me_list[bin_idx] = 0
        else:
            parts = label.split(": ")[1] if ": " in label else label
            e_part = parts.split("e")[0].strip()
            try:
                me_list[bin_idx] = int(e_part)
            except ValueError:
                me_list[bin_idx] = None
            if "default" not in parts:
                buf_part = parts.split("$")[1].split(" ")[0] if "$" in parts else None
                if buf_part:
                    try:
                        psb_list[bin_idx] = float(buf_part)
                    except ValueError:
                        pass
    cfg.vix_regime_max_entries = me_list
    cfg.vix_regime_put_stop_buffer = psb_list
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    configs.append((f"VIX regime only: e={me_list} buf={psb_list}", cfg))

    # Combined: best DoW + best VIX regime
    cfg = deepcopy(configs[-1][1])  # copy VIX regime config
    cfg.skip_weekdays = best_skip_days
    cfg.dow_max_entries = best_cap_dict
    configs.append((f"COMBINED: {best_dow_label} + VIX regime", cfg))

    # Also test with 2 contracts
    cfg_2c = deepcopy(configs[-1][1])
    cfg_2c.contracts = 2
    configs.append((f"COMBINED 2c: {best_dow_label} + VIX regime", cfg_2c))

    results = _run_sweep(configs, "Cross-interactions")
    by_sharpe = sorted(results, key=lambda x: x[2]["sharpe"], reverse=True)

    log(f"\n  Final rankings:")
    baseline_sharpe = results[0][2]["sharpe"]
    for _, label, m in by_sharpe:
        delta = m["sharpe"] - baseline_sharpe
        log(f"    {label:55s} Sharpe {m['sharpe']:.3f} ({delta:+.3f})  "
            f"P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}  Win {m['win_rate']:.1f}%")

    return by_sharpe


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    start_time = time.time()
    log(f"{'═'*80}")
    log(f"HYDRA VIX REGIME + DOW EXHAUSTIVE GRID SWEEP")
    log(f"Started: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Period: {START_DATE} → {END_DATE} | 1-min | Real Greeks | {N_WORKERS} workers")
    log(f"{'═'*80}")

    # Phase 1
    best_per_bin = phase1_vix_regime()

    # Phase 2
    best_skip, best_cap, best_dow = phase2_dow()

    # Phase 3
    final_rankings = phase3_cross(best_per_bin, best_dow)

    elapsed = time.time() - start_time
    log(f"\n{'═'*80}")
    log(f"SWEEP COMPLETE — {elapsed/3600:.1f} hours")
    log(f"Log: {LOG_FILE}")
    log(f"{'═'*80}")


if __name__ == "__main__":
    main()
