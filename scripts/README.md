# CALYPSO Scripts

Utility scripts for analysis, testing, and diagnostics. All scripts are run from the repository root.

## Quick Reference: Which Script to Use?

| If you want to... | Run this |
|-------------------|----------|
| See what Delta Neutral bot would do NOW | `python scripts/preview_live_entry.py` |
| See what Iron Fly bot would do NOW | `python scripts/preview_iron_fly_entry.py` |
| Deep analysis with historical research | `python scripts/optimal_strike_analysis.py` |
| Check if API is working before trading | `python scripts/test_rest_api.py` |
| Quick NET return calculation | `python scripts/calculate_net_return.py` |

---

## Iron Fly Strategy Scripts

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `preview_iron_fly_entry.py` | Shows what Iron Fly bot would do now: VIX check, 0 DTE expiry, expected move, wing width, pricing | **PRIMARY** - Run daily to verify Iron Fly logic |

**Key checks in preview_iron_fly_entry.py:**
1. VIX filter (must be < 20)
2. 0 DTE vs 1 DTE expiration verification
3. Expected move from ATM straddle
4. Wing width (Jim Olson 40pt minimum rule)
5. Complete Iron Fly structure and pricing
6. P&L projections with commission

---

## Delta Neutral Strategy Scripts

### Daily Use

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `preview_live_entry.py` | Shows exactly what the bot would do if it entered now | **PRIMARY** - Run daily to verify bot logic |
| `check_short_strikes.py` | Shows strike prices for short strangle | Quick strike check |

### Analysis & Research

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `optimal_strike_analysis.py` | Comprehensive 740-line analysis using historical research + live prices | Deep strategy analysis, understanding risk/reward |
| `weekly_projection.py` | Compare multiple multipliers side-by-side | Comparing strike options |
| `find_optimal_strikes.py` | Analyze asymmetric adjustment (put skew) | Understanding put/call distance asymmetry |
| `find_optimal_mult.py` | Scan all multipliers 0.5x to 2.0x | Finding optimal symmetric strikes |
| `calculate_1pct_target.py` | Calculate strikes for exact 1% NET return | Matches actual bot logic |
| `calculate_net_return.py` | Quick NET return for a given premium | Simple sanity checks |

---

## API & WebSocket Testing

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `test_rest_api.py` | Test all REST API calls (SPY, VIX, options, Greeks, positions) | **Pre-flight check before trading** |
| `test_websocket_fixes.py` | Unit tests for WebSocket/quote fixes (2026-01-28) | After code changes to WebSocket |
| `test_websocket_live.py` | Integration tests against live Saxo API | Verify WebSocket in live environment |

---

## Instrument Search (`scripts/search/`)

| Script | Purpose |
|--------|---------|
| `find_vix.py` | Search for VIX instruments across multiple asset types |
| `find_spy_uic.py` | Find correct SPY UIC for your account |

---

## VM Status Script

### `bot_status.sh`
Quick status overview of all bots on the VM.

```bash
# On VM:
/opt/calypso/scripts/bot_status.sh
```

Shows:
- Service status (running/stopped) for each bot
- Memory usage
- Last log entry from each bot

---

## Running Scripts

All scripts should be run from the repository root:

```bash
# From repo root
cd /Users/ddias/Desktop/CALYPSO/Git\ Repo

# Run a script
python scripts/preview_live_entry.py
python scripts/test_rest_api.py
```

### On the VM

```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/preview_live_entry.py'"
```

---

## Script Details

### `preview_live_entry.py`
The most useful daily script. Shows:
- Current SPY price and VIX
- What strikes the bot would select
- Expected premium and NET return
- Whether entry conditions are met

### `optimal_strike_analysis.py`
Comprehensive analysis including:
- Historical research on IV vs RV
- Win rates from Tastytrade/Spintwig backtests
- Expected value calculations at different multipliers
- Comparison of bot's approach vs theoretical optimal

### `test_rest_api.py`
Tests 10 API endpoints:
1. SPY quote
2. VIX quote
3. Option expirations
4. Option chain
5. Option Greeks
6. Expected move calculation
7. Find strangle options
8. Account info
9. Positions
10. Orders

Run before each trading day to verify connectivity.

---

## Formal Test Suite

The proper pytest test suite is in `/tests/`:

```bash
# Run all tests
pytest tests/

# Run with verbose output
pytest tests/ -v

# Run specific test file
pytest tests/test_position_registry.py -v
```

---

**Last Updated:** 2026-02-02
