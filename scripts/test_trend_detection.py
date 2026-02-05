#!/usr/bin/env python3
"""
Test script for MEIC-TF trend detection.

This script tests the EMA-based trend detection logic that MEIC-TF uses
to determine whether to place call spreads, put spreads, or full iron condors.

Run on VM:
    gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/test_trend_detection.py'"
"""

import sys
sys.path.insert(0, "/opt/calypso")

from datetime import datetime
import pytz

from shared.saxo_client import SaxoClient
from shared.config_loader import ConfigLoader
from shared.technical_indicators import calculate_ema


def get_trend_signal(ema_short: float, ema_long: float, threshold: float = 0.001):
    """
    Determine trend signal based on EMA crossover.

    Args:
        ema_short: Short-period EMA value (e.g., 20 EMA)
        ema_long: Long-period EMA value (e.g., 40 EMA)
        threshold: Neutral zone threshold (default 0.1%)

    Returns:
        Tuple of (signal, diff_pct) where signal is "BULLISH", "BEARISH", or "NEUTRAL"
    """
    if ema_long == 0:
        return "NEUTRAL", 0.0

    diff_pct = (ema_short - ema_long) / ema_long

    if diff_pct > threshold:
        return "BULLISH", diff_pct
    elif diff_pct < -threshold:
        return "BEARISH", diff_pct
    else:
        return "NEUTRAL", diff_pct


def main():
    print("=" * 70)
    print("MEIC-TF TREND DETECTION TEST")
    print("=" * 70)
    print()

    # Get current time
    et_tz = pytz.timezone("US/Eastern")
    now_et = datetime.now(et_tz)
    print(f"Current Time (ET): {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print()

    # Load config (use MEIC-TF config if available, otherwise MEIC)
    try:
        config_path = "bots/meic_tf/config/config.json"
        config_loader = ConfigLoader(config_path)
        config = config_loader.load_config()
        print(f"Loaded config: {config_path}")
    except Exception:
        config_path = "bots/meic/config/config.json"
        config_loader = ConfigLoader(config_path)
        config = config_loader.load_config()
        print(f"Loaded config: {config_path} (fallback)")

    # Get trend filter settings
    trend_config = config.get("trend_filter", {})
    ema_short_period = trend_config.get("ema_short_period", 20)
    ema_long_period = trend_config.get("ema_long_period", 40)
    threshold = trend_config.get("ema_neutral_threshold", 0.001)
    bars_count = trend_config.get("chart_bars_count", 50)
    horizon = trend_config.get("chart_horizon_minutes", 1)

    print(f"\nTrend Filter Settings:")
    print(f"  Short EMA period: {ema_short_period}")
    print(f"  Long EMA period:  {ema_long_period}")
    print(f"  Neutral threshold: {threshold} ({threshold * 100:.2f}%)")
    print(f"  Chart bars count: {bars_count}")
    print(f"  Chart horizon: {horizon} minute(s)")
    print()

    # Authenticate with Saxo
    print("Authenticating with Saxo Bank API...")
    client = SaxoClient(config)
    client.authenticate()
    print("✓ Authentication successful")
    print()

    # Get SPX underlying UIC
    underlying_uic = config.get("strategy", {}).get("underlying_uic", 4913)
    print(f"Fetching {bars_count} x {horizon}-minute bars for UIC {underlying_uic} (US500.I/SPX)...")
    print()

    # Fetch chart data
    try:
        result = client.get_chart_data(
            uic=underlying_uic,
            asset_type="CfdOnIndex",
            horizon=horizon,
            count=bars_count
        )

        if not result:
            print("ERROR: No chart data returned from Saxo API")
            return 1

        # Extract bars from the Data key
        bars = result.get("Data", []) if isinstance(result, dict) else result

        if not bars:
            print("ERROR: No bars in chart data response")
            return 1

        print(f"✓ Received {len(bars)} bars")
        print()

        # Extract close prices (use CloseBid as the close price, falling back to Close)
        closes = []
        for bar in bars:
            # Saxo CFD chart data uses CloseBid/CloseAsk, not Close
            close = bar.get("CloseBid") or bar.get("Close") or bar.get("C")
            if close:
                closes.append(float(close))

        if len(closes) < ema_long_period:
            print(f"ERROR: Not enough data points ({len(closes)}) for {ema_long_period}-period EMA")
            return 1

        print(f"Close prices extracted: {len(closes)} values")
        print(f"  First close: ${closes[0]:.2f}")
        print(f"  Last close:  ${closes[-1]:.2f}")
        print(f"  Range: ${min(closes):.2f} - ${max(closes):.2f}")
        print()

        # Show last 10 bars
        print("Last 10 bars:")
        for i, bar in enumerate(bars[-10:]):
            bar_time = bar.get("Time", "N/A")
            close = bar.get("CloseBid") or bar.get("Close") or bar.get("C") or 0
            high = bar.get("HighBid") or bar.get("High") or bar.get("H") or 0
            low = bar.get("LowBid") or bar.get("Low") or bar.get("L") or 0
            print(f"  {i+1:2}. {bar_time} | Close: ${close:.2f} | High: ${high:.2f} | Low: ${low:.2f}")
        print()

        # Calculate EMAs
        print(f"Calculating EMAs...")
        ema_short = calculate_ema(closes, ema_short_period)
        ema_long = calculate_ema(closes, ema_long_period)

        print(f"  {ema_short_period} EMA: ${ema_short:.4f}")
        print(f"  {ema_long_period} EMA: ${ema_long:.4f}")
        print()

        # Determine trend signal
        signal, diff_pct = get_trend_signal(ema_short, ema_long, threshold)

        print("=" * 70)
        print("TREND DETECTION RESULT")
        print("=" * 70)
        print()
        print(f"  {ema_short_period} EMA:     ${ema_short:.4f}")
        print(f"  {ema_long_period} EMA:     ${ema_long:.4f}")
        print(f"  Difference:  {diff_pct * 100:+.4f}%")
        print(f"  Threshold:   ±{threshold * 100:.2f}%")
        print()

        if signal == "BULLISH":
            print(f"  📈 SIGNAL: BULLISH (20 EMA > 40 EMA by {diff_pct * 100:.4f}%)")
            print()
            print("  MEIC-TF Action: Place PUT SPREAD ONLY")
            print("  Rationale: Uptrend detected - calls are risky, puts are safe")
        elif signal == "BEARISH":
            print(f"  📉 SIGNAL: BEARISH (20 EMA < 40 EMA by {abs(diff_pct) * 100:.4f}%)")
            print()
            print("  MEIC-TF Action: Place CALL SPREAD ONLY")
            print("  Rationale: Downtrend detected - puts are risky, calls are safe")
        else:
            print(f"  ↔️  SIGNAL: NEUTRAL (EMAs within {threshold * 100:.2f}% of each other)")
            print()
            print("  MEIC-TF Action: Place FULL IRON CONDOR")
            print("  Rationale: Range-bound market - both sides are safe")

        print()
        print("=" * 70)

        # Show what regular MEIC would do vs MEIC-TF
        print()
        print("COMPARISON: MEIC vs MEIC-TF")
        print("-" * 40)
        print(f"  Regular MEIC:  Full Iron Condor (always)")
        if signal == "BULLISH":
            print(f"  MEIC-TF:       Put Spread Only (call spread skipped)")
        elif signal == "BEARISH":
            print(f"  MEIC-TF:       Call Spread Only (put spread skipped)")
        else:
            print(f"  MEIC-TF:       Full Iron Condor (same as MEIC)")
        print()

        return 0

    except Exception as e:
        print(f"ERROR: Failed to fetch chart data: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
