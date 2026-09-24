"""Did the live seat's stopped sides actually finish in the money?

A stop pays for itself only when the move that triggered it KEPT GOING. This
prices every `stop_loss` side against that day's settlement close, capped at the
spread width, and compares it to what the stop actually cost.

Measured 2026-09-24 on variant B: 15 of 21 stops fired on sides that finished
CHEAPER than the stop — net -$12,381 from stopping versus holding. B's bad days
are stop days, not big-move days (it wins 14 of 21 large-range sessions), so the
stop is the dominant term in its loss side.

⚠️ READ THE CAVEATS BEFORE ACTING. n is small (only stops with matching entry
rows). "Hold to expiry" is a COUNTERFACTUAL, not a free alternative: 0DTE gamma
explodes in the final 60-90 minutes and holding a narrow spread near its strikes
into the close carries real risk this model does not price. The honest reading
is "the stop is mis-calibrated", NOT "remove the stop".

Findings + what to do about it: docs/WHAT_WOULD_SOFTEN_B_2026_09_24.md

    python -m scripts.analyze_stop_counterfactual
"""
import sqlite3
c = sqlite3.connect('file:/opt/calypso/data/variant_b/backtesting.db?mode=ro', uri=True)
rows = c.execute("""
  SELECT s.date, s.entry_number, s.side, s.net_pnl,
         e.short_call_strike, e.short_put_strike, e.long_call_strike, e.long_put_strike,
         e.contracts, d.spx_close
  FROM trade_stops s
  JOIN trade_entries e ON e.date=s.date AND e.entry_number=s.entry_number
  JOIN daily_summaries d ON d.date=s.date
  WHERE s.exit_reason='stop_loss' AND d.spx_close>0
""").fetchall()
saved = wasted = 0
saved_amt = wasted_amt = 0.0
for d, en, side, pnl, sc, sp, lc, lp, n, close in rows:
    n = n or 7
    if side == 'call':
        if not sc: continue
        width = abs((lc or sc+5) - sc)
        intrinsic = max(0.0, close - sc)
    else:
        if not sp: continue
        width = abs(sp - (lp or sp-5))
        intrinsic = max(0.0, sp - close)
    # What the side would have cost at expiry, capped at the spread width.
    settle_cost = min(intrinsic, width) * 100 * n
    stop_cost = -pnl if pnl < 0 else 0.0
    if settle_cost > stop_cost:
        saved += 1; saved_amt += settle_cost - stop_cost
    else:
        wasted += 1; wasted_amt += stop_cost - settle_cost
print(f"stop-loss sides analysed: {len(rows)}")
print(f"  stop SAVED money : {saved:3}  (total ${saved_amt:,.0f} avoided)")
print(f"  stop COST money  : {wasted:3}  (total ${wasted_amt:,.0f} given up — the side "
      f"would have finished cheaper than the stop)")
print(f"  NET effect of stopping vs holding to expiry: ${saved_amt - wasted_amt:+,.0f}")
