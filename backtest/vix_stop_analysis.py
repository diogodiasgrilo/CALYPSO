"""
VIX-conditional stop analysis.

Runs credit-based and N=0.3 price-based stops over the full period, then:
  1. Buckets results by VIX level to show which stop works better at each VIX range
  2. Simulates hybrid strategies: switch stop mechanism at a VIX threshold
     - "Low-VIX credit":  credit-based when VIX < T,  N=0.3 when VIX >= T
     - "Low-VIX N=0.3":   N=0.3  when VIX < T,  credit-based when VIX >= T
  3. Sweeps thresholds T in VIX_THRESHOLDS to find the best crossover

Run: python -m backtest.vix_stop_analysis
"""
from datetime import date
from typing import List, Optional
import statistics

from backtest.config import BacktestConfig
from backtest.engine import run_backtest, DayResult

try:
    from rich.console import Console
    from rich.progress import (BarColumn, MofNCompleteColumn, Progress,
                               SpinnerColumn, TextColumn, TimeElapsedColumn,
                               TimeRemainingColumn)
    from rich.table import Table
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False

# ── Config ────────────────────────────────────────────────────────────────────

START_DATE   = date(2022, 5, 16)
END_DATE     = date(2026, 3, 22)

# VIX buckets for breakdown analysis
VIX_BUCKETS = [
    ("VIX < 15",    0,    15),
    ("VIX 15–18",  15,    18),
    ("VIX 18–22",  18,    22),
    ("VIX 22–27",  22,    27),
    ("VIX 27+",    27, 9999),
]

# Threshold sweep for hybrid strategy (credit below T, N=0.3 above T)
VIX_THRESHOLDS = [15, 17, 18, 19, 20, 21, 22, 23, 25]

PRICE_STOP_N = 0.3


# ── Base config ───────────────────────────────────────────────────────────────

def base_cfg(start: date, end: date, price_stop: Optional[float] = None) -> BacktestConfig:
    cfg = BacktestConfig(
        start_date=start,
        end_date=end,
        entry_times=["10:15", "10:45", "11:15", "11:45", "12:15"],
        conditional_e6_enabled=False,
        conditional_e7_enabled=True,
        conditional_upday_e6_enabled=True,
        conditional_upday_e7_enabled=False,
        downday_threshold_pct=0.30,
        upday_threshold_pct=0.60,
        base_entry_downday_callonly_pct=0.30,
        downday_theoretical_put_credit=175.0,
        upday_theoretical_call_credit=0,
        fomc_t1_callonly_enabled=False,
        min_call_credit=1.25,
        min_put_credit=1.75,
        put_stop_buffer=100.0,
        stop_buffer=10.0,
        one_sided_entries_enabled=True,
        put_only_max_vix=25.0,
        stop_slippage_per_leg=0.0,
        target_delta=8.0,
    )
    cfg.price_based_stop_points = price_stop
    cfg.price_stop_inward = True
    return cfg


# ── Helpers ───────────────────────────────────────────────────────────────────

def day_vix(day: DayResult) -> float:
    """Return VIX at open for a day (first non-skipped entry)."""
    for e in day.entries:
        if e.entry_type != "skipped" and e.vix_at_entry > 0:
            return e.vix_at_entry
    # fallback: any entry with VIX recorded
    for e in day.entries:
        if e.vix_at_entry > 0:
            return e.vix_at_entry
    return 0.0


def stats(pnls: List[float], label: str) -> dict:
    if not pnls:
        return {"label": label, "days": 0, "total": 0, "mean": 0,
                "stdev": 0, "sharpe": 0, "wr": 0, "max_dd": 0, "calmar": 0}
    total = sum(pnls)
    days = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    mean = statistics.mean(pnls)
    stdev = statistics.stdev(pnls) if days > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0
    # max drawdown
    peak = cum = max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    calmar = mean * 252 / max_dd if max_dd > 0 else 0
    return {
        "label": label, "days": days, "total": total,
        "mean": mean, "stdev": stdev, "sharpe": sharpe,
        "wr": wins / days * 100, "max_dd": max_dd, "calmar": calmar,
    }


def fmt_pnl(v: float) -> str:
    return f"${v:,.0f}"

def fmt_pct(v: float) -> str:
    return f"{v:.1f}%"

def fmt_sh(v: float) -> str:
    return f"{v:.3f}"


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import csv
    from datetime import datetime as dt
    from pathlib import Path

    console = Console() if _RICH else None

    if _RICH:
        console.print(f"\n[bold cyan]VIX-Conditional Stop Analysis[/]")
        console.print(f"Period: {START_DATE} → {END_DATE}  |  Price-stop N={PRICE_STOP_N}")
        console.print(f"VIX thresholds tested: {VIX_THRESHOLDS}\n")

    # ── Step 1: run both backtests ────────────────────────────────────────────
    if _RICH:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[yellow]{task.percentage:.0f}%"),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TextColumn("[magenta]ETA"),
            TimeRemainingColumn(),
            console=console,
            refresh_per_second=4,
        ) as progress:
            task = progress.add_task("Running backtests", total=2)

            progress.update(task, description="Credit-based stop...")
            credit_results = run_backtest(base_cfg(START_DATE, END_DATE, None), verbose=False)
            progress.advance(task)

            progress.update(task, description=f"N={PRICE_STOP_N} price-based stop...")
            price_results = run_backtest(base_cfg(START_DATE, END_DATE, PRICE_STOP_N), verbose=False)
            progress.advance(task)
    else:
        print("Running credit-based...")
        credit_results = run_backtest(base_cfg(START_DATE, END_DATE, None), verbose=False)
        print(f"Running N={PRICE_STOP_N}...")
        price_results  = run_backtest(base_cfg(START_DATE, END_DATE, PRICE_STOP_N), verbose=False)

    # Build day-indexed dicts keyed by date
    credit_map = {r.date: r for r in credit_results}
    price_map  = {r.date: r for r in price_results}
    all_dates  = sorted(set(credit_map) & set(price_map))

    # Per-day (date, vix, credit_pnl, price_pnl)
    days_data = []
    for d in all_dates:
        vix = day_vix(credit_map[d]) or day_vix(price_map[d])
        if vix <= 0:
            continue
        days_data.append({
            "date":        d,
            "vix":         vix,
            "credit_pnl":  credit_map[d].net_pnl,
            "price_pnl":   price_map[d].net_pnl,
        })

    # ── Step 2: overall comparison ────────────────────────────────────────────
    all_credit = [d["credit_pnl"] for d in days_data]
    all_price  = [d["price_pnl"]  for d in days_data]
    s_credit = stats(all_credit, "Credit-based")
    s_price  = stats(all_price,  f"N={PRICE_STOP_N}")

    if _RICH:
        tbl = Table(title="Overall Comparison", box=box.SIMPLE_HEAVY,
                    show_header=True, header_style="bold yellow")
        tbl.add_column("Metric",  style="cyan", width=20)
        tbl.add_column("Credit-based", justify="right", width=14)
        tbl.add_column(f"N={PRICE_STOP_N}",   justify="right", width=14)
        rows = [
            ("Total P&L",    fmt_pnl(s_credit["total"]),  fmt_pnl(s_price["total"])),
            ("Win rate",     fmt_pct(s_credit["wr"]),      fmt_pct(s_price["wr"])),
            ("Sharpe",       fmt_sh(s_credit["sharpe"]),   fmt_sh(s_price["sharpe"])),
            ("Max DD",       fmt_pnl(s_credit["max_dd"]),  fmt_pnl(s_price["max_dd"])),
            ("Calmar",       fmt_sh(s_credit["calmar"]),   fmt_sh(s_price["calmar"])),
            ("Days",         str(s_credit["days"]),         str(s_price["days"])),
        ]
        for metric, cv, pv in rows:
            tbl.add_row(metric, cv, pv)
        console.print()
        console.print(tbl)
    else:
        print(f"\n{'Metric':<20} {'Credit':>14} {f'N={PRICE_STOP_N}':>14}")
        print("─" * 50)
        for s, lbl in [(s_credit, "Credit"), (s_price, f"N={PRICE_STOP_N}")]:
            print(f"  {lbl}: P&L={fmt_pnl(s['total'])}  Sharpe={fmt_sh(s['sharpe'])}  MaxDD={fmt_pnl(s['max_dd'])}")

    # ── Step 3: VIX bucket breakdown ─────────────────────────────────────────
    if _RICH:
        tbl2 = Table(title="Performance by VIX Bucket", box=box.SIMPLE_HEAVY,
                     show_header=True, header_style="bold yellow")
        tbl2.add_column("VIX Range",   style="cyan", width=12)
        tbl2.add_column("Days",        justify="right", width=5)
        tbl2.add_column("Avg VIX",     justify="right", width=8)
        for lbl in ["Credit P&L", "N=0.3 P&L", "Cr Sharpe", "Pr Sharpe", "Winner"]:
            tbl2.add_column(lbl, justify="right", width=11)
        console.print()

    bucket_rows = []
    for bucket_name, lo, hi in VIX_BUCKETS:
        bucket = [d for d in days_data if lo <= d["vix"] < hi]
        if not bucket:
            continue
        cp = [d["credit_pnl"] for d in bucket]
        pp = [d["price_pnl"]  for d in bucket]
        sc = stats(cp, "credit")
        sp = stats(pp, "price")
        avg_vix = statistics.mean(d["vix"] for d in bucket)
        winner = "[bold green]Credit[/]" if sc["sharpe"] > sp["sharpe"] else f"[bold green]N={PRICE_STOP_N}[/]"
        winner_plain = "Credit" if sc["sharpe"] > sp["sharpe"] else f"N={PRICE_STOP_N}"
        bucket_rows.append((bucket_name, len(bucket), avg_vix, sc, sp, winner, winner_plain))

        if _RICH:
            tbl2.add_row(
                bucket_name,
                str(len(bucket)),
                f"{avg_vix:.1f}",
                fmt_pnl(sc["total"]),
                fmt_pnl(sp["total"]),
                fmt_sh(sc["sharpe"]),
                fmt_sh(sp["sharpe"]),
                winner,
            )
        else:
            print(f"  {bucket_name:<12} {len(bucket):>4}d  credit={fmt_pnl(sc['total'])} Sh={fmt_sh(sc['sharpe'])}"
                  f"  N=0.3={fmt_pnl(sp['total'])} Sh={fmt_sh(sp['sharpe'])}  → {winner_plain}")

    if _RICH:
        console.print(tbl2)

    # ── Step 4: hybrid threshold sweep ───────────────────────────────────────
    # Strategy A: credit when VIX < T, N=0.3 when VIX >= T  (credit = low-vol)
    # Strategy B: N=0.3  when VIX < T, credit  when VIX >= T (N=0.3 = low-vol)
    if _RICH:
        tbl3 = Table(title=f"Hybrid Strategy Sweep  (credit ↔ N={PRICE_STOP_N} switched at VIX threshold)",
                     box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
        tbl3.add_column("Threshold", style="cyan", width=11)
        for lbl in ["Credit<T / N≥T", "", "N<T / Credit≥T", "", "Pure Credit", "Pure N=0.3"]:
            tbl3.add_column(lbl, justify="right", width=10 if lbl else 1)
        tbl3.add_column("Best", justify="left", width=14)
        console.print()

    hybrid_rows = []
    for T in VIX_THRESHOLDS:
        # Strategy A: credit below T, N=0.3 above T
        pnls_a = [d["credit_pnl"] if d["vix"] < T else d["price_pnl"] for d in days_data]
        # Strategy B: N=0.3 below T, credit above T
        pnls_b = [d["price_pnl"]  if d["vix"] < T else d["credit_pnl"] for d in days_data]

        sa = stats(pnls_a, f"Cr<{T}/Pr≥{T}")
        sb = stats(pnls_b, f"Pr<{T}/Cr≥{T}")

        # find best among A, B, pure credit, pure N=0.3 by Sharpe
        candidates = [
            (sa["sharpe"], sa["total"], f"Cr<{T} / N≥{T}"),
            (sb["sharpe"], sb["total"], f"N<{T} / Cr≥{T}"),
            (s_credit["sharpe"], s_credit["total"], "Pure credit"),
            (s_price["sharpe"],  s_price["total"],  f"Pure N={PRICE_STOP_N}"),
        ]
        best_sh, best_pnl, best_label = max(candidates, key=lambda x: x[0])

        hybrid_rows.append((T, sa, sb, best_label, best_sh))

        if _RICH:
            is_a_winner = best_label.startswith("Cr<")
            is_b_winner = best_label.startswith("N<")
            tbl3.add_row(
                f"VIX = {T}",
                f"[bold green]{fmt_pnl(sa['total'])}[/]" if is_a_winner else fmt_pnl(sa["total"]),
                f"[bold green]{fmt_sh(sa['sharpe'])}[/]" if is_a_winner else fmt_sh(sa["sharpe"]),
                f"[bold green]{fmt_pnl(sb['total'])}[/]" if is_b_winner else fmt_pnl(sb["total"]),
                f"[bold green]{fmt_sh(sb['sharpe'])}[/]" if is_b_winner else fmt_sh(sb["sharpe"]),
                fmt_sh(s_credit["sharpe"]),
                fmt_sh(s_price["sharpe"]),
                f"[bold green]{best_label}[/]" if "Cr<" in best_label or "N<" in best_label else best_label,
            )
        else:
            print(f"  T={T:2d}  A(Cr<T/Pr≥T): Sh={fmt_sh(sa['sharpe'])} P&L={fmt_pnl(sa['total'])}"
                  f"  B(Pr<T/Cr≥T): Sh={fmt_sh(sb['sharpe'])} P&L={fmt_pnl(sb['total'])}"
                  f"  → best: {best_label}")

    if _RICH:
        console.print(tbl3)

    # ── Step 5: recommendation ────────────────────────────────────────────────
    # Find the single best hybrid across all thresholds by Sharpe
    best_hybrid = max(hybrid_rows, key=lambda x: max(x[1]["sharpe"], x[2]["sharpe"]))
    T_best = best_hybrid[0]
    sa_best, sb_best = best_hybrid[1], best_hybrid[2]
    if sa_best["sharpe"] > sb_best["sharpe"]:
        rec_label = f"Credit when VIX < {T_best}, N={PRICE_STOP_N} when VIX ≥ {T_best}"
        rec_sharpe = sa_best["sharpe"]
        rec_pnl = sa_best["total"]
    else:
        rec_label = f"N={PRICE_STOP_N} when VIX < {T_best}, Credit when VIX ≥ {T_best}"
        rec_sharpe = sb_best["sharpe"]
        rec_pnl = sb_best["total"]

    if _RICH:
        console.print()
        console.print(f"  [bold]Best hybrid:[/]  {rec_label}")
        console.print(f"              Sharpe {rec_sharpe:.3f}  vs  "
                      f"pure credit {s_credit['sharpe']:.3f}  /  pure N={PRICE_STOP_N} {s_price['sharpe']:.3f}")
        console.print(f"              P&L {fmt_pnl(rec_pnl)}\n")
    else:
        print(f"\n  Best hybrid: {rec_label}")
        print(f"    Sharpe {rec_sharpe:.3f}  P&L {fmt_pnl(rec_pnl)}")
        print(f"    vs pure credit Sharpe {s_credit['sharpe']:.3f} / pure N={PRICE_STOP_N} {s_price['sharpe']:.3f}")

    # ── CSV export ────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"vix_stop_analysis_{ts}.csv"

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        # per-day data
        writer.writerow(["date", "vix", "credit_pnl", "price_pnl", "better"])
        for d in days_data:
            better = "credit" if d["credit_pnl"] >= d["price_pnl"] else f"n={PRICE_STOP_N}"
            writer.writerow([d["date"], f"{d['vix']:.2f}",
                             f"{d['credit_pnl']:.2f}", f"{d['price_pnl']:.2f}", better])

    msg = f"  Per-day data saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
