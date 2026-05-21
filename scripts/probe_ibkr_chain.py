"""Probe IBKR paper's secdef option-chain endpoints — F3 design input.

Goal: learn whether IBKR's Client Portal Web API can return a full
SPXW option chain (all strikes → conids) in ONE call, or whether we
must resolve conids strike-by-strike. This decides the design of
HydraStrategy's get_option_chain rewrite (F3 of the IB-only rewrite).

Migration-diagnostic one-off. Safe to delete once F3 ships. Kept in
scripts/ (not /tmp) so it's inside the repo + version-controlled.

SAFE: read-only. No orders, no writes. Asserts paper environment.

Usage:
    cd "/Users/ddias/Desktop/CALYPSO/Git Repo"
    source .venv/bin/activate
    # (the 3 IBIND_OAUTH1A_* env vars must be exported in this shell)
    python scripts/probe_ibkr_chain.py 2>&1 | tee scripts/probe_chain_$(date +%H%M%S).log
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_client import IBClient, IBConfig, _ib_month_str
from shared.ib_oauth import load_credentials


def _next_trading_day(d: date) -> date:
    """Skip weekends — SPXW lists daily Mon-Fri expiries."""
    while d.weekday() >= 5:  # 5=Sat 6=Sun
        d = d + timedelta(days=1)
    return d


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
                print(f"  sample[0]: {json.dumps(sample, indent=2, default=str)[:600]}")
            else:
                print(f"  sample[0] ({type(sample).__name__}): {sample!r}")
    elif isinstance(data, dict):
        print(f"  type=dict  keys: {sorted(data.keys())}")
        print(f"  data: {json.dumps(data, indent=2, default=str)[:800]}")
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

        # 1. Resolve the SPX underlying conid via IBClient's qualify path
        print("\n=== Resolving SPX underlying conid ===")
        spx_conid = client.qualify_contract("SPX", sec_type="IND")
        print(f"SPX index conid: {spx_conid}")

        expiry = _next_trading_day(date.today())
        month = _ib_month_str(expiry)
        print(f"Probe expiry: {expiry.isoformat()}  month-arg: {month}")

        # 2. search_strikes_by_conid — the strikes-only endpoint
        r = None
        t0 = time.monotonic()
        try:
            r = raw.search_strikes_by_conid(
                conid=str(spx_conid), sec_type="OPT",
                month=month, exchange="CBOE",
            )
            _summarize("PROBE 1: search_strikes_by_conid (strikes only)",
                       r, time.monotonic() - t0)
        except Exception as exc:
            print(f"\nPROBE 1 raised: {exc}")

        # Determine a representative strike near SPX spot for probes 3-5
        probe_strike = None
        try:
            strikes_data = getattr(r, "data", {}) or {}
            calls = strikes_data.get("call") or strikes_data.get("calls") or []
            if calls:
                probe_strike = calls[len(calls) // 2]  # middle of chain
        except Exception:
            pass
        print(f"\nRepresentative strike for probes 3-5: {probe_strike}")

        # 3. search_secdef_info_by_conid WITH a specific strike + right
        if probe_strike is not None:
            t0 = time.monotonic()
            try:
                r = raw.search_secdef_info_by_conid(
                    conid=str(spx_conid), sec_type="OPT", month=month,
                    exchange="CBOE", strike=str(probe_strike), right="C",
                )
                _summarize(
                    "PROBE 3: secdef_info WITH strike+right (one option?)",
                    r, time.monotonic() - t0,
                )
            except Exception as exc:
                print(f"\nPROBE 3 raised: {exc}")

        # 4. search_secdef_info_by_conid WITH strike, WITHOUT right
        if probe_strike is not None:
            t0 = time.monotonic()
            try:
                r = raw.search_secdef_info_by_conid(
                    conid=str(spx_conid), sec_type="OPT", month=month,
                    exchange="CBOE", strike=str(probe_strike),
                )
                _summarize(
                    "PROBE 4: secdef_info WITH strike, NO right (both C+P?)",
                    r, time.monotonic() - t0,
                )
            except Exception as exc:
                print(f"\nPROBE 4 raised: {exc}")

        # 5. search_secdef_info_by_conid WITHOUT strike — the key probe.
        #    If this returns the full chain, get_option_chain is 1 call.
        t0 = time.monotonic()
        try:
            r = raw.search_secdef_info_by_conid(
                conid=str(spx_conid), sec_type="OPT", month=month,
                exchange="CBOE",
            )
            _summarize(
                "PROBE 5: secdef_info NO strike/right (FULL CHAIN in 1 call?)",
                r, time.monotonic() - t0,
            )
        except Exception as exc:
            print(f"\nPROBE 5 raised: {exc}")

        print("\n=== Probe complete ===")
        print("KEY QUESTION: did PROBE 5 return a full list of strikes "
              "with conids? If yes -> get_option_chain is 1 call. If it "
              "errored or returned one entry -> we resolve per-strike.")
    finally:
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
