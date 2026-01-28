#!/usr/bin/env python3
"""
Test script to verify all REST API calls work correctly for Delta Neutral bot.

This script tests all the price fetching and data retrieval that the bot needs,
ensuring REST-only mode will work properly before a live trading day.

Run locally:
    python scripts/test_rest_api.py

Run on VM:
    gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/test_rest_api.py'"
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import time

def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def print_result(test_name: str, passed: bool, details: str = ""):
    """Print test result."""
    status = "PASS" if passed else "FAIL"
    symbol = "[OK]" if passed else "[X]"
    print(f"  {symbol} {test_name}: {status}")
    if details:
        print(f"      {details}")

def main():
    print_header("Delta Neutral REST API Test Suite")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}")
    print(f"  Purpose: Verify all REST API calls work before live trading")

    # Track results
    results = {"passed": 0, "failed": 0}

    # Initialize client
    print_header("1. INITIALIZATION")

    try:
        from shared.saxo_client import SaxoClient
        from shared.config_loader import ConfigLoader

        # Load config from Delta Neutral bot's config directory
        config_loader = ConfigLoader("bots/delta_neutral/config/config.json")
        config = config_loader.load_config()
        client = SaxoClient(config)
        client.authenticate()
        print_result("Saxo Client Initialization", True, f"Environment: {client.environment}")
        results["passed"] += 1
    except Exception as e:
        print_result("Saxo Client Initialization", False, str(e))
        results["failed"] += 1
        print("\nCannot continue without client. Exiting.")
        return 1

    # Get UICs from config
    underlying_uic = config.get("strategy", {}).get("underlying_uic", 36590)  # SPY default
    vix_uic = config.get("strategy", {}).get("vix_uic", 10606)  # VIX default

    print(f"\n  Config: SPY UIC={underlying_uic}, VIX UIC={vix_uic}")

    # =========================================================================
    # TEST 2: SPY Price
    # =========================================================================
    print_header("2. SPY PRICE (get_spy_price)")

    try:
        quote = client.get_spy_price(underlying_uic, symbol="SPY")
        if quote and "Quote" in quote:
            q = quote["Quote"]
            mid = q.get("Mid", 0)
            bid = q.get("Bid", 0)
            ask = q.get("Ask", 0)
            last = q.get("LastTraded", 0)

            price = mid or last or bid or ask
            if price and price > 0:
                print_result("SPY Price Fetch", True, f"${price:.2f}")
                print(f"      Mid=${mid}, Bid=${bid}, Ask=${ask}, Last=${last}")
                results["passed"] += 1
            else:
                print_result("SPY Price Fetch", False, f"Price is 0 or invalid")
                results["failed"] += 1
        else:
            print_result("SPY Price Fetch", False, f"No Quote in response: {quote}")
            results["failed"] += 1
    except Exception as e:
        print_result("SPY Price Fetch", False, str(e))
        results["failed"] += 1

    # =========================================================================
    # TEST 3: VIX Price
    # =========================================================================
    print_header("3. VIX PRICE (get_vix_price)")

    try:
        vix_price = client.get_vix_price(vix_uic)
        if vix_price and vix_price > 0:
            print_result("VIX Price Fetch", True, f"{vix_price:.2f}")
            results["passed"] += 1
        else:
            print_result("VIX Price Fetch", False, f"VIX price is 0 or None: {vix_price}")
            results["failed"] += 1
    except Exception as e:
        print_result("VIX Price Fetch", False, str(e))
        results["failed"] += 1

    # =========================================================================
    # TEST 4: Option Chain
    # =========================================================================
    print_header("4. OPTION CHAIN (get_option_root_id + get_option_chain)")

    chain = None
    option_root_id = None

    try:
        # First get the option root ID for SPY
        option_root_id = client.get_option_root_id(underlying_uic)

        if option_root_id:
            print(f"      Option Root ID: {option_root_id}")

            # Get option chain
            chain_response = client.get_option_chain(option_root_id)

            if chain_response and "OptionSpace" in chain_response:
                option_space = chain_response["OptionSpace"]
                total_options = 0

                # Count total options across all expiries
                for expiry in option_space:
                    specific_options = expiry.get("SpecificOptions", [])
                    total_options += len(specific_options)

                print_result("Option Chain Fetch", True, f"{len(option_space)} expiries, {total_options} options")

                # Show first expiry details
                if option_space:
                    first_expiry = option_space[0]
                    exp_date = first_expiry.get("Expiry", "Unknown")
                    opts = first_expiry.get("SpecificOptions", [])
                    print(f"      First expiry: {exp_date} ({len(opts)} options)")

                    # Store for later tests
                    chain = opts

                results["passed"] += 1
            else:
                print_result("Option Chain Fetch", False, f"No OptionSpace in response")
                results["failed"] += 1
        else:
            print_result("Option Chain Fetch", False, "Could not get option root ID")
            results["failed"] += 1
    except Exception as e:
        print_result("Option Chain Fetch", False, str(e))
        results["failed"] += 1

    # =========================================================================
    # TEST 5: Single Option Quote
    # =========================================================================
    print_header("5. SINGLE OPTION QUOTE (get_quote)")

    option_uic = None

    try:
        # Find an ATM option to quote
        if chain and len(chain) > 0:
            # Get current SPY price for ATM
            spy_quote = client.get_spy_price(underlying_uic, symbol="SPY")
            spy_price = spy_quote["Quote"].get("Mid", 0) or spy_quote["Quote"].get("LastTraded", 0)

            # Find closest call to ATM
            calls = [o for o in chain if o.get("PutCall") == "Call"]
            if calls and spy_price > 0:
                closest_call = min(calls, key=lambda o: abs(o.get("Strike", 0) - spy_price))
                option_uic = closest_call.get("Uic")
                strike = closest_call.get("Strike")

                # Get quote for this option
                option_quote = client.get_quote(option_uic, "StockOption")

                if option_quote and "Quote" in option_quote:
                    oq = option_quote["Quote"]
                    bid = oq.get("Bid", 0)
                    ask = oq.get("Ask", 0)
                    mid = oq.get("Mid", 0)

                    if bid > 0 or ask > 0 or mid > 0:
                        print_result("Option Quote Fetch", True, f"Strike ${strike} Call")
                        print(f"      UIC={option_uic}, Bid=${bid:.2f}, Ask=${ask:.2f}, Mid=${mid:.2f}")
                        results["passed"] += 1
                    else:
                        print_result("Option Quote Fetch", False, f"All prices are 0 (may be outside market hours)")
                        results["failed"] += 1
                else:
                    print_result("Option Quote Fetch", False, f"No Quote in response")
                    results["failed"] += 1
            else:
                print_result("Option Quote Fetch", False, "No calls found or SPY price unavailable")
                results["failed"] += 1
        else:
            print_result("Option Quote Fetch", False, "Skipped - no option chain available")
            results["failed"] += 1
    except Exception as e:
        print_result("Option Quote Fetch", False, str(e))
        results["failed"] += 1

    # =========================================================================
    # TEST 6: Option Greeks
    # =========================================================================
    print_header("6. OPTION GREEKS (get_option_greeks)")

    try:
        if 'option_uic' in dir() and option_uic:
            greeks = client.get_option_greeks(option_uic)

            if greeks:
                delta = greeks.get("Delta", "N/A")
                theta = greeks.get("Theta", "N/A")
                gamma = greeks.get("Gamma", "N/A")
                vega = greeks.get("Vega", "N/A")

                print_result("Option Greeks Fetch", True, f"UIC {option_uic}")
                print(f"      Delta={delta}, Theta={theta}, Gamma={gamma}, Vega={vega}")
                results["passed"] += 1
            else:
                print_result("Option Greeks Fetch", False, "No greeks returned (may be normal outside market hours)")
                results["failed"] += 1
        else:
            print_result("Option Greeks Fetch", False, "Skipped - no option UIC available")
            results["failed"] += 1
    except Exception as e:
        print_result("Option Greeks Fetch", False, str(e))
        results["failed"] += 1

    # =========================================================================
    # TEST 7: Current Positions
    # =========================================================================
    print_header("7. CURRENT POSITIONS (get_positions)")

    try:
        positions = client.get_positions()

        if positions is not None:
            print_result("Positions Fetch", True, f"{len(positions)} positions found")

            # Show SPY options if any
            spy_options = [p for p in positions
                          if p.get("PositionBase", {}).get("AssetType") == "StockOption"
                          and "SPY" in str(p.get("DisplayAndFormat", {}).get("Description", ""))]

            if spy_options:
                print(f"      SPY Options: {len(spy_options)}")
                for pos in spy_options[:3]:  # Show first 3
                    desc = pos.get("DisplayAndFormat", {}).get("Description", "Unknown")
                    amount = pos.get("PositionBase", {}).get("Amount", 0)
                    print(f"        - {desc} (Qty: {amount})")

            results["passed"] += 1
        else:
            print_result("Positions Fetch", False, "None returned")
            results["failed"] += 1
    except Exception as e:
        print_result("Positions Fetch", False, str(e))
        results["failed"] += 1

    # =========================================================================
    # TEST 8: Open Orders
    # =========================================================================
    print_header("8. OPEN ORDERS (get_open_orders)")

    try:
        orders = client.get_open_orders()

        if orders is not None:
            print_result("Orders Fetch", True, f"{len(orders)} open orders")
            results["passed"] += 1
        else:
            print_result("Orders Fetch", False, "None returned")
            results["failed"] += 1
    except Exception as e:
        print_result("Orders Fetch", False, str(e))
        results["failed"] += 1

    # =========================================================================
    # TEST 9: Account Info
    # =========================================================================
    print_header("9. ACCOUNT INFO (get_account_info)")

    try:
        account_info = client.get_account_info()

        if account_info:
            balance = account_info.get("TotalValue", "N/A")
            cash = account_info.get("CashBalance", "N/A")
            margin = account_info.get("MarginAvailableForTrading", "N/A")

            print_result("Account Info Fetch", True)
            print(f"      Total Value: ${balance}")
            print(f"      Cash Balance: ${cash}")
            print(f"      Margin Available: ${margin}")
            results["passed"] += 1
        else:
            print_result("Account Info Fetch", False, "No account info returned")
            results["failed"] += 1
    except Exception as e:
        print_result("Account Info Fetch", False, str(e))
        results["failed"] += 1

    # =========================================================================
    # TEST 10: Rate Limit Check (Multiple rapid calls)
    # =========================================================================
    print_header("10. RATE LIMIT TEST (5 rapid SPY price calls)")

    try:
        start_time = time.time()
        success_count = 0

        for i in range(5):
            quote = client.get_spy_price(underlying_uic, symbol="SPY")
            if quote and "Quote" in quote:
                price = quote["Quote"].get("Mid", 0) or quote["Quote"].get("LastTraded", 0)
                if price > 0:
                    success_count += 1

        elapsed = time.time() - start_time

        if success_count == 5:
            print_result("Rate Limit Test", True, f"5/5 calls succeeded in {elapsed:.2f}s")
            results["passed"] += 1
        else:
            print_result("Rate Limit Test", False, f"Only {success_count}/5 calls succeeded")
            results["failed"] += 1
    except Exception as e:
        print_result("Rate Limit Test", False, str(e))
        results["failed"] += 1

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print_header("SUMMARY")

    total = results["passed"] + results["failed"]
    pass_rate = (results["passed"] / total * 100) if total > 0 else 0

    print(f"\n  Passed: {results['passed']}/{total} ({pass_rate:.0f}%)")
    print(f"  Failed: {results['failed']}/{total}")

    if results["failed"] == 0:
        print("\n  [SUCCESS] All REST API tests passed!")
        print("  The bot is ready for live trading with REST-only mode.")
        return 0
    else:
        print("\n  [WARNING] Some tests failed!")
        print("  Review the failures above before going live.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
