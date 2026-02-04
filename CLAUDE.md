# CALYPSO Trading Bot Infrastructure

## CRITICAL: Bot Control Warning

**NEVER use `kill` or `pkill` to stop bots!** All services have `Restart=always` with `RestartSec=30`. Killing a bot will cause it to auto-restart in 30 seconds. **ALWAYS use `systemctl stop`** to properly stop a bot.

---

## CRITICAL: Shared Code Change Policy

**Before modifying any code in `shared/`, STOP and consider:**

1. **Which bots use this code?** Check with `grep -r "function_name" bots/`
2. **Are those bots working correctly?** If Delta Neutral is working great, don't touch code it depends on unless absolutely necessary.
3. **Is this change surgical or broad?** Fix the specific bug, don't "improve" surrounding code.
4. **Get explicit approval** before changing shared code that affects working bots.

**The principle: Working code earns trust. Don't touch it without a clear reason.**

### When Fixing a Bug in One Bot:
- ✅ **DO**: Make the minimal change needed to fix that bot's issue
- ✅ **DO**: Keep changes isolated to that bot's code when possible
- ❌ **DON'T**: "Improve" shared code that other working bots depend on
- ❌ **DON'T**: Refactor or add defensive code to paths used by working bots

### If Shared Code Change Is Truly Necessary:
1. Explicitly state: "This change affects Delta Neutral / MEIC / etc."
2. Explain why the change is safe for those bots
3. Get user approval before proceeding
4. Test that all affected bots still work after deployment

**Example (2026-02-02):** When fixing Iron Fly's P&L calculation, changes were made to `saxo_client.py` that also affected Delta Neutral. While the changes were backwards-compatible, they should have been flagged for approval since Delta Neutral was working correctly.

---

## Project Overview

CALYPSO is a monorepo containing multiple automated options trading bots that trade SPX 0DTE options via Saxo Bank's OpenAPI. All bots run on a Google Cloud VM and use:
- **Saxo Bank OpenAPI** for order execution and market data
- **Google Secret Manager** for credentials (never in config files)
- **Google Sheets** for trade logging and dashboards
- **WebSocket streaming** for real-time price data
- **Pub/Sub + Cloud Functions** for SMS/Email alerts (Twilio + Gmail)

### Codebase Structure
```
bots/
  iron_fly_0dte/      # Doc Severson's Iron Fly strategy (PAUSED)
  delta_neutral/      # Brian's Delta Neutral strategy
  rolling_put_diagonal/  # Bill Belt's Rolling Put Diagonal strategy
  meic/               # Tammy Chambless's MEIC strategy (Multiple Entry Iron Condors)

shared/               # Shared modules used by all bots
  saxo_client.py      # Saxo Bank API client (orders, positions, streaming)
  logger_service.py   # Trade logging (Google Sheets, local files)
  config_loader.py    # Config loading with Secret Manager integration
  market_hours.py     # Market hours, holidays, early close detection
  event_calendar.py   # FOMC/economic calendar (SINGLE SOURCE OF TRUTH for all bots)
  secret_manager.py   # Google Secret Manager integration
  token_coordinator.py # OAuth token refresh coordination
  external_price_feed.py # Yahoo Finance fallback for VIX
  technical_indicators.py # TA calculations
  alert_service.py    # SMS/Email alerting via Pub/Sub

services/             # Standalone services (independent of trading bots)
  token_keeper/       # Keeps Saxo OAuth tokens fresh 24/7

cloud_functions/      # Google Cloud Functions
  alert_processor/    # Processes alerts from Pub/Sub, sends SMS/Email

scripts/              # Utility scripts (see scripts/README.md for full list)
  preview_live_entry.py    # PRIMARY: Shows what bot would do right now
  weekly_projection.py     # Compare multipliers side-by-side with P&L
  optimal_strike_analysis.py # Deep analysis with historical research
  check_short_strikes.py   # Quick strike check
  test_rest_api.py         # API connectivity test (pre-flight check)
  calculate_net_return.py  # Quick NET return calculation
```

---

## CRITICAL: Check Existing Scripts First

**BEFORE creating ANY new script (temporary or permanent) for any bot, you MUST:**

1. **Check `scripts/README.md`** - Contains a quick reference table of which script to use for common tasks
2. **List existing scripts**: `ls scripts/*.py scripts/**/*.py`
3. **Search for similar functionality**: Many analysis tasks already have dedicated scripts

### Script Quick Reference

| If you want to... | Use this (don't create new!) |
|-------------------|------------------------------|
| See what bot would do NOW | `preview_live_entry.py` |
| Compare premium at different multipliers | `weekly_projection.py` |
| Deep strategy analysis | `optimal_strike_analysis.py` |
| Quick strike check | `check_short_strikes.py` |
| Test API connectivity | `test_rest_api.py` |
| Calculate NET return | `calculate_net_return.py` |
| Find optimal symmetric strikes | `find_optimal_mult.py` |
| Calculate 1% target strikes | `calculate_1pct_target.py` |

### If Existing Script Needs Modification

If an existing script is close but needs small changes:
1. **Modify the existing script** rather than creating a new one
2. Document the change in git commit message
3. Update `scripts/README.md` if the script's purpose changed

### Running Scripts on VM

Scripts must run on VM to access Saxo API (local tokens are often expired):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/SCRIPT_NAME.py'"
```

---

## VM Details

- **VM Name:** `calypso-bot`
- **Zone:** `us-east1-b`
- **Project:** `calypso-trading-bot`
- **Calypso Path:** `/opt/calypso`
- **Calypso User:** `calypso`

---

## Trading Bots (4 Total)

| Bot | Service Name | Strategy | Config Path | Status |
|-----|--------------|----------|-------------|--------|
| Iron Fly | `iron_fly_0dte.service` | Doc Severson's 0DTE Iron Butterfly | `bots/iron_fly_0dte/config/config.json` | PAUSED |
| Delta Neutral | `delta_neutral.service` | Brian's Delta Neutral | `bots/delta_neutral/config/config.json` | LIVE |
| Rolling Put Diagonal | `rolling_put_diagonal.service` | Bill Belt's Rolling Put Diagonal | `bots/rolling_put_diagonal/config/config.json` | DRY-RUN |
| MEIC | `meic.service` | Tammy Chambless's MEIC (Multiple Entry Iron Condors) | `bots/meic/config/config.json` | LIVE |

All bots have: `Restart=always`, `RestartSec=30`, `StartLimitInterval=600`, `StartLimitBurst=5`

### Dry-Run vs Live Mode (Standardized)

**All bots use the same pattern for mode control:**

1. **Config file is the source of truth** - Add `"dry_run": true` or `"dry_run": false` at the root level of `config.json`
2. **CLI flag takes priority** - Running with `--dry-run` flag overrides the config setting
3. **Default is false (LIVE)** - If neither config nor CLI flag is set, bot runs in LIVE mode

**To switch a bot between modes:**
```bash
# Edit config on VM (no service file changes needed)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python << \"SCRIPT\"
import json
with open(\"bots/BOT_NAME/config/config.json\", \"r\") as f:
    config = json.load(f)
config[\"dry_run\"] = True  # or False for LIVE
with open(\"bots/BOT_NAME/config/config.json\", \"w\") as f:
    json.dump(config, f, indent=2)
print(\"Updated dry_run to:\", config[\"dry_run\"])
SCRIPT
'"

# Then restart the bot
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart BOT_SERVICE_NAME"
```

**Current Mode Status (Updated 2026-02-04):**
| Bot | Config `dry_run` | Mode | Service Status |
|-----|------------------|------|----------------|
| Iron Fly | `false` | LIVE | **RUNNING** |
| Delta Neutral | `false` | LIVE | **STOPPED** |
| MEIC | `false` | LIVE | **RUNNING** |
| Rolling Put Diagonal | `true` | DRY-RUN | **STOPPED** |

**Active Services:** `token_keeper`, `iron_fly_0dte`, `meic`

### Iron Fly Bot Details
- **Entry:** 10:00 AM EST (after 30-min opening range)
- **Exit:** Wing touch (stop loss) or 30% of credit profit target
- **Max hold:** 60 minutes (11:00 AM rule)
- **Wing width:** Minimum 40 points (Jim Olson rule), or expected move if higher
- **Filters:** VIX < 20, no FOMC days, price in opening range
- **Strategy spec:** Full strategy specification (see `docs/IRON_FLY_STRATEGY_SPECIFICATION.md`)
- **Edge cases:** 64 analyzed, all resolved (see `docs/IRON_FLY_EDGE_CASES.md`)
- **Code audit:** Comprehensive review completed (see `docs/IRON_FLY_CODE_AUDIT.md`)

#### Iron Fly Safety Features
- **Position Registry:** Uses shared Position Registry for multi-bot SPX trading isolation (2026-02-04)
- **Entry order:** Longs first (Long Call → Long Put → Short Call → Short Put) - safer on partial fills
- **Entry retries:** 3 attempts with 15-second delays; auto-unwind filled legs on failure
- **Stop losses:** Software-based via 2-second polling (NOT broker-side stops)
- **Wing breach tolerance:** $0.10 buffer to avoid floating-point issues
- **Circuit breaker:** 5 consecutive failures or 5-of-10 sliding window triggers halt

#### Iron Fly Typical P&L (1 contract, 40pt wings)
| Scenario | P&L | Notes |
|----------|-----|-------|
| Max profit (expires at ATM) | ~$1,500 | Rare - would hold to 4:00 PM |
| Profit target hit (30% of credit) | +$5 to +$15 net | Target exit |
| Time exit (11:00 AM rule) | +$0 to +$10 net | Small profit or breakeven |
| Stop loss (wing touch) | -$300 to -$350 | Typical stop-out |
| Max loss (circuit breaker) | -$400 | Safety cap |

### MEIC Bot Details (v1.2.0 - Updated 2026-02-02)
- **Strategy:** Tammy Chambless's MEIC (Multiple Entry Iron Condors) - "Queen of 0DTE"
- **Structure:** 6 scheduled iron condor entries per day
- **Entry times:** 10:00, 10:30, 11:00, 11:30, 12:00, 12:30 AM ET
- **Strikes:** VIX-adjusted for ~8 delta, 50-point spreads (25-120pt range based on VIX)
- **Stop loss:** Per-side stop = total credit received (breakeven design)
- **MEIC+ modification:** Stop = credit - $0.10 for small wins (configurable threshold)
- **Credit validation:** Warns if credit < $1.00 or > $1.75 per side
- **Expected results:** 20.7% CAGR, 4.31% max drawdown, 4.8 Calmar ratio, ~70% win rate
- **Edge cases:** 76 analyzed, all resolved (see `docs/MEIC_EDGE_CASES.md`)
- **Specification:** Full strategy spec (see `docs/MEIC_STRATEGY_SPECIFICATION.md`)

#### MEIC Key Features
- **Position Registry:** Uses shared Position Registry for multi-bot SPX trading isolation
- **Safe entry order:** Longs first (hedges) then shorts - never leave naked position
- **Per-entry stops:** Each IC has independent stop monitoring
- **FOMC blackout:** Skips all entries on FOMC announcement days
- **VIX filter:** Skips remaining entries if VIX > 25
- **Circuit breaker:** 5 consecutive failures or 5-of-10 sliding window triggers halt

#### MEIC Typical P&L (per IC, 50pt spreads, $2.50 credit)
| Scenario | Probability | P&L |
|----------|-------------|-----|
| Both sides expire worthless | ~60% | +$250 |
| One side stopped, other expires | ~34% | ~$0 (breakeven) |
| Both sides stopped | ~6% | -$250 to -$750 |

**Note:** MEIC and Iron Fly both trade SPX 0DTE options. The Position Registry prevents conflicts when running simultaneously.

### Delta Neutral Bot Details
- **Version:** 2.0.6 (Updated 2026-02-03 with margin settlement delay and improved retry logic)
- **Strategy:** Brian Terry's Delta Neutral (from Theta Profits)
- **Structure:** Long ATM straddle (90-120 DTE) + Weekly short strangles (5-12 DTE)
- **Long Entry:** 120 DTE target (configurable)
- **Long Exit:** 60 DTE threshold - close everything when longs reach this point
- **Shorts Roll:** Weekly (Thursday/Friday) to next week's expiry for continued premium collection
- **Strike Selection:** Scan 2.0x→1.33x for 1.5% NET return, safety extension to 1.0x if floor gives negative
- **Opening Range Delay:** Wait until 10:00 AM for fresh entries (0 positions) - first 30 min are volatile
- **Adaptive Roll Trigger:** Rolls shorts when 75% of original cushion is consumed (scales with market conditions)
- **Immediate Re-Entry:** After scheduled debit skip, enters next-week shorts immediately (no 19-hour gap)
- **Recenter:** When SPY moves ±$5 from initial strike, rebalance long straddle strikes
- **Edge cases:** 61 analyzed, all resolved (see `docs/DELTA_NEUTRAL_EDGE_CASES.md`)
- **Full specification:** See [DELTA_NEUTRAL_STRATEGY_SPECIFICATION.md](docs/DELTA_NEUTRAL_STRATEGY_SPECIFICATION.md)

#### Delta Neutral Safety Features (Added 2026-02-01)
| Feature | Description | Config Key |
|---------|-------------|------------|
| ORDER-006 | Order size validation (max 10/order, 20/underlying) | `order_limits.*` |
| ORDER-007 | Fill slippage monitoring (5% warning, 15% critical) | `slippage_monitoring.*` |
| ORDER-008 | Emergency close retries with spread wait | `emergency_close.*` |
| Activities Retry | 3 attempts × 1s delay for sync issues | Built-in |

#### Delta Neutral Key Logic

**Opening Range Delay (2026-01-29):**
When bot has 0 positions and wants to enter from scratch:
- Wait until 10:00 AM ET (configurable via `fresh_entry_delay_minutes: 30`)
- First 30 minutes after open are volatile - VIX can spike/drop misleadingly
- State: `WAITING_OPENING_RANGE` until delay ends
- Does NOT apply to re-entries when we already have longs (e.g., after ITM close)

**Strike Selection Priority (2026-01-29):**
Bot uses 3-tier fallback to balance profit target vs safety:
1. **Optimal:** Find highest multiplier (2.0x→1.33x) achieving 1.5% NET return (widest = safest)
2. **Fallback:** Use 1.33x floor with whatever positive return (safe roll trigger at 1.0x EM)
3. **Safety Extension:** If floor gives zero/negative, scan 1.33x→1.0x for first positive
4. **Abort:** Skip entry if no positive return found even at 1.0x

**Why 1.33x Floor?** Formula: `1.0 / 0.75 = 1.33` ensures roll trigger (at 75% cushion consumed) lands exactly at 1.0x expected move boundary.

**Proactive Restart Check:**
Instead of waiting for longs to hit 60 DTE and then closing (which wastes recently opened shorts), the bot checks BEFORE opening/rolling shorts:
- Calculate: `days_until_longs_hit_60_DTE = long_dte - 60`
- Get: expected DTE for new shorts (5-12 days typically)
- If `new_shorts_dte > days_until_longs_hit_60_DTE`:
  - Close everything NOW (don't wait for 60 DTE trigger)
  - Start fresh with new 120 DTE longs + new shorts
  - This avoids scenarios where shorts are opened with 7+ DTE but longs only have 5 days until hitting exit threshold

**Why this matters:**
- Without this: Bot opens shorts on Thursday, longs hit 60 DTE on Monday → exit everything → wasted 4 days of theta on shorts
- With this: Bot detects the conflict BEFORE opening shorts → closes everything → starts fresh → maximizes theta collection

**Abort Callbacks for Recenter/Roll (2026-02-03):**
When executing recenter or roll operations, the bot re-checks conditions before each retry attempt on the first leg:
- **Recenter:** If SPY price bounces back and distance to strike drops below threshold, abort the recenter
- **Roll:** If price moves away from the challenged strike (cushion consumption drops below 75%), abort the roll
- This prevents unnecessary operations when price briefly touches a threshold then bounces back
- Only checked during close phase (leg 1) - once leg 1 fills, we're committed to completing the operation

**SHORT_STRANGLE_ONLY Recovery State (2026-02-03):**
A recovery state for when the bot has only short strangle positions (no longs):
- **Trigger:** Recenter fails mid-way - closes longs successfully but fails to re-enter new longs
- **Behavior:** Bot sets state to `SHORT_STRANGLE_ONLY` and enters longs normally on next cycle
- **Recovery:** Automatically transitions to `FULL_POSITION` once longs are entered
- **Duration:** Typically resolved within 10-60 seconds (next strategy check)
- **Note:** Previously, shorts-only incorrectly set `FULL_POSITION` which caused the bot to skip entering longs

**Margin Settlement Delay (Fix #24, 2026-02-03):**
After closing positions (longs or shorts), wait 3 seconds before entering new positions:
- **Why:** Saxo rejects orders with `WouldExceedMargin` if cash from close isn't immediately available
- **Where:** Recenter (after close longs), Roll (after close shorts), all partial straddle recovery paths
- **Duration:** 3 seconds hardcoded delay
- **Logs:** `"⏳ Waiting 3.0s for margin to settle after close..."`

**Retry Delay Between Attempts (Fix #25, 2026-02-03):**
Add 1.5 second delay between retry attempts in `_place_protected_multi_leg_order`:
- **Why:** Rapid-fire retries caused 409 Conflict errors when Saxo hadn't fully processed previous cancellation
- **When:** After each failed limit order attempt, before the next retry
- **Duration:** 1.5 seconds between retries
- **Logs:** `"Waiting 1.5s before retry..."`

**Fresh Quote Retry on Invalid (Fix #26, 2026-02-03):**
Retry fetching quotes up to 3 times if invalid (Bid=0/Ask=0):
- **Why:** After closing positions, quotes may briefly be unavailable; using stale leg_price caused `PriceExceedsAggressiveTolerance`
- **How:** Up to 3 attempts with 1.5s wait between each
- **Fallback:** Only use original leg_price as last resort (with warning about staleness)
- **Logs:** `"⚠️ Quote pending for UIC ... (attempt X/3)"`

### Bot Isolation
Iron Fly (SPX/SPXW) and Delta Neutral (SPY) are mostly independent:
- Different underlying instruments (UIC 4913/128 vs SPY UICs)
- Separate systemd processes
- Separate config files, logs, and state files
- No shared position data

**Shared Resources:**
- `token_coordinator.py` manages OAuth token refresh across all bots via file-based locking
- When one bot refreshes the token, others pick up the fresh token from the shared cache
- WebSocket connections refresh tokens before connecting to avoid 401 errors (CONN-008 fix)
- `position_registry.py` tracks which bot owns which position (for SPX multi-bot isolation)
  - File-based persistence at `/opt/calypso/data/position_registry.json`
  - fcntl file locking for concurrent access
  - Required when running MEIC + Iron Fly simultaneously (both trade SPX)

---

## Token Keeper Service (NEW - 2026-01-27)

A dedicated service that keeps Saxo OAuth tokens fresh 24/7, independent of trading bot status.

### Why It's Needed
- Saxo tokens expire every **20 minutes**
- If all bots are stopped (e.g., for safety or maintenance), no one refreshes the token
- Expired tokens require **manual OAuth browser flow** to re-authenticate
- Token Keeper ensures tokens stay fresh even when all trading bots are stopped

### How It Works
| Property | Value |
|----------|-------|
| Service Name | `token_keeper.service` |
| Check Interval | Every 60 seconds |
| Refresh Threshold | 5 minutes before expiry |
| Token Cache | `/opt/calypso/data/saxo_token_cache.json` |
| Lock File | `/opt/calypso/data/saxo_token.lock` |
| Restart Policy | `Restart=always`, `RestartSec=10` |

### Service Commands
```bash
# Start token keeper
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start token_keeper"

# Stop token keeper
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop token_keeper"

# Check status
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status token_keeper"

# View logs
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper -n 50 --no-pager"

# Follow logs (live)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper -f"
```

### First-Time Deployment
```bash
# 1. Copy service file to systemd
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo cp /opt/calypso/deploy/token_keeper.service /etc/systemd/system/"

# 2. Reload systemd
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl daemon-reload"

# 3. Enable service (auto-start on boot)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl enable token_keeper"

# 4. Start service
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start token_keeper"
```

### Integration with Trading Bots
- Uses the same `TokenCoordinator` as all trading bots
- File-based locking prevents race conditions during refresh
- All bots read from the same cache file (`saxo_token_cache.json`)
- Token Keeper runs with `Before=` directive to start before trading bots

**Important:** Token Keeper should be the **only** service actively refreshing tokens. Trading bots will:
1. Check the shared cache on startup
2. Use cached tokens (refreshed by Token Keeper)
3. Only refresh if Token Keeper is down and token is about to expire

---

## Alert System (SMS/Email)

All bots use a shared alerting system via Google Cloud Pub/Sub and Cloud Functions.

### Architecture
```
Bot → AlertService → Pub/Sub (~50ms) → Cloud Function → Twilio/Gmail → User
```

**Key Design Principle:** Alerts are sent AFTER actions complete with ACTUAL results (not predictions). The bot publishes to Pub/Sub (~50ms non-blocking) and continues immediately. Cloud Function delivers SMS/email asynchronously.

**Timezone:** All alert timestamps use US Eastern Time (ET) - the exchange timezone. Consistent regardless of where you travel. DST transitions (EST ↔ EDT) are handled automatically.

### Alert Priority Levels
| Priority | Delivery | Examples |
|----------|----------|----------|
| CRITICAL | WhatsApp + Email | Circuit breaker, emergency exit, naked position, ITM risk close |
| HIGH | WhatsApp + Email | Stop loss, max loss, wing breach, roll failed, vigilant mode entry |
| MEDIUM | WhatsApp + Email | Position opened/closed, profit target, roll complete, recenter |
| LOW | WhatsApp + Email | Bot started/stopped, daily summary, vigilant mode exit |

**Note:** ALL alerts go to WhatsApp (rich formatting) + Email. SMS is fallback only.

### Alert Responsibilities by Bot (2026-01-27)

| Bot | AlertService | MarketStatusMonitor |
|-----|--------------|---------------------|
| **Iron Fly** | ✅ IRON_FLY | ❌ |
| **Delta Neutral** | ✅ DELTA_NEUTRAL | ✅ (sole owner) |
| **Rolling Put Diagonal** | ✅ ROLLING_PUT_DIAGONAL | ❌ |
| **MEIC** | ✅ MEIC | ❌ |

**MarketStatusMonitor** (only on Delta Neutral to avoid duplicates):
- Market opening countdown (1h, 30m, 15m before open) - sends WhatsApp/Email alerts
- Market open notification (at 9:30 AM ET)
- Market close notification (at 4:00 PM ET or early close)
- Holiday notifications (weekday market closures)

**Delta Neutral ITM Monitoring Alerts** (Updated 2026-01-28):
- VIGILANT_ENTERED (HIGH): 60-75% of original cushion consumed (adaptive threshold)
- VIGILANT_EXITED (LOW): Cushion consumption drops below 60% (back to safe zone)
- ITM_RISK_CLOSE (CRITICAL): Shorts emergency closed at 0.1% from strike (absolute safety floor)
- ROLL_COMPLETED (MEDIUM): Weekly shorts rolled successfully
- RECENTER (MEDIUM): Long straddle recentered to new ATM strike

### Enabling Alerts
Add to each bot's `config.json`:
```json
{
    "alerts": {
        "enabled": true,
        "phone_number": "+1XXXXXXXXXX",
        "email": "your@email.com"
    }
}
```

### Key Files
| File | Purpose |
|------|---------|
| `shared/alert_service.py` | AlertService class used by bots |
| `cloud_functions/alert_processor/main.py` | Cloud Function that sends SMS/email |
| `docs/ALERTING_SETUP.md` | Full deployment guide |

### Testing Alerts Locally (Dry Run)
```bash
export ALERT_DRY_RUN=true
python -c "
from shared.alert_service import AlertService
svc = AlertService({'alerts': {'enabled': True}}, 'TEST')
svc.circuit_breaker('Test reason', 3)
"
```

### Monitoring Alert Delivery
```bash
# View Cloud Function logs
gcloud functions logs read process-trading-alert --region=us-east1 --project=calypso-trading-bot --limit=50

# Check dead letter queue for failed alerts
gcloud pubsub subscriptions pull calypso-alerts-dlq-sub --project=calypso-trading-bot --limit=10 --auto-ack
```

---

## Quick Reference Commands

### Emergency Stop (All Bots + Token Keeper)
```bash
# Stop all trading bots (token keeper keeps running to preserve auth)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop iron_fly_0dte delta_neutral rolling_put_diagonal meic"

# Stop EVERYTHING including token keeper (token will expire in ~20 min!)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop iron_fly_0dte delta_neutral rolling_put_diagonal meic token_keeper"
```

### Stop Individual Services
```bash
# Iron Fly
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop iron_fly_0dte"

# Delta Neutral
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop delta_neutral"

# Rolling Put Diagonal
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop rolling_put_diagonal"

# MEIC
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop meic"

# Token Keeper (WARNING: token will expire in ~20 min without this!)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop token_keeper"
```

### Start Services
```bash
# Start token keeper first (ensures fresh token for bots)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start token_keeper"

# Start all trading bots
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start iron_fly_0dte delta_neutral rolling_put_diagonal meic"

# Individual bots
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start iron_fly_0dte"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start delta_neutral"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start rolling_put_diagonal"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start meic"
```

### Restart Services
```bash
# Restart all trading bots (token keeper usually doesn't need restart)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart iron_fly_0dte delta_neutral rolling_put_diagonal meic"

# Individual bots
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart iron_fly_0dte"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart delta_neutral"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart rolling_put_diagonal"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart meic"

# Token Keeper (rarely needed)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart token_keeper"
```

### Check Status
```bash
# All services (bots + token keeper)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status token_keeper iron_fly_0dte delta_neutral rolling_put_diagonal meic"

# List running Calypso services
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl list-units --type=service | grep -E '(iron|delta|rolling|meic|token_keeper)'"
```

### View Logs
```bash
# Token Keeper logs (check token refresh status)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper -n 50 --no-pager"

# Trading bot logs (50 lines)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u iron_fly_0dte -n 50 --no-pager"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u delta_neutral -n 50 --no-pager"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u rolling_put_diagonal -n 50 --no-pager"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u meic -n 50 --no-pager"

# Follow logs (live)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper -f"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u iron_fly_0dte -f"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u meic -f"

# Today's logs
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper --since today --no-pager"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u iron_fly_0dte --since today --no-pager"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u meic --since today --no-pager"
```

---

## Deployment Workflow

### Pre-Commit Checklist

**IMPORTANT:** Before every commit, ensure documentation is updated for any code changes:

1. **Update `__init__.py` files** - If you added, removed, or changed exports in a module
2. **Update docstrings/comments** - For any functions or classes you modified
3. **Update relevant `.md` files:**
   - `README.md` - If features, safety measures, or project structure changed
   - `CLAUDE.md` - If VM commands, bot details, or workflows changed
   - `docs/IRON_FLY_EDGE_CASES.md` - If edge case handling changed
   - `docs/IRON_FLY_CODE_AUDIT.md` - If significant code changes were made
   - `bots/*/README.md` - If bot-specific behavior changed
4. **Update "Last Updated" dates** - In any `.md` files you modified

### Push Local Changes to VM

1. **Commit and push locally:**
```bash
git add -A
git commit -m "your message"
git push
```

2. **Pull on VM and clear Python cache (must use calypso user):**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'"
```

> **Why clear cache?** Python caches compiled bytecode in `__pycache__` directories. Stale cache can cause bots to run old code even after `git pull`. This was discovered when VIX data fetching appeared broken but was actually using cached old code.

3. **Restart bots to apply changes:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart iron_fly_0dte delta_neutral rolling_put_diagonal meic"
```

4. **Verify status:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status iron_fly_0dte delta_neutral rolling_put_diagonal meic"
```

5. **Post-Deployment Documentation Update (MANDATORY):**

After successfully deploying code and verifying it works, you MUST update all relevant documentation:

| What Changed | Files to Update |
|--------------|-----------------|
| New exports in shared/ | `shared/__init__.py` - add to imports and `__all__` |
| New/changed functions | Update docstrings in the source file |
| Bot behavior changes | `CLAUDE.md`, `bots/*/README.md` |
| Edge case handling | `docs/*_EDGE_CASES.md` |
| API patterns | `docs/SAXO_API_PATTERNS.md` |
| Filter/calendar changes | `shared/event_calendar.py` docstrings |
| Alert system changes | `docs/ALERTING_SETUP.md` |

**Documentation Checklist:**
- [ ] `__init__.py` exports updated for any new public functions
- [ ] Docstrings added/updated for modified functions
- [ ] Code comments explain non-obvious logic
- [ ] Relevant `.md` files updated with new behavior
- [ ] "Last Updated" dates changed in modified `.md` files

**Example:** When adding FOMC calendar functions:
1. Add functions to `shared/event_calendar.py` with docstrings
2. Export them in `shared/__init__.py`
3. Update `CLAUDE.md` if workflow changed
4. Update `docs/IRON_FLY_EDGE_CASES.md` if edge case handling changed

---

## VM System Commands

```bash
# SSH connect interactively
gcloud compute ssh calypso-bot --zone=us-east1-b

# Disk usage
gcloud compute ssh calypso-bot --zone=us-east1-b --command="df -h"

# Memory usage
gcloud compute ssh calypso-bot --zone=us-east1-b --command="free -h"

# Running Python processes
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ps aux | grep python"

# List log directories
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/logs/"

# List data directory
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/data/"
```

---

## Config Files

**IMPORTANT:** Config files are in `.gitignore` and must be edited directly on the VM. They are NOT synced via git. Local config files are for development only - production configs live on the VM.

To edit VM configs, use `nano` or `vim` via SSH:
```bash
# Edit Iron Fly config on VM
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso nano /opt/calypso/bots/iron_fly_0dte/config/config.json"

# After editing, restart the bot to apply changes
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart iron_fly_0dte"
```

```bash
# View VM config
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/iron_fly_0dte/config/config.json"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/delta_neutral/config/config.json"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/rolling_put_diagonal/config/config.json"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/meic/config/config.json"

# View systemd service files
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /etc/systemd/system/iron_fly_0dte.service"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /etc/systemd/system/delta_neutral.service"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /etc/systemd/system/rolling_put_diagonal.service"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /etc/systemd/system/meic.service"
```

---

## Google Secret Manager

```bash
# List secrets
gcloud secrets list --project=calypso-trading-bot

# View a secret
gcloud secrets versions access latest --secret=SECRET_NAME --project=calypso-trading-bot
```

---

## Key Saxo Bank Symbols

| Symbol | UIC | Description |
|--------|-----|-------------|
| US500.I | 4913 | S&P 500 CFD (for SPX price tracking) |
| SPXW:xcbf | 128 | SPX Weekly options (0DTE) |
| VIX.I | 10606 | VIX spot price |
| VIX:xcbf | 117 | VIX options |

---

## Troubleshooting

### VIX Data Falling Back to Yahoo Finance

**Symptom:** Logs show `VIX: Saxo failed (...), using Yahoo fallback` repeatedly.

**Root Cause (Fixed 2026-01-23):** VIX is a stock index, not a tradable instrument. Unlike stocks/ETFs that have bid/ask/mid prices, VIX only provides `LastTraded` in the `PriceInfoDetails` block. If the WebSocket subscription doesn't request `PriceInfoDetails` in FieldGroups, the cache will have no extractable price.

**Solution:** Ensure `start_price_streaming()` in `saxo_client.py` includes `"PriceInfoDetails"` in the FieldGroups:
```python
"FieldGroups": ["DisplayAndFormat", "Quote", "PriceInfo", "PriceInfoDetails"]
```

**Debugging Steps:**
1. Check logs for the specific failure reason: `cache(no valid price)` or `REST(no valid price in response)`
2. Run the VIX REST API test to see what data Saxo returns
3. Compare cache snapshot vs REST response - cache may be missing `PriceInfoDetails`

### Bots Running Old Code After Deployment

**Symptom:** Code changes don't take effect even after `git pull` and bot restart.

**Root Cause:** Python bytecode cache (`.pyc` files in `__pycache__`) can persist old compiled code.

**Solution:** Always clear cache after pulling changes (now in standard deployment workflow):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'"
```

### Pre-Market Price Fetching Fails (Before 7:00 AM ET)

**Symptom:** Bots log "No quote yet" or fail to get prices during what appears to be pre-market hours.

**Root Cause (Fixed 2026-01-26):** Saxo Bank's extended hours trading only starts at 7:00 AM ET. Before this time, no pre-market data is available. The bots were attempting to fetch prices too early.

**Saxo Extended Hours Schedule:**
| Session | Time (ET) | Notes |
|---------|-----------|-------|
| Pre-Market | 7:00 AM - 9:30 AM | Limit orders only |
| Regular | 9:30 AM - 4:00 PM | Full trading |
| After-Hours | 4:00 PM - 5:00 PM | Limit orders only |

**Solution:** Use `is_saxo_price_available()` from `shared/market_hours.py` before fetching prices:
```python
from shared.market_hours import is_saxo_price_available

if is_saxo_price_available():  # True only 7:00 AM - 5:00 PM ET on trading days
    quote = client.get_quote(uic)
else:
    logger.info("Saxo prices not available yet")
```

**See:** [SAXO_API_PATTERNS.md Section 10](docs/SAXO_API_PATTERNS.md#10-extended-hours-trading-pre-market--after-hours)

### WebSocket Streaming Shows Stale Prices

**Symptom:** Cached prices from WebSocket don't update. Logs show the same price for minutes/hours. Bot falls back to REST API for every price check.

**Root Cause (Fixed 2026-01-26):** Saxo Bank sends WebSocket messages as **binary frames**, not plain JSON text. Previous code tried to decode binary as UTF-8 text which silently failed, leaving the cache with only the initial snapshot.

**Binary Frame Format:**
```
| 8 bytes | 2 bytes  | 1 byte      | N bytes | 1 byte  | 4 bytes | N bytes |
| Msg ID  | Reserved | RefID Len   | RefID   | Format  | Size    | Payload |
| uint64  |          | uint8       | ASCII   | 0=JSON  | int32   | JSON    |
```

**Solution:** The `_decode_binary_ws_message()` function in `saxo_client.py` now properly parses binary frames using Python's `struct` module:
```python
# Proper binary parsing (in saxo_client.py)
msg_id = struct.unpack_from('<Q', raw, pos)[0]  # 8 bytes, uint64 little-endian
ref_id_len = struct.unpack_from('B', raw, pos)[0]  # 1 byte
payload_size = struct.unpack_from('<i', raw, pos)[0]  # 4 bytes, int32 little-endian
```

**Impact on All Bots:**
| Bot | Benefit |
|-----|---------|
| Delta Neutral | ITM monitoring now works at 1-second intervals (was 3s, now uses cache) |
| Iron Fly | Option price callbacks in `handle_price_update()` now actually fire |
| Rolling Put Diagonal | Same benefit as Iron Fly |

**Verification:** After bot restart, check logs for WebSocket update messages:
```
WebSocket update #1: UIC 36590 = $693.19
WebSocket update #2: UIC 36590 = $693.24
```
Prices should change over time, not stay static.

---

## Running Diagnostic Scripts on VM

When debugging issues, you often need to run Python scripts directly on the VM to test API calls, inspect data structures, or diagnose problems. This section documents the **exact patterns that work**.

### Critical Requirements

1. **Run as `calypso` user** - Required for file permissions and Secret Manager access
2. **Run from `/opt/calypso`** - Required for Python imports to resolve
3. **Use the virtualenv Python** - `.venv/bin/python` has all dependencies
4. **Use heredoc for multi-line scripts** - Avoids quoting hell

### Pattern 1: Simple One-Liner Script

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -c \"from shared import SaxoClient; print(SaxoClient)\"'"
```

### Pattern 2: Multi-Line Script (RECOMMENDED)

Use heredoc (`<<'SCRIPT'`) to write readable multi-line Python scripts:

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python << \"SCRIPT\"
import sys
sys.path.insert(0, \"/opt/calypso\")

from shared.saxo_client import SaxoClient
from shared.config_loader import get_config_loader

# Load config
config_loader = get_config_loader(\"bots/iron_fly_0dte/config\")
config = config_loader.load_config()

# Create client and authenticate
client = SaxoClient(config)
client.authenticate()

# Run your diagnostic code
positions = client.get_positions()
print(f\"Found {len(positions)} positions\")
for p in positions:
    print(p)

SCRIPT
'"
```

### Pattern 3: Test Specific API Calls

**Check current positions:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python << \"SCRIPT\"
import sys
sys.path.insert(0, \"/opt/calypso\")
from shared.saxo_client import SaxoClient
from shared.config_loader import get_config_loader

config = get_config_loader(\"bots/iron_fly_0dte/config\").load_config()
client = SaxoClient(config)
client.authenticate()

positions = client.get_positions()
print(f\"Positions: {len(positions)}\")
for p in positions:
    uic = p.get(\"PositionBase\", {}).get(\"Uic\")
    amount = p.get(\"PositionBase\", {}).get(\"Amount\")
    strike = p.get(\"PositionBase\", {}).get(\"OptionsData\", {}).get(\"Strike\")
    print(f\"  UIC={uic}, Amount={amount}, Strike={strike}\")
SCRIPT
'"
```

**Check VIX price:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python << \"SCRIPT\"
import sys
sys.path.insert(0, \"/opt/calypso\")
from shared.saxo_client import SaxoClient
from shared.config_loader import get_config_loader

config = get_config_loader(\"bots/iron_fly_0dte/config\").load_config()
client = SaxoClient(config)
client.authenticate()

vix = client.get_vix_level()
print(f\"VIX: {vix}\")
SCRIPT
'"
```

**Inspect saved position metadata:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/data/iron_fly_position.json"
```

### Common Mistakes to Avoid

| Mistake | Why It Fails | Solution |
|---------|--------------|----------|
| Not using `sudo -u calypso` | Permission errors, can't access Secret Manager | Always use `sudo -u calypso bash -c '...'` |
| Running from wrong directory | `ModuleNotFoundError: No module named 'shared'` | Always `cd /opt/calypso` first |
| Using system Python | Missing dependencies | Use `.venv/bin/python` |
| Single quotes inside single quotes | Shell quoting breaks | Use heredoc or escape carefully |
| Missing `sys.path.insert` | Imports may fail | Add at top of script |

### Debugging Import Errors

If imports fail, run this to check the environment:

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python << \"SCRIPT\"
import sys
print(\"Python:\", sys.executable)
print(\"Path:\")
for p in sys.path:
    print(f\"  {p}\")
print()
print(\"Trying imports...\")
try:
    from shared import SaxoClient
    print(\"OK: shared.SaxoClient\")
except Exception as e:
    print(f\"FAIL: {e}\")
SCRIPT
'"
```

---

## Important Notes

1. **Git on VM:** Must run as `calypso` user: `sudo -u calypso bash -c 'cd /opt/calypso && git pull'`
2. **Service names use underscores:** `iron_fly_0dte`, `delta_neutral`, `rolling_put_diagonal`, `meic`
3. **Log locations:** `/opt/calypso/logs/{iron_fly_0dte,delta_neutral,rolling_put_diagonal,meic}/bot.log`
4. **Position data:** `/opt/calypso/data/iron_fly_position.json`
5. **Config files are gitignored:** Real credentials come from Secret Manager
6. **All API calls are direct:** No caching for order status or positions (always fresh from Saxo)
7. **Iron Fly bot:** Running in LIVE mode (as of 2026-02-04)
8. **Delta Neutral bot:** STOPPED (as of 2026-02-04)
9. **Rolling Put Diagonal bot:** STOPPED (as of 2026-02-04)
10. **MEIC bot:** Running in LIVE mode (v1.2.1, switched to LIVE 2026-02-04)
11. **FOMC Calendar:** Single source of truth in `shared/event_calendar.py` - ALL bots import from there (updated 2026-01-26)
12. **Token Keeper:** Always running - keeps OAuth tokens fresh 24/7

---

## Documentation

### Quick Reference by Problem Type

| Problem | Document | Key Sections |
|---------|----------|--------------|
| P&L incorrect | [SAXO_API_PATTERNS.md](docs/SAXO_API_PATTERNS.md) | Section 2: Extracting Fill Prices |
| Orders stuck/unknown | [SAXO_API_PATTERNS.md](docs/SAXO_API_PATTERNS.md) | Section 6: Order Status Handling |
| VIX fallback to Yahoo | [SAXO_API_PATTERNS.md](docs/SAXO_API_PATTERNS.md) | Section 3: Price Data Extraction |
| Wrong asset type errors | [SAXO_API_PATTERNS.md](docs/SAXO_API_PATTERNS.md) | Section 4: Asset Type Mapping |
| WebSocket 401 errors | [IRON_FLY_CODE_AUDIT.md](docs/IRON_FLY_CODE_AUDIT.md) | Section 8.5: WebSocket Token Refresh |
| Entry filter questions | [IRON_FLY_CODE_AUDIT.md](docs/IRON_FLY_CODE_AUDIT.md) | Section 6: Filter Implementation |
| Edge case handling | [IRON_FLY_EDGE_CASES.md](docs/IRON_FLY_EDGE_CASES.md) | All 64 documented cases |
| **Iron Fly strategy** | [IRON_FLY_STRATEGY_SPECIFICATION.md](docs/IRON_FLY_STRATEGY_SPECIFICATION.md) | Wing width rules, profit targets, Doc Severson + Jim Olson |
| **Delta Neutral strategy** | [DELTA_NEUTRAL_STRATEGY_SPECIFICATION.md](docs/DELTA_NEUTRAL_STRATEGY_SPECIFICATION.md) | Roll mechanics, credit/debit logic, full spec |
| **MEIC strategy spec** | [MEIC_STRATEGY_SPECIFICATION.md](docs/MEIC_STRATEGY_SPECIFICATION.md) | Full MEIC implementation details |
| **MEIC edge cases** | [MEIC_EDGE_CASES.md](docs/MEIC_EDGE_CASES.md) | 75 edge cases for MEIC bot |
| SMS/Email alerts | [ALERTING_SETUP.md](docs/ALERTING_SETUP.md) | Full deployment and testing guide |
| **Next bots to build** | [THETA_PROFITS_STRATEGY_ANALYSIS.md](docs/THETA_PROFITS_STRATEGY_ANALYSIS.md) | Top 3: MEIC, METF, SPX Put Credit |
| **Capital allocation** | [PORTFOLIO_ALLOCATION_ANALYSIS.md](docs/PORTFOLIO_ALLOCATION_ANALYSIS.md) | $50K optimal split: MEIC + Delta Neutral |
| **Multi-bot same underlying** | [MULTI_BOT_POSITION_MANAGEMENT.md](docs/MULTI_BOT_POSITION_MANAGEMENT.md) | Position Registry design for SPX multi-bot |

### Full Documentation List

| Document | Purpose |
|----------|---------|
| `docs/SAXO_API_PATTERNS.md` | **START HERE for Saxo API issues** - Proven patterns for orders, fills, prices |
| `docs/THETA_PROFITS_STRATEGY_ANALYSIS.md` | **20 strategies analyzed** - Next bots to implement (MEIC, METF, etc.) |
| `docs/PORTFOLIO_ALLOCATION_ANALYSIS.md` | **Capital allocation** - $50K optimal split across bots |
| `docs/MULTI_BOT_POSITION_MANAGEMENT.md` | **Position Registry** - Running multiple bots on same underlying |
| `docs/DELTA_NEUTRAL_STRATEGY_SPECIFICATION.md` | **Delta Neutral strategy** - Full Brian Terry spec with roll mechanics |
| `docs/IRON_FLY_STRATEGY_SPECIFICATION.md` | **Iron Fly strategy** - Doc Severson + Jim Olson rules, wing width calculation |
| `docs/IRON_FLY_CODE_AUDIT.md` | Comprehensive code audit with post-deployment fixes |
| `docs/IRON_FLY_EDGE_CASES.md` | 64 edge cases analyzed for Iron Fly bot |
| `docs/MEIC_STRATEGY_SPECIFICATION.md` | **MEIC strategy** - Full Tammy Chambless MEIC implementation spec |
| `docs/MEIC_EDGE_CASES.md` | 75 edge cases analyzed for MEIC bot |
| `docs/DELTA_NEUTRAL_EDGE_CASES.md` | **55 edge cases** for Delta Neutral bot (updated 2026-01-28) |
| `docs/ROLLING_PUT_DIAGONAL_EDGE_CASES.md` | Edge cases for Rolling Put Diagonal bot |
| `docs/ALERTING_SETUP.md` | SMS/Email alert system deployment guide |
| `docs/DEPLOYMENT.md` | Deployment procedures |
| `docs/GOOGLE_SHEETS.md` | Google Sheets logging setup |
| `docs/VM_COMMANDS.md` | VM administration commands |
| `.claude/settings.local.json` | Full command reference (also readable)

### Key Lessons Learned (Updated 2026-02-04)

These mistakes cost real money and debugging time. **READ BEFORE MAKING CHANGES:**

1. **P&L Must Use Actual Fill Prices** - Never use quoted bid/ask for P&L calculation. Extract `FilledPrice` from activities/order response. (Cost: ~$20 P&L error per trade)

2. **"Unknown" Order Status = Usually Filled** - Market orders fill instantly and disappear from /orders/. Check activities endpoint immediately. (Cost: Hours of debugging "stuck" orders)

3. **VIX Needs PriceInfoDetails** - VIX is an index with no bid/ask. Must include `"PriceInfoDetails"` in WebSocket FieldGroups. (Cost: Unnecessary Yahoo Finance fallbacks)

4. **Config Options Need Code** - Just because a config exists doesn't mean it's implemented! Verify code actually uses the config. (Cost: Bad trade entry)

5. **Clear Python Cache After Deploy** - `__pycache__` can persist old code. Always clear after git pull. (Cost: Hours debugging "fixed" code that wasn't running)

6. **Saxo WebSocket Uses Binary Frames, Not JSON Text** - See WebSocket Binary Parsing section below. Previous code tried `json.loads(message.decode('utf-8'))` which silently failed. (Cost: Stale cached prices, unnecessary REST API calls)

7. **Daily Summary Only at Market Close, Not Calendar Day Reset** - Calendar days change at midnight UTC (7 PM EST), but trading days end at 4 PM EST. Never send daily summaries from `reset_for_new_day()` - only from main.py after-hours check. (Cost: Duplicate alert spam, user confusion)

8. **WebSocket Streaming Updates Use ref_id Format** - Initial snapshot wraps data in `{"Data": [{"Uic": 123, ...}]}` but streaming updates use `{"Quote": {...}}` with UIC in ref_id as `ref_<UIC>`. Must handle both formats in `_handle_streaming_message()`. (Cost: SPY/VIX prices stuck at stale values, fixed 2026-01-27)

9. **WebSocket Cache Must Be Invalidated on Disconnect (Fix #1, 2026-01-28)** - When WebSocket disconnects, always clear `_price_cache`. Without this, bot uses stale cached data after reconnection. (Cost: 2026-01-27 order failures with DATA-004 errors)

10. **Cache Needs Timestamps for Staleness Detection (Fix #2, 2026-01-28)** - Each cache entry now stores `{'data': {...}, 'timestamp': datetime}`. `get_quote()` rejects cached data older than 60 seconds and forces REST API fallback. (Cost: Using outdated prices for order placement)

11. **Limit Order Price $0 Bug (Fix #3, 2026-01-28)** - The check `if order_type == OrderType.LIMIT and limit_price:` evaluated False when `limit_price=0.0`. Changed to `if limit_price is None or limit_price <= 0`. (Cost: "OrderPrice must be set" errors on 2026-01-27)

12. **Never Use $0.00 Fallback Price (Fix #4, 2026-01-28)** - In Delta Neutral strategy, if quote is invalid AND `leg_price` is $0, skip to next retry instead of placing order at $0.00. (Cost: 2026-01-27 order rejections)

13. **WebSocket Thread Health Monitoring (Fix #5, 2026-01-28)** - Added `is_websocket_healthy()` that checks: thread alive, last message < 60s ago, last heartbeat < 60s ago. `get_quote()` now forces REST fallback if WebSocket unhealthy. (Cost: Using stale cache when WebSocket silently died)

14. **Heartbeat Timeout Detection (Fix #6, 2026-01-28)** - Track `_last_heartbeat_time` on every heartbeat. If no heartbeat in 60+ seconds, connection is zombie. Saxo sends heartbeats every ~15 seconds. (Cost: Zombie connections going undetected)

15. **VIX NoAccess Requires Session Capability Recovery (Fix #11, 2026-01-29)** - When another Saxo session (SaxoTraderGO, Token Keeper) connects with `FullTradingAndChat`, the bot's session gets downgraded and VIX returns `NoAccess` (CBOE data requires premium capabilities). Solution: Detect `PriceTypeAsk: NoAccess` in REST response, auto-upgrade session via `PATCH /root/v1/sessions/capabilities`, retry VIX request. Yahoo Finance fallback works as safety net. (Cost: Unnecessary Yahoo fallbacks, potential stale VIX data)

16. **Strike Selection Must Use Fresh Quotes, Not Cached (Fix #12, 2026-01-30)** - The strike selection code was using cached prices to decide if a multiplier met the target return. If cached return < target, it never fetched fresh quotes (which might be higher). Solution: Two-phase scan that always fetches fresh quotes before making decisions. (Cost: Bot selected 1.35x strikes instead of 1.5x+ on 2026-01-29, reducing cushion/safety)

17. **Dynamic Strike Range, Not Hardcoded (Fix #13, 2026-01-30)** - Strike fetching used hardcoded ±20 points, but at 2.0x expected move with EM=$12.37, the required range is $24.74. Solution: `max_range = expected_move * max_mult * 1.2` dynamically scales with market conditions. (Cost: Strikes at 1.6x-2.0x were never even fetched from the API)

18. **Activities Endpoint Uses "FilledPrice", Not "Price" (Fix #14, 2026-01-31)** - The `check_order_filled_by_activity()` function was extracting `activity.get("Price", 0)` but Saxo's activities endpoint returns fill price as `"FilledPrice"`. This caused all fills to return price=0, falling back to quoted prices for P&L. (Cost: Iron Fly P&L showed +$160 instead of actual +$10 on 2026-01-30)

19. **Order Fill Verification Timeout Too Long (Fix #15, 2026-01-31)** - Market orders fill in ~3 seconds but `_verify_order_fill()` had 30-second timeout. When order not found in /orders/ (because it filled and was removed), code polled for 30s before checking activities. Solution: Reduced timeout to 10s, check activities after 3 consecutive "not found", break early. (Cost: 30 seconds per leg = 2+ minutes wasted on entry)

20. **Profit Target Should Be Dynamic, Not Fixed (Fix #16, 2026-01-31)** - Fixed $75 profit target doesn't scale with credit received. A $25 credit with $75 target requires 300% return - unrealistic. Solution: Added `profit_target_percent` config (default 30% of credit) with `profit_target_min` floor. For $25 credit at 30%, target = $7.50. (Cost: Profit targets never hit, always TIME_EXIT)

21. **Commission Must Be Tracked for Accurate P&L (Fix #17, 2026-02-01)** - Iron Fly has 4 legs × $5 round-trip = $20 commission per trade. Bot was showing gross P&L only. Solution: Added `commission_per_leg` config, `_calculate_total_commission()`, `_calculate_net_pnl()` methods. Profit targets now add commission to ensure net profit. Logs/alerts show both gross and net P&L. (Cost: Jan 30 trade showed +$30 gross but actual net was +$10)

22. **Activities Endpoint May Have Sync Delay (Fix #18, 2026-02-01)** - When order fills, activities endpoint may not immediately have fill data. Previous code returned "assumed_filled" with no price, falling back to quoted prices. Solution: Retry up to 3 times with 1s delay to get fill price from activities before giving up. Log warning if fallback to quotes. (Cost: P&L accuracy depends on getting actual fill prices)

23. **Activities FilledPrice Sync Takes 5-10 Seconds (Fix #19, 2026-02-02)** - Friday 2026-01-30 Iron Fly trade showed entry credit of $24.80 (quoted) vs $23.50 (actual) - a $1.30 error causing wrong P&L display during the trade. Root cause: `check_order_filled_by_activity()` had 3 retries × 1s = 3s total, but Saxo's FilledPrice field may take 5-10s to populate. Bot returned `fill_price=0` and fell back to quoted bid/ask prices. Solution: Increased to 4 retries × 1.5s = 6s in client (Iron Fly has its own 3-attempt loop on top = ~18s worst case). Also added position lookup for `PositionBase.OpenPrice` as secondary fill price source (NOTE: Originally documented as AverageOpenPrice but that was wrong - see Fix #21), and fixed `place_limit_order_with_timeout()` to handle `fill_price=0` by falling back to limit price. (Cost: Friday showed $1.60 P&L during trade, actual was $10 net)

24. **Option Expiration Selection Must Prefer Exact DTE (Fix #20, 2026-02-02)** - Monday 2026-02-02 Iron Fly bot used 1 DTE options (expiring Tuesday 2026-02-03) instead of 0 DTE (expiring same day). Root cause: `get_iron_fly_options()` and `get_expected_move_from_straddle()` used `target_dte_min=0, target_dte_max=1` and took the FIRST expiration matching this range. Saxo's option chain API returned expirations in an order where 1 DTE came before 0 DTE. Solution: Modified both functions to explicitly prefer exact 0 DTE, only falling back to 1 DTE if no 0 DTE exists. (Cost: Reduced theta decay benefit - 1 DTE options have less time decay than 0 DTE, hurting Iron Fly profitability)

25. **Short Position Fill Price in PositionBase.OpenPrice, Not PositionView.AverageOpenPrice (Fix #21, 2026-02-02)** - Iron Fly bot showed -$22 P&L but actual Saxo P&L was -$150. Root cause: When activities endpoint returned `FilledPrice=0`, code fell back to position lookup but used `PositionView.AverageOpenPrice` which is NOT populated for short (negative amount) positions. Investigation of Saxo position data revealed that `PositionBase.OpenPrice` contains the actual fill price for BOTH long and short positions. The code was using quoted bid/ask prices as fallback instead of actual fills. Solution: Changed position lookup to use `PositionBase.OpenPrice` instead of `PositionView.AverageOpenPrice`. (Cost: $128 P&L error on 2026-02-02 trade - showed -$22 instead of -$150)

26. **Profit Target Must Not Exceed Max Possible Profit (Fix #22, 2026-02-02)** - Iron Fly profit target calculation had a bug where `$25 floor + $20 commission = $45 gross target`, but if credit received was only $30, the maximum possible profit is $30 (100% of credit). Asking for $45 from a $30 credit is impossible - the target would never be reached. Solution: Cap the profit target at the credit received. If calculated target exceeds credit, log a warning and use credit as the target. This ensures the profit target is always achievable. (Cost: Profit targets never hit when credit was small, always fell through to TIME_EXIT)

27. **DELETE Endpoint Returns 404 for SPX Options (Fix #23, 2026-02-03)** - MEIC bot stop losses failed silently because `DELETE /trade/v2/positions/{position_id}` returns 404 "File or directory not found" for StockIndexOption (SPX) positions. The bot marked positions as "stopped" but they remained open, causing ~$1,000 unrealized loss. Solution: Use `place_emergency_order()` with `to_open_close="ToClose"` instead of the DELETE endpoint. This is the same pattern used by Iron Fly and Delta Neutral bots. (Cost: 2026-02-03 MEIC stop losses failed, positions remained open until fix deployed)

28. **SaxoClient.__init__ Can Corrupt Token Cache with Stale Config Tokens (Fix #24, 2026-02-03)** - When `SaxoClient` is instantiated, it was calling `token_coordinator.update_cache()` with whatever tokens were loaded from the config file - even if those tokens were older than tokens already in the cache (maintained fresh by Token Keeper). This meant running a diagnostic script that loaded stale config data would overwrite the fresh tokens, causing authentication failures. Root cause: 2026-02-03 at 4:01 PM EST, a diagnostic script loaded Jan 13 expiry tokens from config and wrote them to cache, corrupting fresh tokens. Token Keeper then failed all refresh attempts with 401. Solution: Before updating cache in `__init__`, compare `token_expiry` timestamps - only update if config tokens are NEWER than cached tokens. (Cost: Complete auth failure requiring manual re-authentication, potential missed trades during market hours)

29. **Margin Settlement Delay Needed Between Close and Enter (Fix #25, 2026-02-03)** - Delta Neutral recenter failed at 2:01 PM EST because after closing the long straddle ($10,180 received), the bot immediately tried to enter new longs (~$10,534). Saxo rejected with `WouldExceedMargin` because the cash from the close wasn't immediately available for margin. Solution: Add 3-second delay after closing positions before entering new positions. Applied to: execute_recenter, roll_weekly_shorts, all partial straddle recovery paths. (Cost: Recenter failed, left bot with shorts-only exposure for remainder of session)

30. **Retry Attempts Need Delay to Avoid 409 Conflicts (Fix #26, 2026-02-03)** - Same recenter failure showed rapid 409 Conflict errors when the bot retried placing orders immediately after previous cancellation. Saxo API hadn't fully processed the cancellation. Solution: Add 1.5-second delay between retry attempts in `_place_protected_multi_leg_order`. (Cost: Multiple rapid rejections consuming all 7 retry attempts without meaningful progress)

31. **Quote Fetch Must Retry on Invalid (Fix #27, 2026-02-03)** - During recenter, fresh quotes returned `Bid=0, Ask=0` immediately after closing positions. The code fell back to stale `leg_price` from original order creation, causing `PriceExceedsAggressiveTolerance` errors when the stale price was far from current market. Solution: Retry quote fetch up to 3 times with 1.5s delay if invalid (Bid=0/Ask=0). Only use leg_price as last resort with warning about staleness. (Cost: Orders placed at stale prices rejected by exchange)

32. **Floating Point Tick Size Rounding (Fix #28, 2026-02-04)** - MEIC orders rejected with `PriceNotInTickSizeIncrements` because `round(2.55 / 0.05) * 0.05 = 2.5500000000000003` due to floating point arithmetic. Saxo API is strict about tick sizes and won't accept prices with floating point artifacts. Solution: Add final `round(result, 2)` after tick size calculation in `round_to_spx_tick()`. (Cost: Multiple entry failures for MEIC Entry #2 on 2026-02-03)

33. **Clear UICs When Positions Settle/Missing (Fix #29, 2026-02-04)** - After options settle or positions are marked as missing, the code only cleared `position_id` but not `uic`. This caused `_update_entry_prices()` to try fetching prices for expired options (UICs still set), generating 40+ `IllegalInstrumentId` (404) errors. Solution: Clear BOTH `position_id` AND `uic` when positions settle in `check_after_hours_settlement()` and when positions are marked missing in `_reconcile_positions()`. (Cost: 2026-02-03 MEIC got stuck in error loop trying to fetch prices for expired options)

34. **Skip Orphan Cleanup in Dry-Run Mode (Fix #30, 2026-02-04)** - Dry-run positions use synthetic IDs like "DRY_xxx" that don't exist in Saxo. When `cleanup_orphans()` was called with real Saxo positions (empty set in dry-run), all DRY_ positions were incorrectly removed as "orphans", causing `STATE-002: Position count mismatch: expected 4, registry has 0` errors. Solution: Skip `cleanup_orphans()` calls when `self.dry_run` is True. (Cost: 2026-02-03 MEIC repeatedly failed recovery, generating 30+ STATE-002 errors)

35. **Zero/Low Credit Causes Immediate False Stop Trigger (Fix #31, STOP-007, 2026-02-04)** - If `_get_fill_price()` returns 0 due to API sync issues, credit becomes 0, making stop_level = 0. With the check `spread_value >= stop_level` and `stop_level = 0`, the condition is always true, causing immediate false stop triggers on every monitoring cycle. Solution: Add MIN_STOP_LEVEL = $50 safety floor in `_calculate_stop_levels()`, skip stop check in `_check_stop_losses()` if levels < $50, and apply same protection in `_recover_entry_from_positions()`. Defense-in-depth with CRITICAL logging for investigation. (Cost: Would have caused all MEIC positions to be stopped immediately on any fill price sync failure)

36. **P&L Double-Counting in Stop Loss Tracking (Fix #32, 2026-02-04)** - MEIC stop losses were recording gross cost-to-close instead of net loss, overstating losses by the credit amount. Example: Entry collects $250 credit, stop_level = $250. Old code: `realized_pnl -= $250`. Actual loss = `stop_level - credit_for_side = $250 - $125 = $125`. The bug affected `total_realized_pnl`, `entry.unrealized_pnl`, `_calculate_side_pnl()`, and alert messages. Solution: All P&L tracking now uses `net_loss = stop_level - credit_for_that_side`. (Cost: P&L displays were overstated by ~50% on stop days)
