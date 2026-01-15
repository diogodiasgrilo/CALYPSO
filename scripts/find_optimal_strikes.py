#!/usr/bin/env python3
"""
Find Optimal Short Strangle Strikes with Asymmetric Adjustment.

Strategy:
1. Start with 1x expected move on both sides as baseline
2. Show how to adjust asymmetrically to capture put skew premium
3. Adapts to current market conditions (not hardcoded)

Usage:
    python scripts/find_optimal_strikes.py
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

    print("=" * 70)
    print("OPTIMAL STRIKE FINDER - ASYMMETRIC SKEW ADJUSTMENT")
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

    # Get expected move
    expected_move = client.get_expected_move_from_straddle(
        underlying_uic, spy_price, for_roll=True
    )
    if not expected_move:
        print("ERROR: Could not get expected move")
        return

    print(f"Expected Move: +/- ${expected_move:.2f} ({(expected_move/spy_price)*100:.2f}%)")

    # Get theta cost
    atm_options = client.find_atm_options(underlying_uic, spy_price, target_dte=target_dte)
    if atm_options:
        call_greeks = client.get_option_greeks(atm_options["call"]["uic"])
        put_greeks = client.get_option_greeks(atm_options["put"]["uic"])
        call_theta = abs(call_greeks.get("Theta", 0)) if call_greeks else 0
        put_theta = abs(put_greeks.get("Theta", 0)) if put_greeks else 0
        weekly_theta = (call_theta + put_theta) * 100 * 7
        print(f"Weekly Theta Cost: ${weekly_theta:.2f}")
    else:
        weekly_theta = 140  # fallback estimate
        print(f"Weekly Theta Cost: ~${weekly_theta:.2f} (estimated)")

    margin = spy_price * 100 * 0.20

    # =========================================================================
    # STEP 1: BASELINE - 1x Expected Move (Symmetric)
    # =========================================================================
    print()
    print("=" * 70)
    print("STEP 1: BASELINE AT 1x EXPECTED MOVE (Symmetric)")
    print("=" * 70)

    baseline_call = round(spy_price + expected_move)
    baseline_put = round(spy_price - expected_move)

    print(f"Baseline Call: ${baseline_call} (+${baseline_call - spy_price:.2f})")
    print(f"Baseline Put:  ${baseline_put} (-${spy_price - baseline_put:.2f})")

    # =========================================================================
    # STEP 2: SCAN ALL STRIKES FOR PREMIUM
    # =========================================================================
    print()
    print("=" * 70)
    print("STEP 2: PREMIUM SCAN - ALL STRIKES")
    print("=" * 70)

    # Get next week's expiration
    expirations = client.get_option_expirations(underlying_uic)
    next_friday_exp = None

    for exp in expirations:
        exp_date_str = exp.get("Expiry", "")[:10]
        if exp_date_str:
            exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
            dte = (exp_date - datetime.now().date()).days
            if 7 <= dte <= 10:  # Next Friday
                next_friday_exp = exp
                print(f"Expiry: {exp_date_str} ({dte} DTE)")
                break

    if not next_friday_exp:
        print("ERROR: Could not find next week's expiration")
        return

    options = next_friday_exp.get("SpecificOptions", [])

    # Collect call and put data
    calls = []
    puts = []

    print()
    print("Scanning strikes...")

    for opt in options:
        strike = opt.get("StrikePrice", 0)
        uic = opt.get("Uic")
        put_call = opt.get("PutCall")

        # Only look at strikes within reasonable range
        if strike < spy_price - 20 or strike > spy_price + 20:
            continue

        q = client.get_quote(uic, "StockOption")
        if not q:
            continue

        bid = q["Quote"].get("Bid", 0) or 0
        if bid <= 0:
            continue

        distance = abs(strike - spy_price)
        mult = distance / expected_move
        premium = bid * 100

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

    # Sort by strike
    calls.sort(key=lambda x: x["strike"])
    puts.sort(key=lambda x: x["strike"], reverse=True)

    print()
    print("CALLS (Short - Above SPY):")
    print(f"{'Strike':<10} {'Dist':<8} {'Mult':<8} {'Bid':<8} {'Premium':<10}")
    print("-" * 50)
    for c in calls[:10]:
        marker = " <-- 1x baseline" if abs(c["mult"] - 1.0) < 0.15 else ""
        print(f"${c['strike']:<9.0f} +${c['distance']:<6.2f} {c['mult']:<8.2f}x ${c['bid']:<7.2f} ${c['premium']:<9.2f}{marker}")

    print()
    print("PUTS (Short - Below SPY):")
    print(f"{'Strike':<10} {'Dist':<8} {'Mult':<8} {'Bid':<8} {'Premium':<10}")
    print("-" * 50)
    for p in puts[:10]:
        marker = " <-- 1x baseline" if abs(p["mult"] - 1.0) < 0.15 else ""
        print(f"${p['strike']:<9.0f} -${p['distance']:<6.2f} {p['mult']:<8.2f}x ${p['bid']:<7.2f} ${p['premium']:<9.2f}{marker}")

    # =========================================================================
    # STEP 3: FIND OPTIMAL ASYMMETRIC COMBINATION
    # =========================================================================
    print()
    print("=" * 70)
    print("STEP 3: OPTIMAL ASYMMETRIC COMBINATIONS")
    print("=" * 70)
    print()
    print("Looking for best premium while staying near 1x expected move...")
    print("(Put skew typically allows wider put for same/more premium)")
    print()

    # Find combinations where:
    # - Both legs are between 0.8x and 1.5x expected move
    # - Maximize premium
    # - Prefer balanced premium per leg

    combinations = []
    for c in calls:
        if c["mult"] < 0.7 or c["mult"] > 1.8:
            continue
        for p in puts:
            if p["mult"] < 0.7 or p["mult"] > 1.8:
                continue

            total_premium = c["premium"] + p["premium"]
            avg_mult = (c["mult"] + p["mult"]) / 2
            premium_balance = min(c["premium"], p["premium"]) / max(c["premium"], p["premium"]) if max(c["premium"], p["premium"]) > 0 else 0

            net = total_premium - weekly_theta
            net_return = (net / margin) * 100

            combinations.append({
                "call": c,
                "put": p,
                "total": total_premium,
                "net": net,
                "return": net_return,
                "avg_mult": avg_mult,
                "balance": premium_balance
            })

    # Sort by total premium (highest first)
    combinations.sort(key=lambda x: x["total"], reverse=True)

    print(f"{'Put':<8} {'Call':<8} {'Put Prem':<10} {'Call Prem':<10} {'Gross':<10} {'NET':<10} {'Return':<8} {'Avg Mult':<10}")
    print("-" * 80)

    shown = 0
    for combo in combinations:
        if shown >= 10:
            break

        c = combo["call"]
        p = combo["put"]

        # Mark the ~1x expected move symmetric option
        marker = ""
        if abs(c["mult"] - 1.0) < 0.2 and abs(p["mult"] - 1.0) < 0.2:
            marker = " <-- SYMMETRIC 1x"
        elif combo["balance"] > 0.7 and combo["avg_mult"] < 1.3:
            marker = " <-- BALANCED"

        print(f"${p['strike']:<7.0f} ${c['strike']:<7.0f} ${p['premium']:<9.2f} ${c['premium']:<9.2f} ${combo['total']:<9.2f} ${combo['net']:<9.2f} {combo['return']:>6.2f}%{marker}")
        shown += 1

    # =========================================================================
    # RECOMMENDATION
    # =========================================================================
    print()
    print("=" * 70)
    print("RECOMMENDATION")
    print("=" * 70)

    # Find best balanced option near 1x
    best = None
    for combo in combinations:
        avg = combo["avg_mult"]
        if 0.9 <= avg <= 1.3 and combo["net"] > 0:
            if best is None or combo["total"] > best["total"]:
                best = combo

    if not best:
        # Fallback to highest premium
        best = combinations[0] if combinations else None

    if best:
        c = best["call"]
        p = best["put"]

        print(f"""
SUGGESTED STRIKES:
  Short Put:  ${p['strike']:.0f} @ ${p['bid']:.2f} = ${p['premium']:.2f}  ({p['mult']:.2f}x exp move)
  Short Call: ${c['strike']:.0f} @ ${c['bid']:.2f} = ${c['premium']:.2f}  ({c['mult']:.2f}x exp move)

PROJECTION:
  Gross Premium:    +${best['total']:>8.2f}
  Theta Cost:       -${weekly_theta:>8.2f}
  ─────────────────────────
  NET Premium:      +${best['net']:>8.2f}
  NET Return:        {best['return']:>8.2f}%

COMPARISON TO LAST WEEK:
  Last week: $687 Put / $701 Call = ~$300 gross
  This week: ${p['strike']:.0f} Put / ${c['strike']:.0f} Call = ${best['total']:.2f} gross

RISK:
  Put is {p['mult']:.2f}x expected move away (${p['distance']:.2f} points)
  Call is {c['mult']:.2f}x expected move away (${c['distance']:.2f} points)
  Profit zone: ${p['strike']:.0f} - ${c['strike']:.0f} (${c['strike'] - p['strike']:.0f} points wide)
""")
    else:
        print("Could not find suitable combination")


if __name__ == "__main__":
    main()
