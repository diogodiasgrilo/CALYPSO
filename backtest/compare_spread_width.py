"""
HYDRA Fixed 50pt vs VIX-Scaled Spread Width Comparison

Compares two identical HYDRA configs — only spread width logic differs.
Runs in real-Greeks strict mode (days without Greeks cache are skipped).

Run: python -m backtest.compare_spread_width
"""
import csv
import statistics
from datetime import date, datetime as dt
from pathlib import Path

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 11)


def fixed_cfg() -> BacktestConfig:
    """Current live HYDRA — fixed 50pt spread width."""
    cfg = live_config()
    cfg.start_date      = START_DATE
    cfg.end_date        = END_DATE
    cfg.use_real_greeks = True
    # already fixed at 50pt in live_config(), explicit for clarity
    cfg.call_min_spread_width = 50
    cfg.put_min_spread_width  = 50
    cfg.max_spread_width      = 50
    return cfg


def vix_scaled_cfg() -> BacktestConfig:
    """HYDRA with VIX-scaled spread width — 25pt floor, 100pt cap."""
    cfg = live_config()
    cfg.start_date      = START_DATE
    cfg.end_date        = END_DATE
    cfg.use_real_greeks = True
    cfg.spread_vix_multiplier = 3.5
    cfg.call_min_spread_width = 25
    cfg.put_min_spread_width  = 25
    cfg.max_spread_width      = 100
    return cfg


def summarise(results, label: str) -> dict:
    daily_pnls  = [r.net_pnl for r in results]
    total_pnl   = sum(daily_pnls)
    total_days  = len(results)
    win_days    = sum(1 for p in daily_pnls if p > 0)
    loss_days   = sum(1 for p in daily_pnls if p < 0)

    placed      = sum(r.entries_placed for r in results)
    skipped     = sum(r.entries_skipped for r in results)
    total_stops = sum(r.stops_hit for r in results)

    call_credits = [e.call_credit for r in results for e in r.entries if e.call_credit > 0]
    put_credits  = [e.put_credit  for r in results for e in r.entries if e.put_credit  > 0]
    avg_call_credit = statistics.mean(call_credits) if call_credits else 0
    avg_put_credit  = statistics.mean(put_credits)  if put_credits  else 0

    call_widths = [e.call_spread_width for r in results for e in r.entries
                   if e.entry_type != "skipped" and e.call_spread_width > 0]
    put_widths  = [e.put_spread_width  for r in results for e in r.entries
                   if e.entry_type != "skipped" and e.put_spread_width  > 0]
    avg_call_width = statistics.mean(call_widths) if call_widths else 0
    avg_put_width  = statistics.mean(put_widths)  if put_widths  else 0

    mean   = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev  = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum  += p
        peak  = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    call_stops = sum(sum(1 for e in r.entries if e.call_outcome == "stopped") for r in results)
    put_stops  = sum(sum(1 for e in r.entries if e.put_outcome  == "stopped") for r in results)

    return {
        "label":            label,
        "days":             total_days,
        "win":              win_days,
        "loss":             loss_days,
        "win_rate":         win_days / total_days * 100 if total_days else 0,
        "total_pnl":        total_pnl,
        "mean_daily":       mean,
        "stdev_daily":      stdev,
        "sharpe":           sharpe,
        "max_dd":           max_dd,
        "calmar":           calmar,
        "placed":           placed,
        "skipped":          skipped,
        "total_stops":      total_stops,
        "stop_rate":        total_stops / placed * 100 if placed else 0,
        "call_stops":       call_stops,
        "put_stops":        put_stops,
        "avg_call_credit":  avg_call_credit,
        "avg_put_credit":   avg_put_credit,
        "avg_call_width":   avg_call_width,
        "avg_put_width":    avg_put_width,
    }


METRICS = [
    ("Win rate %",           "win_rate",        "{:.1f}%"),
    ("Total net P&L",        "total_pnl",       "${:,.0f}"),
    ("Mean daily P&L",       "mean_daily",      "${:.2f}"),
    ("Sharpe (annualised)",  "sharpe",          "{:.3f}"),
    ("Max drawdown",         "max_dd",          "${:,.0f}"),
    ("Calmar ratio",         "calmar",          "{:.3f}"),
    ("Entries placed",       "placed",          "{:.0f}"),
    ("Entries skipped",      "skipped",         "{:.0f}"),
    ("Total stops",          "total_stops",     "{:.0f}"),
    ("Stop rate %",          "stop_rate",       "{:.1f}%"),
    ("Call-side stops",      "call_stops",      "{:.0f}"),
    ("Put-side stops",       "put_stops",       "{:.0f}"),
    ("Avg call credit $",    "avg_call_credit", "${:.2f}"),
    ("Avg put credit $",     "avg_put_credit",  "${:.2f}"),
    ("Avg call width pt",    "avg_call_width",  "{:.1f}pt"),
    ("Avg put width pt",     "avg_put_width",   "{:.1f}pt"),
]

HIGHER_IS_BETTER = {"win_rate", "total_pnl", "sharpe", "calmar", "mean_daily",
                    "placed", "avg_call_credit", "avg_put_credit"}
LOWER_IS_BETTER  = {"max_dd", "stop_rate", "skipped", "total_stops",
                    "call_stops", "put_stops"}


if __name__ == "__main__":
    configs = [
        ("HYDRA Fixed 50pt",    fixed_cfg()),
        ("HYDRA VIX-Scaled",    vix_scaled_cfg()),
    ]

    if _RICH:
        console = Console()
        console.print("\n[bold cyan]HYDRA Spread Width: Fixed 50pt vs VIX-Scaled[/]")
        console.print(f"Period: {START_DATE} → {END_DATE}  |  [green]Real Greeks (strict)[/]")
        console.print("[dim]Fixed: call/put min=50 max=50  |  VIX-Scaled: min=25 max=100 mult=3.5[/]\n")
    else:
        print("\nHYDRA Spread Width: Fixed 50pt vs VIX-Scaled")
        print(f"Period: {START_DATE} → {END_DATE}  |  Real Greeks (strict)")
        print("Fixed: min=50 max=50  |  VIX-Scaled: min=25 max=100 mult=3.5\n")

    all_stats = []
    for label, cfg in configs:
        if _RICH:
            console.print(f"  Running [bold]{label}[/]...")
        else:
            print(f"  Running {label}...")
        results = run_backtest(cfg, verbose=False)
        all_stats.append(summarise(results, label))

    col_w = 16
    if _RICH:
        tbl = Table(title="Fixed 50pt vs VIX-Scaled Spread Width",
                    box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
        tbl.add_column("Metric", style="cyan", width=24)
        for s in all_stats:
            tbl.add_column(s["label"], justify="right", width=col_w)
        for metric, key, fmt in METRICS:
            row_vals = [fmt.format(s[key]) for s in all_stats]
            if len(all_stats) == 2:
                v0, v1 = all_stats[0][key], all_stats[1][key]
                if key in HIGHER_IS_BETTER:
                    if v0 > v1:   row_vals[0] = f"[bold green]{row_vals[0]}[/]"
                    elif v1 > v0: row_vals[1] = f"[bold green]{row_vals[1]}[/]"
                elif key in LOWER_IS_BETTER:
                    if v0 < v1:   row_vals[0] = f"[bold green]{row_vals[0]}[/]"
                    elif v1 < v0: row_vals[1] = f"[bold green]{row_vals[1]}[/]"
            tbl.add_row(metric, *row_vals)
        console.print(); console.print(tbl)
    else:
        header = f"  {'Metric':<24}"
        for s in all_stats:
            header += f"  {s['label']:>{col_w}}"
        print(); print(header)
        print("─" * (26 + (col_w + 2) * len(all_stats)))
        for metric, key, fmt in METRICS:
            row = f"  {metric:<24}"
            for s in all_stats:
                row += f"  {fmt.format(s[key]):>{col_w}}"
            print(row)

    f, v = all_stats[0], all_stats[1]
    pnl_diff    = v["total_pnl"] - f["total_pnl"]
    sharpe_diff = v["sharpe"]    - f["sharpe"]
    dd_diff     = v["max_dd"]    - f["max_dd"]

    if _RICH:
        console.print(f"\n  [bold]VIX-Scaled vs Fixed:[/]")
        console.print(f"  P&L diff:    [{'green' if pnl_diff > 0 else 'red'}]{'+' if pnl_diff >= 0 else ''}{pnl_diff:,.0f}[/]")
        console.print(f"  Sharpe diff: [{'green' if sharpe_diff > 0 else 'red'}]{'+' if sharpe_diff >= 0 else ''}{sharpe_diff:.3f}[/]")
        console.print(f"  MaxDD diff:  [{'green' if dd_diff < 0 else 'red'}]{'+' if dd_diff >= 0 else ''}{dd_diff:,.0f}[/]")
        console.print()
    else:
        print(f"\n  VIX-Scaled vs Fixed:")
        print(f"  P&L diff:    {'+' if pnl_diff >= 0 else ''}{pnl_diff:,.0f}")
        print(f"  Sharpe diff: {'+' if sharpe_diff >= 0 else ''}{sharpe_diff:.3f}")
        print(f"  MaxDD diff:  {'+' if dd_diff >= 0 else ''}{dd_diff:,.0f}")

    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"spread_width_comparison_{ts}.csv"
    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "placed", "skipped", "total_stops", "stop_rate",
                "call_stops", "put_stops",
                "avg_call_credit", "avg_put_credit", "avg_call_width", "avg_put_width"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_stats)
    msg = f"  Results saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
