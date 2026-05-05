"""
HYDRA 0DTE Trading Bot

Multi-Entry Iron Condors (SPX 0DTE) with credit gates, progressive OTM
tightening, and hold-to-expiry. Based on Tammy Chambless's MEIC strategy.

Before each entry, checks 20 EMA vs 40 EMA on SPX 1-minute bars.
The EMA signal (BULLISH/BEARISH/NEUTRAL) is logged and stored for analysis
but is informational only — base entries are full iron condors or put-only via MKT-011.

Credit Gate (MKT-011): Before placing orders, estimates credit from quotes.
MKT-029 graduated fallback for BOTH sides: -$0.05, -$0.10 (call floor $0.75, put floor $2.00).
MKT-035/MKT-038 call-only entries also use MKT-029 call floor ($0.75).
- Both sides viable: Proceed with full iron condor
- Call non-viable, put viable, VIX < 15.0: Place put-only entry (MKT-032/MKT-039 VIX gate)
- Call non-viable, put viable, VIX >= 15.0: Skip entry (no call hedge in volatile conditions)
- Put non-viable, call viable: Retry with tighter put strikes (5pt closer, max 2 retries), then call-only entry (MKT-040, v1.15.1)
- Both non-viable: Skip entry entirely

Conditional Entry Trigger (MKT-035 / Upday-035 / Downday-035):
- Base-entry down-day call-only DISABLED (2026-04-19, base_entry_downday_callonly_pct=null — negative EV in A/B sweep)
- Conditional entry E6 (14:00): fires as put-only when SPX rises >= 0.25% above session open (Upday-035)
  OR call-only when SPX drops >= 0.25% below session open (Downday-035, deployed 2026-04-19)
  Stop = credit + side_stop_buffer (see Option B per-VIX-regime values below)
- E7: DISABLED

Stop Buffers (Option B per-VIX-regime, deployed 2026-04-27):
- Global fallback: call_stop_buffer $0.75, put_stop_buffer $1.75
- Zone 0 (VIX<18) and Zone 3 (VIX>=28): null — fall back to global values
- Zone 1 (VIX 18-22): call $1.50, put $2.50 (wider both — calm regime)
- Zone 2 (VIX 22-28): call $1.00, put $1.50 (wider call, TIGHTER put — stress regime)
- See docs/HYDRA_BUFFER_OPTIMIZATION.md for the 28-day Saxo study + forward-looking review triggers

Version History:
- 1.27.2 (2026-05-05 PM): Hedge position tracking for the defensive overlay.
  Closes the dry-run gap where overlay placements were logged but not journaled.
  New `bots/hydra/brandon/hedge_position.py` module: HedgeLeg dataclass,
  Black-Scholes call/put pricers, per-leg P&L at expiry (long: +(intrinsic −
  fill); short: +(fill − intrinsic), × 100 × quantity), aggregated debit-spread
  and butterfly payoff math. `BrandonHydraStrategy._brandon_place_overlay` now
  creates HedgeLeg objects with synthetic DRY_OVERLAY_* position ids and
  Black-Scholes-estimated fill prices (default IV 0.18 for SPX 0DTE) and stores
  them on `self._brandon_hedge_legs[entry_number]`. New
  `_brandon_settle_hedges(spx_settle)` runs at log_daily_summary time, computes
  per-hedge net P&L vs SPX_close, fires per-hedge `BRANDON-OVERLAY-SETTLED`
  Telegram + a day-aggregate `BRANDON-OVERLAY-DAY` Telegram. Idempotent within
  the day. New override `log_daily_summary` calls settlement before parent
  super(). Reset in `_reset_for_new_day`. Tests: 166 passing (was 138). Added:
  `tests/test_brandon_hedge_position.py` (23 tests covering BS pricing,
  per-leg P&L, debit-spread payoff at all three regions, butterfly payoff
  including symmetric +/-100pt sanity check, settle_hedge aggregation),
  `TestOverlayHedgeTracking` integration class (5 tests covering placement
  → HedgeLeg creation, idempotent placement, settlement returns settlements,
  settlement is idempotent within day, reset clears hedge state).

- 1.27.1 (2026-05-05): Brandon Trojan Horse stack — promoted to FULLY LIVE in
  variants B/C. Removes the "shadow" framing on GEX strike adjuster, GEX
  breach exit, and defensive overlay — all three now act in dry-run mode
  (and would act in live mode when dry_run is flipped off). The ONLY
  shadow-only behavior is HYDRA's existing credit+buffer stop, which runs
  in parallel as a counterfactual and Telegrams when it would have fired,
  but never closes a position in B/C — Brandon's GEX breach is the live
  stop. (a) GEX strike adjuster mutates `entry.short_*_strike` and
  `entry.long_*_strike` BEFORE _execute_entry / _simulate_entry runs;
  SKIP routes through HYDRA's existing one-sided entry path by setting
  `*_side_skipped` and `*_only` flags. (b) GEX breach exit closes the IC
  via `_close_entry_early` on a confirmed 90s sustained breach, marking
  sides via `*_side_pivot_closed` (same disposition as a directional-pivot
  close). Filter is anchored to the entry's short strike, not current
  spot, so a wall stays "relevant" even after spot has moved past it.
  (c) Defensive overlay places the proposed debit-spread / butterfly legs
  via `_place_option_order` in live mode; in dry-run mode logs the legs
  to journal + Telegram and skips the Saxo round-trip (per
  SAFETY-DRY-01). (d) GEX cache TTL: 1-day → 15-min refresh, with 60s
  failure cooldown. Polygon Starter is unlimited so cost is zero. (e) New
  config knobs promoted from hard-coded defaults to per-variant tuning:
  `gex.{decel_min_pct, accel_min_pct, max_shift_pts, shift_buffer_pts}`,
  `defensive_overlay.{butterfly_cutoff_hour, butterfly_cutoff_minute}`,
  `hydra_stop_shadow.enabled`. (f) Tests: 138 passing (was 131 in 1.27.0).
  Added: TestStrikeAdjusterLive (4 tests), TestBreachExitLive (2 tests),
  TestHydraShadowStop (3 tests). Removed obsolete shadow-only tests.

- 1.27.0 (2026-05-04): Brandon Trojan Horse stack — new variants B/C replace the
  retired pivot experiments (old B = stressed_only, old C = both_sides, both at
  75pt). New variants share Brandon's three additions on top of HYDRA: (a) flat
  80% take-profit closes the IC when its mark decays to 20% of credit received
  (LIVE in B/C); (b) GEX-aware strike adjustment via Polygon SPX option chain —
  shifts wings to enclose positive-gamma deceleration walls or skips a side
  inside negative-gamma acceleration zones (SHADOW for first 4 weeks); (c) GEX
  breach exit signal on sustained 90s breach of the outermost decel wall on the
  threatened side (SHADOW); plus defensive overlay (debit spread before 12:30 ET
  / butterfly after) when SPX threatens a short strike + GEX confirms accel
  (SHADOW). Variant B uses HYDRA's MKT-027 dynamic widths (75pt cap, matches A);
  variant C uses Brandon's narrow 5/10pt rule (5pt at VIX<22, 10pt otherwise) via
  the new `narrow_spread` module — strategy.brandon.narrow_spread.enabled=true
  overrides _get_vix_adjusted_spread_width. Implementation lives in
  bots/hydra/brandon/ as a `BrandonHydraStrategy(HydraStrategy)` subclass loaded
  by main.py when `strategy.brandon.enabled: true`. Variant A's path is
  untouched (zero Brandon references in strategy.py). Polygon dependency: set
  POLYGON_API_KEY env var on the VM; absent key disables GEX features
  gracefully (no crashes). All three variants run dry-run on the VM —
  promoting B/C to live is one config flip per file. directional_pivot is
  disabled across all variants in v1.27 (Brandon's GEX-breach exit plays
  the same role) but the pivot logic remains togglable. 131 new tests +
  79 pre-existing = 210 passing.

- 1.26.0 (2026-05-01 PM): Directional pivot strategy + spx_open 9:30 anchor + 75pt × 2c
  baseline. (a) `MarketData.update_spx` / `update_vix` now gate intraday-OHLC capture
  (spx_open, spx_high, spx_low, vix_open, vix_high, vix_low) to >= 9:30 ET via new
  `_is_regular_session_or_later` static — pre-market Saxo extended-hours ticks no
  longer pollute the "% from open" reference used by Upday-035, Downday-035, whipsaw
  filter, ROC gate, and the new pivot. (b) `_restore_market_ohlc_from_state_file_unconditional`
  is called once at end of HYDRA __init__ so mid-day restarts in dry-run still
  preserve the actual 9:30 anchor (the existing recovery path short-circuits in dry
  mode before reaching `_load_state_file_history`'s OHLC restore). (c) New directional
  pivot strategy gated by `directional_pivot.enabled` (variant B/C only): pre-entry
  defer-and-watch (skip up to `pre_entry_defer_minutes`, default 15) when SPX is
  already breached at entry time, plus a continuous breach monitor (any-time SPX
  ±0.25% from session open closes open base entries via configured `close_mode`:
  `stressed_only` = close the side facing the move; `both_sides` = close all 4 legs).
  Conditional E#3 at 14:00 (Upday-035 / Downday-035) is unaffected. Idempotent within
  the day; cascade-skips deferred entries on fire. New `IronCondorEntry.{call,put}_side_pivot_closed`
  flags + 4 new `MEICDailyState` fields persisted/restored. Stops take precedence over
  pivot (Option 1). Main-loop hook in `bots/hydra/main.py` wrapped in try/except. New
  Telegram alerts: LOW "Entry Deferred" + MEDIUM "Directional Pivot Fired". Dashboard
  variant labels updated. VM: all 3 → 75pt × 2c; B/C `entry_times` = ["10:15","10:45"]
  with pivot block enabled (B: stressed_only, C: both_sides); A unchanged (control).

- 1.25.1 (2026-05-01): N-way variant comparison + variant C scaffolding. Backend `dashboard/backend/
  routers/variants.py` refactored from hardcoded a/b ternaries to a `_VARIANTS` registry (add a
  variant by appending to `_VARIANT_IDS` + adding 5 `variant_<id>_*` settings fields). Frontend
  `Comparison.tsx` renders N variants from `/api/variants/health`; 3+ variant grouped-bar daily
  delta chart added. Bot `_discover_variant_ids()` globs `data/variant_*/` so Telegram `/compare`
  and end-of-day `VARIANT_COMPARISON_DAILY` auto-discover all running non-A variants. New
  `api_pacing_multiplier` strategy config (default 1.0 = variant A no-op) scales monitoring
  loop + heartbeat intervals — variant B at 1.5×, C at 2.0× — to keep combined Saxo API rate
  under ~60 req/min. Vigilant mode (stop detection) is intentionally NOT scaled (safety-critical).
  Variant C config: max_spread_width=25, dry_run, alerts off, sheets off. `deploy/hydra_variant_c.
  service` mirrors variant B's unit with HYDRA_VARIANT_ID=c.

- 1.25.0 (2026-04-30): Path-B dry-mode bookkeeping + MKT-024 multiplier tuning + 1v1 variant
  comparison + 4 SAFETY-DRY defense-in-depth gates. (a) `_process_expired_credits` now treats
  `DRY_*` synthetic IDs as settled — fixes Apr 28-29 false net_pnl=-$20 (commission-only) on
  winning days; DB + metrics file backfilled. (b) MKT-024: call_starting_otm_multiplier 3.5→2.5,
  put_starting_otm_multiplier 4.0→2.75, upper clamp 240→180pt. ~40-50% less wasted scan. (c) New
  `dry_run_force_normal_day` flag bypasses FOMC date-based skips in dry mode; live untouched.
  (d) Variant B = parallel HYDRA process (HYDRA_VARIANT_ID=b env var) running in dry mode with
  max_spread_width=110 (vs A's 50). Isolated data/variant_b/* paths, hydra_variant_b.service.
  Dashboard /comparison page (gated by DASHBOARD_COMPARISON_MODE_ENABLED) + 16th Telegram
  command /compare + end-of-day VARIANT_COMPARISON_DAILY alert (idempotent). (e) Defense-in-depth
  `if self.dry_run` gates at _place_option_order, _handle_naked_short, _unwind_partial_entry,
  _close_position_with_retry — every Saxo-order entry point self-protects.

- 1.24.0 (2026-04-21): scale-to-2-contracts support + non-HYDRA bots kill-switched at code level.
  Phase 1: All stop-level math (call/put buffers, theoretical put, MIN_STOP_LEVEL floor, MKT-042 buffer
  decay) scales via self.contracts_per_entry (live path) or entry.contracts (recovery path). All
  commission sites and close-order amounts scale via entry.contracts so mid-day config flips close the
  right quantity on legacy entries. ORDER-004 margin gate scales MIN_BUYING_POWER_PER_IC. MKT-033 long
  salvage threshold scales. DB schema v8: per-row `contracts` column on trade_entries, trade_stops,
  spread_snapshots, shadow_entries, and `contracts_per_entry` on daily_summaries — transactional
  migration with rollback. Metrics file daily_returns record gains contracts_per_entry for HERMES
  per-contract normalization. State file now persists contracts_per_entry at both top-level and
  per-entry levels (null-safe restoration with `.get() or fallback` pattern handles JSON null from
  crash-mid-write scenarios).
  Phase 2 telemetry: AlertService auto-prefixes `[Nc]` on title + enriches details dict — 14 HYDRA call
  sites updated to pass `contracts=entry.contracts`. Dashboard `/api/hydra/summary`, `/api/hydra/bot-config`,
  `/api/widget` expose contracts_per_entry with 3-level fallback. Google Sheets Daily Summary adds
  Contracts column (append-only). HERMES cheat_sheet gets net_pnl_per_contract + per-contract averages;
  CLIO prompt has mixed-week normalization rule; HOMER Section 2 gains Contracts row; all four agent
  system prompts (HERMES/CLIO/HOMER/APOLLO) + hydra_strategy_context.md updated. 8 Telegram builders
  route through `_with_contracts_footer` helper; `/config` banner warns prominently when contracts>1.
  Cosmetics: STOP-DETAIL diagnostic ask_sv/bid_sv scale with entry.contracts for apples-to-apples log
  comparison. Startup banner includes `Contracts per entry: N` line with warning icon when >1.
  Effective entry numbering rename: live code emits `Entry #1 = 10:45, #2 = 11:15, #3 = 14:00` (post-VIX
  regime) instead of canonical `E#1 = 10:15`. `_effective_total_entry_count()` helper used in all
  user-facing displays (heartbeat, /snapshot, /status, startup log). Pre-2026-04-17 records use
  canonical numbering — agents guided to use entry_time as authoritative slot ID. Kill-switches on
  bots/delta_neutral/main.py, bots/iron_fly_0dte/main.py, bots/rolling_put_diagonal/main.py, bots/meic/main.py
  (DISABLED_FOR_SAFETY=True + _check_disabled_kill_switch() exit before any side effects). MEIC
  strategy.py module remains importable as HYDRA's parent class. 55/55 new regression tests pass.
- 1.22.3 (2026-04-09): Fix #86 — Clear position IDs and UICs on entry object after stop loss.
  Without this, POS-003 hourly reconciliation finds closed positions as "missing from Saxo" and
  fires false "Position Mismatch Detected" HIGH alerts on Telegram after every stop. Base MEIC
  path clears both legs (both closed). MKT-025 path clears only short ID/UIC (long stays for
  settlement/MKT-033 salvage).
- 1.22.2 (2026-04-06): Full codebase audit. Fixed VIX regime credit gate ×100 bug, dangerous code defaults
  (spread widths 60→25, smart_entry True→False, conditional entries True→False). Heartbeat and Telegram
  cushion display now uses MKT-042 effective stop level (was showing base level, not decayed). Buffer decay
  logged at entry time and shown as [decay→$X] tag in Telegram /snapshot. Config template synced to VM values.
  Dashboard: DailyPnLCard /5→/{baseCount}, backtest config put_only_max_vix 25→15, theo put $2.50→$2.60,
  upday_threshold_pct 0.004→0.0025. All docs updated.
- 1.22.0 (2026-04-02): MKT-042 Buffer Decay + MKT-043 Calm Entry. MKT-042: time-decaying stop buffer —
  starts at buffer_decay_start_mult × normal buffer (default 2.10×), linearly decays to 1× over
  buffer_decay_hours (default 2.0h). Wider stops early when premium is rich, normal later. MKT-043:
  calm entry filter — delays entry up to calm_entry_max_delay_min (default 5 min) when SPX moved
  > calm_entry_threshold_pts (default 15.0 pts) in last calm_entry_lookback_min (default 3 min).
  MKT-041 Cushion Recovery DISABLED (buffer+cushion interfere; cushion_nearstop_pct/cushion_recovery_pct
  set to null on VM).
- 1.21.0 (2026-04-01): MKT-041 Cushion Recovery Exit. Closes individual IC sides when they nearly hit
  their stop (>= 96% of stop level) then recover (<= 67% of stop level). Backtest: Sharpe 2.182 vs 2.094
  baseline over 938 days, fires on ~101 days (10.8%). Config: cushion_nearstop_pct (default null/disabled),
  cushion_recovery_pct (default null/disabled). Both in strategy section.
- 1.19.0 (2026-03-29): Walk-forward backtest convergence. 3 base entries (was 5) at 10:15, 10:45, 11:15
  (E4/E5 dropped — negative EV in backtest). E6 upday put-only ENABLED at 14:00 (threshold 0.25%).
  E7 DISABLED. Spread width: VIX x 6.0, floor 25pt, cap 110pt. Credit gates: call $2.00, put $2.75,
  call_floor $0.75, put_floor $2.00. Stop buffers: call_stop_buffer $0.35 (renamed from stop_buffer),
  put_stop_buffer $1.55. FOMC skip FALSE (fomc_announcement_skip=false), T+1 call-only TRUE.
  Downday threshold 0.57%, theo put $2.60. Upday threshold 0.25%. Max spread width 110pt.
  NEW: whipsaw filter (whipsaw_filter.enabled=true, threshold 1.75x EM) — skips entries when
  intraday range exceeds 1.75x expected move (high whipsaw = bad for iron condors).
  put_only_max_vix lowered to 15.0.
- 1.17.0 (2026-03-23): Upday-035 conditional up-day put-only entries. Mirror of MKT-035 for bullish days:
  when SPX rises >= upday_threshold_pct (default 0.25%) above session open, conditional slots E6/E7 fire as
  put-only instead of being skipped. Stop = put_credit + put_stop_buffer. Configurable via
  conditional_upday_e6_enabled / conditional_upday_e7_enabled / upday_threshold_pct / upday_reference.
  DISABLED on VM by default. Dashboard EntryCard shows "Upday-035" label for override_reason="upday-035".
  Backtest support added (backtest/engine.py is_upday_conditional, backtest/config.py, backtest/optimize.py).
  APOLLO scout.py system prompt updated to reflect correct MKT-035 reference price (session open, not high)
  and to document Upday-035.
- 1.16.1 (2026-03-19): MKT-029 graduated call fallback in credit gate. Previously only puts had MKT-029 fallback (-$0.05, -$0.10) in _check_credit_gate(); calls used hard $0.60 minimum. Now both sides use graduated fallback: call $0.60→$0.55→$0.50, put $2.50→$2.45→$2.40. MKT-035/MKT-038 call-only skip checks also lowered from $0.60 to $0.50 floor. Fixed stale comments referencing $0.75 calls and $1.75 puts. All agent prompts updated.
- 1.16.0 (2026-03-16): Skip alerts + dashboard improvements. Telegram ENTRY_SKIPPED alerts at all 8 skip paths in _initiate_entry() with detailed reasons (MKT-011 both non-viable, MKT-032 VIX gate, MKT-035 not triggered, MKT-038 call non-viable, MKT-010 illiquidity, margin). Skipped entries now persisted in state file with skip_reason field for dashboard display. entry_schedule (base + conditional times) added to state file. Dashboard: mobile-responsive header, pending entry cards show scheduled times, skipped entry cards show reason. HERMES can see entry_schedule + skip_reason in trimmed state.
- 1.15.1 (2026-03-16): MKT-040 call-only entries when put non-viable. When put credit below minimum but call viable, place call-only instead of skipping. Data: 89% WR for low-credit call-only, +$46 EV per entry. Stop = call + theo $2.50 put + buffer (unified with MKT-035/038). Override reason: "mkt-040".
- 1.15.0 (2026-03-16): MKT-039 put-only stop tightening + MKT-032 VIX gate raise. Put-only stop changed from 2×credit+buffer to credit+buffer — $5.00 put buffer already prevents 91% false stops, 2× was redundant (max loss $750→$500). MKT-032 VIX gate raised 18→25 (tighter stop makes put-only viable at moderate VIX). Call-only later unified to call + theo $2.50 put + buffer. All agent SYSTEM_PROMPTs updated to v1.15.0.
- 1.14.0 (2026-03-15): MKT-038 FOMC T+1 call-only mode. Day after FOMC announcement: all entries forced to call-only. T+1 = 66.7% down days, 23% more volatile. Stop = call_credit + theoretical $2.50 put + buffer. MKT-036 stop confirmation timer documented as DISABLED (code preserved, $5.00 put buffer is the chosen solution). All agent SYSTEM_PROMPTs updated to v1.13.0.
- 1.13.0 (2026-03-13): Stop timestamps in state file (call_stop_time/put_stop_time on IronCondorEntry). Dashboard SPX chart now shows red circle stop markers at actual stop time + white price lines for active entry strikes. Negative P&L bar fill fix for stopped entries. MKT-035 scoped to conditional entries only — base entries E1-E5 always attempt full ICs regardless of down-day status ($5.00 put buffer provides sufficient protection). Conditional entries E6/E7 still fire as call-only on down days (SPX < open -0.3%). Threshold reverted to 0.3%.
- 1.12.1 (2026-03-12): MKT-036 asymmetric put stop buffer ($5.00 put vs $0.10 call). 21-day backtest: $5.00 put buffer avoids 91% of false put stops (+$6,885 NET). Configurable via put_stop_buffer (falls back to call_stop_buffer if not set). Telegram /set put_stop_buffer support. Full IC alert shows asymmetric stops. Sheets retry logic (3 attempts with 2s delay). HOMER stop matching fix for same-strike entries. Code audit: all docs, agents, config updated.
- 1.12.0 (2026-03-11): MKT-036 stop confirmation timer code deployed. Subsequently DISABLED on VM — $5.00 put buffer chosen as the solution instead. Code preserved, configurable via stop_confirmation_enabled.
- 1.11.0 (2026-03-11): MKT-035 call-only on down days. When SPX < open -0.3%, place call spread only (no puts). Stop uses theoretical $2.50 put credit instead of 2× call credit. 20-day data: 71% put stop rate on down days vs 7% call stop rate, +$920 improvement. Two conditional entry times (12:45, 13:15) that only fire when MKT-035 triggers. Configurable via downday_callonly_enabled, downday_threshold_pct, downday_theoretical_put_credit, conditional_entry_times.
- 1.10.4 (2026-03-11): Raise put credit minimum $1.75→$2.50, lower call credit minimum $0.75→$0.60. 20-day data analysis: $2.50-$3.49 put credit bucket = 66.7% survival, +$159 avg EV (best); $2.00-$2.49 = 33.3% survival, -$8 EV (worst); $1.50-$1.99 = 48.3%, +$23 EV. Higher put min forces MKT-022 to scan closer to ATM, landing in Week 1 sweet spot (42-65pt OTM). Lower call min = less MKT-020 tightening = calls stay further OTM. Disable MKT-031 smart entry — enter at scheduled times only.
- 1.10.3 (2026-03-11): Disable MKT-034 VIX time shifting + remove VIX entry cutoff (max_vix_entry=999). Neither Tammy nor Sandvand use VIX cutoffs. Entry times revert to 10:15 AM start. Spread widths reverted to 50pt. MKT-034 remains configurable.
- 1.10.2 (2026-03-10): Replace MEIC+ stop formula with credit+buffer (Brian's approach): stop = total_credit + $0.10. Per-side stop level validation fix. Telegram /set updated: call_stop_buffer replaces meic_plus.
- 1.10.1 (2026-03-09): Fix #83: Emergency close improvements for "limit orders only" failures. Fix #83a: Skip closing worthless long legs (bid=$0) during stop loss — prevents cascading 409 errors when Saxo restricts market orders on illiquid deep OTM options. Fix #83b: $0.05 minimum tick fallback in place_emergency_order() when quote returns no valid price. Fix #83c: Cancel zombie pending orders after 409 Conflict before retry. Fix #83d: Removed narrow is_limit_only_period time check (Saxo can restrict at any time, not just 3:45+ PM). Commission tracking now counts only actually-closed legs. Fix #84: Dashboard P&L history updated after settlement (was showing stale pre-settlement snapshot). Strike-not-found log level changed from ERROR to WARNING.
- 1.10.0 (2026-03-08): MKT-034 VIX-scaled entry time shifting. Entry times shifted to :14:30/:44:30 (30s before :15/:45 marks for execution precision). VIX gate checks at :14:00/:44:00 — blocks E#1 if VIX >= threshold (20/23), shifts schedule to later slots. Floor at 12:14:30 (always enters). MKT-031 early entry integrates with VIX gate. Early close cutoff raised from 12:00 to 12:30 PM. Configurable via `vix_time_shift` config section.
- 1.9.4 (2026-03-08): Configurable stop close mode via `long_salvage.short_only_stop` (default: false = close both legs). MKT-025/MKT-033 preserved but gated behind flag. Added /clio Telegram command (15 total). Updated all agent prompts (CLIO, APOLLO, HERMES, HOMER) to v1.9.3 parameters.
- 1.9.3 (2026-03-07): Actual stop debit tracking for per-entry P&L accuracy. Added actual_call_stop_debit/actual_put_stop_debit fields — records real market order cost (including slippage) instead of theoretical trigger level. Dashboard uses actual when available, falls back to theoretical when 0. Fixed pre-existing gap: MKT-033 long salvage flags were missing from preserved_stopped_entries restoration path. Fixed SPXChart price lines for active entries (is_complete → entry_time).
- 1.9.2 (2026-03-05): MKT-033 long leg salvage (requires `short_only_stop: true`). After MKT-025 closes short, sells surviving long if appreciated >= $10. Two trigger points: immediate post-stop + periodic heartbeat check. Tracked in state, Sheets, heartbeat display.
- 1.9.1 (2026-03-05): MKT-032 VIX gate for put-only entries. Put-only only allowed when VIX < 18 (80% WR in calm markets). At VIX >= 18, skip instead of put-only (2× stop with no hedge = 50% WR, unacceptable risk). Configurable via put_only_max_vix. Telegram /set support.
- 1.9.0 (2026-03-05): Telegram commands expanded to 14: /set (edit config), /restart, /stop (with position warning). Message splitting replaces truncation for HERMES/APOLLO reports. Atomic config writes with file locking.
- 1.8.1 (2026-03-05): Entry times shifted to :15/:45 offset (11:15-13:15). 19-day MAE analysis: :15/:45 has 10% lower 30-min adverse excursion vs :05/:35 (12.39pt vs 13.76pt) with better tail risk (P90: 21.71pt vs 23.84pt). Early close day keeps 11:15/11:45.
- 1.8.0 (2026-03-04): Entry schedule shifted +1hr (11:05-13:05 — journal data: 10:05 -$695, 10:35 -$510 vs 11:05+ all positive). MKT-031 smart entry windows (10min pre-entry scouting, 2-parameter scoring: post-spike ATR calm 0-70pts + momentum pause 0-30pts, threshold 65 triggers early entry). Early close day cutoff raised to 12:00 PM (keeps 11:05/11:35 viable).
- 1.7.2 (2026-03-03): Lower call minimum from $1.00 to $0.75 (credit cushion analysis: 68.1% call cushion vs 61.5% — crosses 65% safety threshold from Week 1 data). Less MKT-020 tightening = calls stay further OTM = safer.
- 1.7.1 (2026-03-03): Re-enable MKT-011 put-only entries (data: 87.5% WR, +$870 net from 6 entries). Strict $1.00 call min (remove MKT-029 call fallbacks). Call-only remains disabled.
- 1.7.0 (2026-03-03): 8 new Telegram commands (/status, /hermes, /apollo, /week, /entry, /stops, /config, /help)
- 1.6.2 (2026-03-03): MKT-029 graduated credit fallback thresholds (calls $1.00→$0.95→$0.90, puts $1.75→$1.70→$1.65 — prevents skipping entries barely below minimum)
- 1.6.1 (2026-03-03): Telegram /lastday and /account commands (historical data lookups from Google Sheets)
- 1.6.0 (2026-03-02): MKT-028 asymmetric spread widths (put floor 75pt, call floor 60pt — put longs cost 7x more due to skew, wider = cheaper). MKT-024 upgraded to 3.5x/4.0x starting OTM (batch API = zero extra cost, catches extra cushion on high-credit days). Drop Entry #6 (5 entries, frees margin for wider spreads: 5 x 75pt x $100 = $37,500 <= $39,000). max_spread_width 75pt margin cap.
- 1.5.1 (2026-03-02): Telegram /snapshot command (on-demand position snapshot)
- 1.5.0 (2026-02-28): Renamed from MEIC-TF to HYDRA
- 1.4.5 (2026-02-28): MKT-026 min spread width raised from 25pt to 60pt (longs 10pt further OTM on low-VIX days = cheaper)
- 1.4.4 (2026-02-28): Add 6th entry at 12:35 PM (matching base MEIC schedule — MKT-011 credit gate ensures zero-cost skip when non-viable)
- 1.4.3 (2026-02-28): MKT-025 short-only stop loss close (configurable since v1.9.4; default: close both legs)
- 1.4.2 (2026-02-27): MEIC+ reduction raised from $0.10 to $0.15 to cover commission on one-side-stop (true breakeven)
- 1.4.1 (2026-02-27): MKT-024 wider starting OTM (2× multiplier both sides), separate put minimum $1.75 (Tammy's $1.00-$1.75 range), enhanced MKT-020/022 scan logging
- 1.4.0 (2026-02-27): Remove MKT-019 (revert to total_credit stop), disable all one-sided entries (EMA signal informational only, always full IC or skip)
- 1.3.11 (2026-02-25): MKT-018 early close threshold raised from 2% to 3% ROC (config change, 11-day analysis showed 2% left $1,025 on table)
- 1.3.10 (2026-02-25): Daily Summary: avg capital deployed, cumulative ROC, avg daily ROC, annualized return columns
- 1.3.9 (2026-02-25): MKT-021 ROC gate lowered from 5 to 3 entries, gate now counts actual placed entries not time slots (skipped/failed entries don't count)
- 1.3.8 (2026-02-24): Fix #83 - FIX-71 idempotency guard poisoned by midnight settlement (stored clock time, not trading date)
- 1.3.7 (2026-02-24): MKT-023 smart hold check (compare close-now vs worst-case-hold before early close)
- 1.3.6 (2026-02-24): MKT-011 one-sided entries only for clear trends (NEUTRAL always full IC or skip)
- 1.3.5 (2026-02-24): MKT-022 progressive put OTM tightening (mirror of MKT-020 for calls)
- 1.3.4 (2026-02-23): Fix #82 - Settlement gate lock bug (midnight reset locked gate for entire day, preventing post-market settlement)
- 1.3.3 (2026-02-23): Remove MKT-016 (stop cascade) + MKT-017 (daily loss limit) + base MEIC loss limit — bot always places all entries
- 1.3.2 (2026-02-20): MKT-021 pre-entry ROC gate (min 3 entries), Fix #81 skip $0 long legs during early close
- 1.3.1 (2026-02-20): MKT-020 progressive call OTM tightening, raise min credit to $1.00/side
- 1.3.0 (2026-02-19): MKT-019 virtual equal credit stop, MKT-018 early close based on ROC, batch quote API (7x rate limit reduction), Fix #80 Sheets resize
- 1.2.9 (2026-02-18): MKT-017 daily loss limit, Fix #77/#78/#79 (settlement, summary accuracy, counters)
- 1.2.8 (2026-02-17): EMA threshold 0.2%, MKT-016 stop cascade breaker
- 1.2.7 (2026-02-16): Daily Summary column redesign, Fix #76 fill price field names
- 1.2.6 (2026-02-13): Fix #75 - Async deferred stop fill lookup (non-blocking P&L correction)
- 1.2.5 (2026-02-13): Fix #74 - Stop loss fill price accuracy (deferred lookup was bypassed by quote fallback)
- 1.2.4 (2026-02-13): Code audit hardening - error handling, timeout protection, documentation
- 1.2.3 (2026-02-12): Fix #70 - Accurate fill price tracking (verify vs PositionBase.OpenPrice)
- 1.2.2 (2026-02-12): Fix #65-#68 - Recovery classification, long overlap, timeout protection
- 1.2.1 (2026-02-12): Fix #71-#73 - Duplicate summary prevention, net P&L, active entries fix
- 1.2.0 (2026-02-12): Accurate P&L tracking and daily summary fixes
- 1.1.8 (2026-02-11): Fix #64 - Google Sheets API timeout protection (prevents bot freeze)
- 1.1.7 (2026-02-11): Fix #63 - EUR conversion in Trades tab (pass saxo_client to log_trade)
- 1.1.6 (2026-02-11): Fix #62 - EMA values now logged to Account Summary tab
- 1.1.5 (2026-02-11): MKT-014 liquidity re-check, counter tracking, position merge detection
- 1.1.4 (2026-02-10): MKT-013 same-strike overlap prevention
- 1.1.3 (2026-02-10): Logging accuracy (Fix #49), correct MKT-011/MKT-010/trend labels
- 1.1.2 (2026-02-10): P&L tracking fixes (Fix #46/#47), expired vs skipped distinction
- 1.1.1 (2026-02-09): Hybrid credit gate - respects trend filter in non-NEUTRAL markets
- 1.1.0 (2026-02-08): MKT-011 credit gate, MKT-010 illiquidity fallback
- 1.0.0 (2026-02-04): Initial implementation with EMA trend detection
"""

from bots.hydra.strategy import HydraStrategy, TrendSignal, HydraIronCondorEntry

__all__ = [
    "HydraStrategy",
    "TrendSignal",
    "HydraIronCondorEntry",
]
