# Saxo Market Data Subscriptions Analysis

**Date**: January 11, 2026
**Status**: ⚠️ MISSING CRITICAL SUBSCRIPTIONS
**Action Required**: YES

---

## Your Bot's Requirements

Your bot trades:
- **Underlying Asset**: SPY (S&P 500 ETF - trades on **NASDAQ/NYSE ARCA**)
- **Index for Signals**: VIX (Volatility Index - trades on **CBOE**)
- **Strategy**: Long straddle + short strangle on SPY options

---

## What You're Currently Subscribed To

### ✅ ACTIVE SUBSCRIPTIONS

| Exchange | Level | Type | Fee | Status |
|----------|-------|------|-----|--------|
| NASDAQ | Level 1 | Stock Exchange | $7 USD/mo | ✅ Active |
| S&P 500 Index | Level 1 | Stock Exchange | $7.5 USD/mo | ✅ Active |
| OPRA Data | Level 1 | Futures & Options | $7 USD/mo | ✅ Active |

### ❌ WHAT YOU'RE MISSING FOR SPY & VIX

| What You Need | Current Status | Cost | Impact |
|---------------|-----------------|------|--------|
| **NYSE Arca (for SPY)** | ❌ NOT SUBSCRIBED | $7-29 USD/mo | **Critical - Can't get real-time SPY prices** |
| **CBOE Indices (for VIX)** | ❌ NOT SUBSCRIBED | $7 USD/mo | **Critical - Can't get real-time VIX prices** |
| **NYSE Options** | ❌ NOT SUBSCRIBED | Part of OPRA* | **Already included in your OPRA subscription** |

---

## The Problem Explained

### Why Your Bot Gets "NoAccess" for Prices

Your subscriptions show:
- ✅ NASDAQ Level 1 (for some stocks)
- ✅ S&P 500 Index Level 1 (for some indices)
- ✅ OPRA Level 1 (for options data)

But **SPY doesn't trade on NASDAQ** — it trades on **NYSE Arca**.
And **VIX doesn't trade on regular CBOE** — it requires **CBOE Indices** subscription.

Result:
- When you query SPY: Saxo says "NoAccess" (not on your allowed exchanges)
- When you query VIX: Saxo says "NoAccess" (not on your allowed exchanges)

---

## What You Need To Do

### STEP 1: Subscribe to NYSE Arca (For SPY Prices)

**In Saxo Account Portal**:
1. Go to Market Data Subscriptions
2. Click on **Stock exchanges** tab
3. Find **"NYSE Arca"** in "Available subscriptions"
4. Select **Level 1** (Minimum for real-time prices)
5. Click **Subscribe**
6. Cost: $7 USD/month
7. Activation: Usually 1-5 minutes

**What it gives you**:
- Real-time best bid/ask for SPY
- Charts and last traded price
- Everything your bot needs for SPY prices

---

### STEP 2: Subscribe to CBOE Indices (For VIX Prices)

**In Saxo Account Portal**:
1. Go to Market Data Subscriptions
2. Click on **Stock exchanges** tab (same tab)
3. Find **"CBOE Indices"** in "Available subscriptions"
4. Select **Level 1** (Minimum for real-time prices)
5. Click **Subscribe**
6. Cost: $7 USD/month
7. Activation: Usually 1-5 minutes

**What it gives you**:
- Real-time best bid/ask for VIX
- Charts and last traded price
- Everything your bot needs for VIX prices

---

## Cost Breakdown

### Current Monthly Cost
```
NASDAQ Level 1:        $7.00
S&P 500 Index Level 1: $7.50
OPRA Level 1:          $7.00
                       ------
TOTAL:                $21.50/month
```

### After Adding NYSE Arca + CBOE Indices
```
NASDAQ Level 1:        $7.00
S&P 500 Index Level 1: $7.50
OPRA Level 1:          $7.00
NYSE Arca Level 1:     $7.00  ← ADD THIS
CBOE Indices Level 1:  $7.00  ← ADD THIS
                       ------
TOTAL:                $35.50/month
```

**Additional Cost**: Only $14/month for real-time SPY and VIX prices
**ROI**: Negligible for live trading (1 profitable trade covers it)

---

## Why Your Bot Works NOW (With Fallback)

Your current setup:
- ✅ OPRA subscription means you **CAN get options chain data**
- ✅ This is why OptionRootId, expirations, and strikes work fine
- ❌ But you **CAN'T get the underlying SPY/VIX prices** (NoAccess)

**Solution in place**: External price feeds (Yahoo Finance) automatically fill in:
- SPY: $694.07 (15-min delayed)
- VIX: $14.49 (15-min delayed)

This works for testing, but for live trading you want real-time.

---

## The Good News

### Your Subscriptions Are Actually Good for Options!

```
OPRA Data Level 1 means you have:
✅ Access to US options chains (all strikes, all expirations)
✅ Real-time options prices (bid/ask)
✅ Everything needed to place options trades
✅ All expirations visible (32 for SPY)
✅ All strikes visible (400+ per expiration)
```

This is why your options chain fetching works perfectly!

---

## Action Plan: What To Do

### Option A: Add Subscriptions (Recommended for Live Trading)

**Time**: 5 minutes
**Cost**: +$14/month
**Result**: Real-time prices for SPY and VIX

1. Log into Saxo account
2. Go to Account → Market Data Subscriptions
3. Subscribe to: NYSE Arca Level 1
4. Subscribe to: CBOE Indices Level 1
5. Restart bot
6. Prices should now show real values instead of NoAccess

**Code change needed**: None (auto-detects when subscriptions activate)

---

### Option B: Keep External Feeds (Current Setup)

**Time**: 0 minutes
**Cost**: $0
**Result**: Bot works with 15-min delayed prices

Current setup is fine for:
- ✅ Testing the strategy
- ✅ Paper trading
- ✅ Validating logic and entry/exit conditions
- ✅ Learning and development

---

### Option C: Hybrid (Recommended for Now)

**Time**: 5 minutes setup, run bot now
**Cost**: $0 now, +$14/month when ready to go live
**Result**: Test now, upgrade later

1. Run bot TODAY with external feeds (Yahoo Finance)
2. Test all entry/exit conditions
3. Validate P&L calculations
4. When ready for live trading: Add subscriptions
5. Switch external_price_feed to false in config

---

## The Bottom Line

| What You Need | Status | Action |
|---------------|--------|--------|
| **SPY Options Chain** | ✅ Working | None needed |
| **VIX Options Chain** | ✅ Working | None needed |
| **SPY Prices** | ❌ NoAccess | Add NYSE Arca ($7/mo) |
| **VIX Prices** | ❌ NoAccess | Add CBOE Indices ($7/mo) |
| **Bot Testing** | ✅ Ready NOW | Use external feeds |
| **Live Trading** | ⏳ Optional upgrade | Add subscriptions |

---

## Recommended Path Forward

### TODAY:
1. ✅ Keep current subscriptions
2. ✅ Use external price feeds (working now)
3. ✅ Run bot in dry-run mode
4. ✅ Test strategy logic thoroughly

### WHEN READY FOR LIVE TRADING:
1. Add NYSE Arca Level 1 ($7/month)
2. Add CBOE Indices Level 1 ($7/month)
3. Set external_price_feed.enabled = false in config
4. Start with small position size
5. Monitor real-time prices

---

## Quick Reference: Subscription Search

**In Saxo Portal**, when searching for subscriptions, look for:

For SPY prices:
- Search: "NYSE Arca" (not just "NYSE")
- Look for: "Stock exchange" category
- Select: "Level 1"
- Cost: $7 USD/month

For VIX prices:
- Search: "CBOE Indices" (not just "CBOE")
- Look for: "Stock exchange" category (same tab as NYSE Arca)
- Select: "Level 1"
- Cost: $7 USD/month

**Note**: CBOE is already listed in your screenshots under "Available subscriptions"

---

## Why This Matters for Your Bot

```
Current Flow (With External Feed):
┌─────────────┐
│  Bot Starts │
└──────┬──────┘
       ▼
┌─────────────────────────┐
│ Try to Get SPY Price    │
│ from Saxo API           │
└──────┬──────────────────┘
       │ Returns: NoAccess
       ▼
┌─────────────────────────┐
│ Fall back to Yahoo      │ ← External feed
│ Finance                 │   (15-min delay)
└──────┬──────────────────┘
       ▼
┌─────────────────────────┐
│ Use Price: $694.07      │
│ for Trading Logic       │
└─────────────────────────┘

After Adding Subscriptions:
┌─────────────┐
│  Bot Starts │
└──────┬──────┘
       ▼
┌─────────────────────────┐
│ Get SPY Price           │
│ from Saxo API           │
└──────┬──────────────────┘
       │ Returns: Real price
       │ (real-time bid/ask)
       ▼
┌─────────────────────────┐
│ Use Real Price          │
│ for Trading Logic       │
└─────────────────────────┘
```

---

## FAQ

**Q: Do I need Level 2 subscriptions?**
A: No. Level 1 includes all you need:
- Real-time best bid/ask
- Charts
- Last traded price
- Level 2 adds market depth (5-best bid/ask) which is optional

**Q: Will this fix the 404 error for VIX?**
A: Yes. The 404 happens when the API tries non-existent asset types. Once CBOE Indices is subscribed, VIX will be found immediately.

**Q: How long until it activates?**
A: Usually 1-5 minutes after subscribing. Sometimes instant.

**Q: Can I test without subscriptions?**
A: Yes! Current setup with Yahoo Finance works fine for testing.

**Q: Do I need these for options trading?**
A: No. You already have OPRA for options. These are only for the underlying asset prices (SPY/VIX).

---

## Summary

**You're 80% done.**

Your subscriptions include OPRA (options data), which is the most important part.

To get real-time prices for SPY and VIX, just add two more Level 1 subscriptions:
1. NYSE Arca ($7/mo) - for SPY prices
2. CBOE Indices ($7/mo) - for VIX prices

Cost: Only $14/month total
Time: 5 minutes to add
Result: Real-time prices instead of 15-min delayed

**Do this when you're ready for live trading. For testing now, your current setup with external feeds works perfectly.**
