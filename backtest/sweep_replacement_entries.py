"""
Sweep: Replacement Entries — re-enter after stops with wider OTM strikes.

When a side gets stopped, place a new spread further OTM after a short delay.
Tests: extra OTM distance, max replacements/day, delay, and cutoff time.

Run: python -m backtest.sweep_replacement_entries
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
        return {"total_pnl": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0, "days": 0,
                "entries": 0, "stops": 0}
    mean = statistics.mean(daily)
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p; peak = max(peak, cum); max_dd = min(max_dd, cum - peak)
    wins = sum(1 for p in daily if p > 0)
    total_entries = sum(r.entries_placed for r in results)
    total_stops = sum(r.stops_hit for r in results)
    return {"total_pnl": sum(daily), "sharpe": sharpe, "max_dd": max_dd,
            "win_rate": wins / n * 100, "days": n,
            "entries": total_entries, "stops": total_stops}


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
    cfg.buffer_decay_start_mult = 2.10
    cfg.buffer_decay_hours = 2.0
    return cfg


def main():
    configs = []

    # Baseline (no replacements)
    configs.append(("No replacement", _base()))

    # ── Sweep: extra_otm × max_per_day × delay × cutoff ───────────────
    for extra_otm in [5, 10, 15, 20, 25]:
        for max_repl in [1, 2, 3]:
            for delay in [5, 10]:
                for cutoff in ["13:00", "14:00"]:
                    c = _base()
                    c.replacement_entry_enabled = True
                    c.replacement_entry_extra_otm = extra_otm
                    c.replacement_entry_max_per_day = max_repl
                    c.replacement_entry_delay_minutes = delay
                    c.replacement_entry_cutoff = cutoff
                    configs.append((
                        f"+{extra_otm}pt R{max_repl} D{delay}m {cutoff[:2]}",
                        c
                    ))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Replacement entry sweep: {n} configs, {N_WORKERS} workers\n", flush=True)
    hdr = (f"{'Strategy':<22s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
           f"{'Win%':>5s}  {'Entries':>7s}  {'Stops':>6s}")
    print(hdr)
    print("─" * len(hdr), flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            idx, label, m = res
            marker = " ◄" if "No rep" in label else ""
            print(f"  [{idx+1:3d}/{n}] {label:<20s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
                  f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
                  f"{m['entries']:>7,d}  {m['stops']:>6,d}{marker}", flush=True)
            results.append(res)

    results.sort(key=lambda x: x[2]["sharpe"], reverse=True)
    baseline = next((m for _, l, m in results if "No rep" in l), None)

    print()
    print("=" * 85)
    print("TOP 20 BY SHARPE")
    print("=" * 85)
    print(f"{'Strategy':<22s}  {'P&L':>10s}  {'Δ P&L':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
          f"{'Win%':>5s}  {'Entries':>7s}")
    print("─" * 85)

    for _, label, m in results[:20]:
        marker = " ◄ BASE" if "No rep" in label else ""
        delta = m["total_pnl"] - baseline["total_pnl"] if baseline else 0
        print(f"{label:<22s}  ${m['total_pnl']:>+9,.0f}  {delta:>+7,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
              f"{m['entries']:>7,d}{marker}", flush=True)


if __name__ == "__main__":
    main()
