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
    fee_per_leg = config["strategy"].get("short_strangle_fee_per_leg", 2.05)
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
    # Fee calculation: $2.05/leg/direction × 2 legs × 2 directions (entry+exit) = $8.20 round-trip
    target_net = long_straddle_cost * (weekly_target_return_pct / 100)
    total_round_trip_fees = fee_per_leg * 2 * position_size * 2  # 2 legs × 2 directions
    required_gross = target_net + weekly_theta + total_round_trip_fees

    print(f"Target Return: {weekly_target_return_pct}% NET of Long Straddle Cost")
    print(f"Long Straddle Cost: ${long_straddle_cost:,.2f}")
    print(f"Target NET: ${target_net:.2f}")
    print(f"+ Weekly Theta: ${weekly_theta:.2f}")
    print(f"+ Round-Trip Fees: ${total_round_trip_fees:.2f} (2 legs × $4.10)")
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
    # STEP 4: Find highest SYMMETRIC multiplier achieving >= 1% NET return
    # =========================================================================
    # NEW STRATEGY (2026-01-27):
    # 1. Start from MAX multiplier (2.0x) and work DOWN in 0.01 increments
    # 2. At each level, find symmetric strikes (same multiplier for both legs)
    # 3. Stop at FIRST (highest) multiplier that achieves >= 1% NET return
    # 4. This gives the safest/widest strikes that still hit premium target
    #
    # Benefits:
    # - Always symmetric (true delta neutral)
    # - Dynamically adapts to VIX/expected move
    # - Low VIX: goes to lower multipliers to hit target
    # - High VIX: stays at higher multipliers for safety

    min_mult_absolute = 0.5  # Absolute floor
    min_target_return = weekly_target_return_pct

    # Build strike->data mappings
    call_by_strike = {c["strike"]: c for c in calls}
    put_by_strike = {p["strike"]: p for p in puts}
    all_call_strikes = sorted(call_by_strike.keys())
    all_put_strikes = sorted(put_by_strike.keys(), reverse=True)

    print(f"Available: {len(all_call_strikes)} call strikes, {len(all_put_strikes)} put strikes")
    print(f"Scanning from {max_multiplier}x down to {min_mult_absolute}x for symmetric strikes >= {min_target_return}% NET")
    print()

    final_call = None
    final_put = None
    found_mult = None

    # Test multipliers from max down to min in 0.01 increments
    test_multipliers = []
    mult = max_multiplier
    while mult >= min_mult_absolute:
        test_multipliers.append(round(mult, 2))
        mult -= 0.01

    for target_mult in test_multipliers:
        target_distance = expected_move * target_mult

        # Find call strike at or above target distance
        target_call_strike = spy_price + target_distance
        call_strike = None
        for s in all_call_strikes:
            if s >= target_call_strike:
                call_strike = s
                break

        # Find put strike at or below target distance
        target_put_strike = spy_price - target_distance
        put_strike = None
        for s in all_put_strikes:
            if s <= target_put_strike:
                put_strike = s
                break

        if not call_strike or not put_strike:
            continue

        call_data = call_by_strike.get(call_strike)
        put_data = put_by_strike.get(put_strike)

        if not call_data or not put_data:
            continue

        # Check symmetry
        mult_diff = abs(call_data["mult"] - put_data["mult"])
        if mult_diff > 0.3:
            continue

        # Calculate NET return
        gross_premium = call_data["premium"] + put_data["premium"]
        net_premium = gross_premium - weekly_theta - total_round_trip_fees
        net_return = (net_premium / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0

        # Check if meets target
        if net_return >= min_target_return:
            # Verify fresh quotes
            call_quote = client.get_quote(call_data["uic"], "StockOption")
            put_quote = client.get_quote(put_data["uic"], "StockOption")

            if call_quote and put_quote:
                call_bid = call_quote["Quote"].get("Bid", 0) or 0
                put_bid = put_quote["Quote"].get("Bid", 0) or 0

                if call_bid > 0 and put_bid > 0:
                    # Update with fresh prices
                    call_data["bid"] = call_bid
                    call_data["premium"] = call_bid * 100 * position_size
                    put_data["bid"] = put_bid
                    put_data["premium"] = put_bid * 100 * position_size

                    # Recalculate with fresh prices
                    fresh_gross = call_data["premium"] + put_data["premium"]
                    fresh_net = fresh_gross - weekly_theta - total_round_trip_fees
                    fresh_return = (fresh_net / long_straddle_cost) * 100

                    if fresh_return >= min_target_return:
                        final_call = call_data
                        final_put = put_data
                        found_mult = target_mult
                        print(f"SUCCESS: Found symmetric strikes at {target_mult:.2f}x")
                        print(f"  Call: ${call_strike} ({call_data['mult']:.2f}x EM)")
                        print(f"  Put:  ${put_strike} ({put_data['mult']:.2f}x EM)")
                        print(f"  NET Return: {fresh_return:.2f}%")
                        break

    if not final_call or not final_put:
        print()
        print("ERROR: No symmetric strikes achieve target return")
        print(f"Scanned from {max_multiplier}x down to {min_mult_absolute}x")
        print("Current market (low VIX) doesn't support 1% weekly return")
        return

    # Calculate final P&L
    final_gross = final_call["premium"] + final_put["premium"]
    final_net = final_gross - weekly_theta - total_round_trip_fees
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
  Buy {position_size} SPY ${call_option['strike']:.0f} Call @ ${call_ask:.2f} = ${call_ask * 100 * position_size:.2f}
  Buy {position_size} SPY ${put_option['strike']:.0f} Put  @ ${put_ask:.2f} = ${put_ask * 100 * position_size:.2f}
  Expiry: {call_option['expiry'][:10]} ({dte} DTE)
  Total Cost: ${long_straddle_cost:,.2f}
  Weekly Theta Decay: -${weekly_theta:.2f}

SHORT STRANGLE (Income):
  Sell {position_size} SPY ${final_call['strike']:.0f} Call @ ${final_call['bid']:.2f} = ${final_call['premium']:.2f}
  Sell {position_size} SPY ${final_put['strike']:.0f} Put  @ ${final_put['bid']:.2f} = ${final_put['premium']:.2f}
  Expiry: {weekly_expiry} ({weekly_dte} DTE)
  Total Premium: ${final_gross:.2f}

WEEKLY P&L PROJECTION:
  Gross Premium:     +${final_gross:>8.2f}
  Weekly Theta:      -${weekly_theta:>8.2f}
  Round-Trip Fees:   -${total_round_trip_fees:>8.2f}  ({position_size} contracts × 2 legs × $4.10 round-trip)
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
