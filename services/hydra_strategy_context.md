# HYDRA Strategy Context (Shared)

**Shared by:** APOLLO, HERMES, HOMER, CLIO
**Last updated:** 2026-04-19
**HYDRA version:** v1.23.0 (deployed 2026-04-19 — Downday-035 + MKT-045 + MKT-046)
**Schema version:** v7
**Source of truth:** `bots/hydra/config/config.json` on VM

This file is the single source of truth for HYDRA strategy parameters across all Claude API agents. When HYDRA's strategy or config changes, update this file ONCE and all agents automatically pick up the change.

---

## Entry Schedule

**Base entries:** 10:15, 10:45, 11:15 ET (up to 3 slots — but **E#1 at 10:15 is dropped at ALL VIX levels**, so effectively only 10:45 and 11:15 fire)
**Conditional entry:** 14:00 ET (E6 — fires as **put-only** when SPX rises ≥0.25% above open [**Upday-035**] OR as **call-only** when SPX drops ≥0.25% below open [**Downday-035**, deployed 2026-04-19])
**E7:** DISABLED

**Historical per-slot performance (37 reliable days, Feb 10 - Apr 10, 2026):**
- E#1 (10:15): 24% WR, -$79/entry — **WORST** (auto-dropped at ALL VIX levels as of 2026-04-17)
- E#2 (10:45): 35% WR, -$39/entry
- E#3 (11:15): 42% WR, -$14/entry — **BEST** slot (preserved at all VIX levels)

---

## VIX Regime Adaptive (tuned 2026-04-17)

**Breakpoints:** `[18.0, 22.0, 28.0]` — creates 4 regimes. All regime credit slots are now filled (previously zones 0 & 1 were null), so the base `min_viable_credit_per_side` ($2.00) and `min_viable_credit_put_side` ($2.75) are effectively dead. **E#1 (10:15) is now dropped at ALL VIX levels** (max_entries `[2, 2, 2, 1]`, changed 2026-04-17).

| Regime | VIX Range | Entries | Slots Kept | Call Min | Put Min | Effective Floors (call / put) |
|--------|-----------|---------|------------|----------|---------|-------------------------------|
| 0 | <18 (calm) | **2** | **10:45, 11:15** (drops E#1) | **$1.00** | **$1.25** | $0.90 / $1.15 |
| 1 | 18-22 | **2** | **10:45, 11:15** (drops E#1) | **$0.50** | **$0.75** | $0.40 / $0.65 |
| 2 | 22-28 | **2** | **10:45, 11:15** (drops E#1) | **$0.30** | **$0.50** | $0.20 / $0.40 |
| 3 | ≥28 (extreme) | **1** | **11:15 only** (E#3) | **$0.30** | **$0.40** | $0.20 / $0.30 |

When the regime applies, `call_credit_floor` / `put_credit_floor` are recomputed to `min_credit − $0.10`; the config-level floors ($0.20 / $0.30) are only used if `vix_regime.enabled = false`.

**Code behavior:** When max_entries caps below base count, drops EARLIEST entries (keeps best-performing E#3 slot). Was previously the opposite (kept earliest) — fixed 2026-04-13 in `strategy.py::_apply_vix_regime_overrides()`.

**Shadow OTM targets** (per regime, for v7 shadow_entries observation): call `[40, 50, 75, 75]`pt, put `[50, 75, 110, 90]`pt.

---

## Core MKT Rules — ACTIVE

- **MKT-011:** Credit gate — thresholds come from VIX regime above at every VIX level; skip entry if below
- **MKT-020/022:** Progressive OTM tightening — scans 5pt inward until credit met (floor: 25pt OTM)
- **MKT-024:** Starting OTM 3.5× call, 4.0× put (VIX-adjusted distance)
- **MKT-027:** Spread width `round(VIX × 6.0 / 5) × 5`, 25-110pt range
- **MKT-029:** Graduated fallback — floor = `min_credit − $0.10` (regime-dependent; e.g. at VIX<18: $0.90 call / $1.15 put)
- **MKT-032/MKT-039:** Put-only when call non-viable AND VIX <15 (`put_only_max_vix: 15.0`)
- **MKT-040:** Call-only fallback when put non-viable (with retries then theoretical put buffer), 89% WR
- **MKT-038:** FOMC T+1 call-only (day after FOMC announcement forced call-only)
- **MKT-042:** Buffer decay — starts at 2.5× normal, linearly decays to 1× over 4 hours
- **MKT-043:** Calm entry filter — delays entry up to 5min if SPX moved >15pt in last 3min
- **Base-downday call-only:** DISABLED (`base_entry_downday_callonly_pct: null`). Removed 2026-04-19 after A/B threshold sweep showed negative EV at all values 0.57%-1.20% over Feb-Apr 2026 (incl. multiple sustained sell-offs). Base entries E2/E3 now always attempt full ICs; MKT-011/MKT-040 handle put-uninvestable days.
- **Upday-035 (E6):** Put-only at 14:00 when SPX rises ≥0.25% above open (`upday_threshold_pct: 0.0025`, override_reason `upday-035`)
- **Downday-035 (E6):** Call-only at 14:00 when SPX drops ≥0.25% below open (`conditional_downday_threshold_pct: 0.0025`, override_reason `downday-035`, deployed 2026-04-19). Stop = `call_credit + $2.60 theo put + call_stop_buffer` (same as MKT-035/038/040)
- **Whipsaw filter:** Skip entry if intraday range > 1.75× expected move (`whipsaw_range_skip_mult: 1.75`)
- **MKT-045:** Chain strike snap — after all overlap adjustments, snaps strikes to actual Saxo chain (far OTM uses 10-25pt intervals, not 5pt)
- **MKT-046:** Stop anti-spike filter — breach must persist 10 seconds before executing. Filters momentary bid/ask spikes that inflate mid-price. Logs full bid/ask on every breach event (`STOP-DETAIL`).

## Core MKT Rules — DISABLED (code preserved but dormant)

- **MKT-008:** FOMC announcement skip (now trades FOMC days)
- **MKT-018:** Early close (hold-to-expiry outperforms per backtest)
- **MKT-031:** Smart entry windows (enter at scheduled times only, no scouting)
- **MKT-034:** VIX time shifting (disabled 2026-03-05)
- **MKT-036:** Stop confirmation timer 75s (MKT-046's 10s timer active instead)
- **MKT-041:** Cushion recovery exit (interferes with buffer decay)
- **E7 conditional:** Down-day call-only E7 disabled (E6 only)

---

## Stop Formula

**Full Iron Condor (both sides placed):**
- Call side stop = `total_credit + call_stop_buffer` ($0.75 default)
- Put side stop = `total_credit + put_stop_buffer` ($1.75 default)

**One-sided entries:**
- Call-only (MKT-035/038/040): `call_credit + $2.60 theoretical_put + call_stop_buffer`
- Put-only (MKT-039, E6): `put_credit + put_stop_buffer`

**Buffer decay (MKT-042):** Applied to above formulas. Effective buffer starts at 2.50× normal at entry, linearly decays to 1× over 4 hours.

**Close mode:** Both legs closed via market order (default). Configurable `short_only_stop` for MKT-025 short-only close + MKT-033 long salvage.

---

## 2026 FOMC Calendar

HYDRA trades on FOMC Day 1 (as of v1.14.0+). Day 2 (announcement) is skipped (MKT-008 trading halt). T+1 forced call-only (MKT-038).

| Meeting | Day 1 (trade) | Day 2 / Announcement (skip) | T+1 (call-only MKT-038) |
|---------|---------------|----------------------------|-------------------------|
| Jan | Jan 27 Tue | Jan 28 Wed | Jan 29 Thu |
| Mar | Mar 17 Tue | Mar 18 Wed | Mar 19 Thu |
| Apr | Apr 28 Tue | Apr 29 Wed | Apr 30 Thu |
| Jun | Jun 16 Tue | Jun 17 Wed | Jun 18 Thu |
| Jul | Jul 28 Tue | Jul 29 Wed | Jul 30 Thu |
| Sep | Sep 15 Tue | Sep 16 Wed | Sep 17 Thu |
| Oct | Oct 27 Tue | Oct 28 Wed | Oct 29 Thu |
| Dec | Dec 8 Tue | Dec 9 Wed | Dec 10 Thu |

Cross-reference today's date against this table — if not listed, it is NOT an FOMC day.

---

## Schema v7: `shadow_entries` Table

Records what OTM-based selection WOULD have placed alongside actual credit-based selection. Per-regime OTM targets `[50, 65, 85, 120]`pt each side.

**Purpose:** Observation-only counterfactual logging. Does NOT affect trading behavior. Enables retroactive analysis: would pure OTM targeting outperform credit-based scanning?

**Columns include:** date, entry_number, entry_time, spx_at_entry, vix_at_entry, vix_regime, shadow_call_otm_target, shadow_put_otm_target, shadow_short_call_strike, shadow_long_call_strike, shadow_short_put_strike, shadow_long_put_strike, actual_short_call_strike, actual_short_put_strike, actual_call_credit, actual_put_credit, is_skipped.

---

## Key Historical Findings (Feb 10 - Apr 10, 2026)

- **80-100pt OTM calls**: 7% stop rate, +$48/entry (13 entries, VIX 25-28 period)
- **40-60pt OTM calls** (typical bot zone): 36% stop rate, -$9/entry
- **60-80pt OTM calls** (danger zone): 50% stop rate, -$128/entry — worst bucket
- **Entry #1 (10:15)** at VIX 22-25: 0% WR, -$202/entry — dropped in new regime
- **ThetaData vs Saxo backtest**: backtest ~65% optimistic vs live (rough rule: live ≈ 34% of backtest)
- **Feb 10-27 winning period**: 40-60pt OTM with 5% call stop rate — worked in low VIX (avg 18.9)
- **Mar 16-31 drawdown**: same OTM distance, high VIX (avg 25.5), 44% call stop rate

---

## Account

- $35,000 margin
- 1 contract per entry

---

## Recent Changes (2026-04-13 through 2026-04-19 deployments)

1. **VIX regime breakpoints:** `[14, 20, 30]` → `[18, 22, 28]` (2026-04-13)
2. **`max_entries` per regime:** `[2, null, null, 1]` → `[null, 2, 2, 1]` (drops E#1 at VIX≥18, 2026-04-13) → **`[2, 2, 2, 1]` (drops E#1 at ALL VIX levels, 2026-04-17)**. Reason: E#1 analysis showed 24% WR, -$79/entry avg — worst slot at every VIX regime. At VIX<18 specifically: 80-100% stop rate in recent data.
3. **Per-regime credit thresholds (2026-04-13 initial → 2026-04-14 tuned):** `min_call_credit: [null, null, 0.75, 0.50]` → **`[1.00, 0.50, 0.30, 0.30]`**; `min_put_credit: [null, null, 1.25, 0.75]` → **`[1.25, 0.75, 0.50, 0.40]`**. All slots now filled — base $2.00 / $2.75 are effectively dead.
4. **Credit floors:** `call_credit_floor: 0.75` → **`0.20`**, `put_credit_floor: 2.00` → **`0.30`** (but when regime is active, floor = `min_credit − $0.10`).
5. **Stop buffers (2026-04-14 tuning carried forward):** `call_stop_buffer: 0.75` (was 0.35), `put_stop_buffer: 1.75` (was 1.55). Buffer decay `start_mult 2.50` over `4.0 hours` (was 2.10 / 2h).
6. **MKT-045 chain strike snapping (2026-04-17):** After overlap adjustments (MKT-013/015, Fix #44/#66), snaps all 4 strikes to nearest actual Saxo chain strike (max 25pt tolerance). Far-OTM 0DTE strikes use 10-25pt intervals, not 5pt. Prevents entries being skipped due to non-existent strikes.
7. **MKT-046 stop anti-spike filter (2026-04-17):** When MKT-036 is disabled, requires stop breach to persist for 10 seconds before executing. Filters momentary bid/ask spikes that inflate mid-price. Verified April 17 filtered 1 false stop on E#1 before executing confirmed stop.
8. **Code fix (strategy.py `_apply_vix_regime_overrides()`):** When capping, drops EARLIEST entries (was LAST)
9. **Added schema v7 `shadow_entries` table:** OTM-based counterfactual logging
10. **Downday-035 conditional E6 (2026-04-19):** Mirror of Upday-035 for down days. When SPX drops ≥0.25% below open at 14:00, fires call-only spread (config `conditional_downday_e6_enabled: true`, `conditional_downday_threshold_pct: 0.0025`). Backtest (Feb 10 - Apr 10): 11 triggers, 91% WR, +$1,295 P&L delta vs Upday-only baseline. Override reason: `downday-035`.
11. **Base-entry down-day call-only DISABLED (2026-04-19):** `base_entry_downday_callonly_pct: null`. A/B threshold sweep (0.57% / 0.70% / 0.80% / 1.00% / 1.20%) over Feb 10 - Apr 10 2026 — a period with 19/42 days ≥0.57% intraday drop, worst single day −1.88%, worst 3-day −3.78%, cumulative −2.25% — showed negative EV at every threshold. Mechanism: mean-reverting drops forfeit put-side profit; continuing drops stop the call side too (so conversion doesn't actually limit risk). MKT-040 + MKT-011 credit gate handle put-uninvestable cases more surgically.
12. **Schema v6 bid/ask capture in `spread_snapshots`:** Saxo quotes per leg during monitoring
13. **Fixed Haiku-introduced threshold unit bug:** `downday_threshold_pct` (0.3 vs 0.003) in backtest engine

---

## Skip Patterns — Entry-Level Analysis

**Entry #1 (10:15) at VIX ≥18: AUTO-DROPPED** (not a skip — regime design per 2026-04-13 update). Historical performance: 24% WR, -$79/entry average.

**Entries #2 and #3:** May be skipped due to:
- MKT-011 credit gate (insufficient credit at scan end)
- MKT-010 illiquidity (both wings illiquid — rare, <1/year)
- MKT-032 VIX gate blocking put-only fallback
- Whipsaw filter (intraday range > 1.75× expected)

**E6 (14:00) conditional:** Fires only if SPX up ≥0.25% at 14:00 open. Skips logged as "Conditional: no up-day trigger".

If agents observe **fewer than 3 base entries** on a VIX≥18 day, that is by design (regime drop), NOT a bug or skip. Check `vix_open` and regime config to determine expected entry count.
