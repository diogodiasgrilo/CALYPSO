# MEIC-TF (Trend Following Hybrid) Trading Bot

**Version:** 1.4.3 | **Last Updated:** 2026-02-28

A modified MEIC bot that adds EMA-based trend direction detection, pre-entry credit validation, progressive OTM tightening, and early close on Return on Capital.

## Strategy Overview

MEIC-TF combines Tammy Chambless's MEIC (Multiple Entry Iron Condors) with trend-following concepts from METF:

- **Before each entry**, check 20 EMA vs 40 EMA on SPX 1-minute bars
- **EMA signal is informational only** — logged and stored but does NOT drive entry type
- **All entries are full iron condors** — skip entry if either side's credit is below minimum (no one-sided entries)

### Why This Works

On February 4, 2026, pure MEIC had all 6 entries get their PUT side stopped because the market was in a sustained downtrend. MEIC-TF addresses this with pre-entry credit validation (MKT-011), progressive OTM tightening (MKT-020/022), wider starting OTM (MKT-024), and early close on profitable days (MKT-018).

### Entry Schedule (5 entries, reduced from MEIC's 6)

| Entry | Time (ET) |
|-------|-----------|
| 1 | 10:05 AM |
| 2 | 10:35 AM |
| 3 | 11:05 AM |
| 4 | 11:35 AM |
| 5 | 12:05 PM |

### Credit Gate (MKT-011)

Before placing any orders, MEIC-TF estimates the expected credit by fetching option quotes. Separate thresholds for calls ($1.00) and puts ($1.75), matching Tammy's $1.00-$1.75 per-side credit range.

| Call Credit | Put Credit | Action |
|-------------|------------|--------|
| >= $1.00 | >= $1.75 | Proceed with full iron condor |
| < $1.00 | Any | Skip entry (no one-sided entries) |
| Any | < $1.75 | Skip entry (no one-sided entries) |
| < $1.00 | < $1.75 | Skip entry |

### Illiquidity Fallback (MKT-010)

If the credit gate can't get valid quotes (rare), it falls back to wing illiquidity flags set during strike calculation. Any wing illiquid → skip entry.

### Wider Starting OTM (MKT-024) - Added v1.4.1

Both call and put sides start at 2× the VIX-adjusted OTM distance. MKT-020/MKT-022 then scan inward from there to find the widest viable strike at or above the minimum credit threshold. This gives puts more breathing room on volatile days where put skew means $1.75 is found much further OTM.

### Progressive OTM Tightening (MKT-020 Calls / MKT-022 Puts)

From the MKT-024 starting distance, progressively moves the short strike closer to ATM in 5pt steps until credit meets the minimum ($1.00 for calls, $1.75 for puts) or a 25pt OTM floor is reached.

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

### Early Close on ROC (MKT-018) - Added v1.3.0

After all entries are placed, monitors Return on Capital (ROC) every heartbeat. When ROC reaches the threshold, closes ALL active positions via market orders to lock in profit. Prevents late-day reversals from erasing gains built early in the session.

**ROC formula:** `(net_pnl - close_cost) / capital_deployed`
- `net_pnl`: realized + unrealized - commission
- `close_cost`: active_legs × $5.00 ($2.50 commission + $2.50 slippage estimate)
- `capital_deployed`: from existing `_calculate_capital_deployed()`

| Condition | Action |
|-----------|--------|
| All entries placed, ROC >= 3.0% | MKT-023 hold check → close if hold check agrees, else hold |
| All entries placed, ROC < 3.0% | Continue monitoring (shadow-log ROC when > 1%) |
| Not all entries placed yet | Skip ROC check |
| Last 15 minutes before close (3:45+ PM) | Skip ROC check (positions expire naturally) |
| Early close already triggered | Skip (idempotent) |

**After early close:**
- All positions closed via market orders (deferred fill lookup for accurate P&L)
- Daily summary, account summary, performance metrics logged immediately
- Bot transitions to DAILY_COMPLETE state (no settlement needed)
- Alert sent with locked-in P&L and ROC

### Smart Hold Check (MKT-023) - Added v1.3.7

When MKT-018's ROC threshold is met, MKT-023 checks whether holding to expiration is mathematically better than closing now — even in the worst case. It determines market lean from average cushion per side, then calculates:

- **Close now P&L**: current net P&L minus close costs (same as ROC numerator)
- **Worst-case hold P&L**: assume all stressed sides get stopped, all safe sides expire worthless

If worst-case hold > close now → **HOLD** (don't close). If worst-case hold <= close now → **CLOSE** (proceed with MKT-018).

### Pre-Entry ROC Gate (MKT-021) - Added v1.3.2

Before placing entries #4 and #5 (after min 3 entries placed), checks if ROC on existing positions already exceeds the early close threshold (3%). If so, skips remaining entries and allows MKT-018 early close to fire immediately at the higher (undiluted) ROC.

Only active when MKT-018 is enabled. Uses the same `early_close_roc_threshold` — no separate threshold needed. Sets a flag, skips remaining entries, and persists state across restarts.

### Credit Gate & Tightening Config (strategy section)

| Setting | Default | Description |
|---------|---------|-------------|
| `min_viable_credit_per_side` | `1.00` | MKT-011/MKT-020: Call minimum credit (MEIC-TF override, base is $0.50) |
| `min_viable_credit_put_side` | `1.75` | MKT-011/MKT-022: Put minimum credit (top of Tammy's $1.00-$1.75 range) |
| `call_starting_otm_multiplier` | `2.0` | MKT-024: Call starting OTM = base × multiplier |
| `put_starting_otm_multiplier` | `2.0` | MKT-024: Put starting OTM = base × multiplier |
| `min_call_otm_distance` | `25` | MKT-020: Minimum OTM distance (points) for call tightening floor |
| `min_put_otm_distance` | `25` | MKT-022: Minimum OTM distance (points) for put tightening floor |
| `early_close_enabled` | `true` | MKT-018: Enable/disable early close on ROC threshold |
| `early_close_roc_threshold` | `0.03` | MKT-018: ROC threshold for early close (3.0%) |
| `early_close_cost_per_position` | `5.00` | MKT-018: Estimated cost per leg to close ($2.50 commission + $2.50 slippage) |
| `hold_check_enabled` | `true` | MKT-023: Enable/disable smart hold check before early close |
| `hold_check_lean_tolerance` | `1.0` | MKT-023: Min cushion difference (%) to determine market lean |
| `min_entries_before_roc_gate` | `3` | MKT-021: Minimum entries placed before pre-entry ROC gate activates |

## Usage

```bash
# Run in simulation mode (no real orders)
python -m bots.meic_tf.main --dry-run

# Run with live data (real orders)
python -m bots.meic_tf.main --live

# Show current status
python -m bots.meic_tf.main --status
```

## Deployment

1. Copy config template and edit:
```bash
cp bots/meic_tf/config/config.json.template bots/meic_tf/config/config.json
# Edit config.json with your settings
```

2. Install systemd service:
```bash
sudo cp bots/meic_tf/meic_tf.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable meic_tf
sudo systemctl start meic_tf
```

3. Monitor:
```bash
sudo journalctl -u meic_tf -f
```

## Differences from Pure MEIC

| Aspect | Pure MEIC | MEIC-TF |
|--------|-----------|---------|
| Entry type | Always full IC | Full IC + credit gate (skip if non-viable) |
| Starting OTM | VIX-adjusted | 2× VIX-adjusted (MKT-024), then tightened |
| Credit minimums | $0.50/side | $1.00 calls, $1.75 puts |
| Trend signal | None | EMA 20/40 (informational only) |
| Profit management | Hold to expiration | Early close at 3% ROC (MKT-018/023/021) |
| Stop formula | total_credit - $0.10 | total_credit - $0.15 (covers commission) |
| Stop execution | Close both legs | Close SHORT only, long expires (MKT-025) |

## Risk Considerations

1. **Skip rate**: Stricter credit gates (especially $1.75 put minimum) may skip more entries
2. **Trend reversal risk**: EMA is a lagging indicator; sudden reversals may not be detected immediately
3. **Early close opportunity cost**: Closing at 3% ROC may leave additional profit on the table on strong days

## State Files

MEIC-TF uses **separate state files** from MEIC to allow both bots to run simultaneously:

| File | Description |
|------|-------------|
| `data/meic_tf_state.json` | MEIC-TF daily state (entries, P&L, stops) |
| `data/position_registry.json` | Shared with all SPX bots for position isolation |

**Important**: The Position Registry is shared across all SPX bots (MEIC, MEIC-TF, Iron Fly) to prevent position conflicts when multiple bots trade the same underlying.

## Files

```
bots/meic_tf/
├── main.py                 # Entry point
├── strategy.py             # Trend-following strategy (extends MEIC)
├── meic_tf.service         # Systemd service file
├── config/
│   └── config.json.template
└── README.md               # This file
```

## Related Documentation

- [MEIC-TF Strategy Specification](../../docs/MEIC_TF_STRATEGY_SPECIFICATION.md) — Full strategy spec: decision flows, MKT rules, performance data
- [MEIC Strategy Specification](../../docs/MEIC_STRATEGY_SPECIFICATION.md) — Base MEIC spec (inherited strike selection, stop math)
- [MEIC-TF Trading Journal](../../docs/MEIC_TF_TRADING_JOURNAL.md) — Daily results and analysis
- [MEIC-TF Early Close Analysis](../../docs/MEIC_TF_EARLY_CLOSE_ANALYSIS.md) — MKT-018 research
- [MEIC Edge Cases](../../docs/MEIC_EDGE_CASES.md)
- [Technical Indicators](../../shared/technical_indicators.py)

## Version History

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
