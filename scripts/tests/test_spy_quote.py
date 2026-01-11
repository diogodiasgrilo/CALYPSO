#!/usr/bin/env python3
"""
Test SPY quote in detail to see what's being returned.
"""

import json
from saxo_client import SaxoClient

def load_config():
    """Load configuration from config.json"""
    with open('config.json', 'r') as f:
        return json.load(f)

def main():
    print("=" * 80)
    print("TESTING SPY QUOTE ACCESS")
    print("=" * 80)

    # Load config
    config = load_config()

    # Force LIVE environment
    config["saxo_api"]["environment"] = "live"

    # Initialize client
    client = SaxoClient(config)

    # Authenticate
    print("\n1. Authenticating with Saxo...")
    if not client.authenticate():
        print("❌ Authentication failed!")
        return
    print("✅ Authentication successful\n")

    # Test SPY quote
    print("2. Testing SPY (UIC 36590, Etf)...")
    print("-" * 80)

    quote = client.get_quote(36590, asset_type="Etf")

    if quote:
        print("✅ Received response:")
        print(json.dumps(quote, indent=2))
    else:
        print("❌ No response received")

    print("\n" + "=" * 80)

    # Test VIX quote
    print("\n3. Testing VIX (UIC 10606, StockIndex)...")
    print("-" * 80)

    vix_quote = client.get_quote(10606, asset_type="StockIndex")

    if vix_quote:
        print("✅ Received response:")
        print(json.dumps(vix_quote, indent=2))
    else:
        print("❌ No response received")

    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()
