"""
Sweep: Asymmetric Buffer Decay — different multiplier/hours for call vs put.

Puts have $1.55 buffer (4.4× larger than call $0.35), so they may need
different decay dynamics. Test call and put independently.

Run: python -m backtest.sweep_asymmetric_decay
"""
import multiprocessing as mp
import os
import statistics
from datetime import date

from backtest.config import live_config
from backtest.engine import run_backtest

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)
N_WORKERS = min(8, os.cpu_count() or 4)


def _metrics(results):
    daily = [r.net_pnl for r in results]
    n = len(daily)
    if n == 0:
        return {"total_pnl": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0, "days": 0}
    mean = statistics.mean(daily)
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p; peak = max(peak, cum); max_dd = min(max_dd, cum - peak)
    wins = sum(1 for p in daily if p > 0)
    return {"total_pnl": sum(daily), "sharpe": sharpe, "max_dd": max_dd,
            "win_rate": wins / n * 100, "days": n}


def _run_one(args):
    idx, label, cfg = args
    r = run_backtest(cfg, verbose=False)
    m = _metrics(r)
    return (idx, label, m)


def _base():
    cfg = live_config()
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [125.0, None, None, None]
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    cfg.vix_regime_min_call_credit = [None, 1.35, None, None]
    cfg.vix_regime_min_put_credit = [None, 2.10, None, None]
    cfg.calm_entry_lookback_min = 3
    cfg.calm_entry_threshold_pts = 15.0
    cfg.calm_entry_max_delay_min = 5
    return cfg


def main():
    configs = []

    # Baseline: symmetric x2.10 / 2.0h (current deployed)
    c = _base(); c.buffer_decay_start_mult = 2.10; c.buffer_decay_hours = 2.0
    configs.append(("Sym x2.10 2.0h", c))

    # No decay baseline
    configs.append(("No decay", _base()))

    # ── Test 1: Keep call at x2.10/2.0h, vary put ─────────────────────
    for put_mult in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
        for put_hours in [1.0, 2.0, 3.0, 4.0]:
            c = _base()
            c.buffer_decay_call_mult = 2.10
            c.buffer_decay_call_hours = 2.0
            c.buffer_decay_put_mult = put_mult
            c.buffer_decay_put_hours = put_hours
            configs.append((f"C2.1/2h P{put_mult:.1f}/{put_hours:.0f}h", c))

    # ── Test 2: Keep put at x2.10/2.0h, vary call ─────────────────────
    for call_mult in [1.25, 1.5, 2.0, 2.5, 3.0]:
        for call_hours in [0.5, 1.0, 1.5, 2.0]:
            c = _base()
            c.buffer_decay_call_mult = call_mult
            c.buffer_decay_call_hours = call_hours
            c.buffer_decay_put_mult = 2.10
            c.buffer_decay_put_hours = 2.0
            configs.append((f"C{call_mult:.1f}/{call_hours:.1f}h P2.1/2h", c))

    # ── Test 3: Best combos from intuition ─────────────────────────────
    combos = [
        ("C1.5/1h P3.0/3h", 1.5, 1.0, 3.0, 3.0),
        ("C2.0/1.5h P2.5/2.5h", 2.0, 1.5, 2.5, 2.5),
        ("C1.5/1h P2.5/3h", 1.5, 1.0, 2.5, 3.0),
        ("C2.0/2h P3.0/3h", 2.0, 2.0, 3.0, 3.0),
        ("C2.5/1.5h P2.0/2.5h", 2.5, 1.5, 2.0, 2.5),
        ("C1.25/0.5h P3.0/4h", 1.25, 0.5, 3.0, 4.0),
        ("C3.0/1h P1.5/3h", 3.0, 1.0, 1.5, 3.0),
    ]
    for label, cm, ch, pm, ph in combos:
        c = _base()
        c.buffer_decay_call_mult = cm; c.buffer_decay_call_hours = ch
        c.buffer_decay_put_mult = pm; c.buffer_decay_put_hours = ph
        configs.append((label, c))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Asymmetric decay sweep: {n} configs, {N_WORKERS} workers\n", flush=True)
    hdr = f"{'Strategy':<24s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Win%':>5s}"
    print(hdr)
    print("─" * len(hdr), flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            idx, label, m = res
            marker = " ◄" if "Sym" in label else ""
            print(f"  [{idx+1:3d}/{n}] {label:<22s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
                  f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%{marker}", flush=True)
            results.append(res)

    results.sort(key=lambda x: x[2]["sharpe"], reverse=True)
    sym = next((m for _, l, m in results if "Sym" in l), None)

    print()
    print("=" * 80)
    print("TOP 20 BY SHARPE")
    print("=" * 80)
    print(f"{'Strategy':<24s}  {'P&L':>10s}  {'Δ P&L':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Win%':>5s}")
    print("─" * 80)

    for _, label, m in results[:20]:
        marker = " ◄ SYM" if "Sym" in label else " ◄ NODECAY" if "No decay" in label else ""
        delta = m["total_pnl"] - sym["total_pnl"] if sym else 0
        print(f"{label:<24s}  ${m['total_pnl']:>+9,.0f}  {delta:>+7,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%{marker}", flush=True)


if __name__ == "__main__":
    main()
