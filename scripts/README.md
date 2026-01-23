# Utility Scripts

Helper scripts for development, testing, and VM management.

---

## VM Status Script

### `bot_status.sh`
Quick status overview of all 3 bots on the VM.

```bash
# On VM:
/opt/calypso/scripts/bot_status.sh
```

Shows:
- Service status (running/stopped) for each bot
- Memory usage
- Last log entry from each bot
- Recent entries from monitor log

---

## Search Scripts (`scripts/search/`)

Tools for finding UICs (Unique Instrument Codes) in the Saxo API:

| Script | Purpose |
|--------|---------|
| `find_spy_uic.py` | Find UIC for SPY ETF |
| `find_vix.py` | Find UIC for VIX index |
| `find_strategy_uics.py` | Find all required UICs |
| `search_instruments.py` | General instrument search |

### Usage
```bash
python scripts/search/find_spy_uic.py
python scripts/search/search_instruments.py --query "QQQ"
```

---

## Test Scripts (`scripts/tests/`)

Scripts for testing API connectivity:

| Script | Purpose |
|--------|---------|
| `test_spy_price.py` | Test SPY price fetching |
| `test_spy_quote.py` | Test SPY quote retrieval |
| `test_live_with_external_feed.py` | Test Yahoo Finance fallback |

### Usage
```bash
python scripts/tests/test_spy_price.py
```

---

## Notes

- Run all scripts from the project root directory
- These are for development/testing only, not during live trading
- Search scripts require valid Saxo API credentials in config

---

**Last Updated:** 2026-01-23
