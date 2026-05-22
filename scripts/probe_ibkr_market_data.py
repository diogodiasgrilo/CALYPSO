"""P7 Step 2 — verify the IBKR paper account delivers real-time market data.

Checks the four data feeds HYDRA depends on, via the exact IBClient
methods the bot uses, and reports for each whether the quote came back
REAL-TIME, DELAYED, or STALE/frozen:

  1. SPX index spot   (qualify_contract IND + get_quote)
  2. VIX index level  (qualify_contract IND + get_quote, + get_vix_price)
  3. SPX 0DTE option quotes  (qualify_option_strikes + get_quote)
  4. SPX 0DTE option greeks  (get_option_greeks — delta/gamma/theta/vega?)

IBClient.get_quote() returns an `availability` field: 'R'=real-time,
'D'=delayed, 'Z'=stale/frozen. That flag is the verdict.

NOTE: the real-time-vs-delayed verdict is only meaningful during
regular market hours (09:30-16:00 ET). Outside them even a fully
entitled quote can read 'Z' (frozen) — re-run intraday for the
definitive check.

SAFE: read-only. No orders, no writes. Asserts paper environment.

Usage:
    cd "/Users/ddias/Desktop/CALYPSO/Git Repo"
    source .venv/bin/activate
    # the 3 IBIND_OAUTH1A_* env vars must be exported in this shell
    python scripts/probe_ibkr_market_data.py 2>&1 | tee scripts/probe_mktdata_$(date +%H%M%S).log
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_client import IBClient, IBConfig
from shared.ib_oauth import load_credentials
from shared.market_hours import is_market_open, get_us_market_time

_AVAIL = {"R": "REAL-TIME ✅", "D": "DELAYED ⚠️", "Z": "STALE/FROZEN"}


def _verdict(avail) -> str:
    return _AVAIL.get((avail or "").upper()[:1], f"UNKNOWN ({avail!r})")


def _show_quote(label: str, q: dict) -> None:
    print(f"\n━━━ {label} ━━━")
    if not q:
        print("  no quote returned")
        return
    print(f"  bid={q.get('bid')}  ask={q.get('ask')}  last={q.get('last')}  "
          f"mid={q.get('mid')}  mark={q.get('mark')}")
    print(f"  availability: {q.get('availability')!r} → {_verdict(q.get('availability'))}")


def _next_trading_day(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def main() -> int:
    creds = load_credentials("paper")
    assert creds.environment == "paper", (
        f"SAFETY: probe must run on paper, got {creds.environment!r}"
    )

    now_et = get_us_market_time()
    mkt_open = is_market_open()
    print(f"Current time: {now_et:%Y-%m-%d %H:%M:%S %Z}")
    print(f"Regular market hours right now: {'YES' if mkt_open else 'NO'}")
    if not mkt_open:
        print("  ⚠️  Market closed — quotes may read STALE even when entitled.")
        print("      Re-run during 09:30-16:00 ET for the definitive verdict.")

    client = IBClient(IBConfig(credentials=creds))
    results: dict[str, str] = {}
    try:
        client.connect()
        print("\nConnected to IBKR paper account.")

        # 1. SPX index spot
        spx_conid = client.qualify_contract("SPX", sec_type="IND")
        spx_q = client.get_quote(spx_conid)
        _show_quote(f"1. SPX index  (conid {spx_conid})", spx_q)
        results["SPX index"] = _verdict(spx_q.get("availability"))
        spx_spot = spx_q.get("last") or spx_q.get("mid") or spx_q.get("mark")

        # 2. VIX index
        vix_conid = client.qualify_contract("VIX", sec_type="IND")
        vix_q = client.get_quote(vix_conid)
        _show_quote(f"2. VIX index  (conid {vix_conid})", vix_q)
        results["VIX index"] = _verdict(vix_q.get("availability"))
        print(f"  get_vix_price() convenience method → {client.get_vix_price()}")

        # 3 + 4. SPX 0DTE option quotes + greeks
        if not spx_spot:
            print("\n⚠️  No SPX spot — skipping option probes.")
            results["SPX options"] = "SKIPPED (no SPX spot)"
            results["SPX greeks"] = "SKIPPED"
        else:
            expiry = _next_trading_day(now_et.date())
            atm = round(spx_spot / 25) * 25
            print(f"\nResolving SPX {expiry.isoformat()} options near "
                  f"ATM {atm} (SPX spot ≈ {spx_spot})...")
            conids = client.qualify_option_strikes(
                symbol="SPX", expiry=expiry, strikes=[float(atm)],
                trading_class="SPXW",
            )
            opt_verdicts, greek_ok = [], []
            for right, label in (("C", "call"), ("P", "put")):
                conid = conids.get((float(atm), right))
                if not conid:
                    print(f"  {label} {atm}: conid did not resolve")
                    continue
                q = client.get_quote(conid)
                _show_quote(f"3. SPX {atm}{right} 0DTE {label}  (conid {conid})", q)
                opt_verdicts.append(_verdict(q.get("availability")))
                g = client.get_option_greeks(conid)
                greeks = {k: g.get(k) for k in
                          ("delta", "gamma", "theta", "vega", "iv", "open_interest")}
                print(f"  greeks: {greeks}")
                have = [k for k in ("delta", "gamma", "theta", "vega")
                        if greeks.get(k) is not None]
                greek_ok.append(bool(have))
                print(f"  → greeks present: {have or 'NONE (only IV?)'}")
            results["SPX options"] = (
                opt_verdicts[0] if opt_verdicts else "SKIPPED")
            results["SPX greeks"] = (
                "delta/gamma/theta/vega present ✅" if greek_ok and all(greek_ok)
                else "IV only — needs local Black-Scholes" if greek_ok
                else "SKIPPED")

        print("\n" + "=" * 60)
        print("STEP 2 SUMMARY")
        print("=" * 60)
        for k, v in results.items():
            print(f"  {k:14s}: {v}")
        if not mkt_open:
            print("\n  (Market closed — re-run intraday to confirm real-time.)")
    finally:
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
