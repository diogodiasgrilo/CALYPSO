#!/usr/bin/env python3
"""Rebuild a REAL past session as dashboard fixtures.

The live view reads state-file entries, but the state file only holds TODAY.
Every historical session survives in the database, so a faithful replay means
mapping trade_entries + trade_stops back into the shape the UI consumes. This
is the only way to see the dashboard under REAL positions — with cushions,
stops, one-sided entries and a large loss — without waiting for a session that
happens to trade.

Source: B, 2026-07-16 — its worst day (-$7,074): 7 entries, all put_only, 6
stops mixing early_close and stop_loss, 10 contracts.
"""
import json, shutil, sys
from pathlib import Path

raw = json.load(open("/tmp/replay_raw.json"))
src, dst = Path("fixtures"), Path("fixtures_replay")
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst)

stops_by = {}
for s in raw["stops"]:
    stops_by.setdefault(int(s["entry_number"]), {})[str(s["side"])] = s

def to_iso_et(ts):
    """DB timestamps are NAIVE wall-clock ET ("2026-07-16 09:46:04"); the state
    file — which the live view reads — uses ISO WITH OFFSET. Feeding the naive
    form into the state path makes `formatTime` treat it as UTC and render the
    entry 4-5 hours early. That is a fixture-faithfulness issue, not a product
    bug (the real DB consumers extract the clock time by regex), but a replay
    that renders the wrong times is not a replay.
    """
    if not ts:
        return ts
    v = str(ts).strip().replace(" ", "T")
    if "+" in v or v.endswith("Z") or v.count("-") > 2:
        return v
    return v + "-04:00"          # 2026-07-16 is EDT


def f(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d

entries = []
for e in raw["entries"]:
    n = int(e["entry_number"])
    st = stops_by.get(n, {})
    call_s, put_s = st.get("call"), st.get("put")
    et = str(e.get("entry_type") or "")
    entries.append({
        "entry_number": n,
        "entry_time": to_iso_et(e.get("entry_time")),
        "short_call_strike": f(e.get("short_call_strike")),
        "long_call_strike": f(e.get("long_call_strike")),
        "short_put_strike": f(e.get("short_put_strike")),
        "long_put_strike": f(e.get("long_put_strike")),
        "call_spread_credit": f(e.get("call_credit")),
        "put_spread_credit": f(e.get("put_credit")),
        "contracts": int(e.get("contracts") or 1),
        "call_only": et == "call_only",
        "put_only": et == "put_only",
        # A side with no credit was never placed — that is what "one-sided"
        # means in this schema, and it is exactly the phantom-zero case the
        # renderers must not draw.
        "call_side_skipped": f(e.get("call_credit")) <= 0,
        "put_side_skipped": f(e.get("put_credit")) <= 0,
        "call_side_stopped": bool(call_s and call_s.get("exit_reason") == "stop_loss"),
        "put_side_stopped": bool(put_s and put_s.get("exit_reason") == "stop_loss"),
        "call_side_expired": bool(call_s and call_s.get("exit_reason") == "early_close"),
        "put_side_expired": bool(put_s and put_s.get("exit_reason") == "early_close"),
        "call_stop_time": (call_s or {}).get("stop_time") or "",
        "put_stop_time": (put_s or {}).get("stop_time") or "",
        "actual_call_stop_debit": f((call_s or {}).get("actual_debit")),
        "actual_put_stop_debit": f((put_s or {}).get("actual_debit")),
        "call_side_stop": f((call_s or {}).get("trigger_level")),
        "put_side_stop": f((put_s or {}).get("trigger_level")),
        "effective_call_stop": f((call_s or {}).get("trigger_level")),
        "effective_put_stop": f((put_s or {}).get("trigger_level")),
        "close_reason": (put_s or call_s or {}).get("exit_reason"),
        "is_complete": True,
        "overlays": [],
        # Commission is why the card's TODAY differed from the DB's net_pnl by
        # $125 on the first replay: the entry-derived sum is GROSS.
        "open_commission": f(e.get("commission_open")) or 0.0,
        "close_commission": f((put_s or call_s or {}).get("commission_close")) or 0.0,
    })

ticks = raw["ticks"]
ohlc = []
by_min = {}
for t in ticks:
    ts, px = str(t.get("timestamp") or ""), f(t.get("spx_price"))
    if not ts or px <= 0:
        continue
    by_min.setdefault(ts[:16], []).append((px, f(t.get("vix_level"))))
for k in sorted(by_min):
    ps = [p for p, _ in by_min[k]]
    vs = [v for _, v in by_min[k] if v]
    ohlc.append({"timestamp": f"{k}:00", "open": ps[0], "high": max(ps),
                 "low": min(ps), "close": ps[-1], "vix": vs[-1] if vs else 0})

summ = raw["summary"]
p = dst / "snapshot_b.json"
snap = json.load(open(p))
b = snap["body"]
b["entries"] = entries
b["ohlc"] = ohlc
b["date"] = summ.get("date")
b["spx_price"] = ohlc[-1]["close"] if ohlc else 0
b["spx_open"], b["spx_high"], b["spx_low"] = (
    summ.get("spx_open"), summ.get("spx_high"), summ.get("spx_low"))
b["vix_open"] = summ.get("vix_open")
b["summary"] = {**(b.get("summary") or {}),
                "net_pnl": summ.get("net_pnl"),
                "entries_placed": summ.get("entries_placed"),
                "entries_stopped": summ.get("entries_stopped"),
                "total_credit": summ.get("total_credit")}
json.dump(snap, open(p, "w"))

# The PRIMARY (live-seat) view reads the WS payload, not /snapshot.
wp = dst / "ws_snapshot.json"
ws = json.load(open(wp))
ws["state"] = {**(ws.get("state") or {}), "entries": entries,
               "date": summ.get("date"),
               "total_realized_pnl": summ.get("net_pnl"),
               "state": "DailyComplete"}
ws["today_entries"] = entries
ws["today_ohlc"] = ohlc
json.dump(ws, open(wp, "w"))
json.dump(ohlc, open(dst / "market_ohlc.json", "w"))

# Intraday P&L curve from the per-entry spread snapshots — without it the
# INTRADAY P&L panel renders a flat $0 line on a day that lost $7,074.
hist, run = [], {}
for sn in raw["snaps"]:
    ts = str(sn.get("timestamp") or "")
    if not ts:
        continue
    run[ts[:16]] = f(sn.get("total_pnl"), 0.0) if "total_pnl" in sn else run.get(ts[:16], 0.0)
for k in sorted(run):
    hist.append({"timestamp": f"{k}:00", "pnl": run[k]})
if hist:
    b["pnl_history"] = hist
    ws["state"]["pnl_history"] = hist
    json.dump(snap, open(p, "w"))
    json.dump(ws, open(wp, "w"))
print(f"  pnl_history points: {len(hist)}")

placed = [e for e in entries if not (e["call_side_skipped"] and e["put_side_skipped"])]
print(f"  replay built: {len(entries)} entries ({len(placed)} placed), "
      f"{len(ohlc)} bars, net {summ.get('net_pnl')}")
print(f"  one-sided: {sum(1 for e in entries if e['put_only'] or e['call_only'])}")
print(f"  stopped:   {sum(1 for e in entries if e['put_side_stopped'] or e['call_side_stopped'])}")
print(f"  expired:   {sum(1 for e in entries if e['put_side_expired'] or e['call_side_expired'])}")
