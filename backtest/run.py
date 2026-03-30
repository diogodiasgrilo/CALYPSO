"""
HYDRA Backtest Runner

Usage:
    # Download all data first (run once, takes ~30-60 min for full history):
    python -m backtest.run --download

    # Run backtest with live config (default):
    python -m backtest.run

    # Run backtest with custom date range:
    python -m backtest.run --start 2023-01-01 --end 2024-12-31

    # Run with custom parameters:
    python -m backtest.run --put-stop-buffer 200 --min-put-credit 3.00

    # Enable E6/E7 conditional entries:
    python -m backtest.run --e6 --e7

    # Compare multiple configs side-by-side:
    python -m backtest.run --compare

    # Save results to CSV:
    python -m backtest.run --output results.csv
"""
import argparse
import sys
from datetime import date
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import BacktestConfig, live_config
from backtest.downloader import download_all
from backtest.engine import run_backtest, summarize, print_stats


def parse_args():
    p = argparse.ArgumentParser(description="HYDRA Backtest")
    p.add_argument("--download", action="store_true", help="Download/update cached data")
    p.add_argument("--fast", action="store_true",
                   help="Fast download: 30s timeout, 1 retry — skips slow dates without "
                        "marking them permanent so they can be retried later")
    p.add_argument("--start", default="2022-05-16", help="Start date YYYY-MM-DD")
    p.add_argument("--end", default=str(date.today()), help="End date YYYY-MM-DD")
    p.add_argument("--compare", action="store_true", help="Compare preset configs")
    p.add_argument("--output", default="", help="Save entry-level CSV to this path")

    # All major parameters
    p.add_argument("--entry-times", nargs="+", default=None,
                   help="Entry times e.g. '10:15 10:45 11:15 11:45 12:15'")
    p.add_argument("--e6", action="store_true", help="Enable E6 conditional entry (12:45)")
    p.add_argument("--e7", action="store_true", help="Enable E7 conditional entry (13:15)")
    p.add_argument("--downday-threshold", type=float, default=0.3,
                   help="SPX drop %% to trigger conditional entries (default 0.3)")
    p.add_argument("--no-fomc-t1", action="store_true", help="Disable FOMC T+1 call-only")

    p.add_argument("--call-otm-mult", type=float, default=None, help="Call starting OTM multiplier")
    p.add_argument("--put-otm-mult", type=float, default=None, help="Put starting OTM multiplier")
    p.add_argument("--spread-vix-mult", type=float, default=None, help="Spread width VIX multiplier")
    p.add_argument("--call-spread-floor", type=int, default=None, help="Call spread min width (pt)")
    p.add_argument("--put-spread-floor", type=int, default=None, help="Put spread min width (pt)")
    p.add_argument("--max-spread", type=int, default=None, help="Max spread width (pt)")

    p.add_argument("--min-call-credit", type=float, default=None, help="Min call credit per side ($)")
    p.add_argument("--min-put-credit", type=float, default=None, help="Min put credit per side ($)")
    p.add_argument("--call-credit-floor", type=float, default=None, help="Call credit hard floor ($)")
    p.add_argument("--put-credit-floor", type=float, default=None, help="Put credit hard floor ($)")
    p.add_argument("--put-only-max-vix", type=float, default=None, help="Max VIX for put-only entries")

    p.add_argument("--call-stop-buffer", type=float, default=None, help="Call stop buffer in $ (e.g. 10)")
    p.add_argument("--put-stop-buffer", type=float, default=None, help="Put stop buffer in $ (e.g. 500)")

    p.add_argument("--target-delta", type=float, default=None, help="Target delta for OTM distance")
    p.add_argument("--contracts", type=int, default=1, help="Number of contracts")
    p.add_argument("--real-greeks", action="store_true",
                   help="Use real delta from ThetaData Greeks files (strict: skips days without cache)")

    return p.parse_args()


def build_config(args) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date = date.fromisoformat(args.start)
    cfg.end_date = date.fromisoformat(args.end)

    if args.entry_times:
        cfg.entry_times = args.entry_times
    if args.e6:
        cfg.conditional_e6_enabled = True
    if args.e7:
        cfg.conditional_e7_enabled = True
    if args.downday_threshold != 0.3:
        cfg.downday_threshold_pct = args.downday_threshold
    if args.no_fomc_t1:
        cfg.fomc_t1_callonly_enabled = False

    if args.call_otm_mult is not None:
        cfg.call_starting_otm_multiplier = args.call_otm_mult
    if args.put_otm_mult is not None:
        cfg.put_starting_otm_multiplier = args.put_otm_mult
    if args.spread_vix_mult is not None:
        cfg.spread_vix_multiplier = args.spread_vix_mult
    if args.call_spread_floor is not None:
        cfg.call_min_spread_width = args.call_spread_floor
    if args.put_spread_floor is not None:
        cfg.put_min_spread_width = args.put_spread_floor
    if args.max_spread is not None:
        cfg.max_spread_width = args.max_spread

    if args.min_call_credit is not None:
        cfg.min_call_credit = args.min_call_credit
    if args.min_put_credit is not None:
        cfg.min_put_credit = args.min_put_credit
    if args.call_credit_floor is not None:
        cfg.call_credit_floor = args.call_credit_floor
    if args.put_credit_floor is not None:
        cfg.put_credit_floor = args.put_credit_floor
    if args.put_only_max_vix is not None:
        cfg.put_only_max_vix = args.put_only_max_vix

    if args.call_stop_buffer is not None:
        cfg.call_stop_buffer = args.call_stop_buffer
    if args.put_stop_buffer is not None:
        cfg.put_stop_buffer = args.put_stop_buffer

    if args.target_delta is not None:
        cfg.target_delta = args.target_delta
    if args.real_greeks:
        cfg.use_real_greeks = True

    cfg.contracts = args.contracts
    return cfg


def run_compare(start: date, end: date):
    """Run multiple preset configs and show side-by-side summary."""
    from backtest.config import (
        live_config, tight_stops_config, wide_stops_config,
        higher_credit_gate_config, e6_e7_enabled_config
    )
    configs = {
        "Live (v1.16.1)":        live_config(),
        "Tight stops ($2 buf)":  tight_stops_config(),
        "Wide stops ($10 buf)":  wide_stops_config(),
        "Higher credits":        higher_credit_gate_config(),
        "E6+E7 enabled":         e6_e7_enabled_config(),
    }
    for name, cfg in configs.items():
        cfg.start_date = start
        cfg.end_date = end

    print(f"\n{'='*80}")
    print(f"  HYDRA BACKTEST COMPARISON: {start} → {end}")
    print(f"{'='*80}")
    print(f"  {'Config':<28}  {'Net P&L':>10}  {'Win%':>6}  {'Sharpe':>7}  {'MaxDD':>10}  {'Stops%':>7}")
    print(f"  {'-'*70}")

    import math
    import pandas as pd
    for name, cfg in configs.items():
        results = run_backtest(cfg)
        if not results:
            print(f"  {name:<28}  no data")
            continue
        daily_net = [r.net_pnl for r in results]
        total_net = sum(daily_net)
        win_rate = sum(1 for x in daily_net if x > 0) / len(daily_net) * 100
        arr = pd.Series(daily_net)
        sharpe = arr.mean() / arr.std() * math.sqrt(252) if arr.std() > 0 else 0
        cumulative = arr.cumsum()
        max_dd = float((cumulative - cumulative.cummax()).min())
        all_placed = [e for r in results for e in r.entries if e.entry_type != "skipped"]
        stops = sum(
            (1 if e.call_outcome == "stopped" else 0) +
            (1 if e.put_outcome == "stopped" else 0)
            for e in all_placed
        )
        stop_rate = stops / (len(all_placed) * 2) * 100 if all_placed else 0
        print(f"  {name:<28}  ${total_net:>9,.0f}  {win_rate:>5.1f}%  {sharpe:>7.2f}  ${max_dd:>9,.0f}  {stop_rate:>6.1f}%")
    print(f"{'='*80}\n")


def main():
    args = parse_args()

    if args.download:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        download_all(start, end, fast_mode=args.fast)
        if not args.compare and not any([
            args.entry_times, args.e6, args.e7,
            args.call_stop_buffer, args.put_stop_buffer,
        ]):
            return  # download only

    if args.compare:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        run_compare(start, end)
        return

    cfg = build_config(args)
    results = run_backtest(cfg)

    if not results:
        print("No results — run --download first to cache data.")
        return

    print_stats(results)

    if args.output:
        df = summarize(results)
        df.to_csv(args.output, index=False)
        print(f"Entry-level results saved to: {args.output}")


if __name__ == "__main__":
    main()
