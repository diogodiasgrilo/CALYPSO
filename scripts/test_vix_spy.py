#!/usr/bin/env python3
"""Test VIX vs SPY API calls to compare responses."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
from shared.config_loader import load_config
import requests
import json

# Load config
config = load_config("bots/delta_neutral/config/config.json")
client = SaxoClient(config)

spy_uic = 36590
vix_uic = 10606

headers = {
    "Authorization": f"Bearer {client.access_token}",
    "Content-Type": "application/json"
}

print("=== Testing SPY (UIC 36590) as Etf ===")
endpoint = "/trade/v1/infoprices/list"
params = {
    "AccountKey": client.account_key,
    "Uics": str(spy_uic),
    "AssetType": "Etf",
    "FieldGroups": "DisplayAndFormat,Quote,PriceInfo,PriceInfoDetails"
}
url = f"{client.base_url}{endpoint}"
resp = requests.get(url, params=params, headers=headers)
print(f"Status: {resp.status_code}")
print(json.dumps(resp.json(), indent=2))

print()
print("=== Testing VIX (UIC 10606) as StockIndex ===")
params = {
    "AccountKey": client.account_key,
    "Uics": str(vix_uic),
    "AssetType": "StockIndex",
    "FieldGroups": "DisplayAndFormat,Quote,PriceInfo,PriceInfoDetails"
}
resp = requests.get(url, params=params, headers=headers)
print(f"Status: {resp.status_code}")
print(json.dumps(resp.json(), indent=2))

print()
print("=== Testing VIX with /trade/v1/prices (streaming endpoint) ===")
endpoint = "/trade/v1/prices"
params = {
    "AccountKey": client.account_key,
    "Uic": str(vix_uic),
    "AssetType": "StockIndex",
    "FieldGroups": "DisplayAndFormat,Quote,PriceInfo,PriceInfoDetails"
}
url = f"{client.base_url}{endpoint}"
resp = requests.get(url, params=params, headers=headers)
print(f"Status: {resp.status_code}")
print(json.dumps(resp.json(), indent=2))

print()
print("=== Testing VIX with /ref/v1/instruments/details ===")
endpoint = "/ref/v1/instruments/details"
params = {
    "Uics": str(vix_uic),
    "AssetTypes": "StockIndex",
    "FieldGroups": "OrderSetting,SupportedOrderTypeSettings,TradingSessions"
}
url = f"{client.base_url}{endpoint}"
resp = requests.get(url, params=params, headers=headers)
print(f"Status: {resp.status_code}")
print(json.dumps(resp.json(), indent=2)[:2000])
