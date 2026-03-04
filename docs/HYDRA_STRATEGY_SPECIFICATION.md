# HYDRA (Trend Following Hybrid) Strategy Specification

**Last Updated:** 2026-03-04
**Version:** 1.8.0
**Purpose:** Complete strategy specification for the HYDRA 0DTE trading bot
**Base Strategy:** Tammy Chambless's MEIC (Multiple Entry Iron Condors)
**Trend Concepts:** From METF (Market EMA Trend Filter)
**Status:** LIVE — deployed on Google Cloud VM, sole active trading bot

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Philosophy: Why HYDRA Exists](#philosophy-why-hydra-exists)
3. [Strategy Overview](#strategy-overview)
4. [Trend Detection](#trend-detection)
5. [Complete Entry Decision Flow](#complete-entry-decision-flow)
6. [Strike Selection & Adjustment Pipeline](#strike-selection--adjustment-pipeline)
7. [Stop Loss Rules](#stop-loss-rules)
8. [Profit Management System](#profit-management-system)
9. [MKT Rules Reference](#mkt-rules-reference)
10. [State Machine & Bot Lifecycle](#state-machine--bot-lifecycle)
11. [Live Performance Data](#live-performance-data)
12. [Config Specification](#config-specification)
13. [Known Limitations & Edge Cases](#known-limitations--edge-cases)
14. [Related Documentation](#related-documentation)

---

## Executive Summary

### What is HYDRA?

HYDRA is MEIC (Multiple Entry Iron Condors) with a trend-following overlay and a suite of "MKT" rules. Before each entry, it checks EMA 20 vs EMA 40 on SPX 1-minute bars. The EMA signal (BULLISH/BEARISH/NEUTRAL) is logged for analysis but is informational only — entries are full iron condors or put-only (when call credit is non-viable, via MKT-011).

Key MKT rules include: pre-entry credit validation, progressive OTM tightening, and hold-to-expiry profit management — developed iteratively from 12 days of live trading data (Feb 10-26, 2026). Early close (MKT-018/023/021) was tested but intentionally disabled — backtest showed hold-to-expiry outperforms.

### Key Numbers (10 Trading Days: Feb 10-24, 2026)

| Metric | Value |
|--------|-------|
| Net P&L | **+$2,075** |
| Winning Days | 7 / 10 (70%) |
| Average Daily P&L | +$208 |
| Best Day | +$690 (Feb 20 — MKT-018 early close) |
| Worst Day | -$740 (Feb 17 — V-shape reversal) |
| Win/Loss Dollar Ratio | 2.77:1 |
| Total Entries | 44 |
| Net Capture Rate | 14.7% of credit collected |
| Double Stops | 0 (never happened) |

### How It Differs from Base MEIC

| Aspect | Base MEIC | HYDRA |
|--------|-----------|---------|
| Philosophy | Always market-neutral | Always full IC + EMA signal (informational) |
| Entry type | Always full iron condor | Always full iron condor (skip if either side non-viable) |
| Entries per day | 6 | 5 (Entry #6 dropped in v1.6.0 to free margin for wider put spreads) |
| Stop formula (full IC) | total_credit per side | total_credit per side (same as base MEIC) |
| Stop execution | Close both legs (short + long) | Close SHORT only, long expires (MKT-025) |
| Credit gate | Skip if both non-viable | Skip if either side non-viable (MKT-011) |
| Profit management | Hold to expiration | Hold to expiration (MKT-018 early close disabled) |
| OTM tightening | None | Progressive 5pt steps (MKT-020/022) |

---

## Philosophy: Why HYDRA Exists

### The Catalyst: February 4, 2026

Pure MEIC entered 6 iron condors in a sustained downtrend. All 6 put sides were stopped. Loss: ~$1,500. The call sides all expired worthless — collecting ~$750 in credit — but it wasn't enough to offset 6 put-side stops.

If the bot had detected the downtrend and placed only call spreads, it would have collected ~$750 with zero stops. HYDRA was built to do exactly that.

### The Core Insight

MEIC's breakeven design means a full IC with one side stopped nets $0 after commission (MEIC+ reduction = $0.15 covers the $15 commission exactly). But a one-sided entry that gets stopped loses the full credit plus commission. This creates an asymmetry:

- **Full IC in a range-bound market:** Very safe. One side stopped = breakeven. Both sides expire = full profit.
- **One-sided entry in a trending market:** Risky if wrong. But if the trend is correctly identified, the spread is far OTM on the safe side and has a high probability of expiring worthless.
- **Full IC in a trending market:** The stressed side gets stopped, but the surviving side's credit offsets the loss. Still safe (~$5 loss), but you tie up capital for a near-zero return.

HYDRA's philosophy: **Default to full ICs (safe breakeven shield). EMA trend signal is informational only — logged and stored for analysis but never drives entry type. When MKT-011 finds call credit non-viable but put credit viable, place a put-only entry (v1.7.1: 87.5% WR from 6 qualifying entries). When put credit non-viable, skip entirely (call-only entries disabled due to insufficient data).**

### Evolution Through Live Trading

HYDRA started as a simple EMA filter (v1.0.0, Feb 4). Over 10 trading days, each day's results revealed edge cases that led to new MKT rules:

| Date | Lesson | Rule Added |
|------|--------|------------|
| Feb 7 | Illiquid wings → bad fills | MKT-011 (credit gate) |
| Feb 9 | One-sided stop = full loss | Fix #40 (2× stop for one-sided) |
| Feb 10 | Same strikes merged by Saxo | MKT-013 (overlap prevention) |
| Feb 13 | High VIX → huge premium, stops late | MKT-019 (removed v1.4.0) |
| Feb 17 | Wrong trend signals amplify losses | EMA threshold 0.1% → 0.2% |
| Feb 18 | Late stops erased morning gains | MKT-018 (early close on ROC, now disabled) |
| Feb 19 | Cascade breaker blocked winner | MKT-016/017 removed (v1.3.3) |
| Feb 20 | Late entries diluted high ROC | MKT-021 (pre-entry ROC gate, now disabled) |
| Feb 24 | NEUTRAL entry went one-sided | MKT-011 v1.3.6 (NEUTRAL = IC or skip) |
| Feb 24 | MKT-018 could leave money on table | MKT-023 (smart hold check, now disabled) |

---

## Strategy Overview

### Entry Schedule

5 entries per day, spaced 30 minutes apart. Shifted +1 hour in v1.8.0 based on 15-day journal data (10:05 lost -$695, 10:35 lost -$510 across 7 full-entry days; 11:05+ all positive). Aligned with external research: Option Alpha 25k trades (ICs after 11:30 = +37%), Sandvand 9,100 trades (1 PM+ most profitable), U-shaped intraday volatility (trough 11:30-12:30).

| Entry | Time (ET) | Notes |
|-------|-----------|-------|
| 1 | 11:05 AM | Mid-morning; opening volatility settled |
| 2 | 11:35 AM | Entering volatility trough |
| 3 | 12:05 PM | |
| 4 | 12:35 PM | MKT-021 ROC gate before #4 (disabled) |
| 5 | 1:05 PM | MKT-021 ROC gate before #5 (disabled) |

Each entry has a 5-minute retry window after the scheduled time. MKT-031 smart entry windows add a 10-minute scouting period BEFORE each scheduled time (see Smart Entry Windows section below).

### Smart Entry Windows (MKT-031)

Instead of entering at exactly the scheduled time, HYDRA opens a 10-minute scouting window before each entry. Market conditions are scored every main-loop cycle (~2-5s). If the composite score >= 65, the bot enters early. Otherwise, it enters at the scheduled time (identical to previous behavior).

```
10:55  Scouting opens — start scoring every 2-5s
10:58  Score = 42 (momentum rough, ATR high)
11:01  Score = 71 → EARLY ENTRY TRIGGERED (4 min early)
  -- OR --
11:05  Window expires → ENTER ANYWAY (same as current behavior)
11:10  Original 5-min retry window still available if entry fails
```

**Scoring (2 parameters, 100 max):**

| Parameter | Points | Data Source |
|-----------|--------|-------------|
| Post-spike calm (ATR declining from elevated) | 0-70 | 1-min OHLC bars via `get_chart_data()`, cached |
| Momentum pause (price calm over 2 min) | 0-30 | `MarketData.price_history` deque (zero API cost) |

**Parameter 1 — Post-spike calm:** Compares ATR(3) over recent bars vs previous bars. "Elevated" = previous ATR(3) > 1.5× long-term ATR(14). Declining 50%+ from elevated = 70pts, 25%+ = 55pts, 10%+ = 40pts, declining without spike = 20pts, rising = 0pts.

**Parameter 2 — Momentum pause:** |SPX change| over 2 min. < 0.025% = 30pts, < 0.05% = 25pts, < 0.10% = 10pts, >= 0.10% = 0pts.

**Failsafe:** If both parameters fail (API error + no price history), score = 0 → enters at scheduled time. Smart entry can never prevent an entry — it can only trigger one earlier.

### Iron Condor Structure

```
                Call Spread Side
    ┌─────────────────────────────────────────┐
    │  Buy Long Call (protection)             │
    │      ▲                                  │
    │      │ 50 points width (VIX-adjusted)   │
    │      ▼                                  │
    │  Sell Short Call (credit)               │
    └─────────────────────────────────────────┘
                      │
                      │ OTM distance (VIX-adjusted, ~25-120pt)
                      │
    ═══════════════ SPX PRICE ════════════════
                      ▲
                      │ OTM distance (VIX-adjusted, ~25-120pt)
                      │
    ┌─────────────────────────────────────────┐
    │  Sell Short Put (credit)                │
    │      ▲                                  │
    │      │ 50 points width (VIX-adjusted)   │
    │      ▼                                  │
    │  Buy Long Put (protection)              │
    └─────────────────────────────────────────┘
                  Put Spread Side
```

### Trend Signal (Informational Only — v1.4.0)

The EMA signal is calculated before each entry and logged for analysis, but does **not** drive entry type. Entry type is determined by MKT-011 credit gate: full IC when both sides viable, put-only when call non-viable (v1.7.1).

| Trend Signal | What Gets Placed | Note |
|--------------|------------------|------|
| BULLISH (EMA20 > EMA40 by >= 0.2%) | Full IC or put-only (MKT-011) | Signal logged, not acted on |
| BEARISH (EMA20 < EMA40 by >= 0.2%) | Full IC or put-only (MKT-011) | Signal logged, not acted on |
| NEUTRAL (within 0.2%) | Full IC or put-only (MKT-011) | Standard behavior |

**Why trend-driven one-sided entries were removed (v1.4.0):** 12-day analysis (Feb 10-26) showed trend-driven one-sided P&L was -$175 across 23 entries. V-shape reversal days (Feb 17, Feb 26) amplified losses. EMA correctly identifies current direction but cannot predict reversals. **MKT-011 credit-driven put-only entries re-enabled (v1.7.1):** 87.5% WR, +$870 net from 6 qualifying entries — these are credit-driven (call side too cheap) not trend-driven.

### Leg Placement Order (Safety-First)

For every entry type, protection (long) legs are placed before income (short) legs:

| Entry Type | Order |
|-----------|-------|
| Full IC | Long Call → Long Put → Short Call → Short Put |

This ensures the account is never momentarily exposed with a naked short position.

---

## Trend Detection

### EMA Calculation

Before each entry (when `recheck_each_entry = true`), the bot:

1. Fetches 50 bars of SPX 1-minute data via Saxo's chart API (uses US500.I CFD for real-time prices)
2. Calculates EMA(20) and EMA(40) on close prices
3. Computes `diff_pct = (ema_short - ema_long) / ema_long`

### Signal Classification

| Condition | Signal |
|-----------|--------|
| `diff_pct > +0.002` (+0.2%) | BULLISH |
| `diff_pct < -0.002` (-0.2%) | BEARISH |
| `-0.002 <= diff_pct <= +0.002` | NEUTRAL |

### Why 0.2% Threshold

Originally 0.1% (v1.0.0-v1.2.7). Widened to 0.2% after Feb 17 analysis:

- At 0.1%, weak trends generated false BULLISH/BEARISH signals that whipsawed on V-shaped reversals
- At 0.2%, only strong trends get directional treatment
- Back-tested impact: +$850 improvement over the first 5 trading days (fewer wrong one-sided entries)
- 86.4% of all signals are NEUTRAL at 0.2% — the trend filter activates only for clear moves

### Trend Filter Accuracy (Live Data)

| Accuracy | Count |
|----------|-------|
| Correct directional signal | 2/6 (Feb 12 BEARISH during sell-off) |
| Wrong directional signal | 2/6 (Feb 17 both BEARISH and BULLISH wrong) |
| Neutral (no signal) | 38/44 entries (86.4%) |

The trend filter's value isn't in its directional accuracy — it's in staying NEUTRAL most of the time and only going directional when the EMA separation is very clear. When it's wrong, full IC breakeven limits the damage.

---

## Complete Entry Decision Flow

Each entry goes through these phases in order:

### Phase 1: Pre-Entry Gates

```
1. MKT-021 ROC Gate (before entries #4+)
   ├── Is ROC >= 3.0% on existing positions?
   │   ├── YES → Skip ALL remaining entries, MKT-018 fires immediately
   │   └── NO → Continue
   └── Only checked after min_entries_before_roc_gate (default 3) entries placed

2. Standard time-window check
   └── Is current time within entry_time ± entry_window_minutes?
```

### Phase 2: Trend Detection

```
3. Fetch 50 bars of SPX 1-minute data
4. Calculate EMA(20) and EMA(40)
5. Classify: BULLISH / BEARISH / NEUTRAL
```

### Phase 3: Safety Checks

```
6. Check for orphaned orders
7. Check for market halt (MKT-005)
8. Check buying power (ORDER-004)
```

### Phase 4: Strike Calculation

```
9.  VIX-adjusted OTM distance: base(40pt) × vix_factor × delta_adj
10. Initial strikes: short = SPX ± OTM, long = short ± spread_width
11. MKT-007: Short strike liquidity check (move closer if illiquid)
12. MKT-008: Long wing liquidity check (reduce spread if illiquid)
```

### Phase 5: Strike Adjustment Pipeline

```
13. Fix #44:  Long-vs-short conflict check (existing shorts)
14. MKT-013: Short-short overlap prevention (5pt further OTM)
    └── MKT-014: Post-adjustment illiquidity warning
15. Fix #66:  Re-run Fix #44 after MKT-013 shift
16. MKT-015: Long-long overlap prevention (5pt further OTM)
```

### Phase 6: Progressive OTM Tightening (all entries)

```
17. MKT-020: Call tightening — move short call closer in 5pt steps
    └── Until credit >= $0.75 (call minimum) or 25pt OTM floor
    └── If tightened, re-runs steps 13-16
18. MKT-022: Put tightening — mirror of MKT-020 for put side
    └── Same 5pt steps, $1.75 target (put minimum), 25pt floor
    └── If tightened, re-runs steps 13-16
```

### Phase 7: Credit Gate

```
19. MKT-011: Estimate credit from live quotes (call >= $0.75 strict, put >= $1.75 with MKT-029 fallback to $1.65)
    ├── Both sides viable → PROCEED with full iron condor
    ├── Call non-viable, put viable → PUT-ONLY entry (v1.7.1)
    ├── Put non-viable, call viable → SKIP entry (call-only disabled)
    └── Both sides below minimum → SKIP entry
20. MKT-010 fallback: If MKT-011 can't get quotes, use illiquidity flags
    └── Any wing illiquid → SKIP entry
```

### Phase 8: Entry Execution

```
21. Log EMA signal (informational only)
22. Place full iron condor (Long Call → Long Put → Short Call → Short Put)
    (No one-sided entries — all trends get full IC since v1.4.0)
```

### Phase 9: Post-Entry

```
23. Calculate stop levels (total_credit for full ICs)
24. Verify fill prices against PositionBase.OpenPrice
26. Log to Google Sheets
27. Send alert
28. Save state to disk
```

---

## Strike Selection & Adjustment Pipeline

### VIX-Adjusted OTM Distance

```python
base_distance_at_vix15 = 40  # Points OTM for ~8 delta at VIX 15
vix_factor = clamp(vix / 15.0, 0.7, 2.5)
delta_adjustment = 8.0 / target_delta
otm_distance = round_to_5(base_distance * vix_factor * delta_adjustment)
otm_distance = clamp(otm_distance, 25, 120)  # Never closer than 25, never wider than 120
```

| VIX | Factor | OTM Distance | Approx Delta |
|-----|--------|-------------|--------------|
| 10 | 0.70 | 30 pts | ~10-12 |
| 15 | 1.00 | 40 pts | ~8 |
| 20 | 1.33 | 55 pts | ~8 |
| 25 | 1.67 | 65 pts | ~8 |

### Spread Width (VIX-Adjusted)

| VIX Range | Spread Width |
|-----------|-------------|
| < 15 | 40 pts |
| 15-20 | 50 pts |
| 20-25 | 60 pts |
| 25-30 | 70 pts |
| > 30 | 80 pts |

**MKT-028 Asymmetric Floors (v1.6.0):** Put longs cost 7× more than calls due to skew ($0.90 vs $0.15 median). Separate floors: call 60pt (`call_min_spread_width`), put 75pt (`put_min_spread_width`). Since MKT-025 never closes longs, wider = cheaper = pure savings. `margin = max(call, put) × $100`, so wider puts don't require wider calls.

Maximum: 75 pts (`max_spread_width`, margin cap: 5 entries × 75pt × $100 = $37,500 ≤ $39,000).

MKT-008 liquidity fallback uses universal `min_spread_width=60` floor. SPX options use 5-point strike increments.

### Strike Adjustment Pipeline (Exact Order)

This pipeline prevents Saxo from rejecting orders or merging positions:

| Step | Rule | What It Does | Why It Exists |
|------|------|-------------|---------------|
| 1 | Fix #44 | Move new long strikes if they conflict with existing short strikes | Saxo rejects opposite-direction orders at same strike |
| 2 | MKT-013 | Move new short strikes 5pt further OTM if they overlap existing short strikes | Saxo merges same-strike positions, breaking tracking |
| 3 | MKT-014 | Warn if MKT-013 landed on illiquid strike | MKT-013 can undo MKT-007's liquidity optimization |
| 4 | Fix #66 | Re-run Fix #44 after MKT-013 | MKT-013 shifts longs too, potentially creating new conflicts |
| 5 | MKT-015 | Move new long strikes 5pt further OTM if they overlap existing long strikes | Saxo merges same-strike longs, deleting older position ID |
| 6 | MKT-020 | Tighten call OTM from MKT-024 starting distance | Get call credit above $0.75 minimum |
| 7 | MKT-022 | Tighten put OTM from MKT-024 starting distance | Get put credit above $1.75 minimum |

Steps 6-7 internally re-run steps 1-5 if they change strikes.

**MKT-024 (v1.6.0):** Calls start at 3.5× and puts start at 4.0× the VIX-adjusted OTM distance. MKT-020/022 scan inward from there to find the widest viable strike at or above the minimum credit threshold. Puts use $1.75 (top of Tammy's range), calls use $0.75 (lowered from $1.00 in v1.7.2 for 68% call cushion — see HYDRA_CREDIT_CUSHION_ANALYSIS.md). Put multiplier higher because put skew means credit is viable further OTM. Batch API = zero extra cost for wider scan.

---

## Stop Loss Rules

### The Breakeven Design

MEIC's core insight: **set the stop loss per side equal to total credit collected**. If one side is stopped and the other expires worthless, the loss on the stopped side exactly equals the profit from the surviving side = breakeven.

With MEIC+ modification: stop = total_credit - $0.15, covering commission on a one-side-stop (4 entry + 1 close = 5 legs × $2.50 = $12.50, buffered to $15). Net P&L on a one-side-stop = +$2.50 (true breakeven with small buffer).

### HYDRA Stop Formula

Full IC stop uses the same formula as base MEIC: `stop_level = total_credit`.

```
stop_level = entry.total_credit          (both sides get the SAME level)
```

| Entry Type | Stop Formula | Example (C=$125, P=$185) |
|-----------|-------------|--------------------------|
| Full IC | total_credit | $125 + $185 = $310 per side |

**Note:** MKT-019 (virtual equal credit stop: `2 × max(call, put)`) was removed in v1.4.0. MKT-020/MKT-022 progressive tightening + credit minimums ($0.75 calls, $1.75 puts) reduced credit skew from 3-7x to 1-3x, making the wider stop unnecessary. Analysis of 6 stops showed ~$825 in savings from tighter stops with zero surviving entries saved by the wider level.

**MEIC+ applies after:** If `meic_plus_enabled` and credit exceeds threshold, subtract $0.15 (× 100 = $15) from the stop level. MKT-025 closes only the short leg on stop, so commission is 4 entry + 1 close = 5 legs × $2.50 = $12.50. The $15 reduction provides a $2.50 buffer for slippage.

**Safety floor:** MIN_STOP_LEVEL = $50. If stop_level is below $50 (e.g., due to zero fill price from API sync issues), skip stop monitoring for that side.

### MKT-025: Short-Only Stop Close (v1.4.3)

When a stop triggers, HYDRA only closes the **SHORT leg** via market order. The long leg stays open and expires at end-of-day settlement (0DTE = same-day expiry, zero overnight risk).

**Why:** Research from the 0DTE iron condor community (Tammy Chambless, John Einar Sandvand with 1,344+ trades) shows that long wings (far OTM, illiquid) are where most slippage occurs on stop closes. Closing only the short leg:
- **Reduces slippage** — one market order instead of two; the short leg (closer to ATM) has tighter markets
- **Saves $2.50 commission** — 1 close leg instead of 2 (1 × $2.50 vs 2 × $2.50)
- **Matches Tammy's approach** — "set stops on the short only, not on the spread"

**Tradeoff:** We lose the long leg's residual value (it expires worthless instead of being sold). With MKT-024's 3.5×/4.0× wider OTM, long wings are further out and less valuable:
- Call long wings: typically $0.05-$0.15 ($5-$15 lost)
- Put long wings: typically $0.20-$0.65 ($20-$65 lost)

This is offset by saved slippage ($5-$15) and saved commission ($2.50). Net impact is roughly neutral for call stops and slightly negative for put stops — acceptable given the execution simplicity and community validation.

**Settlement handling:** The orphaned long leg is cleaned up automatically at settlement. `check_after_hours_settlement()` detects positions in registry but gone from Saxo (expired), clears position_ids and UICs, and runs `_process_expired_credits()`. The expired credit logic correctly skips stopped sides (`if not entry.call_side_stopped`), so there is no double-counting risk.

**Stop trigger is unchanged:** `spread_value >= stop_level` still uses BOTH leg mid prices (short - long) for the trigger condition. Only the close execution changes.

### Stop Monitoring

The main loop checks stops every ~1-2 seconds:

1. Batch-fetch current spread values for all active entries
2. For each active side of each entry:
   - If `spread_value >= stop_level`: trigger stop
   - Close SHORT leg only via emergency market order (MKT-025)
   - Long leg stays open, expires at settlement
   - Record fill prices (deferred async lookup for accurate P&L)
   - Update realized P&L

### What Triggers a Stop

- **Call side stop:** SPX moves UP toward short call → call spread value increases
- **Put side stop:** SPX moves DOWN toward short put → put spread value increases
- **Speed:** 0DTE options have extreme gamma. On Feb 24, Entry #3's call cushion dropped from 64% to 6% in 2 minutes.

---

## Profit Management System

> **STATUS: INTENTIONALLY DISABLED.** MKT-018, MKT-021, and MKT-023 are disabled in production (`early_close_enabled: false`). Backtest showed no ROC-based early close configuration beats hold-to-expiry. All positions are held until 4:00 PM settlement. Code preserved but dormant — set `early_close_enabled: true` in config to re-enable. See `HYDRA_EARLY_CLOSE_ANALYSIS.md` for full analysis.

The following three rules work together when enabled:

### MKT-021: Pre-Entry ROC Gate

**When:** Before placing entries #4, #5, and #6 (after min 3 entries placed).

**Logic:** If ROC on existing positions already exceeds 3%, skip remaining entries. This prevents ROC dilution — new entries add capital and close costs but start at ~$0 P&L.

**Example (Feb 20):** After 3 entries, ROC = 4.17%. If entries #4/#5/#6 were placed, ROC would dilute to 2.26%. MKT-021 skipped them, MKT-018 fired at 4.17%, locking in +$690.

**Gate counts actual placed entries** — skipped or failed entries do not count toward the minimum. This ensures enough capital is deployed for ROC to be meaningful.

**Interaction:** When MKT-021 blocks remaining entry attempts, MKT-018's gate condition is satisfied and early close checks begin on the same heartbeat cycle.

### MKT-018: Early Close on ROC

**When:** After all entries are placed (or skipped), every heartbeat.

**ROC formula:**
```
unrealized_pnl = Saxo live mark-to-market
total_pnl = realized_pnl + unrealized_pnl
net_pnl = total_pnl - commission
close_cost = active_legs × $5.00   ($2.50 commission + $2.50 slippage)
capital_deployed = sum(spread_width × $100 × contracts) per entry
ROC = (net_pnl - close_cost) / capital_deployed
```

**Trigger:** ROC >= 3.0% → check MKT-023 hold check before closing.

**Execution:** Close all active positions via market orders, spawn async fill correction threads, log daily summary immediately, transition to DAILY_COMPLETE.

**Skip conditions:** Last 15 minutes before close (positions expire naturally), already triggered.

### MKT-023: Smart Hold Check

**When:** MKT-018's ROC threshold is met (before closing).

**Logic:** Compares close-now P&L vs worst-case hold P&L:

1. **Determine market lean** from average cushion per side:
   - `avg_call_cushion` vs `avg_put_cushion`
   - Lower cushion = stressed side
   - If difference < 1.0% (lean_tolerance) → no clear lean → CLOSE

2. **Calculate worst-case hold P&L:**
   ```
   worst_case = realized_pnl
              + sum(safe_side_credits)           # expire worthless
              + sum(stressed: credit - stop)      # all get stopped
              - commission
              - stop_close_commission
   ```

3. **Decision:**
   - `worst_case_hold > close_now` → **HOLD** (don't close)
   - `worst_case_hold <= close_now` → **CLOSE** (bird-in-hand)
   - All one-sided / no opposing sides / no clear lean → **CLOSE**

**Example (Feb 24):** ROC hit 2.02%. MKT-023 checked: close_now=$425, worst_case_hold=-$315. Close-now was better → MKT-018 closed, locking in +$435.

### How the Three Rules Interact

```
Entry #1 → #2 → #3 placed normally
                         │
                    MKT-021 checks ROC
                         │
              ┌──────────┴──────────┐
              │                     │
         ROC < 3%              ROC >= 3%
              │                     │
         Place #4, #5          Skip #4, #5
              │                     │
         All placed            MKT-018 gate opens
              │                     │
         MKT-018 monitors      MKT-023 hold check
              │                     │
         ROC >= 3%?          HOLD or CLOSE?
              │                     │
         MKT-023 check         Execute decision
```

---

## MKT Rules Reference

### Active Rules (as of v1.6.0)

| Rule | Name | Added | What It Does |
|------|------|-------|-------------|
| MKT-007 | Short Strike Liquidity | v1.0.0 | Move short strikes closer to ATM if illiquid |
| MKT-008 | Long Wing Liquidity | v1.0.0 | Reduce spread width if long wing illiquid; sets illiquidity flags |
| MKT-009 | VIX-Adjusted Spread Width | v1.0.0 | 40-80pt spreads based on VIX level |
| MKT-026 | Min Spread Width Floor | v1.4.5 | Floor raised to 60pt (cheaper longs on low-VIX days, pure savings with MKT-025) |
| MKT-010 | Illiquidity Fallback | v1.1.0 | Fallback when MKT-011 can't get quotes; uses illiquidity flags |
| MKT-011 | Credit Gate | v1.1.0 | Estimate credit pre-entry; call non-viable → put-only (v1.7.1); put non-viable → skip |
| MKT-013 | Short-Short Overlap | v1.1.4 | Prevent new short strikes from matching existing shorts |
| MKT-014 | Post-Overlap Liquidity Warning | v1.1.5 | Warn if MKT-013 adjustment landed on illiquid strike |
| MKT-015 | Long-Long Overlap | v1.2.2 | Prevent new long strikes from matching existing longs |
| MKT-018 | Early Close on ROC | v1.3.0 | **DISABLED** — Hold-to-expiry outperforms. Close all when ROC >= 3% (if re-enabled) |
| MKT-020 | Progressive Call Tightening | v1.3.1 | Move short call closer in 5pt steps until credit >= $0.75 |
| MKT-021 | Pre-Entry ROC Gate | v1.3.2 | **DISABLED** — Only active when MKT-018 enabled. Skip entries #4/#5 if ROC >= 3% |
| MKT-022 | Progressive Put Tightening | v1.3.5 | Move short put closer in 5pt steps until credit >= $1.75 |
| MKT-023 | Smart Hold Check | v1.3.7 | **DISABLED** — Only active when MKT-018 enabled. Compare close-now vs hold |
| MKT-024 | Wider Starting OTM | v1.4.1 | Start calls at 3.5× and puts at 4.0× VIX-adjusted distance; MKT-020/022 scan inward (v1.6.0: upgraded from 2×) |
| MKT-025 | Short-Only Stop Close | v1.4.3 | Close SHORT leg only on stop; long expires at settlement |
| MKT-028 | Asymmetric Spread Widths | v1.6.0 | Put floor 75pt, call floor 60pt (put longs 7× more expensive due to skew; wider = cheaper = pure savings) |

### Removed Rules

| Rule | Name | Added | Removed | Why Removed |
|------|------|-------|---------|-------------|
| MKT-016 | Stop Cascade Breaker | v1.2.8 | v1.3.3 | Net +$80 over 10 days (noise); blocked profitable entries |
| MKT-017 | Daily Loss Limit | v1.2.9 | v1.3.3 | Cost $1,200 on Feb 23 by blocking 3 winning entries |
| MKT-019 | Virtual Equal Credit Stop | v1.3.0 | v1.4.0 | MKT-020/022 reduced skew to 1-2x; ~$825 saved across 6 stops with tighter total_credit |

**Why removed:** Full IC breakeven means stopped entries cost only ~$5-$30. Post-cascade entries are placed at safer levels (lower SPX = puts further OTM). Blocking entries is counterproductive.

### Rule Interactions

| Rule A | Rule B | Interaction |
|--------|--------|-------------|
| MKT-007 | MKT-013 | MKT-007 moves strikes closer (liquid); MKT-013 moves them further (overlap). Can undo each other. |
| MKT-013 | Fix #44/66 | MKT-013 shifts longs; Fix #66 re-checks for new long-vs-short conflicts. |
| MKT-024 | MKT-020/022 | MKT-024 sets wider starting OTM; MKT-020/022 scan inward from there. |
| MKT-020/022 | MKT-011 | Tightening runs first; MKT-011 re-validates with fresh quotes (call $0.75, put $1.75). |
| MKT-021 | MKT-018 | MKT-021 skips entries → satisfies MKT-018 gate → early close fires same cycle. |
| MKT-018 | MKT-023 | MKT-023 is a sub-check within MKT-018; can override close decision with HOLD. |
| MKT-025 | Settlement | Short-only close leaves long leg open; settlement auto-cleans orphaned positions. |

---

## State Machine & Bot Lifecycle

### States

| State | Description |
|-------|-------------|
| IDLE | No position, waiting for market open |
| WAITING_FIRST_ENTRY | Market open, waiting for 11:05 AM (or 10:55 with MKT-031 scouting) |
| ENTRY_IN_PROGRESS | Currently placing an entry |
| MONITORING | Active entries, watching stops + ROC |
| STOP_TRIGGERED | Processing a stop loss |
| DAILY_COMPLETE | All done for today (all expired or early close) |
| CIRCUIT_BREAKER | Too many consecutive failures (5) |
| HALTED | Critical error, manual intervention required |

### State Transitions

```
IDLE → WAITING_FIRST_ENTRY           (9:30 AM)
WAITING_FIRST_ENTRY → ENTRY_IN_PROGRESS  (11:05 AM, or earlier via MKT-031)
ENTRY_IN_PROGRESS → MONITORING       (entry placed)
MONITORING → ENTRY_IN_PROGRESS       (next entry time)
MONITORING → STOP_TRIGGERED          (spread_value >= stop_level)
MONITORING → DAILY_COMPLETE          (4:00 PM settlement; MKT-018 early close if re-enabled)
STOP_TRIGGERED → MONITORING          (stop processed)
Any → CIRCUIT_BREAKER                (5 consecutive failures)
Any → HALTED                         (critical: overnight positions, stale registry)
```

### Daily Lifecycle

| Time (ET) | Event |
|-----------|-------|
| Midnight | `_reset_for_new_day()`: clear daily state, verify stale registry (Fix #82) |
| 9:30 AM | Market opens, transition to WAITING_FIRST_ENTRY |
| 10:55 AM | MKT-031 scouting opens for Entry #1 (score market conditions every ~2-5s) |
| 11:05 AM | Entry #1 (trend detection → strike calc → credit gate → execution). Earlier if MKT-031 score >= 65. |
| 11:35 AM | Entry #2 (scouting from 11:25) |
| 12:05 PM | Entry #3 (scouting from 11:55) |
| 12:35 PM | Entry #4 (scouting from 12:25) |
| 13:05 PM | Entry #5 (scouting from 12:55) |
| 13:05+ PM | MONITORING: stop checks every ~1-2s, heartbeat every 10s. Hold to expiry. |
| 3:45 PM | Last 15 min, positions expire naturally at settlement |
| 4:00 PM | Market close, 0DTE options expire/settle |
| 4:00-5:00 PM | `check_after_hours_settlement()`: process expired credits |
| Post-settlement | `log_daily_summary()`, `log_account_summary()`, `log_performance_metrics()` |

### Recovery on Restart

If the bot restarts mid-day:

1. Query Saxo API for all open positions
2. Filter by Position Registry (bot name = "HYDRA")
3. Group by entry number using registry metadata
4. Load state file for today's date
5. Reconstruct `HydraIronCondorEntry` objects with correct flags
6. **State file is authoritative** for: entry classification, status flags, counters, credits (Fix #65)
7. Resume monitoring from where it left off

### Heartbeat Display

Every 10 seconds when market is open:

```
HEARTBEAT | Monitoring | SPX: 6012.45 | VIX: 19.5 | Entries: 6/6 | Active: 3 | Trend: NEUTRAL
  Entry #1 [IC]: C:78% cushion | P:45% cushion | Credit: $475
  Entry #2 [IC/SKIPPED]: MKT-011 skipped (put non-viable)
  Entry #3 [IC]: C:52% cushion | P:STOPPED | Credit: $510
  [▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓]
  [░░░░░░░░░░░  +$305.00 net ($35 comm)  ░░░░░░░░░░░░]
  [▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓]
  Capital: $25,000 | Return: +1.4%
  Early Close: ROC +1.85% / 3.0% threshold | Close cost: $50 (10 legs)
  Hold Check: HOLD | close=$380 vs hold=$450 (+70) | CALLS_STRESSED (C:35%/P:82%)
```

---

## Live Performance Data

### Daily Results (Feb 10-24, 2026)

| Date | SPX Range | VIX | Entries | Full IC | One-Sided | Stops | Net P&L | Cumul. |
|------|-----------|-----|---------|---------|-----------|-------|---------|--------|
| Feb 10 | 48 pts (0.7%) | 17.4-17.8 | 5 | 0 | 5 | 1 | +$350 | $350 |
| Feb 11 | 77 pts (1.1%) | 17.0-17.7 | 6 | 1 | 5 | 2 | +$425 | $775 |
| Feb 12 | 149 pts (2.1%) | 17.4-20.7 | 6 | 4 | 2 | 4 | +$360 | $1,135 |
| Feb 13 | 90 pts (1.3%) | 21.0-20.6 | 5 | 4 | 1 | 3 | +$675 | $1,810 |
| Feb 17 | 92 pts (1.3%) | 21.9-20.3 | 5 | 3 | 2 | 5 | -$740 | $1,070 |
| Feb 18 | 61 pts (0.9%) | 19.7-19.6 | 4 | 1 | 3 | 2 | +$315 | $1,385 |
| Feb 19 | 41 pts (0.6%) | 20.4-20.3 | 4 | 2 | 2 | 3 | -$30 | $1,355 |
| Feb 20 | 76 pts (1.1%) | 20.5-19.5 | 3 | 3 | 0 | 1 | +$690 | $2,045 |
| Feb 23 | 94 pts (1.4%) | 20.6-21.4 | 2 | 2 | 0 | 2 | -$405 | $1,640 |
| Feb 24 | 61 pts (0.9%) | 20.6-19.5 | 4 | 2 | 2 | 1 | +$435 | $2,075 |

### Financial Summary

| Metric | Value |
|--------|-------|
| Total Credit Collected | $14,090 |
| Expired Credits | $8,325 (59.1%) |
| Stop Loss Debits | $5,755 (40.8%) |
| Commission | $495 (3.5%) |
| **Net P&L** | **+$2,075 (14.7% capture rate)** |

### Stop Rate by Entry Type

| Entry Type | Entries | Stop Rate | Avg P&L When Stopped |
|-----------|---------|-----------|---------------------|
| Full IC (one side stopped) | 22 | ~36% per side | ~-$5 (breakeven) |
| Put-only (MKT-011 conversion) | 16 | 50% | -$142 (full credit lost) |
| Call-only | 5 | 80% | -$205 (full credit lost) |
| Full IC (both stopped) | — | 0% | Never happened |

### Key Observations

1. **Put stops dominate:** 19 put stops vs 5 call stops. Market had downside bias.
2. **MKT-011 conversions were frequent (pre-v1.4.0):** 36.4% of entries converted from NEUTRAL IC to put-only due to low call credit. One-sided entries were removed in v1.4.0; MKT-011 now skips instead of converting.
3. **Most signals are NEUTRAL:** 86.4% at 0.2% threshold. Trend filter rarely activates.
4. **No double stops:** In 44 entries, both sides were never stopped on the same entry.
5. **Early close was tested but disabled:** MKT-018 triggered twice (Feb 20: +$690, Feb 24: +$435) but backtest showed hold-to-expiry outperforms overall.

### Commission Formula

| Entry Type | Expires | One Side Stopped | Both Stopped |
|-----------|---------|-----------------|--------------|
| Full IC | $10 | $15 | $20 |

Commission = $2.50 per leg per transaction. Expired options incur no close commission.

---

## Config Specification

### Trend Filter (`config.trend_filter`)

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable/disable trend filter |
| `ema_short_period` | `20` | Short EMA period |
| `ema_long_period` | `40` | Long EMA period |
| `ema_neutral_threshold` | `0.002` | 0.2% — threshold for neutral zone |
| `recheck_each_entry` | `true` | Re-check trend before each entry |
| `chart_bars_count` | `50` | Number of 1-min bars to fetch |
| `chart_horizon_minutes` | `1` | Bar interval |

### Strategy (`config.strategy`)

| Key | Default | Description |
|-----|---------|-------------|
| `entry_times` | `["11:05","11:35","12:05","12:35","13:05"]` | Entry schedule (ET). 5 entries, shifted +1hr (v1.8.0) |
| `entry_window_minutes` | `5` | Window around entry time |
| `spread_width` | `50` | Default spread width (points) |
| `min_spread_width` | `60` | MKT-008 liquidity fallback floor (universal) |
| `call_min_spread_width` | `60` | MKT-028: Call spread floor (points) |
| `put_min_spread_width` | `75` | MKT-028: Put spread floor (put longs 7× more expensive due to skew) |
| `max_spread_width` | `75` | Maximum spread width (margin cap: 5 × 75pt × $100 = $37,500) |
| `target_delta` | `8` | Target delta for short strikes |
| `min_delta` | `5` | Minimum acceptable delta |
| `max_delta` | `15` | Maximum acceptable delta |
| `min_credit_per_side` | `1.00` | Credit warning threshold ($/side) |
| `max_credit_per_side` | `1.75` | Credit warning ceiling ($/side) |
| `min_viable_credit_per_side` | `0.75` | MKT-011/MKT-020 call minimum (v1.7.2: lowered from $1.00 for 68% call cushion — see HYDRA_CREDIT_CUSHION_ANALYSIS.md) |
| `min_viable_credit_put_side` | `1.75` | MKT-011/MKT-022 put minimum (top of Tammy's $1.00-$1.75 range) |
| `call_starting_otm_multiplier` | `3.5` | MKT-024: call starting OTM = base × multiplier (batch API = zero extra cost) |
| `put_starting_otm_multiplier` | `4.0` | MKT-024: put starting OTM = base × multiplier (put skew = credit viable further OTM) |
| `min_call_otm_distance` | `25` | MKT-020 OTM floor for call tightening (points) |
| `min_put_otm_distance` | `25` | MKT-022 OTM floor for put tightening (points) |
| `meic_plus_enabled` | `true` | Enable MEIC+ stop reduction |
| `meic_plus_reduction` | `0.15` | MEIC+ reduction (covers $15 commission on one-side-stop) |
| `max_vix_entry` | `30` | Maximum VIX for new entries |
| `contracts_per_entry` | `1` | Contracts per entry |
| `early_close_enabled` | `false` | MKT-018: Intentionally disabled (hold-to-expiry outperforms). Set `true` to re-enable. |
| `early_close_roc_threshold` | `0.03` | MKT-018 ROC threshold (3.0%). Only used when enabled. |
| `early_close_cost_per_position` | `5.00` | Close cost estimate per leg. Only used when enabled. |
| `hold_check_enabled` | `true` | MKT-023: Only used when MKT-018 enabled. |
| `hold_check_lean_tolerance` | `1.0` | MKT-023 lean threshold (%). Only used when enabled. |
| `min_entries_before_roc_gate` | `3` | MKT-021: Only active when MKT-018 enabled. |

### Filters (`config.filters`)

| Key | Default | Description |
|-----|---------|-------------|
| `fomc_blackout` | `true` | Skip trading on FOMC announcement days |

---

## Known Limitations & Edge Cases

### EMA Lag

EMAs are lagging indicators. On Feb 17, a V-shaped reversal generated BEARISH at the first entry (correct for the first move) then BULLISH later (correct for the reversal). Since v1.4.0, the EMA signal is informational only (all entries are full ICs), so lag only affects the logged signal — not entry type.

### Volatility Skew

Put premiums are typically 2-7× higher than call premiums at the same delta. This means:
- MKT-024 starts calls at 3.5× and puts at 4.0× base OTM to give MKT-020/022 room to find optimal strikes
- MKT-011 uses separate thresholds: calls $0.75 (v1.7.2, lowered from $1.00), puts $1.75
- MKT-020 call tightening now reaches $0.75 more easily → fewer MKT-011 skips/conversions
- MKT-022 with $1.75 put minimum finds the widest viable put strike, reducing unnecessary tightness
- Total_credit stop (shared by both sides) is adequate because MKT-020/022 keeps skew at 1-2x

### Saxo Position Merging

Saxo merges positions at the same strike and direction into a single position, deleting the older position ID. This breaks position tracking. MKT-013 (shorts) and MKT-015 (longs) prevent this, but the strike adjustment pipeline adds 5pt offsets that can accumulate across entries.

### 0DTE Gamma Risk

0DTE options have extreme gamma. Cushion can evaporate in minutes:
- Feb 24 Entry #3: 64% → 6% call cushion in 2 minutes
- Feb 17: 3 call stops in 11 minutes during a sharp rally

### Settlement Timing

0DTE options settle between 4:00 PM and 2:00 AM ET. The bot checks for settlement after market close. Fix #82 prevents the midnight reset from locking the settlement gate. Fix #77 ensures expired credits are processed even when the position registry is empty.

### One-Sided Entry Risk (Historical — removed in v1.4.0)

One-sided entries were removed in v1.4.0. All entries are now full iron condors or skipped entirely. Historical context: one-sided entries (v1.0.0-v1.3.x) that got stopped lost the full credit plus commission, which was worse than a full IC stop (breakeven by design). This was a key motivation for switching to full-IC-only in v1.4.0.

---

## Related Documentation

| Document | Purpose |
|----------|---------|
| [MEIC Strategy Specification](MEIC_STRATEGY_SPECIFICATION.md) | Base MEIC spec (strike selection, stop math, sources) |
| [MEIC Edge Cases](MEIC_EDGE_CASES.md) | 79 edge cases for base MEIC |
| [HYDRA Trading Journal](HYDRA_TRADING_JOURNAL.md) | Daily results, analysis, what-if projections |
| [HYDRA Early Close Analysis](HYDRA_EARLY_CLOSE_ANALYSIS.md) | MKT-018 research: ROC vs credit-based thresholds |
| [HYDRA README](../bots/hydra/README.md) | Operational guide: config, deployment, version history |
| [Saxo API Patterns](SAXO_API_PATTERNS.md) | Fill prices, order handling, WebSocket |

### Key Source Files

| File | Purpose |
|------|---------|
| `bots/hydra/strategy.py` | HYDRA strategy (extends base MEIC) |
| `bots/hydra/main.py` | Entry point, main loop, heartbeat, settlement |
| `bots/meic/strategy.py` | Base MEIC strategy (inherited methods) |
| `bots/hydra/config/config.json.template` | Config template |
| `data/hydra_state.json` | Daily state persistence (on VM) |
| `data/hydra_metrics.json` | Cumulative metrics (on VM) |
