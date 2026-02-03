#!/usr/bin/env python3
"""
Test script to inspect Saxo API response for pre-market/after-hours prices.

Purpose: See exactly what data Saxo returns during extended hours sessions.
This helps understand which fields indicate pre-market vs regular hours.

Usage (on VM):
    gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/test_premarket_prices.py'"

Local usage (if you have valid tokens):
    python scripts/test_premarket_prices.py

Version: 1.0.0
Last Updated: 2026-02-03
"""
import sys
import os
import json
from datetime import datetime
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
from shared.config_loader import ConfigLoader
from shared.market_hours import (
    get_us_market_time,
    get_trading_session,
    is_saxo_price_available,
    is_pre_market,
    is_after_hours,
)


def print_json(data: dict, indent: int = 2):
    """Pretty print JSON data."""
    print(json.dumps(data, indent=indent, default=str))


def test_raw_infoprices_response(client: SaxoClient, uic: int, asset_type: str, symbol: str):
    """
    Make a raw REST API call to /trade/v1/infoprices and print the FULL response.

    This shows us exactly what Saxo returns, including any session-related fields.
    """
    print(f"\n{'='*70}")
    print(f"RAW API RESPONSE: {symbol} (UIC {uic}, AssetType: {asset_type})")
    print(f"{'='*70}")

    # Build the request exactly as get_quote() does
    url = f"{client.base_url}/trade/v1/infoprices"

    # Request ALL field groups to see everything Saxo provides
    params = {
        "AccountKey": client.account_key,
        "Uic": str(uic),
        "AssetType": asset_type,
        "FieldGroups": "DisplayAndFormat,Quote,PriceInfo,PriceInfoDetails,InstrumentPriceDetails,MarketDepth"
    }

    headers = {
        "Authorization": f"Bearer {client.access_token}",
        "Content-Type": "application/json"
    }

    print(f"\nRequest URL: {url}")
    print(f"Params: {json.dumps(params, indent=2)}")
    print()

    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        print(f"HTTP Status: {response.status_code}")
        print()

        if response.status_code == 200:
            data = response.json()
            print("FULL RESPONSE:")
            print_json(data)

            # Extract and highlight key fields
            print(f"\n{'-'*70}")
            print("KEY FIELDS EXTRACTED:")
            print(f"{'-'*70}")

            # Quote block
            quote = data.get("Quote", {})
            print(f"\nQuote:")
            print(f"  Bid: {quote.get('Bid', 'N/A')}")
            print(f"  Ask: {quote.get('Ask', 'N/A')}")
            print(f"  Mid: {quote.get('Mid', 'N/A')}")
            print(f"  LastTraded: {quote.get('LastTraded', 'N/A')}")
            print(f"  DelayedByMinutes: {quote.get('DelayedByMinutes', 'N/A')}")
            print(f"  PriceTypeBid: {quote.get('PriceTypeBid', 'N/A')}")
            print(f"  PriceTypeAsk: {quote.get('PriceTypeAsk', 'N/A')}")
            print(f"  MarketState: {quote.get('MarketState', 'N/A')}")

            # PriceInfo block
            price_info = data.get("PriceInfo", {})
            if price_info:
                print(f"\nPriceInfo:")
                for k, v in price_info.items():
                    print(f"  {k}: {v}")

            # PriceInfoDetails block
            price_info_details = data.get("PriceInfoDetails", {})
            if price_info_details:
                print(f"\nPriceInfoDetails:")
                for k, v in price_info_details.items():
                    print(f"  {k}: {v}")

            # InstrumentPriceDetails block
            instrument_details = data.get("InstrumentPriceDetails", {})
            if instrument_details:
                print(f"\nInstrumentPriceDetails:")
                for k, v in instrument_details.items():
                    print(f"  {k}: {v}")

            # DisplayAndFormat block
            display_format = data.get("DisplayAndFormat", {})
            if display_format:
                print(f"\nDisplayAndFormat:")
                print(f"  Symbol: {display_format.get('Symbol', 'N/A')}")
                print(f"  Description: {display_format.get('Description', 'N/A')}")
                print(f"  Currency: {display_format.get('Currency', 'N/A')}")

            return data
        else:
            print(f"ERROR: {response.text}")
            return None

    except Exception as e:
        print(f"ERROR: {e}")
        return None


def main():
    # Load config (use delta_neutral since it has SPY UICs)
    config_path = "bots/delta_neutral/config/config.json"
    config_loader = ConfigLoader(config_path)
    config = config_loader.load_config()

    client = SaxoClient(config)

    # Authenticate
    print("Authenticating with Saxo API...")
    if not client.authenticate():
        print("ERROR: Failed to authenticate with Saxo API")
        return

    # Get current market time and session
    et_now = get_us_market_time()
    session = get_trading_session()

    print()
    print("=" * 70)
    print("CURRENT MARKET STATUS")
    print("=" * 70)
    print(f"Current Time (ET): {et_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Current Time (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"Trading Session: {session}")
    print(f"is_pre_market(): {is_pre_market()}")
    print(f"is_after_hours(): {is_after_hours()}")
    print(f"is_saxo_price_available(): {is_saxo_price_available()}")

    # Test instruments
    # SPY is the main one we care about for Delta Neutral
    spy_uic = config["strategy"]["underlying_uic"]  # Should be SPY UIC

    # Also test VIX for comparison
    vix_uic = config["strategy"]["vix_uic"]

    # Instruments to test
    instruments = [
        {"uic": spy_uic, "asset_type": "Etf", "symbol": "SPY (ETF)"},
        {"uic": vix_uic, "asset_type": "StockIndex", "symbol": "VIX (Index)"},
        {"uic": 4913, "asset_type": "CfdOnIndex", "symbol": "US500.I (S&P 500 CFD)"},
    ]

    print()
    print("Testing the following instruments during " + session + " session:")
    for inst in instruments:
        print(f"  - {inst['symbol']}: UIC {inst['uic']}")

    # Test each instrument
    for inst in instruments:
        test_raw_infoprices_response(
            client,
            inst["uic"],
            inst["asset_type"],
            inst["symbol"]
        )

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("""
Key fields to look for in pre-market vs regular hours:

1. Quote.MarketState - May indicate session state
2. Quote.DelayedByMinutes - 0 = real-time, >0 = delayed
3. Quote.PriceTypeBid/PriceTypeAsk - "Tradable", "Indicative", "NoMarket", etc.
4. PriceInfo.LastTraded vs Quote.LastTraded - Compare for staleness
5. PriceInfoDetails - Additional price metadata

During PRE-MARKET (7:00-9:30 AM ET):
- Bid/Ask should be LIVE (updating)
- PriceTypeBid/PriceTypeAsk should be "Tradable" or "Indicative"
- DelayedByMinutes should be 0

During REGULAR HOURS (9:30 AM - 4:00 PM ET):
- Full liquidity, tighter spreads
- All price types should be "Tradable"

During AFTER-HOURS (4:00-5:00 PM ET):
- Similar to pre-market
- May have wider spreads

BEFORE 7:00 AM ET or AFTER 5:00 PM ET:
- Prices are STALE (yesterday's close)
- Do NOT use for trading decisions
""")


if __name__ == "__main__":
    main()
