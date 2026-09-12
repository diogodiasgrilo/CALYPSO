#!/usr/bin/env python3
"""
READ-ONLY probe: how wide are the real bid/ask spreads on a D-style SPX double
calendar, and what does that imply for the modeled round-trip cost?

WHY
---
A 2026-09-06 forensic found that ~74% of strategy D's -$4,877 same-day-era loss
was the SIMULATOR's own modeled bid/ask crossing — a median 14.3% of the net
debit charged at t=0, before the market moved. That number has never been
checked against a live chain, and none of variants D/E/F/G has ever placed a
real order, so the entire cost axis that decides these strategies rests on an
untested assumption.

This is the read-only half of validating it. It quotes a real calendar
structure and reports what the spread actually is, what a round trip costs at
each fill aggressiveness, and whether the quotes are even real-time.
It PLACES NO ORDERS, writes no state, and touches no strategy.

It CANNOT answer "would a limit order near mid actually fill?" — only a real
order can. It CAN tell you whether the 14.3% figure is representative, which
is the input to deciding whether that experiment is worth running.

USAGE (on the VM, as the calypso user, during market hours)
-----------------------------------------------------------
  sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m scripts.probe_calendar_spread'

Options:
  --short-dte N   target DTE for the near leg (default 10; D uses 6-15)
  --long-gap N    extra days for the far leg (default 2; D uses 1-4)
  --otm-pct P     strike offset from spot, percent (default 2.0)
  --contracts N   scale the dollar figures (default 1)
  --json          machine-readable output
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.main import _build_broker  # noqa: E402

LEGS = ("short_call", "long_call", "short_put", "long_put")


def _fill(mid, bid, ask, action, agg):
    """Mirror CalendarStrategyBase._dc_fill_price with slip=0."""
    if mid is None or mid <= 0:
        return None
    if action == "buy":
        return mid + agg * (ask - mid) if (ask and ask >= mid) else mid
    return mid - agg * (mid - bid) if (bid and 0 < bid <= mid) else mid


def _num(v):
    """IBKR prefixes some price fields with C/H markers."""
    try:
        return float(str(v).lstrip("CH"))
    except (TypeError, ValueError, AttributeError):
        return None


def _pick_expiry(broker, target: date, strikes_near: float, back: int = 6):
    """Nearest listed expiry at/after `target` that has a usable chain.
    Returns (date, set_of_strikes) or (None, set())."""
    for delta in range(0, back + 1):
        for cand in ({target + timedelta(days=delta), target - timedelta(days=delta)}
                     if delta else {target}):
            if cand < date.today():
                continue
            try:
                chain = broker.get_option_chain("SPX", cand,
                                                trading_class="SPXW", exchange="CBOE")
            except Exception:
                continue
            raw = chain if isinstance(chain, list) else (
                chain.get("strikes", []) if isinstance(chain, dict) else [])
            ks = set()
            for s in raw:
                n = _num(s)
                if n:
                    ks.add(round(n, 2))
            if ks:
                return cand, ks
    return None, set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--short-dte", type=int, default=10)
    ap.add_argument("--long-gap", type=int, default=2)
    ap.add_argument("--otm-pct", type=float, default=2.0)
    ap.add_argument("--contracts", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    broker = _build_broker()
    if not broker.connect():
        print("ERROR: broker not connected", file=sys.stderr)
        return 2

    spx = broker.qualify_contract("SPX", sec_type="IND", exchange="CBOE")
    conid = spx.get("conid") if isinstance(spx, dict) else spx
    q = broker.get_quote(int(conid)) or {}
    spot = _num(q.get("last") or q.get("31") or q.get("mid"))
    if not spot:
        print(f"ERROR: could not read SPX spot from {q}", file=sys.stderr)
        return 2

    today = date.today()
    short_exp, short_ks = _pick_expiry(broker, today + timedelta(days=a.short_dte), spot)
    if not short_exp:
        print("ERROR: no usable near expiry", file=sys.stderr)
        return 2
    long_exp, long_ks = _pick_expiry(
        broker, short_exp + timedelta(days=a.long_gap), spot)
    if not long_exp:
        print("ERROR: no usable far expiry", file=sys.stderr)
        return 2

    common = short_ks & long_ks
    if not common:
        print("ERROR: near and far expiries share no strike", file=sys.stderr)
        return 2
    call_k = min(common, key=lambda k: abs(k - spot * (1 + a.otm_pct / 100)))
    put_k = min(common, key=lambda k: abs(k - spot * (1 - a.otm_pct / 100)))

    # Resolve conids: one batch call per expiry.
    want = {
        "short_call": (short_exp, call_k, "C"),
        "long_call": (long_exp, call_k, "C"),
        "short_put": (short_exp, put_k, "P"),
        "long_put": (long_exp, put_k, "P"),
    }
    resolved: dict = {}
    for exp in (short_exp, long_exp):
        try:
            got = broker.qualify_option_strikes(
                symbol="SPX", expiry=exp, strikes=[call_k, put_k],
                trading_class="SPXW", exchange="CBOE",
            ) or {}
        except Exception as e:
            print(f"ERROR: strike resolution failed for {exp}: {e}", file=sys.stderr)
            return 2
        for name, (e_, k_, r_) in want.items():
            if e_ == exp and (k_, r_) in got:
                resolved[name] = got[(k_, r_)]

    missing = [n for n in LEGS if n not in resolved]
    if missing:
        print(f"ERROR: unresolved legs {missing}", file=sys.stderr)
        return 2

    quotes = broker.get_quotes_batch([int(resolved[n]) for n in LEGS]) or []
    by_conid = {int(r.get("conid")): r for r in quotes if r.get("conid")}

    rows = {}
    for name in LEGS:
        exp, strike, right = want[name]
        r = by_conid.get(int(resolved[name]), {})
        bid, ask = _num(r.get("bid") or r.get("84")), _num(r.get("ask") or r.get("86"))
        mid = (bid + ask) / 2 if (bid and ask and ask >= bid) else None
        rows[name] = {
            "conid": int(resolved[name]), "expiry": exp.isoformat(), "strike": strike,
            "right": right, "bid": bid, "ask": ask, "mid": mid,
            "spread": (ask - bid) if (bid is not None and ask is not None) else None,
            "realtime": str(r.get("6509", "?"))[:2],
        }

    result = {"spot": spot, "contracts": a.contracts,
              "short_expiry": short_exp.isoformat(), "long_expiry": long_exp.isoformat(),
              "legs": rows}

    usable = [r for r in rows.values() if r.get("mid")]
    if len(usable) == 4:
        n = a.contracts

        def debit(agg):
            return sum(
                _fill(rows[nm]["mid"], rows[nm]["bid"], rows[nm]["ask"],
                      "sell" if nm.startswith("short") else "buy", agg)
                * (-1 if nm.startswith("short") else 1)
                for nm in LEGS) * 100 * n

        def value(agg):
            px = {nm: _fill(rows[nm]["mid"], rows[nm]["bid"], rows[nm]["ask"],
                            "buy" if nm.startswith("short") else "sell", agg)
                  for nm in LEGS}
            return ((px["long_call"] - px["short_call"])
                    + (px["long_put"] - px["short_put"])) * 100 * n

        result["analysis"] = {
            "full_round_trip_spread": sum(r["spread"] for r in usable) * 100 * n,
            "by_agg": {
                f"{g}": {
                    "debit": debit(g), "immediate_mark": value(g),
                    "birth_toll": debit(g) - value(g),
                    "toll_pct_of_debit": ((debit(g) - value(g)) / debit(g) * 100
                                          if debit(g) else None),
                } for g in (0.0, 0.5, 1.0)
            },
        }

    if a.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    print(f"\nSPX {spot:.2f} | near {short_exp} | far {long_exp} | "
          f"Kc={call_k:.0f} Kp={put_k:.0f} | {a.contracts}c")
    print(f"\n{'leg':12} {'strike':>7} {'bid':>8} {'ask':>8} {'mid':>8} "
          f"{'spread':>8} {'sprd%':>7}  rt")
    for nm in LEGS:
        r = rows[nm]
        sp, mid = r["spread"], r["mid"]
        pct = (sp / mid * 100) if (sp and mid) else 0.0
        print(f"{nm:12} {r['strike']:>7.0f} {r['bid'] or 0:>8.2f} {r['ask'] or 0:>8.2f} "
              f"{mid or 0:>8.2f} {sp if sp is not None else 0:>8.2f} {pct:>6.1f}%  "
              f"{r['realtime']}")

    an = result.get("analysis")
    if not an:
        print("\nIncomplete quotes — cannot compute the round-trip cost.")
        print("If 'rt' is not R, the chain is delayed/frozen and these spreads")
        print("are not representative. Re-run during regular trading hours.\n")
        return 1

    print(f"\nFull round-trip spread, all 4 legs: ${an['full_round_trip_spread']:,.2f}")
    print(f"\n{'agg':>5} {'entry debit':>13} {'immediate mark':>15} "
          f"{'birth toll':>12} {'% of debit':>11}")
    for g, v in an["by_agg"].items():
        print(f"{g:>5} {v['debit']:>13,.2f} {v['immediate_mark']:>15,.2f} "
              f"{v['birth_toll']:>12,.2f} {(v['toll_pct_of_debit'] or 0):>10.1f}%")
    print("\nREADING THIS: the 2026-09-06 forensic measured a MEDIAN birth toll of")
    print("14.3% of net debit across D's 19 full-touch (agg=1.0) same-day trades.")
    print("Compare the agg=1.0 row. A close match means that sample was quoted")
    print("under normal liquidity and the modeled cost is representative. A much")
    print("smaller number here means the historical sample was quoted under worse")
    print("liquidity than today — not that the model is wrong.")
    print("\nNeither answers whether a real limit order near mid would FILL.")
    print("Only placing one does.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
