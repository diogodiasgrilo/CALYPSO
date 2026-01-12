#!/usr/bin/env python3
"""
Calculate short strangle strikes based on target weekly return.

Instead of using a multiplier on expected move, this script:
1. Takes your target weekly return (e.g., 1%)
2. Calculates the premium needed to achieve that return
3. Finds the strikes that would provide that premium
4. Shows you exactly where to place your shorts
"""

import sys
import os
import time
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config_loader import ConfigLoader
from src.saxo_client import SaxoClient


def get_option_chain_with_prices(client, underlying_uic, expiration_data, spy_price):
    """
    Get options for an expiration with their bid prices.
    Only fetches options within a reasonable range of current price to avoid rate limits.
    """
    specific_options = expiration_data.get("SpecificOptions", [])

    calls = []
    puts = []

    # Only fetch options within 5% of current price to reduce API calls
    min_strike = spy_price * 0.95
    max_strike = spy_price * 1.05

    # Filter and sort options by strike
    relevant_options = [
        opt for opt in specific_options
        if opt.get("StrikePrice", 0) >= min_strike and opt.get("StrikePrice", 0) <= max_strike
    ]

    print(f"  Fetching prices for {len(relevant_options)} options (within 5% of SPY)...")

    for i, option in enumerate(relevant_options):
        strike = option.get("StrikePrice", 0)
        uic = option.get("Uic")
        put_call = option.get("PutCall")

        if not uic or not strike:
            continue

        # Add small delay to avoid rate limiting
        if i > 0 and i % 5 == 0:
            time.sleep(0.5)

        # Get quote for this option
        quote = client.get_quote(uic, "StockOption")
        if not quote:
            continue

        bid = quote["Quote"].get("Bid", 0)
        ask = quote["Quote"].get("Ask", 0)

        option_data = {
            "strike": strike,
            "uic": uic,
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2 if bid and ask else 0
        }

        if put_call == "Call":
            calls.append(option_data)
        elif put_call == "Put":
            puts.append(option_data)

    # Sort by strike
    calls.sort(key=lambda x: x["strike"])
    puts.sort(key=lambda x: x["strike"])

    return calls, puts


def find_strikes_for_target_premium(calls, puts, spy_price, target_premium_per_contract):
    """
    Find the best strike combination that meets the target premium.

    Strategy: Start from far OTM and move inward until we hit target premium.
    This gives us the safest strikes that still meet our return goal.
    """
    best_combination = None
    best_total_premium = 0

    # Filter to OTM options only
    otm_calls = [c for c in calls if c["strike"] > spy_price and c["bid"] > 0]
    otm_puts = [p for p in puts if p["strike"] < spy_price and p["bid"] > 0]

    if not otm_calls or not otm_puts:
        return None

    # Sort calls ascending (closest to ATM first), puts descending (closest to ATM first)
    otm_calls.sort(key=lambda x: x["strike"])
    otm_puts.sort(key=lambda x: x["strike"], reverse=True)

    # Try combinations starting from furthest OTM and moving inward
    for call in reversed(otm_calls):  # Start from furthest OTM call
        for put in reversed(otm_puts):  # Start from furthest OTM put
            total_premium = (call["bid"] + put["bid"]) * 100  # Per contract

            if total_premium >= target_premium_per_contract:
                # Found a valid combination - is it better (further OTM) than current best?
                call_distance = call["strike"] - spy_price
                put_distance = spy_price - put["strike"]
                min_distance = min(call_distance, put_distance)

                if best_combination is None:
                    best_combination = {
                        "call": call,
                        "put": put,
                        "total_premium": total_premium,
                        "min_distance": min_distance
                    }
                    best_total_premium = total_premium
                elif min_distance > best_combination["min_distance"]:
                    # This combination is further OTM while still meeting target
                    best_combination = {
                        "call": call,
                        "put": put,
                        "total_premium": total_premium,
                        "min_distance": min_distance
                    }
                    best_total_premium = total_premium

    return best_combination


def main():
    # Load config
    config_path = "config/config.json"
    loader = ConfigLoader(config_path)
    config = loader.load_config()

    # Get target return from config or command line
    target_return_pct = config["strategy"].get("weekly_target_return_percent", 1.0)

    # Allow override from command line
    if len(sys.argv) > 1:
        try:
            target_return_pct = float(sys.argv[1])
        except ValueError:
            print(f"Usage: {sys.argv[0]} [target_return_percent]")
            print(f"Example: {sys.argv[0]} 1.5  # for 1.5% weekly target")
            return

    # Initialize client
    client = SaxoClient(config)

    # Authenticate
    print("Authenticating with Saxo...")
    if not client.authenticate():
        print("ERROR: Failed to authenticate")
        return

    # Get current prices
    underlying_uic = config["strategy"]["underlying_uic"]
    vix_uic = config["strategy"]["vix_uic"]

    spy_quote = client.get_quote(underlying_uic, "Etf")
    vix_quote = client.get_quote(vix_uic, "StockIndex")

    if not spy_quote or not vix_quote:
        print("ERROR: Failed to get quotes")
        return

    spy_price = spy_quote["Quote"].get("Mid") or spy_quote["Quote"].get("LastTraded", 0)
    vix_value = vix_quote["Quote"].get("Mid") or vix_quote["Quote"].get("LastTraded", 0)

    if vix_value == 0:
        vix_value = vix_quote["Quote"].get("LastTraded", 0)
    if vix_value == 0:
        vix_value = 15.0
        print(f"\nNOTE: VIX quote unavailable (after hours), using default: {vix_value}")

    print(f"\n{'='*70}")
    print(f"TARGET-BASED STRIKE CALCULATOR")
    print(f"{'='*70}")
    print(f"Current Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"SPY Price: ${spy_price:.2f}")
    print(f"VIX: {vix_value:.2f}")
    print(f"Target Weekly Return: {target_return_pct}%")

    # Calculate margin requirement (approximate)
    # For a short strangle, margin is roughly 20% of underlying + premium received
    # We'll use a simplified estimate based on SPY price
    margin_per_contract = spy_price * 100 * 0.20  # ~20% of notional

    # Calculate target premium
    target_premium = margin_per_contract * (target_return_pct / 100)

    print(f"\n{'='*70}")
    print(f"PREMIUM CALCULATION")
    print(f"{'='*70}")
    print(f"Estimated margin per strangle: ${margin_per_contract:.2f}")
    print(f"Target return: {target_return_pct}%")
    print(f"Required premium: ${target_premium:.2f}")
    print(f"  (That's ${target_premium/100:.2f} total from call + put bids)")

    # Get weekly expiration
    expirations = client.get_option_expirations(underlying_uic)
    if not expirations:
        print("ERROR: Failed to get expirations")
        return

    today = datetime.now().date()
    weekly_exp = None
    weekly_dte = 7

    for exp_data in expirations:
        exp_date_str = exp_data.get("Expiry")
        if exp_date_str:
            exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if 0 < dte <= 7:
                weekly_exp = exp_data
                weekly_dte = dte
                print(f"\nWeekly Expiry: {exp_date_str[:10]} ({dte} DTE)")
                break

    if not weekly_exp:
        print("ERROR: No weekly expiration found")
        return

    # Get option chain with prices
    print(f"\nFetching option chain prices (this may take a moment)...")
    calls, puts = get_option_chain_with_prices(client, underlying_uic, weekly_exp, spy_price)

    if not calls or not puts:
        print("ERROR: Could not get option prices (market may be closed)")
        return

    print(f"Found {len(calls)} calls and {len(puts)} puts with prices")

    # Find strikes that meet target
    result = find_strikes_for_target_premium(calls, puts, spy_price, target_premium)

    if not result:
        print(f"\n{'='*70}")
        print(f"NO VALID STRIKES FOUND")
        print(f"{'='*70}")
        print(f"Could not find strikes that provide ${target_premium:.2f} premium.")
        print(f"You may need to:")
        print(f"  1. Lower your target return")
        print(f"  2. Wait for higher VIX (more premium available)")
        print(f"  3. Accept tighter strikes (higher risk)")

        # Show what's available at ATM
        print(f"\nClosest strikes available:")
        atm_calls = [c for c in calls if c["strike"] > spy_price and c["bid"] > 0][:3]
        atm_puts = [p for p in puts if p["strike"] < spy_price and p["bid"] > 0][-3:]

        for c in atm_calls:
            print(f"  Call ${c['strike']:.0f}: bid ${c['bid']:.2f}")
        for p in reversed(atm_puts):
            print(f"  Put ${p['strike']:.0f}: bid ${p['bid']:.2f}")
        return

    call = result["call"]
    put = result["put"]
    total_premium = result["total_premium"]

    print(f"\n{'='*70}")
    print(f"RECOMMENDED STRIKES FOR {target_return_pct}% WEEKLY TARGET")
    print(f"{'='*70}")

    print(f"\n  SHORT CALL @ ${call['strike']:.0f}")
    print(f"    Bid: ${call['bid']:.2f}")
    print(f"    Distance from SPY: +${call['strike'] - spy_price:.2f} ({(call['strike'] - spy_price) / spy_price * 100:.2f}%)")

    print(f"\n  SHORT PUT @ ${put['strike']:.0f}")
    print(f"    Bid: ${put['bid']:.2f}")
    print(f"    Distance from SPY: -${spy_price - put['strike']:.2f} ({(spy_price - put['strike']) / spy_price * 100:.2f}%)")

    print(f"\n{'='*70}")
    print(f"PREMIUM & RETURN ANALYSIS")
    print(f"{'='*70}")
    print(f"  Call premium: ${call['bid'] * 100:.2f}")
    print(f"  Put premium:  ${put['bid'] * 100:.2f}")
    print(f"  --------------------------------")
    print(f"  TOTAL PREMIUM: ${total_premium:.2f}")
    print(f"")
    print(f"  Margin required: ~${margin_per_contract:.2f}")
    print(f"  Actual return: {(total_premium / margin_per_contract) * 100:.2f}%")
    print(f"  Target was: {target_return_pct}%")

    # Calculate expected move for comparison
    iv = vix_value / 100
    import math
    expected_move = spy_price * iv * math.sqrt(weekly_dte / 365)

    call_distance = call['strike'] - spy_price
    put_distance = spy_price - put['strike']

    print(f"\n{'='*70}")
    print(f"RISK ANALYSIS")
    print(f"{'='*70}")
    print(f"  Expected move ({weekly_dte} DTE): ${expected_move:.2f}")
    print(f"  Call is {call_distance / expected_move:.2f}x expected move away")
    print(f"  Put is {put_distance / expected_move:.2f}x expected move away")
    print(f"")
    print(f"  Breakeven on call side: ${call['strike'] + total_premium/100:.2f}")
    print(f"  Breakeven on put side:  ${put['strike'] - total_premium/100:.2f}")

    # Show alternative targets
    print(f"\n{'='*70}")
    print(f"ALTERNATIVE TARGETS (for reference)")
    print(f"{'='*70}")

    for alt_pct in [0.5, 0.75, 1.0, 1.5, 2.0]:
        if alt_pct == target_return_pct:
            continue
        alt_premium = margin_per_contract * (alt_pct / 100)
        alt_result = find_strikes_for_target_premium(calls, puts, spy_price, alt_premium)
        if alt_result:
            print(f"  {alt_pct}% target: Call ${alt_result['call']['strike']:.0f} / Put ${alt_result['put']['strike']:.0f} = ${alt_result['total_premium']:.2f} premium")
        else:
            print(f"  {alt_pct}% target: No valid strikes found")


if __name__ == "__main__":
    main()
