"""
HYDRA 0DTE Trading Bot

Multi-Entry Iron Condors (SPX 0DTE) with credit gates, progressive OTM
tightening, and hold-to-expiry. Based on Tammy Chambless's MEIC strategy.

Before each entry, checks 20 EMA vs 40 EMA on SPX 1-minute bars.
The EMA signal (BULLISH/BEARISH/NEUTRAL) is logged and stored for analysis
but is informational only — base entries are full iron condors or put-only via MKT-011.

Credit Gate (MKT-011): Before placing orders, estimates credit from quotes.
- Both sides viable: Proceed with full iron condor
- Call non-viable, put viable, VIX < 25: Place put-only entry (MKT-032/MKT-039 VIX gate)
- Call non-viable, put viable, VIX >= 25: Skip entry (no call hedge in volatile conditions)
- Put non-viable: Skip entry
- Both non-viable: Skip entry entirely

Conditional Entry Trigger (MKT-035): Only affects conditional entries E6/E7.
- Base entries E1-E5 always attempt full ICs regardless of down-day status
- Conditional entries (12:45, 13:15) only fire when SPX drops >= 0.3% below open, as call-only
- Stop = call_credit + theoretical $2.50 put + buffer (not 2× credit)

Version History:
- 1.15.0 (2026-03-16): MKT-039 put-only stop tightening + MKT-032 VIX gate raise. Put-only stop changed from 2×credit+buffer to credit+buffer — $5.00 put buffer already prevents 91% false stops, 2× was redundant (max loss $750→$500). MKT-032 VIX gate raised 18→25 (tighter stop makes put-only viable at moderate VIX). Call-only legacy keeps 2× ($0.10 buffer too small without it). All agent SYSTEM_PROMPTs updated to v1.15.0.
- 1.14.0 (2026-03-15): MKT-038 FOMC T+1 call-only mode. Day after FOMC announcement: all entries forced to call-only. T+1 = 66.7% down days, 23% more volatile. Stop = call_credit + theoretical $2.50 put + buffer. MKT-036 stop confirmation timer documented as DISABLED (code preserved, $5.00 put buffer is the chosen solution). All agent SYSTEM_PROMPTs updated to v1.13.0.
- 1.13.0 (2026-03-13): Stop timestamps in state file (call_stop_time/put_stop_time on IronCondorEntry). Dashboard SPX chart now shows red circle stop markers at actual stop time + white price lines for active entry strikes. Negative P&L bar fill fix for stopped entries. MKT-035 scoped to conditional entries only — base entries E1-E5 always attempt full ICs regardless of down-day status ($5.00 put buffer provides sufficient protection). Conditional entries E6/E7 still fire as call-only on down days (SPX < open -0.3%). Threshold reverted to 0.3%.
- 1.12.1 (2026-03-12): MKT-036 asymmetric put stop buffer ($5.00 put vs $0.10 call). 21-day backtest: $5.00 put buffer avoids 91% of false put stops (+$6,885 NET). Configurable via put_stop_buffer (falls back to stop_buffer if not set). Telegram /set put_stop_buffer support. Full IC alert shows asymmetric stops. Sheets retry logic (3 attempts with 2s delay). HOMER stop matching fix for same-strike entries. Code audit: all docs, agents, config updated.
- 1.12.0 (2026-03-11): MKT-036 stop confirmation timer code deployed. Subsequently DISABLED on VM — $5.00 put buffer chosen as the solution instead. Code preserved, configurable via stop_confirmation_enabled.
- 1.11.0 (2026-03-11): MKT-035 call-only on down days. When SPX < open -0.3%, place call spread only (no puts). Stop uses theoretical $2.50 put credit instead of 2× call credit. 20-day data: 71% put stop rate on down days vs 7% call stop rate, +$920 improvement. Two conditional entry times (12:45, 13:15) that only fire when MKT-035 triggers. Configurable via downday_callonly_enabled, downday_threshold_pct, downday_theoretical_put_credit, conditional_entry_times.
- 1.10.4 (2026-03-11): Raise put credit minimum $1.75→$2.50, lower call credit minimum $0.75→$0.60. 20-day data analysis: $2.50-$3.49 put credit bucket = 66.7% survival, +$159 avg EV (best); $2.00-$2.49 = 33.3% survival, -$8 EV (worst); $1.50-$1.99 = 48.3%, +$23 EV. Higher put min forces MKT-022 to scan closer to ATM, landing in Week 1 sweet spot (42-65pt OTM). Lower call min = less MKT-020 tightening = calls stay further OTM. Disable MKT-031 smart entry — enter at scheduled times only.
- 1.10.3 (2026-03-11): Disable MKT-034 VIX time shifting + remove VIX entry cutoff (max_vix_entry=999). Neither Tammy nor Sandvand use VIX cutoffs. Entry times revert to 10:15 AM start. Spread widths reverted to 50pt. MKT-034 remains configurable.
- 1.10.2 (2026-03-10): Replace MEIC+ stop formula with credit+buffer (Brian's approach): stop = total_credit + $0.10. Per-side stop level validation fix. Telegram /set updated: stop_buffer replaces meic_plus.
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
