# HYDRA Strategy Context (Shared)

**Shared by:** APOLLO, HERMES, HOMER, CLIO
**Last updated:** 2026-04-19
**HYDRA version:** v1.23.0 (deployed 2026-04-19 — Downday-035 + MKT-045 + MKT-046)
**Schema version:** v7
**Source of truth:** `bots/hydra/config/config.json` on VM

This file is the single source of truth for HYDRA strategy parameters across all Claude API agents. When HYDRA's strategy or config changes, update this file ONCE and all agents automatically pick up the change.

---

## Entry Schedule (effective — 2026-04-17 onwards)

Today the bot fires up to **3 entries per day**. The canonical 10:15 slot is
always dropped by the VIX regime cap (`max_entries: [2, 2, 2, 1]` at all VIX
levels), so the first entry that actually fires is at 10:45.

**Current live numbering (what the code emits):**
- **Entry #1 — 10:45 ET** (first base slot that fires)
- **Entry #2 — 11:15 ET** (second base slot)
- **Entry #3 — 14:00 ET** (conditional — fires as **put-only** when SPX rises
  ≥0.25% above open [**Upday-035**] OR as **call-only** when SPX drops ≥0.25%
  below open [**Downday-035**, deployed 2026-04-19])

**At VIX ≥28 (regime 3):** only Entry #2 (11:15) fires from the base schedule,
plus Entry #3 conditional — so 2 entries max on extreme-VIX days.

**E7:** DISABLED.

**Historical note for reading old records (pre-2026-04-17):**
Before Apr 17 2026, the VIX regime dropped E#1 only at high VIX. On low-VIX
days before that date, the 10:15 slot also fired, so historical records may
have `entry_number=1` at 10:15 and `entry_number=2` at 10:45. When reading
a historical row: use `entry_time` as the authoritative slot identifier, not
`entry_number` alone. Also note the old docs/backtests referred to the
14:00 conditional as "E6" — that's the same slot as today's Entry #3.

**Historical per-slot performance (37 reliable days, Feb 10 - Apr 10, 2026,
using the old 10:15-inclusive numbering):**
- 10:15 slot: 24% WR, -$79/entry — **WORST** (auto-dropped since 2026-04-17)
- 10:45 slot: 35% WR, -$39/entry (now Entry #1)
- 11:15 slot: 42% WR, -$14/entry — **BEST** (now Entry #2; preserved at all VIX levels)

---

## VIX Regime Adaptive (tuned 2026-04-17)

**Breakpoints:** `[18.0, 22.0, 28.0]` — creates 4 regimes. All regime credit slots are now filled (previously zones 0 & 1 were null), so the base `min_viable_credit_per_side` ($2.00) and `min_viable_credit_put_side` ($2.75) are effectively dead. **E#1 (10:15) is now dropped at ALL VIX levels** (max_entries `[2, 2, 2, 1]`, changed 2026-04-17).

| Regime | VIX Range | Entries | Slots Kept | Call Min | Put Min | Effective Floors (call / put) |
|--------|-----------|---------|------------|----------|---------|-------------------------------|
| 0 | <18 (calm) | **2** | **10:45 (#1), 11:15 (#2)** | **$1.00** | **$1.25** | $0.90 / $1.15 |
| 1 | 18-22 | **2** | **10:45 (#1), 11:15 (#2)** | **$0.50** | **$0.75** | $0.40 / $0.65 |
| 2 | 22-28 | **2** | **10:45 (#1), 11:15 (#2)** | **$0.30** | **$0.50** | $0.20 / $0.40 |
| 3 | ≥28 (extreme) | **1** | **11:15 only (#2)** | **$0.30** | **$0.40** | $0.20 / $0.30 |

When the regime applies, `call_credit_floor` / `put_credit_floor` are recomputed to `min_credit − $0.10`; the config-level floors ($0.20 / $0.30) are only used if `vix_regime.enabled = false`.

**Code behavior:** When max_entries caps below base count, drops EARLIEST entries (keeps best-performing 11:15 slot — live Entry #2 at 2-cap, live Entry #1 at 1-cap). Was previously the opposite (kept earliest) — fixed 2026-04-13 in `strategy.py::_apply_vix_regime_overrides()`.

**Shadow OTM targets** (per regime, for v7 shadow_entries observation): call `[40, 50, 75, 75]`pt, put `[50, 75, 110, 90]`pt.

---

## Core MKT Rules — ACTIVE

- **MKT-011:** Credit gate — thresholds come from VIX regime above at every VIX level; skip entry if below
- **MKT-020/022:** Progressive OTM tightening — scans 5pt inward until credit met (floor: 25pt OTM)
- **MKT-024:** Starting OTM 2.5× call, 2.75× put (VIX-adjusted distance), hard-clamped to 180pt (tuned 2026-04-30; was 3.5×/4.0× at 240pt)
- **MKT-027:** Spread width `round(VIX × 6.0 / 5) × 5`, 25-110pt range
- **MKT-029:** Graduated fallback — floor = `min_credit − $0.10` (regime-dependent; e.g. at VIX<18: $0.90 call / $1.15 put)
- **MKT-032/MKT-039:** Put-only when call non-viable AND VIX <15 (`put_only_max_vix: 15.0`)
- **MKT-040:** Call-only fallback when put non-viable (with retries then theoretical put buffer), 89% WR
- **FOMC T+1 BLACKOUT (2026-04-19):** Skip ALL entries on the day after an FOMC announcement. `fomc_t1_skip_enabled: true` on VM. Supersedes MKT-038 call-only (which was negative EV per A/B backtest). Next T+1: Apr 30.
- **MKT-038:** DISABLED — FOMC T+1 call-only force. Was forcing call-only on T+1; A/B backtest showed −$425 over 9 T+1 days vs trade-normal. Code preserved as fallback if `fomc_t1_skip_enabled` is disabled.
- **MKT-042:** Buffer decay — starts at 2.5× normal, linearly decays to 1× over 4 hours
- **MKT-043:** Calm entry filter — delays entry up to 5min if SPX moved >15pt in last 3min
- **Base-downday call-only:** DISABLED (`base_entry_downday_callonly_pct: null`). Removed 2026-04-19 after A/B threshold sweep showed negative EV at all values 0.57%-1.20% over Feb-Apr 2026 (incl. multiple sustained sell-offs). Base entries E2/E3 now always attempt full ICs; MKT-011/MKT-040 handle put-uninvestable days.
- **Upday-035 (Entry #3 / 14:00):** Put-only at 14:00 when SPX rises ≥0.25% above open (`upday_threshold_pct: 0.0025`, override_reason `upday-035`). Docs refer to this slot as "E6" historically; today it's Entry #3.
- **Downday-035 (Entry #3 / 14:00):** Call-only at 14:00 when SPX drops ≥0.25% below open (`conditional_downday_threshold_pct: 0.0025`, override_reason `downday-035`, deployed 2026-04-19). Stop = `call_credit + $2.60 theo put + call_stop_buffer` (same as MKT-035/038/040)
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
- Call side stop = `total_credit + call_stop_buffer`
- Put side stop = `total_credit + put_stop_buffer`

**Stop buffers — Option B per-VIX-regime (deployed 2026-04-27):**

| VIX Zone | Range | call_stop_buffer | put_stop_buffer | Rationale |
|---|---|---|---|---|
| Z0 | <18 | `$0.75` (global) | `$1.75` (global) | Insufficient sample (n=5 days) — fall back to global |
| **Z1** | **18-22** | **`$1.50`** | **`$2.50`** | Wider both — calm regime, most stops are noise |
| **Z2** | **22-28** | **`$1.00`** | **`$1.50`** | Wider call, TIGHTER put — stress regime, fast exit beats cushion |
| Z3 | ≥28 | `$0.75` (global) | `$1.75` (global) | Zero entries in study — fall back to global |

Override applied **once per day at first entry** via `_apply_vix_regime_overrides()` based on VIX at open. Decision rationale + 4-week review triggers in `docs/HYDRA_BUFFER_OPTIMIZATION.md`.

**One-sided entries:**
- Call-only (MKT-035/038/040): `call_credit + $2.60 theoretical_put + call_stop_buffer` (regime-conditioned)
- Put-only (MKT-039, E6): `put_credit + put_stop_buffer` (regime-conditioned)

**Buffer decay (MKT-042):** Applied to above formulas. Effective buffer starts at 2.50× normal at entry, linearly decays to 1× over 4 hours.

**Close mode:** Both legs closed via market order (default). Configurable `short_only_stop` for MKT-025 short-only close + MKT-033 long salvage.

---

## 2026 FOMC Calendar

HYDRA's FOMC handling (updated 2026-04-19 after A/B backtest over 2025-01 → 2026-04):
- **Day 1**: trade normally (+$430 over 10 days in backtest — don't skip)
- **Day 2** (announcement, 2 PM): trade normally (+$230 over 10 days — coin flip with slight positive tilt)
- **T+1** (day after): **SKIP ALL ENTRIES** in LIVE mode (skipping = $0 vs trade-normal −$900 vs MKT-038 ON −$1,325)

**Important DRY-RUN exception (2026-04-30):** when `dry_run_force_normal_day: true` is set in the bot config AND the bot is in dry-run mode (`dry_run: true`), all FOMC date-based skips are runtime-bypassed: `fomc_t1_skip_enabled` is honored as configured for live but ignored in dry mode, the Day-2 announcement skip (`fomc_announcement_skip`) is ignored, and MKT-038 (`fomc_t1_callonly_enabled`) is ignored. Reason: variant-comparison and dry-run experiments need a full 252-day-per-year sample. If you observe entries placed on what the calendar says is T+1 in DRY-RUN data, that is **expected behavior, not a contradiction** — check the `<data source="bot_mode">` block in HOMER's prompt to confirm the dry-run + force-normal-day flags are set.

| Meeting | Day 1 (TRADE) | Day 2 / Announcement (TRADE) | T+1 (SKIP — blackout) |
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

- ~$52,000 margin available (varies)
- Contracts per entry: configurable (`contracts_per_entry` in config). Default 1,
  but supported up to the account's margin limit. When checking today's data,
  ALWAYS read the actual contract count from the daily summary / state file /
  cheat_sheet rather than assuming 1. Per-contract normalization is required
  for apples-to-apples comparisons when history spans multiple contract counts.

---

## Recent Changes (2026-04-13 through 2026-04-19 deployments)

1. **VIX regime breakpoints:** `[14, 20, 30]` → `[18, 22, 28]` (2026-04-13)
2. **`max_entries` per regime:** `[2, null, null, 1]` → `[null, 2, 2, 1]` (drops E#1 at VIX≥18, 2026-04-13) → **`[2, 2, 2, 1]` (drops E#1 at ALL VIX levels, 2026-04-17)**. Reason: E#1 analysis showed 24% WR, -$79/entry avg — worst slot at every VIX regime. At VIX<18 specifically: 80-100% stop rate in recent data.
3. **Per-regime credit thresholds (2026-04-13 initial → 2026-04-14 tuned):** `min_call_credit: [null, null, 0.75, 0.50]` → **`[1.00, 0.50, 0.30, 0.30]`**; `min_put_credit: [null, null, 1.25, 0.75]` → **`[1.25, 0.75, 0.50, 0.40]`**. All slots now filled — base $2.00 / $2.75 are effectively dead.
4. **Credit floors:** `call_credit_floor: 0.75` → **`0.20`**, `put_credit_floor: 2.00` → **`0.30`** (but when regime is active, floor = `min_credit − $0.10`).
5. **Stop buffers (2026-04-14 baseline; 2026-04-27 Option B per-VIX-regime overrides on top):** Global fallback unchanged: `call_stop_buffer: 0.75` (was 0.35), `put_stop_buffer: 1.75` (was 1.55). Buffer decay `start_mult 2.50` over `4.0 hours` (was 2.10 / 2h). **Option B (2026-04-27)** populates `vix_regime.call_stop_buffer = [null, 1.50, 1.00, null]` and `vix_regime.put_stop_buffer = [null, 2.50, 1.50, null]` — Zone 0/3 use global fallback, Zone 1 widens both, Zone 2 widens call but tightens put. Override applied once per day at first entry.
6. **MKT-045 chain strike snapping (2026-04-17):** After overlap adjustments (MKT-013/015, Fix #44/#66), snaps all 4 strikes to nearest actual Saxo chain strike (max 25pt tolerance). Far-OTM 0DTE strikes use 10-25pt intervals, not 5pt. Prevents entries being skipped due to non-existent strikes.
7. **MKT-046 stop anti-spike filter (2026-04-17):** When MKT-036 is disabled, requires stop breach to persist for 10 seconds before executing. Filters momentary bid/ask spikes that inflate mid-price. Verified April 17 filtered 1 false stop on E#1 before executing confirmed stop.
8. **Code fix (strategy.py `_apply_vix_regime_overrides()`):** When capping, drops EARLIEST entries (was LAST)
9. **Added schema v7 `shadow_entries` table:** OTM-based counterfactual logging
10. **Downday-035 conditional E6 (2026-04-19):** Mirror of Upday-035 for down days. When SPX drops ≥0.25% below open at 14:00, fires call-only spread (config `conditional_downday_e6_enabled: true`, `conditional_downday_threshold_pct: 0.0025`). Backtest (Feb 10 - Apr 10): 11 triggers, 91% WR, +$1,295 P&L delta vs Upday-only baseline. Override reason: `downday-035`.
11. **Base-entry down-day call-only DISABLED (2026-04-19):** `base_entry_downday_callonly_pct: null`. A/B threshold sweep (0.57% / 0.70% / 0.80% / 1.00% / 1.20%) over Feb 10 - Apr 10 2026 — a period with 19/42 days ≥0.57% intraday drop, worst single day −1.88%, worst 3-day −3.78%, cumulative −2.25% — showed negative EV at every threshold. Mechanism: mean-reverting drops forfeit put-side profit; continuing drops stop the call side too (so conversion doesn't actually limit risk). MKT-040 + MKT-011 credit gate handle put-uninvestable cases more surgically.
12. **FOMC T+1 BLACKOUT / MKT-038 DISABLED (2026-04-19):** `fomc_t1_skip_enabled: true`, `fomc_t1_callonly_enabled: false`. A/B backtest over 2025-01 → 2026-04 (9 T+1 days, using VM-matching config with E#1 drop, [18,22,28] VIX regime, regime credit floors): trade-normal = −$900, MKT-038 ON (call-only force) = −$1,325, skip entirely = $0. Day 1 and Day 2 backtest showed both are winners (trade normally), so T+1 is the only FOMC day that flips to skip. Also caught a **stale backtest config bug**: `live_config()` had `vix_regime_breakpoints=[14,20,30]` and `max_entries=[2,None,None,1]` (outdated) — fixed to match VM. Prior MKT-038 backtest returned +$940 false-positive; corrected to −$425.
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

E#1 at 10:15 is **always dropped** at every VIX level since 2026-04-17 (max_entries `[2,2,2,1]`). Expected live entry count: **2 base (10:45 + 11:15)** at VIX <28, or **1 base (11:15 only)** at VIX ≥28, plus up to 1 conditional E6 at 14:00 if Upday-035 or Downday-035 triggers. On FOMC T+1 days in **LIVE mode**: **0 entries** (blackout). On FOMC T+1 days in **DRY-RUN mode with `dry_run_force_normal_day: true`**: full normal entry schedule fires (the date-based skip is bypassed for data-collection purposes — see "Important DRY-RUN exception" above). If observations diverge from this, check `vix_open`, the VIX-regime config, AND the `<data source="bot_mode">` block.
