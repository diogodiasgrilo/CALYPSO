"""
find_strategy_uics.py - Pre-flight tool for Brian Terry Delta Neutral Strategy
Searches for SPY, VIX, and EURUSD to ensure your config.json is 100% correct.
"""

import json
import sys
from saxo_client import SaxoClient

def main():
    print("=" * 70)
    print("STRATEGY COMPONENT FINDER (SPY / VIX / EURUSD)")
    print("=" * 70)

    # 1. Load config
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("ERROR: config.json not found! Please create it first.")
        sys.exit(1)

    # 2. Initialize and Authenticate
    client = SaxoClient(config)
    print("Authenticating with Saxo...")
    if not client.authenticate():
        print("ERROR: Authentication failed! Check your token in config.json.")
        sys.exit(1)
    print("✓ Connected to Saxo Bank API")
    print("-" * 70)

    # 3. Define the Search Plan
    # We use specific AssetTypes to avoid getting "garbage" results
    search_plan = [
        {
            "label": "UNDERLYING ASSET",
            "query": "SPY",
            "asset_types": "Stock,CfdOnStock",
            "note": "Look for 'SPDR S&P 500 ETF TRUST' (Usually UIC 211)"
        },
        {
            "label": "VOLATILITY FILTER",
            "query": "VIX",
            "asset_types": "StockIndex,CfdOnIndex,Futures",
            "note": "Look for 'CBOE Volatility Index' (Usually UIC 14)"
        },
        {
            "label": "CURRENCY CONVERSION",
            "query": "EURUSD",
            "asset_types": "FxSpot",
            "note": "Needed for EUR account reporting (Usually UIC 21)"
        }
    ]

    search_endpoint = "/ref/v1/instruments"

    for item in search_plan:
        print(f"\n🔍 {item['label']}: Searching for '{item['query']}'...")
        
        params = {
            "Keywords": item['query'],
            "AssetTypes": item['asset_types'],
            "IncludeNonTradable": True,
            "limit": 5
        }

        try:
            results = client._make_request("GET", search_endpoint, params=params)
            
            if results and "Data" in results and len(results["Data"]) > 0:
                print(f"  Found {len(results['Data'])} matches. Recommended:")
                
                for inst in results["Data"]:
                    desc = inst.get('Description', 'Unknown')
                    uic = inst.get('Identifier', 'N/A')
                    a_type = inst.get('AssetType', 'N/A')
                    symbol = inst.get('Symbol', 'N/A')
                    
                    print(f"  → [{uic}] {desc}")
                    print(f"    Symbol: {symbol} | Type: {a_type}")
            else:
                print(f"  ✗ No matches found for '{item['query']}' with types '{item['asset_types']}'")
                print(f"    Tip: {item['note']}")

        except Exception as e:
            print(f"  ✗ API Error during search: {e}")

    print("\n" + "=" * 70)
    print("DONE! Update your config.json with the UIC numbers found above.")
    print("=" * 70)

if __name__ == "__main__":
    main()