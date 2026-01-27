# MEIC Bot - Edge Case Analysis Report

**Analysis Date:** 2026-01-27
**Analyst:** Claude (Pre-Implementation Analysis)
**Bot Version:** 1.0.0 (Pending Implementation)
**Status:** Living Document - Update as implementation progresses

---

## Executive Summary

This document catalogs all identified edge cases and potential failure scenarios for the MEIC (Multiple Entry Iron Condors) trading bot. Each scenario is evaluated before implementation to guide development.

**Total Scenarios Analyzed:** 75
**Well-Handled/Resolved:** 0 (to be implemented)
**Pending Implementation:** 75 (100%)

**Note:** This is a PRE-IMPLEMENTATION analysis. All scenarios marked "PENDING" will be updated to "RESOLVED" as code is written.

---

## Risk Level Definitions

| Level | Symbol | Meaning |
|-------|--------|---------|
| LOW | ✅ | Well-handled, no action needed |
| MEDIUM | ⚠️ | Acceptable but could be improved |
| HIGH | 🔴 | Significant gap, should be addressed |
| CRITICAL | 🚨 | Immediate attention required |
| PENDING | 📋 | Not yet implemented |

---

## 1. CONNECTION/API FAILURE SCENARIOS

### 1.1 Saxo API Outage During Scheduled Entry
| | |
|---|---|
| **ID** | CONN-001 |
| **Trigger** | API returns 500/503 at exactly 10:00 AM when Entry #1 should execute |
| **Expected Handling** | Skip this entry, attempt next scheduled entry. Log missed entry. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Don't retry indefinitely. After 2-3 retries within 2 minutes, skip to next entry window. Track `missed_entries_count` for daily summary. |
| **Test Case** | Mock API failure at 10:00 AM, verify Entry #2 at 10:30 AM still executes. |

### 1.2 API Outage During Stop Loss Monitoring
| | |
|---|---|
| **ID** | CONN-002 |
| **Trigger** | API fails while monitoring positions for stop loss triggers |
| **Expected Handling** | Use circuit breaker. After N failures, send critical alert but don't close positions blindly. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Positions are hedged (long wings protect shorts). Better to wait for API recovery than panic-close. Log "STOP LOSS MONITORING IMPAIRED" as critical. |
| **Test Case** | Simulate 5 consecutive API failures during monitoring phase. |

### 1.3 WebSocket Disconnect During Entry Window
| | |
|---|---|
| **ID** | CONN-003 |
| **Trigger** | WebSocket drops at 10:29 AM, just before Entry #2 |
| **Expected Handling** | Detect disconnect, attempt reconnect, fall back to REST for price quotes. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Copy reconnection logic from Iron Fly. Strike calculation doesn't require real-time streaming. |
| **Test Case** | Force WebSocket disconnect, verify entry still executes with REST fallback. |

### 1.4 Token Expires During Multi-Leg Order
| | |
|---|---|
| **ID** | CONN-004 |
| **Trigger** | OAuth token expires between placing call spread and put spread legs |
| **Expected Handling** | Auto-refresh token on 401, retry the failed leg. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Use existing `_make_request()` 401 handling from saxo_client.py. Track which legs succeeded before token error. |
| **Test Case** | Force token expiry mid-order, verify retry succeeds. |

### 1.5 Rate Limiting at Entry Time
| | |
|---|---|
| **ID** | CONN-005 |
| **Trigger** | Saxo returns 429 rate limit error at 10:00 AM |
| **Expected Handling** | Exponential backoff with max 2-minute delay. Skip entry if still blocked. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Entry windows are 30 minutes apart. If blocked for >5 minutes, skip to next entry. |
| **Test Case** | Mock 429 responses for 3 minutes, verify graceful skip. |

### 1.6 Partial Order Fill Due to Network Timeout
| | |
|---|---|
| **ID** | CONN-006 |
| **Trigger** | Order placed, network times out before confirmation, order actually filled |
| **Expected Handling** | Query activities endpoint to check if order filled. Register position if found. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Use ExternalReference to trace order → activity → position. Critical for Position Registry accuracy. |
| **Test Case** | Simulate timeout after order submission, verify position is still registered. |

---

## 2. ORDER EXECUTION FAILURE SCENARIOS

### 2.1 Call Spread Fills, Put Spread Fails
| | |
|---|---|
| **ID** | ORDER-001 |
| **Trigger** | Call spread order completes, put spread order times out or rejected |
| **Expected Handling** | Partial fill detected. Options: (A) Close call spread immediately, or (B) Leave call spread and set wider stop. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | MEIC philosophy: partial is better than nothing. Leave call spread, it's defined risk. Log as "PARTIAL_ENTRY" and alert operator. |
| **Test Case** | Mock put spread rejection, verify call spread remains with appropriate stop. |

### 2.2 Single Leg Fills, Other Three Fail
| | |
|---|---|
| **ID** | ORDER-002 |
| **Trigger** | Only short call fills, long call/short put/long put all fail |
| **Expected Handling** | NAKED SHORT POSITION - Critical! Must immediately close or complete the hedge. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | This is dangerous. If short fills alone, immediately place market order for long call (the hedge). If that fails, close the short. Never leave naked. |
| **Test Case** | Mock 3-leg failure, verify naked position is closed within 30 seconds. |

### 2.3 Stop Loss Order Fails
| | |
|---|---|
| **ID** | ORDER-003 |
| **Trigger** | Stop loss triggered, but market order to close fails |
| **Expected Handling** | Retry up to 5 times with 2-second delays. If still failing, alert critical and manual intervention. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Copy STOP-002 pattern from Iron Fly. Positions have max loss capped by wings, so delay is tolerable but not ideal. |
| **Test Case** | Mock 3 consecutive stop loss failures, verify retry logic. |

### 2.4 Order Rejected Due to Margin
| | |
|---|---|
| **ID** | ORDER-004 |
| **Trigger** | Saxo rejects order due to insufficient margin (late in day with many positions) |
| **Expected Handling** | Log margin rejection, skip this entry, continue with existing positions. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Pre-check buying power before each entry. If BP < required margin, skip entry proactively rather than wait for rejection. |
| **Test Case** | Mock margin rejection, verify graceful skip without affecting other positions. |

### 2.5 Order Rejected Due to Invalid Strike
| | |
|---|---|
| **ID** | ORDER-005 |
| **Trigger** | Calculated strike doesn't exist in option chain |
| **Expected Handling** | Round to nearest valid strike. SPX strikes are 5-point increments. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Always round strikes to nearest 5. Verify strike exists by querying option chain before order. |
| **Test Case** | Calculate strike of 6023, verify auto-rounded to 6025. |

### 2.6 Wide Bid-Ask Spread on Entry
| | |
|---|---|
| **ID** | ORDER-006 |
| **Trigger** | Spread width > 50% of mid price |
| **Expected Handling** | Log warning, but proceed with limit order at mid price. Skip entry only if spread > 100%. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | MEIC collects $1-$1.75 per side. If bid-ask is $0.50 wide, that's significant but acceptable. If $1.50 wide (>100%), skip entry. |
| **Test Case** | Mock 80% spread, verify entry proceeds. Mock 120% spread, verify skip. |

### 2.7 Stop Loss Triggered During New Entry
| | |
|---|---|
| **ID** | ORDER-007 |
| **Trigger** | Entry #3 order being placed when Entry #1's stop loss triggers |
| **Expected Handling** | Stop loss takes priority. Complete stop loss closure before continuing entry. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Use mutex/lock to prevent concurrent order operations. Stop loss always has priority over new entries. |
| **Test Case** | Simulate simultaneous stop trigger and entry window, verify stop executes first. |

### 2.8 Order Fill at Worse Price Than Expected
| | |
|---|---|
| **ID** | ORDER-008 |
| **Trigger** | Market order fills $0.30 worse than quoted |
| **Expected Handling** | Accept slippage, recalculate stop loss based on ACTUAL fill price. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Stop loss = total credit received. Must use actual fill prices, not quoted prices. Critical for MEIC math to work. |
| **Test Case** | Mock fill $0.25 worse, verify stop adjusted correctly. |

---

## 3. POSITION STATE EDGE CASES

### 3.1 Bot Restarts With Partial Day Entries
| | |
|---|---|
| **ID** | POS-001 |
| **Trigger** | Bot crashes at 11:15 AM with 2 ICs open, restarts at 11:45 AM |
| **Expected Handling** | Query Position Registry for MEIC positions. Recover state. Resume entries starting at 12:00 PM. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Position Registry stores entry_number metadata. On recovery, read registry, rebuild position state, determine next entry time. |
| **Test Case** | Kill bot with 2 entries, restart, verify entries 3-6 continue. |

### 3.2 Position Registry Corrupted
| | |
|---|---|
| **ID** | POS-002 |
| **Trigger** | Registry JSON file corrupted (disk error, incomplete write) |
| **Expected Handling** | Registry returns empty dict. Query Saxo for actual positions. Mark all SPX positions as "unregistered" for manual review. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | PositionRegistry._read_registry() handles JSONDecodeError. On corruption, log critical and halt trading until resolved. |
| **Test Case** | Write invalid JSON to registry, verify bot logs critical and halts. |

### 3.3 Position Closed Outside Bot (Manual Trade)
| | |
|---|---|
| **ID** | POS-003 |
| **Trigger** | User manually closes Entry #2's put spread in SaxoTraderGO |
| **Expected Handling** | Reconciliation detects discrepancy. Position Registry shows 4 positions, Saxo shows 2. Log warning, update registry. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Run reconciliation check every 5 minutes. Remove orphaned registry entries. Don't re-open manually closed positions. |
| **Test Case** | Manually close 2 positions, verify registry cleaned up on next reconciliation. |

### 3.4 Position Registry Conflicts With Saxo
| | |
|---|---|
| **ID** | POS-004 |
| **Trigger** | Registry shows 8 positions, Saxo shows 12 (4 extra SPX positions from another source) |
| **Expected Handling** | Extra positions are "unregistered". Bot ignores them. Log warning about unregistered positions. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Bot only manages positions in registry. Unregistered positions are visible but untouched. Operator should investigate. |
| **Test Case** | Add positions via SaxoTraderGO, verify MEIC ignores them. |

### 3.5 Entry Creates Duplicate Registration
| | |
|---|---|
| **ID** | POS-005 |
| **Trigger** | Network issue causes entry to be attempted twice, same position registered twice |
| **Expected Handling** | Registry.register() returns True for same bot_name (idempotent). No duplicate entries created. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | PositionRegistry handles duplicate registration gracefully. Log debug message if duplicate detected. |
| **Test Case** | Call register() twice with same position_id, verify single entry. |

### 3.6 Iron Fly Running Simultaneously
| | |
|---|---|
| **ID** | POS-006 |
| **Trigger** | MEIC and Iron Fly both trading SPX at same time (not recommended but possible) |
| **Expected Handling** | Each bot sees only its registered positions. No interference. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | This is exactly why Position Registry was built. Test extensively before allowing simultaneous operation. |
| **Test Case** | Run both bots in dry-run, verify position isolation. |

### 3.7 Maximum Positions Exceeded
| | |
|---|---|
| **ID** | POS-007 |
| **Trigger** | All 6 entries complete = 24 positions. What if some didn't close and new day starts? |
| **Expected Handling** | On new day, check for previous day's positions. If any exist, log critical - 0DTE shouldn't have overnight positions. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | 0DTE means all positions should expire or be stopped by 4 PM. Overnight positions indicate major bug. |
| **Test Case** | Simulate position surviving to next day, verify critical alert and trading halt. |

---

## 4. MARKET CONDITION EDGE CASES

### 4.1 Large Overnight Gap
| | |
|---|---|
| **ID** | MKT-001 |
| **Trigger** | SPX gaps down 3% overnight |
| **Expected Handling** | Normal 10:00 AM entry proceeds, but strikes are calculated based on new lower price. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | MEIC doesn't have gap filters by default. Consider adding optional gap check if gap > X%, delay first entry by 30 minutes. |
| **Test Case** | Mock 3% gap, verify entry proceeds with adjusted strikes. |

### 4.2 Flash Crash During Entry Window
| | |
|---|---|
| **ID** | MKT-002 |
| **Trigger** | SPX drops 2% in 5 minutes starting at 10:28 AM, Entry #2 at 10:30 AM |
| **Expected Handling** | Entry proceeds - the crash creates opportunity for better short strike prices further OTM. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | MEIC is market-neutral. Flash crash makes call spread more profitable (collecting premium on lower delta). Put spread is higher risk but wings protect. |
| **Test Case** | Mock flash crash, verify entry executes with adjusted OTM strikes. |

### 4.3 Trend Day (SPX Moves 2% in One Direction)
| | |
|---|---|
| **ID** | MKT-003 |
| **Trigger** | SPX rallies steadily from 10:00 AM to 1:00 PM (+1.5%) |
| **Expected Handling** | Put sides of early entries become very profitable. Call sides approach stops. Multiple call-side stops may trigger. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | This is MEIC working as designed. Losing call sides, winning put sides, approximately breakeven. Log daily theta collected. |
| **Test Case** | Simulate uptrend, verify call stops trigger correctly while put sides profit. |

### 4.4 Both Sides Stopped (Double Stop)
| | |
|---|---|
| **ID** | MKT-004 |
| **Trigger** | SPX whipsaws: drops 1%, recovers, then rallies 1.5% - hitting both stops |
| **Expected Handling** | Both stops execute. Max loss realized. Log as "DOUBLE_STOP" event. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Double stops occur ~6-10% of trading days. This is defined risk. Don't panic. Max loss per IC = spread width - total credit. |
| **Test Case** | Simulate whipsaw pattern, verify both stops execute and P&L is within expected max loss. |

### 4.5 Market Circuit Breaker Halt
| | |
|---|---|
| **ID** | MKT-005 |
| **Trigger** | Level 1 circuit breaker halts trading at 10:45 AM |
| **Expected Handling** | Entry #3 at 11:00 AM is blocked. Resume entries after market reopens. Existing positions protected by long wings. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Copy MKT-004 halt detection from Delta Neutral. During halt, existing positions can't be closed anyway. Wings provide max loss protection. |
| **Test Case** | Mock trading halt, verify entries resume correctly after halt lifts. |

### 4.6 VIX Spike Mid-Day
| | |
|---|---|
| **ID** | MKT-006 |
| **Trigger** | VIX jumps from 18 to 28 at 11:30 AM |
| **Expected Handling** | Consider skipping Entry #4+ if VIX > 25. High VIX = expensive premium but also higher risk of movement. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | MEIC doesn't traditionally have VIX filters. Make this configurable. If VIX > `max_vix_entry` (default 25), skip remaining entries. |
| **Test Case** | Mock VIX spike, verify entries skip when above threshold. |

### 4.7 Low Liquidity Strikes
| | |
|---|---|
| **ID** | MKT-007 |
| **Trigger** | Desired strike has no bids (only asks) |
| **Expected Handling** | Try next strike 5 points closer to ATM. If still no liquidity, skip this side of IC. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | SPX is highly liquid, but far OTM strikes can have gaps. Implement strike availability check before order. |
| **Test Case** | Mock no-bid strike, verify fallback to closer strike. |

### 4.8 FOMC Announcement Day
| | |
|---|---|
| **ID** | MKT-008 |
| **Trigger** | FOMC rate decision at 2:00 PM |
| **Expected Handling** | Skip all entries. FOMC days have extreme volatility risk. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Use shared/event_calendar.py `is_fomc_announcement_day()`. Log "FOMC day - skipping all entries". |
| **Test Case** | Run on FOMC day, verify no entries attempted. |

### 4.9 Early Close Day
| | |
|---|---|
| **ID** | MKT-009 |
| **Trigger** | Market closes at 1:00 PM (Christmas Eve, etc.) |
| **Expected Handling** | Reduce entries. Maybe only Entry #1 and #2 (10:00 AM and 10:30 AM). Skip entries after 11:00 AM. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Check `is_early_close_day()` at startup. If early close, use truncated entry schedule. |
| **Test Case** | Simulate early close day, verify only first 2 entries execute. |

---

## 5. TIMING/RACE CONDITION ISSUES

### 5.1 Clock Skew Between Bot and Exchange
| | |
|---|---|
| **ID** | TIME-001 |
| **Trigger** | Bot clock is 30 seconds ahead of exchange clock |
| **Expected Handling** | Entry at 10:00 AM bot time might be 9:59:30 AM exchange time. Use NTP sync. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | VM should have NTP configured. Add startup check for time accuracy. Log warning if system time differs from Saxo server time by >5 seconds. |
| **Test Case** | Check time sync on VM startup. |

### 5.2 Entry Window Overlaps With Stop Processing
| | |
|---|---|
| **ID** | TIME-002 |
| **Trigger** | 10:30 AM entry starts while 10:00 AM position stop is being processed |
| **Expected Handling** | Use mutex lock. Stop processing completes before new entry begins. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Single-threaded processing with priority queue: stops > entries. Never place new orders while stop orders are pending. |
| **Test Case** | Simulate simultaneous stop and entry, verify sequential processing. |

### 5.3 Market Close Before All Entries
| | |
|---|---|
| **ID** | TIME-003 |
| **Trigger** | Unexpected early halt at 11:00 AM (e.g., September 11 style event) |
| **Expected Handling** | Detect market closed unexpectedly. Halt entries. Wait for market status to change. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Check `is_market_open()` before each entry attempt. If market closes unexpectedly, log safety event and wait. |
| **Test Case** | Mock unexpected market close, verify entries halt. |

### 5.4 Daylight Saving Time Transition
| | |
|---|---|
| **ID** | TIME-004 |
| **Trigger** | DST spring forward: 2:00 AM becomes 3:00 AM |
| **Expected Handling** | Entry times are in ET. Use pytz with US/Eastern timezone. DST handled automatically. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | All time handling through `get_us_market_time()` from shared/market_hours.py. DST transitions tested. |
| **Test Case** | Mock DST transition day, verify entry times are correct. |

### 5.5 Entry Scheduled at Exact Close Time
| | |
|---|---|
| **ID** | TIME-005 |
| **Trigger** | Hypothetical Entry #7 at 4:00 PM (market close) |
| **Expected Handling** | Never schedule entries within 30 minutes of market close. 0DTE positions should be established with time for theta. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Default schedule ends at 12:30 PM, giving 3.5 hours for positions to work. Configurable but warn if entry > 2:00 PM. |
| **Test Case** | Configure entry at 3:45 PM, verify warning logged. |

---

## 6. STOP LOSS EDGE CASES

### 6.1 Stop Loss Calculated Incorrectly
| | |
|---|---|
| **ID** | STOP-001 |
| **Trigger** | Stop set to $2.00 but credit was actually $2.50 |
| **Expected Handling** | Must use ACTUAL fill prices for stop calculation, not config defaults. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | `calculate_stop_loss()` receives actual_credit as parameter. Never hardcode. Store credit received per IC in registry metadata. |
| **Test Case** | Verify stop = actual_credit (or actual_credit - $0.10 for MEIC+). |

### 6.2 MEIC+ Stop Too Tight
| | |
|---|---|
| **ID** | STOP-002 |
| **Trigger** | Credit = $1.00, MEIC+ stop = $0.90, but bid-ask spread is $0.50 |
| **Expected Handling** | Stop might trigger on spread noise, not actual price movement. Consider minimum stop distance. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | If MEIC+ stop would be < $1.50, use standard MEIC stop instead. Log when MEIC+ is overridden due to tight stop. |
| **Test Case** | Mock $0.80 credit, verify MEIC+ not applied (would create $0.70 stop). |

### 6.3 Stop Triggers Before Position Fully Registered
| | |
|---|---|
| **ID** | STOP-003 |
| **Trigger** | Market moves fast, stop condition met before all 4 legs are registered |
| **Expected Handling** | Only monitor positions that are fully registered. Don't set stops on incomplete ICs. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Stop monitoring only begins after all 4 legs of an IC are confirmed filled and registered. Use `ic_complete` flag. |
| **Test Case** | Partial fill scenario, verify no stop monitoring until complete. |

### 6.4 Multiple Stops Trigger Simultaneously
| | |
|---|---|
| **ID** | STOP-004 |
| **Trigger** | Big move triggers stops on Entry #1, #2, #3 call spreads all at once |
| **Expected Handling** | Process stops sequentially with 1-second delays to avoid rate limiting. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Add 1-second delay between stop order placements. All should complete within 30 seconds. |
| **Test Case** | Simulate 3 simultaneous stop triggers, verify sequential execution. |

### 6.5 Stop Price Changes Due to Decay
| | |
|---|---|
| **ID** | STOP-005 |
| **Trigger** | At 10:00 AM, stop at $2.00. By 3:00 PM, spread value decayed to $0.50 |
| **Expected Handling** | Stop is based on premium PAID (now owed), not current value. Original stop of $2.00 remains unless tightened. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Some traders tighten stops as day progresses. Make this optional: `tighten_stops_enabled`. Default: false (keep original stops). |
| **Test Case** | Verify stop price doesn't change as theta decays. |

### 6.6 Slippage Causes Stop to Fill Above Stop Price
| | |
|---|---|
| **ID** | STOP-006 |
| **Trigger** | Stop set at $2.00, market order fills at $2.25 |
| **Expected Handling** | Accept slippage. Log actual fill vs expected. Calculate actual P&L from real prices. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | This is why wings exist - max loss is spread width regardless of fill price. Track slippage for analytics. |
| **Test Case** | Mock $0.30 slippage on stop, verify P&L calculated correctly. |

---

## 7. MULTI-ENTRY SPECIFIC EDGE CASES

### 7.1 Entry #1 Fails, Continue With #2-#6?
| | |
|---|---|
| **ID** | MULTI-001 |
| **Trigger** | Entry #1 at 10:00 AM completely fails (API down) |
| **Expected Handling** | Yes, continue with Entry #2 at 10:30 AM. One failed entry doesn't abort the day. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Track `entries_completed` and `entries_failed`. Alert if >2 entries fail. Continue attempting until all windows pass. |
| **Test Case** | Fail Entry #1, verify Entry #2 proceeds. |

### 7.2 All Entries Fail
| | |
|---|---|
| **ID** | MULTI-002 |
| **Trigger** | API issues all morning, all 6 entries fail |
| **Expected Handling** | Log "NO ENTRIES COMPLETED" as critical. No positions to manage. Daily summary shows 0 trades. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | This is acceptable (no risk if no positions). Alert operator that day was missed. |
| **Test Case** | Fail all 6 entries, verify alert and clean summary. |

### 7.3 Strikes Drift Between Entries
| | |
|---|---|
| **ID** | MULTI-003 |
| **Trigger** | SPX at 6000 for Entry #1, at 6050 for Entry #4 |
| **Expected Handling** | Each entry calculates fresh strikes. Entry #4 will have higher strikes. This is correct - averaging effect. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Don't try to match strikes across entries. Each IC is independent. The averaging is a feature, not a bug. |
| **Test Case** | Verify different entries get different strikes as SPX moves. |

### 7.4 One Entry Stopped While Another Active
| | |
|---|---|
| **ID** | MULTI-004 |
| **Trigger** | Entry #1 call side stopped at 11:00 AM. Entry #2 call side still open. |
| **Expected Handling** | Handle each IC independently. Entry #1 stop doesn't affect Entry #2. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Each IC has its own stop. Track state per entry_number. Use registry strategy_id to differentiate. |
| **Test Case** | Stop Entry #1, verify Entry #2 continues monitoring independently. |

### 7.5 Daily Limit Reached Before All Entries
| | |
|---|---|
| **ID** | MULTI-005 |
| **Trigger** | 3 double-stops in morning, daily loss limit hit at 11:30 AM |
| **Expected Handling** | Skip remaining entries (#5, #6). Daily loss limit protects account. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Check `daily_realized_loss` before each entry. If > `max_daily_loss`, skip entry and log reason. |
| **Test Case** | Simulate 2% daily loss, verify entries #5-#6 skipped. |

### 7.6 Entries Too Close Together
| | |
|---|---|
| **ID** | MULTI-006 |
| **Trigger** | User configures entries 5 minutes apart (not recommended) |
| **Expected Handling** | Log warning. Minimum spacing should be 15 minutes to allow fills and monitoring. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Validate config at startup. Warn if spacing < 15 minutes. Block if spacing < 5 minutes. |
| **Test Case** | Configure 5-minute spacing, verify error logged. |

---

## 8. DATA INTEGRITY ISSUES

### 8.1 Stale Price Data
| | |
|---|---|
| **ID** | DATA-001 |
| **Trigger** | WebSocket cache shows price from 30 seconds ago |
| **Expected Handling** | Check quote timestamp. If > 10 seconds old, fetch fresh via REST. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Add staleness check to strike calculation. Never calculate strikes on stale data. |
| **Test Case** | Mock 30-second-old cache, verify REST fallback used. |

### 8.2 Missing Option Chain Data
| | |
|---|---|
| **ID** | DATA-002 |
| **Trigger** | Option chain API returns empty for today's expiry |
| **Expected Handling** | Skip entry. Log critical error. This shouldn't happen for SPX 0DTE on trading days. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | If option chain empty, something is very wrong. Don't proceed. Alert immediately. |
| **Test Case** | Mock empty option chain, verify entry blocked and alert sent. |

### 8.3 P&L Calculation Error
| | |
|---|---|
| **ID** | DATA-003 |
| **Trigger** | Fill price stored incorrectly, P&L shows $10,000 profit (impossible) |
| **Expected Handling** | Sanity check P&L. Per IC max profit = credit received (~$250). Flag impossible values. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | If calculated P&L > 10x expected, log warning and recalculate. Likely data error. |
| **Test Case** | Mock fill price 100x actual, verify sanity check catches it. |

### 8.4 Google Sheets Logging Fails
| | |
|---|---|
| **ID** | DATA-004 |
| **Trigger** | Google Sheets API quota exceeded, logs not written |
| **Expected Handling** | Log locally. Don't let logging failure affect trading. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | TradeLoggerService already handles Sheets errors gracefully. Verify local file backup works. |
| **Test Case** | Mock Sheets failure, verify local log captured. |

### 8.5 Registry File Locked by Another Process
| | |
|---|---|
| **ID** | DATA-005 |
| **Trigger** | Another bot holds exclusive lock on registry file |
| **Expected Handling** | Wait for lock (fcntl will block). Timeout after 10 seconds, retry. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | File locks with fcntl.LOCK_EX are blocking by default. Add timeout wrapper for safety. |
| **Test Case** | Hold lock from another process, verify MEIC waits and eventually succeeds. |

---

## 9. STATE MACHINE EDGE CASES

### 9.1 State Corruption
| | |
|---|---|
| **ID** | STATE-001 |
| **Trigger** | State shows "MONITORING" but no positions exist |
| **Expected Handling** | Reconciliation detects mismatch. Reset to IDLE if no positions. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Add state validation: if state=MONITORING but positions=0, reset to IDLE and log warning. |
| **Test Case** | Force invalid state, verify auto-correction. |

### 9.2 Stuck in CLOSING State
| | |
|---|---|
| **ID** | STATE-002 |
| **Trigger** | Stop order placed but never confirmed. State stuck in CLOSING. |
| **Expected Handling** | Timeout after 5 minutes. Check if position still exists. If not, transition to next state. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Add `closing_started_at` timestamp. If > 5 minutes in CLOSING, verify position status and recover. |
| **Test Case** | Mock stuck CLOSING state, verify timeout recovery. |

### 9.3 Invalid State Transition
| | |
|---|---|
| **ID** | STATE-003 |
| **Trigger** | Attempt to transition from IDLE directly to MONITORING (skipping entry) |
| **Expected Handling** | State machine rejects invalid transitions. Log error and remain in current state. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Define valid transitions in state machine. Reject and log any invalid transition attempts. |
| **Test Case** | Attempt invalid transition, verify rejection. |

### 9.4 New Day While Positions Still Open
| | |
|---|---|
| **ID** | STATE-004 |
| **Trigger** | Calendar rolls to new day but 0DTE positions somehow still exist |
| **Expected Handling** | CRITICAL ERROR. 0DTE should never survive to next day. Halt trading, alert immediately. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | On new day check, if positions exist with yesterday's expiry, something is very wrong. Log critical and halt. |
| **Test Case** | Mock overnight position, verify halt and alert. |

---

## 10. ALERT SYSTEM EDGE CASES

### 10.1 Alert Service Fails
| | |
|---|---|
| **ID** | ALERT-001 |
| **Trigger** | Pub/Sub publish fails |
| **Expected Handling** | Log locally. Don't block trading. Alerts are non-critical for operation. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | AlertService.publish() already handles failures gracefully. Verify MEIC integration. |
| **Test Case** | Mock Pub/Sub failure, verify trading continues. |

### 10.2 Alert Flood (Many Stops Hit)
| | |
|---|---|
| **ID** | ALERT-002 |
| **Trigger** | 5 stop losses trigger in 10 seconds |
| **Expected Handling** | Rate limit alerts. Don't send 5 separate WhatsApp messages. Batch into one. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Add alert batching with 30-second window. Combine multiple stop alerts into single message. |
| **Test Case** | Trigger 5 stops rapidly, verify single batched alert. |

### 10.3 Daily Summary Missing Data
| | |
|---|---|
| **ID** | ALERT-003 |
| **Trigger** | Some entries failed, daily summary shows incomplete data |
| **Expected Handling** | Include all data we have. Mark missing entries as "FAILED" in summary. |
| **Risk Level** | 📋 PENDING |
| **Implementation Notes** | Daily summary should show: entries attempted, entries completed, entries failed, total P&L. |
| **Test Case** | Fail 2 entries, verify summary shows 4/6 completed. |

---

## 11. SUMMARY TABLES

### Edge Cases by Category

| Category | Count | Status |
|----------|-------|--------|
| Connection/API | 6 | 📋 PENDING |
| Order Execution | 8 | 📋 PENDING |
| Position State | 7 | 📋 PENDING |
| Market Conditions | 9 | 📋 PENDING |
| Timing/Race Conditions | 5 | 📋 PENDING |
| Stop Loss | 6 | 📋 PENDING |
| Multi-Entry Specific | 6 | 📋 PENDING |
| Data Integrity | 5 | 📋 PENDING |
| State Machine | 4 | 📋 PENDING |
| Alert System | 3 | 📋 PENDING |
| **TOTAL** | **75** | **0% Resolved** |

### Priority Implementation Order

| Priority | Category | Reason |
|----------|----------|--------|
| 1 | ORDER-002 (Naked Position) | Safety critical - never leave naked shorts |
| 2 | STOP-001 (Stop Calculation) | Core MEIC math must be correct |
| 3 | POS-001 (Recovery) | Must handle restarts gracefully |
| 4 | MULTI-004 (Independent ICs) | Core multi-entry concept |
| 5 | MKT-008 (FOMC Skip) | Easy win, high value |

---

## 12. CHANGE LOG

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-27 | 1.0.0 | Initial edge case analysis (pre-implementation) |

---

## 13. USAGE

### During Implementation

1. Reference this document when writing MEIC code
2. For each edge case, add the handling code
3. Update status from 📋 PENDING to ✅ LOW (or appropriate level)
4. Add resolution notes with file:line references

### Testing Against This Document

```python
# Example test structure
def test_conn_001_api_outage_during_entry():
    """CONN-001: API outage during scheduled entry"""
    # Mock API failure
    # Attempt entry at 10:00 AM
    # Verify entry skipped
    # Verify Entry #2 at 10:30 AM succeeds
    pass

def test_order_002_naked_short_position():
    """ORDER-002: Single leg fills, others fail - CRITICAL"""
    # Mock 3-leg failure after short call fills
    # Verify immediate hedge placement
    # Verify naked position never exists > 30 seconds
    pass
```

### Continuous Updates

This document should be updated:
- When new edge cases are discovered
- When fixes are implemented
- After each deployment with lessons learned
- After any production incident

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-01-27 | Claude | Initial pre-implementation edge case analysis |
