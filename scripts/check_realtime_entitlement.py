"""Broker-routed real-time market-data entitlement check (SPX index vs SPY ETF).

WHY THIS EXISTS (2026-07-06): strategy E (SPY double calendar) receives DELAYED
SPY quotes (field 6509='DP') all session, while the SPX-index variants (A/B/C/D)
get real-time SPX — despite the account holding the US-equity/ETF real-time
subscriptions. The open question the logs could NOT settle: are E's SPY *option
legs* (which actually drive its P&L/exits) real-time via OPRA, or also delayed?
This checks the 6509 flag on the SPY underlying, E's four live SPY option legs,
and the SPX/VIX controls — so a single RTH run answers it definitively.

SAFE + NON-COMPETING: routes every quote through the ALREADY-RUNNING
``calypso-broker`` session over loopback ``/rpc`` (default http://127.0.0.1:8788).
It opens NO IBKR session of its own, so it will NOT compete with the broker's
single OAuth session (unlike scripts/probe_ibkr_market_data.py, which spins its
own IBClient and must only be run with the bots stopped). Read-only.

Field 6509 first char: R=RealTime, D=Delayed, Z=Frozen, Y=Frozen-Delayed,
N=Not-Subscribed. NOTE: outside RTH (09:30-16:00 ET) the real-time-entitled
instruments FREEZE (Z / empty) because their live feed stops, while delayed-
entitled instruments keep streaming a lagged book — so a 'Z'/empty here after
hours is NORMAL and only an RTH run is a definitive real-time verdict.

Usage (on the VM, as the calypso user, broker running):
    cd /opt/calypso && .venv/bin/python scripts/check_realtime_entitlement.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

BROKER_URL = os.environ.get("CALYPSO_BROKER_URL", "http://127.0.0.1:8788").rstrip("/")
E_SIDECAR = "data/variant_e/dc_open_trades.json"

# Stable underlying conids (paper, verified late-2025/2026 — resolve via
# qualify_contract if IBKR ever rotates them; see CLAUDE.md symbol table).
UNDERLYINGS = [
    ("SPY  underlying (E)", 756733),
    ("SPX  underlying (A/B/C/D control)", 416904),
    ("VIX  underlying (control)", 13455763),
]


def _rpc(method: str, *args, **kwargs) -> dict | None:
    """Call the broker's loopback /rpc. Returns the ``result`` dict or None."""
    body = json.dumps({"method": method, "args": list(args), "kwargs": kwargs}).encode()
    req = urllib.request.Request(
        f"{BROKER_URL}/rpc", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
        return payload.get("result")
    except Exception as e:  # noqa: BLE001 — diagnostic, never raise
        print(f"    RPC {method}{args} failed: {type(e).__name__}: {e}")
        return None


def _verdict(avail) -> str:
    """Human verdict from field 6509 (``availability``)."""
    if not avail:
        return "no-flag (frozen/empty — normal after hours)"
    c = str(avail)[:1].upper()
    return {
        "R": "REAL-TIME ✅",
        "D": "DELAYED ❌",
        "Z": "FROZEN (normal after hours; delayed if during RTH)",
        "Y": "FROZEN-DELAYED ❌",
        "N": "NOT-SUBSCRIBED ❌",
    }.get(c, f"UNKNOWN ({avail})")


def _row(label: str, conid: int) -> str:
    q = _rpc("get_quote", conid) or {}
    avail = q.get("availability")
    bid, ask = q.get("bid"), q.get("ask")
    print(f"  {label:<40} conid={conid:<10} 6509={str(avail or '-'):<5} "
          f"bid/ask={bid}/{ask}  -> {_verdict(avail)}")
    return str(avail or "")[:1].upper()


def _load_e_legs() -> list[tuple[str, int]]:
    try:
        with open(E_SIDECAR) as f:
            trades = json.load(f)
    except Exception:
        return []
    legs: list[tuple[str, int]] = []
    for t in trades:
        for name, leg in (t.get("legs") or {}).items():
            uic = leg.get("uic")
            if uic:
                legs.append((f"SPY option {name} {leg.get('strike')} {leg.get('expiry')}", int(uic)))
    return legs


def main() -> int:
    try:
        with urllib.request.urlopen(f"{BROKER_URL}/health", timeout=10) as r:
            health = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"BROKER UNREACHABLE at {BROKER_URL}: {e}")
        return 0
    print(f"Broker /health: {health}\n")

    print("UNDERLYINGS:")
    spy_u = spx_u = ""
    for label, conid in UNDERLYINGS:
        c = _row(label, conid)
        if conid == 756733:
            spy_u = c
        if conid == 416904:
            spx_u = c

    print("\nE's LIVE SPY OPTION LEGS (from the open calendar sidecar):")
    legs = _load_e_legs()
    if not legs:
        print("  (no open E calendar / sidecar unreadable — nothing to check)")
    leg_flags = [_row(label, conid) for label, conid in legs]

    # Verdict summary
    print("\n" + "=" * 68)
    print("VERDICT (definitive only during RTH 09:30-16:00 ET):")
    print(f"  SPY underlying   : {_verdict(spy_u)}")
    print(f"  SPX control      : {_verdict(spx_u)}")
    if leg_flags:
        rt = sum(1 for f in leg_flags if f == "R")
        dl = sum(1 for f in leg_flags if f == "D")
        print(f"  SPY option legs  : {rt}/{len(leg_flags)} real-time, {dl} delayed "
              f"({'ALL REAL-TIME ✅' if rt == len(leg_flags) else 'SOME/ALL NOT REAL-TIME ❌'})")
    if spx_u == "R" and spy_u == "R" and leg_flags and all(f == "R" for f in leg_flags):
        print("  => PASS: full real-time SPY chain — E's data is clean for go-live.")
    elif spx_u == "R" and (spy_u == "D" or "D" in leg_flags):
        print("  => SPY entitlement NOT reaching this session (SPX real-time, SPY delayed).")
        print("     Fix is account/session config (paper-sharing / competing session),")
        print("     NOT the bot. See CLAUDE.md 'Snapshot returns metadata-only' + E go-live gate.")
    else:
        print("  => Inconclusive (likely after-hours — re-run during RTH).")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
