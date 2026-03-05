# HYDRA (Trend Following Hybrid) Trading Bot

**Version:** 1.8.0 | **Last Updated:** 2026-03-04

A modified MEIC bot that adds EMA-based trend direction detection, pre-entry credit validation, progressive OTM tightening, and hold-to-expiry profit management.

## Strategy Overview

HYDRA combines Tammy Chambless's MEIC (Multiple Entry Iron Condors) with trend-following concepts from METF:

- **Before each entry**, check 20 EMA vs 40 EMA on SPX 1-minute bars
- **EMA signal is informational only** — logged and stored but does NOT drive entry type
- **Entries are full iron condors or put-only** — call credit non-viable → put-only (v1.7.1), put credit non-viable → skip (call-only disabled)

### Why This Works

On February 4, 2026, pure MEIC had all 6 entries get their PUT side stopped because the market was in a sustained downtrend. HYDRA addresses this with pre-entry credit validation (MKT-011), progressive OTM tightening (MKT-020/022), and wider starting OTM (MKT-024).

### Entry Schedule (5 entries — :15/:45 offset in v1.8.1, Entry #6 dropped in v1.6.0)

| Entry | Time (ET) | Scout Window |
|-------|-----------|-------------|
| 1 | 11:15 AM | 11:05-11:15 |
| 2 | 11:45 AM | 11:35-11:45 |
| 3 | 12:15 PM | 12:05-12:15 |
| 4 | 12:45 PM | 12:35-12:45 |
| 5 | 1:15 PM | 1:05-1:15 |

Entry times at :15/:45 offset (v1.8.1): 19-day MAE analysis showed :15/:45 has 10% lower 30-min adverse excursion vs :05/:35 (12.39pt vs 13.76pt MAE), with better tail risk (P90: 21.71pt vs 23.84pt). On early close days, cutoff is 12:00 PM (keeps entries 1-2).

### Smart Entry Windows (MKT-031) — v1.8.0

Before each scheduled entry, a 10-minute scouting window opens. Market conditions are scored every main-loop cycle (~2-5s). If score >= 65, the bot enters early. Otherwise, enters at the scheduled time (zero-risk fallback).

**Scoring (2 parameters, 100 max):**

| Parameter | Points | Data Source |
|-----------|--------|-------------|
| Post-spike calm (ATR declining from elevated) | 0-70 | `get_chart_data()` 1-min OHLC, cached |
| Momentum pause (price calm over 2 min) | 0-30 | `MarketData.price_history` deque (zero API cost) |

### Credit Gate (MKT-011)

Before placing any orders, HYDRA estimates the expected credit by fetching option quotes. Separate thresholds for calls ($0.75) and puts ($1.75). Call minimum lowered from $1.00 to $0.75 in v1.7.2 (credit cushion analysis: 68% call cushion vs 61.5% — see `docs/HYDRA_CREDIT_CUSHION_ANALYSIS.md`).

| Call Credit | Put Credit | Action |
|-------------|------------|--------|
| >= $0.75 | >= $1.75 | Proceed with full iron condor |
| < $0.75 | >= $1.75 | Place put-only entry (v1.7.1) |
| >= $0.75 | < $1.75 | Skip entry (call-only disabled) |
| < $0.75 | < $1.75 | Skip entry |

### Illiquidity Fallback (MKT-010)

If the credit gate can't get valid quotes (rare), it falls back to wing illiquidity flags set during strike calculation. Any wing illiquid → skip entry.

### Wider Starting OTM (MKT-024) - Updated v1.6.0

Calls start at 3.5× and puts at 4.0× the VIX-adjusted OTM distance (asymmetric — put multiplier higher because put skew means credit is viable further OTM). MKT-020/MKT-022 then scan inward from there to find the widest viable strike at or above the minimum credit threshold. Batch API = zero extra cost for wider scan.

### Progressive OTM Tightening (MKT-020 Calls / MKT-022 Puts)

From the MKT-024 starting distance, progressively moves the short strike closer to ATM in 5pt steps until credit meets the minimum ($0.75 for calls, $1.75 for puts) or a 25pt OTM floor is reached.

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

### Smart Entry Config (MKT-031)

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable/disable smart entry scouting |
| `window_minutes` | `10` | Scouting window before each entry (minutes) |
| `score_threshold` | `65` | Score >= this triggers early entry |
| `momentum_threshold_pct` | `0.05` | Momentum calm threshold (0.05 = 0.05%) |

### Early Close on ROC (MKT-018/023/021) — INTENTIONALLY DISABLED

Early close (MKT-018), smart hold check (MKT-023), and pre-entry ROC gate (MKT-021) are **intentionally disabled**. Backtest analysis showed no ROC-based early close configuration beats hold-to-expiry for this strategy. The code is preserved but dormant — set `early_close_enabled: true` in config to re-enable. See `docs/HYDRA_EARLY_CLOSE_ANALYSIS.md` for the full analysis.

### Credit Gate & Tightening Config (strategy section)

| Setting | Default | Description |
|---------|---------|-------------|
| `min_viable_credit_per_side` | `0.75` | MKT-011/MKT-020: Call minimum credit (lowered from $1.00 for 68% call cushion, see HYDRA_CREDIT_CUSHION_ANALYSIS.md) |
| `min_viable_credit_put_side` | `1.75` | MKT-011/MKT-022: Put minimum credit (top of Tammy's $1.00-$1.75 range) |
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
| Entry type | Always full IC | Full IC + credit gate (skip if non-viable) |
| Starting OTM | VIX-adjusted | 3.5× calls, 4.0× puts (MKT-024), then tightened |
| Spread widths | 50pt fixed | Asymmetric: call 60pt, put 75pt floor, 75pt cap (MKT-026/027/028) |
| Credit minimums | $0.50/side | $0.75 calls, $1.75 puts |
| Trend signal | None | EMA 20/40 (informational only) |
| Smart entry | None | MKT-031 10-min scouting windows (post-spike + momentum scoring) |
| Profit management | Hold to expiration | Hold to expiration (MKT-018 early close disabled) |
| Stop formula | total_credit - $0.10 | total_credit - $0.15 (covers commission) |
| Stop execution | Close both legs | Close SHORT only, long expires (MKT-025) |

## Risk Considerations

1. **Skip rate**: Stricter credit gates (especially $1.75 put minimum) may skip more entries
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

- **1.8.0** (2026-03-04): Entry schedule shifted +1hr (11:05-13:05), MKT-031 smart entry windows (10min scouting, 2-parameter scoring: ATR calm 0-70pts + momentum pause 0-30pts, threshold 65), early close day cutoff raised to 12:00 PM
- **1.7.2** (2026-03-03): Lower call minimum from $1.00 to $0.75 (credit cushion analysis)
- **1.7.1** (2026-03-03): Re-enable MKT-011 put-only entries (87.5% WR, +$870 net). Strict $1.00 call min.
- **1.7.0** (2026-03-03): 8 new Telegram commands (/status, /hermes, /apollo, /week, /entry, /stops, /config, /help)
- **1.6.2** (2026-03-03): MKT-029 graduated credit fallback thresholds
- **1.6.1** (2026-03-03): Telegram /lastday and /account commands
- **1.6.0** (2026-03-02): Drop Entry #6 (frees margin for wider puts), MKT-028 asymmetric spreads (call 60pt, put 75pt floor, cap 75pt), MKT-024 updated (3.5× calls, 4.0× puts), MKT-027 VIX-scaled spread width continuous formula
- **1.5.0** (2026-02-28): Rename MEIC-TF → HYDRA (service, state, metrics, Sheets all renamed), Telegram /snapshot command, 30-min periodic position snapshots, alert system channel routing + BOT_STARTED/STOPPED + error isolation
- **1.4.5** (2026-02-28): MKT-026 min spread width raised from 25pt to 60pt (longs cheaper on low-VIX days, MKT-025 never closes longs = pure savings)
- **1.4.4** (2026-02-28): Add 6th entry at 12:35 PM (matching base MEIC schedule — MKT-011 credit gate ensures zero-cost skip when non-viable; later dropped in v1.6.0)
- **1.4.3** (2026-02-28): MKT-025 short-only stop loss close (close short, let long expire — per Tammy/Sandvand best practice)
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
