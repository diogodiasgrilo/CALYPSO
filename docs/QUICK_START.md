# Quick Start Guide

## Your Questions Answered

### ✅ "Is `--dry-run` like read-only mode?"

**YES!** When you use `--live --dry-run`:
- Fetches real market data from Saxo
- Runs full strategy logic
- **Does NOT execute any trades**
- Perfect for testing without risk

```bash
python main.py --live --dry-run
```

---

### ✅ "Can I use a sub-account to test with small amounts?"

**YES!** Use the `--account` flag:

1. **First, list all your accounts**:
   ```bash
   python main.py --live --list-accounts
   ```

2. **Use your sub-account**:
   ```bash
   python main.py --live --account YOUR_SUB_ACCOUNT_KEY
   ```

3. **Test with sub-account (no actual trades)**:
   ```bash
   python main.py --live --dry-run --account YOUR_SUB_ACCOUNT_KEY
   ```

**Perfect for**: Testing with a small funded sub-account before using your main account!

---

## Common Use Cases

### 1. Test Strategy with Live Data (No Trading)

```bash
python main.py --live --dry-run
```

**What happens**:
- ✅ Connects to your live account
- ✅ Gets real SPY/VIX prices
- ✅ Fetches real options chains
- ✅ Runs strategy logic
- ❌ Does NOT place any orders

---

### 2. List All Your Accounts

```bash
python main.py --live --list-accounts
```

**Shows**:
- All accounts you have access to
- Account balances
- Account types
- Which is currently configured

---

### 3. Use a Specific Sub-Account

```bash
# Test with sub-account (read-only)
python main.py --live --dry-run --account <SUB_ACCOUNT_KEY>

# Actually trade with sub-account (REAL MONEY)
python main.py --live --account <SUB_ACCOUNT_KEY>
```

---

### 4. Paper Trading in SIM (Current Setup)

```bash
# Default - uses SIM environment
python main.py --dry-run
```

**What happens**:
- Uses Yahoo Finance for prices (15-min delayed)
- Options chains return 404 (SIM limitation)
- Good for testing logic, but can't test full strategy

---

## Recommended Workflow

### Phase 1: Test with Live Data (Safe)

```bash
python main.py --live --dry-run
```

**Why**: Verifies your strategy works with real options data, no risk.

---

### Phase 2: Small Sub-Account Test (Real Money)

1. Create a sub-account in Saxo with $500-$1000
2. Run:
   ```bash
   python main.py --live --account <SUB_ACCOUNT_KEY>
   ```

**Why**: Tests with real money but limited risk.

---

### Phase 3: Full Deployment

```bash
python main.py --live
```

**Why**: Uses main account for full strategy.

---

## Safety Checklist

Before running with real money:

- [ ] Tested with `--live --dry-run` ✓
- [ ] Verified options chains load correctly ✓
- [ ] Checked strategy logic with real prices ✓
- [ ] Set position_size to 1 contract initially
- [ ] Have stop-loss understanding
- [ ] Monitored for at least 1 full trading day

---

## Quick Reference

| Command | Environment | Executes Trades? | Use Case |
|---------|-------------|------------------|----------|
| `python main.py` | SIM | No | Basic testing |
| `python main.py --dry-run` | SIM | No | Strategy testing (no options) |
| `python main.py --live --dry-run` | LIVE | **No** | Test with real data |
| `python main.py --live --dry-run --account <KEY>` | LIVE | **No** | Test with sub-account |
| `python main.py --live --account <KEY>` | LIVE | **YES** | Real trading (sub-account) |
| `python main.py --live` | LIVE | **YES** | Real trading (main account) |

---

## Your Current Account

From the output you just got:

```
Account Key: HHzaFvDVAVCg3hi3QUvbNg==
Balance: $55,578.88 USD
```

This is your dad's live account. If you want to:

**Test safely**:
```bash
python main.py --live --dry-run --account HHzaFvDVAVCg3hi3QUvbNg==
```

**Trade for real** (be careful!):
```bash
python main.py --live --account HHzaFvDVAVCg3hi3QUvbNg==
```

---

## Need Help?

- Full environment guide: [ENVIRONMENT_SWITCHING.md](ENVIRONMENT_SWITCHING.md)
- General README: [README.md](README.md)
