"""P7 Step 2 — IBKR paper market-data diagnostic.

The first Step-2 run returned all-None for SPX + VIX during market
hours. This rewrite is a *diagnostic*: it figures out WHY by

  1. testing SPY (a plain US-listed ETF) alongside the indices and
     dumping both. NOTE — updated 2026-07-06: the entitlement on THIS
     paper account is the INVERSE of the original premise below. SPX +
     VIX return REAL-TIME (6509 first char 'R'), while SPY returns
     DELAYED (6509='DP') — the account carries the index real-time
     subscription but NOT a US-equity one. So SPY is NOT a
     free-real-time "control" here; SPY 'DP' with the indices on 'R'
     is the expected steady state, not a fault (confirmed same-session:
     A/C/D log realtime SPX while E's SPY logs 'DP' all day). Original
     (now-stale) premise: SPY was assumed to have free real-time data
     ("US Real-Time Non Consolidated Streaming Quotes") as a control,
     so a working control with dead indices meant an index-entitlement
     gap. Interpret today's output against the inverted reality above;
  2. dumping the RAW IBKR snapshot rows (all field codes) so nothing
     is hidden — metadata-only vs an error vs a delayed marker;
  3. polling each conid for up to ~30s (far longer than IBClient's
     2s warmup) so "the warmup is just too short" can be ruled in
     or out.

Field 6509 is IBKR's market-data-availability flag (first char:
R=real-time, D=delayed, Z=frozen).

SAFE: read-only. No orders, no writes. Asserts paper environment.

Usage:
    cd "/Users/ddias/Desktop/CALYPSO/Git Repo"
    source .venv/bin/activate
    # the 3 IBIND_OAUTH1A_* env vars must be exported in this shell
    python scripts/probe_ibkr_market_data.py 2>&1 | tee scripts/probe_mktdata_$(date +%H%M%S).log
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Stream output line-by-line even when piped through `tee` (otherwise
# Python block-buffers stdout on a pipe and nothing shows until exit).
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from shared.ib_client import IBClient, IBConfig
from shared.ib_oauth import load_credentials
from shared.market_hours import is_market_open, get_us_market_time

# last, bid, ask, ask-size, bid-size, volume, open, high, low, change,
# change%, market-data-availability, last-size
_FIELDS = ["31", "84", "86", "85", "88", "87", "7295", "70", "71",
           "82", "83", "6509", "7059"]
_METADATA = {"conid", "conidEx", "_updated", "6119", "server_id", "6509"}
_MAX_POLLS = 30
_POLL_INTERVAL_S = 1.0


def _has_price(row: dict) -> bool:
    """True if the row carries any price field beyond metadata."""
    return isinstance(row, dict) and any(k not in _METADATA for k in row)


def _probe(client: IBClient, label: str, conid: int) -> str:
    """Preflight + long warmup poll on one conid. Returns a verdict."""
    print(f"\n{'━' * 58}\n{label}  (conid {conid})\n{'━' * 58}")
    raw = client._client  # underlying ibind IbkrClient
    cs = str(conid)

    # Preflight — ibind documents the first call as priming-only.
    try:
        raw.live_marketdata_snapshot(conids=cs, fields=_FIELDS)
    except Exception as exc:
        print(f"  preflight raised: {type(exc).__name__}: {exc}")

    first_row = None
    for attempt in range(1, _MAX_POLLS + 1):
        try:
            res = raw.live_marketdata_snapshot(conids=cs, fields=_FIELDS)
            data = getattr(res, "data", res)
        except Exception as exc:
            print(f"  poll {attempt}: raised {type(exc).__name__}: {exc}")
            time.sleep(_POLL_INTERVAL_S)
            continue
        row = data[0] if isinstance(data, list) and data else (
            data if isinstance(data, dict) else {})
        if first_row is None:
            first_row = row
            print(f"  poll 1 raw row: {json.dumps(row, default=str)[:400]}")
        if _has_price(row):
            print(f"  ✅ price data appeared on poll {attempt} "
                  f"(~{attempt * _POLL_INTERVAL_S:.0f}s):")
            print(f"     {json.dumps(row, default=str)[:500]}")
            avail = str(row.get("6509", "") or "")
            print(f"     field 6509 (availability): {avail!r}")
            return f"DATA OK (poll {attempt}, 6509={avail!r})"
        time.sleep(_POLL_INTERVAL_S)

    print(f"  ❌ no price fields after {_MAX_POLLS} polls "
          f"(~{_MAX_POLLS * _POLL_INTERVAL_S:.0f}s)")
    print(f"  final raw row: {json.dumps(first_row, default=str)[:400]}")
    return "NO DATA (metadata-only after 30s)"


def main() -> int:
    creds = load_credentials("paper")
    assert creds.environment == "paper", (
        f"SAFETY: probe must run on paper, got {creds.environment!r}"
    )

    now_et = get_us_market_time()
    print(f"Current time: {now_et:%Y-%m-%d %H:%M:%S %Z}")
    print(f"Regular market hours right now: {'YES' if is_market_open() else 'NO'}")

    client = IBClient(IBConfig(credentials=creds))
    results: dict[str, str] = {}
    try:
        client.connect()
        print("Connected to IBKR paper account.")

        # HYPOTHESIS TEST: the marketdata/snapshot endpoint requires an
        # /iserver/accounts preflight. IBClient.connect() calls
        # portfolio_accounts() (/portfolio/accounts — a DIFFERENT
        # endpoint). receive_brokerage_accounts() is the /iserver/accounts
        # call. If snapshots populate AFTER this, _snapshot_with_preflight
        # is missing it.
        print("\n--- /iserver/accounts preflight (receive_brokerage_accounts) ---")
        try:
            acct = client._client.receive_brokerage_accounts()
            print(f"  → {json.dumps(getattr(acct, 'data', acct), default=str)[:300]}")
            err = getattr(acct, "error", None)
            if err:
                print(f"  error: {err}")
        except Exception as exc:
            print(f"  raised: {type(exc).__name__}: {exc}")

        # CONTROL — a plain US ETF; the account has free real-time US
        # equity data. If this fails, the problem is not index-specific.
        try:
            spy = client.qualify_contract("SPY", sec_type="STK")
            results["SPY (control ETF)"] = _probe(client, "CONTROL: SPY ETF", spy)
        except Exception as exc:
            results["SPY (control ETF)"] = f"conid resolve failed: {exc}"
            print(f"\nCONTROL SPY: conid resolution failed — {exc}")

        # SPX index
        try:
            spx = client.qualify_contract("SPX", sec_type="IND")
            results["SPX index"] = _probe(client, "SPX index", spx)
        except Exception as exc:
            results["SPX index"] = f"conid resolve failed: {exc}"

        # VIX index
        try:
            vix = client.qualify_contract("VIX", sec_type="IND")
            results["VIX index"] = _probe(client, "VIX index", vix)
        except Exception as exc:
            results["VIX index"] = f"conid resolve failed: {exc}"

        print("\n" + "=" * 60)
        print("STEP 2 DIAGNOSTIC SUMMARY")
        print("=" * 60)
        for k, v in results.items():
            print(f"  {k:20s}: {v}")
        print("\nInterpretation:")
        print("  - control OK, indices NO DATA  → index entitlement /")
        print("    paper data-sharing not propagated (wait, or subscribe).")
        print("  - everything NO DATA           → data-sharing toggle not")
        print("    propagated yet, or a broader market-data issue.")
        print("  - all DATA OK                  → Step 2 passes; check 6509.")
    finally:
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
