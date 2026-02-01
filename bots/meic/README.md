# MEIC (Multiple Entry Iron Condors) Trading Bot

**Last Updated:** 2026-02-01
**Strategy Creator:** Tammy Chambless ("Queen of 0DTE")
**Status:** IMPLEMENTED - Production Ready (v1.1.0)

---

## Overview

MEIC implements Tammy Chambless's 0DTE SPX iron condor strategy, featuring **6 scheduled entries throughout the trading day** to average entry prices and reduce single-point timing risk.

### Key Performance Metrics (Tammy Chambless, Jan 2023 - present)

| Metric | Value |
|--------|-------|
| CAGR | 20.7% |
| Max Drawdown | 4.31% |
| Calmar Ratio | 4.8 |
| Win Rate | ~70% |

---

## Strategy Structure

### Entry Times (Eastern Time)
| Entry | Time | Purpose |
|-------|------|---------|
| 1 | 10:00 AM | After opening range |
| 2 | 10:30 AM | Mid-morning |
| 3 | 11:00 AM | Pre-lunch |
| 4 | 11:30 AM | Early afternoon |
| 5 | 12:00 PM | Midday |
| 6 | 12:30 PM | Final entry |

### Iron Condor Structure
Each entry places a complete iron condor:
- **Call Spread:** Sell OTM call, Buy higher strike call (50-60pt wide)
- **Put Spread:** Sell OTM put, Buy lower strike put (50-60pt wide)
- **Target Delta:** 5-15 delta (OTM strikes)
- **Credit Target:** $1.00-$1.75 per side

### Stop Loss Rules
- **Per-side stop:** Total credit received per side
- **MEIC+ Modification:** Stop = credit - $0.10 (for small wins on stop days)
- **Breakeven Design:** If stopped, loss ≈ credit = breakeven

---

## Files

| File | Purpose |
|------|---------|
| `strategy.py` | Main strategy logic with state machine |
| `main.py` | Entry point and trading loop |
| `models/` | Standalone data model definitions |
| `config/config.json` | Configuration (gitignored) |

### Models Package
The `models/` directory contains standalone definitions that can be imported without loading the full strategy module:
- `MEICState` - Strategy state machine enum
- `IronCondorEntry` - Single IC position with 4 legs
- `MEICDailyState` - Daily trading state
- `MarketData` - Market data tracking with flash crash detection

---

## Safety Features

### Core Safety (v1.0.0)
1. **Circuit Breaker** - Halts on 5 consecutive failures or 5-of-10 sliding window
2. **Naked Short Detection** - Immediate close if short fills without hedge
3. **Position Registry** - Isolates MEIC positions from other bots (Iron Fly)
4. **Per-entry Stops** - Independent stop monitoring for each IC
5. **Safety Event Logging** - Audit trail in Google Sheets

### Enhanced Safety (v1.1.0)
| Feature | Code | Description |
|---------|------|-------------|
| **Order Size Validation** | ORDER-006 | Max 10 contracts/order, 30 total |
| **Emergency Close Retries** | EMERGENCY-001 | 5 attempts with spread validation |
| **Fill Slippage Monitoring** | ORDER-007 | Alerts at 5% warn, 15% critical |
| **Activities Retry Logic** | ACTIVITIES-001 | 3 attempts × 1s for fill prices |
| **Duplicate Bot Prevention** | DUPLICATE-001 | Kills existing instances on startup |
| **Config Validation** | CONFIG-001 | Validates config on startup |
| **P&L Sanity Check** | PNL-001 | Alerts on unrealistic P&L values |
| **Quote Freshness Warnings** | DATA-001 | Logs when quotes > 30s old |

### REST-Only Mode (v1.1.0)
MEIC uses **REST API only** for all price fetching (no WebSocket streaming). This provides:
- Guaranteed fresh quotes for every check
- Simpler code with fewer failure modes
- More reliable than WebSocket which had stale cache issues

### Position Recovery
MEIC uses **Saxo API as the single source of truth** for position recovery:
- On startup, queries Saxo for all positions
- Uses Position Registry to identify MEIC positions
- Reconstructs IronCondorEntry objects from live data
- Handles positions closed manually on Saxo platform

### Entry Safety Order
Legs are placed in safe order (longs before shorts):
1. Long Call (buy protection first)
2. Long Put (buy protection)
3. Short Call (now hedged)
4. Short Put (now hedged)

---

## Configuration

```json
{
    "dry_run": true,
    "underlying_uic": 4913,
    "option_root_uic": 128,
    "vix_uic": 10606,
    "entry_times": ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30"],
    "spread_width": 50,
    "contracts_per_entry": 1,
    "max_vix_entry": 25,
    "max_daily_loss_percent": 3.0,
    "meic_plus_enabled": true,
    "meic_plus_reduction": 0.10,
    "alerts": {
        "enabled": true,
        "phone_number": "+1XXXXXXXXXX",
        "email": "your@email.com"
    }
}
```

---

## Commands

### Start/Stop
```bash
# Start
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start meic"

# Stop
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop meic"

# Restart
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart meic"
```

### View Logs
```bash
# Recent logs
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u meic -n 50 --no-pager"

# Follow live
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u meic -f"
```

---

## Documentation

- Full Strategy Spec: [docs/MEIC_STRATEGY_SPECIFICATION.md](../../docs/MEIC_STRATEGY_SPECIFICATION.md)
- Edge Cases (75): [docs/MEIC_EDGE_CASES.md](../../docs/MEIC_EDGE_CASES.md)

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-01 | 1.1.0 | REST-only mode, 8 new safety features (ORDER-006, ORDER-007, EMERGENCY-001, etc.) |
| 2026-02-01 | 1.1.0 | Full code audit: fixed undefined method calls, removed dead code |
| 2026-01-27 | 1.0.0 | Initial implementation with full strategy logic |
| 2026-01-27 | 1.0.0 | Added position recovery from Saxo API (critical fix) |
| 2026-01-27 | 1.0.0 | Added safety event logging, dashboard metrics, pre-market gap detection |
