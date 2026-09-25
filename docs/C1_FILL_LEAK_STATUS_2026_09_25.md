# C1 — the entry fill leak: measured, and largely CLOSED

**The open question** (memory `b-curve-three-tracks-to-investigate`, and
`entry_fill_leak_rung_pricing_2026_09_10`): the entry fill leak was measured at
**~38% of B's net** — rungs crossing the spread by arithmetic accident. Rung
pricing was enabled on B on **2026-09-10**. *What did it actually recover?*

It was ranked the **largest** item on the backlog, ~4× anything else.

---

## Answer: it recovered the leak and reversed it

Measured directly on fills — `fill − mid_at_fill` per leg, signed so that
**positive = worse than mid**, ×100 ×contracts, summed over all four legs.
Full A2 era, 97 entries, split at the 2026-09-10 deployment:

| | n | mean/entry | median | total |
|---|---|---|---|---|
| **before** rung pricing | 65 | **+$27.96** | +$35.00 | +$1,817.50 |
| **after** rung pricing | 32 | **−$31.95** | −$52.50 | −$1,022.50 |

**Improvement: $59.91 per entry.** Before, B paid ~$28/entry over mid. Now it
*captures* ~$32/entry under it.

## And it did not cost fill rate — the thing that would have made it a mirage

A passive order that rests can lose the entry entirely, so capture means
nothing without this half:

| | before | after |
|---|---|---|
| avg attempts per entry | 1.06 | **1.03** |
| entries needing >1 attempt | 4 / 65 (6%) | **1 / 32 (3%)** |
| skipped entries per day | 6.65 | **4.67** |
| avg credit per entry | $277.62 | **$330.00** |

Every axis moved the right way. Escalation did not rise; skips did not rise.

## What the P&L says — and what it cannot say

| | n days | mean/day | median | avg VIX |
|---|---|---|---|---|
| before | 34 | $137.87 | $0.00 | 15.8 |
| after | 11 | **$277.07** | $53.30 | 16.0 |

**+$139.20/day**, with VIX effectively unchanged (15.8 vs 16.0), so this is not
a volatility-regime artefact.

⚠️ **But it is not statistically significant and must not be quoted as if it
were.** B's daily standard deviation is ~$2,046; over 11 days the standard
error is ~$617. A $139 difference is deep inside the noise. The honest
statement is that realised P&L is *consistent* with the fill evidence, not that
it confirms it.

The fill-level measurement is the load-bearing one: it is mechanical, measured
on the fills themselves, and does not depend on outcomes.

## Reconciling the two

The fill metric implies ~$213/day (3.6 entries/day × $59.91). Realised shows
+$139/day. The metric **overstates by ~35%**, which is expected: not every
fill advantage survives to realised P&L — a side that later stops out gives
part of it back, and `mid_at_fill` is a snapshot rather than a perfect
benchmark.

## Confounds, stated

Several things shipped around 2026-09-10/11/12. The credible alternative
explanations were checked:

* **L-M3 double-book guard** (09-10) — accounting only, cannot move fills.
* **MKT-011B estimation-failure fix** — deployed **09-19**, inside the "after"
  window, so it is a *second* possible contributor to the P&L difference (not
  to the fill-quality difference, which is mechanical).
* **Volatility regime** — ruled out: avg VIX 15.8 vs 16.0.
* **Sample size** — 32 entries / 11 days after. Small. The fill metric is far
  better powered than the P&L one.

## What this means for the backlog

**C1's premise — a large open entry leak — is largely closed.** The biggest
item on the list turns out to have been fixed on 2026-09-10; what was missing
was the measurement confirming it.

Remaining execution cost is now on the **exit** side, which is far smaller and
already addressed:

| | measured |
|---|---|
| entry fill (after rung pricing) | **−$32/entry — a capture, not a cost** |
| exit slippage | $43.10 mean/stop, **$1,077.50 total over 2 months** |

So the execution-quality workstream is in much better shape than the backlog
assumed, and the ranking should change: **C1 drops from "the prize" to
"verified done"**, and the case for C2 (independent income) and C3 (capital
efficiency) rises correspondingly.

## What would actually settle it

The fill evidence is strong enough to act on. The P&L claim needs ~40+ trading
days after 2026-09-10 before the difference clears the noise. That is calendar
time, not analysis — re-run this doc's queries then.
