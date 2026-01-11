#!/usr/bin/env python3
"""
Find the correct UIC for SPY that your account can access.
"""

import json
from saxo_client import SaxoClient

def load_config():
    """Load configuration from config.json"""
    with open('config.json', 'r') as f:
        return json.load(f)

def main():
    print("=" * 80)
    print("FINDING CORRECT SPY UIC FOR YOUR ACCOUNT")
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

    # Search for SPY instruments
    print("2. Searching for SPY instruments...")
    endpoint = "/ref/v1/instruments"
    params = {
        "Keywords": "SPY",
        "AssetTypes": "Etf,Stock,StockOption",
        "IncludeNonTradable": "false",
        "limit": 50
    }

    response = client._make_request("GET", endpoint, params=params)

    if not response or "Data" not in response:
        print("❌ No results found!")
        return

    print(f"✅ Found {len(response['Data'])} instruments\n")
    print("=" * 80)
    print("AVAILABLE SPY INSTRUMENTS")
    print("=" * 80)

    # Filter for exact SPY matches
    spy_instruments = []
    for instrument in response["Data"]:
        symbol = instrument.get("Symbol", "")
        description = instrument.get("Description", "")

        # Look for exact SPY match (not SPYD, SPYG, etc.)
        if symbol == "SPY" or description.startswith("SPDR S&P 500 ETF"):
            spy_instruments.append(instrument)

    if not spy_instruments:
        print("❌ No exact SPY matches found!")
        print("\nShowing all results:")
        spy_instruments = response["Data"]

    # Display instruments
    for idx, instrument in enumerate(spy_instruments, 1):
        print(f"\n{idx}. {instrument.get('Description', 'N/A')}")
        print(f"   Symbol: {instrument.get('Symbol', 'N/A')}")
        print(f"   UIC: {instrument.get('Identifier', 'N/A')}")
        print(f"   Asset Type: {instrument.get('AssetType', 'N/A')}")
        print(f"   Exchange: {instrument.get('ExchangeId', 'N/A')}")
        print(f"   Currency: {instrument.get('CurrencyCode', 'N/A')}")
        print(f"   Tradable: {instrument.get('IsTradable', 'N/A')}")

        # Try to get a quote for this instrument
        uic = instrument.get('Identifier')
        asset_type = instrument.get('AssetType')

        if uic and asset_type:
            print(f"   Testing quote access...", end=" ")
            quote = client.get_quote(uic, asset_type=asset_type)
            if quote and "Quote" in quote:
                price = quote["Quote"].get("Mid") or quote["Quote"].get("LastTraded")
                if price:
                    print(f"✅ ACCESSIBLE - Price: ${price:.2f}")
                else:
                    print("⚠️  Response but no price")
            else:
                print("❌ No access")

    print("\n" + "=" * 80)
    print("\nRECOMMENDATION:")
    print("Update config.json with the UIC that shows '✅ ACCESSIBLE'")
    print("=" * 80)

if __name__ == "__main__":
    main()
