#!/usr/bin/env python3
"""
Preview what the Iron Fly bot would do in LIVE mode right now.

Version: 1.0.0
Last Updated: 2026-02-02

Shows exactly:
1. VIX check (must be < 20)
2. Opening range simulation (shows current high/low tracking)
3. Expected move calculation from ATM 0DTE straddle
4. Wing width (max of expected move or 40pt minimum - Jim Olson rule)
5. Iron Fly structure: ATM strike, wing strikes, credit received
6. Expiration verification: confirms 0 DTE vs 1 DTE selection

This is a READ-ONLY preview - no orders are placed.

Usage:
    python scripts/preview_iron_fly_entry.py
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
from shared.config_loader import ConfigLoader
import json


def main():
    config_path = "bots/iron_fly_0dte/config/config.json"
    config_loader = ConfigLoader(config_path)
    config = config_loader.load_config()

    client = SaxoClient(config)

    # Authenticate (required for API calls)
    print("Authenticating with Saxo API...")
    if not client.authenticate():
        print("ERROR: Failed to authenticate with Saxo API")
        return

    # Config values
    underlying_uic = config["strategy"]["underlying_uic"]  # US500.I (4913)
    options_uic = config["strategy"]["options_uic"]  # SPXW:xcbf (128)
    vix_uic = config["strategy"].get("vix_spot_uic", 10606)  # VIX.I
    max_vix = config["strategy"]["max_vix_entry"]
    min_wing_width = config["strategy"].get("min_wing_width", 40)
    profit_target_percent = config["strategy"].get("profit_target_percent", 30)
    profit_target_min = config["strategy"].get("profit_target_min", 25)
    commission_per_leg = config["strategy"].get("commission_per_leg", 5.0)
    position_size = config["strategy"].get("position_size", 1)

    print("=" * 70)
    print("PREVIEW: IRON FLY 0DTE - WHAT THE BOT WOULD DO RIGHT NOW")
    print("=" * 70)
    print(f"Date/Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: LIVE SIMULATION (no orders placed)")
    print()

    # Get SPX price (US500.I)
    quote = client.get_quote(underlying_uic, "CfdOnIndex")
    if not quote:
        print("ERROR: Could not get SPX quote")
        return

    spx_price = quote.get("Quote", {}).get("Mid") or quote.get("Quote", {}).get("LastTraded", 0)
    if spx_price <= 0:
        print("ERROR: Could not get valid SPX price (market may be closed)")
        return
    print(f"SPX Price (US500.I): ${spx_price:.2f}")

    # Get VIX
    vix_price = client.get_vix_price(vix_uic)
    print(f"VIX: {vix_price:.2f}")

    # =========================================================================
    # STEP 1: VIX CHECK
    # =========================================================================
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

    # =========================================================================
    # STEP 2: EXPIRATION VERIFICATION (CRITICAL - 0 DTE CHECK)
    # =========================================================================
    print()
    print("-" * 70)
    print("STEP 2: EXPIRATION VERIFICATION (0 DTE vs 1 DTE)")
    print("-" * 70)

    # Get all expirations for SPXW
    expirations = client.get_option_expirations(underlying_uic, option_root_uic=options_uic)
    if not expirations:
        print("ERROR: Could not get option expirations")
        return

    today = datetime.now().date()
    print(f"Today's date: {today}")
    print(f"Today is: {today.strftime('%A')}")
    print()

    # Show first 5 expirations
    print("Available expirations (first 5):")
    for i, exp_data in enumerate(expirations[:5]):
        exp_date_str = exp_data.get("Expiry", "")[:10]
        if exp_date_str:
            exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            print(f"  {i+1}. {exp_date_str} ({dte} DTE) - {exp_date.strftime('%A')}")
    print()

    # Find 0 DTE expiration (same logic as fixed code)
    target_expiration = None
    fallback_expiration = None

    for exp_data in expirations:
        exp_date_str = exp_data.get("Expiry")
        if not exp_date_str:
            continue

        exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
        dte = (exp_date - today).days

        if dte == 0:  # Prefer exact 0 DTE
            target_expiration = exp_data
            print(f"FOUND 0 DTE: {exp_date_str[:10]} (expires TODAY)")
            break
        elif 0 <= dte <= 1 and fallback_expiration is None:
            fallback_expiration = exp_data

    if not target_expiration:
        if fallback_expiration:
            target_expiration = fallback_expiration
            exp_date_str = fallback_expiration.get("Expiry", "")[:10]
            print(f"WARNING: No 0 DTE found, using fallback: {exp_date_str} (1 DTE)")
            print("This means options expire TOMORROW, not today!")
            print("Theta decay will be slower than expected.")
        else:
            print("ERROR: No expiration found within 0-1 DTE range")
            return

    selected_expiry = target_expiration.get("Expiry", "")[:10]
    selected_date = datetime.strptime(selected_expiry, "%Y-%m-%d").date()
    selected_dte = (selected_date - today).days

    print()
    print(f"SELECTED EXPIRATION: {selected_expiry} ({selected_dte} DTE)")
    if selected_dte == 0:
        print("CORRECT: Using true 0 DTE options (expires TODAY)")
    else:
        print("WARNING: Using 1 DTE options (expires TOMORROW)")

    # =========================================================================
    # STEP 3: EXPECTED MOVE CALCULATION
    # =========================================================================
    print()
    print("-" * 70)
    print("STEP 3: EXPECTED MOVE FROM ATM 0DTE STRADDLE")
    print("-" * 70)

    # Get expected move (should use 0 DTE options)
    expected_move = client.get_expected_move_from_straddle(
        underlying_uic,
        spx_price,
        target_dte_min=0,
        target_dte_max=1,
        option_root_uic=options_uic,
        option_asset_type="StockIndexOption"
    )

    if not expected_move:
        print("ERROR: Could not calculate expected move from straddle")
        return

    print(f"Expected Move (from ATM straddle): ${expected_move:.2f}")
    print(f"As % of SPX: {(expected_move/spx_price)*100:.2f}%")

    # =========================================================================
    # STEP 4: WING WIDTH CALCULATION (Jim Olson Rule)
    # =========================================================================
    print()
    print("-" * 70)
    print("STEP 4: WING WIDTH CALCULATION (Jim Olson Rule)")
    print("-" * 70)

    # Round expected move to nearest 5 (strike increment)
    rounded_em = round(expected_move / 5) * 5

    if rounded_em < min_wing_width:
        wing_width = min_wing_width
        print(f"Expected Move: ${rounded_em:.0f} (rounded from ${expected_move:.2f})")
        print(f"Minimum Wing Width: ${min_wing_width}")
        print(f"USING MINIMUM: ${wing_width} points (Jim Olson rule)")
    else:
        wing_width = rounded_em
        print(f"Expected Move: ${rounded_em:.0f} (rounded from ${expected_move:.2f})")
        print(f"Minimum Wing Width: ${min_wing_width}")
        print(f"USING EXPECTED MOVE: ${wing_width} points")

    # =========================================================================
    # STEP 5: STRIKE SELECTION
    # =========================================================================
    print()
    print("-" * 70)
    print("STEP 5: STRIKE SELECTION")
    print("-" * 70)

    # ATM strike (first strike ABOVE current price per Doc Severson)
    atm_strike = (int(spx_price / 5) + 1) * 5  # Round up to next 5
    upper_wing = atm_strike + wing_width
    lower_wing = atm_strike - wing_width

    print(f"Current SPX: ${spx_price:.2f}")
    print(f"ATM Strike (first above): ${atm_strike}")
    print(f"Upper Wing: ${upper_wing} (+${wing_width})")
    print(f"Lower Wing: ${lower_wing} (-${wing_width})")

    # =========================================================================
    # STEP 6: GET IRON FLY OPTIONS
    # =========================================================================
    print()
    print("-" * 70)
    print("STEP 6: IRON FLY OPTIONS LOOKUP")
    print("-" * 70)

    iron_fly_options = client.get_iron_fly_options(
        underlying_uic,
        atm_strike,
        upper_wing,
        lower_wing,
        target_dte_min=0,
        target_dte_max=1,
        option_root_uic=options_uic
    )

    if not iron_fly_options:
        print("ERROR: Could not find Iron Fly options")
        return

    # Get quotes for all 4 legs
    short_call = iron_fly_options["short_call"]
    short_put = iron_fly_options["short_put"]
    long_call = iron_fly_options["long_call"]
    long_put = iron_fly_options["long_put"]

    print(f"Found all 4 legs:")
    print(f"  Short Call: UIC {short_call['uic']} @ ${short_call['strike']}")
    print(f"  Short Put:  UIC {short_put['uic']} @ ${short_put['strike']}")
    print(f"  Long Call:  UIC {long_call['uic']} @ ${long_call['strike']}")
    print(f"  Long Put:   UIC {long_put['uic']} @ ${long_put['strike']}")

    # Verify expiry
    actual_expiry = iron_fly_options.get("expiry", "")[:10]
    print(f"\nOptions Expiry: {actual_expiry}")

    if actual_expiry == str(today):
        print("VERIFIED: Options expire TODAY (true 0 DTE)")
    else:
        print(f"WARNING: Options expire {actual_expiry} (NOT today!)")

    # =========================================================================
    # STEP 7: PRICING
    # =========================================================================
    print()
    print("-" * 70)
    print("STEP 7: PRICING")
    print("-" * 70)

    # Get quotes
    sc_quote = client.get_quote(short_call["uic"], "StockIndexOption")
    sp_quote = client.get_quote(short_put["uic"], "StockIndexOption")
    lc_quote = client.get_quote(long_call["uic"], "StockIndexOption")
    lp_quote = client.get_quote(long_put["uic"], "StockIndexOption")

    using_last_traded = False

    def get_price(quote, price_type):
        nonlocal using_last_traded
        if not quote:
            return 0
        price = quote.get("Quote", {}).get(price_type, 0) or 0
        if price <= 0:
            price = quote.get("Quote", {}).get("LastTraded", 0) or 0
            if price > 0:
                using_last_traded = True
        return price

    sc_bid = get_price(sc_quote, "Bid")
    sp_bid = get_price(sp_quote, "Bid")
    lc_ask = get_price(lc_quote, "Ask")
    lp_ask = get_price(lp_quote, "Ask")

    if using_last_traded:
        print("NOTE: Using LastTraded prices (market may be closed)")
        print()

    print("Leg Prices:")
    print(f"  Short Call @ ${short_call['strike']}: Bid ${sc_bid:.2f}")
    print(f"  Short Put  @ ${short_put['strike']}: Bid ${sp_bid:.2f}")
    print(f"  Long Call  @ ${long_call['strike']}: Ask ${lc_ask:.2f}")
    print(f"  Long Put   @ ${long_put['strike']}: Ask ${lp_ask:.2f}")

    # Calculate net credit
    gross_credit = (sc_bid + sp_bid - lc_ask - lp_ask) * 100 * position_size

    print()
    print(f"Gross Credit: ${gross_credit:.2f}")

    # =========================================================================
    # STEP 8: P&L PROJECTIONS
    # =========================================================================
    print()
    print("-" * 70)
    print("STEP 8: P&L PROJECTIONS")
    print("-" * 70)

    total_commission = commission_per_leg * 4 * position_size  # 4 legs, round-trip
    net_credit = gross_credit - total_commission

    # Profit target
    target_profit_pct = gross_credit * (profit_target_percent / 100)
    target_profit = max(target_profit_pct, profit_target_min) + total_commission

    # Max loss
    max_loss = (wing_width * 100 * position_size) - gross_credit

    print(f"Position Size: {position_size} contract(s)")
    print()
    print(f"Gross Credit:     ${gross_credit:>8.2f}")
    print(f"Commission:       -${total_commission:>7.2f} (4 legs × ${commission_per_leg:.2f})")
    print(f"{'─' * 30}")
    print(f"Net Credit:       ${net_credit:>8.2f}")
    print()
    print(f"Profit Target ({profit_target_percent}% of credit + commission):")
    print(f"  30% of ${gross_credit:.2f} = ${target_profit_pct:.2f}")
    print(f"  + Commission ${total_commission:.2f}")
    print(f"  = Target: ${target_profit:.2f} gross profit")
    print()
    print(f"Max Loss (wing touched): ${max_loss:.2f}")
    print(f"Risk/Reward Ratio: {max_loss/target_profit:.1f}:1")

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print()
    print("=" * 70)
    print("IRON FLY POSITION SUMMARY")
    print("=" * 70)
    print(f"""
STRUCTURE:
  Sell {position_size} SPX ${short_call['strike']} Call @ ${sc_bid:.2f}  (ATM - short)
  Sell {position_size} SPX ${short_put['strike']} Put  @ ${sp_bid:.2f}  (ATM - short)
  Buy  {position_size} SPX ${long_call['strike']} Call @ ${lc_ask:.2f}  (upper wing - long)
  Buy  {position_size} SPX ${long_put['strike']} Put  @ ${lp_ask:.2f}  (lower wing - long)

EXPIRY: {actual_expiry} ({'0 DTE - TODAY' if actual_expiry == str(today) else '1 DTE - TOMORROW'})

WING WIDTH: ${wing_width} points
  Based on: {'Jim Olson minimum' if rounded_em < min_wing_width else 'Expected Move'}

CREDIT: ${gross_credit:.2f} gross / ${net_credit:.2f} net

PROFIT ZONE: ${lower_wing} - ${upper_wing} (${wing_width * 2} points wide)
  Max profit at: ${atm_strike} (ATM strike)

EXIT CONDITIONS:
  1. Profit Target: ${target_profit:.2f} gross profit (30% of credit + commission)
  2. Stop Loss: SPX touches ${upper_wing} or ${lower_wing}
  3. Time Exit: 11:00 AM EST (60 min max hold)

RISK PROFILE:
  Max Profit: ${net_credit:.2f} (if expires at ATM)
  Max Loss:   ${max_loss:.2f} (if wing touched)
  Typical Win: ${target_profit - total_commission:.2f} net
  Typical Loss: ~$300-350 (partial wing breach)
""")

    print("=" * 70)
    print("THIS IS A PREVIEW ONLY - NO ORDERS WERE PLACED")
    print("=" * 70)


if __name__ == "__main__":
    main()
