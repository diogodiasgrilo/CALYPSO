"""Assert the REAL broker still returns the shapes our code reads.

WHY THIS EXISTS. Every unit test mocks the broker, so every one of them asserts
against field names *we chose*. If IBKR renames or drops a field, the mocks keep
passing and the live seat silently reads `None`. That is not hypothetical here:
`_read_open_positions` already carries a fix for IBKR returning qty-0 "zombie"
rows, and `get_fx_rate` carries three alternative response shapes because the
real one was not what the docs implied.

This is READ-ONLY and safe to run against the live paper broker at any time. It
places nothing and cancels nothing.

Run:
    sudo -u calypso .venv/bin/python -m scripts.verify_broker_contract
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

CHECKS: list[tuple[str, str, list[str]]] = [
    # (label, rpc method, fields our code reads off each row / the dict)
    ("positions", "get_positions",
     ["conid", "position", "contractDesc", "realizedPnl", "unrealizedPnl",
      "assetClass", "strike", "putOrCall", "expiry", "avgPrice"]),
]


def _rpc(method: str, args=None, kwargs=None):
    import json
    import urllib.request
    body = json.dumps({"method": method, "args": args or [],
                       "kwargs": kwargs or {}}).encode()
    req = urllib.request.Request(
        os.environ.get("CALYPSO_BROKER_URL", "http://127.0.0.1:8788") + "/rpc",
        data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("result")


def main() -> int:
    failures: list[str] = []

    # --- positions -------------------------------------------------------
    rows = _rpc("get_positions") or []
    print(f"positions: {len(rows)} rows")
    if not rows:
        print("  ! no rows — cannot verify the position shape right now "
              "(run while the account holds or has held positions today)")
    else:
        row = rows[0]
        for f in CHECKS[0][2]:
            present = f in row
            print(f"  {'ok ' if present else 'MISSING'} {f}")
            if not present:
                failures.append(f"positions row lacks {f!r}")

    # --- balance / ledger ------------------------------------------------
    bal = _rpc("get_balance") or {}
    led = (bal.get("raw_ledger") or {}).get("USD") or {}
    print("\nledger USD:")
    for f in ("realizedpnl", "unrealizedpnl", "netliquidationvalue", "cashbalance"):
        present = f in led
        print(f"  {'ok ' if present else 'MISSING'} {f}")
        if not present:
            failures.append(f"ledger USD lacks {f!r}")
    # BROKER-RECONCILE's whole premise
    if "realizedpnl" in led:
        print(f"      realizedpnl = {led['realizedpnl']} "
              f"(0.0 pre-market is expected — it does not accumulate)")

    # --- base currency (B1's premise: no FX conversion needed) -----------
    print("\nbase currency:")
    bc = bal.get("base_currency")
    print(f"  base_currency = {bc!r} (USD expected; anything else means the "
          f"retired EUR conversion was not dead code after all)")
    if bc and bc != "USD":
        failures.append(f"base currency is {bc!r}, not USD")

    print()
    if failures:
        print(f"FAILED — {len(failures)} contract mismatch(es):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("PASS — every field our code reads is present on the real broker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
