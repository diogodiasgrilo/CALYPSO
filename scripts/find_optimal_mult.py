#!/usr/bin/env python3
"""
Find optimal expected move multiplier for 1% NET return with symmetric strikes.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
from shared.config_loader import ConfigLoader
from datetime import datetime

def main():
    config_loader = ConfigLoader(local_config_path="bots/delta_neutral/config/config.json")
    config = config_loader.load_config()

    client = SaxoClient(config)
    underlying_uic = config["strategy"]["underlying_uic"]
    position_size = config["strategy"]["position_size"]
    entry_fee_per_leg = config["strategy"].get("short_strangle_entry_fee_per_leg", 2.0)

    # Get SPY quote
    spy_quote = client.get_quote(underlying_uic, "Etf")
    spy_price = spy_quote["Quote"].get("Mid", 0) if spy_quote else 0
    print(f"SPY: ${spy_price:.2f}")

    # Get long straddle cost (for 1% target calculation)
    long_atm = client.find_atm_options(underlying_uic, spy_price, target_dte=120)
    if long_atm:
        call_quote = client.get_quote(long_atm["call"]["uic"], "StockOption")
        put_quote = client.get_quote(long_atm["put"]["uic"], "StockOption")
        call_ask = call_quote["Quote"].get("Ask", 0) if call_quote else 0
        put_ask = put_quote["Quote"].get("Ask", 0) if put_quote else 0
        long_straddle_cost = (call_ask + put_ask) * 100 * position_size

        # Get theta
        call_greeks = client.get_option_greeks(long_atm["call"]["uic"])
        put_greeks = client.get_option_greeks(long_atm["put"]["uic"])
        call_theta = abs(call_greeks.get("Theta", 0)) if call_greeks else 0
        put_theta = abs(put_greeks.get("Theta", 0)) if put_greeks else 0
        weekly_theta = (call_theta + put_theta) * 100 * position_size * 7
    else:
        print("ERROR: Could not get long straddle")
        return

    print(f"Long Straddle Cost: ${long_straddle_cost:,.2f}")
    print(f"Weekly Theta Decay: ${weekly_theta:.2f}")

    # Calculate required gross premium for 1% NET return
    target_net = long_straddle_cost * 0.01  # 1% of straddle cost
    total_fees = entry_fee_per_leg * 2 * position_size
    required_gross = target_net + weekly_theta + total_fees

    print(f"Target NET (1%): ${target_net:.2f}")
    print(f"+ Weekly Theta: ${weekly_theta:.2f}")
    print(f"+ Entry Fees: ${total_fees:.2f}")
    print(f"= Required Gross: ${required_gross:.2f}")

    # Get expected move
    expected_move = client.get_expected_move_from_straddle(underlying_uic, spy_price, for_roll=True)
    if not expected_move:
        print("ERROR: Could not get expected move")
        return

    print(f"\nExpected Move: ${expected_move:.2f} ({(expected_move/spy_price)*100:.2f}%)")

    # Get option expirations for next week
    expirations = client.get_option_expirations(underlying_uic)
    today = datetime.now().date()
    target_expiry = None
    target_dte = 0
    for exp in expirations:
        exp_date_str = exp.get("Expiry", "")[:10]
        exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
        dte = (exp_date - today).days
        if 5 <= dte <= 12:
            target_expiry = exp
            target_dte = dte
            break

    if not target_expiry:
        print("ERROR: No expiry in 5-12 DTE range")
        return

    print(f"\nTarget Expiry: {target_expiry['Expiry'][:10]} ({target_dte} DTE)")
    print("\n" + "=" * 70)
    print("SCANNING ALL MULTIPLIERS FROM 0.5x to 2.0x")
    print("Looking for symmetric strikes that achieve 1% NET return")
    print("=" * 70)

    # Get all available options at target expiry
    specific_options = target_expiry.get("SpecificOptions", [])

    # Build strike->UIC mapping
    calls = {}
    puts = {}
    for opt in specific_options:
        strike = opt.get("StrikePrice", 0)
        uic = opt.get("Uic")
        if opt.get("PutCall") == "Call":
            calls[strike] = uic
        else:
            puts[strike] = uic

    # Get all unique strikes sorted
    all_strikes = sorted(set(calls.keys()) & set(puts.keys()))

    print(f"\nAvailable strikes: {len(all_strikes)} (from ${min(all_strikes)} to ${max(all_strikes)})")
    print()

    # Test different multipliers
    results = []
    for mult in [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0]:
        target_distance = expected_move * mult

        # Find call strike (above SPY)
        target_call_strike = spy_price + target_distance
        call_strike = None
        for s in all_strikes:
            if s >= target_call_strike:
                call_strike = s
                break

        # Find put strike (below SPY)
        target_put_strike = spy_price - target_distance
        put_strike = None
        for s in reversed(all_strikes):
            if s <= target_put_strike:
                put_strike = s
                break

        if not call_strike or not put_strike:
            continue

        # Calculate actual multipliers
        call_actual_mult = (call_strike - spy_price) / expected_move
        put_actual_mult = (spy_price - put_strike) / expected_move
        mult_diff = abs(call_actual_mult - put_actual_mult)

        # Get quotes
        call_quote = client.get_quote(calls[call_strike], "StockOption")
        put_quote = client.get_quote(puts[put_strike], "StockOption")

        if not call_quote or not put_quote:
            continue

        call_bid = call_quote["Quote"].get("Bid", 0)
        put_bid = put_quote["Quote"].get("Bid", 0)

        if call_bid <= 0 or put_bid <= 0:
            continue

        gross_premium = (call_bid + put_bid) * 100 * position_size
        net_premium = gross_premium - weekly_theta - total_fees
        net_return_pct = (net_premium / long_straddle_cost) * 100

        results.append({
            "mult": mult,
            "call_strike": call_strike,
            "put_strike": put_strike,
            "call_mult": call_actual_mult,
            "put_mult": put_actual_mult,
            "mult_diff": mult_diff,
            "call_bid": call_bid,
            "put_bid": put_bid,
            "gross": gross_premium,
            "net": net_premium,
            "net_pct": net_return_pct
        })

    # Print results
    header = f"{'Mult':>5} | {'Call':>7} | {'Put':>7} | {'Call x':>6} | {'Put x':>6} | {'Diff':>5} | {'C Bid':>6} | {'P Bid':>6} | {'Gross':>8} | {'NET':>8} | {'NET %':>7}"
    print(header)
    print("-" * len(header))

    for r in results:
        sym_ok = "OK" if r["mult_diff"] <= 0.3 else "X "
        meets_target = "*" if r["net_pct"] >= 1.0 else " "
        print(f"{r['mult']:>5.1f}x | ${r['call_strike']:>6.0f} | ${r['put_strike']:>6.0f} | {r['call_mult']:>5.2f}x | {r['put_mult']:>5.2f}x | {r['mult_diff']:>4.2f}{sym_ok} | ${r['call_bid']:>5.2f} | ${r['put_bid']:>5.2f} | ${r['gross']:>7.2f} | ${r['net']:>7.2f} | {r['net_pct']:>6.2f}%{meets_target}")

    print()
    print("Legend: * = Meets 1% NET target, OK = Symmetric (diff <= 0.3x)")

    # Find best option that meets 1% and is symmetric
    print("\n" + "=" * 70)
    print("RECOMMENDATION")
    print("=" * 70)

    best = None
    for r in results:
        if r["net_pct"] >= 1.0 and r["mult_diff"] <= 0.3:
            if best is None or r["mult"] > best["mult"]:  # Prefer higher multiplier (safer)
                best = r

    if best:
        print(f"Best symmetric option meeting 1% target:")
        print(f"  Multiplier: {best['mult']}x expected move")
        print(f"  Call: ${best['call_strike']} ({best['call_mult']:.2f}x EM, Bid ${best['call_bid']:.2f})")
        print(f"  Put:  ${best['put_strike']} ({best['put_mult']:.2f}x EM, Bid ${best['put_bid']:.2f})")
        print(f"  Symmetry: {best['mult_diff']:.2f}x difference - OK")
        print(f"  Gross Premium: ${best['gross']:.2f}")
        print(f"  NET Return: ${best['net']:.2f} ({best['net_pct']:.2f}%)")
    else:
        # Find highest multiplier that meets 1%
        best_1pct = None
        for r in results:
            if r["net_pct"] >= 1.0:
                if best_1pct is None or r["mult"] > best_1pct["mult"]:
                    best_1pct = r

        if best_1pct:
            print(f"No symmetric option meets 1% target.")
            print(f"Highest multiplier with 1% return (but asymmetric):")
            print(f"  Multiplier: {best_1pct['mult']}x expected move")
            print(f"  Call: ${best_1pct['call_strike']} ({best_1pct['call_mult']:.2f}x EM)")
            print(f"  Put:  ${best_1pct['put_strike']} ({best_1pct['put_mult']:.2f}x EM)")
            print(f"  Symmetry: {best_1pct['mult_diff']:.2f}x difference - FAIL")
            print(f"  NET Return: {best_1pct['net_pct']:.2f}%")
        else:
            print("No options found that achieve 1% NET return at any multiplier.")
            print("Current market conditions (low VIX) do not support the target.")

if __name__ == "__main__":
    main()
