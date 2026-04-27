# HYDRA Stop Buffer Optimization — Research & Decision Log

**Decision date:** 2026-04-27
**Decided by:** diogodiasgrilo
**Status:** Option B selected — per-VIX-regime hybrid (Zone 1 full, Zone 2 mid)
**Next review:** 2026-05-25 (4 weeks of live data)

---

## TL;DR

After a deep study of the call/put stop buffers and the 2.5×/4-hour buffer-decay shape, we are populating the **already-existing** `vix_regime.call_stop_buffer` and `vix_regime.put_stop_buffer` arrays in `bots/hydra/config/config.json` with per-zone values:

| VIX Zone | Range | call_stop_buffer | put_stop_buffer | Rationale |
|---|---|---|---|---|
| 0 | <18 | `null` (= $0.75 fallback) | `null` (= $1.75 fallback) | Only 5 entries / 2 days in study — insufficient data |
| **1** | **18–22** | **$1.50** | **$2.50** | Wider both — calm regime, most "stops" are noise |
| **2** | **22–28** | **$1.00** | **$1.50** | Wider call, **tighter** put — stress regime, stops fire on real moves |
| 3 | ≥28 | `null` (= $0.75 fallback) | `null` (= $1.75 fallback) | Zero entries in study — no data |

**Decay parameters (`buffer_decay_start_mult=2.5`, `buffer_decay_hours=4.0`) are NOT being changed.** Insufficient data — only 8 trading days since MKT-042 deployed (Apr 13).

**Expected improvement:** ~$33K/year at 1 contract on 28-day historical sample.
**Risk profile:** Medium. Zone 1 takes the full data-implied step; Zone 2 hedges back from the optimum to retain stress cushion.

---

## Why this study mattered

Pre-study live performance was below backtest expectations. Last week (Apr 20–24) saw 5 stops with median slippage $1.65–$2.75 per spread above trigger. The hypothesis was that the buffer parameters — last optimized using ThetaData backtests with a known ~34% calibration gap to live Saxo execution — were suboptimal under real fill dynamics.

The 6 parameters in question:

| Side | Parameter | Pre-study value |
|---|---|---|
| Call | `call_stop_buffer` | $0.75 (floor at end of decay) |
| Call | `buffer_decay_start_mult` | 2.5× (effective $1.875 at entry) |
| Call | `buffer_decay_hours` | 4.0h (decay to 1× over 4 hours) |
| Put | `put_stop_buffer` | $1.75 (floor at end of decay) |
| Put | `buffer_decay_start_mult` | 2.5× (shared with call — engine supports asymmetric) |
| Put | `buffer_decay_hours` | 4.0h (shared with call) |

---

## Methodology — Saxo-only data, 28-day window

### Constraint

ThetaData backtests have a known ~34% calibration gap to Saxo execution prices. Optimizing on miscalibrated data gives wrong answers. So this study used **only data from the live VM SQLite DB** (`/opt/calypso/data/backtesting.db`):

- `trade_entries` — strikes, credits, vix_at_entry per entry
- `trade_stops` — actual fill prices, slippage, hold time per stop
- `spread_snapshots` — ~7-second density of spread mid values during entry life
- `entry_mae_mfe` — peak adverse/favorable excursion per entry

### Date range

**2026-03-16 → 2026-04-24 = 28 trading days, 74 entries, 38 stops.**

This is the overlap where ALL needed tables had data. Earlier dates lacked spread_snapshots. The window includes Mar 16-Apr 12 (pre-MKT-042 buffer-decay) and Apr 13-Apr 24 (post-MKT-042) — base buffer optimization is valid across the full window; decay shape can only be informed by the post-Apr-13 subset.

### Counterfactual replay

For each candidate `(call_stop_buffer, put_stop_buffer)` config:
1. For each historical entry, walk the spread_snapshot stream chronologically.
2. At each timestamp, compute the dynamic stop level: `credit + buffer × decay_factor` where `decay_factor = 1 + (start_mult - 1) × max(0, 1 - elapsed_h / decay_hours)`.
3. First time spread_mid crosses the stop level → record stop fire.
4. Estimate fill cost = first-crossing-spread-mid + median observed slippage (call: +$60, put: +$40 from the live data).
5. Aggregate net P&L across all entries → config-level total.

**Limitations of replay:**
- Only counterfactuals existing entries with their existing strikes. Cannot predict new strike selection at different buffers (buffers don't drive strike selection in HYDRA, so this is OK).
- Slippage modeled as a constant additive — actual slippage varies by overshoot magnitude. Conservative for tight buffers, may understate cost at wide buffers.
- Engine doesn't model MKT-046's 10-second confirmation in the replay. Stop fires at the first crossing snapshot. Real-life stops fire ~10s later at a higher price; the replay's per-stop fill is therefore optimistic by ~$30-50.

---

## Phase 1 — Descriptive findings

### Stop characteristics (28-day window)

| Metric | Calls (n=23) | Puts (n=15) |
|---|---|---|
| Hold time median | 108 min | 98 min |
| Hold time max | 262 min (4.4h) | 322 min (5.4h) |
| Slippage median | $60 | $40 |
| Slippage mean | $91 | $90 |
| Slippage max | $305 | $235 |
| Slippage ratio median | 1.16× | 1.09× |
| Stops in first 60 min | 35% | 40% |
| Stops after 180 min | 22% | 20% |

**Surprises:**
1. **Calls fire 53% more often than puts** — the asymmetric buffer ($0.75 vs $1.75) was over-protecting puts and under-protecting calls.
2. **Calls have higher slippage despite the smaller buffer** — when calls overshoot, they overshoot violently.
3. **20-22% of stops fire after hour 3** — past the buffer-decay window. Late stops fire at the floor buffer with no widening protection.

### Spread dynamics by elapsed-hour from entry

| Elapsed h | Call median %trigger | Call std | Put median %trigger | Put std |
|---|---|---|---|---|
| 0–1 | 16% | 13.6 | 20% | 13.5 |
| 1–2 | 9% | 13.6 | 11% | 13.4 |
| 2–3 | 6% | 12.5 | 8% | **15.3** |
| 3–4 | 4% | 7.1 | 4% | 12.4 |
| 4–5 | 2% | 9.1 | 1% | **24.6** ← spike |
| 5–6 | 1% | 2.6 | 1% | **19.0** |

**Key finding:** Put-side volatility spikes in elapsed hours 4-5+ — well after current 4h decay-to-1× completes. **Theoretical justification for asymmetric decay (calls ~3-4h, puts ~5-6h)** but insufficient data to act yet.

---

## Phase 2 — 2D sweep (`call_buffer × put_buffer`, decay shape held fixed)

```
Net P&L over 28 days (decay 2.5× over 4h, fixed at current):

  CB / PB |   100 |   125 |   150 |   175 |   200 |   250 |   300
       25 | +9476 | +9953 | +10234 | +11614 | +11544 | +12315 | +12135
       50 | +11068 | +11545 | +11826 | +13206 | +13136 | +13907 | +13727
       75 | +10841 | +11318 | +11599 | +12979 ← curr | +12909 | +13680 | +13500
      100 | +11893 | +12370 | +12651 | +14031 | +13961 | +14732 | +14552
      125 | +12340 | +12817 | +13098 | +14478 | +14408 | +15179 | +14999
      150 | +12790 | +13267 | +13548 | +14928 | +14858 | +16373 | +16193
```

Full-window optimum: **CB=$1.50, PB=$2.50 → +$3,394 vs current.** Looked like a 26% P&L improvement.

**This optimum was misleading** — it disappeared in the robustness check.

---

## Phase 3 — Robustness check (split 28 days in halves)

| Window | Best (CB, PB) | Δ vs current |
|---|---|---|
| Full (24 trading days) | (150, 250) | +$3,394 |
| **Half 1** (Mar 16–Apr 3, 9 days) | **(100, 100)** ← *put TIGHTER* | +$973 |
| **Half 2** (Apr 6–Apr 24, 15 days) | **(150, 250)** ← *put WIDER* | +$2,890 |

**Top-5 overlap between halves: 0 of 5.** Zero configs in both halves' top tier.

The two halves wanted **opposite** put-buffer directions. Half 1 (which had higher VIX averages) wanted tighter puts; Half 2 (lower VIX) wanted wider puts. This signaled that the optimum is **regime-dependent**, not a single global value.

---

## Phase 4 — Per-VIX-regime sweep (the decisive analysis)

The strategy code at [`bots/hydra/strategy.py:8278-8287`](../bots/hydra/strategy.py#L8278-L8287) already supports per-VIX-regime buffer overrides via the `vix_regime.call_stop_buffer` and `vix_regime.put_stop_buffer` arrays in config.json. They were always `null` (using the global default). This study populates them.

### Zone 1 (VIX 18–22, n=28 entries, 14 stops)

```
  CB / PB |   125 |   150 |   175 |   200 |   250
       50 | +2198 | +2705 | +4200 | +4200 | +5029
       75 | +2081 | +2588 | +4083 ← curr | +4083 | +4912
      100 | +2023 | +2530 | +4025 | +4025 | +4854
      125 | +2470 | +2977 | +4472 | +4472 | +5301
      150 | +2920 | +3427 | +4922 | +4922 | +6495 ← Z1 optimum
```

**Zone 1 optimum: CB=$1.50, PB=$2.50 → +$2,412 vs current.**

**Critical mechanic:** The put-buffer benefit in Zone 1 is **binary**. PB $1.75 → $2.00 produces $0 change (same row CB=125: $4,472 = $4,472). The improvement only unlocks at PB ≥ $2.50. Either commit to $2.50 or stay at $1.75.

### Zone 2 (VIX 22–28, n=41 entries, 22 stops)

```
  CB / PB |   125 |   150 |   175 |   200 |   250
       50 | +8689 | +8463 | +8348 | +8278 | +8220
       75 | +8635 | +8409 | +8294 ← curr | +8224 | +8166
      100 | +9267 ← Z2 max | +9041 | +8926 | +8856 | +8798
      125 | +9267 | +9041 | +8926 | +8856 | +8798
      150 | +9267 | +9041 | +8926 | +8856 | +8798
```

**Zone 2 optimum: CB=$1.00, PB=$1.25 → +$973 vs current.** Note that the call buffer **saturates at $1.00** — wider doesn't help because the call stops that fire are unavoidable.

**Critical mechanic:** PB direction in Zone 2 is **opposite to Zone 1**. Tighter PUT buffer (PB=$1.25 vs current $1.75) is best because in stress regimes, stops fire on real directional moves; the wider buffer just means worse fill prices, not avoided stops.

---

## Phase 5 — Decision (Option B)

Three tiers were considered after the per-zone analysis:

| Tier | Zone 1 | Zone 2 | 28-day Δ | Annualized at 1c | Distance from current |
|---|---|---|---|---|---|
| A. Call-only per-zone | CB=$1.25, PB=$1.75 | CB=$1.00, PB=$1.75 | +$1,021 | ~$11K/yr | Tiny |
| **B. Hybrid (CHOSEN)** | **CB=$1.50, PB=$2.50** | **CB=$1.00, PB=$1.50** | **+$3,159** | **~$33K/yr** | Z1 big, Z2 small |
| C. Aggressive | CB=$1.50, PB=$2.50 | CB=$1.00, PB=$1.25 | +$3,385 | ~$36K/yr | Both zones big |

### Why Option B over Option C

Option C only adds **+$226 over 28 days vs Option B** (~$2.5K/yr) by tightening Zone 2 put buffer from $1.50 → $1.25. This is the highest-risk change in the entire study because:

1. In a stress event we haven't seen (VIX 28+, large overnight gap), put buffer of $1.25 means a put stop fires almost immediately when the spread crosses trigger, with very little cushion above credit.
2. Zone 3 (VIX ≥ 28) has zero data in our window — Zone 2 at high-VIX boundary acts as a proxy, and we don't want to be aggressive at a boundary we have no signal at.
3. The marginal $2.5K/yr is the smallest, most fragile slice of the total improvement.

Option B captures **~93% of Option C's gain at materially less regime-fragility.**

### Why Option B over Option A

Option A leaves both put buffers at current $1.75. This forfeits the Zone 1 binary jump ($1.75 → $2.50) which is worth +$2,023/yr alone in the 28-day data and is well-supported (Zone 1 has 28 entries, the largest single-zone sample).

---

## Forward-looking criteria — when to step up, regress, or hold

### Re-evaluation cadence: every 4 weeks

Re-run `scripts/buffer_study.py` and `scripts/buffer_per_vix_regime.py` against the updated DB. The 28-day window will become 56-day, then 84-day, etc. — sample size compounds quickly.

### When to step up to Option C (CB=$1.00, PB=$1.25 in Zone 2)

**Required conditions, ALL of:**

1. ≥ 8 weeks of live data under Option B (~40+ trading days).
2. Combined re-analysis still shows Zone 2 best at PB ≤ $1.50 (i.e. tighter puts continue to dominate in stress).
3. **No** Zone 3 days (VIX ≥ 28) have shown unusual stop behavior under Option B (specifically: a put stop with overshoot > 3× trigger).
4. Zone 1 sample has grown to ≥ 50 entries with consistent PB=$2.50 dominance.
5. Total P&L under Option B is positive over the 8-week window (must be earning, not just holding).

### When to regress to Option A (call-only widening)

**ANY of these triggers:**

1. Zone 1 PB=$2.50 produces a single-day loss > $1,500 (per contract). The data set has no losses of that magnitude — if one appears, the wider put buffer is the likely cause (spread blew through wider trigger at a much worse price).
2. Zone 2 PB=$1.50 produces a put stop with **negative net P&L absolute value > $600** (per contract) — indicates the tighter put buffer fired at a worse price than current would have.
3. Cumulative live P&L over 4 weeks under Option B is **worse than projected by > $2,000 per contract**. Suggests the buffer change is mis-tuned for current regime.

### When to regress to current (CB=$0.75, PB=$1.75 globally)

**ANY of these triggers:**

1. Two consecutive 4-week windows show negative cumulative P&L delta vs Option B.
2. A Zone 3 stress event reveals that the Zone 2 buffer ($1.50 put) is too tight and produces extreme losses.
3. Saxo execution dynamics change materially (e.g. wider bid-ask spreads on 0DTE due to liquidity changes) — would need a fresh slippage study before the buffer values are valid.

### Metrics to monitor weekly

Add to existing CLIO/HOMER weekly reports:

| Metric | What it measures | Concerning level |
|---|---|---|
| Stop count by VIX zone | Are zones firing stops at expected rates? | Zone 1 stop rate > 50% of entries OR Zone 2 stop rate > 65% |
| Median slippage by zone-side | Are stops filling at expected prices? | Zone 1 call slippage > $100 OR Zone 2 put slippage > $120 |
| Worst single-stop debit by zone | Is any one stop blowing up? | Any stop > $700 debit per contract in any zone |
| P&L delta vs current-buffer counterfactual | What if we'd kept current? | Δ < -$500/week for 2+ consecutive weeks |
| Zone 0 / Zone 3 entry counts | Building up data for future expansion | Zone 0 ≥ 30 entries → consider populating Zone 0 buffers |

---

## Decay parameters — DO NOT CHANGE (yet)

The 4-hour decay window and 2.5× start multiplier are **not being changed** despite some descriptive evidence (Phase 1) suggesting puts may need longer decay. Reasons:

1. Only **8 trading days** of MKT-042 era data (Apr 13 → Apr 24). Per-VIX-regime decay sweeps would have ≤4 days per zone — pure noise.
2. The Phase 1 "put std spikes in hour 4-5" pattern is from 4 days of data and could be an artifact of one or two unusual days.
3. The `buffer_decay_start_mult` and `buffer_decay_hours` are global — engine supports per-side via separate fields but config doesn't currently use them. Adding asymmetric decay would be a code change.

**When to revisit decay:** After **8+ weeks of MKT-042 data** (~50 trading days). Run a focused decay-shape study at that point. Specifically test:
- `put_buffer_decay_hours` 4 → 5 → 6
- `call_buffer_decay_hours` 4 → 3 (calls quiet down faster per Phase 1)
- `buffer_decay_start_mult` 2.5 → 2.0 → 3.0

---

## Implementation

### Config change (Option B)

In `bots/hydra/config/config.json`, change the `vix_regime` block from:

```json
"vix_regime": {
  ...
  "call_stop_buffer": [null, null, null, null],
  "put_stop_buffer":  [null, null, null, null],
  ...
}
```

to:

```json
"vix_regime": {
  ...
  "call_stop_buffer": [null, 1.50, 1.00, null],
  "put_stop_buffer":  [null, 2.50, 1.50, null],
  ...
}
```

Plus a corresponding update in `backtest/config.py:live_config()` so the backtest engine stays synced with VM config (otherwise future analysis runs will compare against stale baseline).

### Validation plan

1. Apply change pre-market on a Mon/Tue (no fresh entries pending overnight).
2. Restart `hydra.service` to load new config.
3. First trading day: verify startup banner logs "VIX regime: call_stop_buffer..." override messages on entry placement.
4. Day-1 monitor: confirm stops in Zone 1 (if any fire) trigger at the wider levels and stops in Zone 2 trigger at the new tighter put level.
5. Telegram `/stops` should show stop levels reflecting the new config.

### Rollback procedure

Single config edit. Revert all four array values to `null` and restart service — returns to global defaults. Total rollback time < 2 min including VM SSH + restart.

---

## Reference: scripts used

- [`scripts/buffer_study.py`](../scripts/buffer_study.py) — Phase 1 + 2 + 3 main analysis (descriptive + 2D sweep + decay)
- [`scripts/buffer_robustness.py`](../scripts/buffer_robustness.py) — Phase 4 half-by-half stability check
- [`scripts/buffer_per_vix_regime.py`](../scripts/buffer_per_vix_regime.py) — Phase 5 per-zone sweep (the decisive analysis)

All three are reproducible — re-run any of them against the current VM DB to extend the analysis with new data.

---

## Glossary

- **VIX regime / zone:** Classification of market volatility based on VIX-at-entry. Breakpoints `[18, 22, 28]` define 4 zones. Zone 0: VIX<18 (calm). Zone 1: 18-22 (normal). Zone 2: 22-28 (stressed). Zone 3: VIX≥28 (high stress).
- **Stop buffer (call/put):** Dollar amount added to credit to compute the stop level. Stop fires when spread mid ≥ credit + buffer × decay_factor.
- **Buffer decay (MKT-042):** Time-decaying stop buffer. Starts at 2.5× the floor buffer at entry, decays linearly to 1× over 4 hours. Wider stops early (when premium rich and noisy), normal stops later (after theta decay).
- **Slippage:** Difference between trigger level and actual fill price. Caused by 10s MKT-046 confirmation wait + market-order spread + bid/ask widening during stress.
- **Counterfactual replay:** Walking historical spread_snapshot streams with alternative buffer parameters to simulate what would have happened.

---

## Decision sign-off

**Author:** Diogo Dias Grilo
**Date:** 2026-04-27
**Pre-deploy validation:** Pending (config diff to be applied next)
**Live deploy date:** TBD (recommended pre-market Mon/Tue)
**First review:** 2026-05-25 (4 weeks of live data)

---

# Addendum (2026-04-27, post-deploy) — Adjacent investigations

After Option B was deployed, two adjacent questions were investigated:
1. Should `max_spread_width` (currently 110pt) be narrowed?
2. Should the EMA 20/40 trend signal be activated to drive entry-type selection?

Both came back negative for action, but produced the most important strategic
finding of the session — captured below for future reference.

## Spread-width study — DO NOT CHANGE

**Initial sweep ([scripts/sweep_max_spread_width.py](../scripts/sweep_max_spread_width.py))** over Dec 1 – Apr 10 (90 days, ThetaData backtest engine) suggested narrowing from 110pt to 50pt would improve Sharpe from 0.76 → 1.01 (+33%) and unlock 2c×3 margin viability ($29K vs $52K available).

**Robustness check ([scripts/spread_width_robustness.py](../scripts/spread_width_robustness.py))** killed it. Splitting the 90-day window in halves revealed:

| Window | Best width by Sharpe | Stop rate | 50pt EV vs 110pt |
|---|---|---|---|
| Half 1 (Dec 1 – Feb 4) | **150pt** (0.55) | 33% (stress) | −$6.6/entry |
| Half 2 (Feb 5 – Apr 10) | **50pt** (2.72) | 18.8% (calm) | +$8.7/entry |
| Full | 50pt by Sharpe, 150pt by EV | 24% | +$2.4/entry |

**Top-3 overlap by Sharpe between halves: 0 of 3.** Identical regime fragility we saw on the put buffer, but with NO clean per-VIX intersection.

**Per-VIX-regime spread analysis ([scripts/spread_width_per_vix_regime.py](../scripts/spread_width_per_vix_regime.py))** — bucket entries by VIX zone, find optimal width per zone:

| Zone | VIX | Entries | Best width by EV | EV at best | Profitable? |
|---|---|---|---|---|---|
| **Z0** | <18 | 75 | **110pt** ($67.3/entry) | $5,050 | 🟢 highly + |
| Z1 | 18-22 | 55 | 75pt (−$24.3) | −$1,335 | ❌ negative everywhere |
| Z2 | 22-28 | 55 | 50pt (−$6.6) | −$365 | ❌ negative everywhere |
| Z3 | ≥28 | 6 | 150pt | +$50 | inconclusive (n too small) |

**Best widths disagree across all 4 zones** (110/75/50/150). No common winner.

### What killed the spread-width recommendation

1. **Z0 (calm regime) generates ALL of the strategy's profitability** ($5,050 of $5,160 total at best widths). Z1/Z2 are net losers at every width tested.
2. **No spread width fixes Z1/Z2 unprofitability.** Best-case in Z2 is still negative (−$6.6/entry).
3. **Z0 doesn't really care about width** — every width is hugely profitable ($59-67/entry) in calm conditions. Picking the optimum saves only ~$200-300/yr at 1c.
4. **Per-VIX-regime widths would require engine code change** (`max_spread_width` is single-valued, would need an array). Engineering cost dwarfs benefit given Z0-only gains.
5. **2c via spread narrowing is structurally unsafe.** At 50pt × 2c in a stress regime (Half 1), EV becomes $11.6/entry — same as current 1c at 110pt with double the position size and double the per-trade tail risk.

### The bigger insight from spread-width work

The strategy's edge is **concentrated in low-VIX regimes (Z0)**, with Z1/Z2 essentially break-even-or-worse and Z3 unmeasurable. The right investigation isn't "what spread width should I use?" but **"should I be trading Z1/Z2 at all?"**

Concrete next-investigation candidates (NOT for today, but documented for future):
1. Reduce `vix_regime.max_entries` in Z1/Z2 — currently `[2, 2, 2, 1]`, try `[2, 1, 1, 0]` or `[2, 2, 1, 0]`
2. Raise `vix_regime.min_call_credit` / `min_put_credit` floors in Z1/Z2 (be more selective)
3. Use EMA signal specifically as a Z1/Z2 filter (it might gate stress days even if it doesn't add directional info in calm Z0)
4. New `vix_regime.skip_entry_pct` field — probability of skipping each entry per zone

## EMA 20/40 trend signal study — leave disabled

**Backtest ([scripts/ema_trend_backtest.py](../scripts/ema_trend_backtest.py))** over the same Saxo Mar 16 – Apr 24 window as the buffer study. For each candidate threshold {0.10%, 0.15%, 0.20%, 0.30%, 0.40%, 0.50%}, classify entries by EMA20-vs-EMA40 divergence and replay full_ic → put-only (BULLISH) or call-only (BEARISH) counterfactuals.

**Result: EMA produces essentially zero actionable signal in this window.**

| Threshold | Bullish entries | Bearish entries | Δ vs baseline |
|---|---|---|---|
| 0.10% | 3 | 3 | +$32 (within noise) |
| 0.15% | 1 | 0 | −$140 |
| 0.20% (live) | 1 | 0 | −$140 |
| 0.30%+ | 0 | 0 | $0 (signal never fires) |

EMA divergence distribution: median absolute = 0.030%, p90 = 0.087%, max = 0.244%. The signal is too tame to fire at any reasonable threshold.

### Why this differs from Feb 2026 (Appendix C in HYDRA_TRADING_JOURNAL.md)

Feb data showed +$850 impact at 0.20% threshold across 6 directional entries. Mar 16 – Apr 24 has similar |divergence| magnitudes BUT calmer days where neither side was in real danger — entries that "would have flipped" expired clean either way. The signal might still have value in stress regimes; we just don't have a stress sample in this window.

### When to revisit EMA

Re-run [scripts/ema_trend_backtest.py](../scripts/ema_trend_backtest.py) at the next 4-week review (2026-05-25). If by then we've had at least 5+ trading days with VIX > 22 and EMA |divergence| > 0.20%, the regime sample is meaningful enough to test directional flipping. Until then, leave EMA `informational only` as today.

## Updated forward-looking priority list

Reordered based on today's findings:

| Rank | Action | Status | Expected timeframe |
|---|---|---|---|
| 1 | Watch Option B buffer change live | Active monitoring | 4 weeks (until 2026-05-25) |
| 2 | Investigate Z1/Z2 unprofitability (entry-count caps, credit floors) | Not started | After 4-week buffer review |
| 3 | Re-test EMA at next review with fresh data | Scheduled | 2026-05-25 |
| 4 | Re-evaluate buffer per-VIX-regime with combined data | Scheduled | 2026-05-25 |
| 5 | Decay parameter study (need 8 weeks MKT-042 data) | Scheduled | ~late June 2026 |
| **DROPPED** | ~~Narrow `max_spread_width`~~ | Killed by robustness check | — |
| **DROPPED** | ~~Per-VIX-regime spread widths~~ | Killed by per-zone analysis (engine cost > Z0-only benefit) | — |
| **DROPPED** | ~~Activate EMA-driven entry-type flipping~~ | No signal in current regime | revisit 2026-05-25 |

## Pattern recognition — what the session as a whole revealed

Across the parameter studies done 2026-04-27, three of four had the same regime-fragility pattern (Half 1 vs Half 2 disagreement on direction):
- **Buffer:** regime-fragile, but the engine had per-VIX-zone wiring → shipped Option B
- **Base-entry directional conversion (tight thresholds):** regime-fragile AND no engine support for per-zone → killed
- **Spread width:** regime-fragile AND per-zone analysis revealed unprofitable middle zones → killed
- **EMA trend signal:** no signal in current regime, period → deferred

**Key lesson:** parameter optimization on a single 90-day window is mostly a regime-fitting mirage. Real edge comes from **regime-adaptive features** (which the buffer Option B is, but few others are). The most actionable structural improvement is investigating *whether to engage Z1/Z2 at all*, not how to optimize within them.

---

# Addendum #2 (2026-04-27, later same day) — Spread-width revisited via Saxo counterfactual

After Addendum #1 documented the per-VIX-zone backtest finding that 50pt was meaningfully better in Z2 (-$6.6 vs -$21.5/entry), a Saxo-grounded counterfactual replay was run to validate. **The result substantially weakens the spread-narrowing P&L case** — the backtest's claimed Z2 advantage is mostly a model artifact, not a real-market phenomenon.

## Methodology — anchor on actual fill, not modeled mid

For each of last week's 6 stops (Apr 17–24), compute the counterfactual fill at 75pt and 50pt long strikes. The fix vs an earlier (buggy) attempt:

```
WRONG: counter_fill = BS(short) - BS(long_50pt) + slippage
       (introduces BS model error which is huge for puts due to no skew)

RIGHT: actual_mid = actual_debit - slippage              # anchor in reality
       delta_long = BS(long_50pt) - BS(long_actual)      # BS only for delta
       counter_mid = actual_mid - delta_long
       counter_fill = counter_mid + slippage
```

The first approach makes BS model error look like real savings. The corrected approach uses BS only for the *difference* between two long strikes (where systematic errors largely cancel).

## Corrected results

| Date | # | Side | SPX | Trig | Actual | @75pt | @50pt | Δ@75 | Δ@50 |
|---|---|---|---|---|---|---|---|---|---|
| 2026-04-17 | 1 | call | 7137 | $320 | $445 | $444 | $436 | +$1 | +$9 |
| 2026-04-21 | 1 | put | 7069 | $305 | $475 | $475 | $474 | +$0 | +$1 |
| 2026-04-23 | 1 | put | 7102 | $300 | $535 | $535 | $535 | +$0 | +$0 |
| 2026-04-23 | 2 | put | 7111 | $290 | $460 | $460 | $459 | +$0 | +$1 |
| 2026-04-24 | 1 | call | 7159 | $195 | $360 | $360 | $355 | +$0 | +$5 |
| 2026-04-24 | 2 | call | 7159 | $210 | $485 | $485 | $477 | +$0 | +$8 |
| **TOTAL** | | | | | **$2,760** | **$2,759** | **$2,735** | **+$1** | **+$25** |

**Total savings going from 110pt → 50pt across all 6 stops last week: ~$25.**

## Why narrower didn't help last week

All 6 stops fired with the SHORT still OTM (or barely ITM). At fill time:
- Apr 17 call: short 7160, SPX 7137 → short still 23pt OTM
- Apr 21 put: short 7050, SPX 7069 → short 19pt OTM
- Apr 23 puts: shorts 7070/7085, SPX 7102/7111 → shorts 26-32pt OTM
- Apr 24 calls: shorts 7180/7185, SPX 7159 → shorts 21-26pt OTM

The actual long legs were 130+pt OTM at fill time. The hypothetical 50pt long would still be 50-80pt OTM at fill time. **Both deep OTM options had near-zero value differences.**

The narrower-protects-more mechanic requires the SHORT to go meaningfully ITM (so its value is dominated by intrinsic, and the long approaches ATM). Last week's stops were all "barely tagged" stops where width didn't matter.

## Reconciling with the backtest's per-VIX-zone result

The backtest claimed Z2 50pt → $815 better than 110pt over 90 days (~$37/stop). Saxo replay says ~$4/stop. **9× discrepancy.** Most likely explanation: ThetaData backtest doesn't model real spread-mid dynamics during stop events realistically. Real market behavior preserves more long-leg-equivalent OTM-ness during typical stops than the backtest models.

The truth is probably **$10-30/stop average savings** going 110pt → 50pt, weighted across mild and catastrophic stops. With ~18-20 stops/year, **annualized P&L benefit at 1c is ~$200-600/yr**, not the $1,236/yr Addendum #1 implied.

## What this means — narrowing's value is NOT P&L

| Reason to narrow | Magnitude | Real or artifact? |
|---|---|---|
| ~~Improve P&L per stopped trade~~ | ~~$15-37/stop~~ | **~~Mostly artifact~~** — Saxo says $0-25 |
| **Sharpe improvement** | **+22-33%** (backtest) | Real — variance reduction, not return |
| **Tail-risk cap reduction** | $11K → $5K max loss/IC | Real — pure structural |
| **Margin freed up** | ~$6K/IC, $18K total at 1c×3 | Real — pure margin math |
| **Optionality for 2c scaling** | Makes 2c×3 viable in $52K | Real — pure margin math |

**The case for narrowing is built on Sharpe + tail risk + scalability — not on per-trade P&L improvement.** If Addendum #1's "spread-narrow saves money in stress" argument was the load-bearing reason, the corrected analysis weakens it significantly. But the Sharpe and tail-risk and margin arguments still stand independently.

## Refined sequencing recommendation

**Today: hold.** Buffer Option B just deployed. Single-change-at-a-time gives clean attribution at the 4-week review.

**At 2026-05-25 (4-week review):**
- Validate buffer Option B held up
- Narrow `max_spread_width` 110 → **75pt** as the next single change (half-step toward Tammy's spec)
- Reasons: Sharpe improvement, tail-risk halving, margin headroom — not P&L

**At 2026-06-22 (8-week review):**
- Validate 75pt held up
- Either step to 50pt (Tammy spec, full margin freedom) OR flip to 2c at 75pt if capital math supports

**At any time:** if account capital changes materially (e.g. deposit), 2c becomes possible at any width without parameter changes.

## Updated decision flag — spread width is no longer "DROPPED"

Reverting the priority list update from Addendum #1:

| Rank | Action | Status | Expected timeframe |
|---|---|---|---|
| 1 | Watch Option B buffer change live | Active monitoring | until 2026-05-25 |
| 2 | **Narrow `max_spread_width` 110 → 75pt** | **Scheduled** | 2026-05-25 (after buffer validates) |
| 3 | Investigate Z1/Z2 unprofitability | Not started | After 2026-05-25 |
| 4 | Re-test EMA at next review with fresh data | Scheduled | 2026-05-25 |
| 5 | Re-evaluate buffer per-VIX-regime with combined data | Scheduled | 2026-05-25 |
| 6 | Final spread step to 50pt OR 2c flip | Scheduled | 2026-06-22 |
| 7 | Decay parameter study | Scheduled | ~late June 2026 |

**Spread width is back on the roadmap, just deferred 4 weeks for clean buffer attribution.**

## Reproducibility

[`scripts/spread_width_counterfactual.py`](../scripts/spread_width_counterfactual.py) — the corrected Saxo-grounded counterfactual. Re-run against any date range to test alternative widths. Use as the validation step before any future spread-width change.

---

## Decision sign-off (updated)

**Author:** Diogo Dias Grilo
**Decision date:** 2026-04-27
**Live deploys today:** Buffer Option B (per main doc above)
**Spread-width status:** **DEFERRED to 2026-05-25** — narrow to 75pt at next review pending buffer validation
**First review:** 2026-05-25 (4 weeks of live data)

---

# Addendum #3 (2026-04-27 EOD) — Why we can't naively narrow `max_spread_width`

After deploying Path-B dry-run + Option B buffers earlier in the day, the
operator flipped `max_spread_width` from 110 → 50 (Tammy's spec) for a
same-day dry-run experiment. **E#1 placed at 10:45:13 with strikes at
177pt OTM call / 198pt OTM put and net credit of $5 per contract** (vs.
HYDRA's typical ~$1.20-$1.50/contract at 110pt). Within 9 seconds, the
state-reconciliation cascade marked the entry as "closed externally" and
the dry session was effectively dead before it began.

The reconciliation cascade is a separate Path-B bug fixed in commit
[fixes-coming-next] — but the root strategy issue around narrow widths is
a config-interaction footgun that needs explicit documentation here.

## Why narrowing alone breaks strike selection

**MKT-024 starting OTM** is `base_otm × call_starting_otm_multiplier`
where `base_otm` is VIX-adjusted (~50pt at VIX 18-19) and the multiplier
is 3.5×/4.0× (call/put). At the standard 110pt spread, this places the
search starting point at ~175-200pt OTM, well into territory where Saxo's
chain has 25pt strike intervals (not 5pt) and far-OTM options are
illiquid.

At 110pt spread, MKT-020/022 progressive tightening then scans inward
in 5pt steps until net credit is viable. The wider 110pt long leg has
near-zero value at far OTM, so net credit is dominated by the short's
extrinsic premium — tightening rapidly produces enough credit to meet
the minimum.

**At 50pt spread, the long leg is much closer to the short and has
MUCH higher value.** Net credit (= short_mid − long_mid) compresses
across the entire OTM range. MKT-020/022 can scan all the way to the
25pt OTM floor without reaching minimum credit. Plus — as observed
2026-04-27 — at the wide MKT-024 starting position, MKT-007/008
illiquidity checks fall back to the original strikes with a warning,
and the tightening loop never logs a successful tighten. The bot ends
up placing a full IC with negligible credit and a MIN_STOP_LEVEL
fallback for stop pricing.

## What today actually showed

```
10:45:04  MKT-024: VIX=18.7, base_otm=50pt → call_otm=175pt (×3.5),
          put_otm=200pt (×4.0), call_spread=50pt, put_spread=50pt
10:45:05  MKT-007: Call 7340 illiquid, trying 7335
10:45:05  MKT-007: Call 7340 illiquid, trying 7330
10:45:06  MKT-007: Could not find liquid strike for Call near 7340
10:45:07  Strike 7385 Call not found in chain
10:45:09  MKT-008: Could not find liquid long Call near 7390, using original
10:45:13  [DRY RUN] Simulated Entry #1: Real credits Call=$0.00 Put=$5.00
10:45:13  CRITICAL: Total credit $5.00 very low, using minimum stop
```

No `MKT-020: Call tightened` or `MKT-022: Put tightened` log line — the
tightening scan didn't run (or ran but produced nothing actionable). The
entry was placed at the original MKT-024 starting position with $0/share
call credit and $0.05/share put credit.

## What needs to change before any spread-width experiment

Before running a 50pt or 75pt experiment again, **MKT-024's starting
multipliers need to be lowered to match the narrower spread**. Sketched
relationship:

| max_spread_width | Recommended call_starting_otm_multiplier | put_starting_otm_multiplier |
|---|---|---|
| 110pt (current live) | 3.5 (current) | 4.0 (current) |
| 75pt | ~2.0 | ~2.5 |
| 50pt | ~1.0 (= base_otm itself) | ~1.5 |

The intuition: at narrower spread, you can't afford to start far OTM
because the long leg is too close to the short for the scan to recover
viable credit. Start near the 8-delta target itself, then let MKT-020/022
tighten only modestly inward.

These multipliers haven't been backtested. **Any future narrow-width
experiment should first run a backtest sweep on
`(max_spread_width, call_starting_otm_multiplier, put_starting_otm_multiplier)`
jointly** — not just `max_spread_width` in isolation.

## Updated priority list — what NOT to repeat

1. ❌ Don't flip `max_spread_width` alone without also reducing MKT-024
   starting multipliers.
2. ❌ Don't combine multiple parameter changes in a single restart
   without validating each in isolation. Today bundled (a) Option B buffers
   (b) Path-B dry-run (c) 50pt spread width — when E#1 misbehaved, attribution
   was unclear until log dive.
3. ❌ Don't trust a static "ready" audit. Future dry-run deploys must
   include a smoke-test that runs through one full entry placement +
   heartbeat + reconciliation cycle BEFORE market open.
4. ✅ Do narrow `max_spread_width` only after a backtest validates
   matched MKT-024 multipliers.

This is captured here so future-me doesn't repeat the same experiment
without the necessary config preparation.
