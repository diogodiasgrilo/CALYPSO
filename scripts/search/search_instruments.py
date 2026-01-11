"""
search_instruments.py - Search for available instruments in Saxo

This script searches for volatility-related instruments and shows
what's available in your current Saxo environment.
"""

import json
import sys
from saxo_client import SaxoClient

def search_instruments(client, keywords, asset_types=None):
    """Search for instruments by keyword."""
    search_endpoint = "/ref/v1/instruments"

    params = {
        "Keywords": keywords,
        "limit": 20
    }

    if asset_types:
        params["AssetTypes"] = asset_types

    try:
        results = client._make_request("GET", search_endpoint, params=params)
        if results and "Data" in results:
            return results["Data"]
    except Exception as e:
        print(f"  Error: {e}")

    return []

def main():
    print("=" * 70)
    print("INSTRUMENT SEARCH - Saxo OpenAPI")
    print("=" * 70)
    print()

    # Load config
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("ERROR: config.json not found!")
        sys.exit(1)

    # Initialize client
    print("Initializing Saxo client...")
    client = SaxoClient(config)

    # Authenticate
    print("Authenticating...")
    if not client.authenticate():
        print("ERROR: Authentication failed!")
        sys.exit(1)

    print("Authentication successful!")
    print()

    # Test 1: Check if SPY (UIC 211) works
    print("-" * 70)
    print("TEST 1: Checking SPY (UIC 211)...")
    spy_quote = client.get_quote(211, asset_type="Stock")
    if spy_quote and "Quote" in spy_quote:
        price = spy_quote["Quote"].get("Mid") or spy_quote["Quote"].get("LastTraded", 0)
        print(f"  SPY Price: ${price:.2f}")
    else:
        print("  SPY: Not available or no quote")
    print()

    # Test 2: Check VIX with various UICs and asset types
    print("-" * 70)
    print("TEST 2: Checking VIX variations...")

    vix_tests = [
        (10606, "StockIndex", "VIX.I - StockIndex"),
        (10606, "CfdOnIndex", "VIX.I - CfdOnIndex"),
        (19217, "StockIndex", "UIC 19217 - StockIndex"),
        (19217, "CfdOnIndex", "UIC 19217 - CfdOnIndex"),
    ]

    for uic, asset_type, desc in vix_tests:
        quote = client.get_quote(uic, asset_type=asset_type)
        if quote and "Quote" in quote:
            price = quote["Quote"].get("Mid") or quote["Quote"].get("LastTraded", 0)
            print(f"  {desc}: {price}")
        else:
            print(f"  {desc}: NOT AVAILABLE")
    print()

    # Test 3: Search for volatility instruments
    print("-" * 70)
    print("TEST 3: Searching for volatility instruments...")

    search_terms = [
        ("VIX", None),
        ("VOLATILITY", None),
        ("CBOE", None),
        ("VIX", "CfdOnIndex"),
        ("VIX", "StockIndex"),
        ("VXX", None),  # VIX ETF
        ("UVXY", None),  # VIX ETF
        ("VIXY", None),  # VIX ETF
    ]

    found_instruments = {}

    for term, asset_type in search_terms:
        print(f"\n  Searching: '{term}'" + (f" (AssetType: {asset_type})" if asset_type else ""))
        instruments = search_instruments(client, term, asset_type)

        if instruments:
            print(f"  Found {len(instruments)} instruments:")
            for inst in instruments[:5]:  # Show top 5
                uic = inst.get("Identifier", "N/A")
                symbol = inst.get("Symbol", "N/A")
                desc = inst.get("Description", "N/A")
                asset = inst.get("AssetType", "N/A")

                key = f"{uic}-{asset}"
                if key not in found_instruments:
                    found_instruments[key] = inst
                    print(f"    - UIC: {uic} | {symbol} | {asset} | {desc[:50]}")
        else:
            print(f"    No results")

    print()
    print("-" * 70)
    print("SUMMARY")
    print("-" * 70)

    if found_instruments:
        print("\nAvailable volatility-related instruments:")
        for key, inst in found_instruments.items():
            uic = inst.get("Identifier", "N/A")
            symbol = inst.get("Symbol", "N/A")
            desc = inst.get("Description", "N/A")
            asset = inst.get("AssetType", "N/A")
            print(f"  UIC: {uic:8} | {symbol:15} | {asset:15} | {desc}")
    else:
        print("\nNo volatility instruments found in your environment.")
        print("\nThis is common in Saxo's simulation environment.")
        print("VIX data may only be available in the live environment.")

    print()
    print("-" * 70)
    print("RECOMMENDATIONS")
    print("-" * 70)
    print("""
Option 1: DISABLE VIX CHECK (Recommended for testing)
  Edit config.json and set:
  "max_vix_entry": 99999.0

  This lets the bot trade regardless of VIX level.

Option 2: USE A VIX ETF AS PROXY
  If VXX, UVXY, or VIXY is available, you could modify the bot
  to use that as a volatility proxy (requires code changes).

Option 3: SWITCH TO LIVE ENVIRONMENT
  VIX data is typically available in Saxo's live environment.
  Change config.json: "environment": "live"
  (Use with caution - real money!)

Option 4: HARDCODE A VIX VALUE FOR TESTING
  The bot already handles missing VIX gracefully -
  it defaults to 0.0 which is below 18.0, so trades can proceed.
""")
    print("=" * 70)

if __name__ == "__main__":
    main()
