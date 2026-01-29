#!/usr/bin/env python3
"""
Weekly Projection Calculator for Delta Neutral Strategy.

Calculates expected premium and NET return for next week based on
current market conditions, MATCHING THE ACTUAL BOT LOGIC.

Scans from max multiplier (2.0x) down to min (1.0x) in 0.01 increments
to find the highest multiplier achieving the target NET return.

Usage:
    python scripts/weekly_projection.py
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
    print("WEEKLY PROJECTION - DELTA NEUTRAL STRATEGY")
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

    # =========================================================================
    # BOT SIMULATION - Scan from max to min multiplier in 0.01 increments
    # =========================================================================
    # Read config values (same as actual bot)
    max_mult = config["strategy"].get("short_strangle_multiplier_max", 2.0)
    min_mult = config["strategy"].get("short_strangle_multiplier_min", 1.0)
    target_return_pct = config["strategy"].get("weekly_target_return_percent", 1.0)

    print("\n" + "-" * 70)
    print("BOT STRIKE SELECTION (matching actual bot logic)")
    print("-" * 70)
    print(f"Config: max_mult={max_mult}x, min_mult={min_mult}x, target_return={target_return_pct}%")
    print(f"Scanning from {max_mult}x down to {min_mult}x in 0.01 increments...")
    print()

    total_costs = weekly_theta + total_round_trip_fees

    # Generate test multipliers from max down to min in 0.01 increments (same as bot)
    test_multipliers = []
    mult = max_mult
    while mult >= min_mult:
        test_multipliers.append(round(mult, 2))
        mult -= 0.01

    best = None
    floor_option = None  # Track option at min multiplier floor

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

        # Use Bid, fallback to LastTraded when market is closed
        call_bid = call_q["Quote"].get("Bid", 0) or call_q["Quote"].get("LastTraded", 0) or 0
        put_bid = put_q["Quote"].get("Bid", 0) or put_q["Quote"].get("LastTraded", 0) or 0

        if call_bid <= 0 or put_bid <= 0:
            continue

        call_strike = strangle["call"]["strike"]
        put_strike = strangle["put"]["strike"]
        width = call_strike - put_strike

        gross = (call_bid + put_bid) * 100 * position_size
        net = gross - total_costs
        net_return = (net / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0

        result = {
            "mult": mult,
            "put": put_strike,
            "call": call_strike,
            "width": width,
            "gross": gross,
            "net": net,
            "return": net_return,
            "call_bid": call_bid,
            "put_bid": put_bid
        }

        # Track floor option (at min multiplier)
        if mult == min_mult or floor_option is None:
            floor_option = result

        # First multiplier that achieves target return wins (highest safe multiplier)
        if net_return >= target_return_pct and best is None:
            best = result
            print(f"✓ Found at {mult:.2f}x: Put ${put_strike:.0f} / Call ${call_strike:.0f} = {net_return:.2f}% NET")
            break  # Stop scanning, we found the highest multiplier meeting target

    # If nothing meets target, use floor multiplier
    if not best:
        best = floor_option
        if best:
            print(f"⚠ No multiplier achieves {target_return_pct}% target")
            print(f"  Using floor ({min_mult}x): Put ${best['put']:.0f} / Call ${best['call']:.0f} = {best['return']:.2f}% NET")

    # =========================================================================
    # RESULT - What the bot would select
    # =========================================================================
    print("\n" + "=" * 70)
    print("BOT SELECTION RESULT")
    print("=" * 70)

    if best:
        meets_target = best['return'] >= target_return_pct
        status = "✓ MEETS TARGET" if meets_target else f"⚠ BELOW TARGET (floor at {min_mult}x)"

        print(f"""
{status}

SELECTED STRIKES ({position_size} contract(s)):
  Short ${best['call']:.0f} Call @ ${best['call_bid']:.2f} = ${best['call_bid']*100*position_size:.2f}
  Short ${best['put']:.0f} Put  @ ${best['put_bid']:.2f} = ${best['put_bid']*100*position_size:.2f}
  Multiplier: {best['mult']:.2f}x expected move

WEEKLY P&L PROJECTION:
  Gross Premium:      +${best['gross']:>8.2f}
  Weekly Theta:       -${weekly_theta:>8.2f}
  Round-Trip Fees:    -${total_round_trip_fees:>8.2f}
  ─────────────────────────────
  NET Premium:        +${best['net']:>8.2f}
  NET Return:          {best['return']:>8.2f}% (target: {target_return_pct}%)

RISK ANALYSIS:
  Strikes at {best['mult']:.2f}x expected move
  Put ${best['put']:.0f} is ${spy_price - best['put']:.2f} below SPY ({(spy_price - best['put'])/expected_move:.1f}x exp move)
  Call ${best['call']:.0f} is ${best['call'] - spy_price:.2f} above SPY ({(best['call'] - spy_price)/expected_move:.1f}x exp move)
  Profit Zone: ${best['put']:.0f} - ${best['call']:.0f} (${best['width']:.0f} points wide)
""")
    else:
        print("\nERROR: Could not find any valid strangle options")

    # =========================================================================
    # PROBABILITY REFERENCE
    # =========================================================================
    print("=" * 70)
    print("PROBABILITY REFERENCE (Expected Move = ~1 Std Dev)")
    print("=" * 70)
    print("""
  Multiplier    Approx Breach Probability
  ──────────    ─────────────────────────
  0.5x          ~50% (coin flip)
  0.75x         ~40%
  1.0x          ~32% (1 in 3 weeks)
  1.25x         ~20%
  1.5x          ~13% (1 in 8 weeks)
  2.0x          ~5%  (1 in 20 weeks)
""")


if __name__ == "__main__":
    main()
