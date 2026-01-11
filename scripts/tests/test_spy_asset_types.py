#!/usr/bin/env python3
"""
Test SPY with different asset types to see if any return price data.
"""

import json
from saxo_client import SaxoClient

def test_spy_all_asset_types():
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)

    # Initialize client
    client = SaxoClient(config)

    # Authenticate
    if not client.authenticate():
        print("Authentication failed!")
        return

    print("\n" + "="*70)
    print("TESTING SPY (UIC 36590) WITH DIFFERENT ASSET TYPES")
    print("="*70)

    spy_uic = 36590

    # Asset types to try
    asset_types = [
        "Etf",
        "Stock",
        "CfdOnStock",
        "CfdOnEtf",
        "StockOption",
        "EtfOption"
    ]

    endpoint = "/trade/v1/infoprices/list"

    for asset_type in asset_types:
        print(f"\n{'='*70}")
        print(f"Testing AssetType: {asset_type}")
        print('='*70)

        params = {
            "AccountKey": client.account_key,
            "Uics": str(spy_uic),
            "AssetType": asset_type,
            "Amount": 100,
            "FieldGroups": "DisplayAndFormat,Quote"
        }

        response = client._make_request("GET", endpoint, params=params)

        if response and "Data" in response and len(response["Data"]) > 0:
            data = response["Data"][0]
            quote = data.get("Quote", {})

            print(f"  ✓ Response received!")
            print(f"  - Description: {data.get('DisplayAndFormat', {}).get('Description')}")
            print(f"  - Symbol: {data.get('DisplayAndFormat', {}).get('Symbol')}")
            print(f"  - PriceTypeAsk: {quote.get('PriceTypeAsk')}")
            print(f"  - PriceTypeBid: {quote.get('PriceTypeBid')}")

            if quote.get('PriceTypeAsk') not in ['NoAccess', None]:
                print(f"  🎉 SUCCESS! This asset type has price access!")
                print(f"  - Mid: {quote.get('Mid')}")
                print(f"  - Ask: {quote.get('Ask')}")
                print(f"  - Bid: {quote.get('Bid')}")
                print(f"  - Full Quote: {json.dumps(quote, indent=6)}")
        elif response and "ErrorInfo" in response:
            print(f"  ✗ Error: {response['ErrorInfo']}")
        else:
            print(f"  ✗ No data returned")

    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print("If all asset types show 'NoAccess', then SPY price data is not")
    print("available in your Saxo simulation account regardless of how we")
    print("request it. You'll need to contact Saxo support.")
    print("="*70)

if __name__ == "__main__":
    test_spy_all_asset_types()
