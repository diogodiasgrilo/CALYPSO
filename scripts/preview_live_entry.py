#!/usr/bin/env python3
"""
Preview what the Delta Neutral bot would do in LIVE mode right now.

Shows exactly:
1. Long Straddle: Strike, Expiry, Cost
2. Short Strangle: Strikes, Expiry, Premium, Expected Move multiplier
3. P&L projections: Gross, Theta, Fees, NET Return

This is a READ-ONLY preview - no orders are placed.

Usage:
    python scripts/preview_live_entry.py
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
from shared.config_loader import ConfigLoader
import json


def main():
    config_path = "bots/delta_neutral/config/config.json"
    config_loader = ConfigLoader(local_config_path=config_path)
    config = config_loader.load_config()

    client = SaxoClient(config)
    underlying_uic = config["strategy"]["underlying_uic"]
    target_dte = config["strategy"].get("long_straddle_max_dte", 120)  # Target ~120 DTE for longs
    max_vix = config["strategy"]["max_vix_entry"]
    weekly_target_return_pct = config["strategy"].get("weekly_target_return_percent", 1.0)
    max_multiplier = config["strategy"].get("weekly_strangle_multiplier_max", 2.0)
    entry_fee_per_leg = config["strategy"].get("short_strangle_entry_fee_per_leg", 2.0)
    position_size = config["strategy"]["position_size"]

    print("=" * 70)
    print("PREVIEW: WHAT THE BOT WOULD DO IN LIVE MODE RIGHT NOW")
    print("=" * 70)
    print(f"Date/Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: LIVE SIMULATION (no orders placed)")
    print()

    # Get SPY price
    quote = client.get_quote(underlying_uic, "Etf")
    if not quote:
        print("ERROR: Could not get SPY quote")
        return

    spy_price = quote["Quote"].get("Mid") or quote["Quote"].get("LastTraded", 0)
    print(f"SPY Price: ${spy_price:.2f}")

    # Get VIX
    vix_uic = config["strategy"]["vix_uic"]
    vix_price = client.get_vix_price(vix_uic)
    print(f"VIX: {vix_price:.2f}")

    # Check VIX entry condition
    print()
    print("-" * 70)
    print("STEP 1: VIX ENTRY CHECK")
    print("-" * 70)
    if vix_price >= max_vix:
        print(f"BLOCKED: VIX {vix_price:.2f} >= {max_vix} threshold")
        print("Bot would NOT enter any positions - waiting for VIX to drop")
        return
    else:
        print(f"PASSED: VIX {vix_price:.2f} < {max_vix} threshold")
        print("Bot would proceed with entry")

    # =========================================================================
    # LONG STRADDLE
    # =========================================================================
    print()
    print("-" * 70)
    print(f"STEP 2: LONG STRADDLE ENTRY (~{target_dte} DTE)")
    print("-" * 70)

    atm_options = client.find_atm_options(underlying_uic, spy_price, target_dte=target_dte)
    if not atm_options:
        print("ERROR: Could not find ATM options for long straddle")
        return

    call_option = atm_options["call"]
    put_option = atm_options["put"]

    # Get quotes for pricing
    call_quote = client.get_quote(call_option["uic"], "StockOption")
    put_quote = client.get_quote(put_option["uic"], "StockOption")

    call_ask = call_quote["Quote"].get("Ask", 0) if call_quote else 0
    put_ask = put_quote["Quote"].get("Ask", 0) if put_quote else 0

    long_straddle_cost = (call_ask + put_ask) * 100 * position_size

    # Calculate DTE
    exp_date = datetime.strptime(call_option["expiry"][:10], "%Y-%m-%d").date()
    dte = (exp_date - datetime.now().date()).days

    print(f"Strike: ${call_option['strike']:.0f}")
    print(f"Expiry: {call_option['expiry'][:10]} ({dte} DTE)")
    print()
    print(f"Long Call @ ${call_ask:.2f} = ${call_ask * 100:.2f}")
    print(f"Long Put  @ ${put_ask:.2f} = ${put_ask * 100:.2f}")
    print(f"{'─' * 40}")
    print(f"TOTAL COST: ${long_straddle_cost:,.2f}")

    # Get theta
    call_greeks = client.get_option_greeks(call_option["uic"])
    put_greeks = client.get_option_greeks(put_option["uic"])
    call_theta = abs(call_greeks.get("Theta", 0)) if call_greeks else 0
    put_theta = abs(put_greeks.get("Theta", 0)) if put_greeks else 0
    daily_theta = (call_theta + put_theta) * 100 * position_size
    weekly_theta = daily_theta * 7

    print()
    print(f"Daily Theta Decay: ${daily_theta:.2f}")
    print(f"Weekly Theta Decay: ${weekly_theta:.2f}")

    # =========================================================================
    # SHORT STRANGLE
    # =========================================================================
    print()
    print("-" * 70)
    print("STEP 3: SHORT STRANGLE ENTRY (Next Friday)")
    print("-" * 70)

    # Get expected move
    expected_move = client.get_expected_move_from_straddle(
        underlying_uic, spy_price, for_roll=True
    )
    if not expected_move:
        print("ERROR: Could not get expected move")
        return

    print(f"Expected Move: ±${expected_move:.2f} ({(expected_move/spy_price)*100:.2f}%)")
    print()

    # Calculate target premium using 1% NET of long straddle cost
    target_net = long_straddle_cost * (weekly_target_return_pct / 100)
    total_entry_fees = entry_fee_per_leg * 2 * position_size
    required_gross = target_net + weekly_theta + total_entry_fees

    print(f"Target Return: {weekly_target_return_pct}% NET of Long Straddle Cost")
    print(f"Long Straddle Cost: ${long_straddle_cost:,.2f}")
    print(f"Target NET: ${target_net:.2f}")
    print(f"+ Weekly Theta: ${weekly_theta:.2f}")
    print(f"+ Entry Fees: ${total_entry_fees:.2f}")
    print(f"= Required Gross: ${required_gross:.2f}")
    print()

    # Get next FRIDAY expiration (ALWAYS next Friday, never current week)
    # Short strangles should always have at least 7 days to expiry
    expirations = client.get_option_expirations(underlying_uic)
    weekly_exp = None
    today = datetime.now().date()

    # Collect all Friday expirations with 7+ DTE
    friday_candidates = []
    for exp_data in expirations:
        exp_date_str = exp_data.get("Expiry", "")[:10]
        if exp_date_str:
            exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days

            # ALWAYS use Friday (weekday() == 4)
            if exp_date.weekday() != 4:
                continue

            # Always look for NEXT Friday (7+ days out)
            if dte >= 7:
                friday_candidates.append((dte, exp_date_str, exp_data))

    # Sort by DTE to get the nearest Friday
    if friday_candidates:
        friday_candidates.sort(key=lambda x: x[0])
        weekly_dte, weekly_expiry, weekly_exp = friday_candidates[0]
    else:
        print("ERROR: Could not find next Friday expiration (7+ DTE)")
        return

    print(f"Short Expiry (Next Friday): {weekly_expiry} ({weekly_dte} DTE)")
    print()

    # Scan all strikes
    calls = []
    puts = []
    specific_options = weekly_exp.get("SpecificOptions", [])

    for opt in specific_options:
        strike = opt.get("StrikePrice", 0)
        uic = opt.get("Uic")
        put_call = opt.get("PutCall")

        if strike < spy_price - 20 or strike > spy_price + 20:
            continue

        q = client.get_quote(uic, "StockOption")
        if not q:
            continue

        bid = q["Quote"].get("Bid", 0) or 0
        if bid <= 0:
            continue

        distance = abs(strike - spy_price)
        mult = distance / expected_move if expected_move > 0 else 0
        premium = bid * 100 * position_size

        data = {
            "strike": strike,
            "uic": uic,
            "bid": bid,
            "premium": premium,
            "distance": distance,
            "mult": mult
        }

        if put_call == "Call" and strike > spy_price:
            calls.append(data)
        elif put_call == "Put" and strike < spy_price:
            puts.append(data)

    calls.sort(key=lambda x: x["strike"])
    puts.sort(key=lambda x: x["strike"], reverse=True)

    # =========================================================================
    # STEP 4: Find optimal strikes with fallback logic
    # =========================================================================
    # Strategy:
    # 1. Start at 1.0x minimum multiplier
    # 2. Find combinations, apply 1.5x cap, optimize for safety
    # 3. If no valid options found, progressively reduce minimum multiplier
    # 4. Track which strikes were capped so we can reverse if needed

    # UPDATED: Use config minimum multiplier (1.5x) per strategy spec
    min_mult_from_config = config["strategy"].get("weekly_strangle_multiplier_min", 1.5)
    MIN_MULT_ATTEMPTS = [min_mult_from_config]  # Only try configured minimum, don't go lower

    final_call = None
    final_put = None

    for min_mult_threshold in MIN_MULT_ATTEMPTS:
        print(f"Trying MIN_MULTIPLIER = {min_mult_threshold}x...")

        # Find all combinations meeting minimum requirements
        combinations = []
        for c in calls:
            for p in puts:
                if c["mult"] < min_mult_threshold or p["mult"] < min_mult_threshold:
                    continue

                gross = c["premium"] + p["premium"]
                if gross < required_gross:
                    continue

                net = gross - weekly_theta - total_entry_fees
                net_return = (net / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0
                min_mult = min(c["mult"], p["mult"])

                combinations.append({
                    "call": c,
                    "put": p,
                    "gross": gross,
                    "net": net,
                    "return": net_return,
                    "min_mult": min_mult
                })

        if not combinations:
            print(f"  No combinations at {min_mult_threshold}x minimum")
            continue

        # Sort by minimum multiplier (widest/safest first)
        combinations.sort(key=lambda x: x["min_mult"], reverse=True)
        print(f"  Found {len(combinations)} combinations")

        # Start with the widest combination
        best = combinations[0]
        working_call = best["call"]
        working_put = best["put"]

        # Track original strikes before any capping
        original_call = working_call
        original_put = working_put
        call_was_capped = False
        put_was_capped = False

        print(f"  Widest: Put ${working_put['strike']:.0f} ({working_put['mult']:.2f}x) / Call ${working_call['strike']:.0f} ({working_call['mult']:.2f}x)")

        # Apply 1.5x cap - track which sides were capped
        # We want the option CLOSEST to 1.5x (but not over), so iterate from furthest to closest
        if working_call["mult"] > max_multiplier:
            call_was_capped = True
            # Calls are sorted ascending (closest first), so reverse to check furthest first
            for c in reversed(calls):
                if c["mult"] <= max_multiplier:
                    working_call = c
                    print(f"  Call capped: ${original_call['strike']:.0f} ({original_call['mult']:.2f}x) -> ${working_call['strike']:.0f} ({working_call['mult']:.2f}x)")
                    break

        if working_put["mult"] > max_multiplier:
            put_was_capped = True
            # Puts are sorted descending (closest first), so reverse to check furthest first
            for p in reversed(puts):
                if p["mult"] <= max_multiplier:
                    working_put = p
                    print(f"  Put capped: ${original_put['strike']:.0f} ({original_put['mult']:.2f}x) -> ${working_put['strike']:.0f} ({working_put['mult']:.2f}x)")
                    break

        # Optimize: Push tighter strike OUT while staying >= target
        current_gross = working_call["premium"] + working_put["premium"]
        current_net = current_gross - weekly_theta - total_entry_fees
        current_return = (current_net / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0

        # Track optimization changes
        call_was_optimized = False
        put_was_optimized = False
        pre_opt_call = working_call
        pre_opt_put = working_put

        if current_return > weekly_target_return_pct:
            # Find tighter side and push out
            if working_call["mult"] < working_put["mult"]:
                # Call is tighter, try wider calls
                for c in calls:
                    if c["mult"] > max_multiplier or c["strike"] <= working_call["strike"]:
                        continue
                    test_gross = c["premium"] + working_put["premium"]
                    test_net = test_gross - weekly_theta - total_entry_fees
                    test_return = (test_net / long_straddle_cost) * 100
                    if test_return >= weekly_target_return_pct:
                        working_call = c
                        call_was_optimized = True
                    else:
                        break
            else:
                # Put is tighter, try wider puts
                for p in puts:
                    if p["mult"] > max_multiplier or p["strike"] >= working_put["strike"]:
                        continue
                    test_gross = working_call["premium"] + p["premium"]
                    test_net = test_gross - weekly_theta - total_entry_fees
                    test_return = (test_net / long_straddle_cost) * 100
                    if test_return >= weekly_target_return_pct:
                        working_put = p
                        put_was_optimized = True
                    else:
                        break

            # Try pushing OTHER leg too
            current_gross = working_call["premium"] + working_put["premium"]
            current_net = current_gross - weekly_theta - total_entry_fees
            current_return = (current_net / long_straddle_cost) * 100

            if current_return > weekly_target_return_pct:
                if working_call["mult"] < working_put["mult"]:
                    for c in calls:
                        if c["mult"] > max_multiplier or c["strike"] <= working_call["strike"]:
                            continue
                        test_gross = c["premium"] + working_put["premium"]
                        test_net = test_gross - weekly_theta - total_entry_fees
                        test_return = (test_net / long_straddle_cost) * 100
                        if test_return >= weekly_target_return_pct:
                            working_call = c
                            call_was_optimized = True
                        else:
                            break
                else:
                    for p in puts:
                        if p["mult"] > max_multiplier or p["strike"] >= working_put["strike"]:
                            continue
                        test_gross = working_call["premium"] + p["premium"]
                        test_net = test_gross - weekly_theta - total_entry_fees
                        test_return = (test_net / long_straddle_cost) * 100
                        if test_return >= weekly_target_return_pct:
                            working_put = p
                            put_was_optimized = True
                        else:
                            break

        # Verify quotes are still valid (bid > 0)
        call_quote = client.get_quote(working_call["uic"], "StockOption")
        put_quote = client.get_quote(working_put["uic"], "StockOption")

        if call_quote and put_quote:
            call_bid = call_quote["Quote"].get("Bid", 0) or 0
            put_bid = put_quote["Quote"].get("Bid", 0) or 0

            if call_bid > 0 and put_bid > 0:
                # Update with fresh prices
                working_call["bid"] = call_bid
                working_call["premium"] = call_bid * 100 * position_size
                working_put["bid"] = put_bid
                working_put["premium"] = put_bid * 100 * position_size

                final_call = working_call
                final_put = working_put
                print(f"  SUCCESS: Found valid options at {min_mult_threshold}x minimum")
                break

        # Fallback: Reverse optimization changes first
        print(f"  Options unavailable, trying fallback...")

        fallback_attempts = []

        # 1. If call was optimized, try pre-optimization call
        if call_was_optimized:
            fallback_attempts.append((pre_opt_call, working_put, "reverse call optimization"))

        # 2. If put was optimized, try pre-optimization put
        if put_was_optimized:
            fallback_attempts.append((working_call, pre_opt_put, "reverse put optimization"))

        # 3. If call was capped, try original (wider) call
        if call_was_capped:
            fallback_attempts.append((original_call, working_put, "reverse call cap"))

        # 4. If put was capped, try original (wider) put
        if put_was_capped:
            fallback_attempts.append((working_call, original_put, "reverse put cap"))

        # 5. Try both original (pre-cap) strikes
        if call_was_capped or put_was_capped:
            fallback_attempts.append((original_call, original_put, "both original strikes"))

        for fb_call, fb_put, fb_desc in fallback_attempts:
            fb_call_quote = client.get_quote(fb_call["uic"], "StockOption")
            fb_put_quote = client.get_quote(fb_put["uic"], "StockOption")

            if fb_call_quote and fb_put_quote:
                fb_call_bid = fb_call_quote["Quote"].get("Bid", 0) or 0
                fb_put_bid = fb_put_quote["Quote"].get("Bid", 0) or 0

                if fb_call_bid > 0 and fb_put_bid > 0:
                    fb_call["bid"] = fb_call_bid
                    fb_call["premium"] = fb_call_bid * 100 * position_size
                    fb_put["bid"] = fb_put_bid
                    fb_put["premium"] = fb_put_bid * 100 * position_size

                    final_call = fb_call
                    final_put = fb_put
                    print(f"  FALLBACK SUCCESS: {fb_desc}")
                    break

        if final_call and final_put:
            break

    if not final_call or not final_put:
        print()
        print("ERROR: No valid strike combinations found after all fallback attempts")
        print("Tried minimum multipliers: " + ", ".join([f"{m}x" for m in MIN_MULT_ATTEMPTS]))
        return

    # Calculate final P&L
    final_gross = final_call["premium"] + final_put["premium"]
    final_net = final_gross - weekly_theta - total_entry_fees
    final_return = (final_net / long_straddle_cost) * 100

    print()
    print("SELECTED STRIKES:")
    print(f"  Short Put:  ${final_put['strike']:.0f} @ ${final_put['bid']:.2f} = ${final_put['premium']:.2f} ({final_put['mult']:.2f}x exp move)")
    print(f"  Short Call: ${final_call['strike']:.0f} @ ${final_call['bid']:.2f} = ${final_call['premium']:.2f} ({final_call['mult']:.2f}x exp move)")

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print()
    print("=" * 70)
    print("COMPLETE POSITION SUMMARY")
    print("=" * 70)
    print(f"""
LONG STRADDLE (Hedge):
  Buy 1 SPY ${call_option['strike']:.0f} Call @ ${call_ask:.2f} = ${call_ask * 100:.2f}
  Buy 1 SPY ${put_option['strike']:.0f} Put  @ ${put_ask:.2f} = ${put_ask * 100:.2f}
  Expiry: {call_option['expiry'][:10]} ({dte} DTE)
  Total Cost: ${long_straddle_cost:,.2f}
  Weekly Theta Decay: -${weekly_theta:.2f}

SHORT STRANGLE (Income):
  Sell 1 SPY ${final_call['strike']:.0f} Call @ ${final_call['bid']:.2f} = ${final_call['premium']:.2f}
  Sell 1 SPY ${final_put['strike']:.0f} Put  @ ${final_put['bid']:.2f} = ${final_put['premium']:.2f}
  Expiry: {weekly_expiry} ({weekly_dte} DTE)
  Total Premium: ${final_gross:.2f}

WEEKLY P&L PROJECTION:
  Gross Premium:     +${final_gross:>8.2f}
  Weekly Theta:      -${weekly_theta:>8.2f}
  Entry Fees:        -${total_entry_fees:>8.2f}
  {'─' * 30}
  NET Premium:       +${final_net:>8.2f}

RETURNS:
  NET Return:        {final_return:.2f}% (target: {weekly_target_return_pct}%)
  Annualized:        ~{final_return * 52:.0f}% (if repeated weekly)

RISK PROFILE:
  Expected Move: ±${expected_move:.2f}
  Put at {final_put['mult']:.2f}x expected move (${final_put['distance']:.2f} points from SPY)
  Call at {final_call['mult']:.2f}x expected move (${final_call['distance']:.2f} points from SPY)
  Profit Zone: ${final_put['strike']:.0f} - ${final_call['strike']:.0f} (${final_call['strike'] - final_put['strike']:.0f} points wide)

PROBABILITY REFERENCE:
  1.0x expected move = ~32% breach chance (1 in 3 weeks)
  1.25x expected move = ~20% breach chance (1 in 5 weeks)
  1.5x expected move = ~13% breach chance (1 in 8 weeks)
""")

    print("=" * 70)
    print("THIS IS A PREVIEW ONLY - NO ORDERS WERE PLACED")
    print("=" * 70)


if __name__ == "__main__":
    main()
