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
from concurrent.futures import ThreadPoolExecutor
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

        # Determine representative strikes near SPX spot for probes 3-7
        probe_strike = None
        call_strikes: list[float] = []
        try:
            strikes_data = getattr(r, "data", {}) or {}
            call_strikes = strikes_data.get("call") or strikes_data.get("calls") or []
            if call_strikes:
                probe_strike = call_strikes[len(call_strikes) // 2]  # middle of chain
        except Exception:
            pass
        print(f"\nRepresentative strike for probes 3-7: {probe_strike}  "
              f"(chain has {len(call_strikes)} call strikes)")

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

        # ── Pick a window of adjacent real strikes for probes 6-7 ──
        mid = len(call_strikes) // 2
        window = call_strikes[max(0, mid - 3):mid + 3]  # up to 6 strikes
        print(f"\nStrike window for probes 6-7: {window}")

        # 6. Multi-strike CSV — does secdef_info accept comma-separated
        #    strikes? If yes, one call resolves many strikes (no thread
        #    pool needed at all — the cleanest F3 design).
        if len(window) >= 3:
            csv_strikes = ",".join(str(s) for s in window[:3])
            t0 = time.monotonic()
            try:
                r = raw.search_secdef_info_by_conid(
                    conid=str(spx_conid), sec_type="OPT", month=month,
                    exchange="CBOE", strike=csv_strikes, right="C",
                )
                _summarize(
                    f"PROBE 6: secdef_info CSV strikes ('{csv_strikes}') "
                    f"— multi-strike in 1 call?",
                    r, time.monotonic() - t0,
                )
                data = getattr(r, "data", None) or []
                if isinstance(data, list):
                    got = sorted({d.get("strike") for d in data
                                  if isinstance(d, dict)})
                    print(f"  → distinct strikes returned: {got}")
                    print(f"  → VERDICT: {'CSV WORKS — multi-strike supported' if len(got) >= 2 else 'CSV did not expand — 1 strike only'}")
            except Exception as exc:
                print(f"\nPROBE 6 raised: {exc}")
                print("  → VERDICT: CSV strikes NOT supported")

        # 7. Concurrent raw secdef calls — is ibind's REST path actually
        #    thread-safe? Fires N calls from N threads, bypassing
        #    IBClient._call_lock entirely (we call the raw ibind method).
        #    Signal: if wall-clock << sum of individual times, calls ran
        #    concurrently. If any strike's response contains a DIFFERENT
        #    strike's data, that's thread-unsafe corruption.
        if len(window) >= 4:
            test_strikes = window[:6]
            print(f"\n━━━ PROBE 7: {len(test_strikes)} concurrent raw secdef "
                  f"calls (ibind REST thread-safety) ━━━")

            def _one_call(strike):
                t = time.monotonic()
                try:
                    res = raw.search_secdef_info_by_conid(
                        conid=str(spx_conid), sec_type="OPT", month=month,
                        exchange="CBOE", strike=str(strike), right="C",
                    )
                    data = getattr(res, "data", res) or []
                    if not isinstance(data, list):
                        data = [data]
                    returned = {d.get("strike") for d in data
                                if isinstance(d, dict)}
                    # Correct = response contains only this strike (or empty)
                    ok = (not returned) or (returned == {float(strike)})
                    return (strike, ok, len(data), returned,
                            time.monotonic() - t, None)
                except Exception as exc:
                    return (strike, False, 0, set(),
                            time.monotonic() - t, f"{type(exc).__name__}: {exc}")

            t0 = time.monotonic()
            with ThreadPoolExecutor(max_workers=len(test_strikes)) as pool:
                results = list(pool.map(_one_call, test_strikes))
            wall = time.monotonic() - t0
            serial_sum = sum(rrow[4] for rrow in results)

            print(f"  wall-clock for all {len(test_strikes)} calls: {wall:.2f}s")
            print(f"  sum of individual call times:            {serial_sum:.2f}s")
            concurrency = serial_sum / wall if wall > 0 else 0
            print(f"  concurrency factor (sum/wall): {concurrency:.1f}x  "
                  f"({'CONCURRENT' if concurrency > 1.8 else 'effectively SERIAL'})")
            all_correct = all(rrow[1] for rrow in results)
            print(f"  all responses strike-correct (no corruption): {all_correct}")
            for strike, ok, n, returned, elapsed, err in results:
                status = "OK" if ok else "!! MISMATCH/ERROR !!"
                print(f"    strike {strike}: {status}  entries={n}  "
                      f"returned={returned}  {elapsed * 1000:.0f}ms"
                      + (f"  err={err}" if err else ""))
            print(f"  → VERDICT: ibind REST is "
                  + ("THREAD-SAFE for concurrent secdef "
                     "(concurrency works, no corruption)"
                     if (all_correct and concurrency > 1.8)
                     else "NOT safely concurrent — use serial or CSV"))

        print("\n=== Probe complete ===")
        print("DESIGN DECISIONS:")
        print(" - PROBE 5: full chain in 1 call? (expect NO — strike required)")
        print(" - PROBE 6: CSV multi-strike in 1 call? If YES → simplest F3")
        print(" - PROBE 7: concurrent secdef safe? If YES → 8-worker pool OK")
        print("   If 6 and 7 both fail → serial resolution + reduce strike count")
    finally:
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
