#!/usr/bin/env python3
"""
Quick script to show what strike prices the strategy would use
for short strangle if it were to sell right now.
"""

import sys
import os
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.config_loader import ConfigLoader
from shared.saxo_client import SaxoClient


def main():
    # Load config
    config_path = "config/config.json"
    loader = ConfigLoader(config_path)
    config = loader.load_config()

    # Initialize client
    client = SaxoClient(config)

    # Authenticate
    print("Authenticating with Saxo...")
    if not client.authenticate():
        print("ERROR: Failed to authenticate")
        return

    # Get current prices
    underlying_uic = config["strategy"]["underlying_uic"]  # SPY
    vix_uic = config["strategy"]["vix_uic"]  # VIX

    spy_quote = client.get_quote(underlying_uic, "Etf")
    vix_quote = client.get_quote(vix_uic, "StockIndex")

    if not spy_quote or not vix_quote:
        print("ERROR: Failed to get quotes")
        return

    spy_price = spy_quote["Quote"].get("Mid") or spy_quote["Quote"].get("LastTraded", 0)
    vix_value = vix_quote["Quote"].get("Mid") or vix_quote["Quote"].get("LastTraded", 0)

    # If VIX is 0 (after hours), try to get last traded or use a reasonable default
    if vix_value == 0:
        vix_value = vix_quote["Quote"].get("LastTraded", 0)
    if vix_value == 0:
        # Use last known VIX from today's close as fallback
        vix_value = 15.0  # Reasonable default
        print(f"\nNOTE: VIX quote unavailable (after hours), using default: {vix_value}")

    print(f"\n{'='*60}")
    print(f"SHORT STRANGLE STRIKE CALCULATOR")
    print(f"{'='*60}")
    print(f"Current Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"SPY Price: ${spy_price:.2f}")
    print(f"VIX: {vix_value:.2f}")

    # Get weekly expiration DTE
    expirations = client.get_option_expirations(underlying_uic)
    if not expirations:
        print("ERROR: Failed to get expirations")
        return

    today = datetime.now().date()
    weekly_dte = 7
    weekly_expiry = None

    for exp_data in expirations:
        exp_date_str = exp_data.get("Expiry")
        if exp_date_str:
            exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if 0 < dte <= 7:
                weekly_dte = dte
                weekly_expiry = exp_date_str[:10]
                break

    print(f"Weekly Expiry: {weekly_expiry} ({weekly_dte} DTE)")

    # Calculate expected move
    iv = vix_value / 100  # VIX as decimal
    expected_move = client.calculate_expected_move(spy_price, iv, days=weekly_dte)

    print(f"\n{'='*60}")
    print(f"EXPECTED MOVE CALCULATION")
    print(f"{'='*60}")
    print(f"IV (VIX): {vix_value:.2f}%")
    print(f"Days: {weekly_dte}")
    print(f"Expected Move: ${expected_move:.2f}")

    # Strategy uses 1.5-2.0x multiplier, middle = 1.75
    multiplier_min = config["strategy"].get("strangle_multiplier_min", 1.5)
    multiplier_max = config["strategy"].get("strangle_multiplier_max", 2.0)
    multiplier = (multiplier_min + multiplier_max) / 2

    move_distance = expected_move * multiplier

    print(f"\n{'='*60}")
    print(f"STRIKE CALCULATION")
    print(f"{'='*60}")
    print(f"Multiplier Range: {multiplier_min}x - {multiplier_max}x")
    print(f"Using Multiplier: {multiplier}x")
    print(f"Move Distance: ${expected_move:.2f} x {multiplier} = ${move_distance:.2f}")

    # Calculate target strikes
    call_target = spy_price + move_distance
    put_target = spy_price - move_distance

    print(f"\nTarget Strikes (raw):")
    print(f"  Call: ${spy_price:.2f} + ${move_distance:.2f} = ${call_target:.2f}")
    print(f"  Put:  ${spy_price:.2f} - ${move_distance:.2f} = ${put_target:.2f}")

    # Round to nearest strike (SPY has $1 strikes)
    call_strike = round(call_target)
    put_strike = round(put_target)

    print(f"\n{'='*60}")
    print(f"RESULT: SHORT STRANGLE STRIKES")
    print(f"{'='*60}")
    print(f"  SHORT CALL @ ${call_strike:.0f}  (target was ${call_target:.2f})")
    print(f"  SHORT PUT  @ ${put_strike:.0f}  (target was ${put_target:.2f})")
    print(f"\n  Call distance from SPY: ${call_strike - spy_price:.2f} ({((call_strike - spy_price) / spy_price * 100):.2f}%)")
    print(f"  Put distance from SPY:  ${spy_price - put_strike:.2f} ({((spy_price - put_strike) / spy_price * 100):.2f}%)")
    print(f"{'='*60}")

    # Also show what the actual strangle finder would return
    print(f"\n{'='*60}")
    print(f"ACTUAL OPTIONS FROM SAXO")
    print(f"{'='*60}")

    strangle = client.find_strangle_options(
        underlying_uic,
        spy_price,
        expected_move,
        multiplier,
        weekly=True
    )

    if strangle:
        print(f"\nCall Option:")
        print(f"  Strike: ${strangle['call']['strike']:.0f}")
        print(f"  UIC: {strangle['call']['uic']}")
        print(f"  Symbol: {strangle['call'].get('symbol', 'N/A')}")

        print(f"\nPut Option:")
        print(f"  Strike: ${strangle['put']['strike']:.0f}")
        print(f"  UIC: {strangle['put']['uic']}")
        print(f"  Symbol: {strangle['put'].get('symbol', 'N/A')}")

        # Get quotes for these options
        call_quote = client.get_quote(strangle['call']['uic'], "StockOption")
        put_quote = client.get_quote(strangle['put']['uic'], "StockOption")

        if call_quote and put_quote:
            call_bid = call_quote["Quote"].get("Bid", 0)
            call_ask = call_quote["Quote"].get("Ask", 0)
            put_bid = put_quote["Quote"].get("Bid", 0)
            put_ask = put_quote["Quote"].get("Ask", 0)

            print(f"\n{'='*60}")
            print(f"PREMIUM INFORMATION")
            print(f"{'='*60}")
            print(f"\nCall Option Pricing:")
            print(f"  Bid: ${call_bid:.2f}")
            print(f"  Ask: ${call_ask:.2f}")
            print(f"  Mid: ${(call_bid + call_ask) / 2:.2f}")

            print(f"\nPut Option Pricing:")
            print(f"  Bid: ${put_bid:.2f}")
            print(f"  Ask: ${put_ask:.2f}")
            print(f"  Mid: ${(put_bid + put_ask) / 2:.2f}")

            # Calculate premiums (selling at bid)
            call_premium = call_bid * 100  # Per contract
            put_premium = put_bid * 100
            total_premium = call_premium + put_premium

            print(f"\n{'='*60}")
            print(f"PREMIUM YOU WOULD RECEIVE (selling at bid)")
            print(f"{'='*60}")
            print(f"  Call Premium: ${call_premium:.2f}")
            print(f"  Put Premium:  ${put_premium:.2f}")
            print(f"  --------------------------------")
            print(f"  TOTAL PREMIUM: ${total_premium:.2f}")

            # Show protection levels
            print(f"\n{'='*60}")
            print(f"PROTECTION ANALYSIS")
            print(f"{'='*60}")
            print(f"  SPY would need to move to ${strangle['call']['strike']:.0f} (+${strangle['call']['strike'] - spy_price:.2f}) to challenge call")
            print(f"  SPY would need to move to ${strangle['put']['strike']:.0f} (-${spy_price - strangle['put']['strike']:.2f}) to challenge put")
            print(f"\n  Premium collected gives you ${total_premium/100:.2f} points of cushion on each side")
            print(f"  Breakeven on call side: ${strangle['call']['strike'] + total_premium/100:.2f}")
            print(f"  Breakeven on put side:  ${strangle['put']['strike'] - total_premium/100:.2f}")
        else:
            print("\n  Could not get option quotes (market may be closed)")
            print("  Bid/Ask prices only available during market hours")
    else:
        print("  Could not find strangle options (market may be closed)")


if __name__ == "__main__":
    main()
