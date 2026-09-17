# Dashboard variant audit — 2026-09-17

**Why this exists.** The dashboard was shown to investors and several strategies rendered empty or
missing key metrics. I had repeatedly reported "no bugs" — those statements were scoped to the
*trading* changes I had just deployed, and **I had never audited the dashboard at all**. I checked
`/api/health` and endpoints returning `401`, and never looked at what a non-live variant renders.
That is the gap this document closes.

**Method.** Called the real `GET /api/strategies/{id}/snapshot` handler for all 7 variants on the VM
and diffed the payloads, then traced each difference to its source.

---

## The good news: there IS a shared model

All 7 variants return an **identical top-level shape** and route through the same
`variant_readers.reader_for()`. The IC variants (A/B/C/F/G) all return the same 16 `body` keys; D/E
return the 4 calendar keys. So the answer to *"shouldn't they all reuse a standard model?"* is: they
do. The failures are **three specific defects inside that shared model**, not per-variant one-offs.

---

## BUG 1 — `total_trades` is permanently 0 for EVERY variant, including B

```
a: total_trades 0 · total_entries 352
b: total_trades 0 · total_entries  84
g: total_trades 0 · total_entries  26
```

**Root cause:** `bots/hydra/base_strategy.py:5899` initialises `"total_trades": 0` and **nothing
anywhere ever increments it.** The only other reference is a read in `shared/logger_service.py:2098`.

**Impact:** any displayed metric derived from trade count — win rate per trade, average P&L per
trade — is broken on **every** strategy, B included. `total_entries` is populated and is the correct
field to use.

---

## BUG 2 — G can never have return-on-capital or average-capital-per-day

```
variant_b  daily_returns: 73 rows   {date, net_pnl, capital_deployed, return_pct, contracts_per_entry}
variant_f  daily_returns:  2 rows
variant_g  daily_returns:  0 rows   <-- empty, despite 26 entries over 13 traded days
```

**Root cause:** `base_strategy._calculate_capital_deployed()` computes margin as

```python
if entry.spread_width <= 0:
    continue                                   # every G entry is skipped here
margin = entry.spread_width * 100 * entry.contracts
```

**G is a naked short strangle — `requires_protective_wings=False`, so it has no spread width.** Every
entry is skipped, `capital_deployed` is 0, and no `daily_returns` row is ever written. `return_pct`
and `capital_deployed` are exactly "return on capital" and "average capital per day".

**This is structural, not a typo.** The capital model assumes *defined* risk. G's risk is sized by
broker margin, as its own taxonomy entry says. Neither F nor G overrides
`_calculate_capital_deployed`.

**Fix shape:** an undefined-risk override that uses broker margin (`what_if_naked_margin` /
the margin snapshot) instead of spread width. Not a one-liner — it needs a real capital definition
for a naked position.

> ⚠️ **This also silently affects the Sortino ratio** (`base_strategy.py:6189`), which averages
> `return_pct` across `daily_returns`. With zero rows, G's risk-adjusted numbers are not merely
> missing — anything computed from them is meaningless.

---

## BUG 3 — the "previous day" endpoints are only half variant-scoped

This is why **only B shows yesterday**. The per-strategy snapshot has **no previous-day concept at
all** — for any variant, B included. Pre-market it returns the day's freshly-reset state, so
`entries`, `pnl_history`, `ohlc`, `spx_open/high/low`, `vix_open` are all legitimately empty for
everyone.

The main page looks right for B because it reads *different* endpoints — and those are inconsistently
scoped:

| endpoint | variant-scoped? |
|---|---|
| `GET /api/hydra/entries` | ✅ `strategy_id` param |
| `GET /api/metrics/daily` | ✅ `strategy_id` param |
| **`GET /api/hydra/summary`** | ❌ **no param — canonical/live-seat only** |
| **`GET /api/metrics/cumulative`** | ❌ **no param — canonical/live-seat only** |

So any card fed by `summary` or `cumulative` shows **the live seat's data regardless of which
strategy is picked** — which looks correct for B and wrong (or empty) for everyone else.

**This is the same class of bug as the 2026-07-14 cross-wiring fix** (`4b3d6a0`), which scoped
`/api/hydra/entries` and `/api/market/replay_pnl`. That fix was **partial** — `summary` and
`cumulative` were left canonical.

---

## Fix order (by user-visible impact per unit of risk)

1. **BUG 3** — scope `summary` + `cumulative` by `strategy_id`, mirroring the endpoints already
   fixed in July. Highest visible impact; the pattern already exists to copy. Dashboard-only.
2. **BUG 1** — stop using `total_trades`, or populate it. `total_entries` already holds the truth,
   so the low-risk fix is to derive from that.
3. **BUG 2** — G's capital model. Genuinely needs design (what *is* deployed capital for a naked
   strangle?), so it should not be rushed.

**All three are dashboard/metrics concerns. None affects trading.** The dashboard is a separate
service — restarting it has zero effect on the bots, and none of this touches the live seat's
clean-session streak.
