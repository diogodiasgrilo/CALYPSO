"""
Tammy Pure vs HYDRA — Strike Selection Comparison

Tests whether HYDRA's dynamic credit gate + progressive tightening + directional
filters actually improve on Tammy Chambless's original approach:
  → Always target ~8 delta on both sides
  → Take whatever credit the market gives
  → Full IC every time, no one-sided entries, no direction filters

Two configs run against the same date range and same stop formula.
Only the entry logic differs — isolates the credit gate contribution.

Run: python -m backtest.compare_tammy_vs_hydra
"""
import csv
import statistics
import sys
from datetime import date, datetime as dt
from pathlib import Path

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

USE_REAL_GREEKS = "--real-greeks" in sys.argv

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 22)


# ── Config builders ────────────────────────────────────────────────────────────

def hydra_cfg() -> BacktestConfig:
    """Current live HYDRA config — all filters, credit gate, directional logic."""
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date   = END_DATE
    cfg.use_real_greeks = USE_REAL_GREEKS
    return cfg


def tammy_cfg() -> BacktestConfig:
    """
    Pure Tammy Chambless approach (matched to real strategy):
    - 6 entries at 10:00, 10:30, 11:00, 11:30, 12:00, 12:30 ET
    - Target ~8 delta on both sides (starting multiplier = 1.0 = exact 8-delta distance)
    - Accept any positive credit (gate effectively disabled)
    - Stop = total IC credit exactly (pure breakeven — no buffers)
    - VIX-scaled spread width: 25pt floor, 100pt cap (not fixed 50pt)
    - Always full IC — no one-sided entries
    - No directional filters, no E6/E7, no FOMC T+1 override
    """
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date   = END_DATE

    # 6 entries at Tammy's actual schedule
    cfg.entry_times = ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30"]

    # Strike selection: start exactly at 8-delta OTM distance, no wide scan
    cfg.call_starting_otm_multiplier = 1.0
    cfg.put_starting_otm_multiplier  = 1.0

    # Credit gate: effectively disabled — accept any positive credit
    cfg.min_call_credit   = 0.01
    cfg.min_put_credit    = 0.01
    cfg.call_credit_floor = 0.01
    cfg.put_credit_floor  = 0.01

    # Stop = total credit exactly (pure breakeven, no buffer)
    cfg.call_stop_buffer = 0.0
    cfg.put_stop_buffer = 0.0

    # VIX-scaled spread width matching Tammy's range (25–100pt)
    cfg.spread_vix_multiplier  = 3.5
    cfg.call_min_spread_width  = 25
    cfg.put_min_spread_width   = 25
    cfg.max_spread_width       = 100

    # Always full IC — never one-sided
    cfg.one_sided_entries_enabled = False

    # No directional filters on base entries
    cfg.base_entry_downday_callonly_pct = None
    cfg.base_entry_upday_putonly_pct    = None

    # No conditional E6/E7 entries
    cfg.conditional_e6_enabled        = False
    cfg.conditional_e7_enabled        = False
    cfg.conditional_upday_e6_enabled  = False
    cfg.conditional_upday_e7_enabled  = False

    # No FOMC T+1 override
    cfg.fomc_t1_callonly_enabled = False
    cfg.use_real_greeks = USE_REAL_GREEKS

    return cfg


# ── Stats ──────────────────────────────────────────────────────────────────────

def summarise(results, label: str) -> dict:
    daily_pnls  = [r.net_pnl for r in results]
    total_pnl   = sum(daily_pnls)
    total_days  = len(results)
    win_days    = sum(1 for p in daily_pnls if p > 0)
    loss_days   = sum(1 for p in daily_pnls if p < 0)

    placed      = sum(r.entries_placed for r in results)
    skipped     = sum(r.entries_skipped for r in results)
    total_stops = sum(r.stops_hit for r in results)

    # Entry type breakdown
    full_ic    = sum(sum(1 for e in r.entries if e.entry_type == "full_ic")    for r in results)
    call_only  = sum(sum(1 for e in r.entries if e.entry_type == "call_only")  for r in results)
    put_only   = sum(sum(1 for e in r.entries if e.entry_type == "put_only")   for r in results)

    # Credit stats (placed entries only)
    call_credits = [e.call_credit for r in results for e in r.entries
                    if e.call_credit > 0]
    put_credits  = [e.put_credit  for r in results for e in r.entries
                    if e.put_credit  > 0]
    avg_call_credit = statistics.mean(call_credits) if call_credits else 0
    avg_put_credit  = statistics.mean(put_credits)  if put_credits  else 0

    # OTM distances (actual short strike distance from SPX at entry)
    call_otm = [abs(e.short_call - e.spx_at_entry) for r in results for e in r.entries
                if e.short_call > 0 and e.spx_at_entry > 0]
    put_otm  = [abs(e.short_put  - e.spx_at_entry) for r in results for e in r.entries
                if e.short_put  > 0 and e.spx_at_entry > 0]
    avg_call_otm = statistics.mean(call_otm) if call_otm else 0
    avg_put_otm  = statistics.mean(put_otm)  if put_otm  else 0

    mean  = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum  += p
        peak  = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    # Stop breakdown by side
    call_stops = sum(sum(1 for e in r.entries if e.call_outcome == "stopped") for r in results)
    put_stops  = sum(sum(1 for e in r.entries if e.put_outcome  == "stopped") for r in results)

    return {
        "label":           label,
        "days":            total_days,
        "win":             win_days,
        "loss":            loss_days,
        "win_rate":        win_days / total_days * 100 if total_days else 0,
        "total_pnl":       total_pnl,
        "mean_daily":      mean,
        "stdev_daily":     stdev,
        "sharpe":          sharpe,
        "max_dd":          max_dd,
        "calmar":          calmar,
        "placed":          placed,
        "skipped":         skipped,
        "total_stops":     total_stops,
        "stop_rate":       total_stops / placed * 100 if placed else 0,
        "call_stops":      call_stops,
        "put_stops":       put_stops,
        "full_ic":         full_ic,
        "call_only":       call_only,
        "put_only":        put_only,
        "avg_call_credit": avg_call_credit,
        "avg_put_credit":  avg_put_credit,
        "avg_call_otm":    avg_call_otm,
        "avg_put_otm":     avg_put_otm,
    }


METRICS = [
    # Performance
    ("Win rate %",           "win_rate",        "{:.1f}%"),
    ("Total net P&L",        "total_pnl",       "${:,.0f}"),
    ("Mean daily P&L",       "mean_daily",      "${:.2f}"),
    ("Sharpe (annualised)",  "sharpe",          "{:.3f}"),
    ("Max drawdown",         "max_dd",          "${:,.0f}"),
    ("Calmar ratio",         "calmar",          "{:.3f}"),
    # Activity
    ("Entries placed",       "placed",          "{:.0f}"),
    ("Entries skipped",      "skipped",         "{:.0f}"),
    ("Full IC entries",      "full_ic",         "{:.0f}"),
    ("Call-only entries",    "call_only",       "{:.0f}"),
    ("Put-only entries",     "put_only",        "{:.0f}"),
    # Stops
    ("Total stops",          "total_stops",     "{:.0f}"),
    ("Stop rate %",          "stop_rate",       "{:.1f}%"),
    ("Call-side stops",      "call_stops",      "{:.0f}"),
    ("Put-side stops",       "put_stops",       "{:.0f}"),
    # Credit & strikes
    ("Avg call credit $",    "avg_call_credit", "${:.2f}"),
    ("Avg put credit $",     "avg_put_credit",  "${:.2f}"),
    ("Avg call OTM pts",     "avg_call_otm",    "{:.1f}pt"),
    ("Avg put OTM pts",      "avg_put_otm",     "{:.1f}pt"),
]

HIGHER_IS_BETTER = {"win_rate", "total_pnl", "sharpe", "calmar", "mean_daily",
                    "placed", "full_ic", "avg_call_credit", "avg_put_credit",
                    "avg_call_otm", "avg_put_otm"}
LOWER_IS_BETTER  = {"max_dd", "stop_rate", "skipped", "total_stops",
                    "call_stops", "put_stops"}


if __name__ == "__main__":
    greeks_label = "[green]Real Greeks (strict)[/]" if USE_REAL_GREEKS else "[dim]VIX formula (approx)[/]"
    greeks_label_plain = "Real Greeks (strict — days with Greeks cache only)" if USE_REAL_GREEKS else "VIX formula (approx)"
    if _RICH:
        console = Console()
        console.print("\n[bold cyan]Tammy Pure vs HYDRA — Strike Selection Comparison[/]")
        console.print(f"Period: {START_DATE} → {END_DATE}  |  Strike selection: {greeks_label}")
        console.print("\n[yellow]HYDRA:[/] Credit gate + progressive tightening + directional filters + E6/E7 + FOMC T+1")
        console.print("[yellow]Tammy:[/] ~8 delta always, full IC always, no gate, no filters\n")
    else:
        print("\nTammy Pure vs HYDRA — Strike Selection Comparison")
        print(f"Period: {START_DATE} → {END_DATE}  |  Strike selection: {greeks_label_plain}")
        print("HYDRA: Credit gate + progressive tightening + directional filters + E6/E7 + FOMC T+1")
        print("Tammy: ~8 delta always, full IC always, no gate, no filters\n")

    configs = [
        ("HYDRA",  hydra_cfg()),
        ("Tammy",  tammy_cfg()),
    ]

    all_stats = []
    for label, cfg in configs:
        if _RICH:
            console.print(f"  Running [bold]{label}[/]...")
        else:
            print(f"  Running {label}...")
        results = run_backtest(cfg, verbose=False)
        all_stats.append(summarise(results, label))

    # ── Table ──────────────────────────────────────────────────────────────────
    col_w = 14
    if _RICH:
        tbl = Table(title="Tammy Pure vs HYDRA",
                    box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
        tbl.add_column("Metric", style="cyan", width=24)
        for s in all_stats:
            tbl.add_column(s["label"], justify="right", width=col_w)
        for metric, key, fmt in METRICS:
            row_vals = [fmt.format(s[key]) for s in all_stats]
            # Highlight better value in green
            if len(all_stats) == 2:
                v0, v1 = all_stats[0][key], all_stats[1][key]
                if key in HIGHER_IS_BETTER:
                    if v0 > v1:
                        row_vals[0] = f"[bold green]{row_vals[0]}[/]"
                    elif v1 > v0:
                        row_vals[1] = f"[bold green]{row_vals[1]}[/]"
                elif key in LOWER_IS_BETTER:
                    if v0 < v1:
                        row_vals[0] = f"[bold green]{row_vals[0]}[/]"
                    elif v1 < v0:
                        row_vals[1] = f"[bold green]{row_vals[1]}[/]"
            tbl.add_row(metric, *row_vals)
        console.print()
        console.print(tbl)
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

    # ── Summary ────────────────────────────────────────────────────────────────
    h, t = all_stats[0], all_stats[1]
    pnl_diff   = h["total_pnl"]   - t["total_pnl"]
    sharpe_diff = h["sharpe"]     - t["sharpe"]
    dd_diff    = h["max_dd"]      - t["max_dd"]

    if _RICH:
        console.print(f"\n  [bold]HYDRA vs Tammy:[/]")
        color_pnl    = "green" if pnl_diff > 0 else "red"
        color_sharpe = "green" if sharpe_diff > 0 else "red"
        color_dd     = "green" if dd_diff < 0 else "red"
        console.print(f"  P&L difference:    [{color_pnl}]{'+' if pnl_diff >= 0 else ''}{pnl_diff:,.0f}[/]  (HYDRA {'better' if pnl_diff > 0 else 'worse'})")
        console.print(f"  Sharpe difference: [{color_sharpe}]{'+' if sharpe_diff >= 0 else ''}{sharpe_diff:.3f}[/]  (HYDRA {'better' if sharpe_diff > 0 else 'worse'})")
        console.print(f"  MaxDD difference:  [{color_dd}]{'+' if dd_diff >= 0 else ''}{dd_diff:,.0f}[/]  (HYDRA drawdown {'smaller' if dd_diff < 0 else 'larger'})")
        console.print()
    else:
        print(f"\n  HYDRA vs Tammy:")
        print(f"  P&L diff:    {'+' if pnl_diff >= 0 else ''}{pnl_diff:,.0f}  (HYDRA {'better' if pnl_diff > 0 else 'worse'})")
        print(f"  Sharpe diff: {'+' if sharpe_diff >= 0 else ''}{sharpe_diff:.3f}  (HYDRA {'better' if sharpe_diff > 0 else 'worse'})")
        print(f"  MaxDD diff:  {'+' if dd_diff >= 0 else ''}{dd_diff:,.0f}  (HYDRA drawdown {'smaller' if dd_diff < 0 else 'larger'})")

    # ── CSV ────────────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"tammy_vs_hydra_{ts}.csv"
    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "placed", "skipped", "total_stops", "stop_rate",
                "call_stops", "put_stops", "full_ic", "call_only", "put_only",
                "avg_call_credit", "avg_put_credit", "avg_call_otm", "avg_put_otm"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_stats)
    msg = f"  Results saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
