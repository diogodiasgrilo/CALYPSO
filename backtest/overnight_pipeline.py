"""
HYDRA Overnight Optimization Pipeline — 1-Minute Resolution

Complete automated pipeline that runs overnight and produces deployment-ready config.

Phase 1: Re-converge all core parameters (loop until Sharpe stabilizes)
Phase 2: Test new features (Skip Wed/Thu, VIX regime, Fri=2e) with converged params
Phase 3: Contract scaling (1c vs 2c for best combos)
Phase 4: Overfitting validation
  4A: Year-by-year stability (does it work every year, not just 1-2?)
  4B: Half-sample cross-validation (optimize on half, test on other half)
  4C: Day-of-week consistency per year (is Wed/Thu really bad every year?)
  4D: Bootstrap confidence intervals (is the Sharpe statistically significant?)

Run: python -m backtest.overnight_pipeline
"""
import csv
import multiprocessing as mp
import os
import random
import statistics
import time
from copy import deepcopy
from datetime import date, datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backtest.config import BacktestConfig, live_config
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)
N_WORKERS = min(8, os.cpu_count() or 4)
CONVERGE_THRESHOLD = 0.01
MAX_PASSES = 5

LOG_FILE = Path("backtest/results") / f"overnight_pipeline_{dt.now().strftime('%Y%m%d_%H%M%S')}.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    """Print and write to log file."""
    print(msg, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")


def _metrics(results: List[DayResult]) -> Dict[str, Any]:
    daily = [r.net_pnl for r in results]
    n = len(daily)
    if n == 0:
        return {"days": 0, "net_pnl": 0, "sharpe": 0, "sortino": 0,
                "max_dd": 0, "win_rate": 0, "avg_daily": 0, "entries": 0, "stops": 0}
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
    placed = sum(1 for r in results for e in r.entries if e.entry_type != "skipped")
    stops = sum(
        (1 if e.call_outcome == "stopped" else 0) +
        (1 if e.put_outcome == "stopped" else 0)
        for r in results for e in r.entries if e.entry_type != "skipped"
    )
    return {
        "days": n, "net_pnl": total, "sharpe": sharpe, "sortino": sortino,
        "max_dd": max_dd, "win_rate": wins / n * 100 if n > 0 else 0,
        "avg_daily": mean, "entries": placed, "stops": stops,
    }


def _run_one(args: Tuple) -> Tuple:
    idx, label, cfg = args
    results = run_backtest(cfg, verbose=False)
    m = _metrics(results)
    return (idx, label, m, results)


def _run_one_no_results(args: Tuple) -> Tuple:
    """Same but don't return raw results (saves memory for large sweeps)."""
    idx, label, cfg = args
    results = run_backtest(cfg, verbose=False)
    m = _metrics(results)
    return (idx, label, m)


def _sweep_param(base_cfg: BacktestConfig, param_name: str,
                 values: list, label_fn=None) -> Tuple[Any, Dict]:
    if label_fn is None:
        label_fn = lambda v: str(v)
    tasks = []
    for i, val in enumerate(values):
        cfg = deepcopy(base_cfg)
        setattr(cfg, param_name, val)
        tasks.append((i, f"{param_name}={label_fn(val)}", cfg))
    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one_no_results, tasks):
            results.append(res)
            idx, label, m = res
            log(f"    {label:45s} Sharpe {m['sharpe']:.3f}  P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}")
    results.sort(key=lambda x: x[0])
    best_idx = max(range(len(results)), key=lambda i: results[i][2]["sharpe"])
    return values[best_idx], results[best_idx][2]


def _sweep_2d(base_cfg: BacktestConfig, param_a: str, values_a: list,
              param_b: str, values_b: list,
              label_fn_a=None, label_fn_b=None) -> Tuple[Any, Any, Dict]:
    if label_fn_a is None: label_fn_a = lambda v: str(v)
    if label_fn_b is None: label_fn_b = lambda v: str(v)
    tasks = []; grid = []; i = 0
    for va in values_a:
        for vb in values_b:
            cfg = deepcopy(base_cfg)
            setattr(cfg, param_a, va); setattr(cfg, param_b, vb)
            label = f"{param_a}={label_fn_a(va)} {param_b}={label_fn_b(vb)}"
            tasks.append((i, label, cfg)); grid.append((va, vb)); i += 1
    results = []
    total = len(tasks)
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one_no_results, tasks):
            results.append(res)
            idx, label, m = res
            log(f"    [{len(results)}/{total}] {label:55s} Sharpe {m['sharpe']:.3f}  "
                f"P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}")
    results.sort(key=lambda x: x[0])
    best_idx = max(range(len(results)), key=lambda i: results[i][2]["sharpe"])
    best_a, best_b = grid[best_idx]
    return best_a, best_b, results[best_idx][2]


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1: RE-CONVERGENCE
# ═══════════════════════════════════════════════════════════════════════════

def run_convergence_pass(cfg: BacktestConfig, pass_num: int) -> Tuple[BacktestConfig, float, List]:
    round_log = []

    # R1: spread_vix_multiplier
    log(f"  R1: spread_vix_multiplier (cur={cfg.spread_vix_multiplier})")
    best, m = _sweep_param(cfg, "spread_vix_multiplier",
                           [3.0, 3.5, 4.0, 4.5, 5.0, 5.3, 5.5, 6.0, 6.5, 7.0],
                           lambda v: f"{v:.1f}")
    log(f"  ★ R1: {best:.1f}  Sharpe {m['sharpe']:.3f}\n")
    cfg.spread_vix_multiplier = best
    round_log.append(("spread_vix_mult", best, m))

    # R2: credit gates (2D)
    log(f"  R2: credit gates (cur call=${cfg.min_call_credit} put=${cfg.min_put_credit})")
    best_c, best_p, m = _sweep_2d(cfg, "min_call_credit",
                                   [0.75, 1.00, 1.25, 1.35, 1.50, 1.75, 2.00],
                                   "min_put_credit",
                                   [1.50, 1.75, 2.00, 2.10, 2.25, 2.50, 2.75],
                                   lambda v: f"${v:.2f}", lambda v: f"${v:.2f}")
    log(f"  ★ R2: call=${best_c:.2f} put=${best_p:.2f}  Sharpe {m['sharpe']:.3f}\n")
    cfg.min_call_credit = best_c; cfg.min_put_credit = best_p
    round_log.append(("credit_gates", f"c={best_c} p={best_p}", m))

    # R3: credit floors (2D)
    log(f"  R3: credit floors (cur call=${cfg.call_credit_floor} put=${cfg.put_credit_floor})")
    best_cf, best_pf, m = _sweep_2d(cfg, "call_credit_floor",
                                     [0.40, 0.50, 0.60, 0.75, 0.85, 1.00],
                                     "put_credit_floor",
                                     [1.50, 1.75, 1.90, 2.00, 2.07, 2.15, 2.25],
                                     lambda v: f"${v:.2f}", lambda v: f"${v:.2f}")
    log(f"  ★ R3: call_floor=${best_cf:.2f} put_floor=${best_pf:.2f}  Sharpe {m['sharpe']:.3f}\n")
    cfg.call_credit_floor = best_cf; cfg.put_credit_floor = best_pf
    round_log.append(("credit_floors", f"c={best_cf} p={best_pf}", m))

    # R4: stop buffers (2D)
    log(f"  R4: stop buffers (cur call=${cfg.call_stop_buffer} put=${cfg.put_stop_buffer})")
    best_csb, best_psb, m = _sweep_2d(cfg, "call_stop_buffer",
                                       [10.0, 20.0, 35.0, 50.0, 75.0, 100.0],
                                       "put_stop_buffer",
                                       [100.0, 125.0, 155.0, 200.0, 250.0, 300.0, 400.0, 500.0],
                                       lambda v: f"${v/100:.2f}", lambda v: f"${v/100:.2f}")
    log(f"  ★ R4: call=${best_csb/100:.2f} put=${best_psb/100:.2f}  Sharpe {m['sharpe']:.3f}\n")
    cfg.call_stop_buffer = best_csb; cfg.put_stop_buffer = best_psb
    round_log.append(("stop_buffers", f"c={best_csb} p={best_psb}", m))

    # R5: theo put credit
    log(f"  R5: theo put credit (cur=${cfg.downday_theoretical_put_credit/100:.2f})")
    best, m = _sweep_param(cfg, "downday_theoretical_put_credit",
                           [100.0, 150.0, 175.0, 200.0, 225.0, 250.0, 260.0, 275.0, 300.0, 350.0],
                           lambda v: f"${v/100:.2f}")
    log(f"  ★ R5: ${best/100:.2f}  Sharpe {m['sharpe']:.3f}\n")
    cfg.downday_theoretical_put_credit = best
    round_log.append(("theo_put", best, m))

    # R6: downday call-only pct
    cur = cfg.base_entry_downday_callonly_pct
    log(f"  R6: downday pct (cur={'OFF' if cur is None else f'{cur:.2f}%'})")
    best, m = _sweep_param(cfg, "base_entry_downday_callonly_pct",
                           [None, 0.20, 0.30, 0.40, 0.50, 0.57, 0.60, 0.70, 0.80, 1.00],
                           lambda v: "OFF" if v is None else f"{v:.2f}%")
    log(f"  ★ R6: {'OFF' if best is None else f'{best:.2f}%'}  Sharpe {m['sharpe']:.3f}\n")
    cfg.base_entry_downday_callonly_pct = best
    round_log.append(("downday_pct", best, m))

    # R7: upday E6
    log(f"  R7: upday E6 (cur={'ON' if cfg.conditional_upday_e6_enabled else 'OFF'} thr={cfg.upday_threshold_pct}%)")
    tasks = []; vals_map = {}; i = 0
    c = deepcopy(cfg); c.conditional_upday_e6_enabled = False
    tasks.append((i, "E6=OFF", c)); vals_map[i] = (False, 0); i += 1
    for thr in [0.20, 0.30, 0.40, 0.48, 0.50, 0.60, 0.70, 0.80, 1.00]:
        c = deepcopy(cfg); c.conditional_upday_e6_enabled = True; c.upday_threshold_pct = thr
        tasks.append((i, f"E6=ON thr={thr:.2f}%", c)); vals_map[i] = (True, thr); i += 1
    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one_no_results, tasks):
            results.append(res)
            idx, label, m_r = res
            log(f"    {label:45s} Sharpe {m_r['sharpe']:.3f}  P&L ${m_r['net_pnl']:+>9,.0f}  MaxDD ${m_r['max_dd']:>7,.0f}")
    results.sort(key=lambda x: x[0])
    best_idx = max(range(len(results)), key=lambda i_r: results[i_r][2]["sharpe"])
    best_e6, best_thr = vals_map[best_idx]
    m = results[best_idx][2]
    log(f"  ★ R7: E6={'ON' if best_e6 else 'OFF'}{f' thr={best_thr:.2f}%' if best_e6 else ''}  Sharpe {m['sharpe']:.3f}\n")
    cfg.conditional_upday_e6_enabled = best_e6
    if best_e6: cfg.upday_threshold_pct = best_thr
    round_log.append(("upday_e6", f"{'ON' if best_e6 else 'OFF'} thr={best_thr}", m))

    # R8: whipsaw
    log(f"  R8: whipsaw (cur={cfg.whipsaw_range_skip_mult})")
    best, m = _sweep_param(cfg, "whipsaw_range_skip_mult",
                           [None, 1.00, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00],
                           lambda v: "OFF" if v is None else f"{v:.2f}×")
    log(f"  ★ R8: {'OFF' if best is None else f'{best:.2f}×'}  Sharpe {m['sharpe']:.3f}\n")
    cfg.whipsaw_range_skip_mult = best
    round_log.append(("whipsaw", best, m))

    return cfg, m["sharpe"], round_log


def phase1_reconverge() -> BacktestConfig:
    log(f"\n{'═'*80}")
    log(f"PHASE 1: PARAMETER RE-CONVERGENCE")
    log(f"{'═'*80}\n")

    cfg = live_config()
    cfg.start_date = START_DATE; cfg.end_date = END_DATE
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"

    prev_sharpe = 0.0
    for pass_num in range(1, MAX_PASSES + 1):
        log(f"\n{'#'*80}")
        log(f"#  CONVERGENCE PASS {pass_num} / {MAX_PASSES}")
        log(f"{'#'*80}\n")

        cfg, sharpe, round_log = run_convergence_pass(deepcopy(cfg), pass_num)
        improvement = sharpe - prev_sharpe
        log(f"  Pass {pass_num}: Sharpe {sharpe:.3f} (Δ {improvement:+.3f})")

        if pass_num > 1 and improvement < CONVERGE_THRESHOLD:
            log(f"  ✓ CONVERGED at pass {pass_num} (Δ {improvement:.4f} < {CONVERGE_THRESHOLD})")
            break
        prev_sharpe = sharpe

    log(f"\n── Phase 1 converged config ──")
    for attr in ["spread_vix_multiplier", "max_spread_width", "min_call_credit",
                 "min_put_credit", "call_credit_floor", "put_credit_floor",
                 "call_stop_buffer", "put_stop_buffer", "downday_theoretical_put_credit",
                 "base_entry_downday_callonly_pct", "conditional_upday_e6_enabled",
                 "upday_threshold_pct", "whipsaw_range_skip_mult"]:
        log(f"  {attr:36s} = {getattr(cfg, attr)}")

    return cfg


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2: NEW FEATURES SWEEP (with converged params)
# ═══════════════════════════════════════════════════════════════════════════

def phase2_features(base_cfg: BacktestConfig) -> Tuple[str, BacktestConfig, Dict]:
    log(f"\n{'═'*80}")
    log(f"PHASE 2: NEW FEATURES SWEEP")
    log(f"{'═'*80}\n")

    configs: List[Tuple[str, BacktestConfig]] = []

    # Baseline (converged, no new features)
    configs.append(("Baseline (converged)", deepcopy(base_cfg)))

    # Skip individual days
    for dow, name in [(2, "Wed"), (3, "Thu")]:
        cfg = deepcopy(base_cfg); cfg.skip_weekdays = [dow]
        configs.append((f"Skip {name}", cfg))

    # Skip Wed+Thu
    cfg = deepcopy(base_cfg); cfg.skip_weekdays = [2, 3]
    configs.append(("Skip Wed+Thu", cfg))

    # Fri=2e
    cfg = deepcopy(base_cfg); cfg.dow_max_entries = {4: 2}
    configs.append(("Fri=2e", cfg))

    # VIX regimes
    for label, bp, max_e, psb, csb, mpc, mcc in [
        ("VIX Conservative",
         [14.0, 20.0, 30.0], [2, None, 2, 1],
         [None, None, 200.0, 300.0], [None, None, None, None],
         [None, None, None, None], [None, None, None, None]),
        ("VIX Tight LowVIX",
         [14.0, 20.0, 30.0], [None, None, None, None],
         [None, None, 200.0, 250.0], [None, None, None, None],
         [2.50, None, None, None], [1.75, None, None, None]),
        ("VIX 2e@VIX>25",
         [25.0], [None, 2],
         [None, None], [None, None],
         [None, None], [None, None]),
    ]:
        cfg = deepcopy(base_cfg)
        cfg.vix_regime_enabled = True
        cfg.vix_regime_breakpoints = bp
        cfg.vix_regime_max_entries = max_e
        cfg.vix_regime_put_stop_buffer = psb
        cfg.vix_regime_call_stop_buffer = csb
        cfg.vix_regime_min_put_credit = mpc
        cfg.vix_regime_min_call_credit = mcc
        configs.append((label, cfg))

    # 2-way combos
    for skip_days, skip_label in [([2], "Skip Wed"), ([3], "Skip Thu"), ([2, 3], "Skip Wed+Thu")]:
        # + VIX Conservative
        cfg = deepcopy(base_cfg); cfg.skip_weekdays = skip_days
        cfg.vix_regime_enabled = True
        cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
        cfg.vix_regime_max_entries = [2, None, 2, 1]
        cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
        configs.append((f"{skip_label} + VIX Conserv", cfg))

        # + Fri=2e
        cfg = deepcopy(base_cfg); cfg.skip_weekdays = skip_days; cfg.dow_max_entries = {4: 2}
        configs.append((f"{skip_label} + Fri=2e", cfg))

    # 3-way combos
    for skip_days, skip_label in [([2], "Skip Wed"), ([3], "Skip Thu"), ([2, 3], "Skip Wed+Thu")]:
        cfg = deepcopy(base_cfg); cfg.skip_weekdays = skip_days; cfg.dow_max_entries = {4: 2}
        cfg.vix_regime_enabled = True
        cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
        cfg.vix_regime_max_entries = [2, None, 2, 1]
        cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
        configs.append((f"{skip_label} + VIX + Fri=2e", cfg))

    # Run all
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    all_results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one_no_results, tasks):
            all_results.append(res)
            idx, label, m = res
            log(f"  [{len(all_results)}/{len(tasks)}] {label:35s} Sharpe {m['sharpe']:.3f}  "
                f"P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}")

    all_results.sort(key=lambda x: x[0])
    baseline_sharpe = all_results[0][2]["sharpe"]

    log(f"\n── Phase 2 results (sorted by Sharpe) ──")
    by_sharpe = sorted(all_results, key=lambda x: x[2]["sharpe"], reverse=True)
    for idx, label, m in by_sharpe:
        delta = m["sharpe"] - baseline_sharpe
        log(f"  {label:35s} Sharpe {m['sharpe']:.3f} ({delta:+.3f})  "
            f"P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}  Win {m['win_rate']:.1f}%")

    # Return best config
    best_idx, best_label, best_m = by_sharpe[0]
    best_cfg = configs[best_idx][1]
    log(f"\n  ★ BEST: {best_label}  Sharpe {best_m['sharpe']:.3f}")

    return best_label, best_cfg, best_m


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3: CONTRACT SCALING
# ═══════════════════════════════════════════════════════════════════════════

def phase3_contracts(base_cfg: BacktestConfig, best_cfg: BacktestConfig,
                     best_label: str) -> None:
    log(f"\n{'═'*80}")
    log(f"PHASE 3: CONTRACT SCALING (1c vs 2c)")
    log(f"{'═'*80}\n")

    configs = []

    # Baseline converged at 1c
    cfg = deepcopy(base_cfg); cfg.contracts = 1
    configs.append(("Baseline 1c", cfg))

    # Baseline at 2c
    cfg = deepcopy(base_cfg); cfg.contracts = 2
    configs.append(("Baseline 2c", cfg))

    # Best combo at 1c
    cfg = deepcopy(best_cfg); cfg.contracts = 1
    configs.append((f"{best_label} 1c", cfg))

    # Best combo at 2c
    cfg = deepcopy(best_cfg); cfg.contracts = 2
    configs.append((f"{best_label} 2c", cfg))

    # Also test Skip Wed+Thu only (simpler) at 2c
    cfg = deepcopy(base_cfg); cfg.skip_weekdays = [2, 3]; cfg.contracts = 2
    configs.append(("Skip Wed+Thu 2c", cfg))

    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one_no_results, tasks):
            results.append(res)

    results.sort(key=lambda x: x[0])
    log(f"  {'Config':<35s}  {'Days':>4s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Win%':>5s}")
    log(f"  {'─'*80}")
    for idx, label, m in results:
        log(f"  {label:<35s}  {m['days']:>4d}  ${m['net_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
            f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4: OVERFITTING VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def phase4_validation(base_cfg: BacktestConfig, best_cfg: BacktestConfig,
                      best_label: str) -> None:
    log(f"\n{'═'*80}")
    log(f"PHASE 4: OVERFITTING VALIDATION")
    log(f"{'═'*80}")

    # ── 4A: Year-by-year stability ────────────────────────────────────
    log(f"\n── 4A: Year-by-Year Stability ──")
    log(f"Does the best config work every year, or just 1-2 good years?\n")

    year_ranges = [
        (date(2022, 5, 16), date(2022, 12, 31), "2022 (partial)"),
        (date(2023, 1, 1), date(2023, 12, 31), "2023"),
        (date(2024, 1, 1), date(2024, 12, 31), "2024"),
        (date(2025, 1, 1), date(2025, 12, 31), "2025"),
        (date(2026, 1, 1), date(2026, 3, 27), "2026 (partial)"),
    ]

    tasks = []
    i = 0
    for start, end, yr_label in year_ranges:
        # Baseline for this year
        cfg = deepcopy(base_cfg); cfg.start_date = start; cfg.end_date = end
        tasks.append((i, f"Baseline {yr_label}", cfg)); i += 1
        # Best config for this year
        cfg = deepcopy(best_cfg); cfg.start_date = start; cfg.end_date = end
        tasks.append((i, f"Best {yr_label}", cfg)); i += 1

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one_no_results, tasks):
            results.append(res)
    results.sort(key=lambda x: x[0])

    log(f"  {'Year':<20s}  {'Baseline Sharpe':>15s}  {'Best Sharpe':>12s}  {'Δ':>7s}  "
        f"{'Base P&L':>9s}  {'Best P&L':>9s}")
    log(f"  {'─'*80}")
    for j in range(0, len(results), 2):
        _, bl, bm = results[j]
        _, xl, xm = results[j + 1]
        yr = bl.replace("Baseline ", "")
        delta = xm["sharpe"] - bm["sharpe"]
        marker = "✓" if delta > 0 else "✗"
        log(f"  {yr:<20s}  {bm['sharpe']:>15.3f}  {xm['sharpe']:>12.3f}  {delta:>+7.3f} {marker}  "
            f"${bm['net_pnl']:>+8,.0f}  ${xm['net_pnl']:>+8,.0f}")

    # ── 4B: Half-sample cross-validation ──────────────────────────────
    log(f"\n── 4B: Half-Sample Cross-Validation ──")
    log(f"Optimize on one half, test on the other. Genuine OOS test.\n")

    midpoint = date(2024, 6, 30)
    halves = [
        ("First half (train)", START_DATE, midpoint,
         "Second half (OOS)", date(2024, 7, 1), END_DATE),
        ("Second half (train)", date(2024, 7, 1), END_DATE,
         "First half (OOS)", START_DATE, midpoint),
    ]

    for train_label, train_start, train_end, test_label, test_start, test_end in halves:
        log(f"  {train_label}: {train_start} → {train_end}")
        log(f"  {test_label}: {test_start} → {test_end}")

        # Test the FULL-SAMPLE optimized config on each half (not re-optimizing)
        tasks = []
        for j, (label, cfg_template) in enumerate([
            ("Baseline", base_cfg), (best_label, best_cfg)
        ]):
            # Train period
            cfg = deepcopy(cfg_template); cfg.start_date = train_start; cfg.end_date = train_end
            tasks.append((j * 2, f"{label} [train]", cfg))
            # Test period
            cfg = deepcopy(cfg_template); cfg.start_date = test_start; cfg.end_date = test_end
            tasks.append((j * 2 + 1, f"{label} [OOS]", cfg))

        results = []
        with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
            for res in pool.imap_unordered(_run_one_no_results, tasks):
                results.append(res)
        results.sort(key=lambda x: x[0])

        for idx, label, m in results:
            log(f"    {label:35s} Sharpe {m['sharpe']:.3f}  P&L ${m['net_pnl']:+>8,.0f}  "
                f"MaxDD ${m['max_dd']:>7,.0f}  {m['days']} days")
        log("")

    # ── 4C: Day-of-week consistency per year ──────────────────────────
    log(f"── 4C: Wednesday/Thursday P&L per Year ──")
    log(f"Is the day-of-week pattern consistent or driven by 1-2 extreme years?\n")

    cfg_full = deepcopy(base_cfg)
    r_full = run_backtest(cfg_full, verbose=False)

    dow_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
    years = sorted(set(r.date.year for r in r_full))

    log(f"  {'Year':<6s}  " + "  ".join(f"{dow_names[d]:>8s}" for d in range(5)))
    log(f"  {'─'*56}")
    for yr in years:
        row = f"  {yr:<6d}  "
        for dow in range(5):
            pnls = [r.net_pnl for r in r_full if r.date.weekday() == dow and r.date.year == yr]
            avg = statistics.mean(pnls) if pnls else 0
            row += f"${avg:>+7.0f}  "
        log(row)

    # Totals
    row = f"  {'TOTAL':<6s}  "
    for dow in range(5):
        pnls = [r.net_pnl for r in r_full if r.date.weekday() == dow]
        avg = statistics.mean(pnls) if pnls else 0
        row += f"${avg:>+7.0f}  "
    log(row)

    # ── 4D: Bootstrap confidence intervals ────────────────────────────
    log(f"\n── 4D: Bootstrap Confidence Intervals ──")
    log(f"Resample daily P&L 10,000 times to estimate Sharpe distribution.\n")

    for label, cfg_template in [("Baseline", base_cfg), (best_label, best_cfg)]:
        cfg = deepcopy(cfg_template)
        results = run_backtest(cfg, verbose=False)
        daily_pnls = [r.net_pnl for r in results]
        n = len(daily_pnls)

        if n < 2:
            log(f"  {label}: Insufficient data ({n} days), skipping bootstrap")
            continue

        random.seed(42)
        bootstrap_sharpes = []
        for _ in range(10000):
            sample = random.choices(daily_pnls, k=n)
            mean = statistics.mean(sample)
            std = statistics.stdev(sample) if len(sample) > 1 else 0
            bs_sharpe = mean / std * (252 ** 0.5) if std > 0 else 0
            bootstrap_sharpes.append(bs_sharpe)

        bootstrap_sharpes.sort()
        p5 = bootstrap_sharpes[int(0.05 * len(bootstrap_sharpes))]
        p25 = bootstrap_sharpes[int(0.25 * len(bootstrap_sharpes))]
        p50 = bootstrap_sharpes[int(0.50 * len(bootstrap_sharpes))]
        p75 = bootstrap_sharpes[int(0.75 * len(bootstrap_sharpes))]
        p95 = bootstrap_sharpes[int(0.95 * len(bootstrap_sharpes))]

        std = statistics.stdev(daily_pnls)
        actual_sharpe = statistics.mean(daily_pnls) / std * 252**0.5 if std > 0 else 0

        log(f"  {label}:")
        log(f"    Actual Sharpe:  {actual_sharpe:.3f}")
        log(f"    Bootstrap:  5th={p5:.3f}  25th={p25:.3f}  50th={p50:.3f}  75th={p75:.3f}  95th={p95:.3f}")
        log(f"    {'✓ Statistically significant (5th pctile > 0)' if p5 > 0 else '✗ NOT significant (5th pctile ≤ 0)'}")
        log("")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def main():
    pipeline_start = time.time()
    log(f"{'═'*80}")
    log(f"HYDRA OVERNIGHT OPTIMIZATION PIPELINE")
    log(f"Started: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Period: {START_DATE} → {END_DATE} | 1-min | Real Greeks | {N_WORKERS} workers")
    log(f"Log: {LOG_FILE}")
    log(f"{'═'*80}")

    # Phase 1: Re-convergence
    converged_cfg = phase1_reconverge()

    # Phase 2: New features
    best_label, best_cfg, best_m = phase2_features(converged_cfg)

    # Phase 3: Contract scaling
    phase3_contracts(converged_cfg, best_cfg, best_label)

    # Phase 4: Overfitting validation
    phase4_validation(converged_cfg, best_cfg, best_label)

    # ── Final summary ─────────────────────────────────────────────────
    elapsed = time.time() - pipeline_start
    log(f"\n{'═'*80}")
    log(f"PIPELINE COMPLETE — {elapsed/3600:.1f} hours")
    log(f"{'═'*80}")

    log(f"\n── RECOMMENDED DEPLOYMENT CONFIG ──")
    log(f"Best config: {best_label}")
    log(f"  Sharpe {best_m['sharpe']:.3f}  P&L ${best_m['net_pnl']:+,.0f}  MaxDD ${best_m['max_dd']:,.0f}")

    log(f"\n── Converged parameters ──")
    for attr in ["spread_vix_multiplier", "max_spread_width", "min_call_credit",
                 "min_put_credit", "call_credit_floor", "put_credit_floor",
                 "call_stop_buffer", "put_stop_buffer", "downday_theoretical_put_credit",
                 "base_entry_downday_callonly_pct", "conditional_upday_e6_enabled",
                 "upday_threshold_pct", "whipsaw_range_skip_mult"]:
        log(f"  {attr:36s} = {getattr(converged_cfg, attr)}")

    log(f"\n── Feature flags ({best_label}) ──")
    log(f"  skip_weekdays                      = {getattr(best_cfg, 'skip_weekdays', [])}")
    log(f"  dow_max_entries                     = {getattr(best_cfg, 'dow_max_entries', {})}")
    log(f"  vix_regime_enabled                  = {getattr(best_cfg, 'vix_regime_enabled', False)}")
    if getattr(best_cfg, 'vix_regime_enabled', False):
        log(f"  vix_regime_breakpoints              = {best_cfg.vix_regime_breakpoints}")
        log(f"  vix_regime_max_entries               = {best_cfg.vix_regime_max_entries}")
        log(f"  vix_regime_put_stop_buffer           = {best_cfg.vix_regime_put_stop_buffer}")

    log(f"\nFull log saved to: {LOG_FILE}")
    log(f"Finished: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
