#!/usr/bin/env python3
"""
Test script to check SPY price data with different field groups.
"""

import json
from saxo_client import SaxoClient

def test_spy_price():
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)

    # Initialize client
    client = SaxoClient(config)

    # Authenticate
    if not client.authenticate():
        print("Authentication failed!")
        return

    print("\n" + "="*60)
    print("TESTING SPY PRICE DATA (UIC 36590)")
    print("="*60)

    spy_uic = 36590

    # Test 1: Standard field groups
    print("\n1. Testing with DisplayAndFormat,Quote,PriceInfo:")
    endpoint = "/trade/v1/infoprices/list"
    params = {
        "AccountKey": client.account_key,
        "Uics": str(spy_uic),
        "AssetType": "Etf",
        "Amount": 1,
        "FieldGroups": "DisplayAndFormat,Quote,PriceInfo"
    }
    response = client._make_request("GET", endpoint, params=params)
    if response and "Data" in response:
        data = response["Data"][0]
        print(f"  - Description: {data.get('DisplayAndFormat', {}).get('Description')}")
        print(f"  - Symbol: {data.get('DisplayAndFormat', {}).get('Symbol')}")
        print(f"  - LastUpdated: {data.get('LastUpdated')}")
        print(f"  - Quote: {json.dumps(data.get('Quote', {}), indent=4)}")
        print(f"  - PriceInfo: {json.dumps(data.get('PriceInfo', {}), indent=4)}")

    # Test 2: Try PriceInfoDetails
    print("\n2. Testing with DisplayAndFormat,Quote,PriceInfoDetails:")
    params["FieldGroups"] = "DisplayAndFormat,Quote,PriceInfoDetails"
    response = client._make_request("GET", endpoint, params=params)
    if response and "Data" in response:
        data = response["Data"][0]
        print(f"  - Quote: {json.dumps(data.get('Quote', {}), indent=4)}")
        print(f"  - PriceInfoDetails: {json.dumps(data.get('PriceInfoDetails', {}), indent=4)}")

    # Test 3: Try ALL field groups
    print("\n3. Testing with ALL available field groups:")
    all_field_groups = "DisplayAndFormat,Quote,PriceInfo,PriceInfoDetails,Greeks,InstrumentPriceDetails"
    params["FieldGroups"] = all_field_groups
    response = client._make_request("GET", endpoint, params=params)
    if response and "Data" in response:
        data = response["Data"][0]
        for key in data.keys():
            if key not in ["Uic", "AssetType", "DisplayAndFormat"]:
                print(f"  - {key}: {json.dumps(data.get(key), indent=4)}")

    # Test 4: Try without AccountKey (to compare)
    print("\n4. Testing WITHOUT AccountKey (for comparison):")
    params_no_account = {
        "Uics": str(spy_uic),
        "AssetType": "Etf",
        "FieldGroups": "DisplayAndFormat,Quote,PriceInfo"
    }
    response = client._make_request("GET", endpoint, params=params_no_account)
    if response and "Data" in response:
        data = response["Data"][0]
        print(f"  - Quote: {json.dumps(data.get('Quote', {}), indent=4)}")
    else:
        print(f"  - Response: {response}")

    print("\n" + "="*60)
    print("CONCLUSION:")
    print("="*60)
    print("If ALL tests show 'NoAccess', this means:")
    print("  1. Your Saxo simulation account lacks US equity market data entitlements")
    print("  2. You need to contact Saxo support to request market data access")
    print("  3. This is a common limitation in paper trading accounts")
    print("\nFX data (like EURUSD) works because it's usually included by default.")
    print("="*60)

if __name__ == "__main__":
    test_spy_price()
