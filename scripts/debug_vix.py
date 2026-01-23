#!/usr/bin/env python3
"""Debug VIX data fetching."""

import sys
import os
import json
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
from shared.config_loader import load_config
import requests

config = load_config("bots/iron_fly_0dte/config/config.json")
client = SaxoClient(config)

vix_uic = 10606

# Subscribe to VIX to populate cache
print("=== Subscribing to VIX ===")
client.start_price_streaming([vix_uic], ["StockIndex"])

time.sleep(2)

print(f"\n=== Cache contents for VIX ===")
print(f"VIX in cache: {vix_uic in client._price_cache}")
if vix_uic in client._price_cache:
    cached = client._price_cache[vix_uic]
    print(f"Cached data type: {type(cached)}")
    print(f"Cached data: {json.dumps(cached, indent=2)}")

    # Try to extract price
    price = client._extract_price_from_data(cached, "cache test")
    print(f"Extracted price from cache: {price}")
else:
    print("VIX not in cache!")

print(f"\n=== REST API for VIX ===")
headers = {
    "Authorization": f"Bearer {client.access_token}",
    "Content-Type": "application/json"
}
params = {
    "AccountKey": client.account_key,
    "Uics": str(vix_uic),
    "AssetType": "StockIndex",
    "FieldGroups": "DisplayAndFormat,Quote,PriceInfo,PriceInfoDetails"
}
url = f"{client.base_url}/trade/v1/infoprices/list"
resp = requests.get(url, params=params, headers=headers)
print(f"Status: {resp.status_code}")
data = resp.json()
if "Data" in data and len(data["Data"]) > 0:
    rest_data = data["Data"][0]
    print(f"REST data: {json.dumps(rest_data, indent=2)}")
    price = client._extract_price_from_data(rest_data, "REST test")
    print(f"Extracted price from REST: {price}")

# Clean up
client.stop_price_streaming()
