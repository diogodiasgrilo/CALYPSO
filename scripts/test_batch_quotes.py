#!/usr/bin/env python3
"""
Test script: Verify get_quotes_batch() returns correct data for multiple UICs.

Uses real UICs from current bot positions (if any) or from the state file.

Run on VM:
    gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/test_batch_quotes.py'"
"""

import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.saxo_client import SaxoClient
from shared.config_loader import load_config


def main():
    print("=" * 60)
    print("TEST: get_quotes_batch() verification")
    print("=" * 60)

    # Load config and authenticate
    config = load_config("bots/meic_tf/config/config.json")
    client = SaxoClient(config)
    client.authenticate()
    print(f"\nAuthenticated. Account: {client.account_key}")

    # Step 1: Get real option UICs from the state file
    print("\n--- Step 1: Getting real option UICs from state file ---")
    state_file = "data/meic_tf_state.json"
    test_uics = []

    try:
        with open(state_file, "r") as f:
            state = json.load(f)
        entries = state.get("entries", [])
        for entry in entries:
            for leg in ["short_call_uic", "long_call_uic", "short_put_uic", "long_put_uic"]:
                uic = entry.get(leg, 0)
                if uic and uic not in test_uics:
                    test_uics.append(uic)
                    strike = entry.get(leg.replace("_uic", "_strike"), "?")
                    print(f"  {leg}: UIC {uic} (strike {strike})")
    except Exception as e:
        print(f"  State file error: {e}")

    # Fallback: Get UICs from live positions
    if not test_uics:
        print("  No UICs in state file, trying live positions...")
        positions = client.get_positions()
        for p in positions:
            uic = p.get("PositionBase", {}).get("Uic")
            if uic:
                test_uics.append(uic)
                strike = p.get("PositionBase", {}).get("OptionsData", {}).get("Strike", "?")
                print(f"  Position UIC {uic} (strike {strike})")

    if len(test_uics) < 2:
        print("ERROR: Need at least 2 UICs to test batching. No active positions found.")
        print("Falling back to known SPXW option UICs from today's option chain...")

        # Get today's option chain to find valid UICs
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        chain = client.get_option_chain(128, expiry_dates=[today])
        if chain and "OptionSpace" in chain:
            for expiry in chain["OptionSpace"]:
                options = expiry.get("SpecificOptions", [])
                for opt in options[:8]:  # Take first 8
                    uic = opt.get("Uic")
                    if uic and uic not in test_uics:
                        test_uics.append(uic)
                        strike = opt.get("StrikePrice", "?")
                        pc = opt.get("PutCall", "?")
                        print(f"  Chain UIC {uic}: Strike {strike} {pc}")
                    if len(test_uics) >= 8:
                        break
                if len(test_uics) >= 8:
                    break

    if len(test_uics) < 2:
        print("ERROR: Still couldn't find enough UICs. Aborting.")
        return

    print(f"\nTest UICs ({len(test_uics)}): {test_uics}")

    # Step 2: Test batch call
    print("\n--- Step 2: Batch quote call ---")
    batch_results = client.get_quotes_batch(test_uics, asset_type="StockIndexOption")
    print(f"Batch returned {len(batch_results)} quotes for {len(test_uics)} UICs requested")

    for uic in test_uics:
        if uic in batch_results:
            q = batch_results[uic]
            bid = q.get("Quote", {}).get("Bid", 0)
            ask = q.get("Quote", {}).get("Ask", 0)
            mid = (bid + ask) / 2 if bid and ask else 0
            resp_uic = q.get("DisplayAndFormat", {}).get("Uic", "?")
            print(f"  UIC {uic}: Bid={bid:.4f} Ask={ask:.4f} Mid={mid:.4f} (resp_uic={resp_uic})")
        else:
            print(f"  UIC {uic}: MISSING from batch response!")

    # Step 3: Compare with individual calls
    print("\n--- Step 3: Individual quote calls (comparison) ---")
    mismatches = 0
    for uic in test_uics[:4]:  # Only compare first 4 to avoid rate limits
        individual = client.get_quote(uic, asset_type="StockIndexOption")
        ind_bid = individual.get("Quote", {}).get("Bid", 0) if individual else 0
        ind_ask = individual.get("Quote", {}).get("Ask", 0) if individual else 0

        batch_q = batch_results.get(uic, {})
        bat_bid = batch_q.get("Quote", {}).get("Bid", 0)
        bat_ask = batch_q.get("Quote", {}).get("Ask", 0)

        bid_diff = abs(ind_bid - bat_bid)
        ask_diff = abs(ind_ask - bat_ask)

        # Allow $0.30 tolerance for time between calls
        status = "OK" if (bid_diff < 0.30 and ask_diff < 0.30) else "MISMATCH"
        if status == "MISMATCH":
            mismatches += 1

        print(f"  UIC {uic}: batch=({bat_bid:.4f}/{bat_ask:.4f}) vs individual=({ind_bid:.4f}/{ind_ask:.4f}) "
              f"diff=({bid_diff:.4f}/{ask_diff:.4f}) [{status}]")

    # Step 4: Edge cases
    print("\n--- Step 4: Edge cases ---")

    empty = client.get_quotes_batch([], asset_type="StockIndexOption")
    print(f"  Empty list: {len(empty)} results {'PASS' if len(empty) == 0 else 'FAIL'}")

    single = client.get_quotes_batch([test_uics[0]], asset_type="StockIndexOption")
    print(f"  Single UIC: {len(single)} results {'PASS' if test_uics[0] in single else 'FAIL'}")

    dupes = client.get_quotes_batch([test_uics[0], test_uics[0]], asset_type="StockIndexOption")
    print(f"  Duplicate UICs: {len(dupes)} results {'PASS' if len(dupes) == 1 else 'FAIL'}")

    mixed = client.get_quotes_batch([test_uics[0], 999999999], asset_type="StockIndexOption")
    has_valid = test_uics[0] in mixed
    no_invalid = 999999999 not in mixed
    print(f"  Mixed valid+invalid: {len(mixed)} results, valid={has_valid}, no_invalid={no_invalid} "
          f"{'PASS' if has_valid and no_invalid else 'FAIL'}")

    # Step 5: Summary
    print("\n" + "=" * 60)
    all_returned = all(uic in batch_results for uic in test_uics)
    print(f"Batch returned all UICs: {'PASS' if all_returned else 'FAIL'}")
    print(f"Price mismatches: {mismatches} (tolerance: $0.30 for time drift)")
    print(f"Overall: {'PASS' if all_returned and mismatches == 0 else 'CHECK RESULTS'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
