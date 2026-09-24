"""What would an OTM / delta FLOOR have been worth to B?

Per side: P&L = credit if it survived, else the recorded stop net_pnl.
Refusing a side forfeits its credit — so this prices the trade-off honestly.
"""
import sqlite3, statistics as st
c = sqlite3.connect('file:/opt/calypso/data/variant_b/backtesting.db?mode=ro', uri=True)
stops = {(d, e, s): p for d, e, s, p in c.execute(
    "SELECT date, entry_number, side, net_pnl FROM trade_stops WHERE exit_reason='stop_loss'")}
sides = []
for d, en, cc, pc, oc, op, dc, dp, spx in c.execute(
        """SELECT date, entry_number, call_credit, put_credit,
                  otm_distance_call, otm_distance_put, delta_call, delta_put, spx_at_entry
           FROM trade_entries"""):
    for side, cred, otm, dl in (("call", cc, oc, dc), ("put", pc, op, dp)):
        if otm is None or not spx: continue
        pnl = stops.get((d, en, side))
        sides.append(dict(otm=otm/spx*100, dlt=abs(dl) if dl else None,
                          cred=cred or 0.0,
                          pnl=pnl if pnl is not None else (cred or 0.0),
                          stopped=pnl is not None))
base = sum(s["pnl"] for s in sides)
print(f"all {len(sides)} sides: total ${base:+,.0f}   ({sum(1 for s in sides if s['stopped'])} stopped)\n")
print("OTM FLOOR — refuse any side closer than the threshold:")
print(f"  {'floor':>7} {'refused':>8} {'credit lost':>12} {'stops avoided':>14} {'NET vs today':>13}")
for f in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70):
    ref = [s for s in sides if s["otm"] < f]
    kept = [s for s in sides if s["otm"] >= f]
    new = sum(s["pnl"] for s in kept)
    print(f"  {f:6.2f}% {len(ref):8} {sum(s['cred'] for s in ref if not s['stopped']):12,.0f} "
          f"{sum(1 for s in ref if s['stopped']):14} {new-base:+13,.0f}")
print("\nDELTA CEILING — refuse any side above the threshold:")
print(f"  {'ceiling':>8} {'refused':>8} {'stops avoided':>14} {'NET vs today':>13}")
for f in (0.09, 0.10, 0.11, 0.12, 0.15, 0.20):
    have = [s for s in sides if s["dlt"] is not None]
    kept = [s for s in have if s["dlt"] <= f]
    ref = [s for s in have if s["dlt"] > f]
    if not ref: continue
    b2 = sum(s["pnl"] for s in have)
    print(f"  {f:7.2f} {len(ref):8} {sum(1 for s in ref if s['stopped']):14} "
          f"{sum(s['pnl'] for s in kept)-b2:+13,.0f}")
