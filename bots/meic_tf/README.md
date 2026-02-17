# MEIC-TF (Trend Following Hybrid) Trading Bot

**Version:** 1.2.8 | **Last Updated:** 2026-02-17

A modified MEIC bot that adds EMA-based trend direction detection to avoid losses on strong trend days, plus pre-entry credit validation to skip illiquid entries.

## Strategy Overview

MEIC-TF combines Tammy Chambless's MEIC (Multiple Entry Iron Condors) with trend-following concepts from METF:

- **Before each entry**, check 20 EMA vs 40 EMA on SPX 1-minute bars
- **BULLISH** (20 > 40): Place PUT spread only (calls are risky in uptrend)
- **BEARISH** (20 < 40): Place CALL spread only (puts are risky in downtrend)
- **NEUTRAL** (within 0.2%): Place full iron condor (standard MEIC behavior)

### Why This Works

On February 4, 2026, pure MEIC had all 6 entries get their PUT side stopped because the market was in a sustained downtrend. MEIC-TF would have detected the bearish trend and only placed call spreads, avoiding ~$1,500 in losses.

### Entry Schedule (5 entries, reduced from MEIC's 6)

| Entry | Time (ET) |
|-------|-----------|
| 1 | 10:05 AM |
| 2 | 10:35 AM |
| 3 | 11:05 AM |
| 4 | 11:35 AM |
| 5 | 12:05 PM |

### Trend-Based Entry Logic

| Trend Signal | What Gets Placed | Rationale |
|--------------|------------------|-----------|
| BULLISH | PUT spread only | Uptrend → calls risky, puts safe |
| BEARISH | CALL spread only | Downtrend → puts risky, calls safe |
| NEUTRAL | Full iron condor | Range-bound → both sides safe |

### Credit Gate (MKT-011) - Updated v1.1.1

Before placing any orders, MEIC-TF estimates the expected credit by fetching option quotes.

**Key Design: Trend Filter Takes Priority**

MKT-011 respects the trend filter - it won't force you into a trade that contradicts the trend direction:

| Trend | Preferred Side | If Non-Viable | Action |
|-------|----------------|---------------|--------|
| NEUTRAL | Full IC | Call non-viable | Convert to PUT-only ✅ |
| NEUTRAL | Full IC | Put non-viable | Convert to CALL-only ✅ |
| NEUTRAL | Full IC | Both non-viable | Skip entry |
| BULLISH | PUT spread | Put non-viable | **Skip entry** (won't place calls in uptrend) |
| BEARISH | CALL spread | Call non-viable | **Skip entry** (won't place puts in downtrend) |

**Why this design?** The trend filter exists to protect against directional risk. If the market is trending up (BULLISH), we don't want to sell call spreads - even if they're the only "viable" option. Better to skip the entry than contradict the safety mechanism.

### Illiquidity Fallback (MKT-010)

If the credit gate can't get valid quotes (rare), it falls back to wing illiquidity flags set during strike calculation. Same trend-respecting logic applies:

| Trend | Illiquid Wing | Action |
|-------|---------------|--------|
| NEUTRAL | Call wing | Convert to PUT-only |
| NEUTRAL | Put wing | Convert to CALL-only |
| BULLISH | Put wing | **Skip entry** (won't place calls) |
| BEARISH | Call wing | **Skip entry** (won't place puts) |
| Any | Both wings | Skip entry |

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

### Stop Cascade Breaker (MKT-016) - Added v1.2.8

After a configurable number of total stops (call + put) in a single day, pause all remaining entries. Prevents compounding losses on whipsaw/reversal days where the EMA trend filter lags rapid price changes.

| Trend | Stops Hit | Remaining Entries | Action |
|-------|-----------|-------------------|--------|
| Any | < threshold | Any | Continue normally |
| Any | >= threshold | N remaining | **Skip all N entries** |

**Why this works:** On Feb 17, 2026, a V-shaped reversal caused 5/5 entries to stop. Entries #4 and #5 together lost $195 net. A cascade breaker at 3 stops would have paused Entry #4 and #5, saving $195.

### Credit Gate Config (strategy section)

| Setting | Default | Description |
|---------|---------|-------------|
| `min_viable_credit_per_side` | `0.50` | MKT-011: Skip/convert if estimated credit below this |
| `max_daily_stops_before_pause` | `3` | MKT-016: Pause entries after N total stops in a day |

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
| Entry type | Always full IC | Trend-dependent |
| Premium per entry | Higher (both sides) | Lower on trend days |
| Trend day losses | Expected (stops on one side) | Avoided (safe side only) |
| Range day behavior | Normal | Identical to MEIC |
| Complexity | Simpler | Slightly more complex |

## Risk Considerations

1. **Lower premium on trend days**: One-sided = ~50% of full IC premium
2. **Trend reversal risk**: If trend flips after entry, the "safe" side becomes risky
3. **EMA lag**: EMAs are lagging indicators; sudden reversals may not be detected immediately

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

- [MEIC Strategy Specification](../../docs/MEIC_STRATEGY_SPECIFICATION.md)
- [MEIC Edge Cases](../../docs/MEIC_EDGE_CASES.md)
- [Technical Indicators](../../shared/technical_indicators.py)

## Version History

- **1.2.8** (2026-02-17): EMA threshold widening + stop cascade breaker
  - EMA neutral threshold widened from 0.1% to 0.2% (fewer false trend signals on low-conviction moves)
  - MKT-016: Stop cascade breaker - pause all remaining entries after N total stops in a day (default: 3)
  - Based on Feb 10-17 performance analysis: 4 winning days then $740 loss on V-shaped reversal
  - EMA threshold change would have saved ~$330 on Feb 17 (fewer one-sided entries on weak trends)
  - Cascade breaker would have saved ~$195 on Feb 17 (blocked Entry #4 and #5 after 3 stops)
  - See `docs/MEIC_TF_PERFORMANCE_BASELINE.md` for full analysis and baseline data

- **1.2.7** (2026-02-16): Daily Summary column redesign (24 → 34 columns)
  - SPX OHLC: Added SPX Open, High, Low columns (Close already existed)
  - VIX OHLC: Replaced single VIX column with VIX Open, Close, High, Low
  - P&L breakdown: Added Stop Loss Debits, Commission, Expired Credits columns
  - Added Cumulative P&L (EUR) column
  - Full logical column rearrangement: Market Context → Bot Activity → Position Outcomes → P&L Breakdown → Performance & Risk → Notes
  - MarketData: Added `spx_open`, `vix_open`, `vix_low` tracking fields
  - OHLC persisted to state file and restored on mid-day restart (prevents data loss)
  - Fix: MEIC-TF `_save_state_to_disk()` was missing `market_data_ohlc` persistence (save/restore asymmetry)
  - Fix #76: Saxo activities `FilledPrice` field does NOT exist - correct field is `AveragePrice`/`ExecutionPrice`
  - Fix #76: Added `/port/v1/closedpositions` (`ClosingPrice`) as authoritative fallback for close fill prices
  - Fix #76: Two-tier fill price lookup: activities AveragePrice → closedpositions ClosingPrice

- **1.2.6** (2026-02-13): Fix #75 - Async deferred stop fill lookup
  - `_deferred_stop_fill_lookup()` blocked main loop for 10-15s per stop (3s sleep + retries)
  - In fast markets, multiple simultaneous stops would stack delays (10s × N stops)
  - Fix: Use theoretical P&L immediately, spawn daemon thread for actual price correction
  - Theoretical P&L is within ~$15 of actual (based on Feb 13 data)
  - Background thread corrects P&L within ~10s, re-saves state to disk
  - Cleanup gate ensures daily summary always uses corrected values
  - Main loop stays responsive for monitoring other entries' stop levels

- **1.2.5** (2026-02-13): Fix #74 - Stop loss fill price accuracy
  - `_get_close_fill_price()` quote fallback was bypassing Fix #70's deferred lookup
  - Emergency market orders return `FilledPrice=0` from activities (Saxo sync delay)
  - Old code fell back to current bid/ask quotes and returned them as fill prices
  - This prevented `_deferred_stop_fill_lookup()` from ever running
  - Fix: Return `None` instead of quote price, triggering the deferred lookup path
  - Deferred lookup waits 3s then retries 3×1.5s - enough for Saxo to sync actual prices
  - Impact: Feb 13 had $75 P&L understatement across 3 stops ($25 + $35 + $15)

- **1.2.4** (2026-02-13): Code audit hardening
  - Error handling: try/except wrappers for settlement, shutdown, position status, strategy init
  - Timeout protection: remaining Google Sheets `append_row` calls wrapped with 10s timeout
  - Documentation: version consistency, edge case counts aligned
  - Shared modules: `add_secret_version` timeout, account key validation warning

- **1.2.3** (2026-02-12): Fix #70 - Accurate fill price tracking
  - Verify entry fill prices against `PositionBase.OpenPrice` after all legs fill
  - Deferred stop fill lookup - waits 3s after stop close, re-checks activities for actual price

- **1.2.2** (2026-02-12): Recovery and overlap fixes
  - Fix #65: State file is authoritative source for entry classification (not position reconstruction)
  - Fix #66: Re-run strike conflict check after MKT-013 overlap adjustment
  - Fix #67/MKT-015: Prevent long-long strike overlap (Saxo merges same-strike longs)
  - Fix #68: Comprehensive timeout protection for all blocking calls

- **1.2.1** (2026-02-12): Daily summary and metrics fixes
  - Fix #71: Prevent duplicate daily summary rows after bot restart (idempotency guard)
  - Fix #72: Daily summary now uses net P&L (after commission) for all tracking
  - Fix #73: `active_entries` property now checks expired and skipped flags

- **1.2.0** (2026-02-12): Accurate P&L tracking and daily summary fixes
  - Fix #70: Verify entry fill prices against `PositionBase.OpenPrice` after all legs fill
  - Fix #70: Deferred stop fill lookup - waits 3s after stop close, re-checks activities for actual price
  - Fix #71: Prevent duplicate daily summary rows after bot restart (idempotency guard)
  - Fix #72: Daily summary now uses net P&L (after commission) for all tracking and cumulative metrics
  - Fix #73: `active_entries` property now checks expired and skipped flags, not just stopped

- **1.1.8** (2026-02-11): Fix #64 - Google Sheets API timeout protection
  - Bot froze for 3+ minutes when Google Sheets API returned 503 and hung
  - Added `_sheets_call_with_timeout()` wrapper with 10-second timeout
  - All 30+ gspread calls now protected: append_row, get_all_values, update, delete_rows, format
  - Trading operations continue even if logging fails (logging is non-critical)

- **1.1.7** (2026-02-11): Fix #63 - EUR conversion in Trades tab
  - Passes `saxo_client` to `log_trade()` calls to enable FX rate fetching
  - P&L EUR column now shows actual converted values instead of "N/A"

- **1.1.6** (2026-02-11): Fix #62 - EMA values now appear in Account Summary
  - MEIC-TF now overrides `log_account_summary()` to include EMA 20/40 values
  - Previously showed "N/A" because parent class didn't pass EMA data to logger

- **1.1.5** (2026-02-11): Liquidity re-check, counter tracking, position merge detection
  - MKT-014: After MKT-013 moves strikes to avoid overlap, re-check liquidity
  - Warns if the overlap adjustment landed on an illiquid strike
  - Fix #52-#57: Multi-contract support, accurate counter tracking (skips, one-sided, trend overrides)
  - Fix #58: Win rate calculation now handles merged entries correctly
  - Fix #59: EMA values logged in Trades tab trade_reason (instead of Account Summary)
  - Fix #61: Position merge detection - merged entries counted as wins, not stopped

- **1.1.4** (2026-02-10): Same-strike overlap prevention
  - Fix #50: Detect when new entry would land on same strikes as existing entry
  - MKT-013: Automatically offset overlapping strikes by 5 points further OTM
  - Prevents Saxo position merging which caused tracking issues (Feb 10 Entry #1/#2 incident)

- **1.1.3** (2026-02-10): Logging accuracy improvements
  - Fix #49: Correct log labels for MKT-011 vs MKT-010 vs trend-based entries
  - Log messages now show actual reason for one-sided entries (not just "BULLISH"/"BEARISH")
  - Heartbeat cushion display shows "SKIPPED" for never-opened sides (not "0%⚠️")
  - Google Sheets entries now tagged with correct override reason ([MKT-011], [MKT-010], or [BULLISH]/[BEARISH])

- **1.1.2** (2026-02-10): P&L tracking fixes
  - Fix #46: Expired positions now correctly add credit to realized P&L
  - Fix #47: Non-opened sides now marked as "skipped" instead of "stopped"
  - Proper distinction between stopped (loss), expired (profit), and skipped (never opened)
  - Fixes Feb 9 P&L discrepancy (-$360 reported vs +$170 actual)

- **1.1.1** (2026-02-09): Hybrid credit gate - respects trend filter
  - MKT-011/MKT-010 now respect trend direction
  - In trending markets: skip entry if preferred side is non-viable (won't contradict trend)
  - In NEUTRAL markets: convert to one-sided entry (same as before)
  - New safety event: MKT-011_TREND_CONFLICT logged when skipping due to trend conflict

- **1.1.0** (2026-02-08): Credit gate and illiquidity handling
  - MKT-011: Pre-entry credit estimation - skips/converts non-viable entries
  - MKT-010: Illiquidity override - fallback when quotes unavailable
  - Fixed: Illiquidity logic now trades the VIABLE side (not the illiquid side)
  - Simplified: MKT-010 is now fallback-only (MKT-011 is primary check)

- **1.0.0** (2026-02-04): Initial implementation
  - EMA 20/40 trend detection
  - One-sided entries for trending markets
  - Full IC for neutral markets
