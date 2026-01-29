#!/usr/bin/env python3
"""
Safe Roll Trigger Analysis - Finding strikes where roll trigger lands at 1.0x expected move.

CONCEPT:
Markets overestimate volatility ~85% of the time (IV > RV). The 1.0x expected move
is historically very safe - SPY breaches it only ~15-20% of the time (vs theoretical 32%).

This script finds strikes where the 75% roll trigger lands exactly at 1.0x expected move,
meaning you'd only trigger a roll in ~15-20% of weeks (the rare big moves).

MATH:
- Roll triggers when 75% of cushion is consumed (25% remains)
- current_distance_at_trigger = 0.25 × original_distance
- We want: 0.25 × strike_distance = expected_move
- Therefore: strike_distance = expected_move / 0.25 = 4.0x expected move

This script tests whether 4.0x strikes:
1. Actually exist (SPY has $1 strikes, so they should)
2. Have any meaningful premium
3. What NET return we'd get vs the standard approach

Usage:
    python scripts/safe_roll_analysis.py
"""
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
from shared.config_loader import ConfigLoader


def main():
    config_path = "bots/delta_neutral/config/config.json"
    loader = ConfigLoader(config_path)
    config = loader.load_config()

    client = SaxoClient(config)

    # Authenticate (required for API calls)
    print("Authenticating with Saxo API...")
    if not client.authenticate():
        print("ERROR: Failed to authenticate with Saxo API")
        return

    underlying_uic = config["strategy"]["underlying_uic"]
    target_dte = config["strategy"]["long_straddle_target_dte"]
    position_size = config["strategy"]["position_size"]
    fee_per_leg = config["strategy"].get("short_strangle_entry_fee_per_leg", 2.05)

    print("=" * 70)
    print("SAFE ROLL TRIGGER ANALYSIS")
    print("Roll trigger at 1.0x Expected Move = ~85% weekly success rate")
    print("=" * 70)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Position Size: {position_size} contract(s)")
    print()

    # Get SPY price
    quote = client.get_quote(underlying_uic, "Etf")
    if not quote:
        print("ERROR: Could not get SPY quote")
        return

    spy_price = quote["Quote"].get("Mid") or quote["Quote"].get("LastTraded", 0)
    print(f"SPY Price: ${spy_price:.2f}")

    # =========================================================================
    # EXPECTED MOVE
    # =========================================================================
    print("\n" + "-" * 70)
    print("EXPECTED MOVE (from ATM weekly straddle)")
    print("-" * 70)

    expected_move = client.get_expected_move_from_straddle(
        underlying_uic, spy_price, for_roll=True
    )

    if not expected_move:
        print("ERROR: Could not get expected move")
        return

    print(f"Expected Move: ±${expected_move:.2f}")
    print(f"As % of SPY: ±{(expected_move/spy_price)*100:.2f}%")
    print(f"Expected Range: ${spy_price - expected_move:.2f} - ${spy_price + expected_move:.2f}")

    # =========================================================================
    # LONG STRADDLE COST & THETA
    # =========================================================================
    print("\n" + "-" * 70)
    print("LONG STRADDLE COST & THETA")
    print("-" * 70)

    atm_options = client.find_atm_options(underlying_uic, spy_price, target_dte=target_dte)
    if not atm_options:
        print("ERROR: Could not find ATM options for long straddle")
        return

    # Get quotes for long straddle cost
    call_quote = client.get_quote(atm_options["call"]["uic"], "StockOption")
    put_quote = client.get_quote(atm_options["put"]["uic"], "StockOption")

    call_ask = 0
    put_ask = 0
    if call_quote:
        call_ask = call_quote["Quote"].get("Ask", 0) or call_quote["Quote"].get("LastTraded", 0) or 0
    if put_quote:
        put_ask = put_quote["Quote"].get("Ask", 0) or put_quote["Quote"].get("LastTraded", 0) or 0

    long_straddle_cost = (call_ask + put_ask) * 100 * position_size

    print(f"Long Straddle: ${atm_options['call']['strike']:.0f} @ {atm_options['call']['expiry'][:10]}")
    print(f"Long Straddle Cost: ${long_straddle_cost:,.2f} ({position_size} contract(s))")

    call_greeks = client.get_option_greeks(atm_options["call"]["uic"])
    put_greeks = client.get_option_greeks(atm_options["put"]["uic"])

    call_theta = abs(call_greeks.get("Theta", 0)) if call_greeks else 0
    put_theta = abs(put_greeks.get("Theta", 0)) if put_greeks else 0
    daily_theta = (call_theta + put_theta) * 100 * position_size
    weekly_theta = daily_theta * 7

    # Round-trip fees (entry + exit)
    total_round_trip_fees = fee_per_leg * 2 * position_size * 2

    print(f"Daily Theta: ${daily_theta:.2f}/day")
    print(f"Weekly Theta (7 days): ${weekly_theta:.2f}")
    print(f"Round-Trip Fees: ${total_round_trip_fees:.2f}")

    total_costs = weekly_theta + total_round_trip_fees

    # =========================================================================
    # SAFE ROLL CALCULATION
    # =========================================================================
    print("\n" + "-" * 70)
    print("SAFE ROLL TRIGGER MATH")
    print("-" * 70)

    # The key insight: we want roll trigger to land at 1.0x expected move
    # Roll triggers when 75% consumed (25% remains)
    # 0.25 × original_distance = 1.0 × expected_move
    # original_distance = 4.0 × expected_move

    safe_multiplier = 4.0
    safe_distance = expected_move * safe_multiplier
    roll_trigger_distance = expected_move  # This is where 75% consumed lands

    print(f"""
To have roll trigger at 1.0x expected move:
  - Roll triggers when 75% cushion consumed (25% remains)
  - 25% of original distance = 1.0x expected move
  - Therefore: original distance = 4.0x expected move

  Expected Move: ±${expected_move:.2f}
  Required Strike Distance: ±${safe_distance:.2f} (4.0x)

  This means:
  - Put strike at: ${spy_price:.2f} - ${safe_distance:.2f} = ${spy_price - safe_distance:.2f}
  - Call strike at: ${spy_price:.2f} + ${safe_distance:.2f} = ${spy_price + safe_distance:.2f}

  Roll would trigger when SPY reaches:
  - Put side: ${spy_price - roll_trigger_distance:.2f} (1.0x below entry)
  - Call side: ${spy_price + roll_trigger_distance:.2f} (1.0x above entry)
""")

    # =========================================================================
    # COMPARE: Standard vs Safe Approach
    # =========================================================================
    print("=" * 70)
    print("COMPARISON: Standard Bot Logic vs Safe Roll Approach")
    print("=" * 70)

    # Standard approach - what the bot currently selects
    max_mult = config["strategy"].get("short_strangle_multiplier_max", 2.0)
    min_mult = config["strategy"].get("short_strangle_multiplier_min", 1.0)
    target_return_pct = config["strategy"].get("weekly_target_return_percent", 1.0)

    # Test both approaches
    results = {}

    # Multipliers to test: standard range + safe approach
    test_cases = [
        ("CURRENT BOT (1.0x min)", 1.0, 2.0),
        ("SAFE ROLL (4.0x)", 4.0, 4.0),
        ("SAFER VARIANT (3.0x)", 3.0, 3.0),  # Roll at 0.75x EM
        ("SAFEST (5.0x)", 5.0, 5.0),  # Roll at 1.25x EM
    ]

    print(f"\nTesting strike selection at different multipliers...\n")

    for name, test_mult_min, test_mult_max in test_cases:
        print(f"Testing {name}...")

        strangle = client.find_strangle_options(
            underlying_uic, spy_price, expected_move, test_mult_min,
            weekly=True, for_roll=True
        )

        if not strangle:
            print(f"  ⚠ Could not find options at {test_mult_min}x - strikes may not exist")
            results[name] = None
            continue

        call_q = client.get_quote(strangle["call"]["uic"], "StockOption")
        put_q = client.get_quote(strangle["put"]["uic"], "StockOption")

        if not call_q or not put_q:
            print(f"  ⚠ Could not get quotes for {test_mult_min}x strikes")
            results[name] = None
            continue

        call_bid = call_q["Quote"].get("Bid", 0) or call_q["Quote"].get("LastTraded", 0) or 0
        put_bid = put_q["Quote"].get("Bid", 0) or put_q["Quote"].get("LastTraded", 0) or 0

        if call_bid <= 0 or put_bid <= 0:
            print(f"  ⚠ Zero premium at {test_mult_min}x - options too far OTM")
            results[name] = None
            continue

        call_strike = strangle["call"]["strike"]
        put_strike = strangle["put"]["strike"]

        gross = (call_bid + put_bid) * 100 * position_size
        net = gross - total_costs
        net_return = (net / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0

        # Calculate actual multiplier from strikes
        actual_call_mult = (call_strike - spy_price) / expected_move
        actual_put_mult = (spy_price - put_strike) / expected_move

        # Calculate where roll would trigger
        call_roll_trigger = spy_price + (call_strike - spy_price) * 0.75
        put_roll_trigger = spy_price - (spy_price - put_strike) * 0.75

        results[name] = {
            "call_strike": call_strike,
            "put_strike": put_strike,
            "call_bid": call_bid,
            "put_bid": put_bid,
            "gross": gross,
            "net": net,
            "return": net_return,
            "actual_call_mult": actual_call_mult,
            "actual_put_mult": actual_put_mult,
            "call_roll_trigger": call_roll_trigger,
            "put_roll_trigger": put_roll_trigger,
        }

        print(f"  ✓ Found: Put ${put_strike:.0f} / Call ${call_strike:.0f}")

    # =========================================================================
    # RESULTS COMPARISON
    # =========================================================================
    print("\n" + "=" * 70)
    print("RESULTS COMPARISON")
    print("=" * 70)

    # First, get the actual current bot selection by scanning
    print("\n[Finding current bot selection by scanning from 2.0x to 1.0x...]")
    test_multipliers = []
    mult = max_mult
    while mult >= min_mult:
        test_multipliers.append(round(mult, 2))
        mult -= 0.01

    current_bot = None
    for mult in test_multipliers:
        strangle = client.find_strangle_options(
            underlying_uic, spy_price, expected_move, mult,
            weekly=True, for_roll=True
        )
        if not strangle:
            continue

        call_q = client.get_quote(strangle["call"]["uic"], "StockOption")
        put_q = client.get_quote(strangle["put"]["uic"], "StockOption")

        if not call_q or not put_q:
            continue

        call_bid = call_q["Quote"].get("Bid", 0) or call_q["Quote"].get("LastTraded", 0) or 0
        put_bid = put_q["Quote"].get("Bid", 0) or put_q["Quote"].get("LastTraded", 0) or 0

        if call_bid <= 0 or put_bid <= 0:
            continue

        call_strike = strangle["call"]["strike"]
        put_strike = strangle["put"]["strike"]

        gross = (call_bid + put_bid) * 100 * position_size
        net = gross - total_costs
        net_return = (net / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0

        if net_return >= target_return_pct:
            actual_call_mult = (call_strike - spy_price) / expected_move
            actual_put_mult = (spy_price - put_strike) / expected_move

            call_roll_trigger = spy_price + (call_strike - spy_price) * 0.75
            put_roll_trigger = spy_price - (spy_price - put_strike) * 0.75

            current_bot = {
                "mult": mult,
                "call_strike": call_strike,
                "put_strike": put_strike,
                "call_bid": call_bid,
                "put_bid": put_bid,
                "gross": gross,
                "net": net,
                "return": net_return,
                "actual_call_mult": actual_call_mult,
                "actual_put_mult": actual_put_mult,
                "call_roll_trigger": call_roll_trigger,
                "put_roll_trigger": put_roll_trigger,
            }
            print(f"Current bot selects: {mult:.2f}x = Put ${put_strike:.0f} / Call ${call_strike:.0f}")
            break

    # Display comparison table
    print(f"""
╔══════════════════════════════════════════════════════════════════════════╗
║                      STRIKE SELECTION COMPARISON                          ║
╠════════════════════╦═══════════╦═══════════╦══════════╦══════════════════╣
║ Approach           ║ Put       ║ Call      ║ NET Ret  ║ Roll Trigger     ║
╠════════════════════╬═══════════╬═══════════╬══════════╬══════════════════╣""")

    if current_bot:
        put_roll_dist = (spy_price - current_bot["put_roll_trigger"]) / expected_move
        call_roll_dist = (current_bot["call_roll_trigger"] - spy_price) / expected_move
        print(f"""║ Current Bot        ║ ${current_bot['put_strike']:>7.0f} ║ ${current_bot['call_strike']:>7.0f} ║ {current_bot['return']:>7.2f}% ║ ±{put_roll_dist:.2f}x EM          ║""")

    for name, data in results.items():
        if data:
            put_roll_dist = (spy_price - data["put_roll_trigger"]) / expected_move
            call_roll_dist = (data["call_roll_trigger"] - spy_price) / expected_move
            short_name = name[:18].ljust(18)
            print(f"""║ {short_name} ║ ${data['put_strike']:>7.0f} ║ ${data['call_strike']:>7.0f} ║ {data['return']:>7.2f}% ║ ±{put_roll_dist:.2f}x EM          ║""")
        else:
            short_name = name[:18].ljust(18)
            print(f"""║ {short_name} ║    N/A   ║    N/A   ║    N/A   ║ N/A              ║""")

    print("""╚════════════════════╩═══════════╩═══════════╩══════════╩══════════════════╝""")

    # =========================================================================
    # KEY INSIGHT
    # =========================================================================
    print("\n" + "=" * 70)
    print("KEY INSIGHT")
    print("=" * 70)

    safe_result = results.get("SAFE ROLL (4.0x)")

    if current_bot and safe_result:
        return_diff = current_bot["return"] - safe_result["return"]
        print(f"""
TRADE-OFF ANALYSIS:

Current Bot ({current_bot['mult']:.2f}x):
  - NET Return: {current_bot['return']:.2f}%
  - Roll triggers at: ~{(spy_price - current_bot['put_roll_trigger']) / expected_move:.2f}x expected move
  - Estimated weekly roll probability: ~{30 + (2.0 - current_bot['mult']) * 15:.0f}%

Safe Roll (4.0x):
  - NET Return: {safe_result['return']:.2f}%
  - Roll triggers at: ~1.0x expected move
  - Estimated weekly roll probability: ~15-20%

Return Sacrifice: {return_diff:.2f}% per week for {30 + (2.0 - current_bot['mult']) * 15 - 17:.0f}% fewer rolls

QUESTION: Is {return_diff:.2f}% weekly return worth the extra roll risk?

If the 4.0x approach yields NEGATIVE return, it's not viable - the premium
is too low to cover theta + fees. You'd need to find a middle ground.
""")
    elif not safe_result:
        print("""
⚠ IMPORTANT FINDING:

The 4.0x multiplier approach does NOT work with current market conditions!

Either:
1. No strikes exist that far OTM (unlikely for SPY)
2. Premium is effectively zero ($0.01 or less)
3. The NET return is deeply negative after theta + fees

This means the "pure safe" approach isn't viable. Consider:
- Finding the MINIMUM multiplier that still yields positive NET return
- That's the safest viable approach
""")

    # =========================================================================
    # FIND MINIMUM VIABLE MULTIPLIER
    # =========================================================================
    print("\n" + "-" * 70)
    print("MINIMUM VIABLE MULTIPLIER (where NET return > 0)")
    print("-" * 70)

    # Scan from high to low to find minimum positive return
    test_mults = [5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0, 1.75, 1.5, 1.25, 1.0]

    for mult in test_mults:
        strangle = client.find_strangle_options(
            underlying_uic, spy_price, expected_move, mult,
            weekly=True, for_roll=True
        )

        if not strangle:
            print(f"  {mult:.2f}x: No strikes found")
            continue

        call_q = client.get_quote(strangle["call"]["uic"], "StockOption")
        put_q = client.get_quote(strangle["put"]["uic"], "StockOption")

        if not call_q or not put_q:
            continue

        call_bid = call_q["Quote"].get("Bid", 0) or call_q["Quote"].get("LastTraded", 0) or 0
        put_bid = put_q["Quote"].get("Bid", 0) or put_q["Quote"].get("LastTraded", 0) or 0

        if call_bid <= 0 or put_bid <= 0:
            print(f"  {mult:.2f}x: Zero premium")
            continue

        call_strike = strangle["call"]["strike"]
        put_strike = strangle["put"]["strike"]

        gross = (call_bid + put_bid) * 100 * position_size
        net = gross - total_costs
        net_return = (net / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0

        # Roll trigger distance
        roll_trigger_mult = mult * 0.75

        status = "✓" if net_return > 0 else "✗"
        print(f"  {status} {mult:.2f}x: Put ${put_strike:.0f} / Call ${call_strike:.0f} | "
              f"NET {net_return:+.2f}% | Roll @ {roll_trigger_mult:.2f}x EM")

    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    print("""
Use the minimum viable multiplier (first one with positive NET return)
to maximize safety while still making money.

REMEMBER:
- 1.0x expected move breach probability: ~15-20% (due to IV > RV)
- 1.5x expected move breach probability: ~5-8%
- 2.0x expected move breach probability: ~2-3%

Your roll trigger lands at: multiplier × 0.75
So 4.0x strikes = roll at 3.0x EM = almost never rolls (~0.5%)
   2.0x strikes = roll at 1.5x EM = rarely rolls (~5-8%)
   1.33x strikes = roll at 1.0x EM = occasionally rolls (~15-20%)
""")


if __name__ == "__main__":
    main()
