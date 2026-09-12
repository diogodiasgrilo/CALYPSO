#!/usr/bin/env python3
"""Flatten ALL open option positions on the shared IBKR paper account.

WHY account-level (not per-variant): A/B/C trade the ONE shared paper account,
and IBKR merges positions at the same (conid, side) — there is no per-variant
position id to flatten selectively. When only one variant is live (the swap
invariant), the open book IS that variant's, so an account flatten == flattening
the live variant. Run it when exactly one seat is live.

USE: the B<->C rollback needs the LIVE variant flat BEFORE flipping it to
dry-run — flipping to dry only stops NEW orders; it does NOT close, manage, or
even monitor an already-open position, which would then ride to expiry unmanaged
(no stop-loss). Flatten first, confirm flat, then flip. (RB-8 / BC_SWAP_PLAN.)

Runs THROUGH the shared calypso-broker (loopback), so it needs no OAuth of its
own and cannot evict the bot's session.

Default is CHECK-ONLY: it prints every open option position and the exact
opposite MARKET order it WOULD place. Pass --execute to actually place the
closes (a REAL paper-order action).

    # dry preview
    sudo -u calypso CALYPSO_BROKER_URL=http://127.0.0.1:8788 \
        /opt/calypso/.venv/bin/python scripts/flatten_paper_account.py
    # actually flatten
    sudo -u calypso CALYPSO_BROKER_URL=http://127.0.0.1:8788 \
        /opt/calypso/.venv/bin/python scripts/flatten_paper_account.py --execute
"""
import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _pos_qty(p: dict) -> float:
    for k in ("position", "quantity", "size"):
        v = p.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _is_option(p: dict) -> bool:
    ac = str(p.get("assetClass") or p.get("asset_class") or p.get("secType") or "").upper()
    return ac in ("OPT", "FOP")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="actually place the closing MARKET orders (REAL paper orders)")
    ap.add_argument("--broker-url",
                    default=os.environ.get("CALYPSO_BROKER_URL", "http://127.0.0.1:8788"))
    args = ap.parse_args()

    from shared.broker_client import BrokerClient
    bc = BrokerClient(args.broker_url)
    if not bc.ensure_connected():
        print(f"flatten: broker at {args.broker_url} not connected — aborting", file=sys.stderr)
        return 2

    positions = bc.get_positions() or []
    open_opts = [p for p in positions if _is_option(p) and abs(_pos_qty(p)) > 0]

    if not open_opts:
        print("flatten: account is FLAT — no open option positions. Nothing to do.")
        return 0

    print(f"flatten: {len(open_opts)} open option position(s) "
          f"{'(EXECUTE)' if args.execute else '(CHECK-ONLY — pass --execute to place)'}:")
    failures = 0
    for p in open_opts:
        conid = p.get("conid") or p.get("conidEx")
        qty = _pos_qty(p)
        desc = p.get("contractDesc") or p.get("ticker") or conid
        side = "SELL" if qty > 0 else "BUY"        # opposite of the held side
        close_qty = int(abs(qty))
        print(f"  {desc} (conid={conid}) held={qty:+g} -> {side} {close_qty} @ MARKET")
        if not args.execute:
            continue
        if not conid or close_qty <= 0:
            print(f"    SKIP: bad conid/qty")
            failures += 1
            continue
        try:
            res = bc.place_and_wait_for_fill(
                conid=int(conid), side=side, quantity=close_qty,
                order_type="MKT", tif="DAY",
                coid=f"FLATTEN_{conid}_{uuid.uuid4().hex[:6]}",
            )
            status = (res or {}).get("status") or (res or {}).get("order_status")
            filled = (res or {}).get("filled_quantity")
            print(f"    -> status={status} filled={filled}")
            if status not in ("filled", "Filled"):
                failures += 1
        except Exception as e:
            print(f"    -> ERROR: {e}")
            failures += 1

    if args.execute:
        if failures:
            print(f"flatten: {failures} close(s) did NOT confirm filled — "
                  f"re-run to verify FLAT, or intervene manually.", file=sys.stderr)
            return 1
        print("flatten: all closes confirmed filled. Re-run (check-only) to verify FLAT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
