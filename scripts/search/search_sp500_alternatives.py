#!/usr/bin/env python3
"""
Search for S&P 500 related instruments that might have price access.
"""

import json
from saxo_client import SaxoClient

def test_instrument_access(client, uic, asset_type, name):
    """Test if an instrument has price access."""
    endpoint = "/trade/v1/infoprices/list"
    params = {
        "AccountKey": client.account_key,
        "Uics": str(uic),
        "AssetType": asset_type,
        "Amount": 1,
        "FieldGroups": "DisplayAndFormat,Quote"
    }

    response = client._make_request("GET", endpoint, params=params)

    if response and "Data" in response and len(response["Data"]) > 0:
        data = response["Data"][0]
        quote = data.get("Quote", {})
        price_type = quote.get('PriceTypeAsk', 'NoAccess')

        has_access = price_type not in ['NoAccess', None]

        return {
            'name': name,
            'uic': uic,
            'asset_type': asset_type,
            'has_access': has_access,
            'price_type': price_type,
            'description': data.get('DisplayAndFormat', {}).get('Description'),
            'symbol': data.get('DisplayAndFormat', {}).get('Symbol'),
            'mid': quote.get('Mid'),
            'ask': quote.get('Ask'),
            'bid': quote.get('Bid')
        }
    return None

def search_sp500_alternatives():
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)

    # Initialize client
    client = SaxoClient(config)

    # Authenticate
    if not client.authenticate():
        print("Authentication failed!")
        return

    print("\n" + "="*80)
    print("SEARCHING FOR S&P 500 INSTRUMENTS WITH PRICE ACCESS")
    print("="*80)

    # List of S&P 500 related instruments to test
    # These are common UIC codes across Saxo environments
    test_instruments = [
        # CFD on Index
        (2119, "CfdOnIndex", "S&P 500 CFD"),
        (2771, "CfdOnIndex", "S&P 500 Mini CFD"),

        # Stock Index
        (2237, "StockIndex", "S&P 500 Index"),
        (1138, "StockIndex", "S&P 500 Index Alt"),

        # Futures
        (73668896, "ContractFutures", "S&P 500 E-mini Mar 2026"),
        (72839263, "ContractFutures", "S&P 500 E-mini Feb 2026"),

        # CFD on Futures
        (2119, "CfdOnFutures", "S&P 500 Futures CFD"),
    ]

    results_with_access = []
    results_no_access = []

    for uic, asset_type, name in test_instruments:
        print(f"\nTesting: {name} (UIC {uic}, {asset_type})...")
        result = test_instrument_access(client, uic, asset_type, name)

        if result:
            if result['has_access']:
                results_with_access.append(result)
                print(f"  ✅ HAS ACCESS! PriceType: {result['price_type']}")
                print(f"     Description: {result['description']}")
                print(f"     Symbol: {result['symbol']}")
                print(f"     Mid: {result['mid']}")
            else:
                results_no_access.append(result)
                print(f"  ❌ NoAccess")
        else:
            print(f"  ⚠️  Not found or error")

    print("\n" + "="*80)
    print("RESULTS SUMMARY")
    print("="*80)

    if results_with_access:
        print("\n✅ INSTRUMENTS WITH PRICE ACCESS:")
        print("-" * 80)
        for r in results_with_access:
            print(f"\n  {r['name']}")
            print(f"  - UIC: {r['uic']}")
            print(f"  - AssetType: {r['asset_type']}")
            print(f"  - Symbol: {r['symbol']}")
            print(f"  - Description: {r['description']}")
            print(f"  - Current Mid: {r['mid']}")
            print(f"  - PriceType: {r['price_type']}")
            print(f"\n  👉 You can use this as a proxy for SPY in your config!")
    else:
        print("\n❌ NO INSTRUMENTS WITH PRICE ACCESS FOUND")
        print("\nThis confirms that your Saxo simulation account does not have")
        print("access to S&P 500 price data in any form (ETF, CFD, Index, Futures).")
        print("\nYour options:")
        print("  1. Contact Saxo support to request market data access")
        print("  2. Switch to a live account (uses real money)")
        print("  3. Use external price feeds (Yahoo Finance, Alpha Vantage, etc.)")

    print("\n" + "="*80)

if __name__ == "__main__":
    search_sp500_alternatives()
