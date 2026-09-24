"""For sides that ACTUALLY stopped: what were they worth at the session's end?

Answers "is the stop helping at all?" — which the %-of-width SHADOW aggregator
(bots/hydra/stop_shadow.py) deliberately cannot, because it scores 0 wherever
the shadow never fires.

Uses `spread_snapshots` — the bot's OWN recorded cost-to-close every ~10s — so
"what it would have cost to hold" is the value the bot itself last observed, not
a price approximation.

⚠️ SUPERSEDES scripts/analyze_stop_counterfactual.py, which priced each side
against that day's spx_close capped at the spread width and OVERSTATED the
effect by roughly 4x (-$12,381 vs the real +$3,360 on B). Prefer this one.

Measured 2026-09-24: on B, 17 of 21 stopped sides would have been better held,
net +$3,360, median +$175/side. On C, 8 of 9, net +$1,195. Both agree the stop
is MILDLY net-negative — but holding does not turn these into winners, it loses
somewhat less. The damage is done before the stop fires.

Findings: docs/WHAT_WOULD_SOFTEN_B_2026_09_24.md

    python -m scripts.analyze_stop_vs_hold --variant b
"""
import argparse, sqlite3, statistics as st
ap = argparse.ArgumentParser(); ap.add_argument("--variant", default="b")
a = ap.parse_args()
db = f"/opt/calypso/data/variant_{a.variant}/backtesting.db"
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

cols = {r[1] for r in c.execute("PRAGMA table_info(spread_snapshots)")}
sv_call = "call_spread_value" if "call_spread_value" in cols else None
sv_put = "put_spread_value" if "put_spread_value" in cols else None
if not sv_call:
    print("spread_snapshots columns:", sorted(cols)); raise SystemExit

stops = c.execute("""SELECT s.date, s.entry_number, s.side, s.net_pnl,
                            e.call_credit, e.put_credit, e.contracts
                     FROM trade_stops s
                     JOIN trade_entries e ON e.date=s.date AND e.entry_number=s.entry_number
                     WHERE s.exit_reason='stop_loss'""").fetchall()
better = worse = 0
delta_total = 0.0
rows = []
for d, en, side, pnl, cc, pc, n in stops:
    col = sv_call if side == "call" else sv_put
    last = c.execute(f"""SELECT {col} FROM spread_snapshots
                         WHERE date=? AND entry_number=? AND {col} IS NOT NULL
                         ORDER BY timestamp DESC LIMIT 1""", (d, en)).fetchone()
    if not last or last[0] is None:
        continue
    credit = (cc if side == "call" else pc) or 0.0
    hold_pnl = credit - float(last[0])      # both already in dollars
    delta = hold_pnl - (pnl or 0.0)         # + => holding would have been better
    rows.append((d, en, side, pnl, hold_pnl, delta))
    delta_total += delta
    if delta > 0: better += 1
    else: worse += 1

print(f"variant {a.variant.upper()}: {len(rows)} actual stop-loss sides with snapshot data\n")
print(f"  holding would have been BETTER on {better}")
print(f"  stopping was better on          {worse}")
print(f"  NET of holding instead of stopping: ${delta_total:+,.0f}")
if rows:
    ds = [r[5] for r in rows]
    print(f"  median per side ${st.median(ds):+,.0f}   mean ${st.mean(ds):+,.0f}")
    print("\n  worst 6 for the stop (holding would have saved most):")
    for d, en, side, pnl, hold, dl in sorted(rows, key=lambda r: -r[5])[:6]:
        print(f"    {d} E#{en} {side:4}  stopped ${pnl:8.0f}  held ${hold:8.0f}  diff ${dl:+8.0f}")
    print("\n  best 4 for the stop (it genuinely saved):")
    for d, en, side, pnl, hold, dl in sorted(rows, key=lambda r: r[5])[:4]:
        print(f"    {d} E#{en} {side:4}  stopped ${pnl:8.0f}  held ${hold:8.0f}  diff ${dl:+8.0f}")
