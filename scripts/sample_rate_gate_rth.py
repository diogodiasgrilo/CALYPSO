#!/usr/bin/env python3
"""Sample the broker's IBKR rate gate over a window, DURING market hours.

Why this exists. `/health`'s `rate_gate` block is CUMULATIVE since the broker
process started, so it mixes RTH with nights and weekends and cannot be
compared with the 180-second RTH samples taken on 2026-10-07/08 (857–1,023 ms
mean added delay, 93–98% of the 5/s cap). After the quote cache shipped
(88% hit rate) nobody has re-measured under load, so "the saturation is fixed"
is an inference, not a measurement — `docs/NEXT_STEPS.md` §A-oct says so.

What it does. Reads `/health` twice, `window` seconds apart, and DIFFERENCES the
counters. The difference over a known interval is the only way to get a
rate for that interval out of monotonically-accumulating totals.

Read-only and inert: one HTTP GET to the broker's own health endpoint, no
IBClient, no BrokerClient, no order path, nothing written but stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8788/health"


def _get(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as fh:  # noqa: S310
        return json.loads(fh.read().decode("utf-8"))


def _gate(d: dict) -> dict:
    return d.get("rate_gate") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--window", type=float, default=180.0,
                    help="seconds between the two reads (default 180, matching "
                         "the 2026-10-07/08 samples this is compared against)")
    a = ap.parse_args()

    try:
        first = _get(a.url)
    except Exception as e:  # noqa: BLE001
        print(f"RATE-GATE SAMPLE: FAILED to read {a.url}: {e}")
        return 1
    t0 = time.time()
    g0 = _gate(first)
    if not g0:
        print("RATE-GATE SAMPLE: /health carries no rate_gate block — nothing to sample")
        return 1

    time.sleep(a.window)

    try:
        second = _get(a.url)
    except Exception as e:  # noqa: BLE001
        print(f"RATE-GATE SAMPLE: FAILED on the second read: {e}")
        return 1
    elapsed = time.time() - t0
    g1 = _gate(second)

    acq = (g1.get("acquisitions", 0) or 0) - (g0.get("acquisitions", 0) or 0)
    waited = (g1.get("waited", 0) or 0) - (g0.get("waited", 0) or 0)
    wait_s = (g1.get("total_wait_s", 0.0) or 0.0) - (g0.get("total_wait_s", 0.0) or 0.0)
    cap = g1.get("max_rps") or 0

    rps = acq / elapsed if elapsed else 0.0
    pct_cap = (rps / cap * 100.0) if cap else 0.0
    pct_waited = (waited / acq * 100.0) if acq else 0.0
    mean_wait_ms = (wait_s / acq * 1000.0) if acq else 0.0

    print("=" * 74)
    print("  RATE-GATE RTH SAMPLE — windowed, NOT cumulative")
    print("=" * 74)
    print(f"  window            : {elapsed:.1f}s")
    print(f"  acquisitions      : {acq}")
    print(f"  requests/sec      : {rps:.2f}   (cap {cap} = {pct_cap:.0f}% of capacity)")
    print(f"  queued            : {waited} = {pct_waited:.1f}% of calls")
    print(f"  mean added delay  : {mean_wait_ms:.0f} ms/request")
    print()
    print("  BENCHMARK (180s RTH samples, 2026-10-07/08, pre- and post-cache):")
    print("    2026-10-07  1,023 ms  ~98% of cap   99.2% queued")
    print("    2026-10-08    857 ms  ~93% of cap")
    print("  A mean added delay well under ~300 ms means the quote cache closed it.")
    print("  Anything near 800+ ms means it did NOT and the snapshot priming call")
    print("  (P7-audit H10 — 'less is brittle') is still the open lever.")
    if acq == 0:
        print()
        print("  ⚠️  ZERO acquisitions in the window — the market is almost certainly")
        print("     CLOSED, or the strategies are not running. This sample says nothing")
        print("     about saturation; re-run between 09:30 and 16:00 ET.")
        return 2
    for lab in (g1.get("by_label") or [])[:6]:
        print(f"    cumulative top caller: {lab.get('label')} {lab.get('pct')}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
