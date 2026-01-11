import json
import logging
from saxo_client import SaxoClient

# Configure basic logging to see what's happening
logging.basicConfig(level=logging.INFO)

def run_search():
    # Load config
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("Error: config.json not found.")
        return

    client = SaxoClient(config)
    
    print("\nAUTHENTICATING...")
    if not client.authenticate():
        print("Authentication failed. Check config.json.")
        return

    # List of VALID Saxo AssetTypes to try
    # 'Futures' is invalid; 'ContractFutures' or 'CfdOnFutures' are correct.
    asset_types_to_try = [
        "StockIndex",       # The actual VIX Index
        "CfdOnIndex",       # CFD tracking the index
        "Etf",              # VIX ETFs (like VXX)
        "ContractFutures",  # Real Futures
        "CfdOnFutures"      # CFDs on Futures
    ]

    found_any = False

    print("\n" + "="*50)
    print("SEARCHING FOR 'VIX' INSTRUMENTS")
    print("="*50)

    for asset_type in asset_types_to_try:
        print(f"\n---> Trying AssetType: {asset_type}...")
        
        # We manually call _make_request to see the full list, not just the first one
        endpoint = "/ref/v1/instruments"
        params = {
            "Keywords": "VIX",
            "AssetTypes": asset_type,
            "IncludeNonTradable": "true" # Important: The VIX index itself is not tradable!
        }

        try:
            # Accessing the private method _make_request to get raw data
            # (The client.search_instrument method filters too aggressively)
            response = client._make_request("GET", endpoint, params=params)
            
            if response and "Data" in response and len(response["Data"]) > 0:
                for item in response["Data"]:
                    print(f"  [FOUND MATCH]")
                    print(f"  - Description: {item.get('Description')}")
                    print(f"  - Symbol:      {item.get('Symbol')}")
                    print(f"  - UIC:         {item.get('Identifier')}  <-- USE THIS IN CONFIG")
                    print(f"  - AssetType:   {item.get('AssetType')}")
                    print(f"  - Tradable:    {item.get('Tradable')}")
                    found_any = True
            else:
                print(f"  No results for {asset_type}.")
                
        except Exception as e:
            print(f"  Error searching {asset_type}: {e}")

    # Fallback Search: VXX (VIX ETF)
    # If we can't find the index, we can use the VXX ETF for data
    if not found_any:
        print("\n" + "="*50)
        print("FALLBACK: SEARCHING FOR 'VXX' (VIX ETF)")
        print("="*50)
        response = client.search_instrument("VXX", asset_type="Etf")
        if response:
             print(f"  [FOUND VXX]")
             print(f"  - Description: {response.get('Description')}")
             print(f"  - UIC:         {response.get('Identifier')}")
             print(f"  - AssetType:   {response.get('AssetType')}")

if __name__ == "__main__":
    run_search()