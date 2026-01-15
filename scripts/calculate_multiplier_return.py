#!/usr/bin/env python3
"""
Calculate NET return for short strangle at a specific expected move multiplier.

Shows exactly what you would make if placing shorts at Nx expected move,
accounting for long straddle theta decay.

Usage:
    python scripts/calculate_multiplier_return.py [multiplier]

Examples:
    python scripts/calculate_multiplier_return.py 1.5   # 1.5x expected move
    python scripts/calculate_multiplier_return.py 2.0   # 2.0x expected move
"""
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
import json


def main():
    # Get multiplier from command line or default to 1.5
    multiplier = 1.5
    if len(sys.argv) > 1:
        try:
            multiplier = float(sys.argv[1])
        except ValueError:
            print(f"Usage: {sys.argv[0]} [multiplier]")
            print(f"Example: {sys.argv[0]} 1.5")
            return

    # Load config
    config_path = "bots/delta_neutral/config/config.json"
    with open(config_path, "r") as f:
        config = json.load(f)

    client = SaxoClient(config)
    underlying_uic = config["strategy"]["underlying_uic"]
    target_dte = config["strategy"]["long_straddle_target_dte"]

    print("=" * 70)
    print(f"NET RETURN AT {multiplier}x EXPECTED MOVE")
    print("=" * 70)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    # Get SPY price
    quote = client.get_quote(underlying_uic, "Etf")
    if not quote:
        print("ERROR: Could not get SPY quote")
        return

    spy_price = quote["Quote"].get("Mid") or quote["Quote"].get("LastTraded", 0)
    print(f"SPY Price: ${spy_price:.2f}")

    # Calculate margin (20% of notional)
    margin = spy_price * 100 * 0.20
    print(f"Margin (20% notional): ${margin:,.2f}")

    # =========================================================================
    # STEP 1: Get Expected Move from ATM Straddle
    # =========================================================================
    print("\n" + "-" * 70)
    print("STEP 1: EXPECTED MOVE (from ATM straddle)")
    print("-" * 70)

    expected_move = client.get_expected_move_from_straddle(
        underlying_uic,
        spy_price,
        target_dte_min=0,
        target_dte_max=7,
        for_roll=True  # Next week's expiration
    )

    if not expected_move:
        print("ERROR: Could not get expected move from ATM straddle")
        print("Market may be closed. Try again during market hours.")
        return

    print(f"Expected Move (ATM straddle): ±${expected_move:.2f}")
    print(f"As % of SPY: ±{(expected_move / spy_price) * 100:.2f}%")

    # =========================================================================
    # STEP 2: Calculate Strike Distances
    # =========================================================================
    print("\n" + "-" * 70)
    print(f"STEP 2: STRIKES AT {multiplier}x EXPECTED MOVE")
    print("-" * 70)

    strike_distance = expected_move * multiplier
    call_strike = round(spy_price + strike_distance)
    put_strike = round(spy_price - strike_distance)

    print(f"Strike Distance: ${expected_move:.2f} x {multiplier} = ${strike_distance:.2f}")
    print(f"Call Strike: ${spy_price:.2f} + ${strike_distance:.2f} = ${call_strike}")
    print(f"Put Strike:  ${spy_price:.2f} - ${strike_distance:.2f} = ${put_strike}")

    # =========================================================================
    # STEP 3: Find Actual Options and Get Premium
    # =========================================================================
    print("\n" + "-" * 70)
    print("STEP 3: OPTION PRICES")
    print("-" * 70)

    strangle = client.find_strangle_options(
        underlying_uic,
        spy_price,
        expected_move,
        multiplier,
        weekly=True,
        for_roll=True
    )

    if not strangle:
        print("ERROR: Could not find strangle options")
        print("Market may be closed or strikes not available.")
        return

    actual_call_strike = strangle["call"]["strike"]
    actual_put_strike = strangle["put"]["strike"]

    print(f"Expiry: {strangle['call']['expiry'][:10]}")
    print(f"Call Strike: ${actual_call_strike:.0f}")
    print(f"Put Strike:  ${actual_put_strike:.0f}")

    # Get quotes
    call_quote = client.get_quote(strangle["call"]["uic"], "StockOption")
    put_quote = client.get_quote(strangle["put"]["uic"], "StockOption")

    if not call_quote or not put_quote:
        print("ERROR: Could not get option quotes (market may be closed)")
        return

    call_bid = call_quote["Quote"].get("Bid", 0)
    put_bid = put_quote["Quote"].get("Bid", 0)

    if call_bid == 0 or put_bid == 0:
        print("WARNING: Bid prices are 0 (market closed). Using mid prices as estimate.")
        call_bid = call_quote["Quote"].get("Mid", 0) or (call_quote["Quote"].get("Ask", 0) * 0.9)
        put_bid = put_quote["Quote"].get("Mid", 0) or (put_quote["Quote"].get("Ask", 0) * 0.9)

    call_premium = call_bid * 100
    put_premium = put_bid * 100
    gross_premium = call_premium + put_premium

    print(f"\nCall Bid: ${call_bid:.2f} → ${call_premium:.2f} per contract")
    print(f"Put Bid:  ${put_bid:.2f} → ${put_premium:.2f} per contract")
    print(f"{'─' * 40}")
    print(f"GROSS PREMIUM: ${gross_premium:.2f}")

    # =========================================================================
    # STEP 4: Get Long Straddle Theta
    # =========================================================================
    print("\n" + "-" * 70)
    print("STEP 4: LONG STRADDLE THETA COST")
    print("-" * 70)

    atm_options = client.find_atm_options(underlying_uic, spy_price, target_dte=target_dte)
    if not atm_options:
        print("ERROR: Could not find long straddle ATM options")
        return

    print(f"Long Straddle: ${atm_options['call']['strike']:.0f} @ {atm_options['call']['expiry'][:10]}")

    call_greeks = client.get_option_greeks(atm_options["call"]["uic"])
    put_greeks = client.get_option_greeks(atm_options["put"]["uic"])

    call_theta = abs(call_greeks.get("Theta", 0)) if call_greeks else 0
    put_theta = abs(put_greeks.get("Theta", 0)) if put_greeks else 0
    total_theta = call_theta + put_theta

    daily_theta_cost = total_theta * 100
    weekly_theta_cost = daily_theta_cost * 7

    print(f"\nCall theta: -${call_theta:.4f}/share/day = -${call_theta * 100:.2f}/contract/day")
    print(f"Put theta:  -${put_theta:.4f}/share/day = -${put_theta * 100:.2f}/contract/day")
    print(f"{'─' * 40}")
    print(f"Daily Theta Cost:  ${daily_theta_cost:.2f}")
    print(f"WEEKLY THETA COST: ${weekly_theta_cost:.2f}")

    # =========================================================================
    # FINAL CALCULATION
    # =========================================================================
    net_premium = gross_premium - weekly_theta_cost
    gross_return = (gross_premium / margin) * 100
    net_return = (net_premium / margin) * 100

    print("\n" + "=" * 70)
    print(f"FINAL RESULTS - {multiplier}x EXPECTED MOVE")
    print("=" * 70)

    print(f"""
SHORT STRANGLE:
  Sell ${actual_call_strike:.0f} Call @ ${call_bid:.2f}
  Sell ${actual_put_strike:.0f} Put  @ ${put_bid:.2f}

PREMIUM CALCULATION:
  Gross Premium (shorts):  +${gross_premium:>8.2f}
  Theta Cost (longs):      -${weekly_theta_cost:>8.2f}
  {'─' * 30}
  NET PREMIUM:             +${net_premium:>8.2f}

RETURNS (on ${margin:,.2f} margin):
  Gross Return:             {gross_return:>8.2f}%
  NET Return:               {net_return:>8.2f}%  ← TRUE weekly profit

PROFIT ZONE:
  SPY can move from ${actual_put_strike:.0f} to ${actual_call_strike:.0f}
  Width: ${actual_call_strike - actual_put_strike:.0f} points ({((actual_call_strike - actual_put_strike) / spy_price) * 100:.1f}% of SPY)

BREAKEVEN POINTS:
  Upper: ${actual_call_strike + gross_premium/100:.2f} (call strike + premium)
  Lower: ${actual_put_strike - gross_premium/100:.2f} (put strike - premium)
""")

    # Compare different multipliers
    print("=" * 70)
    print("COMPARISON: DIFFERENT MULTIPLIERS")
    print("=" * 70)
    print(f"{'Mult':>6} {'Strikes':>15} {'Gross':>10} {'Theta':>10} {'NET':>10} {'Return':>10}")
    print("-" * 70)

    for mult in [1.0, 1.25, 1.5, 1.75, 2.0]:
        s = client.find_strangle_options(
            underlying_uic, spy_price, expected_move, mult,
            weekly=True, for_roll=True
        )
        if s:
            cq = client.get_quote(s["call"]["uic"], "StockOption")
            pq = client.get_quote(s["put"]["uic"], "StockOption")
            if cq and pq:
                cb = cq["Quote"].get("Bid", 0) or cq["Quote"].get("Mid", 0)
                pb = pq["Quote"].get("Bid", 0) or pq["Quote"].get("Mid", 0)
                gp = (cb + pb) * 100
                np = gp - weekly_theta_cost
                nr = (np / margin) * 100
                marker = " ←" if mult == multiplier else ""
                print(f"{mult:>6.2f}x  {s['put']['strike']:.0f}/{s['call']['strike']:.0f}  "
                      f"${gp:>8.2f}  ${weekly_theta_cost:>8.2f}  ${np:>8.2f}  {nr:>8.2f}%{marker}")


if __name__ == "__main__":
    main()
