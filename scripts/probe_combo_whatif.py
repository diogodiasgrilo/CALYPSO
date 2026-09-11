#!/usr/bin/env python3
"""
Non-committal COMBO preview: does IBKR accept a 4-leg SPX iron-condor BAG, what
margin does it assign, and does the routing prefix change the answer?

READ-ONLY BY CONSTRUCTION. Everything goes through `what_if_order`, which posts
to `/iserver/account/{id}/orders/whatif` — a DISTINCT endpoint from `/orders`,
with no `answers` parameter and no reply loop. It cannot place an order. This
script never imports, references or reaches any placement method, and it runs
THROUGH calypso-broker rather than building its own IBClient: a second IBClient
would open a second IBKR session and evict the broker's, which is precisely the
failure calypso-broker exists to prevent (one brokerage session per username).

WHAT THIS ANSWERS (combo-track step 1, and part of step 2)
---------------------------------------------------------
A 2026-06-10 probe already established, by actually PLACING a 2-leg vertical,
that the paper account "ACCEPTS a BAG combo ticket + margins it defined-risk" —
but its combo order LIFECYCLE is broken (phantom PendingSubmit, "OrderID doesn't
exist" on cancel), which is why that approach is not repeated here. Three things
it never covered:

  1. **Is a FOUR-leg combo accepted at all?** IBKR: "the number of legs
     permissible varies by exchange." The plan side-steps this with two 2-leg
     verticals; nobody has checked whether the 4-leg form is accepted.
  2. **Does the conidex need `@CBOE`?** `ib_constants.SPREAD_TEMPLATE_CONID` is
     the bare universal-USD template and the tests only assert self-consistency
     with it. Routing decides guaranteed vs non-guaranteed execution — see
     COMBO_ENTRY_LIVE_CUTOVER_PLAN.md §0-bis.
  3. **Is the side/price convention right?** A SHORT iron condor should preview
     as a CREDIT with initial margin ≈ width x 100 x qty. If the sign convention
     is inverted, the preview shows a debit and/or a margin that is not the
     defined-risk figure — the trade would be backwards.

HOW TO READ THE RESULT
----------------------
  initial.change ≈ width x 100 x qty  -> defined-risk. Combo margin works.
  initial.change ≈ naked-short margin -> IBKR is NOT treating it as a spread.
  amount is a CREDIT                  -> side/price convention is right.

AN EMPTY OR MISSING BLOCK IS **INCONCLUSIVE, NEVER A PASS.** IBKR returns an
empty whatif when the preflight has not run or the instrument was not snapshot
first, and reading that as "fine" is how a bad assumption gets promoted to a
fact. This script says INCONCLUSIVE and exits non-zero.

RUN IT DURING MARKET HOURS. Margin/price previews off stale overnight quotes can
mislead. Contracts default to the front 0DTE expiry.

USAGE (on the VM, as calypso — the broker must be up)
  .venv/bin/python -m scripts.probe_combo_whatif
  .venv/bin/python -m scripts.probe_combo_whatif --width 5 --quantity 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.ib_constants import SPREAD_TEMPLATE_CONID  # noqa: E402

BROKER = os.environ.get("CALYPSO_BROKER_URL", "http://127.0.0.1:8788")

#: Methods this script is permitted to call. A hard allowlist rather than a
#: convention: it makes "this script cannot place an order" checkable by reading
#: eight lines instead of the whole file.
_PERMITTED = frozenset({
    "qualify_contract", "qualify_option_strikes", "get_option_chain",
    "get_quote", "get_quotes_batch", "what_if_order",
})


def rpc(method: str, *args, **kwargs):
    if method not in _PERMITTED:
        raise RuntimeError(
            f"probe_combo_whatif refuses to call {method!r} — this script is "
            f"read-only and may only use {sorted(_PERMITTED)}"
        )
    body = json.dumps({"method": method, "args": list(args), "kwargs": kwargs})
    req = urllib.request.Request(
        f"{BROKER}/rpc", data=body.encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if "error" in payload:
        raise RuntimeError(f"{method} failed: {payload['error']}")
    return payload.get("result")


def _money(v):
    """IBKR returns margin strings like '+4,500.00' or '500 USD'."""
    if v in (None, ""):
        return None
    s = str(v).replace(",", "").replace("USD", "").replace("+", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def preview(conidex: str, side: str, price: float, qty: int, label: str) -> dict:
    print(f"\n--- {label} ---")
    print(f"  conidex: {conidex}")
    print(f"  side={side}  price={price}  qty={qty}")
    order = {
        "conidex": conidex, "side": side, "quantity": qty,
        "order_type": "LMT", "price": price, "tif": "DAY",
    }
    try:
        blocks = rpc("what_if_order", order) or {}
    except Exception as e:
        print(f"  REJECTED: {e}")
        return {"label": label, "ok": False, "reason": str(e)}

    if not blocks:
        print("  INCONCLUSIVE — empty block. NOT a pass: IBKR returns empty when "
              "the preflight has not run or the legs were not snapshot first.")
        return {"label": label, "ok": False, "reason": "empty"}

    init = (blocks.get("initial") or {})
    amt = (blocks.get("amount") or {})
    change = _money(init.get("change"))
    print(f"  initial.change : {init.get('change')!r}  -> {change}")
    print(f"  amount         : {amt.get('amount')!r}")
    print(f"  maintenance    : {(blocks.get('maintenance') or {}).get('change')!r}")
    return {"label": label, "ok": True, "initial_change": change, "blocks": blocks}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Read-only 4-leg combo margin preview.")
    p.add_argument("--symbol", default="SPX")
    p.add_argument("--width", type=float, default=5.0, help="wing width in points")
    p.add_argument("--quantity", type=int, default=1)
    p.add_argument("--otm", type=float, default=60.0, help="points OTM for the shorts")
    a = p.parse_args(argv)

    print("=" * 72)
    print("COMBO WHAT-IF PROBE — READ-ONLY (whatif endpoint; cannot place)")
    print("=" * 72)

    # get_quote takes a CONID, not a symbol — qualify the index first.
    # (The first draft passed sec_type= to get_quote, which does not accept it;
    # caught on the first live run rather than by reading the signature. The
    # signatures are now mirrored in the tests so this cannot recur silently.)
    try:
        spx_conid = rpc("qualify_contract", a.symbol, sec_type="IND", exchange="CBOE")
    except Exception as e:
        print(f"\nABORT: could not qualify {a.symbol}: {e}")
        return 2
    q = rpc("get_quote", spx_conid) or {}
    spot = _money(q.get("last") or q.get("mid") or q.get("close"))
    if not spot:
        print("\nABORT: no live SPX quote. Run this during market hours — a margin "
              "preview off a stale quote is not evidence.")
        return 2
    print(f"\nSPX spot: {spot:,.2f}")

    inc = 5
    sc = round((spot + a.otm) / inc) * inc
    sp = round((spot - a.otm) / inc) * inc
    lc, lp = sc + a.width, sp - a.width
    print(f"legs: SC {sc} / LC {lc}  |  SP {sp} / LP {lp}   (width {a.width:g}pt)")

    conids = {}
    for strike, right, name in ((sc, "C", "sc"), (lc, "C", "lc"),
                                (sp, "P", "sp"), (lp, "P", "lp")):
        try:
            conids[name] = rpc("qualify_contract", a.symbol, sec_type="OPT",
                               strike=float(strike), right=right,
                               trading_class="SPXW")
        except Exception as e:
            print(f"\nABORT: could not qualify {right}{strike}: {e}")
            return 2
    if not all(conids.values()):
        print(f"\nABORT: unresolved legs: {conids}")
        return 2
    print(f"conids: {conids}")

    legs = (f"{conids['sc']}/-1,{conids['lc']}/1,"
            f"{conids['sp']}/-1,{conids['lp']}/1")
    est_credit = round(a.width * 0.25, 2)   # a plausible SELL limit; preview only

    results = [
        preview(f"{SPREAD_TEMPLATE_CONID};;;{legs}", "SELL", est_credit,
                a.quantity, "BARE template (what the code builds today)"),
        preview(f"{SPREAD_TEMPLATE_CONID}@CBOE;;;{legs}", "SELL", est_credit,
                a.quantity, "@CBOE-routed template (guaranteed-combo candidate)"),
    ]

    defined_risk = a.width * 100 * a.quantity
    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    print(f"  defined-risk margin would be: ${defined_risk:,.2f} "
          f"({a.width:g}pt x 100 x {a.quantity})")
    for r in results:
        if not r["ok"]:
            print(f"  {r['label']}: INCONCLUSIVE ({r['reason']})")
            continue
        ch = r["initial_change"]
        if ch is None:
            print(f"  {r['label']}: INCONCLUSIVE (no initial.change)")
        elif ch <= defined_risk * 1.25:
            print(f"  {r['label']}: DEFINED-RISK ✓  (${ch:,.2f})")
        else:
            print(f"  {r['label']}: NOT defined-risk ✗ (${ch:,.2f} — IBKR is not "
                  f"treating this as a spread)")
    print("\n  Reminder: an accepted PREVIEW is not an atomic FILL. Atomicity is")
    print("  venue-level and only observable on a live account — see")
    print("  COMBO_ENTRY_LIVE_CUTOVER_PLAN.md §0-bis.")
    return 0 if any(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
