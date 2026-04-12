"""
Quick test: VIX regime ON vs OFF with optimal combo baseline parameters.

Compares:
- ON:  vix_regime_enabled=true (current, with nulled overrides)
- OFF: vix_regime_enabled=false (test)

Everything else locked to optimal combo: pb=$1.75, cb=$0.75, decay=2.5x/4h, etc.

Run: python -u -m backtest.test_vix_regime
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
    # VIX regime overrides: locked to null (disabled)
    "vix_regime_min_call_credit": [None, None, None, None],
    "vix_regime_min_put_credit": [None, None, None, None],
    "vix_regime_put_stop_buffer": [None, None, None, None],
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


def test_vix_regime(enabled: bool, test_num: int, total_tests: int):
    """Run backtest with vix_regime on or off, with progress display."""
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

    # Toggle VIX regime
    cfg.vix_regime_enabled = enabled

    print(f"\n{'='*70}")
    print(f"Test {test_num}/{total_tests}: vix_regime_enabled = {enabled}")
    print(f"{'='*70}")
    print(f"Baseline: pb=$1.75, cb=$0.75, decay=2.5x/4h, pf=$2.75, sw=110, tp=$2.60")
    print(f"Period: {FULL_START} to {FULL_END}")
    print(f"Slippage: ${SLIPPAGE/100:.2f}/leg, Markup: {MARKUP*100:.0f}%")
    print()

    t0 = time.time()
    # verbose=True will print progress every 50 days
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
    print("🧪 VIX REGIME IMPACT TEST")
    print("="*70)
    print("Baseline parameters from 432-combo sweep optimal")
    print("Testing: vix_regime ON vs OFF (with feature enabled/disabled)")
    print("="*70)

    # Test both with progress tracking
    results_on = test_vix_regime(True, 1, 2)
    results_off = test_vix_regime(False, 2, 2)

    if results_on and results_off:
        # Compare
        print(f"\n{'='*70}")
        print("COMPARISON SUMMARY")
        print(f"{'='*70}")
        print(f"{'Metric':<20} {'ON':>15} {'OFF':>15} {'Diff':>15}")
        print("-" * 70)
        print(f"{'Sharpe':<20} {results_on['sharpe']:>15.3f} {results_off['sharpe']:>15.3f} {results_on['sharpe']-results_off['sharpe']:>+15.3f}")
        print(f"{'P&L':<20} ${results_on['total_pnl']:>14,.0f} ${results_off['total_pnl']:>14,.0f} ${results_on['total_pnl']-results_off['total_pnl']:>+14,.0f}")
        print(f"{'Max DD':<20} ${results_on['max_dd']:>14,.0f} ${results_off['max_dd']:>14,.0f} ${results_on['max_dd']-results_off['max_dd']:>+14,.0f}")
        print(f"{'Calmar':<20} {results_on['calmar']:>15.3f} {results_off['calmar']:>15.3f} {results_on['calmar']-results_off['calmar']:>+15.3f}")
        print(f"{'Win Rate':<20} {results_on['win_rate']:>14.1%} {results_off['win_rate']:>14.1%} {results_on['win_rate']-results_off['win_rate']:>+14.1%}")
        print(f"{'Stops':<20} {results_on['num_stops']:>15} {results_off['num_stops']:>15} {results_on['num_stops']-results_off['num_stops']:>+15}")

        # Conclusion
        print(f"\n{'='*70}")
        print("CONCLUSION")
        print(f"{'='*70}")
        diff = results_on['sharpe'] - results_off['sharpe']
        if abs(diff) < 0.01:
            print(f"➖ NO MEANINGFUL DIFFERENCE")
            print(f"   Sharpe difference: {diff:+.3f}")
            print(f"   P&L difference:    ${results_on['total_pnl']-results_off['total_pnl']:+,.0f}")
            print(f"   Recommendation: Either enable or disable VIX regime (no impact)")
        elif results_on['sharpe'] > results_off['sharpe']:
            print(f"✅ VIX REGIME ON IS BETTER")
            print(f"   Sharpe improvement: +{diff:.3f}")
            print(f"   P&L improvement:    +${results_on['total_pnl']-results_off['total_pnl']:,.0f}")
            print(f"   Recommendation: Keep VIX regime enabled")
        else:
            print(f"❌ VIX REGIME OFF IS BETTER")
            print(f"   Sharpe improvement: +{-diff:.3f}")
            print(f"   P&L improvement:    +${results_off['total_pnl']-results_on['total_pnl']:,.0f}")
            print(f"   Recommendation: Disable VIX regime (remove from config)")
        print(f"{'='*70}\n")
