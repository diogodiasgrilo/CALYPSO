#!/usr/bin/env python3
"""
Calculate NET return for the delta neutral strategy.

Shows exactly what short strangle strikes the bot would select to achieve
the target NET weekly return after accounting for long straddle theta decay.

Usage:
    python scripts/calculate_net_return.py
"""
import sys
import os
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
import json

def main():
    config_path = "bots/delta_neutral/config/config.json"
    with open(config_path, "r") as f:
        config = json.load(f)

    client = SaxoClient(config)
    underlying_uic = config["strategy"]["underlying_uic"]
    target_dte = config["strategy"]["long_straddle_target_dte"]
    target_return_pct = config["strategy"]["weekly_target_return_percent"]

    print("=" * 70)
    print("COMPLETE NET RETURN CALCULATION")
    print("=" * 70)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Target NET Weekly Return: {target_return_pct}%")
    print()

    # Get SPY price
    quote = client.get_quote(underlying_uic, "Stock")
    spy_price = quote.get("Quote", {}).get("Mid", 695)
    print(f"SPY Price: ${spy_price:.2f}")

    # Margin and base premium
    margin = spy_price * 100 * 0.20
    base_premium = margin * (target_return_pct / 100)
    print(f"Margin (20% notional): ${margin:,.2f}")
    print(f"Base premium for {target_return_pct}%: ${base_premium:.2f}")

    # =========================================================================
    # STEP 1: Get Long Straddle Theta
    # =========================================================================
    print("\n" + "-" * 70)
    print("STEP 1: LONG STRADDLE THETA (May 29, 2026)")
    print("-" * 70)

    atm_options = client.find_atm_options(underlying_uic, spy_price, target_dte=target_dte)
    if not atm_options:
        print("ERROR: Could not find ATM options")
        return

    print(f"Strike: ${atm_options['call']['strike']}, Expiry: {atm_options['call']['expiry'][:10]}")

    # Get Greeks
    call_greeks = client.get_option_greeks(atm_options["call"]["uic"])
    put_greeks = client.get_option_greeks(atm_options["put"]["uic"])

    call_theta = abs(call_greeks.get("Theta", 0)) if call_greeks else 0
    put_theta = abs(put_greeks.get("Theta", 0)) if put_greeks else 0
    total_theta = call_theta + put_theta

    daily_cost = total_theta * 100
    weekly_cost = daily_cost * 7

    print(f"\nCall theta: -${call_theta:.4f}/share/day = -${call_theta * 100:.2f}/contract/day")
    print(f"Put theta:  -${put_theta:.4f}/share/day = -${put_theta * 100:.2f}/contract/day")
    print(f"{'─' * 50}")
    print(f"TOTAL:      -${total_theta:.4f}/share/day = -${daily_cost:.2f}/contract/day")
    print(f"\nWEEKLY THETA COST: ${weekly_cost:.2f}")

    # =========================================================================
    # STEP 2: Adjusted Target Premium
    # =========================================================================
    print("\n" + "-" * 70)
    print("STEP 2: ADJUSTED TARGET PREMIUM")
    print("-" * 70)

    adjusted_premium = base_premium + weekly_cost
    print(f"Base premium (for {target_return_pct}%):  ${base_premium:>10.2f}")
    print(f"+ Theta cost to offset:      ${weekly_cost:>10.2f}")
    print(f"{'─' * 40}")
    print(f"ADJUSTED TARGET:             ${adjusted_premium:>10.2f}")

    # =========================================================================
    # STEP 3: Find Short Strangle
    # =========================================================================
    print("\n" + "-" * 70)
    print("STEP 3: SHORT STRANGLE FOR NEXT FRIDAY")
    print("-" * 70)

    strangle = client.find_strangle_by_target_premium(
        underlying_uic, spy_price, adjusted_premium, weekly=True, for_roll=True
    )

    if not strangle:
        print(f"ERROR: Cannot find strikes for ${adjusted_premium:.2f} premium")
        return

    call_strike = strangle["call"]["strike"]
    put_strike = strangle["put"]["strike"]
    gross_premium = strangle.get("total_premium", 0)

    print(f"Expiry: {strangle['call']['expiry'][:10]}")
    print(f"Strikes: ${put_strike:.0f} Put / ${call_strike:.0f} Call")
    print(f"Gross Premium: ${gross_premium:.2f}")

    # =========================================================================
    # FINAL SUMMARY
    # =========================================================================
    net_premium = gross_premium - weekly_cost
    gross_return = (gross_premium / margin) * 100
    net_return = (net_premium / margin) * 100

    print("\n" + "=" * 70)
    print("FINAL SUMMARY - WHAT THE BOT WILL DO")
    print("=" * 70)
    print(f"""
LONG STRADDLE (to be held):
  Buy 1 SPY ${atm_options['call']['strike']:.0f} Call @ May 29, 2026
  Buy 1 SPY ${atm_options['put']['strike']:.0f} Put  @ May 29, 2026
  Weekly theta decay: -${weekly_cost:.2f}

SHORT STRANGLE (to be sold):
  Sell 1 SPY ${call_strike:.0f} Call @ next Friday
  Sell 1 SPY ${put_strike:.0f} Put  @ next Friday
  Premium received: +${gross_premium:.2f}

P&L CALCULATION:
  Gross Premium:     +${gross_premium:>8.2f}
  Theta Cost:        -${weekly_cost:>8.2f}
  {'─' * 25}
  NET Premium:       +${net_premium:>8.2f}

RETURNS:
  Gross Return:       {gross_return:>8.2f}%
  NET Return:         {net_return:>8.2f}%  ← TRUE weekly return
  Target was:         {target_return_pct:>8.2f}%

PROFIT ZONE: ${put_strike:.0f} - ${call_strike:.0f} (${call_strike - put_strike:.0f} points wide)
""")

if __name__ == "__main__":
    main()
