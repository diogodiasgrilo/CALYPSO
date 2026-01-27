# CALYPSO Trading Bot Infrastructure

## CRITICAL: Bot Control Warning

**NEVER use `kill` or `pkill` to stop bots!** All services have `Restart=always` with `RestartSec=30`. Killing a bot will cause it to auto-restart in 30 seconds. **ALWAYS use `systemctl stop`** to properly stop a bot.

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

cloud_functions/      # Google Cloud Functions
  alert_processor/    # Processes alerts from Pub/Sub, sends SMS/Email
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
| MEIC | `meic.service` | Tammy Chambless's MEIC (Multiple Entry Iron Condors) | `bots/meic/config/config.json` | NEW |

All bots have: `Restart=always`, `RestartSec=30`, `StartLimitInterval=600`, `StartLimitBurst=5`

### Iron Fly Bot Details
- **Entry:** 10:00 AM EST (after 30-min opening range)
- **Exit:** Wing touch (stop loss) or $75 profit target
- **Max hold:** 60 minutes (11:00 AM rule)
- **Filters:** VIX < 20, no FOMC days, price in opening range
- **Edge cases:** 63 analyzed, all resolved (see `docs/IRON_FLY_EDGE_CASES.md`)
- **Code audit:** Comprehensive review completed (see `docs/IRON_FLY_CODE_AUDIT.md`)

#### Iron Fly Safety Features
- **Entry order:** Longs first (Long Call → Long Put → Short Call → Short Put) - safer on partial fills
- **Entry retries:** 3 attempts with 15-second delays; auto-unwind filled legs on failure
- **Stop losses:** Software-based via 2-second polling (NOT broker-side stops)
- **Wing breach tolerance:** $0.10 buffer to avoid floating-point issues
- **Circuit breaker:** 5 consecutive failures or 5-of-10 sliding window triggers halt

#### Iron Fly Typical P&L (1 contract, ~30pt wings)
| Scenario | P&L | Notes |
|----------|-----|-------|
| Max profit (expires at ATM) | ~$1,500 | Rare - would hold to 4:00 PM |
| Profit target hit | +$75 | Target exit |
| Stop loss (wing touch) | -$250 to -$350 | Typical stop-out |
| Max loss (circuit breaker) | -$400 | Safety cap |

### MEIC Bot Details (NEW - 2026-01-27)
- **Strategy:** Tammy Chambless's MEIC (Multiple Entry Iron Condors) - "Queen of 0DTE"
- **Structure:** 6 scheduled iron condor entries per day
- **Entry times:** 10:00, 10:30, 11:00, 11:30, 12:00, 12:30 AM ET
- **Strikes:** 5-15 delta OTM, 50-60 point spreads
- **Stop loss:** Per-side stop = total credit received (breakeven design)
- **MEIC+ modification:** Stop = credit - $0.10 for small wins on stop days
- **Expected results:** 20.7% CAGR, 4.31% max drawdown, 4.8 Calmar ratio, ~70% win rate
- **Edge cases:** 75 analyzed pre-implementation (see `docs/MEIC_EDGE_CASES.md`)
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
- **Strategy:** Brian Terry's Delta Neutral (from Theta Profits)
- **Structure:** Long ATM straddle (90-120 DTE) + Weekly short strangles (5-12 DTE)
- **Long Entry:** 120 DTE target (configurable)
- **Long Exit:** 60 DTE threshold - close everything when longs reach this point
- **Shorts Roll:** Weekly (Thursday/Friday) to next week's expiry for continued premium collection
- **Recenter:** When SPY moves ±$5 from initial strike, rebalance long straddle strikes
- **Full specification:** See [DELTA_NEUTRAL_STRATEGY_SPECIFICATION.md](docs/DELTA_NEUTRAL_STRATEGY_SPECIFICATION.md)

#### Delta Neutral Key Logic (2026-01-23)

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

**Delta Neutral ITM Monitoring Alerts** (2026-01-26):
- VIGILANT_ENTERED (HIGH): Price enters 0.1%-0.3% danger zone near short strike
- VIGILANT_EXITED (LOW): Price moves back to safe zone (>0.3% from strikes)
- ITM_RISK_CLOSE (CRITICAL): Shorts emergency closed at 0.1% threshold
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

### Emergency Stop (All Bots)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop iron_fly_0dte delta_neutral rolling_put_diagonal meic"
```

### Stop Individual Bots
```bash
# Iron Fly
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop iron_fly_0dte"

# Delta Neutral
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop delta_neutral"

# Rolling Put Diagonal
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop rolling_put_diagonal"

# MEIC
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop meic"
```

### Start Bots
```bash
# All bots
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start iron_fly_0dte delta_neutral rolling_put_diagonal meic"

# Individual
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start iron_fly_0dte"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start delta_neutral"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start rolling_put_diagonal"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start meic"
```

### Restart Bots
```bash
# All bots
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart iron_fly_0dte delta_neutral rolling_put_diagonal meic"

# Individual
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart iron_fly_0dte"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart delta_neutral"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart rolling_put_diagonal"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart meic"
```

### Check Status
```bash
# All bots
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status iron_fly_0dte delta_neutral rolling_put_diagonal meic"

# List running services
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl list-units --type=service | grep -E '(iron|delta|rolling|meic)'"
```

### View Logs
```bash
# Recent logs (50 lines)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u iron_fly_0dte -n 50 --no-pager"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u delta_neutral -n 50 --no-pager"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u rolling_put_diagonal -n 50 --no-pager"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u meic -n 50 --no-pager"

# Follow logs (live)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u iron_fly_0dte -f"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u meic -f"

# Today's logs
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
7. **Iron Fly bot:** PAUSED (as of 2026-01-23)
8. **Delta Neutral bot:** Running in LIVE mode
9. **Rolling Put Diagonal bot:** Still in dry-run mode
10. **MEIC bot:** NEW - ready for dry-run testing
11. **FOMC Calendar:** Single source of truth in `shared/event_calendar.py` - ALL bots import from there (updated 2026-01-26)

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
| Edge case handling | [IRON_FLY_EDGE_CASES.md](docs/IRON_FLY_EDGE_CASES.md) | All 63 documented cases |
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
| `docs/IRON_FLY_CODE_AUDIT.md` | Comprehensive code audit with post-deployment fixes |
| `docs/IRON_FLY_EDGE_CASES.md` | 63 edge cases analyzed for Iron Fly bot |
| `docs/MEIC_STRATEGY_SPECIFICATION.md` | **MEIC strategy** - Full Tammy Chambless MEIC implementation spec |
| `docs/MEIC_EDGE_CASES.md` | 75 edge cases analyzed for MEIC bot |
| `docs/DELTA_NEUTRAL_EDGE_CASES.md` | Edge cases for Delta Neutral bot |
| `docs/ROLLING_PUT_DIAGONAL_EDGE_CASES.md` | Edge cases for Rolling Put Diagonal bot |
| `docs/ALERTING_SETUP.md` | SMS/Email alert system deployment guide |
| `docs/DEPLOYMENT.md` | Deployment procedures |
| `docs/GOOGLE_SHEETS.md` | Google Sheets logging setup |
| `docs/VM_COMMANDS.md` | VM administration commands |
| `.claude/settings.local.json` | Full command reference (also readable)

### Key Lessons Learned (2026-01-27)

These mistakes cost real money and debugging time. **READ BEFORE MAKING CHANGES:**

1. **P&L Must Use Actual Fill Prices** - Never use quoted bid/ask for P&L calculation. Extract `FilledPrice` from activities/order response. (Cost: ~$20 P&L error per trade)

2. **"Unknown" Order Status = Usually Filled** - Market orders fill instantly and disappear from /orders/. Check activities endpoint immediately. (Cost: Hours of debugging "stuck" orders)

3. **VIX Needs PriceInfoDetails** - VIX is an index with no bid/ask. Must include `"PriceInfoDetails"` in WebSocket FieldGroups. (Cost: Unnecessary Yahoo Finance fallbacks)

4. **Config Options Need Code** - Just because a config exists doesn't mean it's implemented! Verify code actually uses the config. (Cost: Bad trade entry)

5. **Clear Python Cache After Deploy** - `__pycache__` can persist old code. Always clear after git pull. (Cost: Hours debugging "fixed" code that wasn't running)

6. **Saxo WebSocket Uses Binary Frames, Not JSON Text** - See WebSocket Binary Parsing section below. Previous code tried `json.loads(message.decode('utf-8'))` which silently failed. (Cost: Stale cached prices, unnecessary REST API calls)

7. **Daily Summary Only at Market Close, Not Calendar Day Reset** - Calendar days change at midnight UTC (7 PM EST), but trading days end at 4 PM EST. Never send daily summaries from `reset_for_new_day()` - only from main.py after-hours check. (Cost: Duplicate alert spam, user confusion)

8. **WebSocket Streaming Updates Use ref_id Format** - Initial snapshot wraps data in `{"Data": [{"Uic": 123, ...}]}` but streaming updates use `{"Quote": {...}}` with UIC in ref_id as `ref_<UIC>`. Must handle both formats in `_handle_streaming_message()`. (Cost: SPY/VIX prices stuck at stale values, fixed 2026-01-27)
