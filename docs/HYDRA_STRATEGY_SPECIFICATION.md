# HYDRA (Trend Following Hybrid) Strategy Specification

**Last Updated:** 2026-04-01
**Version:** 1.21.0
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

HYDRA is MEIC (Multiple Entry Iron Condors) with a trend-following overlay and a suite of "MKT" rules. Before each entry, it checks EMA 20 vs EMA 40 on SPX 1-minute bars. The EMA signal (BULLISH/BEARISH/NEUTRAL) is logged for analysis but is informational only — base entries are full iron condors, put-only (when call credit is non-viable AND VIX < 15.0, via MKT-011 + MKT-032/MKT-039), or call-only (when put credit is non-viable but call viable, via MKT-040; 89% WR, +$46 EV). Conditional entry E6 fires as put-only when SPX rises >= 0.25% above the session open (Upday-035). E7 is DISABLED.

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
| Entry type | Always full iron condor | Full IC; put-only if call non-viable (MKT-011); call-only if put non-viable (MKT-040) |
| Entries per day | 6 | 3 base + 1 conditional (v1.19.0, was 5+2) |
| Stop formula (full IC) | total_credit per side (MEIC+: -$0.10) | total_credit + asymmetric buffer (call +$0.35, put +$1.55) |
| Stop execution | Close both legs (short + long) | Close both legs (default) or SHORT only when `short_only_stop: true` (MKT-025) |
| Credit gate | Skip if both non-viable | Call non-viable → put-only; put non-viable → retry tighter puts, then call-only (MKT-040); both → skip |
| Profit management | Hold to expiration | Hold to expiration (MKT-018 early close disabled) |
| OTM tightening | None | Progressive 5pt steps (MKT-020/022) |

---

## Philosophy: Why HYDRA Exists

### The Catalyst: February 4, 2026

Pure MEIC entered 6 iron condors in a sustained downtrend. All 6 put sides were stopped. Loss: ~$1,500. The call sides all expired worthless — collecting ~$750 in credit — but it wasn't enough to offset 6 put-side stops.

If the bot had detected the downtrend and placed only call spreads, it would have collected ~$750 with zero stops. HYDRA was built to do exactly that.

### The Core Insight

MEIC's breakeven design means a full IC with one side stopped nets ~$0. HYDRA uses a credit+buffer stop (stop = credit + $0.10), giving ~$25 more cushion per stop at the cost of ~$25 loss per stop event. But a one-sided entry that gets stopped loses the full credit plus commission. This creates an asymmetry:

- **Full IC in a range-bound market:** Very safe. One side stopped = breakeven. Both sides expire = full profit.
- **One-sided entry in a trending market:** Risky if wrong. But if the trend is correctly identified, the spread is far OTM on the safe side and has a high probability of expiring worthless.
- **Full IC in a trending market:** The stressed side gets stopped, but the surviving side's credit offsets the loss. Still safe (~$5 loss), but you tie up capital for a near-zero return.

HYDRA's philosophy: **Default to full ICs (safe breakeven shield). EMA trend signal is informational only — logged and stored for analysis but never drives entry type. When MKT-011 finds call credit non-viable but put credit viable, place a put-only entry only if VIX < 15.0 (MKT-032/MKT-039). At VIX >= 15.0, skip instead (no call hedge in volatile conditions). When put credit non-viable but call viable, place a call-only entry (MKT-040, v1.15.1; 89% WR, +$46 EV). When both non-viable, skip. On down days (SPX drops >= 0.57% below the session open), base entries E1-E3 convert to call-only. Conditional entry E6 fires as put-only on up days (SPX rises >= 0.25%, Upday-035). Whipsaw filter skips entries when intraday range > 1.75× expected move.**

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

3 base entries per day, spaced 30 minutes apart at :15/:45 marks, plus 1 conditional entry. Starting at 10:15 AM. E4/E5 dropped in v1.19.0 (negative EV in walk-forward backtest).

**Current schedule (v1.19.0, walk-forward convergence):**

| Entry | Time (ET) | Type | Notes |
|-------|-----------|------|-------|
| 1 | 10:15 | Base | Always attempts (full IC or one-sided) |
| 2 | 10:45 | Base | |
| 3 | 11:15 | Base | |
| 6 | 14:00 | Conditional (Upday-035) | Only fires on up days as put-only |

E7 (13:15) is DISABLED. E6 fires as put-only when SPX rises >= 0.25% above session open (Upday-035). On non-up days, E6 is silently skipped.

**Previous schedule (MKT-034 enabled, v1.10.0-1.10.2):**

| Entry | Time (ET) | Notes |
|-------|-----------|-------|
| 1 | 11:14:30 | VIX gate check at 11:14:00 |
| 2 | 11:44:30 | |
| 3 | 12:14:30 | |
| 4 | 12:44:30 | |
| 5 | 13:14:30 | |

Each entry has a 5-minute retry window after the scheduled time. MKT-031 smart entry windows add a 10-minute scouting period BEFORE each scheduled time (see Smart Entry Windows section below).

### VIX-Scaled Entry Time Shifting (MKT-034) — DISABLED (v1.10.3)

> **Status:** DISABLED via `vix_time_shift.enabled: false`. Neither Tammy Chambless nor John Sandvand use VIX-based time shifting. Winning period (Feb 10-27) used early entries starting at 10:05 AM. Code preserved and configurable — set `enabled: true` to re-enable.

At high VIX (>= 20), early entries (11:15-11:45) have 86-100% stop rates while later entries (12:15-12:45) have only 50-67% stop rates with nearly double the P&L per entry. MKT-034 shifts the 5-entry schedule later on high-VIX days.

**VIX gate check** runs at :14:00/:44:00 (30s before entry execution, 1 min before :15/:45 marks). Only applies to E#1 — after E#1 is placed, E#2-E#5 use standard scheduling.

| VIX at Check | E#1 Start | Entry Schedule |
|--|--|--|
| < 20 | 11:14:30 | 11:14:30, 11:44:30, 12:14:30, 12:44:30, 13:14:30 |
| 20-23 | 11:44:30 | 11:44:30, 12:14:30, 12:44:30, 13:14:30, 13:44:30 |
| >= 23 | 12:14:30 | 12:14:30, 12:44:30, 13:14:30, 13:44:30, 14:14:30 |

**Floor:** 12:14:30 — E#1 always enters by this slot regardless of VIX.

**MKT-031 interaction:** If MKT-031 wants early entry during scouting, VIX gate is checked first. If VIX allows → resolve + enter early. If VIX blocks → skip early entry, wait for scheduled time.

**Early close days:** Cutoff raised to 12:30 PM (from 12:00) to allow 12:14:30 entry on high-VIX early close days.

**Config:** `vix_time_shift.enabled`, `medium_vix_threshold` (20.0), `high_vix_threshold` (23.0).

### Smart Entry Windows (MKT-031) — DISABLED (v1.10.4)

> **Status:** DISABLED via `smart_entry.enabled: false`. Early entries add complexity without proven edge — enter at scheduled times only. Code preserved and configurable — set `enabled: true` to re-enable.

Instead of entering at exactly the scheduled time, HYDRA opens a 10-minute scouting window before each entry. Market conditions are scored every main-loop cycle (~2-5s). If the composite score >= 65, the bot enters early. Otherwise, it enters at the scheduled time (identical to previous behavior).

```
10:05     Scouting opens — start scoring every 2-5s
10:08     Score = 42 (momentum rough, ATR high)
10:10     Score = 71 → EARLY ENTRY
  -- OR --
10:15     Window expires → ENTER ANYWAY (scheduled time)
10:20     Original 5-min retry window still available if entry fails
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
    │      │ VIX×6.0 width (25-110pt)         │
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
    │      │ VIX×6.0 width (25-110pt)         │
    │      ▼                                  │
    │  Buy Long Put (protection)              │
    └─────────────────────────────────────────┘
                  Put Spread Side
```

### Trend Signal (Informational Only — v1.4.0)

The EMA signal is calculated before each entry and logged for analysis, but does **not** drive entry type. For base entries E1-E3, entry type is determined by MKT-011 (credit gate): full IC when both sides viable, put-only when call non-viable AND VIX < 15.0 (MKT-032/MKT-039), call-only when put non-viable but call viable after retrying tighter puts (MKT-040), skip when both non-viable. Conditional entry E6 fires as put-only when Upday-035 triggers (SPX rises >= 0.25% above session open). E7 is DISABLED.

| Trend Signal | What Gets Placed | Note |
|--------------|------------------|------|
| BULLISH (EMA20 > EMA40 by >= 0.2%) | Full IC, put-only (MKT-011), call-only (MKT-040), or skip (base); put-only (E6 on up days) | Signal logged, not acted on |
| BEARISH (EMA20 < EMA40 by >= 0.2%) | Full IC, put-only (MKT-011), call-only (MKT-040), or skip (base); put-only (E6 on up days) | Signal logged, not acted on |
| NEUTRAL (within 0.2%) | Full IC, put-only (MKT-011), call-only (MKT-040), or skip (base); put-only (E6 on up days) | Standard behavior |

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
    └── Until credit >= $2.00 (call minimum) or 25pt OTM floor
    └── If tightened, re-runs steps 13-16
18. MKT-022: Put tightening — mirror of MKT-020 for put side
    └── Same 5pt steps, $2.75 target (put minimum), 25pt floor
    └── If tightened, re-runs steps 13-16
```

### Phase 6.5: Conditional Entry Trigger (MKT-035 / Upday-035, updated v1.19.0)

```
18.5a MKT-035 (down-day): Check if SPX < session_open × (1 - 0.0057)
      ├── Base entry (1-3): Convert to call-only when down >= 0.57% (base_entry_downday_callonly_pct)
      └── Conditional entries: E7 DISABLED
18.5b Upday-035 (up-day): Check if SPX > session_open × (1 + 0.0025)
      ├── E6 (14:00): Fire as PUT-ONLY when up >= 0.25%
      └── Base entries (1-3): Unaffected by up-day status
```

### Phase 7: Credit Gate

```
19. MKT-011: Estimate credit from live quotes (call >= $2.00, put >= $2.75 with MKT-029 fallback)
    ├── Conditional entry with MKT-035/Upday-035 triggered → Already handled above
    ├── Whipsaw filter: range > 1.75× EM → SKIP entry (v1.19.0)
    ├── Both sides viable → PROCEED with full iron condor
    ├── Call non-viable, put viable, VIX < 15.0 → PUT-ONLY entry (MKT-032/MKT-039 allows)
    ├── Call non-viable, put viable, VIX >= 15.0 → SKIP entry (MKT-032: no call hedge)
    ├── Put non-viable, call viable → Retry with tighter put strikes (5pt closer, max 2 retries)
    │   └── Still non-viable after retries → CALL-ONLY entry (MKT-040: 89% WR, +$46 EV)
    ├── Put non-viable, call non-viable → SKIP entry
    └── Both sides below minimum → SKIP entry
20. MKT-010 fallback: If MKT-011 can't get quotes, use illiquidity flags
    └── Any wing illiquid → SKIP entry
```

### Phase 8: Entry Execution

```
21. Log EMA signal (informational only)
22. Place entry based on credit gate result:
    - Full IC: Long Call → Long Put → Short Call → Short Put
    - Put-only (MKT-011/MKT-032/MKT-039): Long Put → Short Put
    - Call-only (MKT-040 or MKT-035): Long Call → Short Call
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

### Spread Width (VIX-Scaled, v1.19.0)

**Continuous formula:** `round(VIX × 6.0 / 5) × 5`, floor 25pt, cap 110pt.

| VIX | Spread Width |
|-----|-------------|
| 10 | 60 pts |
| 12 | 70 pts |
| 15 | 90 pts |
| 18 | 110 pts (cap) |
| 20+ | 110 pts (cap) |
| < 5 | 25 pts (floor) |

**v1.19.0 changes:** `spread_vix_multiplier` raised from 3.5 to 6.0. Floor lowered from 25pt (was 60pt call, 75pt put asymmetric — now unified). Cap raised from 75pt to 110pt. Walk-forward backtest validated over 719 days.

SPX options use 5-point strike increments.

### Strike Adjustment Pipeline (Exact Order)

This pipeline prevents Saxo from rejecting orders or merging positions:

| Step | Rule | What It Does | Why It Exists |
|------|------|-------------|---------------|
| 1 | Fix #44 | Move new long strikes if they conflict with existing short strikes | Saxo rejects opposite-direction orders at same strike |
| 2 | MKT-013 | Move new short strikes 5pt further OTM if they overlap existing short strikes | Saxo merges same-strike positions, breaking tracking |
| 3 | MKT-014 | Warn if MKT-013 landed on illiquid strike | MKT-013 can undo MKT-007's liquidity optimization |
| 4 | Fix #66 | Re-run Fix #44 after MKT-013 | MKT-013 shifts longs too, potentially creating new conflicts |
| 5 | MKT-015 | Move new long strikes 5pt further OTM if they overlap existing long strikes | Saxo merges same-strike longs, deleting older position ID |
| 6 | MKT-020 | Tighten call OTM from MKT-024 starting distance | Get call credit above $2.00 minimum (MKT-029 floor $0.75) |
| 7 | MKT-022 | Tighten put OTM from MKT-024 starting distance | Get put credit above $2.75 minimum (MKT-029 floor $2.00) |

Steps 6-7 internally re-run steps 1-5 if they change strikes.

**MKT-024 (v1.6.0):** Calls start at 3.5× and puts start at 4.0× the VIX-adjusted OTM distance. MKT-020/022 scan inward from there to find the widest viable strike at or above the minimum credit threshold. Puts use $2.75 (v1.19.0, walk-forward optimized), calls use $2.00 (v1.19.0, walk-forward optimized). Put multiplier higher because put skew means credit is viable further OTM. Batch API = zero extra cost for wider scan.

---

## Stop Loss Rules

### The Breakeven Design

MEIC's core insight: **set the stop loss per side equal to total credit collected**. If one side is stopped and the other expires worthless, the loss on the stopped side exactly equals the profit from the surviving side = breakeven.

**HYDRA (v1.10.2+)** uses a credit+buffer approach with **asymmetric buffers**: call stop = total_credit + `call_stop_buffer` ($0.35), put stop = total_credit + `put_stop_buffer` ($1.55). Walk-forward optimized in v1.19.0 (was $0.10/$5.00). If `put_stop_buffer` is not set, falls back to `call_stop_buffer` for both sides.

### HYDRA Stop Formula

```
call_stop = entry.total_credit + call_stop_buffer     (full IC: call side — $0.35 default)
put_stop  = entry.total_credit + put_stop_buffer      (full IC: put side — $1.55 default)
stop_level = credit + put_stop_buffer                  (put-only via MKT-039: $1.55 buffer)
stop_level = call_credit + theoretical_put + call_stop_buffer (call-only via MKT-040: unified with MKT-035/038)
stop_level = call_credit + theoretical_put + call_stop_buffer (MKT-035 call-only: theoretical put = $260)
```

| Entry Type | Stop Formula | Example (C=$135, P=$210) |
|-----------|-------------|--------------------------|
| Full IC (call side) | total_credit + call_stop_buffer | $345 + $35 = $380 |
| Full IC (put side) | total_credit + put_stop_buffer | $345 + $155 = $500 |
| Call-only (MKT-035) | call_credit + theo_put + call_stop_buffer | $135 + $260 + $35 = $430 |
| Call-only (MKT-040) | call_credit + theo_put + call_stop_buffer | $135 + $260 + $35 = $430 |
| Put-only (MKT-039) | credit + put_stop_buffer | $210 + $155 = $365 |

**Note:** MKT-019 (virtual equal credit stop: `2 × max(call, put)`) was removed in v1.4.0. MKT-020/MKT-022 progressive tightening + credit minimums ($2.00 calls, $2.75 puts) reduced credit skew from 3-7x to 1-3x, making the wider stop unnecessary.

**Credit+Buffer approach (v1.10.2+):** Stop = total_credit + buffer. **Asymmetric buffers:** call side uses `call_stop_buffer` (default $0.35 × 100 = $35), put side uses `put_stop_buffer` (default $1.55 × 100 = $155). Walk-forward optimized in v1.19.0 (was $0.10/$5.00). If `put_stop_buffer` not set, falls back to `call_stop_buffer` for both. Replaces the earlier MEIC+ design (stop = credit - $0.15). **MKT-042 buffer decay (v1.22.0):** Buffers start at 1.75× and decay linearly to 1× over 2 hours — see MKT-042 section below.

**Safety floor:** MIN_STOP_LEVEL = $50. If stop_level is below $50 (e.g., due to zero fill price from API sync issues), skip stop monitoring for that side.

### MKT-025: Short-Only Stop Close (v1.4.3, configurable since v1.9.4)

**Configurable via `long_salvage.short_only_stop` (default: `false` = close both legs).** When `short_only_stop: true`, HYDRA only closes the **SHORT leg** via market order. The long leg stays open and expires at end-of-day settlement (0DTE = same-day expiry, zero overnight risk). When `false` (default), delegates to base MEIC behavior which closes both short and long legs. Analysis of 19+ trading days showed closing both legs has better expected value per stop (~$15-30 better).

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

### MKT-033: Long Leg Salvage After Short Stop (v1.9.2, requires `short_only_stop: true`)

**Only active when `long_salvage.short_only_stop: true`.** After MKT-025 closes the short leg, the surviving long leg normally expires worthless at settlement. But on directional days, the long leg can appreciate significantly. MKT-033 automates selling the long if it's profitable enough to cover round-trip costs.

**Condition:** `(current_bid - open_price) × 100 × contracts >= $10.0`
- $5 round-trip commission ($2.50 open + $2.50 close)
- $5 max market order slippage (1 SPX tick = $0.05 × 100)
- Minimum $0.10 price appreciation (2 SPX ticks)

**Execution:** Market order (guaranteed fill). The bid-price pre-check ensures profitability even with 1-tick slippage.

**Two integration points:**
1. **Immediate:** Right after `_execute_stop_loss()` closes the short, attempt salvage on the long
2. **Periodic:** `_check_long_salvage()` runs every heartbeat cycle during market hours (9:30 AM - 4:00 PM ET), checking all surviving longs with stopped shorts

**On successful sale:**
- `total_realized_pnl += fill_price × 100 × contracts` (revenue from sale)
- `total_commission += $2.50 × contracts` (close commission only; open already counted)
- Entry fields updated: `{side}_long_sold = True`, `{side}_long_sold_revenue = revenue`
- Position ID and UIC cleared (position is gone)
- Unregistered from position registry
- MEDIUM alert sent, logged to Trades tab and safety events

**P&L identity preserved:** `stop_loss_debits = expired_credits - total_realized_pnl` (Fix #78). Since salvage revenue increases `total_realized_pnl`, `stop_loss_debits` automatically decreases — no formula changes needed.

**Market hours guard:** Only attempts during regular market hours. After 4 PM, 0DTE options settle at official settlement price — market orders would fail or get bad fills.

**Config:**
```json
"long_salvage": {
    "enabled": true,
    "min_profit": 10.0
}
```

**Heartbeat display:** Stopped sides with sold longs show "SALVAGED +$X" instead of "STOPPED".

### MKT-036: Stop Confirmation Timer — INTENTIONALLY DISABLED

MKT-036 stop confirmation timer is **intentionally disabled**. The $5.00 put buffer (`put_stop_buffer`) is the chosen solution for false stops instead. Code preserved but dormant — set `stop_confirmation_enabled: true` to re-enable.

**Config:**
```json
"stop_confirmation_enabled": false,
"stop_confirmation_seconds": 75
```

When enabled: 75-second confirmation window before executing stop. 20-day backtest: 17 false stops avoided ($2,870 saved), 1 real stop missed ($85).

### MKT-041: Cushion Recovery Exit

Closes individual IC sides when they nearly hit their stop level then recover. This captures premium from sides that were in danger but pulled back, rather than leaving them exposed to a second breach.

**Trigger conditions (per side):**
1. Side reaches >= `cushion_nearstop_pct` (default 96%) of its stop level (near-stop state)
2. Side subsequently recovers to <= `cushion_recovery_pct` (default 67%) of its stop level

When both conditions are met in sequence, the side is closed at current market price.

**Backtest results (938 days):**
- Sharpe: 2.182 vs 2.094 baseline (hold-to-expiry)
- Fires on ~101 days (10.8% of trading days)

**Config:**
```json
"cushion_nearstop_pct": null,
"cushion_recovery_pct": null
```

Both keys default to `null` (disabled). Set to decimal fractions (e.g., `0.96` and `0.67`) to enable.

**Status:** DISABLED on VM (v1.22.0). Buffer decay (MKT-042) and cushion recovery interfere — wider early buffers push the near-stop threshold further out, causing premature recovery closes. Use one or the other, not both.

### MKT-042: Buffer Decay (v1.22.0)

Time-decaying stop buffer that starts wider and narrows to normal over a configurable period. Early in the trade, premium is rich and market moves are noisier — wider buffers avoid false stops. As theta decays, the normal buffer suffices.

**Formula:**
```
minutes_since_entry = (now - entry_time).total_seconds() / 60
decay_minutes = buffer_decay_hours * 60
decay_factor = max(1.0, buffer_decay_start_mult - (buffer_decay_start_mult - 1.0) * min(1.0, minutes_since_entry / decay_minutes))
effective_call_buffer = call_stop_buffer * decay_factor
effective_put_buffer = put_stop_buffer * decay_factor
```

**Example (defaults: 1.75× start, 2h decay):**
- At entry: call buffer = $0.35 × 1.75 = $0.6125, put buffer = $1.55 × 1.75 = $2.7125
- After 1h: call buffer = $0.35 × 1.375 = $0.4813, put buffer = $1.55 × 1.375 = $2.1313
- After 2h+: call buffer = $0.35 × 1.0 = $0.35, put buffer = $1.55 × 1.0 = $1.55

**Config:**
```json
"buffer_decay_start_mult": 1.75,
"buffer_decay_hours": 2.0
```

Set `buffer_decay_start_mult` to `1.0` or `null` to disable (buffers remain constant).

### MKT-043: Calm Entry Filter (v1.22.0)

Delays entry when SPX has moved sharply in the recent lookback window. Sharp spikes inflate premium attractively but often reverse, leading to immediate stop-outs. The filter waits for the spike to settle before entering.

**Trigger:** If SPX moved more than `calm_entry_threshold_pts` points (high-low range) in the last `calm_entry_lookback_min` minutes, delay entry. Re-checks every ~30 seconds up to `calm_entry_max_delay_min` minutes. If still not calm after max delay, enters anyway (failsafe — never skips an entry entirely).

**Config:**
```json
"calm_entry_lookback_min": 3,
"calm_entry_threshold_pts": 15.0,
"calm_entry_max_delay_min": 5
```

Set `calm_entry_threshold_pts` to `null` to disable.

### MKT-038: FOMC T+1 Call-Only Mode

On the day after FOMC announcement (T+1), all entries are forced to call-only spreads. This applies to both base entries (E1-E3) and conditional entries.

**Rationale:** Research on FOMC T+1 days shows:
- 66.7% of T+1 days are down days
- 23% more volatility than normal trading days
- Put-side exposure is highly dangerous on T+1

**Rules:**
- T+0 (FOMC announcement day only): MKT-008 blocks ALL entries (day 1 trades normally)
- T+1 (day after announcement): MKT-038 forces call-only entries
- T-1 (day before FOMC): No changes — actually favorable for premium selling (69.6% win rate)

**Stop formula:** Same as MKT-035: `call_credit + theoretical $2.60 put + call buffer`

**Config:**
```json
"fomc_t1_callonly_enabled": true,
"fomc_announcement_skip": false
```

**FOMC announcement day (v1.19.0):** `fomc_announcement_skip` changed to `false` — backtest showed trading FOMC days is profitable.

**Implementation:** Uses `is_fomc_t_plus_one()` from `shared/event_calendar.py` to check if yesterday was an FOMC announcement day (day 2). Inserted in `_initiate_entry()` after MKT-035 conditional check and before MKT-011 credit gate.

### Stop Monitoring

The main loop checks stops every ~1-2 seconds:

1. Batch-fetch current spread values for all active entries
2. For each active side of each entry:
   - If `spread_value >= stop_level`: execute stop immediately (MKT-036 timer is DISABLED)
   - Close both legs via market order (default) or SHORT only if `short_only_stop: true` (MKT-025)
   - If short-only: long leg stays open, expires at settlement; MKT-033 may salvage
   - Record fill prices (deferred async lookup for accurate P&L)
   - Update realized P&L

### What Triggers a Stop

- **Call side stop:** SPX moves UP toward short call → call spread value increases
- **Put side stop:** SPX moves DOWN toward short put → put spread value increases
- **Speed:** 0DTE options have extreme gamma. On Feb 24, Entry #3's call cushion dropped from 64% to 6% in 2 minutes.
- **Put buffer:** $1.55 put buffer (v1.19.0, walk-forward optimized). Call buffer is $0.35.

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

### Active Rules (as of v1.19.0)

| Rule | Name | Added | What It Does |
|------|------|-------|-------------|
| MKT-007 | Short Strike Liquidity | v1.0.0 | Move short strikes closer to ATM if illiquid |
| MKT-008 | Long Wing Liquidity | v1.0.0 | Reduce spread width if long wing illiquid; sets illiquidity flags |
| MKT-009 | VIX-Adjusted Spread Width | v1.0.0 | VIX × 6.0, floor 25pt, cap 110pt (v1.19.0) |
| MKT-010 | Illiquidity Fallback | v1.1.0 | Fallback when MKT-011 can't get quotes; uses illiquidity flags |
| MKT-011 | Credit Gate | v1.1.0 | Estimate credit pre-entry; call $2.00 (floor $0.75), put $2.75 (floor $2.00). Call non-viable → put-only if VIX < 15.0 (MKT-032/MKT-039), else skip; put non-viable → retry with tighter puts, then call-only (MKT-040); both → skip |
| MKT-013 | Short-Short Overlap | v1.1.4 | Prevent new short strikes from matching existing shorts |
| MKT-014 | Post-Overlap Liquidity Warning | v1.1.5 | Warn if MKT-013 adjustment landed on illiquid strike |
| MKT-015 | Long-Long Overlap | v1.2.2 | Prevent new long strikes from matching existing longs |
| MKT-018 | Early Close on ROC | v1.3.0 | **DISABLED** — Hold-to-expiry outperforms. Close all when ROC >= 3% (if re-enabled) |
| MKT-020 | Progressive Call Tightening | v1.3.1 | Move short call closer in 5pt steps until credit >= $2.00 (floor $0.75) |
| MKT-021 | Pre-Entry ROC Gate | v1.3.2 | **DISABLED** — Only active when MKT-018 enabled. Skip entries #4/#5 if ROC >= 3% |
| MKT-022 | Progressive Put Tightening | v1.3.5 | Move short put closer in 5pt steps until credit >= $2.75 (floor $2.00) |
| MKT-023 | Smart Hold Check | v1.3.7 | **DISABLED** — Only active when MKT-018 enabled. Compare close-now vs hold |
| MKT-024 | Wider Starting OTM | v1.4.1 | Start calls at 3.5× and puts at 4.0× VIX-adjusted distance; MKT-020/022 scan inward (v1.6.0: upgraded from 2×) |
| MKT-025 | Short-Only Stop Close | v1.4.3 | **Configurable** (`short_only_stop`, default: false). When true: close SHORT only, long expires. When false: close both legs (default since v1.9.4) |
| MKT-026 | Min Spread Width Floor | v1.4.5 | Floor 25pt (v1.19.0, was 60pt) |
| MKT-027 | VIX-Scaled Spread Width | v1.6.0 | Continuous formula `VIX × 6.0` (v1.19.0, was 3.5), floor 25pt, cap 110pt |
| MKT-028 | Asymmetric Spread Widths | v1.6.0 | **Unified in v1.19.0** — single formula VIX × 6.0 replaces separate call/put floors |
| MKT-029 | Graduated Credit Fallback | v1.6.2 | -$0.05, -$0.10 steps below minimum for BOTH calls and puts (call floor $0.75, put floor $2.00). Applied in MKT-011 gate, MKT-020/022 tightening, and MKT-035/038 call-only skip checks. |
| MKT-031 | Smart Entry Windows | v1.8.0 | 10-min scouting before each entry; 2-parameter scoring (ATR calm + momentum pause); score >= 65 triggers early entry |
| MKT-032 | VIX Gate for Put-Only | v1.9.1 | Put-only entries only when VIX < 15.0 (v1.19.0, lowered from 25); at VIX >= 15.0 skip |
| MKT-033 | Long Leg Salvage | v1.9.2 | Requires `short_only_stop: true`. After short stop, sell long if appreciated >= $10 |
| MKT-034 | VIX-Scaled Entry Time Shifting | v1.10.0 | Shifts 5-entry schedule later on high-VIX days. VIX gate checks E#1 at :14:00/:44:00; floor at 12:14:30 |
| MKT-035 | Call-Only on Down Days | v1.11.0 | When SPX drops >= 0.57% (v1.19.0, was 0.3%) below session open, base entries E1-E3 convert to call-only. E7 DISABLED. Stop = call_credit + $260 theo put + buffer |
| MKT-036 | Stop Confirmation Timer | v1.12.0 | **DISABLED.** Put buffer chosen instead ($1.55 in v1.19.0, was $5.00). When enabled: 75-second sustained breach before executing stop. Code preserved, configurable. |
| MKT-038 | FOMC T+1 Call-Only | v1.13.0 | Day after FOMC announcement: force all entries to call-only. T+1 = 66.7% down days, 23% more volatile. Stop = call + $2.60 theo put + buffer. FOMC skip disabled (v1.19.0). |
| MKT-039 | Put-Only Stop Tightening | v1.15.0 | Put-only stop = credit + put_stop_buffer ($1.55). MKT-032 VIX gate at 15.0 (v1.19.0). |
| MKT-040 | Call-Only When Put Non-Viable | v1.15.1 | When put credit below minimum but call viable, place call-only (89% WR, +$46 EV). Stop = call + theo $2.60 put + buffer (unified with MKT-035/038). |
| MKT-041 | Cushion Recovery Exit | v1.21.0 | **DISABLED** (buffer+cushion interfere). When enabled: closes IC side that reaches >= 96% of stop then recovers to <= 67%. Sharpe 2.182 vs 2.094 baseline (938 days). |
| MKT-042 | Buffer Decay | v1.22.0 | Time-decaying stop buffer: starts at 1.75× normal buffer, linearly decays to 1× over 2h. Wider stops early, normal later. Config: `buffer_decay_start_mult`, `buffer_decay_hours`. |
| MKT-043 | Calm Entry Filter | v1.22.0 | Delays entry up to 5 min when SPX moved >15pt in last 3 min. Prevents spike entries. Config: `calm_entry_lookback_min`, `calm_entry_threshold_pts`, `calm_entry_max_delay_min`. |
| Whipsaw | Whipsaw Filter | v1.19.0 | Skip entries when intraday range > 1.75× expected move. High whipsaw = bad for iron condors. |
| Upday-035 | Up-Day Put-Only | v1.17.0 | E6 (14:00) fires as put-only when SPX rises >= 0.25% above session open. Stop = credit + put_stop_buffer. |

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
| MKT-020/022 | MKT-011 | Tightening runs first (uses MKT-029 fallbacks); MKT-011 re-validates with fresh quotes and its own MKT-029 fallbacks (call $2.00/$0.75, put $2.75/$2.00). |
| MKT-021 | MKT-018 | MKT-021 skips entries → satisfies MKT-018 gate → early close fires same cycle. |
| MKT-018 | MKT-023 | MKT-023 is a sub-check within MKT-018; can override close decision with HOLD. |
| MKT-025 | Settlement | Short-only close leaves long leg open; settlement auto-cleans orphaned positions. |
| MKT-034 | MKT-031 | MKT-031 early entry checks VIX gate first for E#1; if VIX blocks, no early entry. |
| MKT-034 | Entry schedule | VIX gate shifts E#1 later; `_resolve_vix_gate` rebuilds 5 consecutive slots from resolved position. |

---

## State Machine & Bot Lifecycle

### States

| State | Description |
|-------|-------------|
| IDLE | No position, waiting for market open |
| WAITING_FIRST_ENTRY | Market open, waiting for first entry (10:15 AM; MKT-034 DISABLED, MKT-031 DISABLED) |
| ENTRY_IN_PROGRESS | Currently placing an entry |
| MONITORING | Active entries, watching stops + ROC |
| STOP_TRIGGERED | Processing a stop loss |
| DAILY_COMPLETE | All done for today (all expired or early close) |
| CIRCUIT_BREAKER | Too many consecutive failures (5) |
| HALTED | Critical error, manual intervention required |

### State Transitions

```
IDLE → WAITING_FIRST_ENTRY           (9:30 AM)
WAITING_FIRST_ENTRY → ENTRY_IN_PROGRESS  (10:15 AM default; MKT-034/MKT-031 DISABLED)
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
| 10:15 | Entry #1 (MKT-034 VIX time shifting DISABLED, MKT-031 smart entry DISABLED) |
| 10:45, 11:15 | Entry #2, #3 |
| 14:00 | Conditional E6 (Upday-035 — fires as put-only when SPX rises >= 0.25% above session open) |
| Last entry + | MONITORING: stop checks every ~1-2s, heartbeat every 10s. Hold to expiry. |
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
  Entry #2 [MKT-040]: C:65% cushion | P:SKIPPED | Credit: $120
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
2. **MKT-011 conversions were frequent (pre-v1.4.0):** 36.4% of entries converted from NEUTRAL IC to put-only due to low call credit. Trend-driven one-sided entries removed in v1.4.0; credit-driven put-only re-enabled v1.7.1 (MKT-011/MKT-032/MKT-039). Call-only when put non-viable added v1.15.1 (MKT-040).
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
| `entry_times` | `["10:15","10:45","11:15"]` | Entry schedule (ET). 3 base entries (v1.19.0, was 5). |
| `entry_window_minutes` | `5` | Window around entry time |
| `spread_width` | `50` | Default spread width (points) |
| `min_spread_width` | `60` | MKT-008 liquidity fallback floor (universal) |
| `call_min_spread_width` | `25` | MKT-028: Call spread floor (v1.19.0, was 60) |
| `put_min_spread_width` | `25` | MKT-028: Put spread floor (v1.19.0, was 75) |
| `max_spread_width` | `110` | Maximum spread width (v1.19.0, must be multiple of 5 for Saxo strikes) |
| `spread_vix_multiplier` | `6.0` | MKT-027: VIX × multiplier for spread width (v1.19.0, was 3.5) |
| `target_delta` | `8` | Target delta for short strikes |
| `min_delta` | `5` | Minimum acceptable delta |
| `max_delta` | `15` | Maximum acceptable delta |
| `min_credit_per_side` | `1.00` | Credit warning threshold ($/side) |
| `max_credit_per_side` | `1.75` | Credit warning ceiling ($/side) |
| `min_viable_credit_per_side` | `2.00` | MKT-011/MKT-020 call minimum (v1.19.0: walk-forward optimized, was $0.60) |
| `min_viable_credit_put_side` | `2.75` | MKT-011/MKT-022 put minimum (v1.19.0: walk-forward optimized, was $2.50) |
| `call_starting_otm_multiplier` | `3.5` | MKT-024: call starting OTM = base × multiplier (batch API = zero extra cost) |
| `put_starting_otm_multiplier` | `4.0` | MKT-024: put starting OTM = base × multiplier (put skew = credit viable further OTM) |
| `min_call_otm_distance` | `25` | MKT-020 OTM floor for call tightening (points) |
| `min_put_otm_distance` | `25` | MKT-022 OTM floor for put tightening (points) |
| `call_stop_buffer` | `0.35` | Call stop buffer: call_stop = credit + $0.35 (v1.19.0, renamed from `stop_buffer`, was $0.10) |
| `put_stop_buffer` | `1.55` | Put stop buffer: put_stop = credit + $1.55 (v1.19.0, walk-forward optimized, was $5.00). Falls back to `call_stop_buffer` if not set. |
| `buffer_decay_start_mult` | `1.75` | MKT-042: buffer starts at 1.75× normal, decays to 1× (set 1.0 or null to disable) |
| `buffer_decay_hours` | `2.0` | MKT-042: hours to decay from start_mult to 1× |
| `calm_entry_lookback_min` | `3` | MKT-043: lookback window (minutes) for SPX range check |
| `calm_entry_threshold_pts` | `15.0` | MKT-043: SPX move threshold (points) to trigger delay (null=disabled) |
| `calm_entry_max_delay_min` | `5` | MKT-043: max delay (minutes) when spike detected |
| `max_vix_entry` | `999` | Maximum VIX for new entries. Set to 999 to effectively disable (v1.10.3). **CALYPSO addition** — neither Tammy Chambless nor John Sandvand (ThetaProfits) use a VIX cutoff; both studied VIX correlation and found none. |
| `contracts_per_entry` | `1` | Contracts per entry |
| `early_close_enabled` | `false` | MKT-018: Intentionally disabled (hold-to-expiry outperforms). Set `true` to re-enable. |
| `early_close_roc_threshold` | `0.03` | MKT-018 ROC threshold (3.0%). Only used when enabled. |
| `early_close_cost_per_position` | `5.00` | Close cost estimate per leg. Only used when enabled. |
| `hold_check_enabled` | `true` | MKT-023: Only used when MKT-018 enabled. |
| `hold_check_lean_tolerance` | `1.0` | MKT-023 lean threshold (%). Only used when enabled. |
| `min_entries_before_roc_gate` | `3` | MKT-021: Only active when MKT-018 enabled. |
| `downday_callonly_enabled` | `true` | MKT-035: Enable call-only entries on down days |
| `downday_threshold_pct` | `0.003` | MKT-035: SPX must drop this % below the session open to trigger E6/E7 conditional (0.3%). E7 DISABLED. |
| `downday_theoretical_put_credit` | `2.60` | MKT-035: Theoretical put credit ($) for stop calculation (v1.19.0, was $2.50) |
| `base_entry_downday_callonly_pct` | `0.0057` | Base entries E1-E3 convert to call-only when SPX drops >= 0.57% from open |
| `conditional_entry_times` | `["14:00"]` | Conditional entry times (v1.19.0: E6 at 14:00) |
| `conditional_e6_enabled` | `false` | MKT-035: E6 down-day call-only. **DISABLED** |
| `conditional_e7_enabled` | `false` | MKT-035: E7. **DISABLED in v1.19.0** |
| `conditional_upday_e6_enabled` | `true` | Upday-035: E6 fires as put-only on up days (v1.19.0) |
| `upday_threshold_pct` | `0.002` | Upday-035: SPX must rise this % above session open (0.20%, v1.19.0) |

### Whipsaw Filter (`config.whipsaw_filter`, v1.19.0)

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable/disable whipsaw filter |
| `threshold` | `1.75` | Skip entry when intraday range > 1.75× expected move |

### VIX Regime (`config.vix_regime`)

VIX regime controls dynamic behavior adjustments based on current VIX level. When enabled, the bot adapts entry count and skip behavior to market volatility conditions.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable/disable VIX regime adjustments |
| `low_threshold` | `14.0` | VIX below this = low regime (calm markets) |
| `high_threshold` | `22.0` | VIX above this = high regime (elevated volatility) |
| `high_max_entries` | `2` | Max entries in high VIX regime (reduce exposure) |

| VIX Regime | VIX Range | Behavior |
|------------|-----------|----------|
| Low | < 14 | Normal entries, wider OTM, higher premium capture |
| Medium | 14-22 | Normal entries, standard parameters |
| High | > 22 | Reduced entries (max 2), wider spreads, more conservative |

### Skip Weekdays (`config.skip_weekdays`)

| Key | Default | Description |
|-----|---------|-------------|
| `skip_weekdays` | `[]` | List of weekday names to skip trading (e.g., `["Monday", "Friday"]`). Empty = trade all days. |

### Day-of-Week Max Entries (`config.dow_max_entries`)

| Key | Default | Description |
|-----|---------|-------------|
| `dow_max_entries` | `{}` | Map of weekday name to max entries (e.g., `{"Monday": 2, "Friday": 2}`). Empty = use default entry count for all days. |

### Filters (`config.filters`)

| Key | Default | Description |
|-----|---------|-------------|
| `fomc_blackout` | `true` | Skip trading on FOMC announcement days |
| `fomc_announcement_skip` | `false` | MKT-008: Skip all entries on FOMC announcement day. **Changed to `false` in v1.19.0** — backtest showed FOMC days are profitable |

---

## Known Limitations & Edge Cases

### EMA Lag

EMAs are lagging indicators. On Feb 17, a V-shaped reversal generated BEARISH at the first entry (correct for the first move) then BULLISH later (correct for the reversal). Since v1.4.0, the EMA signal is informational only (all entries are full ICs), so lag only affects the logged signal — not entry type.

### Volatility Skew

Put premiums are typically 2-7× higher than call premiums at the same delta. This means:
- MKT-024 starts calls at 3.5× and puts at 4.0× base OTM to give MKT-020/022 room to find optimal strikes
- MKT-011 uses separate thresholds: calls $2.00, puts $2.75, with MKT-029 graduated fallback (call floor $0.75, put floor $2.00)
- MKT-020 call tightening reaches $2.00 (or $0.75 at MKT-029 floor) → fewer MKT-011 skips/conversions
- MKT-022 with $2.75 put minimum forces closer-to-ATM puts
- Total_credit stop (shared by both sides) is adequate because MKT-020/022 keeps skew at 1-2x

### Saxo Position Merging

Saxo merges positions at the same strike and direction into a single position, deleting the older position ID. This breaks position tracking. MKT-013 (shorts) and MKT-015 (longs) prevent this, but the strike adjustment pipeline adds 5pt offsets that can accumulate across entries.

### 0DTE Gamma Risk

0DTE options have extreme gamma. Cushion can evaporate in minutes:
- Feb 24 Entry #3: 64% → 6% call cushion in 2 minutes
- Feb 17: 3 call stops in 11 minutes during a sharp rally

### Settlement Timing

0DTE options settle between 4:00 PM and 2:00 AM ET. The bot checks for settlement after market close. Fix #82 prevents the midnight reset from locking the settlement gate. Fix #77 ensures expired credits are processed even when the position registry is empty.

### One-Sided Entry Risk

One-sided entries (put-only via MKT-011/MKT-032/MKT-039, call-only via MKT-035/MKT-038/MKT-040) that get stopped lose the full credit plus commission. MKT-039 put-only stop = credit + put_stop_buffer ($1.55). Call-only (MKT-040) uses call + theo $2.60 put + call buffer (unified with MKT-035/038). Historical context: trend-driven one-sided entries were removed in v1.4.0, then credit-driven put-only was re-enabled in v1.7.1 and call-only added in v1.15.1.

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

### State File Fields (v1.19.0)

**Top-level fields added:**

| Field | Type | Description |
|-------|------|-------------|
| `entry_schedule.base` | `string[]` | Scheduled base entry times (e.g., `["10:15","10:45","11:15"]`) |
| `entry_schedule.conditional` | `string[]` | Scheduled conditional entry times (e.g., `["14:00"]`) |

**Per-entry fields added:**

| Field | Type | Description |
|-------|------|-------------|
| `skip_reason` | `string` | Human-readable skip reason (empty if not skipped). Set when both sides are skipped. |

### Skip Alert Behavior (v1.16.0)

When an entry is skipped, the bot: (1) records a minimal `HydraIronCondorEntry` with `is_complete=True`, both sides flagged as skipped, and `skip_reason` set, (2) sends a Telegram `ENTRY_SKIPPED` alert (LOW priority, Telegram-only) with the reason and context.

**Skip reasons by path:**

| Path | Reason | Alert |
|------|--------|-------|
| Margin insufficient | `"Insufficient margin"` | No (existing HIGH alert) |
| MKT-035 not triggered | `"MKT-035: SPX not down enough for conditional entry"` | Yes |
| MKT-035 call non-viable | `"MKT-035: call credit non-viable ($X.XX < $0.75)"` | Yes |
| MKT-038 call non-viable | `"MKT-038: call credit non-viable on FOMC T+1 ($X.XX < $0.75)"` | Yes |
| MKT-011 both non-viable | `"MKT-011: both sides below minimum credit (call $X.XX, put $X.XX)"` | Yes |
| MKT-032 VIX gate | `"MKT-032: VIX X.X too high for put-only (max 15.0)"` | Yes |
| MKT-010 one wing illiquid | `"MKT-010: [call/put] wings illiquid"` | Yes |
| MKT-010 both illiquid | `"MKT-010: both wings illiquid"` | Yes |

Skipped entries are inert — zero credits/strikes, `is_complete=True`, no P&L impact, no stop monitoring, no settlement processing.
