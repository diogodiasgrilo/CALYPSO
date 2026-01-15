#!/usr/bin/env python3
"""
Weekly Projection Calculator for Delta Neutral Strategy.

Calculates expected premium and NET return for next week based on
current market conditions, matching your actual trading style.

Shows strikes at different expected move multipliers so you can choose.

Usage:
    python scripts/weekly_projection.py
"""
import sys
import os
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

    print("=" * 70)
    print("WEEKLY PROJECTION - DELTA NEUTRAL STRATEGY")
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

    # Margin calculation
    margin = spy_price * 100 * 0.20
    print(f"Margin (20% notional): ${margin:,.2f}")

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
    # LONG STRADDLE THETA
    # =========================================================================
    print("\n" + "-" * 70)
    print("LONG STRADDLE THETA COST")
    print("-" * 70)

    atm_options = client.find_atm_options(underlying_uic, spy_price, target_dte=target_dte)
    if not atm_options:
        print("ERROR: Could not find ATM options for long straddle")
        return

    print(f"Long Straddle: ${atm_options['call']['strike']:.0f} @ {atm_options['call']['expiry'][:10]}")

    call_greeks = client.get_option_greeks(atm_options["call"]["uic"])
    put_greeks = client.get_option_greeks(atm_options["put"]["uic"])

    call_theta = abs(call_greeks.get("Theta", 0)) if call_greeks else 0
    put_theta = abs(put_greeks.get("Theta", 0)) if put_greeks else 0
    daily_theta = (call_theta + put_theta) * 100
    weekly_theta = daily_theta * 7

    print(f"Daily Theta: ${daily_theta:.2f}/day")
    print(f"Weekly Theta (7 days): ${weekly_theta:.2f}")

    # =========================================================================
    # SHORT STRANGLE OPTIONS AT DIFFERENT MULTIPLIERS
    # =========================================================================
    print("\n" + "-" * 70)
    print("SHORT STRANGLE OPTIONS FOR NEXT FRIDAY")
    print("-" * 70)
    print()
    print(f"{'Mult':<6} {'Put':<8} {'Call':<8} {'Width':<8} {'Gross':<10} {'Theta':<10} {'NET':<10} {'Return':<8}")
    print("-" * 70)

    results = []

    for mult in [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]:
        strangle = client.find_strangle_options(
            underlying_uic, spy_price, expected_move, mult,
            weekly=True, for_roll=True
        )

        if not strangle:
            print(f"{mult:<6.2f}x  -- Could not find options --")
            continue

        call_q = client.get_quote(strangle["call"]["uic"], "StockOption")
        put_q = client.get_quote(strangle["put"]["uic"], "StockOption")

        if not call_q or not put_q:
            print(f"{mult:<6.2f}x  -- Could not get quotes --")
            continue

        call_bid = call_q["Quote"].get("Bid", 0) or 0
        put_bid = put_q["Quote"].get("Bid", 0) or 0

        call_strike = strangle["call"]["strike"]
        put_strike = strangle["put"]["strike"]
        width = call_strike - put_strike

        gross = (call_bid + put_bid) * 100
        net = gross - weekly_theta
        net_return = (net / margin) * 100

        results.append({
            "mult": mult,
            "put": put_strike,
            "call": call_strike,
            "width": width,
            "gross": gross,
            "net": net,
            "return": net_return,
            "call_bid": call_bid,
            "put_bid": put_bid
        })

        marker = ""
        if 0.9 <= mult <= 1.1:
            marker = " ← ~like last week"

        print(f"{mult:<6.2f}x ${put_strike:<7.0f} ${call_strike:<7.0f} ${width:<7.0f} ${gross:<9.2f} ${weekly_theta:<9.2f} ${net:<9.2f} {net_return:>6.2f}%{marker}")

    # =========================================================================
    # RECOMMENDATION (matching last week's ~$300 gross)
    # =========================================================================
    print("\n" + "=" * 70)
    print("RECOMMENDATION - MATCHING LAST WEEK'S PERFORMANCE")
    print("=" * 70)

    # Find the option closest to $300 gross (like last week)
    target_gross = 300
    best = None
    for r in results:
        if best is None or abs(r["gross"] - target_gross) < abs(best["gross"] - target_gross):
            best = r

    if best:
        print(f"""
LAST WEEK YOU HAD:
  Short $701 Call @ $1.28 = $128
  Short $687 Put  @ $1.72 = $172
  Total Gross: $300

THIS WEEK TO MATCH (~${target_gross} gross):
  Short ${best['call']:.0f} Call @ ${best['call_bid']:.2f} = ${best['call_bid']*100:.2f}
  Short ${best['put']:.0f} Put  @ ${best['put_bid']:.2f} = ${best['put_bid']*100:.2f}
  Total Gross: ${best['gross']:.2f}

WEEKLY P&L PROJECTION:
  Gross Premium:    +${best['gross']:>8.2f}
  Theta Cost:       -${weekly_theta:>8.2f}
  ─────────────────────────
  NET Premium:      +${best['net']:>8.2f}
  NET Return:        {best['return']:>8.2f}%

RISK ANALYSIS:
  Strikes at {best['mult']:.2f}x expected move
  Put ${best['put']:.0f} is ${spy_price - best['put']:.2f} below SPY ({(spy_price - best['put'])/expected_move:.1f}x exp move)
  Call ${best['call']:.0f} is ${best['call'] - spy_price:.2f} above SPY ({(best['call'] - spy_price)/expected_move:.1f}x exp move)

  Profit Zone: ${best['put']:.0f} - ${best['call']:.0f} (${best['width']:.0f} points wide)
""")

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
