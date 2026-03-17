# HYDRA (Trend Following Hybrid) Trading Bot

**Version:** 1.16.0 | **Last Updated:** 2026-03-16

A modified MEIC bot that adds EMA-based trend direction detection, pre-entry credit validation, progressive OTM tightening, and hold-to-expiry profit management.

## Strategy Overview

HYDRA combines Tammy Chambless's MEIC (Multiple Entry Iron Condors) with trend-following concepts from METF:

- **Before each entry**, check 20 EMA vs 40 EMA on SPX 1-minute bars
- **EMA signal is informational only** — logged and stored but does NOT drive entry type
- **Base entries are full iron condors or one-sided** — call credit non-viable → put-only if VIX < 25 (MKT-032/MKT-039), skip if VIX >= 25; put credit non-viable → call-only (MKT-040, 89% WR). Conditional entries E6/E7 fire as call-only when SPX drops ≥ 0.3% from open (MKT-035)

### Why This Works

On February 4, 2026, pure MEIC had all 6 entries get their PUT side stopped because the market was in a sustained downtrend. HYDRA addresses this with pre-entry credit validation (MKT-011), progressive OTM tightening (MKT-020/022), and wider starting OTM (MKT-024).

### Entry Schedule (5 base + 2 conditional entries)

**Current schedule (v1.11.0 — MKT-034 disabled, matches winning period Feb 10-27):**

| Entry | Time (ET) | Type | Notes |
|-------|-----------|------|-------|
| 1 | 10:15 | Base | Always attempts (full IC or put-only) |
| 2 | 10:45 | Base | Always attempts |
| 3 | 11:15 | Base | Always attempts |
| 4 | 11:45 | Base | Always attempts |
| 5 | 12:15 | Base | Always attempts |
| 6 | 12:45 | Conditional (MKT-035) | Only fires on down days as call-only |
| 7 | 13:15 | Conditional (MKT-035) | Only fires on down days as call-only |

**Conditional entries** only fire when MKT-035 triggers (SPX < open -0.3%). They are always call-only. On non-down days, conditional entries are silently skipped.

On early close days, cutoff is 12:30 PM. MKT-034 (VIX-scaled time shifting) is disabled — neither Tammy Chambless nor John Sandvand use VIX-based scheduling. Code preserved and configurable via `vix_time_shift.enabled`.

### Smart Entry Windows (MKT-031) — v1.8.0

Before each scheduled entry, a 10-minute scouting window opens. Market conditions are scored every main-loop cycle (~2-5s). If score >= 65, the bot enters early. Otherwise, enters at the scheduled time (zero-risk fallback).

**Scoring (2 parameters, 100 max):**

| Parameter | Points | Data Source |
|-----------|--------|-------------|
| Post-spike calm (ATR declining from elevated) | 0-70 | `get_chart_data()` 1-min OHLC, cached |
| Momentum pause (price calm over 2 min) | 0-30 | `MarketData.price_history` deque (zero API cost) |

### Conditional Entry Trigger (MKT-035) — v1.11.0, updated v1.12.1

Before the credit gate, checks if SPX is down >= 0.3% from today's open. MKT-035 **only affects conditional entries (E6/E7)** — base entries E1-E5 always attempt full ICs regardless of down-day status (the $5.00 put stop buffer provides sufficient protection).

When triggered:
- **Base entries (#1-5):** Unaffected — always full IC (or put-only via MKT-011)
- **Conditional entries (#6-7):** Fire as call-only spreads (only on down days)
- **Stop formula for E6/E7:** `call_credit + theoretical put ($250) + buffer` (not 2× credit)
- **Credit check:** Call credit must still pass MKT-011 minimum ($0.60)

### Credit Gate (MKT-011)

Before placing any orders, HYDRA estimates the expected credit by fetching option quotes. Separate thresholds for calls ($0.60) and puts ($2.50). For conditional entries (E6/E7), MKT-035 runs first — when triggered, only call credit is checked.

| Condition | Call Credit | Put Credit | VIX | Action |
|-----------|-------------|------------|-----|--------|
| Conditional entry (MKT-035) | >= $0.60 | N/A | Any | Place call-only entry |
| Conditional entry (MKT-035) | < $0.60 | N/A | Any | Skip entry |
| Base entry | >= $0.60 | >= $2.50 | Any | Proceed with full iron condor |
| Normal | < $0.60 | >= $2.50 | < 25 | Place put-only entry (MKT-032/MKT-039 allows) |
| Normal | < $0.60 | >= $2.50 | >= 25 | Skip entry (MKT-032: no call hedge in volatile conditions) |
| Normal | >= $0.60 | < $2.50 | Any | Retry with tighter put strikes (5pt, max 2 retries), then call-only (MKT-040: 89% WR) |
| Normal | < $0.60 | < $2.50 | Any | Skip entry |

### Illiquidity Fallback (MKT-010)

If the credit gate can't get valid quotes (rare), it falls back to wing illiquidity flags set during strike calculation. Any wing illiquid → skip entry.

### Wider Starting OTM (MKT-024) - Updated v1.6.0

Calls start at 3.5× and puts at 4.0× the VIX-adjusted OTM distance (asymmetric — put multiplier higher because put skew means credit is viable further OTM). MKT-020/MKT-022 then scan inward from there to find the widest viable strike at or above the minimum credit threshold. Batch API = zero extra cost for wider scan.

### Progressive OTM Tightening (MKT-020 Calls / MKT-022 Puts)

From the MKT-024 starting distance, progressively moves the short strike closer to ATM in 5pt steps until credit meets the minimum ($0.60 for calls, $2.50 for puts) or a 25pt OTM floor is reached.

```
Flow: MKT-024 (wider start) → MKT-020 (calls) → MKT-022 (puts) → MKT-011 credit gate
  - If tightened strikes meet minimums: proceed as full IC
  - If can't reach minimum at 25pt floor: MKT-011 skips entry
```

Both use batch quote API for efficiency: 1 option chain fetch + 1 batch quote call = 2 API calls each. Include liquidity checks (skip candidates with bid/ask = 0).

## Configuration

```json
{
    "trend_filter": {
        "enabled": true,
        "ema_short_period": 20,
        "ema_long_period": 40,
        "ema_neutral_threshold": 0.002,
        "recheck_each_entry": true,
        "chart_bars_count": 50,
        "chart_horizon_minutes": 1
    },
    "smart_entry": {
        "enabled": true,
        "window_minutes": 10,
        "score_threshold": 65,
        "momentum_threshold_pct": 0.05
    },
    "vix_time_shift": {
        "enabled": true,
        "medium_vix_threshold": 20.0,
        "high_vix_threshold": 23.0
    },
    "long_salvage": {
        "short_only_stop": false,
        "enabled": true,
        "min_profit": 10.0
    }
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable/disable trend filtering |
| `ema_short_period` | `20` | Fast EMA period |
| `ema_long_period` | `40` | Slow EMA period |
| `ema_neutral_threshold` | `0.002` | 0.2% - threshold for neutral zone (widened from 0.1% on 2026-02-17) |
| `recheck_each_entry` | `true` | Re-check EMAs before each entry |
| `chart_bars_count` | `50` | Number of 1-min bars to fetch |
| `chart_horizon_minutes` | `1` | Bar interval (1 = 1 minute) |

### Smart Entry Config (MKT-031) — DISABLED (v1.10.4)

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `false` | Enable/disable smart entry scouting (DISABLED) |
| `window_minutes` | `10` | Scouting window before each entry (minutes) |
| `score_threshold` | `65` | Score >= this triggers early entry |
| `momentum_threshold_pct` | `0.05` | Momentum calm threshold (0.05 = 0.05%) |

### VIX-Scaled Entry Time Shifting Config (MKT-034) — DISABLED (v1.10.3)

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `false` | Enable/disable VIX-scaled entry time shifting (DISABLED) |
| `medium_vix_threshold` | `20.0` | VIX >= this skips slot 0 (11:14:30), shifts to 11:44:30 |
| `high_vix_threshold` | `23.0` | VIX >= this skips slot 1 (11:44:30), shifts to 12:14:30 (floor) |

### Stop Close Mode & Long Leg Salvage Config (MKT-025/MKT-033)

| Setting | Default | Description |
|---------|---------|-------------|
| `short_only_stop` | `false` | `false` = close both legs on stop (default). `true` = MKT-025 short-only + MKT-033 salvage |
| `enabled` | `true` | Enable/disable long leg salvage after short stop (only when `short_only_stop: true`) |
| `min_profit` | `10.0` | Minimum profit ($) to sell long (covers $5 commission + $5 slippage) |

### Down Day Filter Config (MKT-035)

| Setting | Default | Description |
|---------|---------|-------------|
| `downday_callonly_enabled` | `true` | Enable/disable MKT-035 down day filter |
| `downday_threshold_pct` | `0.003` | 0.3% — SPX must drop this much below open to trigger |
| `downday_theoretical_put_credit` | `2.50` | Theoretical put credit ($) for stop calculation |
| `conditional_entry_times` | `["12:45","13:15"]` | Extra entry times that only fire when MKT-035 triggers |

### Stop Confirmation Timer (MKT-036) — INTENTIONALLY DISABLED

MKT-036 stop confirmation timer is **intentionally disabled**. The $5.00 put buffer (`put_stop_buffer`) is the chosen solution for false stops instead. Code preserved but dormant — set `stop_confirmation_enabled: true` to re-enable.

When enabled: 75-second confirmation window before executing stop. 20-day backtest: 17 false stops avoided ($2,870 saved), 1 real stop missed ($85).

| Setting | Default | Description |
|---------|---------|-------------|
| `stop_confirmation_enabled` | `false` | Enable/disable MKT-036 stop confirmation timer (DISABLED) |
| `stop_confirmation_seconds` | `75` | Duration (seconds) breach must sustain before executing stop |
| `stop_buffer` | `0.10` | Call stop buffer: call_stop = credit + $0.10 |
| `put_stop_buffer` | `5.00` | Put stop buffer: put_stop = credit + $5.00 (wider — avoids 91% false put stops). Falls back to `stop_buffer` if not set. |

### FOMC T+1 Call-Only (MKT-038)

On the day after FOMC announcement (T+1), forces all entries to call-only spreads. Research shows T+1 is 66.7% down days with 23% more volatility — put-side exposure is dangerous.

| Setting | Default | Description |
|---------|---------|-------------|
| `fomc_t1_callonly_enabled` | `true` | Force call-only entries on day after FOMC announcement |

Stop formula for MKT-038 entries: `call_credit + theoretical $2.50 put + call buffer` (same as MKT-035).

### Early Close on ROC (MKT-018/023/021) — INTENTIONALLY DISABLED

Early close (MKT-018), smart hold check (MKT-023), and pre-entry ROC gate (MKT-021) are **intentionally disabled**. Backtest analysis showed no ROC-based early close configuration beats hold-to-expiry for this strategy. The code is preserved but dormant — set `early_close_enabled: true` in config to re-enable. See `docs/HYDRA_EARLY_CLOSE_ANALYSIS.md` for the full analysis.

### Credit Gate & Tightening Config (strategy section)

| Setting | Default | Description |
|---------|---------|-------------|
| `min_viable_credit_per_side` | `0.60` | MKT-011/MKT-020: Call minimum credit (v1.10.4: lowered from $0.75 — calls are secondary income) |
| `min_viable_credit_put_side` | `2.50` | MKT-011/MKT-022: Put minimum credit (v1.10.4: raised from $1.75 — 20-day data: $2.50+ = 66.7% survival) |
| `put_only_max_vix` | `25.0` | MKT-032/MKT-039: Max VIX for put-only entries (skip instead when VIX >= threshold). Raised 18→25 in v1.15.0. |
| `call_starting_otm_multiplier` | `3.5` | MKT-024: Call starting OTM = base × multiplier |
| `put_starting_otm_multiplier` | `4.0` | MKT-024: Put starting OTM = base × multiplier (higher due to put skew) |
| `min_call_otm_distance` | `25` | MKT-020: Minimum OTM distance (points) for call tightening floor |
| `min_put_otm_distance` | `25` | MKT-022: Minimum OTM distance (points) for put tightening floor |
| `early_close_enabled` | `false` | MKT-018: Intentionally disabled (hold-to-expiry outperforms). Set `true` to re-enable. |
| `early_close_roc_threshold` | `0.03` | MKT-018: ROC threshold (3.0%). Only used when early_close_enabled=true. |
| `early_close_cost_per_position` | `5.00` | MKT-018: Close cost estimate per leg. Only used when early_close_enabled=true. |
| `hold_check_enabled` | `true` | MKT-023: Smart hold check. Only used when early_close_enabled=true. |
| `hold_check_lean_tolerance` | `1.0` | MKT-023: Lean threshold (%). Only used when early_close_enabled=true. |
| `min_entries_before_roc_gate` | `3` | MKT-021: Pre-entry ROC gate. Only active when early_close_enabled=true. |

## Usage

```bash
# Run in simulation mode (no real orders)
python -m bots.hydra.main --dry-run

# Run with live data (real orders)
python -m bots.hydra.main --live

# Show current status
python -m bots.hydra.main --status
```

## Deployment

1. Copy config template and edit:
```bash
cp bots/hydra/config/config.json.template bots/hydra/config/config.json
# Edit config.json with your settings
```

2. Install systemd service:
```bash
sudo cp bots/hydra/hydra.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hydra
sudo systemctl start hydra
```

3. Monitor:
```bash
sudo journalctl -u hydra -f
```

## Differences from Pure MEIC

| Aspect | Pure MEIC | HYDRA |
|--------|-----------|---------|
| Entry type | Always full IC | Full IC, put-only (MKT-032), or call-only (MKT-035/038/040) via credit gate |
| Starting OTM | VIX-adjusted | 3.5× calls, 4.0× puts (MKT-024), then tightened |
| Spread widths | 50pt fixed | Asymmetric: call 60pt, put 75pt floor, 75pt cap (MKT-026/027/028) |
| Credit minimums | $0.50/side | $0.60 calls, $2.50 puts |
| Trend signal | None | EMA 20/40 (informational only) |
| Smart entry | None | MKT-031 10-min scouting windows (post-spike + momentum scoring) |
| Profit management | Hold to expiration | Hold to expiration (MKT-018 early close disabled) |
| Stop formula | total_credit - $0.10 | total_credit + asymmetric buffer (call $0.10, put $5.00). MKT-036 timer DISABLED. |
| FOMC handling | Skip both days | Skip announcement day only (MKT-008) + T+1 call-only (MKT-038) |
| Stop execution | Close both legs | Close both legs (default) or SHORT only when `short_only_stop: true` (MKT-025 + MKT-033) |

## Risk Considerations

1. **Skip rate**: Stricter credit gates (especially $2.50 put minimum) may skip more entries
2. **Trend reversal risk**: EMA is a lagging indicator; sudden reversals may not be detected immediately
3. **Hold-to-expiry**: All positions held until settlement (MKT-018 early close intentionally disabled — backtest showed hold outperforms)

## State Files

HYDRA uses **separate state files** from MEIC to allow both bots to run simultaneously:

| File | Description |
|------|-------------|
| `data/hydra_state.json` | HYDRA daily state (entries, P&L, stops) |
| `data/position_registry.json` | Shared with all SPX bots for position isolation |

**Important**: The Position Registry is shared across all SPX bots (MEIC, HYDRA, Iron Fly) to prevent position conflicts when multiple bots trade the same underlying.

## Files

```
bots/hydra/
├── main.py                 # Entry point + Telegram snapshot daemon
├── strategy.py             # Trend-following strategy (extends MEIC)
├── telegram_commands.py    # /snapshot command handler
├── config/
│   └── config.json.template
└── README.md               # This file
```

## Related Documentation

- [HYDRA Strategy Specification](../../docs/HYDRA_STRATEGY_SPECIFICATION.md) — Full strategy spec: decision flows, MKT rules, performance data
- [MEIC Strategy Specification](../../docs/MEIC_STRATEGY_SPECIFICATION.md) — Base MEIC spec (inherited strike selection, stop math)
- [HYDRA Trading Journal](../../docs/HYDRA_TRADING_JOURNAL.md) — Daily results and analysis
- [HYDRA Early Close Analysis](../../docs/HYDRA_EARLY_CLOSE_ANALYSIS.md) — MKT-018 research
- [MEIC Edge Cases](../../docs/MEIC_EDGE_CASES.md)
- [Technical Indicators](../../shared/technical_indicators.py)

## Version History

- **1.16.0** (2026-03-16): Skip alerts + dashboard improvements. Telegram ENTRY_SKIPPED alerts at all 8 skip paths with detailed reasons. Skipped entries persisted in state file with `skip_reason` field. `entry_schedule` (base + conditional times) added to state file. Dashboard: mobile-responsive header, pending cards show scheduled times, skipped cards show reason. HERMES trimmed state includes `entry_schedule` + `skip_reason`.
- **1.15.1** (2026-03-16): MKT-040 call-only entries when put credit non-viable. When put credit < $2.50 but call credit >= $0.60, place call-only entry instead of skipping. Data: 89% WR for low-credit call-only entries, +$46 EV per entry. Stop = call + theo $2.50 put + buffer (unified with MKT-035/038). No VIX gate (unlike MKT-032 for put-only). Gated by existing `one_sided_entries_enabled` config. Override reason: `mkt-040`.
- **1.15.0** (2026-03-16): MKT-039 put-only stop tightening + MKT-032 VIX gate raise. Put-only stop changed from 2×credit+buffer to credit+buffer — $5.00 put buffer already prevents 91% false stops, 2× was redundant (max loss $750→$500). MKT-032 VIX gate raised 18→25 (tighter stop makes put-only viable at moderate VIX). Call-only later unified to call + theo $2.50 put + buffer. All agent SYSTEM_PROMPTs updated to v1.15.0.
- **1.14.0** (2026-03-15): MKT-038 FOMC T+1 call-only mode. Day after FOMC announcement: all entries forced to call-only. T+1 = 66.7% down days, 23% more volatile. Stop = call_credit + theoretical $2.50 put + buffer. MKT-036 stop confirmation timer documented as DISABLED (code preserved, $5.00 put buffer is the chosen solution). Telegram `/status` shows T+1 status. All agent SYSTEM_PROMPTs updated to v1.13.0. `stop_confirmation_enabled` default changed to `false`.
- **1.13.0** (2026-03-13): Stop timestamps in state file. Dashboard SPX chart stop markers + entry strike lines. MKT-035 scoped to conditional entries only.
- **1.12.1** (2026-03-12): Asymmetric put stop buffer ($5.00 put vs $0.10 call). 21-day backtest: 91% false put stops avoided.
- **1.12.0** (2026-03-11): MKT-036 stop confirmation timer code deployed. Subsequently DISABLED on VM — $5.00 put buffer (`put_stop_buffer`) chosen as the solution instead. Code preserved, configurable via `stop_confirmation_enabled`.
- **1.11.0** (2026-03-11): MKT-035 call-only on down days. When SPX < open -0.3%, place call spread only (no puts). Stop = call_credit + theoretical $2.50 put + buffer. 20-day data: 71% put stop rate on down days vs 7% call stop rate, +$920 improvement. Two conditional entry times (12:45, 13:15) that only fire when MKT-035 triggers as call-only. Configurable via `downday_callonly_enabled`, `downday_threshold_pct`, `downday_theoretical_put_credit`, `conditional_entry_times`.
- **1.10.3** (2026-03-11): Disable MKT-034 VIX time shifting + remove VIX entry cutoff (max_vix_entry=999). Neither Tammy Chambless nor John Sandvand use VIX cutoffs or time shifting (both studied VIX correlation, found none). Entry times revert to 10:15 AM start (winning period Feb 10-27). Spread widths reverted to 50pt. MKT-034 remains configurable (`vix_time_shift.enabled`).
- **1.10.2** (2026-03-10): Replace MEIC+ stop formula with credit+buffer (Brian's approach): stop = total_credit + $0.10 instead of total_credit - $0.15. Extra cushion reduces marginal stops. Fix: stop level validation now per-side (prevents skipping active side when stopped side has 0). Telegram /set updated: `stop_buffer` replaces `meic_plus`.
- **1.10.1** (2026-03-09): Fix #83: Emergency close improvements — skip worthless long legs (bid=$0), $0.05 min tick fallback, cancel zombie 409 orders, dynamic limit-only handling. Fix #84: Dashboard P&L history updated after settlement. Commission tracks actual legs closed.
- **1.10.0** (2026-03-08): MKT-034 VIX-scaled entry time shifting. Entry execution at :14:30/:44:30 (30s before :15/:45 marks). VIX gate checks at :14:00/:44:00 — blocks E#1 if VIX >= threshold (20/23), shifts schedule to later slots. Floor at 12:14:30. Early close cutoff raised to 12:30 PM. Configurable via `vix_time_shift` config section.
- **1.9.4** (2026-03-08): Configurable stop close mode via `long_salvage.short_only_stop` (default: false = close both legs). Added /clio Telegram command (15 total). Updated all agent prompts to v1.9.3 parameters.
- **1.9.3** (2026-03-07): Actual stop debit tracking for per-entry P&L accuracy. Added actual_call_stop_debit/actual_put_stop_debit fields. Dashboard uses actual when available.
- **1.9.2** (2026-03-05): MKT-033 long leg salvage after short stop (requires `short_only_stop: true`). Sells surviving long if appreciated >= $10. Config: `long_salvage.enabled`, `long_salvage.min_profit`.
- **1.9.1** (2026-03-05): MKT-032 VIX gate for put-only entries. Put-only only when VIX < 18 (80% WR calm markets); at VIX >= 18 skip instead. Configurable via `put_only_max_vix`. (Gate raised to 25 in v1.15.0/MKT-039.)
- **1.8.0** (2026-03-04): Entry schedule shifted to :15/:45 offset (11:15-13:15), MKT-031 smart entry windows (10min scouting, 2-parameter scoring: ATR calm 0-70pts + momentum pause 0-30pts, threshold 65), early close day cutoff raised to 12:00 PM
- **1.7.2** (2026-03-03): Lower call minimum from $1.00 to $0.75 (credit cushion analysis)
- **1.7.1** (2026-03-03): Re-enable MKT-011 put-only entries (87.5% WR, +$870 net). Strict $1.00 call min.
- **1.7.0** (2026-03-03): 8 new Telegram commands (/status, /hermes, /apollo, /week, /entry, /stops, /config, /help)
- **1.6.2** (2026-03-03): MKT-029 graduated credit fallback thresholds
- **1.6.1** (2026-03-03): Telegram /lastday and /account commands
- **1.6.0** (2026-03-02): Drop Entry #6 (frees margin for wider puts), MKT-028 asymmetric spreads (call 60pt, put 75pt floor, cap 75pt), MKT-024 updated (3.5× calls, 4.0× puts), MKT-027 VIX-scaled spread width continuous formula
- **1.5.0** (2026-02-28): Rename MEIC-TF → HYDRA (service, state, metrics, Sheets all renamed), Telegram /snapshot command, 30-min periodic position snapshots, alert system channel routing + BOT_STARTED/STOPPED + error isolation
- **1.4.5** (2026-02-28): MKT-026 min spread width raised from 25pt to 60pt (longs cheaper on low-VIX days)
- **1.4.4** (2026-02-28): Add 6th entry at 12:35 PM (matching base MEIC schedule — MKT-011 credit gate ensures zero-cost skip when non-viable; later dropped in v1.6.0)
- **1.4.3** (2026-02-28): MKT-025 short-only stop loss close (configurable since v1.9.4; default: close both legs)
- **1.4.2** (2026-02-27): MEIC+ reduction raised from $0.10 to $0.15 to cover commission on one-side-stop (true breakeven)
- **1.4.1** (2026-02-27): MKT-024 wider starting OTM (2× multiplier both sides), separate put minimum $1.75 (Tammy's $1.00-$1.75 range), enhanced MKT-020/022 scan logging
- **1.4.0** (2026-02-27): Remove MKT-019 (revert to total_credit stop), disable all one-sided entries (EMA signal informational only, always full IC or skip)
- **1.3.11** (2026-02-25): MKT-018 early close threshold raised from 2% to 3% ROC
- **1.3.9** (2026-02-25): MKT-021 ROC gate lowered from 5 to 3 entries
- **1.3.7** (2026-02-24): MKT-023 smart hold check before early close
- **1.3.6** (2026-02-24): MKT-011 one-sided entries only for clear trends
- **1.3.5** (2026-02-24): MKT-022 progressive put OTM tightening
- **1.3.4** (2026-02-23): Fix #82 - Settlement gate lock bug
- **1.3.3** (2026-02-23): Remove MKT-016/017/base loss limit
- **1.3.2** (2026-02-20): MKT-021 pre-entry ROC gate + Fix #81
- **1.3.1** (2026-02-20): MKT-020 progressive call OTM tightening, min credit $1.00/side
- **1.3.0** (2026-02-19): MKT-019 virtual equal credit stop + MKT-018 early close + batch quote API
- **1.2.9** (2026-02-18): Fix #77/#78/#79 (settlement, summary, counters)
- **1.2.8** (2026-02-17): EMA threshold 0.2%
- **1.2.7** (2026-02-16): Daily Summary column redesign, Fix #76
- **1.2.6** (2026-02-13): Fix #75 - Async deferred stop fill lookup
- **1.2.5** (2026-02-13): Fix #74 - Stop loss fill price accuracy
- **1.2.4** (2026-02-13): Code audit hardening
- **1.2.3** (2026-02-12): Fix #70 - Accurate fill price tracking
- **1.2.2** (2026-02-12): Fix #65-#68 - Recovery, overlap, timeout protection
- **1.2.1** (2026-02-12): Fix #71-#73 - Summary, P&L, active entries
- **1.2.0** (2026-02-12): Accurate P&L tracking and daily summary fixes
- **1.1.8** (2026-02-11): Fix #64 - Google Sheets timeout protection
- **1.1.7** (2026-02-11): Fix #63 - EUR conversion
- **1.1.6** (2026-02-11): Fix #62 - EMA in Account Summary
- **1.1.5** (2026-02-11): MKT-014, counter tracking, merge detection
- **1.1.4** (2026-02-10): MKT-013 same-strike overlap prevention
- **1.1.3** (2026-02-10): Logging accuracy (Fix #49)
- **1.1.2** (2026-02-10): P&L tracking (Fix #46/#47)
- **1.1.1** (2026-02-09): Hybrid credit gate
- **1.1.0** (2026-02-08): MKT-011 credit gate, MKT-010 fallback
- **1.0.0** (2026-02-04): Initial implementation
