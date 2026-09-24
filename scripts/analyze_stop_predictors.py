"""Which ENTRY-TIME feature predicts that a side will be stopped?

Per SIDE (call/put separately) — that is the unit the stop acts on.
"""
import sqlite3, statistics as st
c = sqlite3.connect('file:/opt/calypso/data/variant_b/backtesting.db?mode=ro', uri=True)
ent = c.execute("""SELECT date, entry_number, entry_time, vix_at_entry,
                          call_credit, put_credit, otm_distance_call, otm_distance_put,
                          delta_call, delta_put, spx_at_entry
                   FROM trade_entries""").fetchall()
stopped = {(d, e, s) for d, e, s in c.execute(
    "SELECT date, entry_number, side FROM trade_stops WHERE exit_reason='stop_loss'")}

def _hr(t):
    """entry_time is inconsistently formatted (bare HH:MM and full timestamps)."""
    if not t: return None
    s = str(t)
    if " " in s: s = s.split(" ")[1]
    try:
        h, m = s.split(":")[:2]
        return int(h) + int(m) / 60
    except Exception:
        return None

rows = []
for d, en, t, vix, cc, pc, oc, op, dc, dp, spx in ent:
    for side, cred, otm, dlt in (("call", cc, oc, dc), ("put", pc, op, dp)):
        if otm is None or not spx:
            continue
        rows.append(dict(side=side, stop=(d, en, side) in stopped,
                         otm_pct=otm / spx * 100, otm_pt=otm, credit=cred or 0,
                         vix=vix or 0, delta=abs(dlt) if dlt else None,
                         hour=_hr(t)))
S = [r for r in rows if r["stop"]]; N = [r for r in rows if not r["stop"]]
print(f"sides: {len(rows)}   stopped {len(S)} ({len(S)/len(rows):.0%})   survived {len(N)}\n")
print(f"{'feature':16} {'stopped':>12} {'survived':>12}   separation")
for k, lab in (("otm_pct","OTM dist %"),("otm_pt","OTM dist pt"),("credit","credit $"),
               ("vix","VIX"),("hour","entry hour"),("delta","|delta|")):
    a = [r[k] for r in S if r[k] is not None]; b = [r[k] for r in N if r[k] is not None]
    if len(a) < 3 or len(b) < 3: continue
    pooled = (st.pstdev(a+b) or 1)
    d_eff = (st.mean(a) - st.mean(b)) / pooled
    flag = "  <-- STRONG" if abs(d_eff) > 0.5 else ("  <- moderate" if abs(d_eff) > 0.3 else "")
    print(f"{lab:16} {st.mean(a):12.2f} {st.mean(b):12.2f}   d={d_eff:+.2f}{flag}")

print("\nstop rate by OTM-distance quartile (the candidate lever):")
allr = sorted([r for r in rows if r["otm_pct"]], key=lambda r: r["otm_pct"])
q = len(allr)//4
for i in range(4):
    grp = allr[i*q:(i+1)*q] if i < 3 else allr[3*q:]
    sr = sum(1 for r in grp if r["stop"])/len(grp)
    print(f"  Q{i+1}  OTM {grp[0]['otm_pct']:.2f}-{grp[-1]['otm_pct']:.2f}%   n={len(grp):3}  stop rate {sr:.0%}")
