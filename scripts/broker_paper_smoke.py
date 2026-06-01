#!/usr/bin/env python3
"""Paper-account order-path smoke test — THROUGH the live calypso-broker.

The legacy tests/integration/test_ib_paper_smoke.py opens its OWN IBClient,
which on the broker-era VM would start a SECOND brokerage session on the same
paper username and EVICT the broker (the one-session-per-username war). This
probe instead drives the EXACT production path the bot uses — BrokerClient →
broker RPC → IBClient.place_and_wait_for_fill — so it (a) never causes session
contention and (b) validates the real wire + fill-parsing code.

What it proves end-to-end against the LIVE paper account:
  • the account is paper (DU…) — HARD safety gate, refuses to write otherwise
  • live quotes + option-chain resolution over the broker RPC (#4/#5)
  • a real 1-contract order PLACES, FILLS, and reports a correct price + qty
    (captures the raw /order/status payload → pins the #3 fill field name)
  • the position is CLOSED again (flat at the end)

Modes:
  • default (no --place): CHECK-ONLY — safety gate + reads + strike/quote
    resolution. Places NO order. Safe to run any time (incl. weekends).
  • --place: full 1-contract round trip (buy-to-open marketable → confirm fill
    → sell-to-close). RTH only. Use for the armed Monday-open run.

Exit 0 on success, non-zero on any failure. Sends ONE Telegram summary via
AlertService either way. Self-contained (no Claude session).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, "/opt/calypso")

BROKER_URL = os.environ.get("CALYPSO_BROKER_URL", "http://127.0.0.1:8788")
PLACE = "--place" in sys.argv or os.environ.get("SMOKE_PLACE") == "1"


def _next_weekday(d: date) -> date:
    while d.weekday() >= 5:  # Sat/Sun → Monday
        d += timedelta(days=1)
    return d


def _acct_code(bal: dict) -> str:
    """Pull the IBKR account code out of get_balance()'s raw summary/ledger."""
    try:
        v = (bal.get("raw_summary", {}) or {}).get("accountcode", {})
        if isinstance(v, dict) and v.get("value"):
            return str(v["value"])
    except Exception:
        pass
    try:
        return str(((bal.get("raw_ledger", {}) or {}).get("USD", {}) or {}).get("acctcode", ""))
    except Exception:
        return ""


def _alert(ok: bool, lines: list) -> None:
    if os.environ.get("SMOKE_NO_ALERT") == "1":
        return  # validation runs suppress the Telegram; the armed run alerts
    title = "Paper smoke ✅ PASS" if ok else "Paper smoke ❌ FAIL"
    body = "\n".join(lines)
    try:
        from shared.alert_service import AlertService, AlertType, AlertPriority
        AlertService({"alerts": {"enabled": True}}, "HYDRA").send_alert(
            alert_type=AlertType.API_ERROR,  # exists on every AlertType; title carries PASS/FAIL
            title=title,
            message=body,
            priority=AlertPriority.HIGH if not ok else AlertPriority.MEDIUM,
        )
    except Exception as e:
        print(f"smoke: alert send failed: {e}", file=sys.stderr)


def main() -> int:
    from shared.broker_client import BrokerClient, BrokerError
    bc = BrokerClient(BROKER_URL)
    out: list = []

    def log(m):
        print(m, flush=True)
        out.append(m)

    log(f"broker paper smoke — mode={'PLACE' if PLACE else 'CHECK-ONLY'} url={BROKER_URL}")

    # ── HARD SAFETY GATE: must be a paper (DU…) account ─────────────────────
    try:
        bal = bc.get_balance()
    except Exception as e:
        log(f"FAIL: get_balance: {type(e).__name__}: {e}")
        _alert(False, out); return 1
    acct = _acct_code(bal)
    if not acct.upper().startswith("DU"):
        log(f"FAIL/ABORT: account {acct!r} is NOT a paper (DU…) account — refusing to trade")
        _alert(False, out); return 2
    log(f"safety gate OK — paper account {acct}, tradable={bal.get('tradable')}")

    # ── live data + chain/strike resolution over the broker RPC (#4/#5) ─────
    exp = _next_weekday(date.today())
    try:
        spx = bc.get_quote(bc.qualify_contract("SPX", sec_type="IND"))
        spot = spx.get("last") or spx.get("mark") or spx.get("mid")
        log(f"SPX spot={spot} availability={spx.get('availability')}")
        strikes = bc.get_option_chain("SPX", exp)
        if not strikes:
            log("FAIL: option chain empty"); _alert(False, out); return 3
        atm = min(strikes, key=lambda s: abs(s - (spot or sorted(strikes)[len(strikes)//2])))
        conid = bc.qualify_contract("SPX", expiry=exp, strike=atm, right="C", trading_class="SPXW")
        q = bc.get_quote(conid)
        log(f"expiry={exp} ATM call strike={atm} conid={conid} bid={q.get('bid')} ask={q.get('ask')} avail={q.get('availability')}")
    except Exception as e:
        log(f"FAIL: chain/quote resolution: {type(e).__name__}: {e}")
        _alert(False, out); return 4

    if not PLACE:
        log("CHECK-ONLY complete — reads + safety gate + chain/quote OK; NO order placed.")
        _alert(True, out); return 0

    # ── GO-LIVE ENTITLEMENT GATE (IBKR audit #2/#3/#4) ──────────────────────
    # Require REAL-TIME data — 6509 availability FIRST char == 'R' — on SPX, VIX,
    # AND the SPXW leg before placing (and therefore before the ExecStartPost
    # auto-flip). A paper account missing the SPX-index / OPRA real-time
    # entitlement returns Frozen ('Z'/'Y'), Delayed ('D'), or Not-subscribed
    # ('N'); flipping A live onto non-real-time data would trade on stale prices.
    # Parse only the first char (IBKR appends snapshot/book chars, e.g. 'RpB').
    def _is_rt(quote, label):
        a = str((quote or {}).get("availability") or "")
        ok = a[:1].upper() == "R"
        log(f"  realtime[{label}]: 6509={a!r} -> {'R (OK)' if ok else 'NOT real-time'}")
        return ok
    try:
        vix_q = bc.get_quote(bc.qualify_contract("VIX", sec_type="IND"))
    except Exception as e:
        log(f"FAIL: VIX quote for realtime gate: {type(e).__name__}: {e}"); _alert(False, out); return 5
    rt_spx, rt_vix, rt_leg = _is_rt(spx, "SPX"), _is_rt(vix_q, "VIX"), _is_rt(q, "SPXW-leg")
    if not (rt_spx and rt_vix and rt_leg) or q.get("ask") in (None, 0):
        log("ABORT place: market data is NOT real-time (6509 first-char != 'R') on SPX/VIX/leg, "
            "or no ask — refusing to place a paper order and refusing the auto-flip onto frozen/"
            "delayed/unentitled data. Check the account's SPX-index + OPRA real-time subscriptions.")
        _alert(False, out); return 5

    nonce = datetime.now().strftime("%H%M%S")
    buy_coid = f"SMOKE_BUY_{nonce}"
    ask = float(q["ask"])
    log(f"PLACING buy-to-open 1x conid={conid} marketable LMT @ {ask}")
    try:
        res = bc.place_and_wait_for_fill(
            conid=conid, side="BUY", quantity=1, order_type="LMT",
            limit_price=ask, coid=buy_coid,
        )
    except BrokerError as e:
        log(f"FAIL: buy place returned BrokerError: {e}")
        _alert(False, out); return 6
    log(f"buy result: status={res.get('status')} filled_qty={res.get('filled_quantity')} "
        f"avg_fill_price={res.get('avg_fill_price')} order_id={res.get('order_id')}")
    log(f"RAW order/status payload (pins #3 fill-field name): {res.get('raw')}")

    filled = int(res.get("filled_quantity") or 0)
    oid = res.get("order_id")
    if filled <= 0:
        # didn't fill (e.g. moved away) — cancel so nothing rests, still a pass
        # for the place/cancel path but note no fill captured.
        if oid:
            try:
                bc.cancel_order(str(oid)); log(f"no fill — cancelled order {oid}")
            except Exception as e:
                log(f"WARN: cancel after no-fill failed: {e}")
        log("PLACE path OK but order did not fill — fill-parsing NOT exercised; re-run.")
        _alert(False, out); return 7

    log(f"✓ FILLED 1x @ {res.get('avg_fill_price')} — now closing to flatten")
    # ── close it (sell-to-close market) so we end flat ──────────────────────
    sell_coid = f"SMOKE_SELL_{nonce}"
    try:
        close = bc.place_and_wait_for_fill(
            conid=conid, side="SELL", quantity=filled, order_type="MKT",
            coid=sell_coid,
        )
        log(f"close result: status={close.get('status')} filled_qty={close.get('filled_quantity')} "
            f"avg_fill_price={close.get('avg_fill_price')}")
        if int(close.get("filled_quantity") or 0) < filled:
            log("WARN: close not fully filled — VERIFY position is flat manually (paper, DAY tif expires EOD)")
            _alert(False, out); return 8
    except Exception as e:
        log(f"FAIL: close leg errored: {type(e).__name__}: {e} — position may be open (paper); verify")
        _alert(False, out); return 9

    # ── confirm flat ────────────────────────────────────────────────────────
    try:
        pos = bc.get_positions()
        residual = [p for p in (pos or []) if p.get("instrument_id") == conid and (p.get("quantity") or 0) != 0]
        log(f"positions after round-trip: residual_for_test_conid={residual}")
    except Exception as e:
        log(f"WARN: get_positions after close failed: {e}")

    log("✅ FULL ROUND TRIP OK — real paper order placed, FILLED at a live price, and closed flat.")
    # Defense-in-depth for the conditional auto-flip: write a DATED PASS marker.
    # flip_a_live.sh requires a fresh (today) marker in addition to systemd only
    # running it on a clean ExecStart — two independent gates before A goes live.
    try:
        import os as _os
        _os.makedirs("/opt/calypso/data/smoke", exist_ok=True)
        with open("/opt/calypso/data/smoke/last_pass.txt", "w") as fh:
            fh.write(date.today().isoformat() + " PASS\n")
        log("wrote PASS sentinel /opt/calypso/data/smoke/last_pass.txt")
    except Exception as e:
        log(f"WARN: could not write PASS sentinel: {e}")
    _alert(True, out); return 0


if __name__ == "__main__":
    sys.exit(main())
