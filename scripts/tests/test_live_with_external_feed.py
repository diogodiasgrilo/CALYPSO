#!/usr/bin/env python3
"""
Test LIVE environment with external price feed as fallback.
"""

import json
from saxo_client import SaxoClient

def load_config():
    """Load configuration from config.json"""
    with open('config.json', 'r') as f:
        return json.load(f)

def main():
    print("=" * 80)
    print("TESTING LIVE ENVIRONMENT WITH EXTERNAL PRICE FEED")
    print("=" * 80)

    # Load config
    config = load_config()

    # Force LIVE environment
    config["saxo_api"]["environment"] = "live"

    # Ensure external feed is enabled
    config["external_price_feed"]["enabled"] = True

    # Initialize client
    client = SaxoClient(config)

    # Authenticate
    print("\n1. Authenticating with Saxo...")
    if not client.authenticate():
        print("❌ Authentication failed!")
        return
    print("✅ Authentication successful\n")

    # Test SPY price
    print("2. Getting SPY price...")
    print("-" * 80)

    spy_result = client.get_spy_price(36590, "SPY")

    if spy_result and "Quote" in spy_result:
        quote = spy_result["Quote"]
        price = quote.get("Mid") or quote.get("LastTraded")
        external = quote.get("_external_source", False)

        if price and price > 0:
            source = "Yahoo Finance (External)" if external else "Saxo API"
            print(f"✅ SPY Price: ${price:.2f}")
            print(f"   Source: {source}")
            spy_price = price
        else:
            print(f"❌ Failed to get SPY price (got: {spy_result})")
            spy_price = None
    else:
        print(f"❌ Failed to get SPY price (got: {spy_result})")
        spy_price = None

    print()

    # Test VIX price
    print("3. Getting VIX price...")
    print("-" * 80)

    vix_price = client.get_vix_price(10606)

    if vix_price and vix_price > 0:
        print(f"✅ VIX Price: {vix_price:.2f}")
    else:
        print(f"❌ Failed to get VIX price (got: {vix_price})")

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    if spy_price and spy_price > 0 and vix_price and vix_price > 0:
        print("✅ SUCCESS: Bot can get market data in LIVE mode using external feed")
        print("\nNote: This uses Yahoo Finance data (15-min delayed)")
        print("For real-time data, contact Saxo to enable API market data subscriptions")
    else:
        print("❌ FAILED: Still unable to get market data")

    print("=" * 80)

if __name__ == "__main__":
    main()
