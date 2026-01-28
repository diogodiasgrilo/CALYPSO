#!/usr/bin/env python3
"""
Live Integration Test for WebSocket/Quote Fixes (2026-01-28)

This script tests the WebSocket fixes with actual Saxo Bank API.
Run this during market hours to verify streaming works correctly.

Tests:
1. WebSocket connection establishment
2. Cache population with timestamps
3. Health monitoring (thread alive, heartbeat, messages)
4. Quote retrieval (cache vs REST fallback)
5. Staleness detection

Usage:
    python scripts/test_websocket_live.py

Author: Calypso Trading Bot
Date: 2026-01-28
"""
import sys
import os
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
from shared.config_loader import ConfigLoader


def main():
    print("=" * 70)
    print("LIVE WEBSOCKET/QUOTE INTEGRATION TEST (2026-01-28)")
    print("=" * 70)
    print(f"Test started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Load config
    config_path = "bots/delta_neutral/config/config.json"
    config_loader = ConfigLoader(local_config_path=config_path)
    config = config_loader.load_config()

    # Create client
    client = SaxoClient(config)

    print("1. Authenticating...")
    if not client.authenticate():
        print("   ❌ FAIL: Authentication failed")
        return 1
    print("   ✅ PASS: Authenticated successfully")
    print()

    # Get SPY UIC
    spy_uic = config["strategy"]["underlying_uic"]  # 36590 for SPY
    vix_uic = config["strategy"]["vix_uic"]  # 10606 for VIX

    print("2. Testing REST API quote fetch...")
    quote = client.get_quote(spy_uic, "Etf", skip_cache=True)
    if quote and "Quote" in quote:
        bid = quote["Quote"].get("Bid", 0)
        ask = quote["Quote"].get("Ask", 0)
        print(f"   ✅ PASS: REST quote for SPY: Bid=${bid:.2f}, Ask=${ask:.2f}")
    else:
        print("   ❌ FAIL: No REST quote for SPY")
        return 1
    print()

    print("3. Starting WebSocket streaming...")
    subscriptions = [
        {"uic": spy_uic, "asset_type": "Etf"},
        {"uic": vix_uic, "asset_type": "StockIndex"},
    ]

    def price_callback(uic, data):
        quote_data = data.get("Quote", {})
        bid = quote_data.get("Bid", 0)
        ask = quote_data.get("Ask", 0)
        mid = quote_data.get("Mid", 0)
        last = data.get("PriceInfoDetails", {}).get("LastTraded", 0)
        print(f"   [STREAM] UIC {uic}: Bid=${bid:.2f}, Ask=${ask:.2f}, Mid=${mid:.2f}, Last=${last:.2f}")

    success = client.start_price_streaming(subscriptions, price_callback)
    if not success:
        print("   ❌ FAIL: WebSocket streaming failed to start")
        return 1
    print("   ✅ PASS: WebSocket streaming started")
    print()

    # Wait for initial data
    print("4. Waiting for WebSocket data (10 seconds)...")
    time.sleep(10)
    print()

    print("5. Checking WebSocket health...")
    is_healthy = client.is_websocket_healthy()
    print(f"   is_websocket_healthy(): {is_healthy}")
    print(f"   is_streaming: {client.is_streaming}")
    print(f"   ws_thread alive: {client.ws_thread.is_alive() if client.ws_thread else 'N/A'}")
    print(f"   _last_message_time: {client._last_message_time}")
    print(f"   _last_heartbeat_time: {client._last_heartbeat_time}")
    print(f"   _heartbeat_count: {client._heartbeat_count}")
    if is_healthy:
        print("   ✅ PASS: WebSocket is healthy")
    else:
        print("   ❌ FAIL: WebSocket is NOT healthy")
    print()

    print("6. Checking cache contents...")
    with client._price_cache_lock:
        cache_keys = list(client._price_cache.keys())
        print(f"   Cache contains UICs: {cache_keys}")
        for uic in cache_keys:
            entry = client._price_cache.get(uic, {})
            timestamp = entry.get('timestamp')
            data = entry.get('data', {})
            age = (datetime.now() - timestamp).total_seconds() if timestamp else -1
            quote_data = data.get("Quote", {}) if isinstance(data, dict) else {}
            bid = quote_data.get("Bid", 0)
            ask = quote_data.get("Ask", 0)
            print(f"   UIC {uic}: age={age:.1f}s, Bid=${bid:.2f}, Ask=${ask:.2f}")

    if spy_uic in cache_keys:
        print("   ✅ PASS: SPY in cache")
    else:
        print("   ❌ FAIL: SPY not in cache")
    print()

    print("7. Testing cache retrieval with staleness check...")
    cached_spy = client._get_from_cache(spy_uic, max_age_seconds=60)
    if cached_spy:
        bid = cached_spy.get("Quote", {}).get("Bid", 0)
        print(f"   ✅ PASS: Cache hit for SPY: Bid=${bid:.2f}")
    else:
        print("   ⚠️  WARN: Cache miss for SPY (may be stale)")
    print()

    print("8. Testing get_quote() uses cache when healthy...")
    start = time.time()
    quote = client.get_quote(spy_uic, "Etf")  # Should use cache
    elapsed = (time.time() - start) * 1000
    if quote and elapsed < 50:  # Cache hit should be < 50ms
        bid = quote.get("Quote", {}).get("Bid", 0)
        print(f"   ✅ PASS: get_quote() returned in {elapsed:.1f}ms (cache hit): Bid=${bid:.2f}")
    else:
        print(f"   ⚠️  WARN: get_quote() took {elapsed:.1f}ms (possible REST fallback)")
    print()

    print("9. Stopping WebSocket streaming...")
    client.stop_price_streaming()
    print("   ✅ PASS: Streaming stopped")
    print()

    print("10. Verifying cache cleared on stop...")
    with client._price_cache_lock:
        cache_size = len(client._price_cache)
    if cache_size == 0:
        print("   ✅ PASS: Cache cleared after stop")
    else:
        print(f"   ❌ FAIL: Cache still has {cache_size} entries")
    print()

    print("=" * 70)
    print("LIVE INTEGRATION TEST COMPLETE")
    print("=" * 70)
    print(f"Test completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
