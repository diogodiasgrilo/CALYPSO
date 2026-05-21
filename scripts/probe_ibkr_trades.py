"""Probe IBKR paper's trade-execution history — F5 design input.

Goal: learn the shape of IBKR's Client Portal trade-execution endpoint
(`/iserver/account/trades`, ibind `IbkrClient.trades()`) so F5 can
build `IBClient.get_closed_position_price` on verified behavior. We
need to know, for each execution record:
  - the conid field name (conid vs contract id)
  - the side field (Buy/Sell — and its exact values)
  - the price field (execution price)
  - the timestamp field + format
  - how far back `trades()` reaches, and whether SAME-DAY 0DTE
    executions appear (HYDRA closes 0DTE intraday — the close must be
    visible the same session)

Migration-diagnostic one-off. Safe to delete once F5 ships. Kept in
scripts/ (not /tmp) so it's inside the repo + version-controlled.

SAFE: read-only. No orders, no writes. Asserts paper environment.

Usage:
    cd "/Users/ddias/Desktop/CALYPSO/Git Repo"
    source .venv/bin/activate
    # (the 3 IBIND_OAUTH1A_* env vars must be exported in this shell)
    python scripts/probe_ibkr_trades.py 2>&1 | tee scripts/probe_trades_$(date +%H%M%S).log
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_client import IBClient, IBConfig
from shared.ib_oauth import load_credentials


def _summarize(label: str, result, elapsed_s: float) -> None:
    """Print a compact shape summary of an ibind Result / raw response."""
    print(f"\n━━━ {label}  ({elapsed_s * 1000:.0f} ms) ━━━")
    data = getattr(result, "data", result)
    err = getattr(result, "error", None)
    if err:
        print(f"  ERROR: {err}")
        return
    if data is None:
        print("  data = None")
        return
    if isinstance(data, list):
        print(f"  type=list  len={len(data)}")
        if data:
            sample = data[0]
            if isinstance(sample, dict):
                print(f"  sample[0] keys: {sorted(sample.keys())}")
                print(f"  sample[0]: {json.dumps(sample, indent=2, default=str)[:1000]}")
            else:
                print(f"  sample[0] ({type(sample).__name__}): {sample!r}")
    elif isinstance(data, dict):
        print(f"  type=dict  keys: {sorted(data.keys())}")
        print(f"  data: {json.dumps(data, indent=2, default=str)[:1000]}")
    else:
        print(f"  type={type(data).__name__}: {data!r}")


def main() -> int:
    creds = load_credentials("paper")
    assert creds.environment == "paper", (
        f"SAFETY: probe must run on paper, got {creds.environment!r}"
    )
    client = IBClient(IBConfig(credentials=creds))
    try:
        client.connect()
        raw = client._client  # the underlying ibind IbkrClient

        # /iserver/account/trades requires the brokerage session to be
        # initialized via /iserver/accounts first — without it IBKR
        # returns 500 "Please query /accounts first". connect() only
        # primed /portfolio/accounts (a different endpoint), so prime
        # /iserver/accounts explicitly here. F5.2's IBClient method will
        # need the same priming.
        print("\n=== Priming /iserver/accounts (receive_brokerage_accounts) ===")
        t0 = time.monotonic()
        try:
            pr = raw.receive_brokerage_accounts()
            _summarize("PRIME: receive_brokerage_accounts",
                       pr, time.monotonic() - t0)
        except Exception as exc:
            print(f"PRIME raised: {type(exc).__name__}: {exc}")

        # 1. trades() with no args — the default lookback window.
        t0 = time.monotonic()
        try:
            r = raw.trades()
            _summarize("PROBE 1: trades() — default lookback", r,
                       time.monotonic() - t0)
        except Exception as exc:
            print(f"\nPROBE 1 raised: {type(exc).__name__}: {exc}")

        # 2. trades(days=...) — does an explicit lookback widen the window?
        #    IBKR's CP API documents a `days` query param (max 7).
        for days in ("1", "7"):
            t0 = time.monotonic()
            try:
                r = raw.trades(days=days)
                _summarize(f"PROBE 2: trades(days={days!r})", r,
                           time.monotonic() - t0)
            except Exception as exc:
                print(f"\nPROBE 2 (days={days}) raised: "
                      f"{type(exc).__name__}: {exc}")

        # 3. Field inventory — for whatever PROBE 1 returned, enumerate
        #    the candidate fields F5 needs (conid / side / price / time).
        try:
            r = raw.trades()
            data = getattr(r, "data", r) or []
            if isinstance(data, list) and data:
                rec = data[0]
                if isinstance(rec, dict):
                    print("\n━━━ PROBE 3: field inventory (first record) ━━━")
                    for key in ("conid", "conidEx", "contract_id", "side",
                                "buy_sell", "price", "size", "trade_time",
                                "trade_time_r", "execution_id", "symbol",
                                "secType", "sec_type", "order_ref"):
                        if key in rec:
                            print(f"  {key!r:18} = {rec[key]!r}")
                    missing = [k for k in ("conid", "side", "price")
                               if k not in rec]
                    print(f"  → F5-critical fields missing: {missing or 'NONE'}")
            else:
                print("\nPROBE 3: no trade records to inventory "
                      "(account may have no recent executions)")
        except Exception as exc:
            print(f"\nPROBE 3 raised: {type(exc).__name__}: {exc}")

        print("\n=== Probe complete ===")
        print("DESIGN QUESTIONS for F5.2 (IBClient.get_closed_position_price):")
        print(" - PROBE 1/2: how far back does trades() reach? same-day 0DTE?")
        print(" - PROBE 3: exact conid / side / price field names")
        print(" - if trades() does NOT surface same-day executions, F5.2")
        print("   must use a different endpoint — flag for re-design")
    finally:
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
