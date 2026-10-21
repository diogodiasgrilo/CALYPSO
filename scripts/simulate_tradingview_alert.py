#!/usr/bin/env python3
"""Post a realistic TradingView alert at the signal-ingest service.

Exists so the whole path can be exercised with NO TradingView account, NO
public endpoint and NO money — which is how the service gets tested long before
a real signal source is settled.

The body mirrors what TradingView actually sends when the alert message is JSON
with placeholders:

    {"secret":"...","ticker":"{{ticker}}","action":"{{strategy.order.action}}",
     "price":{{close}},"time":"{{timenow}}","source":"IADSS"}

Usage:
  python -m scripts.simulate_tradingview_alert --ticker TSLA --action buy
  python -m scripts.simulate_tradingview_alert --ticker BTC --action sell --quantity 0.02
  python -m scripts.simulate_tradingview_alert --replay        # same alert twice
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone


def post(url: str, payload: dict) -> tuple[int, str]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8002/webhook")
    ap.add_argument("--ticker", default="TSLA")
    ap.add_argument("--action", default="buy", choices=["buy", "sell"])
    ap.add_argument("--quantity", type=float, default=None)
    ap.add_argument("--price", type=float, default=123.45)
    ap.add_argument("--source", default="SIMULATED")
    ap.add_argument("--secret", default=os.environ.get("SIGNAL_INGEST_SECRET", ""))
    ap.add_argument("--replay", action="store_true",
                    help="send the identical alert twice (TradingView resend)")
    ap.add_argument("--bad-secret", action="store_true")
    a = ap.parse_args()

    payload = {
        "secret": "wrong-on-purpose" if a.bad_secret else a.secret,
        "ticker": a.ticker,
        "action": a.action,
        "price": a.price,
        "time": datetime.now(timezone.utc).isoformat(),
        "source": a.source,
    }
    if a.quantity is not None:
        payload["quantity"] = a.quantity

    n = 2 if a.replay else 1
    for i in range(n):
        code, body = post(a.url, payload)
        label = f"attempt {i + 1}/{n}"
        print(f"  {label}: HTTP {code} {body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
