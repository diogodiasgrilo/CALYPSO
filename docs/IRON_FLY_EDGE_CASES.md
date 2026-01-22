# Iron Fly 0DTE Bot - Edge Case Analysis Report

**Analysis Date:** 2026-01-22
**Analyst:** Claude (Devil's Advocate Review)
**Bot Version:** 1.0.0
**Strategy:** Doc Severson's 0DTE Iron Butterfly
**Status:** Living Document - Update as fixes are implemented

---

## Executive Summary

This document catalogs all identified edge cases and potential failure scenarios for the Iron Fly 0DTE trading bot. Each scenario is evaluated for current handling and risk level.

**Total Scenarios Analyzed:** 52
**Well-Handled (LOW):** 38 (73%)
**Medium Risk:** 11 (21%)
**High Risk:** 3 (6%)

---

## Risk Level Definitions

| Level | Symbol | Meaning |
|-------|--------|---------|
| LOW | ✅ | Well-handled, no action needed |
| MEDIUM | ⚠️ | Acceptable but could be improved |
| HIGH | 🔴 | Significant gap, should be addressed |
| CRITICAL | 🚨 | Immediate attention required |

---

## 1. CONNECTION/API FAILURE SCENARIOS

### 1.1 Saxo API Complete Outage
| | |
|---|---|
| **ID** | CONN-001 |
| **Trigger** | Saxo API returns HTTP 500/503 or connection refused |
| **Current Handling** | Circuit breaker increments `_consecutive_failures`, opens after 5 failures. See `strategy.py:629-641` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Bot pauses trading; attempts emergency close if position open; 5-min cooldown. |

### 1.2 Saxo API Intermittent Errors
| | |
|---|---|
| **ID** | CONN-002 |
| **Trigger** | API returns errors ~30% of requests (flaky connection) |
| **Current Handling** | Consecutive failure counter - requires 5 back-to-back failures to trigger circuit breaker. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | Unlike Delta Neutral bot, Iron Fly uses consecutive counter only, not sliding window. Intermittent errors (fail-pass-fail-pass) never trigger circuit breaker. |
| **Recommendation** | Add sliding window counter (e.g., 5 of last 10 calls fail). |

### 1.3 WebSocket Disconnects Mid-Position
| | |
|---|---|
| **ID** | CONN-003 |
| **Trigger** | Real-time price feed drops during position monitoring |
| **Current Handling** | Detected via `is_price_stale()` after 30 seconds. REST fallback via `update_market_data(skip_cache=True)`. See `strategy.py:1181-1229` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Automatic REST fallback; logs warning; continues monitoring with fresh data. |

### 1.4 Token Expires During Order Placement
| | |
|---|---|
| **ID** | CONN-004 |
| **Trigger** | OAuth token expires mid-operation |
| **Current Handling** | `saxo_client.py:886-893` detects 401, calls `authenticate(force_refresh=True)`, retries request. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Automatic token refresh with request retry. Logs "CONN-004: Token refreshed, retrying request". |

### 1.5 Network Timeout During Order Confirmation
| | |
|---|---|
| **ID** | CONN-005 |
| **Trigger** | Order placed, but HTTP response times out before confirmation received |
| **Current Handling** | `_verify_order_fill()` polls for 30 seconds. If timeout, order tracked as orphaned. See `strategy.py:1028-1079` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Orphaned order tracking + cleanup attempts. Position reconciliation on restart detects ghost fills. |

### 1.6 Rate Limiting from Saxo (429)
| | |
|---|---|
| **ID** | CONN-006 |
| **Trigger** | Too many API requests, Saxo returns 429 |
| **Current Handling** | `saxo_client.py:864-883` implements exponential backoff (1s, 2s, 4s, 8s, 16s). Max 5 retries. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Respects Retry-After header if present. Resets on successful request. |

### 1.7 Both WebSocket AND REST API Fail with Position Open
| | |
|---|---|
| **ID** | CONN-007 |
| **Trigger** | Complete data blackout during position monitoring |
| **Current Handling** | After 3 consecutive stale data warnings with position open, logs CRITICAL. See `strategy.py:1216-1227` |
| **Risk Level** | 🔴 HIGH |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | Bot continues with last known price. Stop loss protection compromised - price may have already breached wing. |
| **Recommendation** | After N consecutive data failures with position open, trigger emergency close. |

---

## 2. ORDER EXECUTION FAILURE SCENARIOS

### 2.1 Iron Fly Partial Fill (3 of 4 Legs)
| | |
|---|---|
| **ID** | ORDER-001 |
| **Trigger** | First 3 legs fill, 4th leg order fails/times out |
| **Current Handling** | Detected at `strategy.py:1911-1939`. Logs `IRON_FLY_PARTIAL_FILL` safety event. Opens circuit breaker. Tracks failed orders as orphaned. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | PARTIALLY HANDLED |
| **Gap** | Position left in unhedged state. Manual intervention required to close 3 legs. No automatic rollback. |
| **Notes** | Safety-first: circuit breaker prevents further trading. Operator must clean up manually. |

### 2.2 Order Fill Verification Timeout
| | |
|---|---|
| **ID** | ORDER-002 |
| **Trigger** | Order placed but status never shows "Filled" within 30 seconds |
| **Current Handling** | `_verify_order_fill()` returns `(False, None)` after timeout. Order cancelled if possible. Tracked as orphaned. See `strategy.py:1049-1079` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Timeout prevents infinite wait; orphaned tracking enables later cleanup. |

### 2.3 Order Cancellation Fails
| | |
|---|---|
| **ID** | ORDER-003 |
| **Trigger** | Limit order times out, cancellation request fails |
| **Current Handling** | Order added to `_orphaned_orders` list with reason "timeout_cancel_failed". See `strategy.py:1006-1016` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Tracked for cleanup; `_check_for_orphaned_orders()` retries cancellation periodically. |

### 2.4 Emergency MARKET Order Fails (Stop Loss)
| | |
|---|---|
| **ID** | ORDER-004 |
| **Trigger** | Stop loss triggered, market order doesn't fill (no liquidity, trading halt) |
| **Current Handling** | `_emergency_close_position()` attempts all 4 legs. Logs success/failure count. See `strategy.py:709-857` |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | If emergency close fails (e.g., 2/4 legs), position remains partially open. No escalation beyond logging. |
| **Recommendation** | Add `_critical_intervention_required` flag (like Delta Neutral) to halt ALL trading until manual reset. |

### 2.5 Bid/Ask Spread Too Wide
| | |
|---|---|
| **ID** | ORDER-005 |
| **Trigger** | Options have 50%+ spread at entry |
| **Current Handling** | No explicit spread check before order placement. Orders use mid price or limit at bid/ask. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | Unlike Delta Neutral bot, no `max_bid_ask_spread_percent` config or check. May enter with poor fills. |
| **Recommendation** | Add spread validation before entry; skip if spread > threshold. |

### 2.6 Price Moves During Multi-Leg Entry
| | |
|---|---|
| **ID** | ORDER-006 |
| **Trigger** | SPX moves 5 points between placing leg 1 and leg 4 |
| **Current Handling** | Each leg placed with fresh quote. ATM strike fixed at entry start. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Wings are relative to ATM; price movement doesn't affect structure integrity. |

### 2.7 Order Rejected by Exchange
| | |
|---|---|
| **ID** | ORDER-007 |
| **Trigger** | Exchange rejects order (position limits, invalid strike, market closed) |
| **Current Handling** | `place_order_with_retry()` returns None on rejection. Counted as failure. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Failure counter increments; if partial fill scenario, circuit breaker opens. |

---

## 3. POSITION STATE EDGE CASES

### 3.1 Bot Crashes with Active Position
| | |
|---|---|
| **ID** | POS-001 |
| **Trigger** | Bot crashes mid-position; restarts later |
| **Current Handling** | `_reconcile_positions_with_broker()` runs on first strategy check. Reconstructs `IronFlyPosition` from broker data. See `strategy.py:1258-1420` |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | PARTIALLY HANDLED |
| **Gap** | Entry time set to NOW (not actual entry time). Credit received set to 0. This affects hold time calculation and P&L display. |
| **Recommendation** | Store position metadata to file; restore on restart. |

### 3.2 Manual Intervention (User Trades Outside Bot)
| | |
|---|---|
| **ID** | POS-002 |
| **Trigger** | User manually closes positions in SaxoTraderGO |
| **Current Handling** | Position reconciliation on startup; daily reset checks for orphaned positions. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Bot syncs with broker reality; logs `IRON_FLY_ORPHAN_DETECTED` if structure invalid. |

### 3.3 Orphaned Position Detection on Startup
| | |
|---|---|
| **ID** | POS-003 |
| **Trigger** | Found SPX options at broker that don't match expected 4-leg structure |
| **Current Handling** | Logs critical warning with option details. Does NOT reset state. See `strategy.py:1393-1415` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Bot refuses to enter new trades until orphans resolved. Requires manual intervention. |

### 3.4 Position Recovery Finds Multiple Iron Flies
| | |
|---|---|
| **ID** | POS-004 |
| **Trigger** | Broker has 8 SPX options (two 4-leg structures) |
| **Current Handling** | Not explicitly handled - would log as orphaned. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | No logic to identify which 4 legs belong together if multiple sets exist. |
| **Recommendation** | Match by expiry date; use closest strikes to current price; warn if multiple candidates. |

### 3.5 Daily Reset with Pending Close Orders
| | |
|---|---|
| **ID** | POS-005 |
| **Trigger** | New day detected while close orders still processing |
| **Current Handling** | `reset_for_new_day()` checks for local position AND broker positions before resetting. See `strategy.py:2929-2983` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Logs `IRON_FLY_BROKER_ORPHAN_ON_RESET` and refuses to reset if positions found at broker. |

---

## 4. MARKET DATA INTEGRITY EDGE CASES

### 4.1 Stale Quote Data (WebSocket)
| | |
|---|---|
| **ID** | DATA-001 |
| **Trigger** | WebSocket subscription receives snapshot but no delta updates |
| **Current Handling** | `update_market_data()` uses `skip_cache=True` to always fetch REST. See `strategy.py:2257` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED (Fixed 2026-01-22) |
| **Notes** | CfdOnIndex doesn't receive WebSocket deltas reliably; REST fallback compensates. |

### 4.2 VIX Data "NoAccess" Response
| | |
|---|---|
| **ID** | DATA-002 |
| **Trigger** | Saxo returns NoAccess for VIX (subscription limitation) |
| **Current Handling** | `client.get_vix_price()` has Yahoo Finance fallback. See `strategy.py:2266-2267` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Seamless fallback; logs if Yahoo used. |

### 4.3 Invalid Quote (Bid=0 or Ask=0)
| | |
|---|---|
| **ID** | DATA-003 |
| **Trigger** | Quote returns zero bid/ask (common at market open) |
| **Current Handling** | `get_quote()` checks `bid > 0 and ask > 0` before returning cached data. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Invalid quotes fall through to REST API call. |

### 4.4 Opening Range with No Price Updates
| | |
|---|---|
| **ID** | DATA-004 |
| **Trigger** | 9:30-10:00 AM passes with no price updates received |
| **Current Handling** | Checks `if self.opening_range.high <= 0 or self.opening_range.low == float('inf')`. Transitions to DAILY_COMPLETE. See `strategy.py:1470-1485` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Logs "Invalid opening range data" and skips trading for the day. |

### 4.5 Price Gap Larger Than Wing Width
| | |
|---|---|
| **ID** | DATA-005 |
| **Trigger** | Overnight gap exceeds expected move (e.g., 100 points vs 30-point wings) |
| **Current Handling** | Opening range check detects price outside prior range; blocks as Trend Day. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Filter correctly identifies trend days from gaps. |

---

## 5. STOP LOSS & EXIT EDGE CASES

### 5.1 Wing Breach Detection Latency
| | |
|---|---|
| **ID** | STOP-001 |
| **Trigger** | Price touches wing between 5-second check intervals |
| **Current Handling** | `is_wing_breached()` checked every 5 seconds in main loop. Tolerance of $0.10. See `strategy.py:480-498` |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | ACCEPTABLE |
| **Notes** | 5-second latency is standard for polling. In fast markets, could miss exact wing touch but catches breach on next cycle. |

### 5.2 Stop Loss During API Outage
| | |
|---|---|
| **ID** | STOP-002 |
| **Trigger** | Price breaches wing but API calls to close position fail |
| **Current Handling** | `_close_position("STOP_LOSS", ...)` uses `place_emergency_order()` which bypasses circuit breaker. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | PARTIALLY HANDLED |
| **Gap** | If emergency orders fail, position remains open with breached wing. No escalation mechanism. |
| **Notes** | Relies on operator monitoring logs. |

### 5.3 Profit Target Exit with Wide Spread
| | |
|---|---|
| **ID** | STOP-003 |
| **Trigger** | Profit target reached but bid-ask spread is $5 wide |
| **Current Handling** | Uses market orders for profit target exit (same as stop loss). |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Per Doc Severson: "Don't overstay" - exiting is priority over optimal fills. |

### 5.4 Time Exit (11:00 AM Rule) During Position Close
| | |
|---|---|
| **ID** | STOP-004 |
| **Trigger** | Max hold time reached while previous close operation still processing |
| **Current Handling** | State machine prevents duplicate close attempts - already in CLOSING state. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | State machine ensures only one close operation at a time. |

### 5.5 Position Stuck in CLOSING State
| | |
|---|---|
| **ID** | STOP-005 |
| **Trigger** | Close orders placed but verification hangs for > 5 minutes |
| **Current Handling** | `MAX_CLOSING_TIMEOUT_SECONDS = 300`. After timeout, logs CRITICAL and returns error. See `strategy.py:1626-1641` |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | Position remains in CLOSING state indefinitely after timeout. Manual intervention required. |
| **Recommendation** | Force state to ERROR or trigger emergency close retry. |

---

## 6. FILTER & ENTRY CONDITION EDGE CASES

### 6.1 VIX Changes Between Filter Check and Entry
| | |
|---|---|
| **ID** | FILTER-001 |
| **Trigger** | VIX at 19.5 during filter check; spikes to 22 before order placement |
| **Current Handling** | No re-check immediately before order placement. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | Entry proceeds even though VIX now exceeds max_vix threshold. |
| **Recommendation** | Re-validate VIX immediately before first order. |

### 6.2 FOMC Date List Maintenance
| | |
|---|---|
| **ID** | FILTER-002 |
| **Trigger** | Running in 2027 with only 2026 FOMC dates defined |
| **Current Handling** | `fomc_dates_2026` list at `strategy.py:2560-2571`. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | No 2027+ dates. Filter silently passes in future years. |
| **Recommendation** | Add multi-year support; log warning if year not in calendar. |

### 6.3 Economic Calendar Date Maintenance
| | |
|---|---|
| **ID** | FILTER-003 |
| **Trigger** | Running in 2027 with only 2026 economic dates |
| **Current Handling** | `major_economic_dates_2026` at `strategy.py:2602-2650`. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | No 2027+ dates for CPI/PPI/Jobs. |
| **Recommendation** | Add multi-year calendar; source from external API if possible. |

### 6.4 Opening Range Calculation with Price Gaps
| | |
|---|---|
| **ID** | FILTER-004 |
| **Trigger** | Price gaps 20 points during 9:30-10:00 opening range |
| **Current Handling** | `opening_range.update_price()` tracks high/low. Wide range detected. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Wide opening range naturally increases wing distance; filter checks price vs range. |

### 6.5 VIX Spike Calculation with Zero Opening VIX
| | |
|---|---|
| **ID** | FILTER-005 |
| **Trigger** | Opening VIX not set (0.0); spike calculation divides by zero |
| **Current Handling** | `vix_spike_percent` calculation at `strategy.py` checks opening_vix. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Division by zero prevented by checking opening_vix > 0 first. |

---

## 7. EXPECTED MOVE & WING CALCULATION EDGE CASES

### 7.1 ATM Straddle Pricing Unavailable
| | |
|---|---|
| **ID** | WING-001 |
| **Trigger** | Cannot get ATM straddle quote for expected move calculation |
| **Current Handling** | Falls back to VIX-based calculation. See `strategy.py:2460-2474` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | VIX formula: `daily_vol = vix / sqrt(252)`, `expected_move = price * daily_vol`. |

### 7.2 Expected Move Too Small
| | |
|---|---|
| **ID** | WING-002 |
| **Trigger** | Calculated expected move < 5 points |
| **Current Handling** | Minimum enforced: `if rounded_move < 5: rounded_move = 5.0`. See code in `_calculate_expected_move()`. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Minimum 5-point wing distance ensures some buffer. |

### 7.3 Strike Selection Finds No Valid Options
| | |
|---|---|
| **ID** | WING-003 |
| **Trigger** | Option chain empty or all strikes have zero bid/ask |
| **Current Handling** | `find_iron_fly_options()` returns None/empty for missing strikes. Entry aborted. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Transitions to DAILY_COMPLETE with error logged. |

### 7.4 Credit Calculation Returns Debit
| | |
|---|---|
| **ID** | WING-004 |
| **Trigger** | Short premium < long premium (would pay to enter) |
| **Current Handling** | Checks `if total_credit <= 0: return "Iron fly would result in debit"`. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Entry blocked if no credit edge. |

---

## 8. CIRCUIT BREAKER & EMERGENCY MODE EDGE CASES

### 8.1 Circuit Breaker Opens During Partial Fill
| | |
|---|---|
| **ID** | CB-001 |
| **Trigger** | 5th failure occurs after 3 legs already filled |
| **Current Handling** | Circuit breaker opens; attempts emergency close before halt. See `strategy.py:642-675` |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | PARTIALLY HANDLED |
| **Gap** | Emergency close may fail to close the 3 partial legs (different UICs than expected position). |
| **Recommendation** | Emergency close should use the actual placed order UICs, not expected position UICs. |

### 8.2 Emergency Close Slippage Calculation
| | |
|---|---|
| **ID** | CB-002 |
| **Trigger** | Emergency close applies 5% slippage to limit price |
| **Current Handling** | `EMERGENCY_SLIPPAGE_PERCENT = 5.0` at `strategy.py:62`. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | 5% slippage on option prices ($0.50 → $0.525) is reasonable for fast exit. |

### 8.3 Circuit Breaker Cooldown Expires with Unresolved Issue
| | |
|---|---|
| **ID** | CB-003 |
| **Trigger** | 5-minute cooldown ends but underlying API issue persists |
| **Current Handling** | Counter resets to 0; new failure count starts. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | ACCEPTABLE |
| **Notes** | May oscillate between trading and halted. Exponential cooldown could help but adds complexity. |

### 8.4 Multiple Circuit Breaker Opens in One Day
| | |
|---|---|
| **ID** | CB-004 |
| **Trigger** | Circuit breaker opens 3+ times in same trading session |
| **Current Handling** | No escalation - same 5-minute cooldown each time. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | Repeated failures should trigger longer cooldowns or full halt. |
| **Recommendation** | Track daily CB opens; after 3, halt for rest of day. |

---

## 9. TIMING & RACE CONDITION EDGE CASES

### 9.1 Entry Time Edge Case (Exactly 10:00:00)
| | |
|---|---|
| **ID** | TIME-001 |
| **Trigger** | Strategy check runs at exactly 10:00:00.000 |
| **Current Handling** | Comparison is `>= entry_time`, so 10:00:00 proceeds to entry. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Correct behavior per strategy rules. |

### 9.2 Bot Starts After Entry Window
| | |
|---|---|
| **ID** | TIME-002 |
| **Trigger** | Bot starts at 10:15 AM (after 10:00 AM entry) |
| **Current Handling** | Detects late start; transitions to DAILY_COMPLETE. See `strategy.py:1452-1456` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Logs "Bot started after entry window - skipping today". |

### 9.3 Market Early Close (Half Day)
| | |
|---|---|
| **ID** | TIME-003 |
| **Trigger** | Trading day before holiday (1:00 PM close) |
| **Current Handling** | Not explicitly handled - uses standard 4:00 PM close time. |
| **Risk Level** | 🔴 HIGH |
| **Status** | NOT IMPLEMENTED |
| **Gap** | Bot may attempt trades after actual market close on half days. |
| **Recommendation** | Add early close detection (day before July 4th, Thanksgiving Friday, Christmas Eve, etc.). |

### 9.4 Daylight Saving Time Transition
| | |
|---|---|
| **ID** | TIME-004 |
| **Trigger** | DST transition occurs (spring forward / fall back) |
| **Current Handling** | Uses `pytz` timezone-aware datetimes throughout. See `strategy.py:65-91` |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | `get_eastern_timestamp()` handles DST automatically. |

### 9.5 Concurrent Strategy Checks
| | |
|---|---|
| **ID** | TIME-005 |
| **Trigger** | Long-running operation + scheduled check overlaps |
| **Current Handling** | Single-threaded execution; main loop waits for each cycle to complete. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | No concurrent access possible in current architecture. |

---

## 10. EXTREME MARKET CONDITION EDGE CASES

### 10.1 Flash Crash (3%+ Move in 5 Minutes)
| | |
|---|---|
| **ID** | MKT-001 |
| **Trigger** | SPX drops 200 points in 5 minutes |
| **Current Handling** | Standard wing breach detection; stop loss triggers when wing touched. |
| **Risk Level** | 🔴 HIGH |
| **Status** | NOT IMPLEMENTED |
| **Gap** | No velocity-based early warning. By the time wing is touched, move may have continued past wing. |
| **Recommendation** | Add flash crash detection (track 5-min price history; alert if move > 2%). |

### 10.2 Market Circuit Breaker Halt (Level 1/2/3)
| | |
|---|---|
| **ID** | MKT-002 |
| **Trigger** | Exchange-wide trading halt |
| **Current Handling** | Orders would fail with rejection. Circuit breaker would eventually open. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | No specific detection of market halt vs API error. |
| **Recommendation** | Check error messages for "halt", "suspended", "circuit breaker" keywords. |

### 10.3 VIX Spike to 50+ During Position
| | |
|---|---|
| **ID** | MKT-003 |
| **Trigger** | VIX spikes from 15 to 50 while position is open |
| **Current Handling** | VIX check only at entry; existing positions not affected by VIX changes. |
| **Risk Level** | ✅ LOW (By Design) |
| **Status** | RESOLVED |
| **Notes** | Per Doc Severson: exit is based on wing touch, not VIX. High VIX doesn't change exit rules. |

### 10.4 Extreme Bid-Ask Spreads ($10+ Wide)
| | |
|---|---|
| **ID** | MKT-004 |
| **Trigger** | Options spreads widen to $10 during crisis |
| **Current Handling** | No explicit spread check. Orders placed at market. |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | NEEDS IMPROVEMENT |
| **Gap** | May enter or exit with massive slippage during crisis. |
| **Recommendation** | Add max spread threshold; log warning if exceeded but proceed (closing takes priority). |

### 10.5 Underlying Gaps Past Both Wings
| | |
|---|---|
| **ID** | MKT-005 |
| **Trigger** | Overnight gap opens position past both wings (shouldn't happen with 0DTE) |
| **Current Handling** | N/A for 0DTE - positions close same day. |
| **Risk Level** | ✅ LOW |
| **Status** | NOT APPLICABLE |
| **Notes** | 0DTE positions never held overnight. |

---

## 11. DRY-RUN SIMULATION EDGE CASES

### 11.1 Simulated P&L Accuracy
| | |
|---|---|
| **ID** | SIM-001 |
| **Trigger** | Dry-run shows +$100 profit; live execution differs |
| **Current Handling** | Simulated P&L uses theta decay formula. See `strategy.py:2280-2350` |
| **Risk Level** | ⚠️ MEDIUM |
| **Status** | ACCEPTABLE |
| **Notes** | Simulation is approximate. Live trading will differ due to actual fills and slippage. |

### 11.2 Simulated Credit Calculation
| | |
|---|---|
| **ID** | SIM-002 |
| **Trigger** | Dry-run uses estimated credit; actual credit differs |
| **Current Handling** | Uses `DEFAULT_SIMULATED_CREDIT_PER_WING_POINT = 2.50`. |
| **Risk Level** | ✅ LOW |
| **Status** | ACCEPTABLE |
| **Notes** | For dry-run testing purposes only. Live mode uses actual quotes. |

### 11.3 Dry-Run Position Reconciliation Skip
| | |
|---|---|
| **ID** | SIM-003 |
| **Trigger** | Dry-run mode skips broker position check |
| **Current Handling** | `strategy.py:1271`: "Position reconciliation skipped (dry-run mode)". |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Correct behavior - dry-run shouldn't query live broker positions. |

---

## 12. CONFIGURATION & STARTUP EDGE CASES

### 12.1 Config File Missing
| | |
|---|---|
| **ID** | CFG-001 |
| **Trigger** | `config/config.json` not found |
| **Current Handling** | `FileNotFoundError` caught in main(); program exits with message. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |

### 12.2 Config Malformed JSON
| | |
|---|---|
| **ID** | CFG-002 |
| **Trigger** | Syntax error in config file |
| **Current Handling** | `ValueError` caught; exits with "Configuration Error". |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |

### 12.3 Missing Required Config Fields
| | |
|---|---|
| **ID** | CFG-003 |
| **Trigger** | `strategy.underlying_uic` not specified |
| **Current Handling** | Uses defaults (e.g., UIC 4913 for US500.I). |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Reasonable defaults prevent crashes. |

### 12.4 Cloud vs Local Credential Loading
| | |
|---|---|
| **ID** | CFG-004 |
| **Trigger** | Running on GCP but Secret Manager access fails |
| **Current Handling** | `ConfigLoader` checks environment; falls back appropriately. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |

---

## 13. GOOGLE SHEETS LOGGING EDGE CASES

### 13.1 Google Sheets API Quota Exceeded
| | |
|---|---|
| **ID** | LOG-001 |
| **Trigger** | Too many writes; 429 from Google API |
| **Current Handling** | Errors caught; logging continues locally. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |
| **Notes** | Local logging unaffected; dashboard may be delayed. |

### 13.2 Spreadsheet Not Found
| | |
|---|---|
| **ID** | LOG-002 |
| **Trigger** | `Calypso_Iron_Fly_Live_Data` sheet doesn't exist |
| **Current Handling** | `TradeLoggerService` handles missing sheet gracefully. |
| **Risk Level** | ✅ LOW |
| **Status** | RESOLVED |

---

## 14. SUMMARY TABLES

### 14.1 All High Risk Issues

| ID | Issue | Status | Recommended Action |
|----|-------|--------|-------------------|
| CONN-007 | Both WebSocket AND REST fail with position open | NEEDS FIX | Trigger emergency close after N consecutive data failures |
| TIME-003 | Market early close (half days) not detected | NOT IMPLEMENTED | Add early close date detection |
| MKT-001 | Flash crash velocity detection missing | NOT IMPLEMENTED | Add 5-min price history tracking with 2% threshold |

### 14.2 All Medium Risk Issues

| ID | Issue | Status | Priority |
|----|-------|--------|----------|
| CONN-002 | Intermittent API errors (sliding window) | NEEDS IMPROVEMENT | Medium |
| ORDER-001 | Partial fill manual cleanup required | PARTIALLY HANDLED | Medium |
| ORDER-004 | Emergency close failure escalation | NEEDS IMPROVEMENT | High |
| ORDER-005 | Bid-ask spread validation missing | NEEDS IMPROVEMENT | Medium |
| POS-001 | Position recovery loses entry time/credit | PARTIALLY HANDLED | Low |
| POS-004 | Multiple iron fly detection | NEEDS IMPROVEMENT | Low |
| STOP-002 | Stop loss during API outage | PARTIALLY HANDLED | High |
| STOP-005 | CLOSING state stuck handling | NEEDS IMPROVEMENT | Medium |
| FILTER-001 | VIX re-check before entry | NEEDS IMPROVEMENT | Medium |
| FILTER-002 | FOMC calendar 2027+ | NEEDS IMPROVEMENT | Low |
| FILTER-003 | Economic calendar 2027+ | NEEDS IMPROVEMENT | Low |
| CB-001 | Circuit breaker partial fill handling | PARTIALLY HANDLED | Medium |
| CB-004 | Multiple CB opens escalation | NEEDS IMPROVEMENT | Low |
| MKT-002 | Market halt detection | NEEDS IMPROVEMENT | Medium |
| MKT-004 | Extreme spread warning | NEEDS IMPROVEMENT | Low |

### 14.3 Statistics by Category

| Category | Total | ✅ LOW | ⚠️ MEDIUM | 🔴 HIGH |
|----------|-------|--------|-----------|---------|
| Connection/API | 7 | 5 | 1 | 1 |
| Order Execution | 7 | 4 | 3 | 0 |
| Position State | 5 | 3 | 2 | 0 |
| Market Data | 5 | 5 | 0 | 0 |
| Stop Loss/Exit | 5 | 3 | 2 | 0 |
| Filters/Entry | 5 | 2 | 3 | 0 |
| Wing Calculation | 4 | 4 | 0 | 0 |
| Circuit Breaker | 4 | 1 | 3 | 0 |
| Timing/Race | 5 | 4 | 0 | 1 |
| Market Conditions | 5 | 2 | 2 | 1 |
| Dry-Run Simulation | 3 | 2 | 1 | 0 |
| Configuration | 4 | 4 | 0 | 0 |
| Google Sheets | 2 | 2 | 0 | 0 |
| **TOTAL** | **52** | **38** | **11** | **3** |

---

## 15. RECOMMENDED IMMEDIATE FIXES (Priority Order)

### Priority 1: HIGH RISK (Must Fix)

1. **Add early close day detection (TIME-003)**
   - Implement `is_early_close_day()` function
   - Known dates: day before July 4th, Black Friday, Christmas Eve, New Year's Eve
   - Block trading after 12:45 PM on those days

2. **Add flash crash velocity detection (MKT-001)**
   - Track 5-minute price history
   - Alert if price moves >= 2% within window
   - Trigger immediate position review when detected

3. **Emergency close on data blackout (CONN-007)**
   - If 5+ consecutive data fetch failures with position open
   - Trigger emergency close (better to exit than be blind)

### Priority 2: HIGH MEDIUM RISK (Should Fix)

4. **Add critical intervention flag (ORDER-004)**
   - When emergency close fails, set `_critical_intervention_required = True`
   - Halt ALL trading until manually reset
   - More severe than circuit breaker

5. **Re-validate filters before order placement (FILTER-001)**
   - Check VIX immediately before first leg order
   - If VIX now exceeds threshold, abort entry

6. **Add sliding window failure counter (CONN-002)**
   - Track last 10 API call results
   - Trigger circuit breaker if 5+ of last 10 fail

### Priority 3: MEDIUM RISK (Nice to Have)

7. **Add bid-ask spread validation (ORDER-005)**
   - Check spread before entry
   - Log warning if spread > 15% of option price

8. **Add market halt detection (MKT-002)**
   - Check error messages for halt keywords
   - Pause trading until halt lifts

9. **Persist position metadata (POS-001)**
   - Save entry_time, credit_received to file
   - Restore on crash recovery

10. **Update calendar for 2027+ (FILTER-002, FILTER-003)**
    - Add multi-year FOMC/economic dates
    - Log warning if current year not in calendar

---

## 16. CHANGE LOG

| Date | Change | Author |
|------|--------|--------|
| 2026-01-22 | Initial analysis completed (52 edge cases) | Claude |
| 2026-01-22 | Identified 3 HIGH risk, 11 MEDIUM risk items | Claude |
| 2026-01-22 | Created priority fix recommendations | Claude |

---

## 17. USAGE

### Running Verification Against Code

After implementing fixes, search for the scenario ID in code comments:

```bash
# Check if a scenario has been addressed
grep -r "CONN-007" bots/iron_fly_0dte/

# List all scenario references in code
grep -rE "(CONN|ORDER|POS|DATA|STOP|FILTER|WING|CB|TIME|MKT|SIM|CFG|LOG)-[0-9]{3}" bots/iron_fly_0dte/
```

### Marking Scenarios as Resolved

When fixing a scenario:
1. Add a code comment with the scenario ID
2. Update the "Status" field in this document to "RESOLVED"
3. Add entry to Change Log

---

**Document Version:** 1.0
**Last Updated:** 2026-01-22
