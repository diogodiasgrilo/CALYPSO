"""Does variant H stabilise variant B? Model H over B's own live days.

Answers the 2026-09-24 question "do they complement each other?" by pricing a
long strangle against every day B actually traded, then measuring the
correlation and what the combined curve would have looked like.

⚠️ NOT A BACKTEST, and the difference matters. The expected move is calibrated
from ONE real observation (H's 2026-09-24 entry: SPY 764.69, VIX 15.73 -> EM
2.8pt, debit $1.02/share) and scaled by VIX elsewhere; intraday exits are
approximated from daily OHLC, crediting the +50% target whenever the best
single-sided excursion reaches 1.5x the debit. That approximation is GENEROUS to
H, so the real figures are likely worse rather than better. Commissions and
slippage are excluded, and both hurt H.

It is enough to answer a directional question. It is not enough to size
anything. Findings: docs/H_AS_A_HEDGE_FOR_B_2026_09_24.md

    python -m scripts.analyze_h_hedges_b            # gated (H's real filters)
    python -m scripts.analyze_h_hedges_b --ungated  # every day
"""

import argparse, sqlite3, statistics as st, sys
sys.path.insert(0, '/opt/calypso')
from bots.hydra.iv_percentile import iv_percentile
from bots.hydra.long_strangle_chain import range_expansion_signal

C = sqlite3.connect('file:/opt/calypso/data/variant_b/backtesting.db?mode=ro', uri=True)
rows = C.execute("""SELECT date, net_pnl, spx_open, spx_high, spx_low, spx_close, vix_open
                    FROM daily_summaries
                    WHERE spx_high>0 AND spx_low>0 AND spx_open>0 AND net_pnl IS NOT NULL
                      AND vix_open>0 ORDER BY date""").fetchall()
# VIX history (ticks + backfill), one close per prior day.
vh = {d: v for d, v in C.execute(
    "SELECT date, close FROM vix_daily WHERE close>0")}
# Daily ranges for the range-expansion gate.
rr = {d: (h - l) for d, h, l in C.execute(
    "SELECT date, spx_high, spx_low FROM daily_summaries WHERE spx_high>0 AND spx_low>0")}
alldates = sorted(set(vh) | set(rr))

EM_PER_VIX_PCT = (2.8 / 764.69) / 15.73
DEBIT_OVER_EM  = 1.02 / 2.8

def gates(d, vix):
    prior_v = [vh[x] for x in alldates if x < d and x in vh][-252:]
    pct = iv_percentile(vix, prior_v, min_history=60)
    if pct is None or pct > 35.0:
        return False, f"IV {('n/a' if pct is None else f'{pct:.0f}%')}"
    prior_r = [rr[x] for x in alldates if x < d and x in rr][-80:]
    fires, _ = range_expansion_signal(prior_r)
    if not fires and len(prior_r) >= 11:
        return False, "no range expansion"
    return True, "pass"

traded, skipped = [], 0
for d, bpnl, o, hi, lo, cl, vix in rows:
    ok, _ = gates(d, vix)
    if not ok:
        skipped += 1
        continue
    em = o * EM_PER_VIX_PCT * vix
    callK, putK = o + em, o - em
    debit = em * DEBIT_OVER_EM * 100
    if debit <= 0: continue
    best = max(hi - callK, putK - lo, 0.0) * 100
    settle = (max(0.0, cl - callK) + max(0.0, putK - cl)) * 100
    h = 0.50 * debit if best >= 1.5 * debit else settle - debit
    traded.append((d, bpnl, h))

n = len(traded)
print(f"gated: H would have traded {n} of {len(rows)} days ({n/len(rows):.0%}); skipped {skipped}")
if n < 5:
    print("too few days to say anything"); raise SystemExit
bs = [r[1] for r in traded]; hs = [r[2] for r in traded]
mb, mh = st.mean(bs), st.mean(hs)
corr = (sum((b-mb)*(h-mh) for _,b,h in traded)/n) / (st.pstdev(bs)*st.pstdev(hs))
print(f"  on those days:  B mean ${mb:+8.2f}   H mean ${mh:+7.2f} (1c)   corr {corr:+.3f}")
print(f"  H win rate {sum(1 for h in hs if h>0)/n:.0%}   H total ${sum(hs):+.2f}")
print()
print("  B's worst days AMONG the gated ones:")
for d,b,h in sorted(traded, key=lambda r: r[1])[:6]:
    print(f"    {d}  B ${b:9.2f}   H ${h:+7.2f}")
print()
for mult in (1, 3, 5, 10):
    comb = [b + h*mult for _,b,h in traded]
    print(f"  H x{mult:2d}c on gated days: mean ${st.mean(comb):+8.2f}  sd ${st.pstdev(comb):8.2f} "
          f"(B alone on same days ${st.pstdev(bs):.2f})  worst ${min(comb):+9.2f} (B ${min(bs):+.2f})")
