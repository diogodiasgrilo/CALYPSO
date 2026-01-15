#!/usr/bin/env python3
"""
Calculate Short Strangle Strikes for 1% NET Return on Long Straddle Cost.

Brian's strategy targets 1% NET weekly return based on the total cost of
the long straddle position, NOT on margin.

This script:
1. Uses your actual long straddle cost as the basis
2. Calculates required gross premium (target + theta + fees)
3. Finds the widest (safest) strikes that meet the target
4. Enforces minimum 1x expected move on both sides

Usage:
    python scripts/calculate_1pct_target.py

Optional: Override long straddle cost from command line
    python scripts/calculate_1pct_target.py 3500
"""
import sys
import os
from datetime import datetime

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

    # =========================================================================
    # CONFIGURATION
    # =========================================================================

    # Long straddle cost (your actual position or fetched live)
    # Default from your last position: $19.78 call + $15.58 put = $35.36/share
    DEFAULT_LONG_CALL_PRICE = 19.78
    DEFAULT_LONG_PUT_PRICE = 15.58

    # Override from command line if provided
    if len(sys.argv) > 1:
        try:
            total_long_cost = float(sys.argv[1])
        except ValueError:
            print(f"Usage: {sys.argv[0]} [long_straddle_cost]")
            return
    else:
        total_long_cost = (DEFAULT_LONG_CALL_PRICE + DEFAULT_LONG_PUT_PRICE) * 100

    # Target return percentage
    TARGET_RETURN_PCT = 1.0

    # Entry fees per leg (Saxo approximate)
    ENTRY_FEE_PER_LEG = 2.00

    # Minimum expected move multiplier (safety floor)
    MIN_MULTIPLIER = 1.0

    # =========================================================================
    # GET MARKET DATA
    # =========================================================================

    print("=" * 70)
    print("1% NET WEEKLY RETURN CALCULATOR")
    print("(Based on Long Straddle Cost)")
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

    # Get expected move from ATM straddle
    expected_move = client.get_expected_move_from_straddle(
        underlying_uic, spy_price, for_roll=True
    )
    if not expected_move:
        print("ERROR: Could not get expected move")
        return

    print(f"Expected Move: +/- ${expected_move:.2f} ({(expected_move/spy_price)*100:.2f}%)")

    # Get theta from long straddle
    atm_options = client.find_atm_options(underlying_uic, spy_price, target_dte=target_dte)
    if atm_options:
        call_greeks = client.get_option_greeks(atm_options["call"]["uic"])
        put_greeks = client.get_option_greeks(atm_options["put"]["uic"])
        call_theta = abs(call_greeks.get("Theta", 0)) if call_greeks else 0
        put_theta = abs(put_greeks.get("Theta", 0)) if put_greeks else 0
        weekly_theta = (call_theta + put_theta) * 100 * 7
        print(f"Long Straddle: ${atm_options['call']['strike']:.0f} @ {atm_options['call']['expiry'][:10]}")
    else:
        weekly_theta = 140.00  # Fallback estimate
        print("Long Straddle: Using estimated theta")

    print(f"Weekly Theta Cost: ${weekly_theta:.2f}")

    # =========================================================================
    # TARGET CALCULATION
    # =========================================================================

    print()
    print("-" * 70)
    print("TARGET CALCULATION")
    print("-" * 70)

    total_entry_fees = ENTRY_FEE_PER_LEG * 2
    target_net = total_long_cost * (TARGET_RETURN_PCT / 100)
    required_gross = target_net + weekly_theta + total_entry_fees

    print(f"Long Straddle Cost:     ${total_long_cost:,.2f}")
    print(f"Target Return:          {TARGET_RETURN_PCT}%")
    print(f"Target NET Profit:      ${target_net:.2f}")
    print()
    print(f"  Target NET:           ${target_net:>8.2f}")
    print(f"  + Weekly Theta:       ${weekly_theta:>8.2f}")
    print(f"  + Entry Fees:         ${total_entry_fees:>8.2f}")
    print(f"  ─────────────────────────────")
    print(f"  = REQUIRED GROSS:     ${required_gross:>8.2f}")

    # =========================================================================
    # FIND OPTIMAL STRIKES
    # =========================================================================

    print()
    print("-" * 70)
    print(f"FINDING STRIKES (minimum {MIN_MULTIPLIER}x expected move)")
    print("-" * 70)

    # Get next week's expiration
    expirations = client.get_option_expirations(underlying_uic)
    next_friday_exp = None

    for exp in expirations:
        exp_date_str = exp.get("Expiry", "")[:10]
        if exp_date_str:
            exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
            dte = (exp_date - datetime.now().date()).days
            if 7 <= dte <= 10:
                next_friday_exp = exp
                print(f"Expiry: {exp_date_str} ({dte} DTE)")
                break

    if not next_friday_exp:
        print("ERROR: Could not find next week's expiration")
        return

    options = next_friday_exp.get("SpecificOptions", [])

    # Collect option data
    calls = []
    puts = []

    print("Scanning options...")

    for opt in options:
        strike = opt.get("StrikePrice", 0)
        uic = opt.get("Uic")
        put_call = opt.get("PutCall")

        if strike < spy_price - 25 or strike > spy_price + 25:
            continue

        q = client.get_quote(uic, "StockOption")
        if not q:
            continue

        bid = q["Quote"].get("Bid", 0) or 0
        if bid <= 0:
            continue

        distance = abs(strike - spy_price)
        mult = distance / expected_move

        data = {
            "strike": strike,
            "bid": bid,
            "premium": bid * 100,
            "distance": distance,
            "mult": mult
        }

        if put_call == "Call" and strike > spy_price:
            calls.append(data)
        elif put_call == "Put" and strike < spy_price:
            puts.append(data)

    calls.sort(key=lambda x: x["strike"])
    puts.sort(key=lambda x: x["strike"], reverse=True)

    # Find combinations meeting criteria
    combinations = []

    for c in calls:
        if c["mult"] < MIN_MULTIPLIER:
            continue
        for p in puts:
            if p["mult"] < MIN_MULTIPLIER:
                continue

            gross = c["premium"] + p["premium"]
            if gross < required_gross:
                continue

            net = gross - weekly_theta - total_entry_fees
            net_return = (net / total_long_cost) * 100
            avg_mult = (c["mult"] + p["mult"]) / 2

            combinations.append({
                "call": c,
                "put": p,
                "gross": gross,
                "net": net,
                "return": net_return,
                "avg_mult": avg_mult
            })

    if not combinations:
        print()
        print("WARNING: No combinations meet the target with minimum multiplier!")
        print(f"Required gross: ${required_gross:.2f}")
        print(f"Try lowering target return or accepting tighter strikes.")
        return

    # Sort by average multiplier (widest/safest first)
    combinations.sort(key=lambda x: x["avg_mult"], reverse=True)

    # Display top options
    print()
    print(f"{'Put':<8} {'Call':<8} {'Put $':<10} {'Call $':<10} {'Gross':<10} {'NET':<10} {'Return':<8} {'Avg Mult':<10}")
    print("-" * 80)

    for combo in combinations[:10]:
        c = combo["call"]
        p = combo["put"]
        print(f"${p['strike']:<7.0f} ${c['strike']:<7.0f} ${p['premium']:<9.2f} ${c['premium']:<9.2f} ${combo['gross']:<9.2f} ${combo['net']:<9.2f} {combo['return']:>6.2f}% {combo['avg_mult']:>6.2f}x")

    # =========================================================================
    # APPLY 1.5x CAP - Pull down any strike > 1.5x to exactly 1.5x
    # =========================================================================

    MAX_MULTIPLIER = 1.5

    # Calculate 1.5x strikes
    call_at_1_5x = round(spy_price + (expected_move * MAX_MULTIPLIER))
    put_at_1_5x = round(spy_price - (expected_move * MAX_MULTIPLIER))

    print()
    print("=" * 70)
    print("APPLYING 1.5x EXPECTED MOVE CAP")
    print("=" * 70)
    print(f"1.5x Expected Move = ${expected_move * MAX_MULTIPLIER:.2f}")
    print(f"1.5x Call Strike: ${call_at_1_5x}")
    print(f"1.5x Put Strike:  ${put_at_1_5x}")

    # Start with widest that meets target
    best = combinations[0]
    original_call = best["call"]
    original_put = best["put"]

    print()
    print(f"Initial widest strikes meeting target:")
    print(f"  Put:  ${original_put['strike']:.0f} ({original_put['mult']:.2f}x)")
    print(f"  Call: ${original_call['strike']:.0f} ({original_call['mult']:.2f}x)")

    # Find the actual options at 1.5x if we need to cap
    final_call = original_call
    final_put = original_put

    # Cap call if > 1.5x
    if original_call["mult"] > MAX_MULTIPLIER:
        print(f"\n  Call at {original_call['mult']:.2f}x > 1.5x, pulling down to ${call_at_1_5x}")
        for c in calls:
            if c["strike"] == call_at_1_5x:
                final_call = c
                break
        if final_call["strike"] != call_at_1_5x:
            # Find closest available
            for c in calls:
                if c["strike"] >= call_at_1_5x:
                    final_call = c
                    break
    else:
        print(f"\n  Call at {original_call['mult']:.2f}x <= 1.5x, keeping as is")

    # Cap put if > 1.5x
    if original_put["mult"] > MAX_MULTIPLIER:
        print(f"  Put at {original_put['mult']:.2f}x > 1.5x, pulling down to ${put_at_1_5x}")
        for p in puts:
            if p["strike"] == put_at_1_5x:
                final_put = p
                break
        if final_put["strike"] != put_at_1_5x:
            # Find closest available
            for p in puts:
                if p["strike"] <= put_at_1_5x:
                    final_put = p
                    break
    else:
        print(f"  Put at {original_put['mult']:.2f}x <= 1.5x, keeping as is")

    # Calculate current P&L after capping
    current_gross = final_call["premium"] + final_put["premium"]
    current_net = current_gross - weekly_theta - total_entry_fees
    current_return = (current_net / total_long_cost) * 100

    # =========================================================================
    # OPTIMIZE: If above 1% target, push tighter strike OUT for more safety
    # =========================================================================

    print()
    print("=" * 70)
    print("OPTIMIZING FOR SAFETY (push strikes out while staying >= 1% NET)")
    print("=" * 70)

    min_target_return = TARGET_RETURN_PCT  # 1%

    if current_return > min_target_return:
        print(f"Current return {current_return:.2f}% > {min_target_return}% target")
        print("Attempting to push strikes further out for more safety...")
        print()

        # Find which leg is tighter (lower multiplier = more risk)
        if final_call["mult"] < final_put["mult"]:
            tighter_side = "call"
            print(f"Call is tighter ({final_call['mult']:.2f}x vs put {final_put['mult']:.2f}x)")
        else:
            tighter_side = "put"
            print(f"Put is tighter ({final_put['mult']:.2f}x vs call {final_call['mult']:.2f}x)")

        # Try pushing the tighter leg out until we hit 1% or 1.5x
        best_call = final_call
        best_put = final_put

        if tighter_side == "put":
            # Try wider puts (lower strikes)
            for p in puts:
                if p["mult"] > MAX_MULTIPLIER:
                    continue  # Don't go past 1.5x
                if p["strike"] >= final_put["strike"]:
                    continue  # Only consider wider strikes

                test_gross = final_call["premium"] + p["premium"]
                test_net = test_gross - weekly_theta - total_entry_fees
                test_return = (test_net / total_long_cost) * 100

                if test_return >= min_target_return:
                    best_put = p
                    print(f"  Trying put ${p['strike']:.0f} ({p['mult']:.2f}x): {test_return:.2f}% - OK")
                else:
                    print(f"  Trying put ${p['strike']:.0f} ({p['mult']:.2f}x): {test_return:.2f}% - Below target, stop")
                    break

            final_put = best_put

        else:  # tighter_side == "call"
            # Try wider calls (higher strikes)
            for c in calls:
                if c["mult"] > MAX_MULTIPLIER:
                    continue  # Don't go past 1.5x
                if c["strike"] <= final_call["strike"]:
                    continue  # Only consider wider strikes

                test_gross = c["premium"] + final_put["premium"]
                test_net = test_gross - weekly_theta - total_entry_fees
                test_return = (test_net / total_long_cost) * 100

                if test_return >= min_target_return:
                    best_call = c
                    print(f"  Trying call ${c['strike']:.0f} ({c['mult']:.2f}x): {test_return:.2f}% - OK")
                else:
                    print(f"  Trying call ${c['strike']:.0f} ({c['mult']:.2f}x): {test_return:.2f}% - Below target, stop")
                    break

            final_call = best_call

        # Now try pushing the OTHER leg too if we're still above target
        current_gross = final_call["premium"] + final_put["premium"]
        current_net = current_gross - weekly_theta - total_entry_fees
        current_return = (current_net / total_long_cost) * 100

        if current_return > min_target_return:
            print()
            other_side = "call" if tighter_side == "put" else "put"
            print(f"Still at {current_return:.2f}%, trying to push {other_side} out too...")

            if other_side == "put":
                for p in puts:
                    if p["mult"] > MAX_MULTIPLIER:
                        continue
                    if p["strike"] >= final_put["strike"]:
                        continue

                    test_gross = final_call["premium"] + p["premium"]
                    test_net = test_gross - weekly_theta - total_entry_fees
                    test_return = (test_net / total_long_cost) * 100

                    if test_return >= min_target_return:
                        best_put = p
                        print(f"  Trying put ${p['strike']:.0f} ({p['mult']:.2f}x): {test_return:.2f}% - OK")
                    else:
                        print(f"  Trying put ${p['strike']:.0f} ({p['mult']:.2f}x): {test_return:.2f}% - Below target, stop")
                        break

                final_put = best_put

            else:  # other_side == "call"
                for c in calls:
                    if c["mult"] > MAX_MULTIPLIER:
                        continue
                    if c["strike"] <= final_call["strike"]:
                        continue

                    test_gross = c["premium"] + final_put["premium"]
                    test_net = test_gross - weekly_theta - total_entry_fees
                    test_return = (test_net / total_long_cost) * 100

                    if test_return >= min_target_return:
                        best_call = c
                        print(f"  Trying call ${c['strike']:.0f} ({c['mult']:.2f}x): {test_return:.2f}% - OK")
                    else:
                        print(f"  Trying call ${c['strike']:.0f} ({c['mult']:.2f}x): {test_return:.2f}% - Below target, stop")
                        break

                final_call = best_call

    else:
        print(f"Current return {current_return:.2f}% is at or below target, keeping strikes as is")

    # Calculate final P&L
    final_gross = final_call["premium"] + final_put["premium"]
    final_net = final_gross - weekly_theta - total_entry_fees
    final_return = (final_net / total_long_cost) * 100
    final_avg_mult = (final_call["mult"] + final_put["mult"]) / 2

    # =========================================================================
    # FINAL RECOMMENDATION
    # =========================================================================

    print()
    print("=" * 70)
    print("FINAL RECOMMENDATION")
    print("(1% target with 1.5x expected move cap)")
    print("=" * 70)
    print(f"""
STRIKES TO SELL:
  Short Put:  ${final_put['strike']:.0f} @ ${final_put['bid']:.2f} = ${final_put['premium']:.2f}  ({final_put['mult']:.2f}x expected move)
  Short Call: ${final_call['strike']:.0f} @ ${final_call['bid']:.2f} = ${final_call['premium']:.2f}  ({final_call['mult']:.2f}x expected move)

P&L BREAKDOWN:
  Gross Premium:      +${final_gross:>8.2f}
  Weekly Theta:       -${weekly_theta:>8.2f}
  Entry Fees:         -${total_entry_fees:>8.2f}
  ─────────────────────────────
  NET Premium:        +${final_net:>8.2f}

RETURN:
  Long Straddle Cost: ${total_long_cost:,.2f}
  NET Return:         {final_return:.2f}%
  Target was:         {TARGET_RETURN_PCT:.2f}%

RISK PROFILE:
  Put is {final_put['mult']:.2f}x expected move away (${final_put['distance']:.2f} points)
  Call is {final_call['mult']:.2f}x expected move away (${final_call['distance']:.2f} points)
  Profit Zone: ${final_put['strike']:.0f} - ${final_call['strike']:.0f} (${final_call['strike'] - final_put['strike']:.0f} points wide)

PROBABILITY:
  At ~{final_avg_mult:.1f}x expected move, breach probability is approximately:
  - 1.0x = ~32% (1 in 3 weeks)
  - 1.25x = ~20% (1 in 5 weeks)
  - 1.5x = ~13% (1 in 8 weeks)
  - 2.0x = ~5% (1 in 20 weeks)
""")


if __name__ == "__main__":
    main()
