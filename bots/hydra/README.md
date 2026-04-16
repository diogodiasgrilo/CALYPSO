# HYDRA (Trend Following Hybrid) Trading Bot

**Version:** 1.22.3 | **Last Updated:** 2026-04-12

A modified MEIC bot that adds EMA-based trend direction detection, pre-entry credit validation, progressive OTM tightening, and hold-to-expiry profit management.

## Strategy Overview

HYDRA combines Tammy Chambless's MEIC (Multiple Entry Iron Condors) with trend-following concepts from METF:

- **Before each entry**, check 20 EMA vs 40 EMA on SPX 1-minute bars
- **EMA signal is informational only** — logged and stored but does NOT drive entry type
- **Base entries are full iron condors or one-sided** — call credit non-viable → put-only if VIX < 15.0 (MKT-032/MKT-039), skip if VIX >= 15.0; put credit non-viable → call-only (MKT-040, 89% WR). Conditional entry E6 fires as put-only when SPX rises >= 0.25% above session open (Upday-035)

### Why This Works

On February 4, 2026, pure MEIC had all 6 entries get their PUT side stopped because the market was in a sustained downtrend. HYDRA addresses this with pre-entry credit validation (MKT-011), progressive OTM tightening (MKT-020/022), and wider starting OTM (MKT-024).

### Entry Schedule (3 base + 1 conditional entry)

**Current schedule (v1.19.0+ — walk-forward backtest convergence):**

| Entry | Time (ET) | Type | Notes |
|-------|-----------|------|-------|
| 1 | 10:15 | Base | Always attempts (full IC or one-sided) |
| 2 | 10:45 | Base | Always attempts |
| 3 | 11:15 | Base | Always attempts |
| 6 | 14:00 | Conditional (Upday-035) | Only fires on up days as put-only |

E4 (11:45) and E5 (12:15) dropped in v1.19.0 — negative EV in walk-forward backtest. E7 (13:15) DISABLED. E6 fires as put-only when SPX rises >= 0.25% above session open (Upday-035).

On early close days, cutoff is 12:30 PM. MKT-034 (VIX-scaled time shifting) is disabled — neither Tammy Chambless nor John Sandvand use VIX-based scheduling. Code preserved and configurable via `vix_time_shift.enabled`.

### Smart Entry Windows (MKT-031) — v1.8.0

Before each scheduled entry, a 10-minute scouting window opens. Market conditions are scored every main-loop cycle (~2-5s). If score >= 65, the bot enters early. Otherwise, enters at the scheduled time (zero-risk fallback).

**Scoring (2 parameters, 100 max):**

| Parameter | Points | Data Source |
|-----------|--------|-------------|
| Post-spike calm (ATR declining from elevated) | 0-70 | `get_chart_data()` 1-min OHLC, cached |
| Momentum pause (price calm over 2 min) | 0-30 | `MarketData.price_history` deque (zero API cost) |

### Conditional Entry Trigger (MKT-035 / Upday-035)

**MKT-035 (down-day call-only):** When SPX drops >= 0.57% below session open, conditional entries fire as call-only. Base entries E1-E3 convert to call-only on down days via `base_entry_downday_callonly_pct: 0.0057`. E7 is DISABLED.

**Upday-035 (up-day put-only):** When SPX rises >= 0.25% above session open, E6 (14:00) fires as put-only. Stop = put_credit + put_stop_buffer ($1.55).

Base entries (#1-3) are unaffected by conditional triggers — they always attempt full ICs (or one-sided via MKT-011).

### Credit Gate (MKT-011)

Before placing any orders, HYDRA estimates the expected credit by fetching option quotes. **Effective thresholds are VIX-regime-dependent** (see the VIX Regime Adaptive section below for the live values — currently $1.00 / $1.25 at VIX<18, scaling down to $0.30 / $0.40 at VIX≥28). When the VIX regime is active, `call_credit_floor` / `put_credit_floor` are overwritten to `min_credit − $0.10` (MKT-029 graduated fallback steps −$0.05, −$0.10 down to that floor). MKT-035 / Upday-035 / MKT-038 one-sided entries use the same active floor.

The table below shows the DECISION LOGIC. Replace `call_min` / `put_min` / `call_floor` / `put_floor` with the active regime values:

| Condition | Call Credit | Put Credit | VIX | Action |
|-----------|-------------|------------|-----|--------|
| Conditional entry (MKT-035) | >= call_floor | N/A | Any | Place call-only entry |
| Conditional entry (MKT-035) | < call_floor | N/A | Any | Skip entry |
| Conditional entry (Upday-035) | N/A | >= put_floor | Any | Place put-only entry |
| FOMC T+1 (MKT-038) | >= call_floor | N/A | Any | Place call-only entry |
| FOMC T+1 (MKT-038) | < call_floor | N/A | Any | Skip entry |
| Base entry | >= call_min (call_floor w/ MKT-029) | >= put_min (put_floor w/ MKT-029) | Any | Proceed with full iron condor |
| Normal | < call_floor | >= put_floor | < 15.0 | Place put-only entry (MKT-032/MKT-039 allows) |
| Normal | < call_floor | >= put_floor | >= 15.0 | Skip entry (MKT-032: no call hedge in volatile conditions) |
| Normal | >= call_min (call_floor w/ MKT-029) | < put_floor | Any | Retry with tighter put strikes (5pt, max 2 retries), then call-only (MKT-040: 89% WR) |
| Normal | < call_floor | < put_floor | Any | Skip entry |

### Illiquidity Fallback (MKT-010)

If the credit gate can't get valid quotes (rare), it falls back to wing illiquidity flags set during strike calculation. Any wing illiquid → skip entry.

### Wider Starting OTM (MKT-024) - Updated v1.6.0

Calls start at 3.5× and puts at 4.0× the VIX-adjusted OTM distance (asymmetric — put multiplier higher because put skew means credit is viable further OTM). MKT-020/MKT-022 then scan inward from there to find the widest viable strike at or above the minimum credit threshold. Batch API = zero extra cost for wider scan.

### Progressive OTM Tightening (MKT-020 Calls / MKT-022 Puts)

From the MKT-024 starting distance, progressively moves the short strike closer to ATM in 5pt steps until credit meets the active regime minimum (see VIX Regime Adaptive below — with MKT-029 fallback down to `min_credit − $0.10`) or a 25pt OTM floor is reached.

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
        "enabled": false,
        "window_minutes": 10,
        "score_threshold": 65,
        "momentum_threshold_pct": 0.05
    },
    "vix_time_shift": {
        "enabled": false,
        "medium_vix_threshold": 20.0,
        "high_vix_threshold": 23.0
    },
    "whipsaw_filter": {
        "enabled": true,
        "threshold": 1.75
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
| `downday_threshold_pct` | `0.003` | 0.3% — SPX must drop this much below session open to trigger E6/E7 conditional (E7 DISABLED) |
| `downday_theoretical_put_credit` | `2.60` | Theoretical put credit ($) for stop calculation (v1.19.0) |
| `base_entry_downday_callonly_pct` | `0.0057` | Base entries E1-E3 convert to call-only when SPX drops >= 0.57% from open |
| `conditional_entry_times` | `["14:00"]` | Extra entry times for conditional entries (v1.19.0: E6 at 14:00) |
| `conditional_e6_enabled` | `false` | Enable/disable E6 conditional entry. **MKT-035 E6 DISABLED**; Upday-035 E6 controlled separately |
| `conditional_e7_enabled` | `false` | Enable/disable E7 (13:15) conditional entry. **DISABLED in v1.19.0** |
| `conditional_upday_e6_enabled` | `true` | Upday-035: E6 fires as put-only when SPX rises >= 0.25% (v1.19.0) |
| `upday_threshold_pct` | `0.0025` | 0.25% — SPX must rise this much above session open for Upday-035 (v1.20.1) |

### FOMC Announcement Day Override

| Key | Default | Description |
|-----|---------|-------------|
| `fomc_announcement_skip` | `false` | MKT-008: Skip all entries on FOMC announcement day. Set `false` to trade normally. **v1.19.0: Changed to `false`** — backtest showed trading FOMC days is profitable. |

### Stop Confirmation Timer (MKT-036) — INTENTIONALLY DISABLED

MKT-036 stop confirmation timer is **intentionally disabled**. The $5.00 put buffer (`put_stop_buffer`) is the chosen solution for false stops instead. Code preserved but dormant — set `stop_confirmation_enabled: true` to re-enable.

When enabled: 75-second confirmation window before executing stop. 20-day backtest: 17 false stops avoided ($2,870 saved), 1 real stop missed ($85).

| Setting | Default | Description |
|---------|---------|-------------|
| `stop_confirmation_enabled` | `false` | Enable/disable MKT-036 stop confirmation timer (DISABLED) |
| `stop_confirmation_seconds` | `75` | Duration (seconds) breach must sustain before executing stop |
| `call_stop_buffer` | `0.35` | Call stop buffer: call_stop = credit + $0.35 (v1.19.0, renamed from `stop_buffer`) |
| `put_stop_buffer` | `1.55` | Put stop buffer: put_stop = credit + $1.55 (v1.19.0, walk-forward optimized). Falls back to `call_stop_buffer` if not set. |

### FOMC T+1 Call-Only (MKT-038)

On the day after FOMC announcement (T+1), forces all entries to call-only spreads. Research shows T+1 is 66.7% down days with 23% more volatility — put-side exposure is dangerous.

| Setting | Default | Description |
|---------|---------|-------------|
| `fomc_t1_callonly_enabled` | `true` | Force call-only entries on day after FOMC announcement |

Stop formula for MKT-038 entries: `call_credit + theoretical $2.60 put + call buffer` (same as MKT-035).

### Whipsaw Filter (v1.19.0)

Skips entries when intraday range exceeds a threshold relative to the expected move. High whipsaw days are bad for iron condors — price oscillates through strike levels, triggering false stops.

| Setting | Default | Description |
|---------|---------|-------------|
| `whipsaw_filter.enabled` | `true` | Enable/disable whipsaw filter |
| `whipsaw_filter.threshold` | `1.75` | Skip entry when intraday range > 1.75× expected move |

### VIX Regime Adaptive (updated 2026-04-14)

Adjusts entries AND credit thresholds based on VIX at open. Uses a 4-zone breakpoint system. When `max_entries` caps below base count, drops EARLIEST entries (keeps best-performing E#3 at 11:15). **All regime credit slots are now filled, so the base `min_viable_credit_per_side` / `min_viable_credit_put_side` are effectively dead — the regime always overrides.**

| Zone | VIX Range | Max Entries | Entries Kept | Call Min | Put Min | Effective Call Floor | Effective Put Floor |
|------|-----------|-------------|--------------|----------|---------|----------------------|---------------------|
| 0 | < 18 | 3 (default) | E#1, E#2, E#3 | $1.00 | $1.25 | $0.90 | $1.15 |
| 1 | 18-22 | 2 | E#2, E#3 | $0.50 | $0.75 | $0.40 | $0.65 |
| 2 | 22-28 | 2 | E#2, E#3 | $0.30 | $0.50 | $0.20 | $0.40 |
| 3 | >= 28 | 1 | E#3 only | $0.30 | $0.40 | $0.20 | $0.30 |

When the regime applies, `call_credit_floor` / `put_credit_floor` are recomputed as `min_credit − $0.10`; the top-level `call_credit_floor` ($0.20) and `put_credit_floor` ($0.30) in config only apply if the regime is disabled.

| Setting | Live VM Value | Description |
|---------|---------------|-------------|
| `vix_regime.enabled` | `true` | Enable/disable VIX regime adaptive |
| `vix_regime.breakpoints` | `[18.0, 22.0, 28.0]` | VIX zone boundaries |
| `vix_regime.max_entries` | `[null, 2, 2, 1]` | Max entries per zone (null = use default 3). Drops EARLIEST when capped. |
| `vix_regime.min_call_credit` | `[1.00, 0.50, 0.30, 0.30]` | Per-zone call credit threshold |
| `vix_regime.min_put_credit` | `[1.25, 0.75, 0.50, 0.40]` | Per-zone put credit threshold |
| `vix_regime.shadow_call_otm` | `[40.0, 50.0, 75.0, 75.0]` | v7: OTM target (pt) for shadow_entries logging (observation only, no trading effect) |
| `vix_regime.shadow_put_otm` | `[50.0, 75.0, 110.0, 90.0]` | v7: OTM target (pt) for shadow_entries logging |
| `vix_regime.put_stop_buffer` | `[null, null, null, null]` | Per-zone put buffer override (null = use base `put_stop_buffer`) |
| `vix_regime.call_stop_buffer` | `[null, null, null, null]` | Per-zone call buffer override (null = use base `call_stop_buffer`) |

### Buffer Decay (MKT-042) — v1.22.0

Time-decaying stop buffer that starts wider and linearly decays to 1× over a configurable period. Provides wider stops early when premium is rich and moves are noisy, normal stops later as theta decays.

| Setting | Default | Description |
|---------|---------|-------------|
| `buffer_decay_start_mult` | `2.10` | Starting multiplier (2.10× = buffer is 2.10× normal at entry time) |
| `buffer_decay_hours` | `2.0` | Hours to decay from start_mult to 1× |

Set `buffer_decay_start_mult` to `1.0` or `null` to disable.

### Calm Entry Filter (MKT-043) — v1.22.0

Delays entry when SPX moved sharply in the recent lookback window. Prevents entering during spikes that inflate premium but reverse quickly.

| Setting | Default | Description |
|---------|---------|-------------|
| `calm_entry_lookback_min` | `3` | Lookback window in minutes |
| `calm_entry_threshold_pts` | `15.0` | SPX movement threshold (points) |
| `calm_entry_max_delay_min` | `5` | Maximum delay before entering anyway |

Set `calm_entry_threshold_pts` to `null` to disable.

### Cushion Recovery Exit (MKT-041) — v1.21.0, DISABLED

Closes individual IC sides when they nearly hit their stop then recover. **DISABLED** because buffer decay (MKT-042) and the put buffer ($1.55) interfere — the wider buffer already prevents false stops that MKT-041 was designed to catch.

| Setting | Default | Description |
|---------|---------|-------------|
| `cushion_nearstop_pct` | `null` | Fraction of stop level to trigger near-stop (e.g., 0.96). null = disabled. |
| `cushion_recovery_pct` | `null` | Fraction of stop level to trigger recovery close (e.g., 0.67). null = disabled. |

### Chain Strike Snapping (MKT-045) — v1.23.0

After MKT-020/MKT-022 tightening and overlap adjustments (MKT-013/015, Fix #44/#66), snaps all 4 strikes to the nearest actual Saxo chain strike (max 25pt tolerance). Saxo's 0DTE chain uses 5pt intervals near ATM but switches to 10-25pt intervals far OTM — overlap adjustments that blindly add 5pt can land on non-existent strikes. Re-runs overlap checks once after snapping. MKT-044 (inside MKT-020/022) also snaps both sides after overlap re-runs.

### Stop Anti-Spike Filter (MKT-046) — v1.23.0

When MKT-036 is disabled (current config), requires stop breach to persist for 10 seconds before executing. Filters momentary bid/ask spikes that inflate mid-price without a real underlying move. On first breach, logs full bid/ask detail (`STOP-DETAIL`). If spread recovers within 10s, stop is avoided and logged as `MKT-046_FALSE_STOP_AVOIDED`. When MKT-036 is enabled, its longer timer (75s) takes precedence.

### Early Close on ROC (MKT-018/023/021) — INTENTIONALLY DISABLED

Early close (MKT-018), smart hold check (MKT-023), and pre-entry ROC gate (MKT-021) are **intentionally disabled**. Backtest analysis showed no ROC-based early close configuration beats hold-to-expiry for this strategy. The code is preserved but dormant — set `early_close_enabled: true` in config to re-enable. See `docs/HYDRA_EARLY_CLOSE_ANALYSIS.md` for the full analysis.

### Credit Gate & Tightening Config (strategy section)

| Setting | Default | Description |
|---------|---------|-------------|
| `min_viable_credit_per_side` | `2.00` | MKT-011/MKT-020: Call minimum credit — **base fallback only**, overridden by `vix_regime.min_call_credit` at every VIX level in live config |
| `min_viable_credit_put_side` | `2.75` | MKT-011/MKT-022: Put minimum credit — **base fallback only**, overridden by `vix_regime.min_put_credit` at every VIX level in live config |
| `call_credit_floor` | `0.20` | MKT-029 fallback floor for calls when VIX regime disabled. When regime is active, floor is recomputed as `min_call_credit − $0.10`. |
| `put_credit_floor` | `0.30` | MKT-029 fallback floor for puts when VIX regime disabled. When regime is active, floor is recomputed as `min_put_credit − $0.10`. |
| `put_only_max_vix` | `15.0` | MKT-032/MKT-039: Max VIX for put-only entries (skip instead when VIX >= threshold). Lowered 25→15 in v1.19.0. |
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
| Entries per day | 6 | 3 base + 1 conditional (v1.19.0, was 5+2) |
| Starting OTM | VIX-adjusted | 3.5× calls, 4.0× puts (MKT-024), then tightened |
| Spread widths | 50pt fixed | VIX × 6.0, floor 25pt, cap 110pt (MKT-027 v1.19.0) |
| Credit minimums | $0.50/side | VIX-regime-dependent (see VIX Regime Adaptive table): $1.00 / $1.25 at VIX<18 down to $0.30 / $0.40 at VIX≥28 |
| Trend signal | None | EMA 20/40 (informational only) |
| Smart entry | None | MKT-031 10-min scouting windows (DISABLED) |
| Whipsaw filter | None | Skip entries when range > 1.75× expected move (v1.19.0) |
| VIX regime | None | Adaptive entries/buffers based on VIX at open (v1.20.0) |
| Buffer decay | None | MKT-042: 2.10× buffer at entry, decays to 1× over 2h (v1.22.0) |
| Calm entry | None | MKT-043: delays entry up to 5min on sharp SPX moves (v1.22.0) |
| Profit management | Hold to expiration | Hold to expiration (MKT-018 early close disabled) |
| Stop formula | total_credit - $0.10 | total_credit + asymmetric buffer (call $0.35, put $1.55). MKT-036 timer DISABLED. |
| FOMC handling | Skip announcement day | Trade FOMC days (fomc_skip=false) + T+1 call-only (MKT-038) |
| Stop execution | Close both legs | Close both legs (default) or SHORT only when `short_only_stop: true` (MKT-025 + MKT-033) |

## Risk Considerations

1. **Skip rate**: VIX-regime-dependent credit gates may skip entries when quotes can't reach the active minimum (see VIX Regime Adaptive for live thresholds)
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

- **1.23.0** (2026-04-13): VIX regime reconvergence + Schema v7 shadow entry logging. Breakpoints updated `[14, 20, 30]` → `[18.0, 22.0, 28.0]` after per-entry analysis showed Entry #1 (10:15) underperforms at all VIX ≥ 18 (24% WR, -$79/entry). `max_entries` reshaped `[2, null, null, 1]` → `[null, 2, 2, 1]` (drops E#1 at VIX ≥ 18). Per-regime credit thresholds added at VIX ≥ 22 ($0.75 call / $1.25 put at zone 2, $0.50 / $0.75 at zone 3) to force strikes 60-100pt OTM. Code fix near `strategy.py:7721` — VIX regime cap now drops EARLIEST entries (preserves best-performing E#3 at 11:15), previously dropped latest. New `shadow_entries` SQLite table records what OTM-based strike selection WOULD have chosen alongside actual credit-based selection (observation only). Fix 2026-04-14: `_record_shadow_entry()` moved outside main DB-write try/except so shadow data survives upstream failures. Config-audit library + backtest-vs-live calibration scripts added under `scripts/`.
- **1.22.1** (2026-04-02): MKT-042 buffer decay optimal: 2.10× start, 2h decay. Docs audit for MKT-041/042/043.
- **1.22.0** (2026-04-02): MKT-042 Buffer Decay + MKT-043 Calm Entry. Buffer decay: starts at 2.10× normal buffer, linearly decays to 1× over 2h. Calm entry: delays entry up to 5min when SPX moved >15pt in 3min.
- **1.21.0** (2026-03-31): MKT-041 Cushion Recovery Exit (DISABLED — buffer+cushion interfere). Backtest infrastructure for sweep analysis.
- **1.20.1** (2026-03-31): Full reconvergence audit. Tighter credit gates (call $2.00, put $2.75). Config template sync.
- **1.20.0** (2026-03-30): Reconvergence + skip_weekdays, VIX regime adaptive, dow_max_entries. VIX regime: breakpoints [14,20,30], adaptive entries/buffers per zone.
- **1.19.0** (2026-03-29): Walk-forward backtest convergence. 3 base entries at 10:15, 10:45, 11:15 (E4/E5 dropped — negative EV). E6 upday put-only ENABLED at 14:00 (threshold 0.25%). E7 DISABLED. Spread width: VIX × 6.0, floor 25pt, cap 110pt. Credit gates: call $2.00, put $2.75, call_floor $0.75, put_floor $2.00. Stop buffers: call_stop_buffer $0.35, put_stop_buffer $1.55. FOMC skip FALSE, T+1 call-only TRUE. Downday threshold 0.57%, theo put $2.60. Upday threshold 0.25%. Whipsaw filter 1.75× EM. put_only_max_vix 15.0.
- **1.16.1** (2026-03-19): MKT-029 graduated call fallback in credit gate. Calls now have graduated fallback like puts: $0.60→$0.55→$0.50. MKT-035/MKT-038 call-only skip checks lowered from $0.60 to $0.50 floor. Fixed stale comments ($0.75→$0.60, $1.75→$2.50). All agent prompts updated.
- **1.16.0** (2026-03-16): Skip alerts + dashboard improvements. Telegram ENTRY_SKIPPED alerts at all 8 skip paths with detailed reasons. Skipped entries persisted in state file with `skip_reason` field. `entry_schedule` (base + conditional times) added to state file. Dashboard: mobile-responsive header, pending cards show scheduled times, skipped cards show reason. HERMES trimmed state includes `entry_schedule` + `skip_reason`.
- **1.15.1** (2026-03-16): MKT-040 call-only entries when put credit non-viable. When put credit < $2.50 but call credit >= $0.60, place call-only entry instead of skipping. Data: 89% WR for low-credit call-only entries, +$46 EV per entry. Stop = call + theo $2.50 put + buffer (unified with MKT-035/038). No VIX gate (unlike MKT-032 for put-only). Gated by existing `one_sided_entries_enabled` config. Override reason: `mkt-040`.
- **1.15.0** (2026-03-16): MKT-039 put-only stop tightening + MKT-032 VIX gate raise. Put-only stop changed from 2×credit+buffer to credit+buffer — $5.00 put buffer already prevents 91% false stops, 2× was redundant (max loss $750→$500). MKT-032 VIX gate raised 18→25 (tighter stop makes put-only viable at moderate VIX). Call-only later unified to call + theo $2.50 put + buffer. All agent SYSTEM_PROMPTs updated to v1.15.0.
- **1.14.0** (2026-03-15): MKT-038 FOMC T+1 call-only mode. Day after FOMC announcement: all entries forced to call-only. T+1 = 66.7% down days, 23% more volatile. Stop = call_credit + theoretical $2.50 put + buffer. MKT-036 stop confirmation timer documented as DISABLED (code preserved, $5.00 put buffer is the chosen solution). Telegram `/status` shows T+1 status. All agent SYSTEM_PROMPTs updated to v1.13.0. `stop_confirmation_enabled` default changed to `false`.
- **1.13.0** (2026-03-13): Stop timestamps in state file. Dashboard SPX chart stop markers + entry strike lines. MKT-035 scoped to conditional entries only.
- **1.12.1** (2026-03-12): Asymmetric put stop buffer ($5.00 put vs $0.10 call). 21-day backtest: 91% false put stops avoided.
- **1.12.0** (2026-03-11): MKT-036 stop confirmation timer code deployed. Subsequently DISABLED on VM — $5.00 put buffer (`put_stop_buffer`) chosen as the solution instead. Code preserved, configurable via `stop_confirmation_enabled`.
- **1.11.0** (2026-03-11): MKT-035 call-only on down days. When SPX < open -0.3%, place call spread only (no puts). Stop = call_credit + theoretical $2.50 put + buffer. 20-day data: 71% put stop rate on down days vs 7% call stop rate, +$920 improvement. Two conditional entry times (12:45, 13:15) that only fire when MKT-035 triggers as call-only. Configurable via `downday_callonly_enabled`, `downday_threshold_pct`, `downday_theoretical_put_credit`, `conditional_entry_times`.
- **1.10.3** (2026-03-11): Disable MKT-034 VIX time shifting + remove VIX entry cutoff (max_vix_entry=999). Neither Tammy Chambless nor John Sandvand use VIX cutoffs or time shifting (both studied VIX correlation, found none). Entry times revert to 10:15 AM start (winning period Feb 10-27). Spread widths reverted to 50pt. MKT-034 remains configurable (`vix_time_shift.enabled`).
- **1.10.2** (2026-03-10): Replace MEIC+ stop formula with credit+buffer (Brian's approach): stop = total_credit + $0.10 instead of total_credit - $0.15. Extra cushion reduces marginal stops. Fix: stop level validation now per-side (prevents skipping active side when stopped side has 0). Telegram /set updated: `call_stop_buffer` replaces `meic_plus`.
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
