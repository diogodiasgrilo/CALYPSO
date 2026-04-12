"""
Test: VIX regime zone-specific overrides ON vs OFF

Compares:
- WITH overrides: min_call_credit[1.35 VIX14-20], min_put_credit[2.10 VIX14-20], put_stop_buffer[1.25 VIX<14]
- WITHOUT overrides: all null (baseline from combo_sweep test)

Everything else locked to optimal combo: pb=$1.75, cb=$0.75, decay=2.5x/4h, etc.

Run: python -u -m backtest.test_vix_overrides
"""
import math
import time
from datetime import date
from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest
import pandas as pd

FULL_START = date(2022, 5, 16)
FULL_END = date(2026, 4, 8)
SLIPPAGE = 30.0
MARKUP = 0.10

# Optimal combo baseline (from 432-combo sweep)
BASELINE = {
    "put_stop_buffer": 175.0,  # $1.75
    "call_stop_buffer": 75.0,  # $0.75
    "buffer_decay_start_mult": 2.5,
    "buffer_decay_hours": 4.0,
    "put_credit_floor": 2.75,
    "max_spread_width": 110,
    "downday_theoretical_put_credit": 260.0,  # $2.60
    "vix_regime_enabled": True,
}


def compute_stats(results):
    """Compute performance metrics from DayResult list."""
    if not results:
        return None

    daily_net = [r.net_pnl for r in results]
    total_net = sum(daily_net)
    winning_days = sum(1 for x in daily_net if x > 0)
    losing_days = sum(1 for x in daily_net if x < 0)
    win_rate = winning_days / len(daily_net) if daily_net else 0

    all_entries = [e for r in results for e in r.entries]
    placed = [e for e in all_entries if e.entry_type != "skipped"]
    stops = sum(1 for e in placed if e.call_outcome == "stopped" or e.put_outcome == "stopped")
    stop_rate = stops / len(placed) if placed else 0

    # Sharpe (annualized)
    if len(daily_net) > 1:
        arr = pd.Series(daily_net)
        sharpe = arr.mean() / arr.std() * math.sqrt(252) if arr.std() > 0 else 0
    else:
        sharpe = 0

    # Max drawdown
    cumulative = pd.Series(daily_net).cumsum()
    rolling_max = cumulative.cummax()
    drawdown = cumulative - rolling_max
    max_dd = float(drawdown.min())

    # Calmar ratio
    calmar = sharpe * (arr.std() / abs(max_dd)) if max_dd < 0 else 0

    return {
        "sharpe": sharpe,
        "total_pnl": total_net,
        "max_dd": max_dd,
        "calmar": calmar,
        "win_rate": win_rate,
        "num_stops": stops,
        "stop_rate": stop_rate,
        "days": len(results),
    }


def test_overrides(with_overrides: bool, test_num: int, total_tests: int):
    """Run backtest with VIX regime overrides on or off."""
    cfg = live_config()
    cfg.start_date = FULL_START
    cfg.end_date = FULL_END
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.stop_slippage_per_leg = SLIPPAGE
    cfg.stop_spread_markup_pct = MARKUP

    # Apply baseline
    for k, v in BASELINE.items():
        setattr(cfg, k, v)

    # Configure overrides
    if with_overrides:
        # Enable zone-specific overrides (from template config)
        cfg.vix_regime_min_call_credit = [None, 1.35, None, None]      # VIX 14-20: $1.35
        cfg.vix_regime_min_put_credit = [None, 2.10, None, None]       # VIX 14-20: $2.10
        cfg.vix_regime_put_stop_buffer = [1.25, None, None, None]      # VIX<14: $1.25
        override_str = "WITH zone overrides ($1.35 call, $2.10 put, $1.25 put-buffer)"
    else:
        # Disable all zone-specific overrides (null = use global defaults)
        cfg.vix_regime_min_call_credit = [None, None, None, None]
        cfg.vix_regime_min_put_credit = [None, None, None, None]
        cfg.vix_regime_put_stop_buffer = [None, None, None, None]
        override_str = "WITHOUT zone overrides (all global)"

    print(f"\n{'='*70}")
    print(f"Test {test_num}/{total_tests}: VIX Regime {override_str}")
    print(f"{'='*70}")
    print(f"Baseline: pb=$1.75, cb=$0.75, decay=2.5x/4h, pf=$2.75, sw=110, tp=$2.60")
    print(f"Period: {FULL_START} to {FULL_END}")
    print(f"Slippage: ${SLIPPAGE/100:.2f}/leg, Markup: {MARKUP*100:.0f}%")
    print()

    t0 = time.time()
    results = run_backtest(cfg, verbose=True)
    elapsed = time.time() - t0

    # Compute and print stats
    stats = compute_stats(results)
    if stats:
        print(f"\n{'─'*70}")
        print(f"Results:")
        print(f"{'─'*70}")
        print(f"Sharpe:     {stats['sharpe']:.3f}")
        print(f"Total P&L:  ${stats['total_pnl']:,.0f}")
        print(f"Max DD:     ${stats['max_dd']:,.0f}")
        print(f"Calmar:     {stats['calmar']:.3f}")
        print(f"Win Rate:   {stats['win_rate']:.1%}")
        print(f"Stops:      {stats['num_stops']} ({stats['stop_rate']:.1%})")
        print(f"Days:       {stats['days']}")
        print(f"Elapsed:    {elapsed:.1f}s")
        return stats
    else:
        print("ERROR: No results")
        return None


if __name__ == "__main__":
    print("\n" + "="*70)
    print("🧪 VIX REGIME ZONE OVERRIDES TEST")
    print("="*70)
    print("Baseline parameters from 432-combo sweep optimal")
    print("Testing: VIX regime WITH vs WITHOUT zone-specific overrides")
    print("="*70)

    # Test both with progress tracking
    results_with = test_overrides(True, 1, 2)
    results_without = test_overrides(False, 2, 2)

    if results_with and results_without:
        # Compare
        print(f"\n{'='*70}")
        print("COMPARISON SUMMARY")
        print(f"{'='*70}")
        print(f"{'Metric':<20} {'WITH':>15} {'WITHOUT':>15} {'Diff':>15}")
        print("-" * 70)
        print(f"{'Sharpe':<20} {results_with['sharpe']:>15.3f} {results_without['sharpe']:>15.3f} {results_with['sharpe']-results_without['sharpe']:>+15.3f}")
        print(f"{'P&L':<20} ${results_with['total_pnl']:>14,.0f} ${results_without['total_pnl']:>14,.0f} ${results_with['total_pnl']-results_without['total_pnl']:>+14,.0f}")
        print(f"{'Max DD':<20} ${results_with['max_dd']:>14,.0f} ${results_without['max_dd']:>14,.0f} ${results_with['max_dd']-results_without['max_dd']:>+14,.0f}")
        print(f"{'Calmar':<20} {results_with['calmar']:>15.3f} {results_without['calmar']:>15.3f} {results_with['calmar']-results_without['calmar']:>+15.3f}")
        print(f"{'Win Rate':<20} {results_with['win_rate']:>14.1%} {results_without['win_rate']:>14.1%} {results_with['win_rate']-results_without['win_rate']:>+14.1%}")
        print(f"{'Stops':<20} {results_with['num_stops']:>15} {results_without['num_stops']:>15} {results_with['num_stops']-results_without['num_stops']:>+15}")

        # Conclusion
        print(f"\n{'='*70}")
        print("CONCLUSION")
        print(f"{'='*70}")
        diff = results_with['sharpe'] - results_without['sharpe']
        if abs(diff) < 0.01:
            print(f"➖ NO MEANINGFUL DIFFERENCE")
            print(f"   Sharpe difference: {diff:+.3f}")
            print(f"   P&L difference:    ${results_with['total_pnl']-results_without['total_pnl']:+,.0f}")
            print(f"   Recommendation: Zone overrides don't help — use simplified config (all null)")
        elif results_with['sharpe'] > results_without['sharpe']:
            print(f"✅ ZONE OVERRIDES IMPROVE RESULTS")
            print(f"   Sharpe improvement: +{diff:.3f}")
            print(f"   P&L improvement:    +${results_with['total_pnl']-results_without['total_pnl']:,.0f}")
            print(f"   Recommendation: Enable zone overrides in config")
        else:
            print(f"❌ ZONE OVERRIDES HURT RESULTS")
            print(f"   Sharpe improvement: +{-diff:.3f} (without)")
            print(f"   P&L improvement:    +${results_without['total_pnl']-results_with['total_pnl']:,.0f} (without)")
            print(f"   Recommendation: Keep zone overrides disabled (all null)")
        print(f"{'='*70}\n")
