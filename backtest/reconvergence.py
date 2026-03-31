"""
HYDRA Full Parameter Re-Convergence — 1-Minute Resolution

Re-sweeps all core parameters with max_spread_width=110 (corrected).
Repeats ALL 8 rounds in a loop until Sharpe stops improving (< 0.01 gain).

Order per pass (most impactful first):
  1. spread_vix_multiplier
  2. min_call_credit × min_put_credit (2D)
  3. call_credit_floor × put_credit_floor (2D)
  4. call_stop_buffer × put_stop_buffer (2D)
  5. downday_theoretical_put_credit
  6. base_entry_downday_callonly_pct
  7. upday_threshold_pct (E6 conditional)
  8. whipsaw_range_skip_mult

Run: python -m backtest.reconvergence
"""
import csv
import multiprocessing as mp
import os
import statistics
import time
from copy import copy
from datetime import date, datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backtest.config import BacktestConfig, live_config
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)
N_WORKERS = min(8, os.cpu_count() or 4)
CONVERGE_THRESHOLD = 0.01  # stop when Sharpe improves less than this between passes
MAX_PASSES = 5             # safety cap


def _metrics(results: List[DayResult]) -> Dict[str, Any]:
    daily = [r.net_pnl for r in results]
    n = len(daily)
    total = sum(daily)
    mean = statistics.mean(daily) if daily else 0
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
    return (idx, label, m)


def _sweep_param(base_cfg: BacktestConfig, param_name: str,
                 values: list, label_fn=None) -> Tuple[Any, Dict]:
    """Sweep a single parameter. Returns (best_value, best_metrics)."""
    if label_fn is None:
        label_fn = lambda v: str(v)

    tasks = []
    for i, val in enumerate(values):
        cfg = copy(base_cfg)
        setattr(cfg, param_name, val)
        tasks.append((i, f"{param_name}={label_fn(val)}", cfg))

    results = []
    with mp.Pool(N_WORKERS) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)
            idx, label, m = res
            print(f"    {label:45s} Sharpe {m['sharpe']:.3f}  P&L ${m['net_pnl']:+>9,.0f}  "
                  f"MaxDD ${m['max_dd']:>7,.0f}", flush=True)

    results.sort(key=lambda x: x[0])
    best_idx = max(range(len(results)), key=lambda i: results[i][2]["sharpe"])
    best_val = values[best_idx]
    best_m = results[best_idx][2]
    return best_val, best_m


def _sweep_2d(base_cfg: BacktestConfig, param_a: str, values_a: list,
              param_b: str, values_b: list,
              label_fn_a=None, label_fn_b=None) -> Tuple[Any, Any, Dict]:
    """Sweep two parameters jointly (2D grid). Returns (best_a, best_b, best_metrics)."""
    if label_fn_a is None:
        label_fn_a = lambda v: str(v)
    if label_fn_b is None:
        label_fn_b = lambda v: str(v)

    tasks = []
    grid = []
    i = 0
    for va in values_a:
        for vb in values_b:
            cfg = copy(base_cfg)
            setattr(cfg, param_a, va)
            setattr(cfg, param_b, vb)
            label = f"{param_a}={label_fn_a(va)} {param_b}={label_fn_b(vb)}"
            tasks.append((i, label, cfg))
            grid.append((va, vb))
            i += 1

    results = []
    with mp.Pool(N_WORKERS) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)
            idx, label, m = res
            print(f"    [{len(results)}/{len(tasks)}] {label:55s} Sharpe {m['sharpe']:.3f}  "
                  f"P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}", flush=True)

    results.sort(key=lambda x: x[0])
    best_idx = max(range(len(results)), key=lambda i: results[i][2]["sharpe"])
    best_a, best_b = grid[best_idx]
    best_m = results[best_idx][2]
    return best_a, best_b, best_m


def run_one_pass(cfg: BacktestConfig, pass_num: int) -> Tuple[BacktestConfig, float, List]:
    """Run all 8 rounds once. Returns (updated_cfg, final_sharpe, round_log)."""
    round_log = []
    round_num = 0

    # ── Round 1: spread_vix_multiplier ────────────────────────────────
    round_num += 1
    print(f"  ── R{round_num}: spread_vix_multiplier (current={cfg.spread_vix_multiplier}) ──", flush=True)
    values = [3.0, 3.5, 4.0, 4.5, 5.0, 5.3, 5.5, 6.0, 6.5, 7.0]
    best, m = _sweep_param(cfg, "spread_vix_multiplier", values, lambda v: f"{v:.1f}")
    print(f"  ★ R{round_num} BEST: {best:.1f}  Sharpe {m['sharpe']:.3f}\n", flush=True)
    cfg.spread_vix_multiplier = best
    round_log.append(("spread_vix_mult", best, m))

    # ── Round 2: min_call_credit × min_put_credit ─────────────────────
    round_num += 1
    print(f"  ── R{round_num}: credit gates (current call=${cfg.min_call_credit} put=${cfg.min_put_credit}) ──", flush=True)
    call_vals = [0.75, 1.00, 1.25, 1.35, 1.50, 1.75, 2.00]
    put_vals = [1.50, 1.75, 2.00, 2.10, 2.25, 2.50, 2.75]
    best_c, best_p, m = _sweep_2d(cfg, "min_call_credit", call_vals,
                                   "min_put_credit", put_vals,
                                   lambda v: f"${v:.2f}", lambda v: f"${v:.2f}")
    print(f"  ★ R{round_num} BEST: call=${best_c:.2f} put=${best_p:.2f}  Sharpe {m['sharpe']:.3f}\n", flush=True)
    cfg.min_call_credit = best_c
    cfg.min_put_credit = best_p
    round_log.append(("credit_gates", f"c={best_c} p={best_p}", m))

    # ── Round 3: call_credit_floor × put_credit_floor ─────────────────
    round_num += 1
    print(f"  ── R{round_num}: credit floors (current call=${cfg.call_credit_floor} put=${cfg.put_credit_floor}) ──", flush=True)
    cf_vals = [0.40, 0.50, 0.60, 0.75, 0.85, 1.00]
    pf_vals = [1.50, 1.75, 1.90, 2.00, 2.07, 2.15, 2.25]
    best_cf, best_pf, m = _sweep_2d(cfg, "call_credit_floor", cf_vals,
                                     "put_credit_floor", pf_vals,
                                     lambda v: f"${v:.2f}", lambda v: f"${v:.2f}")
    print(f"  ★ R{round_num} BEST: call_floor=${best_cf:.2f} put_floor=${best_pf:.2f}  Sharpe {m['sharpe']:.3f}\n", flush=True)
    cfg.call_credit_floor = best_cf
    cfg.put_credit_floor = best_pf
    round_log.append(("credit_floors", f"c={best_cf} p={best_pf}", m))

    # ── Round 4: call_stop_buffer × put_stop_buffer ───────────────────
    round_num += 1
    print(f"  ── R{round_num}: stop buffers (current call=${cfg.call_stop_buffer} put=${cfg.put_stop_buffer}) ──", flush=True)
    csb_vals = [10.0, 20.0, 35.0, 50.0, 75.0, 100.0]
    psb_vals = [100.0, 125.0, 155.0, 200.0, 250.0, 300.0, 400.0, 500.0]
    best_csb, best_psb, m = _sweep_2d(cfg, "call_stop_buffer", csb_vals,
                                       "put_stop_buffer", psb_vals,
                                       lambda v: f"${v/100:.2f}", lambda v: f"${v/100:.2f}")
    print(f"  ★ R{round_num} BEST: call=${best_csb/100:.2f} put=${best_psb/100:.2f}  Sharpe {m['sharpe']:.3f}\n", flush=True)
    cfg.call_stop_buffer = best_csb
    cfg.put_stop_buffer = best_psb
    round_log.append(("stop_buffers", f"c={best_csb} p={best_psb}", m))

    # ── Round 5: downday_theoretical_put_credit ───────────────────────
    round_num += 1
    print(f"  ── R{round_num}: theo put credit (current=${cfg.downday_theoretical_put_credit/100:.2f}) ──", flush=True)
    values = [100.0, 150.0, 175.0, 200.0, 225.0, 250.0, 260.0, 275.0, 300.0, 350.0]
    best, m = _sweep_param(cfg, "downday_theoretical_put_credit", values,
                           lambda v: f"${v/100:.2f}")
    print(f"  ★ R{round_num} BEST: ${best/100:.2f}  Sharpe {m['sharpe']:.3f}\n", flush=True)
    cfg.downday_theoretical_put_credit = best
    round_log.append(("theo_put", best, m))

    # ── Round 6: base_entry_downday_callonly_pct ──────────────────────
    round_num += 1
    cur = cfg.base_entry_downday_callonly_pct
    print(f"  ── R{round_num}: downday call-only pct (current={'OFF' if cur is None else f'{cur:.2f}%'}) ──", flush=True)
    values = [None, 0.20, 0.30, 0.40, 0.50, 0.57, 0.60, 0.70, 0.80, 1.00]
    best, m = _sweep_param(cfg, "base_entry_downday_callonly_pct", values,
                           lambda v: "OFF" if v is None else f"{v:.2f}%")
    print(f"  ★ R{round_num} BEST: {'OFF' if best is None else f'{best:.2f}%'}  Sharpe {m['sharpe']:.3f}\n", flush=True)
    cfg.base_entry_downday_callonly_pct = best
    round_log.append(("downday_pct", best, m))

    # ── Round 7: upday E6 threshold ───────────────────────────────────
    round_num += 1
    print(f"  ── R{round_num}: upday E6 (current={'ON' if cfg.conditional_upday_e6_enabled else 'OFF'} "
          f"thr={cfg.upday_threshold_pct}%) ──", flush=True)
    tasks = []
    vals_map = {}
    i = 0
    c = copy(cfg); c.conditional_upday_e6_enabled = False
    tasks.append((i, "E6=OFF", c)); vals_map[i] = (False, 0); i += 1
    for thr in [0.20, 0.30, 0.40, 0.48, 0.50, 0.60, 0.70, 0.80, 1.00]:
        c = copy(cfg); c.conditional_upday_e6_enabled = True; c.upday_threshold_pct = thr
        tasks.append((i, f"E6=ON thr={thr:.2f}%", c)); vals_map[i] = (True, thr); i += 1

    results = []
    with mp.Pool(N_WORKERS) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)
            idx, label, m_r = res
            print(f"    {label:45s} Sharpe {m_r['sharpe']:.3f}  P&L ${m_r['net_pnl']:+>9,.0f}  "
                  f"MaxDD ${m_r['max_dd']:>7,.0f}", flush=True)

    results.sort(key=lambda x: x[0])
    best_idx = max(range(len(results)), key=lambda i_r: results[i_r][2]["sharpe"])
    best_e6, best_thr = vals_map[best_idx]
    m = results[best_idx][2]
    print(f"  ★ R{round_num} BEST: E6={'ON' if best_e6 else 'OFF'}"
          f"{f' thr={best_thr:.2f}%' if best_e6 else ''}  Sharpe {m['sharpe']:.3f}\n", flush=True)
    cfg.conditional_upday_e6_enabled = best_e6
    if best_e6:
        cfg.upday_threshold_pct = best_thr
    round_log.append(("upday_e6", f"{'ON' if best_e6 else 'OFF'} thr={best_thr}", m))

    # ── Round 8: whipsaw_range_skip_mult ──────────────────────────────
    round_num += 1
    print(f"  ── R{round_num}: whipsaw (current={cfg.whipsaw_range_skip_mult}) ──", flush=True)
    values = [None, 1.00, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00]
    best, m = _sweep_param(cfg, "whipsaw_range_skip_mult", values,
                           lambda v: "OFF" if v is None else f"{v:.2f}×")
    print(f"  ★ R{round_num} BEST: {'OFF' if best is None else f'{best:.2f}×'}  Sharpe {m['sharpe']:.3f}\n", flush=True)
    cfg.whipsaw_range_skip_mult = best
    round_log.append(("whipsaw", best, m))

    final_sharpe = m["sharpe"]
    return cfg, final_sharpe, round_log


def main():
    start_time = time.time()
    print(f"{'='*80}")
    print(f"HYDRA FULL PARAMETER RE-CONVERGENCE (with convergence loop)")
    print(f"max_spread_width=110 (corrected) | 1-min | Real Greeks")
    print(f"Period: {START_DATE} → {END_DATE} | {N_WORKERS} workers")
    print(f"Convergence threshold: {CONVERGE_THRESHOLD} Sharpe | Max passes: {MAX_PASSES}")
    print(f"{'='*80}\n")

    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"

    prev_sharpe = 0.0
    all_pass_logs = []

    for pass_num in range(1, MAX_PASSES + 1):
        print(f"\n{'#'*80}")
        print(f"#  PASS {pass_num} / {MAX_PASSES}")
        print(f"{'#'*80}\n")

        cfg, sharpe, round_log = run_one_pass(cfg, pass_num)
        all_pass_logs.append((pass_num, sharpe, round_log))

        improvement = sharpe - prev_sharpe
        print(f"  Pass {pass_num} result: Sharpe {sharpe:.3f} (improvement: {improvement:+.3f})")

        if pass_num > 1 and improvement < CONVERGE_THRESHOLD:
            print(f"\n  ✓ CONVERGED — improvement {improvement:.4f} < threshold {CONVERGE_THRESHOLD}")
            break

        prev_sharpe = sharpe

    elapsed = time.time() - start_time

    # ── Final summary ─────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"RE-CONVERGENCE COMPLETE — {len(all_pass_logs)} passes in {elapsed/60:.1f} min")
    print(f"{'='*80}")

    print(f"\n── Pass history ──")
    for p, s, _ in all_pass_logs:
        delta = s - all_pass_logs[0][1] if p > 1 else 0
        print(f"  Pass {p}: Sharpe {s:.3f}{f'  ({delta:+.3f} vs pass 1)' if p > 1 else ''}")

    print(f"\n── Final converged config ──")
    print(f"  spread_vix_multiplier        = {cfg.spread_vix_multiplier}")
    print(f"  max_spread_width             = {cfg.max_spread_width}")
    print(f"  min_call_credit              = {cfg.min_call_credit}")
    print(f"  min_put_credit               = {cfg.min_put_credit}")
    print(f"  call_credit_floor            = {cfg.call_credit_floor}")
    print(f"  put_credit_floor             = {cfg.put_credit_floor}")
    print(f"  call_stop_buffer             = {cfg.call_stop_buffer}")
    print(f"  put_stop_buffer              = {cfg.put_stop_buffer}")
    print(f"  downday_theoretical_put_credit = {cfg.downday_theoretical_put_credit}")
    print(f"  base_entry_downday_callonly_pct = {cfg.base_entry_downday_callonly_pct}")
    print(f"  conditional_upday_e6_enabled   = {cfg.conditional_upday_e6_enabled}")
    print(f"  upday_threshold_pct            = {cfg.upday_threshold_pct}")
    print(f"  whipsaw_range_skip_mult        = {cfg.whipsaw_range_skip_mult}")

    # ── Last pass detail ──────────────────────────────────────────────
    last_pass_num, last_sharpe, last_log = all_pass_logs[-1]
    print(f"\n── Last pass (#{last_pass_num}) round-by-round ──")
    for param, val, m in last_log:
        print(f"  {param:30s}: {str(val):20s}  Sharpe {m['sharpe']:.3f}  P&L ${m['net_pnl']:+,.0f}  MaxDD ${m['max_dd']:,.0f}")

    # ── Save CSV ──────────────────────────────────────────────────────
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path("backtest/results") / f"reconvergence_1min_{ts}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["pass", "round", "parameter", "best_value", "sharpe",
                         "sortino", "net_pnl", "max_dd", "win_rate"])
        for pass_num, _, round_log in all_pass_logs:
            for r_idx, (param, val, m) in enumerate(round_log, 1):
                writer.writerow([pass_num, r_idx, param, val,
                                 f"{m['sharpe']:.4f}", f"{m['sortino']:.4f}",
                                 f"{m['net_pnl']:.2f}", f"{m['max_dd']:.2f}",
                                 f"{m['win_rate']:.2f}"])
    print(f"\nSaved to {csv_path}")


if __name__ == "__main__":
    main()
