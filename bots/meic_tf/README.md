# MEIC-TF (Trend Following Hybrid) Trading Bot

**Version:** 1.3.7 | **Last Updated:** 2026-02-24

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
| NEUTRAL | Full IC | Either side non-viable | **Skip entry** (one-sided only for clear trends) |
| NEUTRAL | Full IC | Both non-viable | Skip entry |
| BULLISH | PUT spread | Call non-viable | Convert to PUT-only ✅ (trend confirms puts) |
| BULLISH | PUT spread | Put non-viable | **Skip entry** (won't place calls in uptrend) |
| BEARISH | CALL spread | Put non-viable | Convert to CALL-only ✅ (trend confirms calls) |
| BEARISH | CALL spread | Call non-viable | **Skip entry** (won't place puts in downtrend) |

**Why this design?** One-sided entries are directional bets. They should only happen when the trend filter confirms that direction (>= 0.2% EMA separation). In NEUTRAL markets, placing a one-sided entry creates unwanted directional exposure — better to skip and wait for the next entry.

### Illiquidity Fallback (MKT-010)

If the credit gate can't get valid quotes (rare), it falls back to wing illiquidity flags set during strike calculation. Same trend-respecting logic applies:

| Trend | Illiquid Wing | Action |
|-------|---------------|--------|
| NEUTRAL | Either wing | **Skip entry** (one-sided only for clear trends) |
| BULLISH | Call wing | Convert to PUT-only ✅ (trend confirms puts) |
| BEARISH | Put wing | Convert to CALL-only ✅ (trend confirms calls) |
| BULLISH | Put wing | **Skip entry** (won't place calls in uptrend) |
| BEARISH | Call wing | **Skip entry** (won't place puts in downtrend) |
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

### Early Close on ROC (MKT-018) - Added v1.3.0

After all entries are placed, monitors Return on Capital (ROC) every heartbeat. When ROC reaches the threshold, closes ALL active positions via market orders to lock in profit. Prevents late-day reversals from erasing gains built early in the session.

**ROC formula:** `(net_pnl - close_cost) / capital_deployed`
- `net_pnl`: realized + unrealized - commission
- `close_cost`: active_legs × $5.00 ($2.50 commission + $2.50 slippage estimate)
- `capital_deployed`: from existing `_calculate_capital_deployed()`

| Condition | Action |
|-----------|--------|
| All entries placed, ROC >= 2.0% | MKT-023 hold check → close if hold check agrees, else hold |
| All entries placed, ROC < 2.0% | Continue monitoring (shadow-log ROC when > 1%) |
| Not all entries placed yet | Skip ROC check |
| Last 15 minutes before close (3:45+ PM) | Skip ROC check (positions expire naturally) |
| Early close already triggered | Skip (idempotent) |

**Why this works:** On Feb 18, 2026, the bot had $645 unrealized profit at 1:04 PM but finished with only $315 after 2 late stops. A 2.0% ROC threshold would have closed all positions around 12:08 PM, locking in ~$400 net. Backtest over 6 trading days showed -$8 total cost (negligible) while providing early exit on high-profit days.

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

| Condition | Action |
|-----------|--------|
| ROC >= threshold, worst-case hold > close now | HOLD — don't close (safe side credits outweigh stressed stops) |
| ROC >= threshold, worst-case hold <= close now | CLOSE — MKT-018 proceeds normally |
| All entries one-sided (no opposing side) | Skip hold check → MKT-018 closes normally |
| Equal cushion both sides (within tolerance) | Skip hold check → MKT-018 closes normally |

**Heartbeat display:** `Hold Check: HOLD | close=$380 vs hold=$450 (+70) | CALLS_STRESSED (C:35%/P:82%)`

### Pre-Entry ROC Gate (MKT-021) - Added v1.3.2

Before placing each entry (after min 5 entries attempted), checks if ROC on existing positions already exceeds the early close threshold (2%). If so, skips remaining entries and allows MKT-018 early close to fire immediately at the higher (undiluted) ROC.

**Problem:** MKT-018 early close only fires after ALL entries are placed. When earlier entries are already profitable (4%+ ROC), opening more entries dilutes ROC by adding capital deployed and close costs with ~$0 P&L. The new entries either trigger early close at a lower profit, or push ROC below the threshold entirely.

**Example (Feb 20):**
- After 3 entries: +$675 net, $15K capital, ROC = 4.17%
- If entries #4/#5 opened: +$655 net, $25K capital, ROC = 2.26% (diluted)
- MKT-021 skips #4/#5, early close fires at 4.17%, locks in ~$625 instead of ~$565

| Condition | Action |
|-----------|--------|
| < 5 entries placed | Skip check (too early) |
| ROC < early_close_threshold | Continue normally, place entry |
| ROC >= early_close_threshold | Skip remaining entries, MKT-018 fires same cycle |
| Early close disabled | MKT-021 inactive |

Only active when MKT-018 is enabled. Uses the same `early_close_roc_threshold` — no separate threshold needed. Sets a flag, skips remaining entries, and persists state across restarts.

### Progressive OTM Tightening (MKT-020 Calls / MKT-022 Puts) - Added v1.3.1 / v1.3.5

For full IC entries (NEUTRAL trend), VIX-adjusted OTM distances can produce credit below the $1.00/side minimum on either side. MKT-020 (calls) and MKT-022 (puts) progressively move the short strike closer to ATM in 5pt steps until credit >= $1.00/side or a 25pt OTM floor is reached.

```
Flow: _calculate_strikes() → MKT-020 (calls) → MKT-022 (puts) → MKT-011 credit gate
  - If tightened strikes meet $1.00: proceed as full IC
  - If can't reach $1.00 at 25pt floor: MKT-011 skips entry (tightening only runs for NEUTRAL, which always skips)
```

Both use batch quote API for efficiency: 1 option chain fetch + 1 batch quote call = 2 API calls each. Only run for NEUTRAL trend entries. Include liquidity checks (skip candidates with bid/ask = 0).

### Virtual Equal Credit Stop (MKT-019) - Added v1.3.0

For full iron condor entries, stop_level = 2 × max(call_credit, put_credit) instead of total_credit. Volatility skew makes puts 2-7× more expensive than calls at the same delta, causing the low-credit call side to hit stops prematurely when using total_credit.

| Entry Type | Stop Formula | Example (C=$105, P=$370) |
|-----------|-------------|--------------------------|
| Full IC (OLD) | total_credit | $475 per side |
| Full IC (NEW) | 2 × max(credit) | $740 per side |
| One-sided | 2 × credit | Unchanged |

**Why this works:** Over 7 trading days, 3 call-side stops would have been avoided (SPX never reached the short strike) saving ~$1,675, with ~$635 additional cost on stops that still fire. Net benefit: +$1,040.

### Credit Gate Config (strategy section)

| Setting | Default | Description |
|---------|---------|-------------|
| `min_viable_credit_per_side` | `1.00` | MKT-011: Skip if estimated credit below this; one-sided conversion only for clear trends (MEIC-TF override, base is $0.50) |
| `min_call_otm_distance` | `25` | MKT-020: Minimum OTM distance (points) for call tightening floor |
| `min_put_otm_distance` | `25` | MKT-022: Minimum OTM distance (points) for put tightening floor |
| `early_close_enabled` | `true` | MKT-018: Enable/disable early close on ROC threshold |
| `early_close_roc_threshold` | `0.02` | MKT-018: ROC threshold for early close (2.0%) |
| `early_close_cost_per_position` | `5.00` | MKT-018: Estimated cost per leg to close ($2.50 commission + $2.50 slippage) |
| `hold_check_enabled` | `true` | MKT-023: Enable/disable smart hold check before early close |
| `hold_check_lean_tolerance` | `1.0` | MKT-023: Min cushion difference (%) to determine market lean |
| `min_entries_before_roc_gate` | `5` | MKT-021: Minimum entries placed before pre-entry ROC gate activates |

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

- **1.3.7** (2026-02-24): MKT-023 smart hold check before early close
  - When MKT-018 ROC threshold is met, compares close-now P&L vs worst-case-hold P&L
  - Determines market lean from average cushion per side (calls vs puts)
  - If holding is better even when all stressed sides get stopped: HOLD (don't close)
  - Falls through to MKT-018 close when: no clear lean, all one-sided, or close-now is better
  - Heartbeat shows live hold check decision with dollar comparison
- **1.3.6** (2026-02-24): MKT-011 one-sided entries only for clear trends
  - NEUTRAL markets: if either side is non-viable, skip entry (no more one-sided conversions)
  - One-sided entries only allowed when trend filter confirms direction (BULLISH/BEARISH >= 0.2% EMA)
  - Same logic applied to MKT-010 illiquidity fallback
  - Prevents unintended directional bets in range-bound markets (e.g., Entry #4 on Feb 24)
- **1.3.5** (2026-02-24): MKT-022 progressive put OTM tightening
  - Mirror of MKT-020 for put side — moves short put closer to ATM in 5pt steps until credit >= $1.00 or 25pt OTM floor
  - Batch API: 1 option chain + 1 batch quote = 2 API calls total
  - Prevents MKT-011 from converting full IC to call-only when put credit is low in flat/low-VIX markets
  - New config: `min_put_otm_distance` (default 25pt)
- **1.3.4** (2026-02-23): Fix #82 - Settlement gate lock bug
  - At midnight ET, `_reset_for_new_day()` + empty-registry settlement locked `daily_summary_sent_date` for the entire day
  - Post-market settlement at 4 PM was skipped, stale registry caused HALT loop next midnight
  - Fix: Don't lock gate pre-market with no activity; verify stale registry against Saxo before halting
- **1.3.3** (2026-02-23): Remove MKT-016/017/base loss limit — bot always places all 5 entries
  - Removed MKT-016 (stop cascade breaker): was pausing entries after 3 stops
  - Removed MKT-017 (daily loss limit): was pausing entries after -$500 realized P&L
  - Override base MEIC `_is_daily_loss_limit_reached()` to return False
- **1.3.2** (2026-02-20): MKT-021 pre-entry ROC gate + Fix #81
  - Before placing entry #6+ (min 5 entries), checks if ROC on existing entries already exceeds early close threshold (2%)
  - If so, skips remaining entries and MKT-018 early close fires immediately at undiluted ROC
  - Prevents wasteful entries that dilute ROC with capital + close costs but ~$0 P&L
  - Only active when MKT-018 early close is enabled; minimum 5 entries before gate activates
  - Flag + skip remaining + persist state across restart
  - Fix #81: Skip closing long legs with $0 bid during early close (worthless, expire naturally at 4 PM)
- **1.3.1** (2026-02-20): MKT-020 progressive call OTM tightening + raise min credit to $1.00/side
  - Progressively moves short call closer to ATM in 5pt steps until credit >= $1.00 or 25pt OTM floor
  - Batch API: 1 option chain + 1 batch quote = 2 API calls regardless of candidate count
  - Min credit raised from $0.50 to $1.00 per side (ensures meaningful call contribution to total credit)
  - Only runs for NEUTRAL trend (full IC candidates); one-sided entries unaffected
- **1.3.0** (2026-02-19): MKT-019 virtual equal credit stop + MKT-018 early close based on Return on Capital (ROC) + batch quote API
  - Closes ALL positions when ROC >= 2.0% after all entries are placed
  - ROC = (net_pnl - close_cost) / capital_deployed, checked every heartbeat
  - Close cost: active_legs × $5.00 ($2.50 commission + $2.50 slippage)
  - Immediate daily summary, account summary, performance metrics logging after early close
  - Deferred fill lookup for accurate close P&L (non-blocking background thread)
  - Heartbeat displays live ROC vs threshold after all entries placed
  - Google Sheets: new early_close and notes columns in Daily Summary tab
  - State persistence: early_close_triggered saved/restored across restarts
  - Skip ROC check in last 15 minutes before close (positions expire naturally)
  - Based on 6-day backtest: -$8 total cost, captures high-profit days before late-day reversals
  - Batch quote API: `get_quotes_batch()` fetches all option prices in single API call (7x rate limit reduction)
  - Stop loss monitoring now uses `_batch_update_entry_prices()` instead of per-entry individual calls
  - Fix #80: Google Sheets Positions snapshot uses unconditional resize (prevents stale row_count after timeout)

- **1.2.9** (2026-02-18): ~~Daily loss limit~~ + daily summary accuracy
  - ~~MKT-017: Daily loss limit~~ *(removed in v1.3.3)*
  - Fix #77: Post-restart settlement processes expired credits even when registry is empty
  - Fix #78: Stop Loss Debits derived from P&L identity (not theoretical stop levels with slippage error)
  - Fix #79: MKT-011 "both non-viable" skip path now increments entries_skipped counter

- **1.2.8** (2026-02-17): EMA threshold widening + ~~stop cascade breaker~~
  - EMA neutral threshold widened from 0.1% to 0.2% (fewer false trend signals on low-conviction moves)
  - ~~MKT-016: Stop cascade breaker~~ *(removed in v1.3.3)*
  - Based on Feb 10-17 performance analysis: 4 winning days then $740 loss on V-shaped reversal
  - EMA threshold change would have saved ~$330 on Feb 17 (fewer one-sided entries on weak trends)
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
  - In trending markets: convert only if trend matches viable side, skip otherwise
  - In NEUTRAL markets: skip entry if either side non-viable (one-sided only for clear trends)
  - Safety events: MKT-011_SKIP / MKT-010_SKIP logged when skipping

- **1.1.0** (2026-02-08): Credit gate and illiquidity handling
  - MKT-011: Pre-entry credit estimation - skips/converts non-viable entries
  - MKT-010: Illiquidity override - fallback when quotes unavailable
  - Fixed: Illiquidity logic now trades the VIABLE side (not the illiquid side)
  - Simplified: MKT-010 is now fallback-only (MKT-011 is primary check)

- **1.0.0** (2026-02-04): Initial implementation
  - EMA 20/40 trend detection
  - One-sided entries for trending markets
  - Full IC for neutral markets
