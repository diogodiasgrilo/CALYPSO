#!/usr/bin/env python3
"""Strategy H (0DTE long strangle) — the Step 3 data probe. READ-ONLY.

The playbook requires this before Steps 4–5 are built on top of
``bots/hydra/long_strangle_chain.py``, and says why in one sentence: *D's offline
tests were green but the live probe caught SPXW expiry gaps and a missing IV
field.* The chain module currently carries **three flagged assumptions**, and
this script exists to settle all three against a real chain in one pass.

READ-ONLY BY CONSTRUCTION. The method allowlist below is a hard gate, not a
convention — it makes "this cannot place an order" checkable by reading eight
lines. Nothing here touches `what_if_order` either: that is on the ``orders``
family and would share a retry/breaker budget with real placement, which is how
the 2026-09-19 combo probe took the breaker down for ~34s.

WHAT IT ANSWERS
---------------
**Q1 — which expected move is real?** ``long_strangle_chain`` implements two and
they disagree badly: the ATM straddle (source-faithful) and VIX-implied (variant
F's formula). On 2026-09-22's numbers those were roughly 22pt and 71pt. That
factor of three IS the strike choice, so the strategy is undefined until this is
measured. Prints both, their ratio, and the strikes each would actually pick.

**Q2 — can IV percentile be computed honestly, or at all?** The filter is "IV
percentile below ~35%". Nothing in the repo stores option-IV history;
``market_ticks`` keeps VIX, which is a 30-day INDEX vol, not the IV of the 0DTE
options being bought. This checks whether the broker even returns a per-option IV
for an SPXW 0DTE leg — if it does not, the filter is unbuildable as specified and
that must be recorded rather than silently approximated with VIX.

**Q3 — how lopsided is the skew really?** The chain module vetoes an entry when
the two premiums differ by more than 35%, a number invented against a source that
says only "reasonably similar". This measures the actual call/put premium gap at
equidistant strikes so the tolerance can be set from data instead of a guess.

Plus the two failures the playbook warns about directly: **SPXW expiry gaps**
(is today actually listed?) and **chain completeness** (is the strike grid dense
enough that ``snap_to_chain``'s 25pt cap is safe, or will it refuse constantly?).

USAGE (on the VM, as calypso — the broker must be up, market should be OPEN)
    .venv/bin/python -m scripts.probe_long_strangle_data
    .venv/bin/python -m scripts.probe_long_strangle_data --vix-days 60
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.long_strangle_chain import (  # noqa: E402
    expected_move_from_straddle,
    expected_move_from_vix,
    iv_percentile,
    premiums_are_balanced,
    select_strangle_strikes,
)

BROKER = os.environ.get("CALYPSO_BROKER_URL", "http://127.0.0.1:8788")

#: Hard allowlist. Every entry is a READ. No order, no whatif, no write.
_PERMITTED = frozenset({
    "qualify_contract", "qualify_option_strikes", "get_option_chain",
    "get_quote", "get_quotes_batch",
})


def rpc(method: str, *args, **kwargs):
    if method not in _PERMITTED:
        raise RuntimeError(
            f"probe_long_strangle_data refuses to call {method!r} — this script "
            f"is read-only and may only use {sorted(_PERMITTED)}"
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


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _quote(conid, tries=4):
    """Re-poll a fresh conid before believing an empty quote.

    Same lesson as the 2026-09-22 paper-smoke failure: IBKR serves a
    metadata-only row for the first poll(s) on a conid it has not served before,
    and a single read made the smoke abort on a quote that was actually there.
    """
    import time
    q = rpc("get_quote", conid) or {}
    for _ in range(tries - 1):
        if _num(q.get("bid")) and _num(q.get("ask")):
            break
        time.sleep(1.0)
        q = rpc("get_quote", conid) or {}
    return q


def _mid(q):
    bid, ask = _num(q.get("bid")), _num(q.get("ask"))
    if bid is None or ask is None or ask < bid:   # crossed book -> no mid
        return None
    return (bid + ask) / 2.0


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="SPX")
    p.add_argument("--expiry", default=None, help="ISO; default today (0DTE)")
    p.add_argument("--vix-days", type=int, default=90,
                   help="lookback for the VIX-percentile fallback in Q2")
    p.add_argument("--db", default="/opt/calypso/data/variant_b/backtesting.db")
    a = p.parse_args(argv)

    print("=" * 74)
    print("STRATEGY H — Step 3 data probe (READ-ONLY)".center(74))
    print("=" * 74)

    expiry = a.expiry or _dt.date.today().isoformat()
    print(f"\nexpiry: {expiry}   broker: {BROKER}")

    # ---- spot + VIX ------------------------------------------------------
    try:
        spx_conid = rpc("qualify_contract", a.symbol, sec_type="IND", exchange="CBOE")
        spx = _quote(spx_conid)
        spot = _num(spx.get("last")) or _num(spx.get("mid")) or _num(spx.get("close"))
        vix_conid = rpc("qualify_contract", "VIX", sec_type="IND")
        vix_q = _quote(vix_conid)
        vix = _num(vix_q.get("last")) or _num(vix_q.get("close"))
    except Exception as e:
        print(f"\nABORT: could not read SPX/VIX: {type(e).__name__}: {e}")
        return 2
    if not spot or not vix:
        print(f"\nABORT: spot={spot} vix={vix} — market may be closed. "
              f"This probe needs LIVE quotes; re-run during RTH.")
        return 2
    print(f"SPX {spot:,.2f}   VIX {vix:.2f}   "
          f"(SPX avail={spx.get('availability')!r}, VIX avail={vix_q.get('availability')!r})")

    # ---- chain completeness (playbook's named failure #2) ----------------
    print("\n" + "-" * 74)
    print("CHAIN COMPLETENESS — is snap_to_chain's 25pt cap safe?")
    print("-" * 74)
    try:
        strikes = sorted(float(s) for s in (rpc("get_option_chain", a.symbol, expiry) or []))
    except Exception as e:
        print(f"  ABORT: option chain failed: {type(e).__name__}: {e}")
        print("  (An SPXW expiry gap on this date is exactly what the playbook warns about.)")
        return 3
    if not strikes:
        print("  ABORT: chain EMPTY for this expiry — SPXW may not list it (expiry gap).")
        return 3
    near = [s for s in strikes if abs(s - spot) <= 200]
    gaps = sorted({round(b - a_, 2) for a_, b in zip(near, near[1:])}) if len(near) > 1 else []
    print(f"  strikes: {len(strikes)} total, {len(near)} within ±200pt of spot")
    print(f"  range  : {strikes[0]:,.0f} … {strikes[-1]:,.0f}")
    print(f"  spacing near the money: {gaps if gaps else '(n/a)'}")
    widest = max(gaps) if gaps else 0
    print(f"  → 25pt snap cap is {'SAFE' if widest <= 25 else 'TOO TIGHT — will refuse entries'}"
          f" (widest near-money gap {widest:g}pt)")

    # ---- Q1: which expected move is real? --------------------------------
    print("\n" + "-" * 74)
    print("Q1 — WHICH EXPECTED MOVE? (the strike choice IS this number)")
    print("-" * 74)
    atm = min(strikes, key=lambda s: abs(s - spot))
    try:
        c_atm = rpc("qualify_contract", a.symbol, expiry=expiry, strike=float(atm),
                    right="C", trading_class="SPXW")
        p_atm = rpc("qualify_contract", a.symbol, expiry=expiry, strike=float(atm),
                    right="P", trading_class="SPXW")
        cq, pq = _quote(c_atm), _quote(p_atm)
    except Exception as e:
        print(f"  ABORT: ATM legs failed: {type(e).__name__}: {e}")
        return 4
    c_mid, p_mid = _mid(cq), _mid(pq)
    print(f"  ATM strike {atm:,.0f}: call mid={c_mid} put mid={p_mid}")
    if c_mid is None or p_mid is None:
        print("  ⚠ no usable ATM mid — straddle EM cannot be measured this tick.")
        em_straddle = 0.0
    else:
        em_straddle = expected_move_from_straddle(c_mid, p_mid)
    em_vix = expected_move_from_vix(spot, vix)
    print(f"  straddle EM : ±{em_straddle:,.2f}pt   (source-faithful)")
    print(f"  VIX EM      : ±{em_vix:,.2f}pt   (variant F's formula)")
    if em_straddle and em_vix:
        print(f"  ratio       : VIX/straddle = {em_vix / em_straddle:.2f}×")
    for label, em in (("straddle", em_straddle), ("vix", em_vix)):
        call_k, put_k = select_strangle_strikes(spot, em, strikes)
        print(f"  → {label:<9} would buy  C {call_k}  /  P {put_k}")

    # ---- Q2: is per-option IV available at all? --------------------------
    print("\n" + "-" * 74)
    print("Q2 — IV PERCENTILE: is there an honest input, or any input?")
    print("-" * 74)
    iv_fields = {k: v for k, v in (cq or {}).items()
                 if "iv" in k.lower() or "vol" in k.lower() or k == "7633"}
    print(f"  per-option IV fields on the ATM call quote: {iv_fields or 'NONE'}")
    if not iv_fields:
        print("  → the <35% IV-percentile filter is NOT buildable as specified from")
        print("    this quote shape. Either drop it and record that, or find another")
        print("    series. Do NOT silently substitute VIX and call it IV.")
    hist = []
    try:
        con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
        since = (_dt.date.today() - _dt.timedelta(days=a.vix_days)).isoformat()
        hist = [r[0] for r in con.execute(
            "SELECT DISTINCT vix_level FROM market_ticks WHERE timestamp >= ? "
            "AND vix_level > 0", (since,)) if r[0]]
    except Exception as e:
        print(f"  (VIX history unavailable: {type(e).__name__}: {e})")
    if hist:
        pct = iv_percentile(vix, hist)
        print(f"  VIX percentile over {a.vix_days}d (n={len(hist)}): {pct:.1f}%")
        print(f"  → this is a VIX percentile, NOT an option-IV percentile. If it is")
        print(f"    used, the config key and the logs must say 'vix_percentile'.")

    # ---- Q3: how lopsided is the skew really? ----------------------------
    print("\n" + "-" * 74)
    print("Q3 — SKEW: calibrate the 35% balance tolerance against reality")
    print("-" * 74)
    em = em_straddle or em_vix
    call_k, put_k = select_strangle_strikes(spot, em, strikes)
    if not call_k or not put_k:
        print("  (no equidistant pair resolvable — skipping)")
    else:
        try:
            ck = rpc("qualify_contract", a.symbol, expiry=expiry, strike=float(call_k),
                     right="C", trading_class="SPXW")
            pk = rpc("qualify_contract", a.symbol, expiry=expiry, strike=float(put_k),
                     right="P", trading_class="SPXW")
            ckq, pkq = _quote(ck), _quote(pk)
            cm, pm = _mid(ckq), _mid(pkq)
            print(f"  C {call_k:,.0f} mid={cm}   P {put_k:,.0f} mid={pm}")
            if cm and pm:
                gap = abs(cm - pm) / max(cm, pm) * 100.0
                ok = premiums_are_balanced(cm, pm)
                print(f"  premium gap: {gap:.1f}%  → 35% tolerance says "
                      f"{'BALANCED' if ok else 'REJECT'}")
                print(f"  → if real sessions cluster well above 35%, the tolerance is")
                print(f"    too tight and would veto ordinary index skew.")
                debit = (cm + pm) * 100
                print(f"  debit for 1 contract: ${debit:,.2f}  (= max loss per contract)")
        except Exception as e:
            print(f"  (skew read failed: {type(e).__name__}: {e})")

    print("\n" + "=" * 74)
    print("Nothing here decides anything. These are inputs to Steps 4–5, and the")
    print("three flagged assumptions in long_strangle_chain.py stay flagged until")
    print("a result above is written into the spec.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
