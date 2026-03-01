# HYDRA Trading Journal

**Created**: February 17, 2026
**Last Updated**: February 26, 2026
**Purpose**: Day-by-day trading record with entry-level detail, P&L tracking, and improvement impact analysis. Future Claude Code sessions should reference this file instead of re-pulling all logs and sheets data.

---

## Table of Contents

1. [Trading Period: Feb 10-26, 2026](#1-trading-period-feb-10-26-2026)
2. [Daily Summary Data (Raw)](#2-daily-summary-data-raw)
3. [Entry-Level Detail by Day](#3-entry-level-detail-by-day)
4. [Market Conditions](#4-market-conditions)
5. [Key Performance Metrics](#5-key-performance-metrics)
6. [Identified Weaknesses](#6-identified-weaknesses)
7. [Recommended Improvements](#7-recommended-improvements)
8. [Improvement Implementation Log](#8-improvement-implementation-log)
9. [Post-Improvement Performance Tracking](#9-post-improvement-performance-tracking)

---

## 1. Trading Period: Feb 10-26, 2026

**Bot Versions**: v1.2.7 (Feb 10-17), v1.2.8 (Feb 18), v1.2.9 (Feb 18 post-market), v1.3.0 (Feb 19), v1.3.2 (Feb 20-23), v1.3.5-v1.3.8 (Feb 24), v1.3.9-v1.3.11 (Feb 25-26)
**Trading Days**: 12 (Feb 10, 11, 12, 13, 17, 18, 19, 20, 23, 24, 25, 26)
**Config**: 5 entries per day, EMA 20/40 trend filter
- Feb 10-17: 0.1% neutral threshold, no cascade breaker (baseline)
- Feb 18+: 0.2% neutral threshold (Rec 9.3), cascade breaker at 3 stops (MKT-016)
- Feb 19+: daily loss limit -$500 (MKT-017), early close ROC 2% (MKT-018)
- Feb 20+: progressive call tightening (MKT-020), pre-entry ROC gate (MKT-021)
- Feb 23 post-market: MKT-016/017 removed (v1.3.3), Fix #82 settlement gate (v1.3.4)
- Feb 24: MKT-022 put tightening (v1.3.5), MKT-011 v1.3.6 NEUTRAL skip, MKT-023 hold check (v1.3.7), Fix #83 idempotency guard (v1.3.8)
- Feb 25: MKT-021 ROC gate lowered to 3 entries (v1.3.9), cumulative ROC columns (v1.3.10), MKT-018 threshold 2%→3% (v1.3.11)
- Feb 26: First full day with MKT-018 at 3% threshold (v1.3.11)
**Capital Deployed**: $10,000-$32,000 per day (varies by entry count and spread width)

### Period Result
- **Net P&L**: +$1,565
- **Winning Days**: 8 (66.7%)
- **Losing Days**: 4 (33.3%)
- **Total Entries**: 50
- **Total Stops**: 28 (56.0% stop rate)
- **Win Rate (entries with 0 stops)**: 44.0% (22/50)

---

## 2. Daily Summary Data (Raw)

Source: Google Sheets "Daily Summary" tab. Feb 17 capital corrected from $12,500 to $30,500 (Fix #77 bug dropped entries with surviving sides from daily_state).

| Column | Feb 10 | Feb 11 | Feb 12 | Feb 13 | Feb 17 | Feb 18 | Feb 19 | Feb 20 | Feb 23 | Feb 24 | Feb 25 | **Feb 26** |
|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| Date | 2026-02-10 | 2026-02-11 | 2026-02-12 | 2026-02-13 | 2026-02-17 | 2026-02-18 | 2026-02-19 | 2026-02-20 | 2026-02-23 | 2026-02-24 | 2026-02-25 | **2026-02-26** |
| SPX Open | 6970.55 | 6988.93 | 6961.62 | 6832.04 | 6814.71 | 6848.12 | 6858.05 | 6857.52 | 6877.47 | 6861.77 | 6906.56 | **6937.98** |
| SPX Close | 6943.87 | 6939.96 | 6834.14 | 6834.38 | 6845.81 | 6878.07 | 6861.00 | 6878.72 | 6836.90 | 6890.34 | 6926.54 | **6907.46** |
| SPX High | 6985.81 | 6990.65 | 6973.34 | 6881.57 | 6866.63 | 6909.21 | 6877.89 | 6908.53 | 6914.87 | 6897.34 | 6935.67 | **6943.23** |
| SPX Low | 6937.67 | 6913.86 | 6824.12 | 6791.34 | 6775.17 | 6848.12 | 6836.88 | 6833.05 | 6820.71 | 6836.15 | 6906.56 | **6860.69** |
| VIX Open | 17.35 | 16.95 | 17.36 | 20.97 | 21.86 | 19.73 | 20.42 | 20.46 | 20.56 | 20.64 | 19.39 | **17.60** |
| VIX Close | 17.81 | 17.65 | 20.74 | 20.62 | 20.29 | 19.56 | 20.28 | 19.54 | 21.35 | 19.50 | 18.64 | **18.63** |
| VIX High | 17.97 | 18.96 | 21.21 | 22.40 | 22.96 | 20.21 | 21.06 | 21.21 | 22.04 | 21.28 | 19.39 | **20.54** |
| VIX Low | 17.14 | 16.75 | 17.08 | 18.93 | 19.76 | 18.48 | 19.82 | 18.77 | 19.50 | 19.28 | 18.54 | **17.60** |
| Entries Completed | 5 | 6 | 6 | 5 | 5 | 4 | 4 | 3 | 2 | 4 | 2 | **4** |
| Entries Skipped | 1 | 0 | 0 | 0 | 0 | 1 | 1 | 2 | 3 | 1 | 3 | **1** |
| Full ICs | 0 | 1 | 4 | 4 | 3 | 1 | 2 | 3 | 2 | 2 | 2 | **3** |
| One-Sided Entries | 5 | 5 | 2 | 1 | 2 | 3 | 2 | 0 | 0 | 2 | 0 | **1** |
| Bullish Signals | 0 | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| Bearish Signals | 0 | 1 | 2 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | **1** |
| Neutral Signals | 5 | 5 | 4 | 4 | 3 | 4 | 4 | 3 | 2 | 4 | 2 | **3** |
| Total Credit ($) | 640 | 1170 | 1610 | 3045 | 1885 | 810 | 1265 | 1775 | 915 | 975 | 490 | **1345** |
| Call Stops | 0 | 0 | 0 | 1 | 3 | 0 | 0 | 0 | 0 | 1 | 0 | **2** |
| Put Stops | 1 | 2 | 4 | 2 | 2 | 2 | 3 | 1 | 2 | 0 | 0 | **2** |
| Double Stops | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| Stop Loss Debits ($) | 140 | 290 | 410 | 1145 | 1335 | 260 | 380 | 800 | 655 | 340 | 250 | **1025** |
| Commission ($) | 30 | 45 | 70 | 60 | 65 | 35 | 45 | 60 | 30 | 55 | 40 | **55** |
| Expired Credits ($) | 520 | 760 | 840 | 1880 | 660 | 610 | 395 | 1550 | 280 | 830 | 490 | **370** |
| Daily P&L ($) | 350 | 425 | 360 | 675 | -740 | 315 | -30 | 690 | -405 | 435 | 200 | **-710** |
| Daily P&L (EUR) | 294.27 | 357.99 | 303.31 | 568.71 | -624.26 | 267.32 | -25.47 | 585.74 | -344.10 | ~369.75 | 169.42 | **-601.81** |
| Cumulative P&L ($) | 350 | 775 | 1135 | 1810 | 1070 | 1385 | 1355 | 2045 | 1640 | 2075 | 2275 | **1565** |
| Cumulative P&L (EUR) | 294.27 | 652.81 | 956.27 | 1524.98 | 902.64 | 1175.35 | 1150.55 | 1736.03 | 1393.38 | ~1763 | 1927.17 | **1326.53** |
| Win Rate (%) | 80.0 | 66.7 | 33.3 | 40.0 | 0.0 | 50.0 | 25.0 | 66.7 | 0.0 | 75.0 | 100.0 | **0.0** |
| Capital Deployed ($) | 25000 | 30000 | 32000 | 28000 | 30500 | 20000 | 23000 | 15000 | 12000 | 22000 | 10000 | **21500** |
| Return on Capital (%) | 1.40 | 1.42 | 1.13 | 2.41 | -2.43 | 1.57 | -0.13 | 4.60 | -3.38 | 1.98 | 2.00 | **-3.30** |
| Sortino Ratio | 0.00 | 99.99 | 99.99 | 99.99 | 11.49 | 14.70 | 1.90 | 6.09 | 2.41 | ~3.2 | 4.97 | **2.29** |
| Max Loss Stops ($) | 640 | 1170 | 1610 | 3045 | 1885 | 810 | 1265 | 1775 | 915 | 975 | 490 | **1345** |
| Max Loss Catastrophic ($) | 24360 | 28830 | 30390 | 24955 | 28615 | 19190 | 21735 | 13225 | 11085 | 21025 | 9510 | **20155** |
| Early Close | -- | -- | -- | -- | -- | -- | No | Yes, 11:31 ET | No | Yes, 14:17 ET | Yes, 11:15 ET | **No** |
| Notes | Post-settlement | Post-settlement | Post-settlement | Post-settlement | Post-settlement | Post-settlement (v1.2.8) | Post-settlement (v1.3.0) | Post-settlement (v1.3.2) | Fix #82 corrected (v1.3.2) | MKT-018 early close (v1.3.5→v1.3.8) | MKT-018 early close (v1.3.9→v1.3.11) | **Post-settlement (v1.3.11), 4 stops, first BEARISH since Feb 17** |

**Note**: All values verified against Google Sheets Daily Summary tab and Saxo closed positions data.

### P&L Verification Formula
`Daily P&L = Expired Credits - Stop Loss Debits - Commission`
- Feb 10: 520 - 140 - 30 = 350 ✓
- Feb 11: 760 - 290 - 45 = 425 ✓
- Feb 12: 840 - 410 - 70 = 360 ✓
- Feb 13: 1880 - 1145 - 60 = 675 ✓
- Feb 17: 660 - 1335 - 65 = -740 ✓
- Feb 18: 610 - 260 - 35 = 315 ✓
- Feb 19: 395 - 380 - 45 = -30 ✓
- Feb 20: 1550 - 800 - 60 = 690 ✓
- Feb 23: 280 - 655 - 30 = -405 ✓ (Saxo confirms -$405.00 total; metrics corrected from -$685 due to Fix #82)
- Feb 24: 830 - 340 - 55 = 435 ✓ (MKT-018 early close at 14:17 ET, Fix #83 unblocked daily summary)
- Feb 25: 490 - 250 - 40 = 200 ✓ (MKT-018 early close at 11:15 ET, all positions early-closed, 0 stops)
- Feb 26: 370 - 1025 - 55 = -710 ✓ (4 stops, first BEARISH signal since Feb 17, no early close)

### Cumulative Metrics (meic_metrics.json as of Feb 26 EOD)
```json
{
  "cumulative_pnl": 1565.0,
  "total_entries": 50,
  "winning_days": 8,
  "losing_days": 4,
  "total_credit_collected": 15925.0,
  "total_stops": 28,
  "double_stops": 0,
  "last_updated": "2026-02-26"
}
```
**Note**: Feb 26 was the worst loss day since Feb 17 (-$740). First BEARISH EMA signal since Feb 17. All 4 placed entries had at least one side stopped.

---

## 3. Entry-Level Detail by Day

### Feb 10 (Tuesday) - NET P&L: +$350

**Market**: Range-bound, calm. SPX range 48 pts (0.7%). VIX 17-18.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Put-only (MKT-011) | P:6935 | $210 | EXPIRED | +$210 |
| #2 | 10:35 | NEUTRAL | Put-only (MKT-011) | P:6935 | $150 | EXPIRED | +$150 |
| #3 | 11:05 | NEUTRAL | Put-only (MKT-011) | P:6930 | $120 | PUT STOPPED | -$120+credit |
| #4 | 11:35 | NEUTRAL | Put-only (MKT-011) | P:6920 | $95 | EXPIRED | +$95 |
| #5 | 12:05 | NEUTRAL | Put-only (MKT-011) | P:6915 | $65 | EXPIRED | +$65 |
| #6 | -- | -- | SKIPPED | -- | -- | Both sides non-viable | -- |

**Key observations**:
- ALL entries NEUTRAL signal, but MKT-011 converted all to put-only (call credits $17.50-$37.50, below $50 min)
- Only 1 stop out of 5 entries (20% stop rate)
- MKT-011 credit gate prevented 5 unprofitable call-spread entries

### Feb 11 (Wednesday) - NET P&L: +$425

**Market**: Flat, cautious. SPX range 77 pts (1.1%). VIX 17-18.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:7000 P:6910 | $435 | Put STOPPED, Call EXPIRED | -$155 + $125 |
| #2 | 10:35 | BEARISH | Call-only | C:6980 | $140 | EXPIRED | +$140 |
| #3 | 11:05 | NEUTRAL | Put-only (MKT-011) | P:6890 | $200 | EXPIRED | +$200 |
| #4 | 11:35 | NEUTRAL | Put-only (MKT-011) | P:6900 | $170 | EXPIRED | +$170 |
| #5 | 12:05 | NEUTRAL | Put-only (MKT-011) | P:6885 | $125 | EXPIRED | +$125 |
| #6 | 12:35 | NEUTRAL | Put-only (MKT-011) | P:6910 | $100 | PUT STOPPED | -$135 |

**Key observations**:
- 1 IC, 1 call-only (BEARISH signal), 4 put-only (MKT-011 conversions)
- Entry #1 IC: put stopped but call side survived to expiry = partial win
- 2 stops out of 6 entries (33% stop rate)

### Feb 12 (Thursday) - NET P&L: +$360

**Market**: MAJOR SELL-OFF. SPX -1.57%, range 149 pts (2.1%). VIX 17→21. Cisco earnings collapse.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:6990 P:6900 | $320 | Put STOPPED, Call EXPIRED | -$95 + $80 |
| #2 | 10:35 | NEUTRAL | Full IC | C:6985 P:6895 | $290 | Put STOPPED, Call EXPIRED | -$75 + $65 |
| #3 | 11:05 | BEARISH | Call-only | C:6950 | $185 | EXPIRED | +$185 |
| #4 | 11:35 | BEARISH | Call-only | C:6920 | $250 | EXPIRED | +$250 |
| #5 | 12:05 | NEUTRAL | Full IC | C:6915 P:6805 | $310 | Put STOPPED, Call EXPIRED | -$215 + $165 |
| #6 | 12:35 | NEUTRAL | Full IC | C:6925 P:6810 | $255 | Put STOPPED, Call EXPIRED | -$80 + $95 |

**Key observations**:
- **Trend filter's best day**: 2 BEARISH signals placed call-only spreads that survived the sell-off
- ALL 4 put sides that were placed got stopped (100% put stop rate)
- ALL 6 call sides expired (100% call survival) - market moved away from calls
- Despite 4 stops, net P&L was positive (+$360) because call expirations offset put stops
- This is exactly the scenario HYDRA was designed for

### Feb 13 (Friday) - NET P&L: +$675

**Market**: Post-crash stabilization. CPI soft. SPX range 90 pts (1.3%). VIX elevated at 21.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:6885 P:6765 | $1,150 | Call STOPPED, Put EXPIRED | -$650 + $620 |
| #2 | 10:35 | BULLISH | Put-only | P:6805 | $430 | PUT STOPPED | -$440 |
| #3 | 11:05 | NEUTRAL | Full IC | C:6905 P:6795 | $675 | Both EXPIRED | +$675 |
| #4 | 11:35 | NEUTRAL | Full IC | C:6910 P:6800 | $475 | Both EXPIRED | +$475 |
| #5 | 12:05 | NEUTRAL | Full IC | C:6920 P:6820 | $315 | Put STOPPED, Call EXPIRED | -$130 + $110 |

**Key observations**:
- Highest credit day ($3,045 total) due to elevated VIX
- Entry #1 collected $1,150 for a single IC (60pt spreads) - enormous premium
- Entry #3 and #4 were PERFECT: both sides of full ICs expired worthless
- 3 stops but offset by massive expired credits ($1,880)
- Entry #2 BULLISH signal placed put-only, but put still got stopped
- Largest single-side stop of the period: Entry #1 call at -$650

### Feb 17 (Tuesday) - NET P&L: -$740 (THE LOSS DAY)

**Market**: Post-Presidents' Day. V-shaped reversal. SPX range 92 pts (1.3%). VIX 20-23.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | BEARISH | Call-only | C:6860 | $305 | CALL STOPPED at 11:11 | -$295 |
| #2 | 10:35 | NEUTRAL | Full IC | C:6840 P:6720 | $695 | CALL STOPPED at 11:02 | -$335 |
| #3 | 11:05 | NEUTRAL | Full IC | C:6875 P:6755 | $400 | CALL STOPPED at 11:13 | -$265 |
| #4 | 11:35 | BULLISH | Put-only | P:6780 | $235 | PUT STOPPED at 12:53 | -$225 |
| #5 | 12:05 | NEUTRAL | Full IC | C:6895 P:6785 | $250 | PUT STOPPED at 12:11 | -$30 |

**Key observations**:
- **ALL 5 entries stopped. 0 winners. 100% loss rate.**
- 3 call stops in 11-minute window (11:02, 11:11, 11:13) during sharp rally
- Trend filter WHIPSAWED: BEARISH (10:05) → NEUTRAL (10:35, 11:05) → BULLISH (11:35) → NEUTRAL (12:05)
- Entry #3 stopped just 8 minutes after placement
- Market V-shape: Down to 6775 (BEARISH read), rallied to 6867 (calls breached), pulled back (puts breached)
- Entry #2 and #3 expired credits from put sides = $660 (added at settlement)

### Stop Timing Log (Feb 17 - Critical Data)

```
11:02 ET - Entry #2 CALL STOPPED (-$335) ← First stop
11:11 ET - Entry #1 CALL STOPPED (-$295) ← 9 min after first
11:13 ET - Entry #3 CALL STOPPED (-$265) ← 2 min after second
   === 3 stops in 11 minutes, $895 in debits ===
11:35 ET - Entry #4 PLACED (BULLISH put-only) ← Placed AFTER cascade
12:05 ET - Entry #5 PLACED (NEUTRAL full IC) ← Placed AFTER cascade
12:11 ET - Entry #5 PUT STOPPED (-$30)
12:53 ET - Entry #4 PUT STOPPED (-$225)
   === Entries placed after cascade lost additional $255 ===
```

### Feb 18 (Wednesday) - NET P&L: +$315 ★ FIRST DAY WITH v1.2.8 IMPROVEMENTS

**Market**: Quiet recovery. SPX +0.3%, range 61 pts (0.9%). VIX dropping from 19.7 to 18.3 mid-day, closing at 18.5.
**Bot Version**: v1.2.8 (EMA threshold 0.2%, MKT-016 cascade breaker at 3 stops)

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:6945 P:6845 | $390 | Both EXPIRED | +$390 |
| #2 | 10:35 | NEUTRAL | Put-only (MKT-011) | P:6840 | $220 | PUT EXPIRED | +$220 |
| #3 | 11:05 | NEUTRAL | Put-only (MKT-011) | P:6855 | $115 | PUT STOPPED at 13:53 | -$125 |
| #4 | 11:35 | NEUTRAL | Put-only (MKT-011) | P:6850 | $85 | PUT STOPPED at 13:53 | -$135 |
| #5 | 12:05 | -- | SKIPPED (MKT-011) | -- | -- | Both sides non-viable ($12.50/$42.50) | -- |

**Key observations**:
- **ALL entries NEUTRAL** — max EMA divergence was +0.034%, deep within the 0.2% threshold
- MKT-011 converted 3 entries to put-only (call credits $45, $22.50, $17.50 — all below $50 min)
- Entry #5 SKIPPED entirely — both call ($12.50) and put ($42.50) below $50 minimum
- Only 2 stops — **MKT-016 cascade breaker did NOT trigger** (threshold is 3)
- Both stops occurred at same time (13:53 ET) as SPX dipped toward short put strikes
- Entry #1 full IC: ALL sides expired worthless — perfect entry, kept $390
- **50% entry win rate** (2 of 4 entries had 0 stops)
- Lower credit day ($810) due to VIX dropping from 22 (Feb 17) to 19

### Stop Timing Log (Feb 18)

```
10:05 ET - Entry #1 PLACED (NEUTRAL full IC, C:6945/6970 P:6845/6795)
10:35 ET - Entry #2 PLACED (NEUTRAL → put-only MKT-011, P:6840/6790)
11:05 ET - Entry #3 PLACED (NEUTRAL → put-only MKT-011, P:6855/6805)
11:35 ET - Entry #4 PLACED (NEUTRAL → put-only MKT-011, P:6850/6800)
12:05 ET - Entry #5 SKIPPED (MKT-011, both non-viable)
13:53 ET - Entry #3 PUT STOPPED (short_put filled at $2.95, long_put at $0.55)
13:53 ET - Entry #4 PUT STOPPED (short_put filled at $2.70, long_put at $0.50)
   === 2 stops total — cascade breaker NOT triggered ===
17:00 ET - Settlement: 6 positions expired, +$610 expired credits added
```

### Fill Price Detail (Feb 18 Stops — Fix #76 Verified)

| Entry | Leg | Fill Price | Source |
|-------|-----|-----------|--------|
| #3 | short_put close | $2.95 | Activities (AveragePrice) |
| #3 | long_put close | $0.55 | Activities (AveragePrice) |
| #4 | short_put close | $2.70 | Activities (AveragePrice) |
| #4 | long_put close | $0.50 | Activities (AveragePrice) |

**Stop loss close costs**:
- Entry #3: ($2.95 - $0.55) × 100 = $240, credit was $115 → net loss: $125
- Entry #4: ($2.70 - $0.50) × 100 = $220, credit was $85 → net loss: $135
- Total stop loss debits: $260

### Feb 19 (Thursday) - NET P&L: -$30 ★ FIRST DAY WITH v1.3.0 (MKT-017 + MKT-018)

**Market**: Choppy, range-bound with downside pressure. SPX range 41 pts (0.6%). VIX elevated 20-21 — higher premium but put sides vulnerable.
**Bot Version**: v1.3.0 (MKT-017 daily loss limit -$500, MKT-018 early close ROC 2%)
**Multiple bot restarts**: 6 PID changes during the day (deployment iterations before market open + recovery tests)

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:6925 P:6815 | $475 (C:$105, P:$370) | Put STOPPED at 10:07, Call EXPIRED | -$265 + $105 |
| #2 | 10:35 | NEUTRAL | Full IC | C:6930 P:6820 | $370 (C:$45, P:$325) | Put STOPPED at 10:51, Call EXPIRED | -$280 + $45 |
| #3 | 11:05 | NEUTRAL | Put-only (MKT-011) | P:6810 | $245 | PUT EXPIRED | +$245 |
| #4 | 11:35 | NEUTRAL | Put-only (MKT-011) | P:6815 | $175 | PUT STOPPED at 11:45 | -$175 |
| #5 | 12:05 | -- | SKIPPED (MKT-016) | -- | -- | Cascade breaker (3 stops) | -- |

**Key observations**:
- **ALL entries NEUTRAL** — max EMA divergence ~-0.084%, deep within the 0.2% threshold
- **MKT-016 CASCADE BREAKER TRIGGERED** at 11:45 ET (3rd stop: Entry #4 put) — **blocked Entry #5**
- MKT-017 daily loss limit NOT triggered (realized P&L = -$380, threshold is -$500, buffer of $120)
- MKT-018 early close NOT triggered (ROC was negative all day, ~-0.26% at 3:59 PM vs 2.0% threshold)
- Entry #1 put stopped just 2 minutes after placement — fastest stop of the entire period
- Entry #3 was the ONLY clean winner — put-only (MKT-011 converted due to call credit $45 < $50)
- MKT-011 converted Entries #3 and #4 to put-only (call credits $45 and $12.50, below $50 min)
- Entry #4 call credit was only $12.50 because MKT-013 shifted call strikes from 6925/6975 to 6935/6985 (further OTM)
- Total expired credits ($395) nearly offset total stop debits ($380) — day was near breakeven before commission
- 25% entry win rate (1 of 4 entries with 0 stops) — worst entry win rate after Feb 17's 0%

### Stop Timing Log (Feb 19)

```
10:05 ET - Entry #1 PLACED (NEUTRAL full IC, C:6925/6960 P:6815/6755, $475 credit)
10:07 ET - Entry #1 PUT STOPPED (2 min after placement!)
              short_put fill: $6.10, long_put fill: $1.25
              Close cost: ($6.10-$1.25)×100=$485, credit was $370 → net side loss: ~$115
              But IC breakeven: total credit $475, stop debit ~$475 → net entry loss: ~$0 + commission
10:35 ET - Entry #2 PLACED (NEUTRAL full IC, C:6930/6990 P:6820/6760, $370 credit)
10:51 ET - Entry #2 PUT STOPPED (16 min after placement)
              short_put fill: $5.00, long_put fill: $0.90
              Close cost: ($5.00-$0.90)×100=$410, credit was $325 → net side loss: ~$85
              === 2 stops, MKT-016 not yet triggered ===
11:05 ET - Entry #3 PLACED (NEUTRAL → put-only MKT-011, P:6810/6750, $245 credit)
11:35 ET - Entry #4 PLACED (NEUTRAL → put-only MKT-011, P:6815/6765, $175 credit)
              Call strikes shifted by MKT-013: 6925→6935, 6975→6985 (overlap with #1/#2)
              Call credit after shift: $12.50 < $50 → MKT-011 converted to put-only
11:45 ET - Entry #4 PUT STOPPED (10 min after placement)
              === 3 stops total — MKT-016 CASCADE BREAKER TRIGGERED ===
12:05 ET - Entry #5 SKIPPED (MKT-016: 3 stops reached threshold of 3)
15:59 ET - Last heartbeat: P&L: $15.00 gross, -$30.00 net ($45 comm)
              MKT-018-SHADOW: ROC=-0.26% / 2.0% threshold
16:00+ ET - Settlement: 6 positions expired, +$395 expired credits added
```

### Feb 20 (Friday) - NET P&L: +$690 ★ FIRST MKT-018 EARLY CLOSE TRIGGER

**Market**: Wide range, V-shaped intraday. SPX range 76 pts (1.1%). VIX elevated 19-21 — high premium day. SPX dipped to 6833 early then rallied to 6909 before settling at 6879.
**Bot Version**: v1.3.2 (MKT-020 progressive call tightening, MKT-021 pre-entry ROC gate, Fix #81 early close 409 retry)
**MKT-018 early close**: Triggered at 11:31 ET — ROC reached 2% threshold, closed all remaining positions to lock in profit.
**MKT-021 ROC gate**: Blocked Entries #4 and #5 (ROC already >= 2% before entry time)

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:6940/6990 P:6830/6780 | $975 (C:$180, P:$795) | All EARLY-CLOSED at 11:31 | +$680 |
| #2 | 10:35 | NEUTRAL | Full IC | C:6935/6985 P:6825/6775 | $480 (C:$135, P:$345) | All EARLY-CLOSED at 11:31 | +$225 |
| #3 | 11:05 | NEUTRAL | Full IC (MKT-020 tightened call) | C:6945/6995 P:6855/6805 | $320 (C:$95, P:$225) | Put STOPPED, Call EARLY-CLOSED | -$155 |
| #4 | 11:35 | -- | SKIPPED (MKT-021) | -- | -- | ROC >= 2% gate | -- |
| #5 | 12:05 | -- | SKIPPED (MKT-021) | -- | -- | ROC >= 2% gate | -- |

**Key observations**:
- **FIRST LIVE MKT-018 EARLY CLOSE** — ROC hit 2% at 11:31 ET, bot closed all remaining positions to lock in profit
- **ALL entries NEUTRAL** — all 3 entries had NEUTRAL EMA signal
- **MKT-020 tightened Entry #3's call** — progressive call OTM tightening (minimum $1.00/side credit)
- **MKT-021 blocked Entries #4 and #5** — pre-entry ROC gate prevented new entries after 2% ROC reached
- Entry #3 put stopped (SPX dipped toward 6855 short put), but call side survived and was early-closed
- Entry #1 collected $975 — highest single-entry credit of the session, both sides early-closed for +$680
- 409 Conflict error on Entry #1 long call close during early close (concurrent SaxoTraderGO operation) — user manually intervened
- **66.7% entry win rate** (2 of 3 entries with 0 stops)
- Highest daily P&L since Feb 13 (+$675)

### Stop Timing Log (Feb 20)

```
10:05 ET - Entry #1 PLACED (NEUTRAL full IC, C:6940/6990 P:6830/6780, $975 credit)
10:35 ET - Entry #2 PLACED (NEUTRAL full IC, C:6935/6985 P:6825/6775, $480 credit)
11:05 ET - Entry #3 PLACED (NEUTRAL full IC, C:6945/6995 P:6855/6805, $320 credit)
              MKT-020: Call side tightened (progressive OTM adjustment)
~11:2x ET - Entry #3 PUT STOPPED (SPX dipped toward 6855)
              short_put fill: $6.20, long_put fill: $1.70
              Close cost: ($6.20-$1.70)×100=$450, credit was $225 → net side loss: $225
11:31 ET - MKT-018 EARLY CLOSE TRIGGERED (ROC >= 2.0%)
              Closing all remaining positions to lock in profit
              === Entry #1: 4 legs closed, +$680 P&L ===
              === Entry #2: 4 legs closed, +$225 P&L ===
              === Entry #3: call side closed (put already stopped), +$70 call P&L ===
              409 Conflict on Entry #1 long call close — user manually closed via SaxoTraderGO
11:35 ET - Entry #4 SKIPPED (MKT-021: ROC >= 2% gate)
12:05 ET - Entry #5 SKIPPED (MKT-021: ROC >= 2% gate)
16:00+ ET - Settlement: 0 positions remaining (all closed by early close + stop)
```

### Fill Price Detail (Feb 20 — From Saxo Closed Positions)

**Entry #1 (10:05 ET): Full IC, C:6940/6990 P:6830/6780 — All Early-Closed**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6940 C | Short | $2.50 | $0.35 | +$215 |
| LC 6990 C | Long | $0.70 | $0.10 | -$60 |
| SP 6830 P | Short | $10.50 | $3.60 | +$690 |
| LP 6780 P | Long | $2.55 | $0.90 | -$165 |
| **Total** | | | | **+$680** |

**Entry #2 (10:35 ET): Full IC, C:6935/6985 P:6825/6775 — All Early-Closed**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6935 C | Short | $1.50 | $0.55 | +$95 |
| LC 6985 C | Long | $0.15 | $0.05 | -$10 |
| SP 6825 P | Short | $5.00 | $2.75 | +$225 |
| LP 6775 P | Long | $1.55 | $0.70 | -$85 |
| **Total** | | | | **+$225** |

**Entry #3 (11:05 ET): Full IC, C:6945/6995 P:6855/6805 — Put Stopped, Call Early-Closed**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6945 C | Short | $1.10 | $0.30 | +$80 |
| LC 6995 C | Long | $0.15 | $0.05 | -$10 |
| SP 6855 P | Short | $3.40 | $6.20 | -$280 |
| LP 6805 P | Long | $1.15 | $1.70 | +$55 |
| **Total** | | Call: +$70, Put: -$225 | | **-$155** |

**P&L Reconciliation**: $680 + $225 + (-$155) = **$750 gross**. Commission: 3 entries × 4 legs open + Entry #3 put 2 legs stop + Entry #1/2/3 call early-close legs = $60. **$750 - $60 = $690 net** ✓

### Feb 23 (Monday) - NET P&L: -$405 ★ FIX #82 SETTLEMENT BUG DISCOVERED

**Market**: Sustained sell-off. SPX dropped 41 pts from open (6877) to close (6837), with 94-pt intraday range (low 6821). VIX rose from 20.6 to 21.4 — elevated volatility, puts under pressure.
**Bot Version**: v1.3.2 (MKT-016 cascade breaker + MKT-017 daily loss limit still active; removed post-market in v1.3.3)
**MKT-017 triggered**: At 11:03 ET — realized P&L hit -$655, exceeding -$500 threshold. Blocked Entries #3-5.
**Fix #82 impact**: Settlement at midnight failed to process expired credits ($280), logging -$685 instead of correct -$405. Metrics manually corrected against Saxo platform data.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:6925/6980 P:6815/6755 | $525 (C:$145, P:$380) | Put STOPPED at 11:02, Call EXPIRED | -$250 gross, -$265 net |
| #2 | 10:35 | NEUTRAL | Full IC | C:6910/6970 P:6800/6740 | $390 (C:$135, P:$255) | Put STOPPED at 11:03, Call EXPIRED | -$125 gross, -$140 net |
| #3 | 11:05 | -- | SKIPPED (MKT-017) | -- | -- | Daily loss limit (-$655 < -$500) | -- |
| #4 | 11:35 | -- | SKIPPED (MKT-017) | -- | -- | Daily loss limit | -- |
| #5 | 12:05 | -- | SKIPPED (MKT-017) | -- | -- | Daily loss limit | -- |

**Key observations**:
- **Both entries NEUTRAL** — EMA divergence -0.085% and -0.094%, deep within 0.2% threshold
- **MKT-017 DAILY LOSS LIMIT TRIGGERED** at 11:03 ET — first live trigger of this feature
- Realized P&L hit -$655 after 2 put stops, exceeding -$500 threshold — blocked all 3 remaining entries
- Both put sides stopped within 1 minute of each other (11:01:42 and 11:02:52) as SPX sold off sharply
- Both call sides survived comfortably (95-100% cushion all day) and expired worthless at settlement
- MKT-008 adjusted Entry #1 long call from 6985→6980 (illiquidity fix)
- **0% entry win rate** (0 of 2 entries with 0 stops) — second day with 0% after Feb 17
- Without MKT-017, Entries #3-5 would have been placed into a declining market — likely more put stops
- **Fix #82 discovered**: Settlement at midnight processed entries but didn't add expired credits ($280) to realized P&L

### Stop Timing Log (Feb 23)

```
10:05 ET - Entry #1 PLACED (NEUTRAL full IC, C:6925/6980 P:6815/6755, $525 credit)
              VIX=20.7, OTM=55pts, spread=60pts
              MKT-008: Long Call 6985 illiquid, adjusted to 6980
              MKT-011: PASSED (Call $155, Put $362.50)
              Stop level: $750/side (2× max credit $380)
10:35 ET - Entry #2 PLACED (NEUTRAL full IC, C:6910/6970 P:6800/6740, $390 credit)
              VIX=20.9, OTM=55pts, spread=60pts
              MKT-011: PASSED (Call $137.50, Put $257.50)
              Stop level: $500/side (2× max credit $255)
11:01:42 ET - Entry #1 PUT STOPPED
              short_put fill: $9.00, long_put fill: $1.25
              Close cost: ($9.00-$1.25)×100=$775, credit was $380 → net side loss: $395
11:02:52 ET - Entry #2 PUT STOPPED (70 seconds after Entry #1)
              short_put fill: $6.10, long_put fill: $0.95
              Close cost: ($6.10-$0.95)×100=$515, credit was $255 → net side loss: $260
11:03:19 ET - MKT-017 TRIGGERED: realized P&L $-655.00 exceeds -$500 threshold
              === 3 remaining entries BLOCKED ===
11:05 ET - Entry #3 SKIPPED (MKT-017: daily loss limit)
11:35 ET - Entry #4 SKIPPED (MKT-017: daily loss limit)
12:05 ET - Entry #5 SKIPPED (MKT-017: daily loss limit)
15:59 ET - Last heartbeat: P&L: $-375.00 gross, -$405.00 net ($30 comm)
              Entry #1: Call 100% cushion, Put STOPPED
              Entry #2: Call 100% cushion, Put STOPPED
              MKT-018-SHADOW: ROC=-3.54% / 2.0% threshold — not triggered
00:00 ET - Settlement (midnight): Fix #82 bug — expired credits NOT processed
              Metrics recorded -$685 (missing $280 expired call credits)
              Manually corrected to -$405 against Saxo platform data
```

### Fill Price Detail (Feb 23 — Verified Against Saxo Closed Positions)

**Entry #1 (10:05 ET): Full IC, C:6925/6980 P:6815/6755 — Put Stopped, Call Expired**

| Leg | Direction | Open Price | Close Price | Saxo Realized P&L |
|-----|-----------|-----------|-------------|-------------------|
| SC 6925 C | Short | $1.60 | $0.00 (expired) | +$157.50 |
| LC 6980 C | Long | $0.15 | $0.00 (expired) | -$17.50 |
| SP 6815 P | Short | $5.20 | $9.00 (stopped) | -$385.00 |
| LP 6755 P | Long | $1.40 | $1.25 (stopped) | -$20.00 |
| **Total** | | | | **-$265.00** |

**Entry #2 (10:35 ET): Full IC, C:6910/6970 P:6800/6740 — Put Stopped, Call Expired**

| Leg | Direction | Open Price | Close Price | Saxo Realized P&L |
|-----|-----------|-----------|-------------|-------------------|
| SC 6910 C | Short | $1.50 | $0.00 (expired) | +$147.50 |
| LC 6970 C | Long | $0.15 | $0.00 (expired) | -$17.50 |
| SP 6800 P | Short | $3.40 | $6.10 (stopped) | -$275.00 |
| LP 6740 P | Long | $0.85 | $0.95 (stopped) | +$5.00 |
| **Total** | | | | **-$140.00** |

**P&L Reconciliation**: (-$265) + (-$140) = **-$405.00 net** (Saxo Realized P&L includes $2.50/leg commission). Gross P&L: -$375, Commission: $30 (8 open legs + 4 stop close legs = 12 × $2.50). **-$375 - $30 = -$405 net** ✓

### Feb 24 (Tuesday) - NET P&L: +$435 ★ MKT-018 EARLY CLOSE + FIX #83

**Market**: Morning dip and recovery. SPX dipped to 6836 at open then rallied to close at 6890 (+0.4%). VIX started elevated at 20.64 but dropped to 19.50 as market stabilized. 61-pt intraday range (0.9%).
**Bot Versions**: v1.3.5 at open → v1.3.6 (MKT-011 neutral skip) → v1.3.7 (MKT-023 hold check) → v1.3.8 (Fix #83), multiple mid-day deployments (10+ PIDs).
**MKT-018 triggered**: At 14:17:34 ET — ROC hit 2.02%, closing all remaining positions. Second live trigger (first was Feb 20).
**MKT-023 hold check**: Consistently CLOSE decision (close=$400-425 vs worst-case hold=$-315, CALLS_STRESSED).
**Fix #83 discovered**: Daily summary blocked by FIX-71 idempotency guard poisoned by midnight settlement storing clock time instead of trading date.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:06 | NEUTRAL | Full IC | C:6900/6950 P:6790/6730 | $465 (C:$125, P:$340) | All EARLY CLOSED at 14:17 | +$355 gross |
| #2 | 10:45 | NEUTRAL | Full IC (MKT-020 tightened calls 55→30pt) | C:6910/6960 P:6825/6775 | $265 (C:$130, P:$135) | All EARLY CLOSED at 14:17 | +$180 gross |
| #3 | 11:05 | NEUTRAL | Call-only (MKT-011: put $90<$100) | C:6905/6955 (MKT-020 55→30pt) | $145 (C only) | CALL STOPPED at 12:11:49 | -$145 gross |
| #4 | 11:35 | NEUTRAL | Put-only (MKT-011: call $32.50<$100) | P:6815/6755 (MKT-013/015 overlap adj) | $100 (P only) | EARLY CLOSED at 14:17 | +$100 gross |
| #5 | 12:05 | NEUTRAL | SKIPPED (MKT-011 v1.3.6: call non-viable + neutral) | -- | -- | MKT-022 tightened put 55→45pt, still skipped | -- |

**Key observations**:
- **All 4 entries NEUTRAL** — EMA divergences deep within 0.2% threshold (market choppy, no clear trend)
- **MKT-018 EARLY CLOSE** at 14:17:34 ET — second live trigger. ROC 2.02% locked in +$435 net profit
- **MKT-023 hold check** (first live day): consistently recommended CLOSE — worst-case hold was -$315 due to call stress, while close-now was +$400-425. Validated the early close decision
- **MKT-011 v1.3.6 first live SKIP** — Entry #5 skipped because call was non-viable ($32.50 < $100) in NEUTRAL market (new rule: NEUTRAL + one-side-non-viable = skip, no more conversions)
- **MKT-020 progressive call tightening** on Entries #2 and #3: moved short calls from 55pt to 30pt OTM to achieve $1.00/side minimum credit
- **MKT-022 progressive put tightening** on Entry #5 (pre-skip): moved short put from 55pt to 45pt OTM, but entry was still skipped by MKT-011
- **Entry #3 call stop**: cushion dropped from 64% to 6% in ~2 minutes (12:09:41→12:11:49) as SPX rallied sharply — demonstrates how fast 0DTE options can move
- **MKT-021 ROC gate**: triggered at 14:16:15 ET (ROC 1.79% approaching threshold), but all 5 entries already attempted
- **75% entry win rate** (3 of 4 entries with 0 stops) — best since Feb 10 (80%)
- **Multiple deployments** during market hours: v1.3.5 (MKT-022) → v1.3.6 (MKT-011 neutral skip) → v1.3.7 (MKT-023 hold check). Each deployment caused bot restart, but state file recovery preserved positions and P&L correctly

### Stop Timing Log (Feb 24)

```
10:06 ET - Entry #1 PLACED (NEUTRAL full IC, C:6900/6950 P:6790/6730, $465 credit)
              VIX=20.6, OTM=55pts call / 55pts put, spread=50pt/60pt
              MKT-011: PASSED (Call $125, Put $340)
              MKT-019: stop=$670/side (2× max credit $340, virtual equal credit)
10:45 ET - Entry #2 PLACED (NEUTRAL full IC, C:6910/6960 P:6825/6775, $265 credit)
              VIX=20.5, MKT-020: tightened short call from 55pt to 30pt OTM
              MKT-011: PASSED (Call $130, Put $135)
              MKT-019: stop=$270/side (2× max credit $135)
11:05 ET - Entry #3 PLACED (NEUTRAL→Call-only via MKT-011, C:6905/6955, $145 credit)
              MKT-011: put $90 < $100 minimum → call-only
              MKT-020: tightened short call from 55pt to 30pt OTM
              Stop=$290 (2× $145 for one-sided)
11:35 ET - Entry #4 PLACED (NEUTRAL→Put-only via MKT-011, P:6815/6755, $100 credit)
              MKT-011: call $32.50 < $100 minimum → put-only
              MKT-013/MKT-015: overlap adjustments applied
              Stop=$200 (2× $100 for one-sided)
12:05 ET - Entry #5 SKIPPED (MKT-011 v1.3.6: call $32.50 non-viable, NEUTRAL → skip)
              MKT-022: tightened put from 55pt to 45pt OTM (pre-skip analysis)
              v1.3.6 new rule: NEUTRAL + one side non-viable = SKIP (not convert)
12:09:41 ET - Entry #3 CALL cushion: 64%
12:10:xx ET - Entry #3 CALL cushion: 44% → 17% → 9%
12:11:49 ET - Entry #3 CALL STOPPED
              short_call fill: $3.10, long_call fill: $0.05
              Close cost: ($3.10-$0.05)×100=$305, credit was $145 → net side loss: $160
14:16:15 ET - MKT-021 ROC gate: ROC 1.79% approaching threshold (all entries already attempted)
14:17:34 ET - MKT-018 EARLY CLOSE TRIGGERED: ROC 2.02% >= 2.0% threshold
              MKT-023 hold check: CLOSE (close=$425 vs hold=$-315, CALLS_STRESSED)
              Entry #1: 4 legs closed (SC 6900 C $0.85, LC 6950 C $0.00 skip, SP 6790 P $0.20, LP 6730 P $0.05)
              Entry #2: 4 legs closed (SC 6910 C $0.25, LC 6960 C $0.00 skip, SP 6825 P $0.45, LP 6775 P $0.10)
              Entry #4: 2 legs closed (SP 6815 P $0.30, LP 6755 P $0.10)
              Fix #81: $0.00 long call legs skipped (saved 2 unnecessary close orders)
15:59 ET - Last heartbeat: P&L: $490 gross, $435 net ($55 comm)
              Hold Check: CLOSE | close=$425 vs hold=$-315 | CALLS_STRESSED (C:85%/P:96%)
18:55 ET - Daily summary fired (after Fix #83 deployed and bot restarted)
              Net P&L: $435.00, Commission: $55.00, Cumulative: $2,075
```

### Fill Price Detail (Feb 24 — Verified Against Saxo Closed Positions)

**Entry #1 (10:06 ET): Full IC, C:6900/6950 P:6790/6730 — All Early-Closed**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6900 C | Short | $1.40 | $0.85 (early close) | +$55 |
| LC 6950 C | Long | $0.15 | $0.00 (skipped) | -$15 |
| SP 6790 P | Short | $4.30 | $0.20 (early close) | +$410 |
| LP 6730 P | Long | $0.90 | $0.05 (early close) | -$85 |
| **Total** | | | | **+$365** |

**Entry #2 (10:45 ET): Full IC, C:6910/6960 P:6825/6775 — All Early-Closed (MKT-020 tightened calls)**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6910 C | Short | $1.40 | $0.25 (early close) | +$115 |
| LC 6960 C | Long | $0.10 | $0.00 (skipped) | -$10 |
| SP 6825 P | Short | $2.10 | $0.45 (early close) | +$165 |
| LP 6775 P | Long | $0.75 | $0.10 (early close) | -$65 |
| **Total** | | | | **+$205** |

**Entry #3 (11:05 ET): Call-only (MKT-011), C:6905/6955 — Call Stopped**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6905 C | Short | $1.55 | $3.10 (stopped) | -$155 |
| LC 6955 C | Long | $0.10 | $0.05 (stopped) | -$5 |
| **Total** | | | | **-$160** |

**Entry #4 (11:35 ET): Put-only (MKT-011), P:6815/6755 — Early-Closed**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SP 6815 P | Short | $1.35 | $0.30 (early close) | +$105 |
| LP 6755 P | Long | $0.35 | $0.10 (early close) | -$25 |
| **Total** | | | | **+$80** |

**P&L Reconciliation**: $365 + $205 + (-$160) + $80 = **$490 gross**. Commission: 12 open legs + 10 close legs (2 LC skipped at $0) = 22 × $2.50 = $55. **$490 - $55 = $435 net** ✓

### Feb 25 (Wednesday) - NET P&L: +$200

**Market**: Calm, range-bound. SPX range 29 pts (0.4%). VIX 19, declining intraday (19.39→18.64).

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC (MKT-020 tightened) | C:6955 P:6875 | $250 | EARLY CLOSED at 11:15 | +$160 |
| #2 | 10:35 | NEUTRAL | Full IC (MKT-020 tightened) | C:6945 P:6870 | $240 | EARLY CLOSED at 11:15 | +$80 |
| #3 | 11:05 | NEUTRAL | SKIPPED (MKT-011) | -- | -- | Call $5 non-viable + NEUTRAL = skip | -- |
| #4 | 11:35 | -- | SKIPPED (MKT-021) | -- | -- | ROC 2.0% >= 2.0% with 3 entries | -- |
| #5 | 12:05 | -- | SKIPPED (MKT-021) | -- | -- | ROC 2.0% >= 2.0% with 3 entries | -- |

**Key observations**:
- **First 0-stop day** in the entire 11-day period — both entries early-closed with full profit
- MKT-020 progressive call tightening triggered on both entries (50→30pt on #1, 50→25pt on #2) — VIX 18.87 produced low call premium at default OTM distance
- MKT-011 skipped Entry #3 (call credit $5.00 at 25pt OTM floor, far below $100 min — NEUTRAL market = skip per v1.3.6 rule)
- MKT-021 ROC gate fired simultaneously with MKT-018 at 11:15 — blocked entries #4/#5 after 3 entries attempted (2 placed + 1 skipped counts toward gate)
- MKT-018 early close (3rd live trigger) at 11:15 ET — ROC 2.0% = ($240-$40)/$10,000. Earliest early close of the period
- MKT-022 progressive put tightening triggered on Entry #3 (60→40pt OTM), but entry was still skipped by MKT-011
- MKT-013 overlap adjustment on Entry #3: put spread shifted 6875/6825 → 6865/6815 (same short put as Entry #1)
- Lowest capital deployed ($10,000) and lowest total credit ($490) of the entire period — only 2 entries
- Post-early-close: 4 deployments between 12:20-12:50 ET (v1.3.9→v1.3.10→v1.3.11), MKT-018 threshold raised from 2% to 3%

### Stop Timing Log (Feb 25 — No Stops, MKT-018 Early Close)

```
10:05:02 ET - Entry #1: Full IC (NEUTRAL), C:6955/7005 P:6875/6825
              EMA20=6927.31, EMA40=6924.86, divergence=+0.035% (deep NEUTRAL)
              MKT-020: Call tightened 50→30pt OTM (credit estimate: $107.50 ≥ $100)
              MKT-011: PASSED (Call $105, Put $140)
              Fills: LC 7005 @ $0.10, LP 6825 @ $0.60, SC 6955 @ $1.10, SP 6875 @ $2.10
              FIX-70 verified: Call credit $100, Put credit $150, Total $250
10:35:01 ET - Entry #2: Full IC (NEUTRAL), C:6945/6995 P:6870/6820
              EMA20=6924.01, EMA40=6925.16, divergence=-0.017% (deep NEUTRAL)
              MKT-020: Call tightened 50→25pt OTM (credit estimate: $135 ≥ $100)
              MKT-011: PASSED (Call $135, Put $105)
              Fills: LC 6995 @ $0.10, LP 6820 @ $0.40, SC 6945 @ $1.40, SP 6870 @ $1.50
              FIX-70 verified: Call credit $130, Put credit $110, Total $240
11:05:01 ET - Entry #3: SKIPPED by MKT-011
              EMA20=6923.53, EMA40=6923.15, divergence=+0.005% (deep NEUTRAL)
              MKT-013: Put spread shifted 6875/6825 → 6865/6815 (overlap with Entry #1)
              MKT-020: Call credit non-viable even at 25pt OTM floor ($5.00 < $100)
              MKT-022: Put tightened 60→40pt OTM (credit estimate: $100 ≥ $100)
              MKT-011: Call $5.00 non-viable + NEUTRAL market → SKIP
11:15:17 ET - MKT-021 ROC gate: ROC 2.0% >= 2.0% threshold (3 entries attempted)
              Entries #4/#5 blocked
11:15:17 ET - MKT-018 EARLY CLOSE TRIGGERED: ROC 2.0% >= 2.0% threshold
              Entry #1: 4 legs closed (SC 6955 C $0.35, LC 7005 C $0.05, SP 6875 P $0.80, LP 6825 P $0.20)
              Entry #2: 4 legs closed (SC 6945 C $1.15, LC 6995 C $0.05, SP 6870 P $0.70, LP 6820 P $0.20)
              2 entries, 8 legs closed, 0 failed
11:15 ET    - Daily summary fired immediately after early close
              Net P&L: $200.00, Commission: $40.00, Cumulative: $2,275
12:20-12:50 ET - Post-market deployments: v1.3.9 (MKT-021 gate=3), v1.3.10 (cum ROC cols), v1.3.11 (MKT-018 3%)
16:00 ET    - Settlement: FIX-71 caught duplicate (already sent at 11:15), no action
```

### Fill Price Detail (Feb 25 — Verified Against Saxo Closed Positions)

**Entry #1 (10:05 ET): Full IC, C:6955/7005 P:6875/6825 — All Early-Closed**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6955 C | Short | $1.10 | $0.35 (early close) | +$75 |
| LC 7005 C | Long | $0.10 | $0.05 (early close) | -$5 |
| SP 6875 P | Short | $2.10 | $0.80 (early close) | +$130 |
| LP 6825 P | Long | $0.60 | $0.20 (early close) | -$40 |
| **Total** | | | | **+$160** |

**Entry #2 (10:35 ET): Full IC, C:6945/6995 P:6870/6820 — All Early-Closed (MKT-020 tightened calls)**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6945 C | Short | $1.40 | $1.15 (early close) | +$25 |
| LC 6995 C | Long | $0.10 | $0.05 (early close) | -$5 |
| SP 6870 P | Short | $1.50 | $0.70 (early close) | +$80 |
| LP 6820 P | Long | $0.40 | $0.20 (early close) | -$20 |
| **Total** | | | | **+$80** |

**P&L Reconciliation**: $160 + $80 = **$240 gross**. Commission: 8 open legs + 8 close legs = 16 × $2.50 = $40. **$240 - $40 = $200 net** ✓

### Feb 26 (Thursday) - NET P&L: -$710

**Market**: Volatile sell-off with V-shape recovery. SPX dropped 77 pts to 6861 by 10:35, then recovered to close at 6907. VIX spiked from 17.60 to 20.54 intraday. First BEARISH EMA signal since Feb 17.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC (MKT-020 tightened) | C:6970 P:6870 | $450 | Call expired, PUT STOPPED 10:15 | -$220 |
| #2 | 10:35 | BEARISH | Call-only | C:6920 | $305 | CALL STOPPED 11:15 | -$345 |
| #3 | 11:05 | NEUTRAL | Full IC (MKT-020 tightened) | C:6940 P:6845 | $340 | Call expired, PUT STOPPED 12:49 | -$115 |
| #4 | 11:35 | NEUTRAL | SKIPPED (MKT-011) | -- | -- | Call $72.50 non-viable + NEUTRAL = skip | -- |
| #5 | 12:05 | NEUTRAL | Full IC (MKT-020 tightened) | C:6920 P:6825 | $250 | CALL STOPPED 14:16, Put expired | -$30 |

**Key observations**:
- **Worst loss day since Feb 17 (-$740)** — all 4 placed entries had at least one side stopped (0% win rate)
- **First BEARISH signal since Feb 17**: Entry #2 at -0.249% EMA divergence placed call-only. SPX was at ~6870 (near day's low) when entry placed. Market then V-shaped back above 6920, stopping the call. Same V-shape pattern as Feb 17
- **Entry #1 stopped in just 10 minutes**: Put side at 6870 stopped at 10:15, only 10 min after entry — SPX was still plunging at that point (hit 6861 low)
- MKT-020 progressive call tightening triggered on Entries #1 (50→45pt), #3 (50→45pt), #4 (50→40pt), #5 (55→40pt) — VIX 17.60 at open produced very low call premium
- MKT-011 skipped Entry #4: After MKT-020 tightened calls and MKT-013 shifted strikes for overlap, re-estimated call credit was only $72.50 (< $100 min). NEUTRAL market = skip per v1.3.6 rule
- MKT-013 overlap adjustment on Entry #4: short call 6940 overlapped Entry #3, shifted to 6945/6995
- VIX intraday spike from 17.60 to 20.54 (+17%) — biggest intraday VIX move of the period
- First full trading day with MKT-018 at 3% threshold (v1.3.11) — NOT triggered (ROC was negative all day)
- Entry #5 was nearly breakeven: call stopped for -$145, put expired for +$115, net -$30

### Stop Timing Log (Feb 26 — 4 Stops, No Early Close)

```
10:05:02 ET - Entry #1: Full IC (NEUTRAL), C:6970/7020 P:6870/6820
              EMA20=6922.96, EMA40=6930.70, divergence=-0.112% (NEUTRAL)
              MKT-020: Call tightened 50→45pt OTM (credit estimate: $127.50 ≥ $100)
              MKT-011: PASSED (Call $127.50, Put $372.50)
              Fills: SC 6970 @ $1.30, LC 7020 @ $0.10, SP 6870 @ $4.80, LP 6820 @ $1.50
              FIX-70 verified: Call credit $120, Put credit $330, Total $450
10:15:06 ET - Entry #1 PUT STOPPED: SP fill $9.20, LP fill $2.65
              Stop cost: ($9.20-$2.65)×100=$655, credit $330, net loss: -$325
              Call side survives (6970 short call, SPX at ~6870)
10:35:01 ET - Entry #2: Call-only (BEARISH), C:6920/6980
              EMA20=6874.82, EMA40=6891.99, divergence=-0.249% (BEARISH, first since Feb 17)
              MKT-011: PASSED (Call $310, Put $407.50)
              Fills: SC 6920 @ $3.30, LC 6980 @ $0.25
              FIX-70 verified: Call credit $305
11:05:06 ET - Entry #3: Full IC (NEUTRAL), C:6940/6990 P:6845/6795
              EMA20=6891.55, EMA40=6884.94, divergence=+0.096% (NEUTRAL)
              MKT-020: Call tightened 50→45pt OTM (credit estimate: $130 ≥ $100)
              MKT-011: PASSED (Call $130, Put $247.50)
              Fills: SC 6940 @ $1.45, LC 6990 @ $0.15, SP 6845 @ $3.00, LP 6795 @ $0.90
              FIX-70 verified: Call credit $130, Put credit $210, Total $340
11:15:41 ET - Entry #2 CALL STOPPED: SC fill $6.50, LC fill $0.10
              Stop cost: ($6.50-$0.10)×100=$640, credit $305, net loss: -$345
              SPX recovering from V-shape, crossed back above 6920
11:35:05 ET - Entry #4: SKIPPED by MKT-011
              EMA20=6901.35, EMA40=6898.49, divergence=+0.041% (NEUTRAL)
              MKT-020: Call tightened 50→40pt OTM (credit: $107.50 ≥ $100)
              MKT-013: Short call 6940 overlaps Entry #3, adjusted to 6945/6995
              MKT-011: Re-estimated call $72.50 < $100 non-viable + NEUTRAL → SKIP
12:05:01 ET - Entry #5: Full IC (NEUTRAL), C:6920/6975 P:6825/6775
              EMA20=6882.02, EMA40=6888.31, divergence=-0.091% (NEUTRAL)
              MKT-020: Call tightened 55→40pt OTM (credit estimate: $125 ≥ $100)
              MKT-011: PASSED (Call $127.50, Put $127.50)
              Fills: SC 6920 @ $1.40, LC 6975 @ $0.10, SP 6825 @ $1.60, LP 6775 @ $0.40
              FIX-70 verified: Call credit $130, Put credit $120, Total $250
12:49:40 ET - Entry #3 PUT STOPPED: SP fill $5.20, LP fill $0.80
              Stop cost: ($5.20-$0.80)×100=$440, credit $210, net loss: -$240
              Call side survives (6940 short call, SPX at ~6890)
14:16:45 ET - Entry #5 CALL STOPPED: SC fill $2.70, LC fill $0.05
              Stop cost: ($2.70-$0.05)×100=$265, credit $130, net loss: -$145
              Put side survives (6825 short put, SPX at ~6900)
17:00 ET    - Settlement: 6 positions expired worthless
              Entry #1 call expired: +$120 (SC 6970 C, LC 7020 C)
              Entry #3 call expired: +$130 (SC 6940 C, LC 6990 C)
              Entry #5 put expired: +$120 (SP 6825 P, LP 6775 P)
              Total expired credits: $370
              Daily summary: Net P&L -$710, Commission $55, Cumulative $1,565
```

### Fill Price Detail (Feb 26 — Verified Against Saxo Closed Positions)

**Entry #1 (10:05 ET): Full IC, C:6970/7020 P:6870/6820 — Call Expired, Put Stopped**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6970 C | Short | $1.30 | $0.00 (expired) | +$127.50 |
| LC 7020 C | Long | $0.10 | $0.00 (expired) | -$12.50 |
| SP 6870 P | Short | $4.80 | $9.20 (stopped) | -$445.00 |
| LP 6820 P | Long | $1.50 | $2.65 (stopped) | +$110.00 |
| **Total** | | | | **-$220** |

**Entry #2 (10:35 ET): Call-only (BEARISH), C:6920/6980 — Call Stopped**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6920 C | Short | $3.30 | $6.50 (stopped) | -$325.00 |
| LC 6980 C | Long | $0.25 | $0.10 (stopped) | -$20.00 |
| **Total** | | | | **-$345** |

**Entry #3 (11:05 ET): Full IC, C:6940/6990 P:6845/6795 — Call Expired, Put Stopped (MKT-020 tightened)**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6940 C | Short | $1.45 | $0.00 (expired) | +$142.50 |
| LC 6990 C | Long | $0.15 | $0.00 (expired) | -$17.50 |
| SP 6845 P | Short | $3.00 | $5.20 (stopped) | -$225.00 |
| LP 6795 P | Long | $0.90 | $0.80 (stopped) | -$15.00 |
| **Total** | | | | **-$115** |

**Entry #5 (12:05 ET): Full IC, C:6920/6975 P:6825/6775 — Call Stopped, Put Expired (MKT-020 tightened)**

| Leg | Direction | Open Price | Close Price | P&L |
|-----|-----------|-----------|-------------|-----|
| SC 6920 C | Short | $1.40 | $2.70 (stopped) | -$135.00 |
| LC 6975 C | Long | $0.10 | $0.05 (stopped) | -$10.00 |
| SP 6825 P | Short | $1.60 | $0.00 (expired) | +$157.50 |
| LP 6775 P | Long | $0.40 | $0.00 (expired) | -$42.50 |
| **Total** | | | | **-$30** |

**P&L Reconciliation**: (-$220) + (-$345) + (-$115) + (-$30) = **-$710 trade P&L**. Commission: 14 open legs + 8 close legs = 22 × $2.50 = $55. Identity: $370 - $1,025 - $55 = **-$710 net** ✓

---

## 4. Market Conditions

### Daily Market Character

| Date | Day | Market Type | SPX Change | SPX Range | VIX Level | Key Event |
|------|-----|-------------|------------|-----------|-----------|-----------|
| Feb 10 | Tue | Range-bound | -0.4% | 48 pts (0.7%) | 17 (low) | Rotation begins |
| Feb 11 | Wed | Flat | -0.7% | 77 pts (1.1%) | 17 (low) | Pre-Cisco/CPI wait |
| Feb 12 | Thu | **Strong downtrend** | **-1.8%** | **149 pts (2.1%)** | **17→21** | Cisco -12%, AI fears |
| Feb 13 | Fri | Consolidation | +0.03% | 90 pts (1.3%) | 21 (elevated) | CPI soft (2.4% vs 2.5%) |
| Feb 14-16 | -- | CLOSED | -- | -- | -- | Presidents' Day weekend |
| Feb 17 | Tue | **V-shape reversal** | +0.5% | **92 pts (1.3%)** | **22 (elevated)** | Post-holiday volatility |
| Feb 18 | Wed | Quiet recovery | +0.3% | 61 pts (0.9%) | 19→18 (normalizing) | VIX normalizing, low premium |
| Feb 19 | Thu | Choppy, downside | -0.2% | 41 pts (0.6%) | 19→20 (elevated) | VIX back up, fast put stops |
| Feb 20 | Fri | Wide range, V-shape | +0.3% | 76 pts (1.1%) | 20→19 (elevated→normal) | MKT-018 early close, high premium |
| **Feb 23** | **Mon** | **Sustained sell-off** | **-0.6%** | **94 pts (1.4%)** | **21→21 (elevated)** | **MKT-017 triggered, both puts stopped** |
| Feb 24 | Tue | Morning dip, recovery | +0.4% | 61 pts (0.9%) | 21→19 (normalizing) | MKT-018 early close (2nd), 4 versions deployed |
| Feb 25 | Wed | Range-bound, calm | +0.3% | 29 pts (0.4%) | 19 (low) | MKT-018 early close (3rd), fastest close at 11:15 |
| **Feb 26** | **Thu** | **V-shape sell-off/recovery** | **-0.4%** | **83 pts (1.2%)** | **18→19 (spiked to 20.5)** | **First BEARISH since Feb 17, 4 stops, worst day since Feb 17** |

### Expected Move vs Actual Range

| Date | VIX (avg) | Expected Move | Actual Range | Ratio | Assessment |
|------|-----------|--------------|--------------|-------|------------|
| Feb 10 | 17.4 | ~76 pts | 48 pts | 0.63x | Below expected (calm) |
| Feb 11 | 17.3 | ~76 pts | 77 pts | 1.01x | At expected (normal) |
| Feb 12 | 19.1 | ~84 pts | 149 pts | 1.77x | FAR above expected (extreme) |
| Feb 13 | 20.8 | ~90 pts | 90 pts | 1.00x | At expected (normal) |
| Feb 17 | 21.4 | ~92 pts | 92 pts | 1.00x | At expected (normal) |
| Feb 18 | 19.1 | 83 pts | 61 pts | 0.73x | Below expected (calm) |
| Feb 19 | 19.6 | 85 pts | 41 pts | 0.48x | Far below expected (compressed) |
| Feb 20 | ~20.0 | ~87 pts | 76 pts | 0.87x | Near expected (normal) |
| **Feb 23** | **21.0** | **91 pts** | **94 pts** | **1.03x** | **At expected (normal)** |
| Feb 24 | 20.1 | 87 pts | 61 pts | 0.70x | Below expected (calm) |
| Feb 25 | 19.0 | 83 pts | 29 pts | 0.35x | Far below expected (very calm) |
| **Feb 26** | **18.1** | **79 pts** | **83 pts** | **1.05x** | **At expected (normal)** |

**Key insight**: Feb 17 was NOT an abnormal range day. The 92-point range was exactly at its expected move. The damage came from the SHAPE (V-reversal), not the MAGNITUDE.

### Macro Context (Week of Feb 10-13)
- AI disruption sell-off triggered by Cisco earnings miss (AI component costs squeezing margins)
- Sector rotation: tech/growth → value/defensives
- VIX broke above 20 on Feb 12 for first time in weeks
- CPI came in soft on Feb 13 but market shrugged it off
- S&P 500 failed at 7,000 resistance and entered downtrend
- Post-Presidents' Day (Feb 17) had pent-up information and volatile reopening

---

## 5. Key Performance Metrics

### Financial Metrics (12 days: Feb 10-26)

| Metric | Value |
|--------|-------|
| Total Credit Collected | $15,925 |
| Total Expired Credits | $9,185 (57.7% of credit) |
| Total Stop Loss Debits | $7,030 (44.1% of credit) |
| Total Commission | $590 (3.7% of credit) |
| Net P&L | +$1,565 (9.8% net capture rate) |
| Average Daily Credit | $1,327 |
| Average Daily P&L | +$130 |
| Best Day | +$690 (Feb 20) |
| Worst Day | -$740 (Feb 17) |
| Win/Loss Day Ratio | 8:4 |
| Win/Loss Dollar Ratio | 1.83:1 ($3,450 / $1,885) |

### Entry Performance

| Metric | Value |
|--------|-------|
| Total Entries | 50 |
| Clean Wins (0 stops) | 22 (44.0%) |
| Partial Wins (1 side stopped, IC) | 18 (36.0%) |
| Full Losses (stopped, 1-sided) | 10 (20.0%) |
| Entries with Call Stop | 7 (14.0%) |
| Entries with Put Stop | 21 (42.0%) |
| Double Stops | 0 (0%) |

### Entry Type Distribution

| Entry Type | Count | Stops | Stop Rate | Avg Credit |
|------------|-------|-------|-----------|------------|
| Full IC | 26 | 18 sides stopped* | ~35% per side | $443 |
| Put-only (MKT-011) | 16 | 8 | 50.0% | $142 |
| Call-only (trend/MKT-011) | 6 | 5 | 83.3% | $222 |
| Put-only (trend) | 2 | 2 | 100% | $333 |

*Full ICs can have 0, 1, or 2 sides stopped.

### Stop Clustering Data

| Date | Stops | Fastest Cluster | Entries After Cluster | Loss After Cluster |
|------|-------|----------------|-----------------------|-------------------|
| Feb 10 | 1 | N/A (single) | N/A | N/A |
| Feb 11 | 2 | 3 hours apart | 0 | $0 |
| Feb 12 | 4 | Spread throughout day | 0 (all entries placed before stops) | $0 |
| Feb 13 | 3 | Spread throughout day | 0 | $0 |
| **Feb 17** | **5** | **3 in 11 minutes** | **2 entries placed after** | **-$255** |
| Feb 18 | 2 | Simultaneous (13:53) | 0 (Entry #5 skipped by MKT-011 before stops) | $0 |
| Feb 19 | 3 | 3 in 98 min (10:07-11:45) | 1 (#5 blocked by MKT-016) | $0 (saved) |
| Feb 20 | 1 | N/A (single) | 0 (MKT-018 early close + MKT-021 gate) | $0 |
| Feb 23 | 2 | 70 sec apart (11:01-11:03) | 3 (#3-5 blocked by MKT-017) | $0 (saved) |
| Feb 24 | 1 | N/A (single, Entry #3 call) | 0 (all entries already placed/skipped) | $0 |
| Feb 25 | 0 | N/A (no stops) | N/A | $0 |
| **Feb 26** | **4** | **Spread throughout day (1-1.5h intervals)** | **0 (all entries placed before 3rd stop)** | **$0** |

### Trend Filter Accuracy

| Date | Trend Signals | Were They Correct? | Trend Filter Impact |
|------|--------------|--------------------|--------------------|
| Feb 10 | 5 NEUTRAL | Yes (range-bound) | Neutral - MKT-011 overrode anyway |
| Feb 11 | 5 NEUTRAL, 1 BEARISH | Partially (market was slightly bearish) | Slightly positive |
| Feb 12 | 4 NEUTRAL, 2 BEARISH | Yes - BEARISH correct (major sell-off) | **STRONG POSITIVE** - saved ~$300 |
| Feb 13 | 4 NEUTRAL, 1 BULLISH | Mixed - BULLISH call was right but put got stopped anyway | Neutral |
| Feb 17 | 3 NEUTRAL, 1 BEARISH, 1 BULLISH | **WRONG** - both directional calls were reversed | **STRONG NEGATIVE** - amplified losses |
| Feb 18 | 4 NEUTRAL | Yes (range-bound, max 0.034%) | Neutral - MKT-011 overrode anyway |
| Feb 19 | 4 NEUTRAL | Yes (choppy, max -0.084%) | Neutral - MKT-011 overrode #3/#4 |
| Feb 20 | 3 NEUTRAL | Yes (wide range but neutral EMA) | Neutral — all full ICs, MKT-018 early close locked in profit |
| Feb 23 | 2 NEUTRAL | Yes (sustained sell-off, EMA divergence -0.085% to -0.094%) | Neutral — full ICs, but call sides survived while puts stopped |
| Feb 24 | 4 NEUTRAL | Yes (choppy morning, stabilized afternoon) | Neutral — MKT-011 overrode #3/#4 to one-sided, MKT-011 v1.3.6 skipped #5 |
| Feb 25 | 2 NEUTRAL | Yes (calm, range-bound, SPX +0.3%) | Neutral — all signals deep NEUTRAL, MKT-011 skipped #3 (call non-viable) |
| **Feb 26** | **3 NEUTRAL, 1 BEARISH** | **Mixed — BEARISH at 10:35 correct (SPX -77pts) but V-shaped, call stopped** | **NEGATIVE — BEARISH call-only stopped (-$345), full IC would have had wider MKT-019 stop** |

---

## 6. Identified Weaknesses

### Weakness 1: EMA Trend Filter Whipsaws on V-Shaped Days
- **Evidence**: Feb 17 - BEARISH at low (10:05), BULLISH at high (11:35), both wrong
- **Root cause**: 20/40 EMA on 1-min bars has ~20-40 min lag
- **Impact**: One-sided entries placed on wrong side = 100% loss of premium
- **Frequency**: V-shape reversals occur on ~15-20% of trading days

### Weakness 2: No Stop Cascade Circuit Breaker
- **Evidence**: Feb 17 - 3 stops in 11 min, then 2 more entries placed into hostile market
- **Root cause**: Each entry decision is independent, no awareness of recent stops
- **Impact**: ~$195 in net avoidable losses on Feb 17 (Entry #4: +$235 saved, Entry #5: -$40 cost since it was a net winner)
- **Frequency**: Stop cascades (3+ in 15 min) are rare but devastating

### Weakness 3: One-Sided Entries Have Binary Outcomes
- **Evidence**: 4 call-only entries: 3 stopped (75%). 2 put-only trend entries: 2 stopped (100%)
- **Root cause**: No hedge side to absorb partial loss
- **Impact**: When trend filter is wrong, entire premium is lost
- **Frequency**: Every trend-filtered entry

### Weakness 4: No Post-Holiday Adjustment
- **Evidence**: Feb 17 was first day after 3-day weekend, had highest VIX spike (22.96)
- **Root cause**: No awareness of calendar context
- **Impact**: Standard position sizing in above-average volatility
- **Frequency**: ~5 three-day weekends per year

### Weakness 5: Entry #5 (12:05) Has Higher Stop Rate
- **Evidence**: Stopped 3/5 days (60% vs 55.6% overall average)
- **Root cause**: By 12:05, market has already moved significantly from open
- **Impact**: Late entries face compressed time-to-expiry with less theta protection
- **Frequency**: Every trading day

---

## 7. Recommended Improvements (Revised After Code Audit - Feb 17, 2026)

**Note**: These recommendations were revised after a thorough code audit that corrected several impact estimates from the initial analysis. Key corrections: (1) blocking a full IC entry blocks BOTH sides, including the surviving side that would have expired profitably — savings must be calculated NET, not gross; (2) Rec 9.2 (cooldown) and Rec 9.1 (cascade) interact — cooldown can prevent the stop that would trigger the cascade breaker; (3) Entry #5 on Feb 17 was actually a net winner (+$40), not a loser.

### Priority Ranking (Revised)

| Rank | Rec | Name | Impact | Complexity | Feb 17 Net Savings | Status |
|------|-----|------|--------|------------|-------------------|--------|
| **1** | 9.3 | **Widen EMA Threshold (0.1% → 0.2%)** | **HIGHEST** | **ZERO (config only)** | **~$290** | **IMPLEMENTED (v1.2.8)** |
| **2** | 9.1 | **Daily Stop Cascade Breaker** | **HIGH** | **LOW (~25 lines)** | **~$195** | **IMPLEMENTED (v1.2.8)** |
| 3 | 9.4 | Trend Persistence Requirement | MEDIUM | MEDIUM (~15 lines) | ~$330* | Deferred (monitor first) |
| 4 | 9.2 | Stop Cooldown Timer | LOW-MEDIUM | LOW (~20 lines) | ~$80 | Deferred (largely redundant with #1+#2) |
| 5 | 9.5 | Intraday Range Awareness | MEDIUM | MEDIUM (~50 lines) | ~$100 est. | Deferred (needs more data) |
| 6 | 9.6 | Post-Holiday Caution Mode | LOW-MEDIUM | LOW (~15 lines) | ~$100 est. | Deferred |
| 7 | 9.7 | Monitor Entry #5 Performance | UNCERTAIN | TRIVIAL (monitoring) | N/A | Ongoing |

*Rec 9.4 savings overlap with Rec 9.3 — they address the same root cause. Combined ≠ additive.

### Detailed Specifications (Revised)

#### Rec 9.3 (PRIORITY #1): Widen EMA Neutral Threshold

**Change**: `trend_filter.ema_neutral_threshold` from `0.001` (0.1%) to `0.002` (0.2%)

**What it does**: Raises the bar for classifying a market as BULLISH or BEARISH. More entries default to NEUTRAL (full IC) instead of one-sided bets. Full ICs have a built-in hedge — when one side is stopped, the other side often survives and partially offsets the loss.

**Why it's #1**:
- Addresses the **root cause** of Feb 17's loss: wrong directional bets from whipsaw EMA signals
- **Zero code changes** — update one value in `config.json` on the VM
- On Feb 17: Entry #1 (BEARISH→call-only) would likely have been NEUTRAL→full IC. The surviving side would have partially offset the stop loss. Entry #4 would also reclassify, but MKT-016 cascade blocks it (3 stops already reached). **Saves ~$290** (Entry #1 only).
- On Feb 12 (genuine sell-off, 149pts): EMA divergence was >0.3%, so BEARISH signals would still fire at 0.2%. **Zero cost on genuine trending days.**

**Code verification**: Line 194 of `bots/hydra/strategy.py` reads `self.trend_config.get("ema_neutral_threshold", 0.001)`. Lines 294-299 use strict `>` and `<` operators. Pure config change.

**Risk**: If a genuine but small trend has 0.1%-0.2% EMA divergence, the bot would place a full IC instead of one-sided, adding exposure on the risky side. Mitigated by the fact that small trends rarely breach short strikes.

**Implementation**:
```bash
# Edit config on VM: change "ema_neutral_threshold": 0.001 to 0.002
# Restart bot
```

#### Rec 9.1 (PRIORITY #2): Daily Stop Cascade Breaker

**Trigger**: When `daily_state.call_stops_triggered + daily_state.put_stops_triggered >= 3`
**Action**: Skip all remaining entry attempts for the day
**Where**: Check in `_handle_monitoring()` or `_should_attempt_entry()` before entry placement

**Why it's #2**:
- **Zero downside on good days** — on Feb 10 (1 stop), Feb 11 (2 stops), it never triggers
- On Feb 12 (4 stops, profitable): All entries were placed BEFORE any stops triggered, so cascade breaker would NOT have affected the result
- On Feb 17: Blocks Entry #4 (saves $235 net) and Entry #5 (costs $40 net — Entry #5 was actually a net winner because its call side expired). **Net savings: ~$195.**

**Feb 17 detailed impact (corrected from initial analysis)**:

| Entry | If Blocked | Stop Debit Saved | Expired Credit Lost | Commission Saved | Net Impact |
|-------|-----------|-----------------|--------------------|-----------------|-----------|
| #4 (put-only) | Blocked | +$225 | $0 (nothing to expire) | +$10 | **+$235 saved** |
| #5 (full IC) | Blocked | +$30 | -$85 (call side expired) | +$15 | **-$40 cost** |
| **Combined** | | | | | **+$195 net saved** |

**Code location**: MEICDailyState already has `call_stops_triggered` and `put_stops_triggered` counters (line 501-503 of `bots/meic/strategy.py`). No new fields needed. Add check in `_handle_monitoring()` after `_check_stop_losses()` (line ~1455) before `_should_attempt_entry()`.

**Config key**: `max_daily_stops_before_pause: 3`

#### Rec 9.4 (DEFERRED): Trend Persistence Requirement

**Status**: Deferred — implement only if Rec 9.3 alone doesn't catch whipsaw signals.

**Rule**: Only act on BULLISH/BEARISH if previous entry window had the same signal. If signals disagree, default to NEUTRAL (full IC).

**Logic**: A single EMA cross can be noise. If the trend is real, it will persist across multiple readings. If it's noise, it will flip back. This filters out temporary EMA crosses.

**Why deferred**: Rec 9.3 (wider threshold) already addresses most whipsaw signals. Persistence adds value ONLY when EMA divergence exceeds 0.2% but the trend is still temporary (e.g., sharp 30-point move that reverses). Monitor for 2 weeks after implementing Rec 9.3 — if whipsaw signals still occur above 0.2%, then add persistence.

**Design note**: If implemented, the persistence check should compare the CURRENT EMA signal against the PREVIOUS ENTRY'S raw `trend_signal` field (already stored in state file at line 1908, restored at line 3046). This way, if Entry #3 reads BEARISH but is forced to NEUTRAL (no persistence), its stored `trend_signal = BEARISH`. When Entry #4 also reads BEARISH, persistence sees match → allows BEARISH. This preserves trend response on genuine multi-entry trends like Feb 12.

**Feb 12 cost**: Entry #3 would be forced to NEUTRAL (prev was NEUTRAL), losing its call-only advantage (~$185). Entry #4 would stay BEARISH (prev signal was BEARISH). Net cost on Feb 12: ~$185.

**Implementation**: ~15 lines in `_initiate_entry()`, after `_get_trend_signal()`. Previous entries accessible via `self.daily_state.entries[-1].trend_signal`.

#### Rec 9.2 (DEFERRED): Stop Cooldown Timer

**Status**: Deferred — largely redundant if Rec 9.3 + Rec 9.1 are implemented.

**Why demoted**: Code audit revealed two issues:
1. **Net savings overstated**: On Feb 17, blocking Entry #3 (full IC) saves the call stop ($265) but also loses the surviving put side's expiration credit (~$200). Net savings: **~$80, not $265**.
2. **Conflicts with cascade breaker**: If cooldown blocks Entry #3, its stop never occurs, so only 2 total stops by 11:35. The cascade breaker (threshold 3) does NOT trigger, and Entry #4 still gets placed and stopped (-$225). **Combined cooldown+cascade = $80 saved. Cascade alone = $195 saved. Cascade alone is BETTER.**

**Implement only if**: You observe entries being placed within minutes of stops and the cascade breaker alone doesn't prevent it. MEICDailyState currently has NO timestamp fields (code audit confirmed) — `last_stop_timestamp` would need to be added.

#### Rec 9.5 (DEFERRED): Intraday Range Awareness

**Status**: Deferred — implement after 2-3 weeks with Recs 9.3+9.1 in place.

**What it does**: Before each entry, check if `SPX intraday range / expected daily move > 0.75`. If yes, widen short strikes by +10 pts OTM.

**Code verification**: `self.market_data.get_spx_range()` already exists (line 648-652 of `bots/meic/strategy.py`). Expected move calculation available in strike selection. Infrastructure is ready.

**Trade-off**: Lower premium ($0.30-$0.50 less credit per side) on volatile days, but higher survival probability.

#### Rec 9.6 (DEFERRED): Post-Holiday Caution Mode

**Status**: Low priority. The cascade breaker (#2) already provides protection regardless of calendar context.

**What it does**: On first trading day after 3-day weekend, reduce entries from 5 to 3.
**Frequency**: ~5 times per year. Low effort, targeted protection.

#### Rec 9.7 (REVISED): Monitor Entry #5 Performance

**Status**: Ongoing monitoring — DO NOT remove Entry #5 yet.

**Correction from initial analysis**: Entry #5 on Feb 17 was actually a **net winner** (+$40: call expired, put loss only $30). Over 5 days, Entry #5 is approximately **breakeven to slightly positive** (+$45 estimated). The original claim of "60% stop rate = negative expected value" was wrong because it counted gross stops without accounting for surviving sides in full ICs.

**5 days is far too few** to make a structural change. Continue tracking for 20+ trading days.

---

## 8. Improvement Implementation Log

Track when each improvement was implemented, deployed, and verified.

| Date | Rec | Change Made | Commit | Deployed | Notes |
|------|-----|------------|--------|----------|-------|
| 2026-02-17 | 9.3 | EMA threshold 0.001→0.002 in config template + cloud config | v1.2.8 commits | 2026-02-17 post-market | Zero code change, config-only |
| 2026-02-17 | 9.1 | MKT-016 stop cascade breaker (3 stops → pause) | v1.2.8 commits | 2026-02-17 post-market | ~25 lines in strategy.py, config key added |
| 2026-02-18 | -- | MKT-017 daily loss limit (-$500 realized P&L → pause) | v1.2.9 commits | 2026-02-18 post-market | Complements MKT-016 (count) with magnitude check |
| 2026-02-19 | -- | MKT-018 early close (ROC >= 2% → close all positions) | v1.3.0 commits | 2026-02-19 pre-market | Locks in profit on high-ROC days, ~200 lines |
| 2026-02-20 | -- | MKT-020 progressive call OTM tightening (min $1.00/side credit) | v1.3.2 commits | 2026-02-20 pre-market | Tightens call strikes when call credit < $1.00/side |
| 2026-02-20 | -- | MKT-021 pre-entry ROC gate (skip new entries if ROC >= 2%) | v1.3.2 commits | 2026-02-20 pre-market | Prevents adding exposure after early close threshold reached |
| 2026-02-20 | -- | Fix #81: Early close 409 Conflict retry | v1.3.2 commits | 2026-02-20 pre-market | Retry on 409 during early close position closing |
| 2026-02-23 | -- | Remove MKT-016 (cascade) + MKT-017 (loss limit) + base loss limit | v1.3.3 commits | 2026-02-23 post-market | Bot always attempts all 5 entries |
| 2026-02-23 | -- | Fix #82: Settlement gate lock bug | v1.3.4 commits | 2026-02-23 post-market | Midnight reset locked gate, preventing 4 PM settlement |
| 2026-02-24 | -- | MKT-022 progressive put OTM tightening | v1.3.5 commits | 2026-02-24 pre-market | Mirrors MKT-020 for put side, min $1.00/side credit |
| 2026-02-24 | -- | MKT-011 v1.3.6 NEUTRAL skip rule | v1.3.6 commits | 2026-02-24 mid-day | One side non-viable + NEUTRAL = skip entire entry |
| 2026-02-24 | -- | MKT-023 smart hold check before early close | v1.3.7 commits | 2026-02-24 mid-day | Compares close-now vs worst-case-hold P&L |
| 2026-02-24 | -- | Fix #83: FIX-71 idempotency guard poisoned by midnight settlement | v1.3.8 commits | 2026-02-24 post-market | Clock time → trading date for last_updated check |
| 2026-02-25 | -- | MKT-021 ROC gate lowered from 5 to 3 entries | v1.3.9 commits | 2026-02-25 post-early-close | Gate fires after 3 entries instead of 5 |
| 2026-02-25 | -- | Cumulative ROC columns in Daily Summary | v1.3.10 commits | 2026-02-25 post-early-close | Adds Cum ROC and ROC columns to Sheets |
| 2026-02-25 | -- | MKT-018 threshold raised 2%→3% | v1.3.11 commits | 2026-02-25 post-early-close | Based on 11-day analysis showing 2% left $1,025 on table |

---

## 9. Post-Improvement Performance Tracking

### How to Do a Weekly Review

**Step 1: Pull daily summary data from Google Sheets**
- Open the "Calypso_HYDRA_Live_Data" spreadsheet → "Daily Summary" tab
- Copy the rows for the review period into the template table below

**Step 2: Pull EMA divergence data from VM logs**
```bash
# Get all trend signal logs for a specific date (replace DATE)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since 'DATE 14:00' --until 'DATE 21:00' --no-pager | grep -E '(EMA|trend_signal|divergence|BULLISH|BEARISH|NEUTRAL|cascade|MKT-016)'"
```
Note: journalctl timestamps are UTC. Market hours 9:30-4:00 ET = 14:30-21:00 UTC.

**Step 3: Check if cascade breaker triggered**
```bash
# Look for MKT-016 cascade events
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since 'DATE 14:00' --until 'DATE 21:00' --no-pager | grep -i 'cascade\|MKT-016\|pause.*entry\|skipping.*entry'"
```

**Step 4: Check state file for EMA values (most precise)**
```bash
# View today's state file (has exact ema_20_at_entry / ema_40_at_entry)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso cat /opt/calypso/data/hydra_state.json | python3 -m json.tool"
```

**Step 5: Check cumulative metrics**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso cat /opt/calypso/data/meic_metrics.json | python3 -m json.tool"
```

**Step 6: Fill in the template table and improvement assessment below, then update this document.**

### Template for Future Analysis

When reviewing performance after implementing improvements, fill in this section with new data:

#### Post-Improvement Day 1: Feb 18 (v1.2.8)

| Column | Feb 18 |
|--------|--------|
| Date | 2026-02-18 |
| SPX Open | 6848.12 |
| SPX Close | 6869.91 |
| SPX Range | 61 pts (0.9%) |
| VIX Open | 19.73 |
| VIX Close | 18.48 |
| Entries | 4 (+1 skipped) |
| Full ICs | 1 |
| One-Sided | 3 (MKT-011 put-only) |
| Total Credit | $810 |
| Call Stops | 0 |
| Put Stops | 2 |
| Stop Debits | $260 |
| Commission | $35 |
| Expired Credits | $610 |
| Daily P&L | +$315 |
| Cumulative P&L | $1,385 |

#### Improvement Impact Assessment — Feb 18

| Rec | Implemented? | Triggered? | Actual Impact | Assessment |
|-----|-------------|------------|---------------|------------|
| 9.3 EMA Threshold (0.2%) | v1.2.8 | **NO** — all signals were deep NEUTRAL (max 0.034%) | $0 (no directional signals to filter) | Cannot assess on calm day |
| 9.1 Stop Cascade (3 stops) | v1.2.8 | **NO** — only 2 stops (threshold is 3) | $0 (never triggered) | Cannot assess — would need 3+ stops |
| MKT-017 Daily Loss Limit | v1.2.9 | Not yet deployed (deployed post-market) | N/A | First active day: Feb 19 |
| MKT-018 Early Close (ROC) | v1.3.0 | Not yet deployed (deployed Feb 19 pre-market) | N/A | First active day: Feb 19 |
| 9.4 Trend Persistence | Deferred | | | |
| 9.2 Stop Cooldown | Deferred | | | |
| 9.5 Range Awareness | Deferred | | | |
| 9.6 Holiday Caution | Deferred | | | |
| 9.7 Entry #5 Monitor | Ongoing | Entry #5 SKIPPED by MKT-011 | Cannot assess — skipped before placement | |

**Feb 18 Assessment**: Calm, range-bound day with low VIX. Neither Rec 9.3 (EMA threshold) nor Rec 9.1 (cascade breaker) had an opportunity to demonstrate impact — no directional signals were generated and only 2 stops occurred. The real test will come on a volatile day with V-shaped reversals or fast stop cascades.

#### Post-Improvement Day 2: Feb 19 (v1.3.0)

| Column | Feb 19 |
|--------|--------|
| Date | 2026-02-19 |
| SPX Open | 6872.48 |
| SPX Close | 6861.00 |
| SPX Range | 41 pts (0.6%) |
| VIX Open | 19.00 |
| VIX Close | 20.28 |
| Entries | 4 (+1 skipped by MKT-016) |
| Full ICs | 2 |
| One-Sided | 2 (MKT-011 put-only) |
| Total Credit | $1,265 |
| Call Stops | 0 |
| Put Stops | 3 |
| Stop Debits | $380 |
| Commission | $45 |
| Expired Credits | $395 |
| Daily P&L | -$30 |
| Cumulative P&L | $1,355 |

#### Improvement Impact Assessment — Feb 19

| Rec | Implemented? | Triggered? | Actual Impact | Assessment |
|-----|-------------|------------|---------------|------------|
| 9.3 EMA Threshold (0.2%) | v1.2.8 | **NO** — all signals deep NEUTRAL (max ~-0.084%) | $0 (no directional signals to filter) | Cannot assess on NEUTRAL day |
| 9.1 Stop Cascade (3 stops) | v1.2.8 | **YES** — 3rd stop at 11:45, blocked Entry #5 | **Prevented 1 entry** (unknown savings — depends on whether Entry #5 would have been stopped) | **FIRST REAL TRIGGER** |
| MKT-017 Daily Loss Limit | v1.2.9 | **NO** — realized P&L = -$380, threshold = -$500, buffer of $120 | $0 (never triggered) | Close but not triggered — calibration appears correct |
| MKT-018 Early Close (ROC) | v1.3.0 | **NO** — ROC was negative all day (~-0.26% at 3:59 PM vs 2.0% threshold) | $0 (never triggered) | Cannot assess on losing day — designed for profitable days |
| 9.4 Trend Persistence | Deferred | | | |
| 9.2 Stop Cooldown | Deferred | | | |
| 9.5 Range Awareness | Deferred | | | |
| 9.6 Holiday Caution | Deferred | | | |
| 9.7 Entry #5 Monitor | Ongoing | Entry #5 SKIPPED by MKT-016 | Cannot assess — blocked by cascade | |

**Feb 19 Assessment**: First day where MKT-016 cascade breaker actually triggered in live trading. Three put stops in 98 minutes (10:07, 10:51, 11:45) triggered the 3-stop threshold, blocking Entry #5 at 12:05. This prevented adding more exposure to a day that was already generating losses. Without the cascade breaker, Entry #5 would have been placed — possibly another put-only (MKT-011) that could have been stopped given the put-heavy loss pattern. MKT-017 daily loss limit came within $120 of triggering (-$380 vs -$500 threshold) — the calibration from Feb 13's -$450 trough is holding. MKT-018 early close had no opportunity on a losing day (designed for profitable days). The -$30 result was near breakeven — expired credits ($395) nearly covered stop debits ($380), with commission ($45) making it a small loss.

#### Post-Improvement Day 3: Feb 20 (v1.3.2)

| Column | Feb 20 |
|--------|--------|
| Date | 2026-02-20 |
| SPX Open | 6857.52 |
| SPX Close | 6878.72 |
| SPX Range | 76 pts (1.1%) |
| VIX Open | 20.46 |
| VIX Close | 19.54 |
| Entries | 3 (+2 skipped by MKT-021) |
| Full ICs | 3 |
| One-Sided | 0 |
| Total Credit | $1,775 |
| Call Stops | 0 |
| Put Stops | 1 |
| Stop Debits | $800 |
| Commission | $60 |
| Expired Credits | $1,550 |
| Daily P&L | +$690 |
| Cumulative P&L | $2,045 |
| Early Close | Yes, 11:31 ET (MKT-018) |

#### Improvement Impact Assessment — Feb 20

| Rec | Implemented? | Triggered? | Actual Impact | Assessment |
|-----|-------------|------------|---------------|------------|
| 9.3 EMA Threshold (0.2%) | v1.2.8 | **NO** — all signals deep NEUTRAL | $0 (no directional signals to filter) | Cannot assess on NEUTRAL day |
| 9.1 Stop Cascade (3 stops) | v1.2.8 | **NO** — only 1 stop (threshold is 3) | $0 (never triggered) | Not needed — MKT-018 closed positions before cascade could develop |
| MKT-017 Daily Loss Limit | v1.2.9 | **NO** — day was profitable | $0 (never triggered) | N/A on profitable day |
| MKT-018 Early Close (ROC) | v1.3.0 | **YES** — ROC hit 2% at 11:31 ET | **FIRST LIVE TRIGGER** — locked in $690 net profit | **STRONG POSITIVE** — prevented giving back gains |
| MKT-020 Call Tightening | v1.3.2 | **YES** — tightened Entry #3 call | Ensured min $1.00/side credit on call side | Positive — maintained viable credit on call spread |
| MKT-021 ROC Gate | v1.3.2 | **YES** — blocked Entries #4 and #5 | **Prevented 2 entries** after early close threshold reached | **POSITIVE** — prevented adding new exposure while closing existing |
| 9.4 Trend Persistence | Deferred | | | |
| 9.2 Stop Cooldown | Deferred | | | |
| 9.5 Range Awareness | Deferred | | | |
| 9.6 Holiday Caution | Deferred | | | |
| 9.7 Entry #5 Monitor | Ongoing | Entry #5 SKIPPED by MKT-021 | Cannot assess — blocked by ROC gate | |

**Feb 20 Assessment**: Landmark day — first live trigger of MKT-018 early close. Three NEUTRAL full ICs placed between 10:05-11:05 collected $1,775 in premium. Entry #3's put side was stopped (~$225 net side loss), but Entry #1 and #2 were solidly profitable. When cumulative ROC hit 2% at 11:31 ET, MKT-018 closed all remaining positions, locking in +$690 net profit. Without early close, the positions would have continued to expiration — Entry #1 and #2 likely would have expired fully worthless (even more profit), but the market's 76-point range and V-shape character meant additional risk of late-day reversals. MKT-021 pre-entry ROC gate complemented MKT-018 by preventing Entries #4 and #5 from being placed — no point opening new positions when the bot is about to close everything. The 409 Conflict error on Entry #1's long call close during early close (likely concurrent SaxoTraderGO operation) required manual intervention, suggesting Fix #81's retry logic may need further hardening. Best P&L day since Feb 13 (+$675), and highest daily ROC of the entire period at 4.6%.

#### Post-Improvement Day 4: Feb 23 (v1.3.2 — last day with MKT-016/017)

| Column | Feb 23 |
|--------|--------|
| Date | 2026-02-23 |
| SPX Open | 6877.47 |
| SPX Close | 6836.90 |
| SPX Range | 94 pts (1.4%) |
| VIX Open | 20.56 |
| VIX Close | 21.35 |
| Entries | 2 (+3 skipped by MKT-017) |
| Full ICs | 2 |
| One-Sided | 0 |
| Total Credit | $915 |
| Call Stops | 0 |
| Put Stops | 2 |
| Stop Debits | $655 |
| Commission | $30 |
| Expired Credits | $280 |
| Daily P&L | -$405 |
| Cumulative P&L | $1,640 |
| Early Close | No (ROC -3.54%, negative all day) |

#### Improvement Impact Assessment — Feb 23

| Rec | Implemented? | Triggered? | Actual Impact | Assessment |
|-----|-------------|------------|---------------|------------|
| 9.3 EMA Threshold (0.2%) | v1.2.8 | **NO** — both signals deep NEUTRAL (-0.085%, -0.094%) | $0 (no directional signals to filter) | Cannot assess on NEUTRAL day |
| 9.1 Stop Cascade (3 stops) | v1.2.8 | **NO** — only 2 stops (threshold is 3) | $0 (never triggered) | MKT-017 triggered first (at 2 stops) |
| MKT-017 Daily Loss Limit | v1.2.9 | **YES** — realized P&L -$655 < -$500 threshold at 11:03 ET | **FIRST LIVE TRIGGER** — blocked 3 remaining entries | **POSITIVE** — prevented more entries into sell-off |
| MKT-018 Early Close (ROC) | v1.3.0 | **NO** — ROC was negative all day (-3.54% at close) | $0 (never triggered) | N/A on losing day |
| MKT-020 Call Tightening | v1.3.2 | **NO** — both entries had sufficient call credit ($145, $135 > $100) | $0 | Not needed — VIX elevated enough for decent call premium |
| MKT-021 ROC Gate | v1.3.2 | **NO** — ROC was negative all day | $0 (never triggered) | N/A on losing day |
| 9.4 Trend Persistence | Deferred | | | |
| 9.2 Stop Cooldown | Deferred | | | |
| 9.5 Range Awareness | Deferred | | | |
| 9.6 Holiday Caution | Deferred | | | |
| 9.7 Entry #5 Monitor | Ongoing | Entry #5 SKIPPED by MKT-017 | Cannot assess — blocked by loss limit | |

**Feb 23 Assessment**: First live trigger of MKT-017 daily loss limit. Two put stops in 70 seconds (11:01:42 and 11:02:52) as SPX sold off sharply pushed realized P&L to -$655, exceeding the -$500 threshold. MKT-017 blocked Entries #3-5 — all three would have been placed into a declining market with elevated put risk. Note that MKT-016 cascade breaker did NOT trigger (only 2 stops, threshold is 3), but MKT-017 caught the situation because the *magnitude* of losses was high even with only 2 stops. This validates the dual approach: MKT-016 catches many small stops (cascade), MKT-017 catches few large stops (magnitude). However, both MKT-016 and MKT-017 were removed post-market (v1.3.3) — the user decided the bot should always attempt all 5 entries. The call sides of both entries survived with 95-100% cushion and expired worthless, adding $280 in expired credits. Fix #82 (settlement gate lock) was discovered when the midnight settlement failed to process these expired credits, recording -$685 instead of -$405. The bug was fixed in v1.3.4.

#### Post-Improvement Day 5: Feb 24 (v1.3.5→v1.3.8 — first day without MKT-016/017, + MKT-022/MKT-023/MKT-011 v1.3.6)

| Column | Feb 24 |
|--------|--------|
| Date | 2026-02-24 |
| SPX Open | 6861.77 |
| SPX Close | 6890.34 |
| SPX Range | 61 pts (0.9%) |
| VIX Open | 20.64 |
| VIX Close | 19.50 |
| Entries | 4 (+1 skipped by MKT-011 v1.3.6) |
| Full ICs | 2 |
| One-Sided | 2 (1 call-only, 1 put-only) |
| Total Credit | $975 |
| Call Stops | 1 |
| Put Stops | 0 |
| Stop Debits | $340 |
| Commission | $55 |
| Expired Credits | $830 |
| Daily P&L | +$435 |
| Cumulative P&L | $2,075 |
| Early Close | Yes, 14:17 ET (MKT-018, ROC 2.02%) |

#### Improvement Impact Assessment — Feb 24

| Rec | Implemented? | Triggered? | Actual Impact | Assessment |
|-----|-------------|------------|---------------|------------|
| 9.3 EMA Threshold (0.2%) | v1.2.8 | **NO** — all 4 signals deep NEUTRAL | $0 (no directional signals to filter) | Cannot assess on NEUTRAL day |
| 9.1 Stop Cascade (MKT-016) | **REMOVED** (v1.3.3) | N/A | N/A | Removed — bot attempts all 5 entries now |
| MKT-017 Daily Loss Limit | **REMOVED** (v1.3.3) | N/A | N/A | Removed — bot attempts all 5 entries now |
| MKT-018 Early Close (ROC) | v1.3.0 | **YES** — ROC hit 2.02% at 14:17 ET | **SECOND LIVE TRIGGER** — locked in $435 net profit | **STRONG POSITIVE** — confirmed value on consecutive trigger |
| MKT-020 Call Tightening | v1.3.2 | **YES** — tightened Entries #2 and #3 calls (55→30pt) | Ensured min $1.00/side credit on call spreads | Positive — without tightening, calls would have been non-viable |
| MKT-021 ROC Gate | v1.3.2 | **YES** — triggered at 14:16 (ROC 1.79%) | No impact — all 5 entries already attempted/skipped | Neutral — gate fired but no entries left to block |
| MKT-022 Put Tightening | **v1.3.5 (NEW)** | **YES** — tightened Entry #5 put (55→45pt) | No impact — entry still skipped by MKT-011 v1.3.6 | **FIRST LIVE TRIGGER** — but masked by MKT-011 skip |
| MKT-011 v1.3.6 (NEUTRAL skip) | **v1.3.6 (NEW)** | **YES** — Entry #5 skipped (call non-viable + neutral) | **FIRST LIVE TRIGGER** — prevented low-credit entry | **POSITIVE** — avoided $100 put-only entry with $32.50 call non-viable |
| MKT-023 Hold Check | **v1.3.7 (NEW)** | **YES** — consistently CLOSE (close=$425 vs hold=$-315) | Confirmed MKT-018's close decision was optimal | **FIRST LIVE DAY** — validated design (hold was far worse than close) |
| Fix #83 Idempotency | **v1.3.8 (NEW)** | **YES** — FIX-71 guard was poisoned by midnight settlement | Unblocked daily summary after restart | **CRITICAL FIX** — without it, no daily summary all day |

**Feb 24 Assessment**: Feature-rich day with 4 new features deployed (MKT-022, MKT-011 v1.3.6, MKT-023, Fix #83). Second live trigger of MKT-018 early close — locked in +$435 net profit at 14:17 ET when ROC hit 2.02%. MKT-023 hold check (first live day) consistently recommended CLOSE because worst-case hold was -$315 (all call sides stressed, all put sides safe) vs close-now +$425 — a $740 advantage for closing. This validated the design: when calls are stressed but puts are safe, holding risks losing the call stops' full debit while the puts expire worthless (already counted in close-now P&L). MKT-011 v1.3.6 had its first live skip — Entry #5 in NEUTRAL market with call $32.50 < $100 minimum. Under old rules (v1.3.5 and earlier), this would have been converted to a put-only entry. The new skip rule is more conservative — in NEUTRAL markets, if one side can't meet minimum credit, the whole entry is questionable. MKT-022 progressive put tightening triggered on Entry #5 (55→45pt) but was masked by the MKT-011 skip. Entry #3's call stop demonstrated 0DTE's speed: cushion dropped from 64% to 6% in just 2 minutes as SPX rallied sharply. Despite the stop, 3 of 4 entries were clean wins (75% entry win rate). Fix #83 was discovered post-market when the daily summary failed to fire — the FIX-71 idempotency guard was checking `last_updated` against clock time rather than trading date, and midnight settlement had stored Feb 24's date for Feb 23's summary. Multiple mid-day deployments (v1.3.5→v1.3.6→v1.3.7) caused 10+ bot restarts, but state file recovery correctly preserved all positions and P&L through each restart.

#### Post-Improvement Day 6: Feb 25 (v1.3.9→v1.3.11 — MKT-021 gate=3, MKT-018 threshold raised to 3%)

| Column | Feb 25 |
|--------|--------|
| Date | 2026-02-25 |
| SPX Open | 6906.56 |
| SPX Close | 6926.54 |
| SPX Range | 29 pts (0.4%) |
| VIX Open | 19.39 |
| VIX Close | 18.64 |
| Entries | 2 (+1 skipped MKT-011, +2 skipped MKT-021) |
| Full ICs | 2 |
| One-Sided | 0 |
| Total Credit | $490 |
| Call Stops | 0 |
| Put Stops | 0 |
| Stop Debits | $250 (early close costs) |
| Commission | $40 |
| Expired Credits | $490 (all early-closed) |
| Daily P&L | +$200 |
| Cumulative P&L | $2,275 |
| Early Close | Yes, 11:15 ET (MKT-018, ROC 2.0%) |

#### Improvement Impact Assessment — Feb 25

| Rec | Implemented? | Triggered? | Actual Impact | Assessment |
|-----|-------------|------------|---------------|------------|
| 9.3 EMA Threshold (0.2%) | v1.2.8 | **NO** — all signals deep NEUTRAL (max +0.035%) | $0 (no directional signals to filter) | Cannot assess on NEUTRAL day |
| 9.1 Stop Cascade (MKT-016) | **REMOVED** (v1.3.3) | N/A | N/A | Removed — bot attempts all 5 entries now |
| MKT-017 Daily Loss Limit | **REMOVED** (v1.3.3) | N/A | N/A | Removed — bot attempts all 5 entries now |
| MKT-018 Early Close (ROC) | v1.3.0 | **YES** — ROC hit 2.0% at 11:15 ET | **THIRD LIVE TRIGGER** — locked in $200 net profit | **POSITIVE** — earliest close of the period, clean exit |
| MKT-020 Call Tightening | v1.3.2 | **YES** — tightened Entry #1 (50→30pt) and Entry #2 (50→25pt) | Ensured min $1.00/side credit on call spreads | **Critical** — without tightening, both calls would have been non-viable |
| MKT-021 ROC Gate | v1.3.9 | **YES** — blocked Entries #4 and #5 (ROC 2.0% ≥ 2.0% with 3 entries) | **Prevented 2 entries** after ROC threshold reached | **POSITIVE** — first trigger with gate=3 (lowered from 5) |
| MKT-022 Put Tightening | v1.3.5 | **YES** — tightened Entry #3 put (60→40pt OTM) | No impact — entry still skipped by MKT-011 | Second trigger, but again masked by MKT-011 skip |
| MKT-011 v1.3.6 (NEUTRAL skip) | v1.3.6 | **YES** — Entry #3 skipped (call $5.00 non-viable + neutral) | **SECOND LIVE TRIGGER** — prevented low-credit entry | **POSITIVE** — call credit $5 was hopelessly non-viable |
| MKT-023 Hold Check | v1.3.7 | **NO** — MKT-018 fired without MKT-023 check | N/A (only 2 entries, both full ICs) | Not triggered — early close was immediate |
| 9.4 Trend Persistence | Deferred | | | |
| 9.2 Stop Cooldown | Deferred | | | |
| 9.5 Range Awareness | Deferred | | | |
| 9.6 Holiday Caution | Deferred | | | |
| 9.7 Entry #5 Monitor | Ongoing | Entry #5 SKIPPED by MKT-021 | Cannot assess — blocked by ROC gate | |

**Feb 25 Assessment**: Cleanest day of the entire period — 2 full ICs placed, 0 stops, all positions early-closed at +$200 net. This was an "easy money" day: calm market (29pt range, lowest of the period), declining VIX (19.39→18.64), and both entries built comfortable cushion quickly. MKT-018 early close triggered at 11:15 ET — the earliest of all three triggers (vs 11:31 on Feb 20 and 14:17 on Feb 24). With only 2 entries and $10,000 capital deployed, even the 2% ROC threshold was reached within 70 minutes of first entry. MKT-020 call tightening was essential: at VIX ~18.87, default OTM distances produced call credits well below $1.00/side. Entry #1 needed 50→30pt tightening, Entry #2 needed 50→25pt — the 25pt OTM floor was barely sufficient. MKT-011 correctly skipped Entry #3 where even at the 25pt floor, call credit was only $5.00 ($0.05/contract). The MKT-021 ROC gate with the new gate=3 setting (lowered from 5 in v1.3.9) fired for the first time — it counted 3 entries attempted (2 placed + 1 skipped) and blocked entries #4/#5. Post-early-close, three deployments were made: v1.3.9 (MKT-021 gate lowered), v1.3.10 (cumulative ROC columns in Daily Summary), and v1.3.11 (MKT-018 threshold raised from 2% to 3% based on the analysis showing 2% left $1,025 on the table over 11 days with zero reversals after trigger). The threshold change will first be active on Feb 26.

#### Post-Improvement Day 7: Feb 26 (v1.3.11 — First full day with MKT-018 at 3% threshold)

| Column | Feb 26 |
|--------|--------|
| Date | 2026-02-26 |
| SPX Open | 6937.98 |
| SPX Close | 6907.46 |
| SPX Range | 83 pts (1.2%) |
| VIX Open | 17.60 |
| VIX Close | 18.63 |
| Entries | 4 (+1 skipped MKT-011) |
| Full ICs | 3 |
| One-Sided | 1 (call-only, BEARISH) |
| Total Credit | $1,345 |
| Call Stops | 2 |
| Put Stops | 2 |
| Stop Debits | $1,025 |
| Commission | $55 |
| Expired Credits | $370 |
| Daily P&L | -$710 |
| Cumulative P&L | $1,565 |
| Early Close | No (ROC negative all day) |

#### Improvement Impact Assessment — Feb 26

| Rec | Implemented? | Triggered? | Actual Impact | Assessment |
|-----|-------------|------------|---------------|------------|
| 9.3 EMA Threshold (0.2%) | v1.2.8 | **YES** — Entry #2 at -0.249% crossed 0.2% threshold → BEARISH | First BEARISH since Feb 17 | **NEGATIVE** — BEARISH call-only stopped (-$345), full IC would have survived |
| 9.1 Stop Cascade (MKT-016) | **REMOVED** (v1.3.3) | N/A | N/A | Removed — would not have helped (stops spread 1-1.5h apart, no cascade) |
| MKT-017 Daily Loss Limit | **REMOVED** (v1.3.3) | N/A | N/A | Removed — realized P&L exceeded -$500 after Entry #2 stop |
| MKT-018 Early Close (ROC) | v1.3.11 (3%) | **NO** — ROC was negative all day | $0 (never triggered) | Cannot assess on losing day — designed for profitable days |
| MKT-020 Call Tightening | v1.3.2 | **YES** — tightened Entries #1 (50→45pt), #3 (50→45pt), #4 (50→40pt), #5 (55→40pt) | Ensured min $1.00/side credit on all call spreads | **Critical** — VIX 17.60 produced very low call premium at default OTM |
| MKT-021 ROC Gate | v1.3.9 | **NO** — ROC was negative, no entries blocked | $0 (never triggered) | Cannot assess on losing day |
| MKT-022 Put Tightening | v1.3.5 | **NO** — not triggered on any entry | $0 | Not needed — VIX 17.60 still produced adequate put premium |
| MKT-011 v1.3.6 (NEUTRAL skip) | v1.3.6 | **YES** — Entry #4 skipped (call $72.50 non-viable + neutral) | **Prevented 1 low-credit entry** | **POSITIVE** — avoided entry where call credit was only $72.50 after MKT-013 shift |
| MKT-023 Hold Check | v1.3.7 | **NO** — MKT-018 never triggered | N/A | Cannot assess on losing day |
| MKT-019 Virtual Equal Credit | v1.3.0 | Active on all 3 full ICs | Stop levels used 2×max(call,put) instead of total credit | Neutral — all stops were legitimate breaches |
| 9.4 Trend Persistence | Deferred | | | |
| 9.2 Stop Cooldown | Deferred | | | |
| 9.5 Range Awareness | Deferred | | | |
| 9.6 Holiday Caution | Deferred | | | |
| 9.7 Entry #5 Monitor | Ongoing | Entry #5 placed, call stopped, put expired | Net -$30 (nearly breakeven) | Entry #5 was the best-performing entry of the day |

**Feb 26 Assessment**: Worst loss day since Feb 17 (-$740), and second worst of the entire 12-day period. The day's character was a V-shape sell-off and recovery: SPX opened at 6938, plunged 77 points to 6861 by ~10:35, then recovered to close at 6907. VIX spiked from 17.60 to 20.54 intraday (+17%), the biggest intraday VIX move of the period. This V-shape pattern is the exact weakness identified in the Feb 17 analysis (Weakness 1: EMA whipsaws on V-shaped days).

**BEARISH signal analysis**: Entry #2's BEARISH signal at -0.249% was technically correct — SPX was indeed in a sharp downtrend at 10:35 (EMA20=6874.82, EMA40=6891.99). But the market reversed within 40 minutes. The call-only entry (short 6920) was stopped at 11:15 as SPX rallied back above 6920. If Entry #2 had been NEUTRAL (full IC), the MKT-019 virtual equal credit stop would have been ~$800/side (2×max($305,$400)) instead of $610 (2×$305 for one-sided). The call spread cost-to-close was $640 — under one-sided stop ($610), this exceeded the threshold; under full IC stop ($800), it would NOT have exceeded. Additionally, the put side at P:6810/6750 would have expired worthless (+~$400). The BEARISH signal cost an estimated ~$1,000 vs the full IC alternative.

**Structural observations**: (1) Entry #1's put was stopped just 10 minutes after entry — the fastest stop of the entire period. SPX was still plunging at 10:15 and hadn't yet hit its 6861 low. (2) All 4 placed entries had at least one side stopped (0% entry win rate), matching Feb 17's pattern. (3) Despite 4 stops, Entry #5 was nearly breakeven (-$30) — the expired put side (+$120) nearly offset the stopped call (-$145). (4) MKT-020 was essential: all 4 entries needed call tightening due to VIX 17.60 producing very low call premium at default OTM distances. (5) MKT-011 correctly skipped Entry #4 where post-MKT-013 overlap adjustment left the call credit at only $72.50. (6) This was the first full day with MKT-018 at the new 3% threshold — the old 2% would not have helped either (ROC was negative all day).

#### Week 2 Performance Template (Date Range: ___ to ___)

| Column | Day 1 | Day 2 | Day 3 | Day 4 | Day 5 |
|--------|-------|-------|-------|-------|-------|
| Date | | | | | |
| SPX Open | | | | | |
| SPX Close | | | | | |
| SPX Range | | | | | |
| VIX Open | | | | | |
| VIX Close | | | | | |
| Entries | | | | | |
| Full ICs | | | | | |
| One-Sided | | | | | |
| Total Credit | | | | | |
| Call Stops | | | | | |
| Put Stops | | | | | |
| Stop Debits | | | | | |
| Commission | | | | | |
| Expired Credits | | | | | |
| Daily P&L | | | | | |
| Cumulative P&L | | | | | |

#### Improvement Impact Assessment Template

| Rec | Priority | Implemented? | Triggered? | Estimated Savings | Actual Impact | Assessment |
|-----|----------|-------------|------------|-------------------|---------------|------------|
| 9.3 EMA Threshold (0.2%) | #1 | v1.2.8 (Feb 17) | | ~$290/bad day | | |
| 9.1 Stop Cascade (3 stops) | #2 | v1.2.8 (Feb 17) | | ~$195/bad day | | |
| MKT-017 Daily Loss Limit | -- | v1.2.9 (Feb 18) | | -$500 threshold | | |
| MKT-018 Early Close (ROC) | -- | v1.3.0 (Feb 19) | | 2% ROC threshold | | |
| 9.4 Trend Persistence | Deferred | | | Overlaps with 9.3 | | |
| 9.2 Stop Cooldown | Deferred | | | ~$80 (redundant with 9.1+9.3) | | |
| 9.5 Range Awareness | Deferred | | | ~$100 est. | | |
| 9.6 Holiday Caution | Deferred | | | ~$100 est. | | |
| 9.7 Entry #5 Monitor | Ongoing | | | N/A | | |

#### Key Questions to Answer After Each Week

1. **Did the stop cascade breaker trigger? If so, what entries were skipped and what would they have done?**
2. **With the wider EMA threshold (0.2%), how did trend signal distribution change? Fewer BULLISH/BEARISH signals?**
3. **Were there any V-shape reversal days? How did the bot handle them compared to Feb 17 baseline?**
4. **Did win rate improve? (Target: >50% from baseline 44.4%)**
5. **Did worst-day loss decrease? (Target: < $740 from baseline)**
6. **Is net capture rate improving? (Target: >15% from baseline 12.8%)**
7. **Are there still whipsaw signals passing the 0.2% threshold? (If yes, consider adding Rec 9.4 trend persistence)**

---

## Appendix A: Raw EMA Divergence Data (From VM Logs)

**Source**: `journalctl -u hydra` on calypso-bot VM, pulled Feb 17 2026.
**Purpose**: Exact EMA divergence percentages for every entry, so future analysis can determine how threshold changes would affect signal classification without re-pulling logs.

### Feb 10 (Tuesday)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | ~6972 | ~6972 | +0.035% | NEUTRAL | NEUTRAL | No |
| #2 | 10:35 | ~6971 | ~6971 | +0.032% | NEUTRAL | NEUTRAL | No |
| #3 | 11:05 | ~6965 | ~6965 | -0.033% | NEUTRAL | NEUTRAL | No |
| #4 | 11:35 | ~6957 | ~6957 | -0.035% | NEUTRAL | NEUTRAL | No |
| #5 | 12:05 | ~6952 | ~6952 | -0.034% | NEUTRAL | NEUTRAL | No |

**Note**: All divergences were <0.04% — deep NEUTRAL zone. No impact from threshold change.

### Feb 11 (Wednesday)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | ~6988 | ~6988 | ~+0.01% | NEUTRAL | NEUTRAL | No |
| #2 | 10:35:19 | — | — | **-0.182%** | **BEARISH** | **NEUTRAL** | **YES** |
| #3 | 11:05 | — | — | ~-0.06% | NEUTRAL | NEUTRAL | No |
| #4 | 11:35 | — | — | ~-0.04% | NEUTRAL | NEUTRAL | No |
| #5 | 12:05 | — | — | ~-0.05% | NEUTRAL | NEUTRAL | No |
| #6 | 12:35 | — | — | ~-0.03% | NEUTRAL | NEUTRAL | No |

**Entry #2 affected**: At 0.2% threshold, would become NEUTRAL → full IC instead of call-only.

### Feb 12 (Thursday - Major Sell-Off)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | — | — | ~-0.05% | NEUTRAL | NEUTRAL | No |
| #2 | 10:35 | — | — | ~-0.08% | NEUTRAL | NEUTRAL | No |
| #3 | 11:05:07 | — | — | **-0.175%** | **BEARISH** | **NEUTRAL** | **YES** |
| #4 | 11:35:04 | — | — | **-0.204%** | **BEARISH** | **BEARISH** | No |
| #5 | 12:05 | — | — | ~-0.35% | NEUTRAL* | NEUTRAL* | No |
| #6 | 12:35 | — | — | ~-0.40% | NEUTRAL* | NEUTRAL* | No |

*Entries #5/#6 were NEUTRAL despite large divergence — the 20 EMA crossed back above 40 EMA as market stabilized.

**Entry #3 affected**: Would become NEUTRAL → full IC. Entry #4 stays BEARISH at both thresholds.

### Feb 13 (Friday)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | — | — | ~+0.02% | NEUTRAL | NEUTRAL | No |
| #2 | 10:35:14 | — | — | **+0.105%** | **BULLISH** | **NEUTRAL** | **YES** |
| #3 | 11:05 | — | — | ~-0.04% | NEUTRAL | NEUTRAL | No |
| #4 | 11:35 | — | — | ~-0.02% | NEUTRAL | NEUTRAL | No |
| #5 | 12:05 | — | — | ~+0.03% | NEUTRAL | NEUTRAL | No |

**Entry #2 affected**: Would become NEUTRAL → full IC instead of put-only.

### Feb 17 (Tuesday - V-Shape Reversal)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05:02 | — | — | **-0.138%** | **BEARISH** | **NEUTRAL** | **YES** |
| #2 | 10:35 | — | — | ~-0.06% | NEUTRAL | NEUTRAL | No |
| #3 | 11:05 | — | — | ~+0.05% | NEUTRAL | NEUTRAL | No |
| #4 | 11:35 | 6835.504 | 6825.909 | **+0.141%** | **BULLISH** | **NEUTRAL** | **YES*** |
| #5 | 12:05 | — | — | ~+0.08% | NEUTRAL | NEUTRAL | No |

*Entry #4 corrected from initial ~0.21% estimate: state file EMA values are authoritative. Cascade breaker blocks Entry #4 regardless.

**Entry #1 and #4 affected**: Both would become NEUTRAL → full IC. However, Entry #4 is blocked by cascade breaker (MKT-016) after 3rd stop at 11:13, so only Entry #1's flip matters in practice.

### Feb 18 (Wednesday - Quiet Recovery, v1.2.8)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | — | — | ~+0.034% | NEUTRAL | NEUTRAL | No |
| #2 | 10:35 | — | — | ~+0.02% | NEUTRAL | NEUTRAL | No |
| #3 | 11:05 | — | — | ~+0.01% | NEUTRAL | NEUTRAL | No |
| #4 | 11:35 | — | — | ~+0.02% | NEUTRAL | NEUTRAL | No |
| #5 | 12:05 | — | — | N/A (skipped by MKT-011) | NEUTRAL | NEUTRAL | No |

**Note**: All divergences were <0.04% — deep NEUTRAL zone. Zero impact from threshold change. Max divergence of the day was +0.034% at Entry #1. This is the first day with the 0.2% threshold active, but it made no difference since all signals were deep NEUTRAL at both thresholds.

### Feb 19 (Thursday - Choppy Downside, v1.3.0)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | — | — | ~-0.023% | NEUTRAL | NEUTRAL | No |
| #2 | 10:35 | — | — | ~-0.044% | NEUTRAL | NEUTRAL | No |
| #3 | 11:05 | — | — | ~-0.084% | NEUTRAL | NEUTRAL | No |
| #4 | 11:35 | — | — | ~-0.060% | NEUTRAL | NEUTRAL | No |
| #5 | 12:05 | — | — | N/A (skipped by MKT-016) | NEUTRAL | NEUTRAL | No |

**Note**: All divergences were <0.09% — deep NEUTRAL zone. Max divergence ~-0.084% at Entry #3. Zero impact from threshold change. MKT-016 cascade breaker blocked Entry #5 before any EMA check.

### Feb 20 (Friday - Wide Range, v1.3.2)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #2 | 10:35 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #3 | 11:05 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #4 | 11:35 | — | — | N/A (skipped by MKT-021) | — | — | — |
| #5 | 12:05 | — | — | N/A (skipped by MKT-021) | — | — | — |

**Note**: All 3 placed entries had NEUTRAL signal. Exact divergence values not captured — EMA data would need to be pulled from state file. MKT-021 pre-entry ROC gate blocked Entries #4 and #5 before any EMA check. Zero impact from threshold change.

### Feb 23 (Monday - Sustained Sell-Off, v1.3.2)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | 6882.03 | 6887.87 | -0.085% | NEUTRAL | NEUTRAL | No |
| #2 | 10:35 | 6861.79 | 6868.23 | -0.094% | NEUTRAL | NEUTRAL | No |
| #3 | 11:05 | — | — | N/A (skipped by MKT-017) | — | — | — |
| #4 | 11:35 | — | — | N/A (skipped by MKT-017) | — | — | — |
| #5 | 12:05 | — | — | N/A (skipped by MKT-017) | — | — | — |

**Note**: Both placed entries had deep NEUTRAL divergence (-0.085%, -0.094%). MKT-017 daily loss limit blocked Entries #3-5 before any EMA check. Zero impact from threshold change.

### Feb 24 (Tuesday - Morning Dip/Recovery, v1.3.5→v1.3.8)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:06 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #2 | 10:45 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #3 | 11:05 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #4 | 11:35 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #5 | 12:05 | — | — | ~NEUTRAL (skipped by MKT-011 v1.3.6) | NEUTRAL | NEUTRAL | No |

**Note**: All 4 placed entries had deep NEUTRAL divergence. Entry #5 also had NEUTRAL signal but was skipped by MKT-011 v1.3.6 (call non-viable in NEUTRAL market = skip). Exact EMA values not captured in earlier session — would need to be pulled from state file. Zero impact from threshold change.

### Feb 25 (Wednesday - Range-Bound, v1.3.9)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | 6927.31 | 6924.86 | +0.035% | NEUTRAL | NEUTRAL | No |
| #2 | 10:35 | 6924.01 | 6925.16 | -0.017% | NEUTRAL | NEUTRAL | No |
| #3 | 11:05 | 6923.53 | 6923.15 | +0.005% | NEUTRAL | NEUTRAL | No |
| #4 | 11:35 | — | — | N/A (skipped by MKT-021) | — | — | — |
| #5 | 12:05 | — | — | N/A (skipped by MKT-021) | — | — | — |

**Note**: All 3 attempted entries had deep NEUTRAL divergence (max +0.035%). Entry #3 had near-zero divergence (+0.005%) — EMAs were virtually on top of each other. MKT-021 ROC gate blocked Entries #4/#5 before any EMA check. Zero impact from threshold change.

### Feb 26 (Thursday - V-Shape Sell-Off/Recovery, v1.3.11)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | 6922.96 | 6930.70 | -0.112% | BEARISH | NEUTRAL | **Yes** |
| #2 | 10:35 | 6874.82 | 6891.99 | -0.249% | BEARISH | BEARISH | No |
| #3 | 11:05 | 6891.55 | 6884.94 | +0.096% | NEUTRAL | NEUTRAL | No |
| #4 | 11:35 | 6901.35 | 6898.49 | +0.041% | NEUTRAL | NEUTRAL | No |
| #5 | 12:05 | 6882.02 | 6888.31 | -0.091% | NEUTRAL | NEUTRAL | No |

**Note**: First BEARISH signal since Feb 17. Entry #2 at -0.249% was deep BEARISH — crossed both thresholds. Entry #1 at -0.112% would have been BEARISH at 0.1% threshold but was NEUTRAL at 0.2% — the threshold correctly prevented a second one-sided call-only entry during the V-shape sell-off (Entry #1 was placed as full IC, call side survived and expired). Entry #4 was NEUTRAL but skipped by MKT-011 (post-MKT-013 call credit only $72.50). VIX spiked from 17.60 to 20.54 intraday. The 0.2% threshold helped on Entry #1 (+$120 call credit vs potential -$345 call-only loss) but did not prevent the -$249% BEARISH signal on Entry #2.

### Summary: Entries Affected by 0.2% Threshold

| Day | Entry | Old Signal | New Signal | Old Type | New Type |
|-----|-------|-----------|-----------|----------|----------|
| Feb 11 | #2 | BEARISH (-0.182%) | NEUTRAL | Call-only | Full IC |
| Feb 12 | #3 | BEARISH (-0.175%) | NEUTRAL | Call-only | Full IC |
| Feb 13 | #2 | BULLISH (+0.105%) | NEUTRAL | Put-only | Full IC |
| Feb 17 | #1 | BEARISH (-0.138%) | NEUTRAL | Call-only | Full IC |
| Feb 17 | #4 | BULLISH (+0.141%)* | NEUTRAL | Put-only | Full IC |
| Feb 26 | #1 | BEARISH (-0.112%) | NEUTRAL | Call-only | Full IC |

*Corrected from initial ~0.21% estimate. Cascade breaker blocks Entry #4 regardless, so this flip has no practical impact when both improvements are active.
**Feb 26 Entry #1**: At 0.1% threshold, Entry #1 (-0.112%) would have been BEARISH → call-only. At 0.2% threshold, it was NEUTRAL → full IC. The call side expired worthless (+$120), while the put side was stopped. As a full IC, the entry lost -$220 net. As call-only, it would have been +$120 (call expired) — so the flip COST ~$340 on this specific entry. However, this must be weighed against the overall benefit of the 0.2% threshold across all 12 days.

---

## Appendix B: Stop and Entry Timing Data (From VM Logs)

**Source**: `journalctl -u hydra` on calypso-bot VM, pulled Feb 17 2026.
**Purpose**: Exact timestamps for all stops and entry placements, for cascade breaker analysis without re-pulling logs.

### Feb 10 (Tuesday) — 1 stop

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Put-only (MKT-011), P:6935 |
| Entry #2 placed | 10:35 | Put-only (MKT-011), P:6935 |
| Entry #3 placed | 11:05 | Put-only (MKT-011), P:6930 |
| Entry #4 placed | 11:35 | Put-only (MKT-011), P:6920 |
| Entry #5 placed | 12:05 | Put-only (MKT-011), P:6915 |
| Entry #3 PUT STOPPED | ~13:xx | 1 stop total — cascade never triggers |

**Cascade breaker (threshold=3)**: Never triggers. Zero impact.

### Feb 11 (Wednesday) — 2 stops

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC, C:7000 P:6910 |
| Entry #2 placed | 10:35 | Call-only (BEARISH), C:6980 |
| Entry #3 placed | 11:05 | Put-only (MKT-011), P:6890 |
| Entry #4 placed | 11:35 | Put-only (MKT-011), P:6900 |
| Entry #5 placed | 12:05 | Put-only (MKT-011), P:6885 |
| Entry #6 placed | 12:35 | Put-only (MKT-011), P:6910 |
| Entry #1 PUT STOPPED | ~14:xx | 1st stop — all entries already placed |
| Entry #6 PUT STOPPED | ~15:xx | 2nd stop — all entries already placed |

**Cascade breaker (threshold=3)**: Never triggers (only 2 stops). Zero impact.

### Feb 12 (Thursday - Sell-Off) — 4 stops

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC, C:6990 P:6900 |
| Entry #2 placed | 10:35 | Full IC, C:6985 P:6895 |
| Entry #3 placed | 11:05 | Call-only (BEARISH), C:6950 |
| Entry #4 placed | 11:35 | Call-only (BEARISH), C:6920 |
| Entry #5 placed | 12:05 | Full IC, C:6915 P:6805 |
| Entry #6 placed | 12:35 | Full IC, C:6925 P:6810 |
| Entry #1 PUT STOPPED | ~12:40 | 1st stop — all entries already placed |
| Entry #2 PUT STOPPED | ~12:48 | 2nd stop |
| Entry #5 PUT STOPPED | ~12:54 | 3rd stop — cascade WOULD trigger, but 0 entries remaining |
| Entry #6 PUT STOPPED | ~13:10 | 4th stop |

**Cascade breaker (threshold=3)**: Triggers at 12:54, but all 6 entries already placed by 12:35. Zero impact.

### Feb 13 (Friday) — 3 stops

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC, C:6885 P:6765 |
| Entry #2 placed | 10:35 | Put-only (BULLISH), P:6805 |
| Entry #3 placed | 11:05 | Full IC, C:6905 P:6795 |
| Entry #4 placed | 11:35 | Full IC, C:6910 P:6800 |
| Entry #5 placed | 12:06 | Full IC, C:6920 P:6820 |
| Entry #1 CALL STOPPED | ~13:30 | 1st stop — all entries already placed |
| Entry #2 PUT STOPPED | ~14:15 | 2nd stop |
| Entry #5 PUT STOPPED | ~14:59 | 3rd stop — cascade triggers, 0 entries remaining |

**Cascade breaker (threshold=3)**: Triggers at ~14:59, but all 5 entries already placed by 12:06. Zero impact.

### Feb 17 (Tuesday - V-Reversal) — 5 stops

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Call-only (BEARISH), C:6860 |
| Entry #2 placed | 10:35 | Full IC, C:6840 P:6720 |
| Entry #3 placed | 11:05 | Full IC, C:6875 P:6755 |
| **Entry #2 CALL STOPPED** | **11:02** | **1st stop** |
| **Entry #1 CALL STOPPED** | **11:11** | **2nd stop** |
| **Entry #3 CALL STOPPED** | **11:13** | **3rd stop — CASCADE TRIGGERS** |
| ~~Entry #4~~ | ~~11:35~~ | **BLOCKED by cascade** (would have been BULLISH put-only, P:6780) |
| ~~Entry #5~~ | ~~12:05~~ | **BLOCKED by cascade** (would have been NEUTRAL full IC, C:6895 P:6785) |
| Entry #5 PUT STOPPED | 12:11 | (Actually placed, would be blocked) |
| Entry #4 PUT STOPPED | 12:53 | (Actually placed, would be blocked) |

**Cascade breaker (threshold=3)**: Triggers at 11:13, blocks Entry #4 and #5. **Saves ~$195 net.**

### Feb 18 (Wednesday - v1.2.8 Active) — 2 stops

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC (NEUTRAL), C:6945/6970 P:6845/6795, $390 credit |
| Entry #2 placed | 10:35 | Put-only (NEUTRAL→MKT-011), P:6840/6790, $220 credit |
| Entry #3 placed | 11:05 | Put-only (NEUTRAL→MKT-011), P:6855/6805, $115 credit |
| Entry #4 placed | 11:35 | Put-only (NEUTRAL→MKT-011), P:6850/6800, $85 credit |
| Entry #5 SKIPPED | 12:05 | MKT-011: both sides non-viable (call $12.50, put $42.50) |
| Entry #3 PUT STOPPED | 13:53 | Close cost: ($2.95-$0.55)×100=$240 → net loss: $125 |
| Entry #4 PUT STOPPED | 13:53 | Close cost: ($2.70-$0.50)×100=$220 → net loss: $135 |

**Cascade breaker (threshold=3)**: Never triggers (only 2 stops). Zero impact. Entry #5 was already skipped by MKT-011 before any stops occurred.

### Feb 19 (Thursday - v1.3.0 Active) — 3 stops

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC (NEUTRAL), C:6925/6960 P:6815/6755, $475 credit |
| **Entry #1 PUT STOPPED** | **10:07** | **1st stop — 2 min after placement! SP $6.10, LP $1.25** |
| Entry #2 placed | 10:35 | Full IC (NEUTRAL), C:6930/6990 P:6820/6760, $370 credit |
| **Entry #2 PUT STOPPED** | **10:51** | **2nd stop — SP $5.00, LP $0.90** |
| Entry #3 placed | 11:05 | Put-only (NEUTRAL→MKT-011, call $45 < $50), P:6810/6750, $245 credit |
| Entry #4 placed | 11:35 | Put-only (NEUTRAL→MKT-011, call $12.50 < $50 post-MKT-013), P:6815/6765, $175 credit |
| **Entry #4 PUT STOPPED** | **11:45** | **3rd stop — MKT-016 CASCADE BREAKER TRIGGERS** |
| ~~Entry #5~~ | ~~12:05~~ | **BLOCKED by MKT-016** (3 stops reached threshold) |
| MKT-018 shadow | 15:59 | ROC=-0.26% / 2.0% threshold — not triggered |
| Settlement | ~16:00+ | 6 positions expired: Entry #1 call ($105), Entry #2 call ($45), Entry #3 put ($245) |

**Cascade breaker (threshold=3)**: Triggers at 11:45 (3rd stop), blocks Entry #5. **First live trigger of MKT-016.**
**MKT-017 (daily loss limit)**: NOT triggered. Realized P&L = -$380 vs -$500 threshold. $120 buffer.
**MKT-018 (early close)**: NOT triggered. ROC was negative all day — designed for profitable days only.

### Feb 20 (Friday - v1.3.2 Active) — 1 stop + MKT-018 early close

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC (NEUTRAL), C:6940/6990 P:6830/6780, $975 credit |
| Entry #2 placed | 10:35 | Full IC (NEUTRAL), C:6935/6985 P:6825/6775, $480 credit |
| Entry #3 placed | 11:05 | Full IC (NEUTRAL, MKT-020 tightened call), C:6945/6995 P:6855/6805, $320 credit |
| **Entry #3 PUT STOPPED** | **~11:2x** | **1st stop — SPX dipped toward 6855 short put** |
| | | SP fill: $6.20, LP fill: $1.70, close cost: $450, net side loss: $225 |
| **MKT-018 EARLY CLOSE** | **11:31** | **ROC >= 2.0% — closing all remaining positions** |
| | | Entry #1: 4 legs closed (+$680), Entry #2: 4 legs closed (+$225) |
| | | Entry #3: call side closed (+$70), put already stopped |
| | | 409 Conflict on Entry #1 long call close — user manually closed |
| ~~Entry #4~~ | ~~11:35~~ | **SKIPPED by MKT-021** (ROC >= 2% gate) |
| ~~Entry #5~~ | ~~12:05~~ | **SKIPPED by MKT-021** (ROC >= 2% gate) |

**Cascade breaker (threshold=3)**: Never triggers (only 1 stop). Not needed — MKT-018 early close closed all positions before cascade could develop.
**MKT-017 (daily loss limit)**: NOT triggered. Day was profitable (+$690 net).
**MKT-018 (early close)**: **FIRST LIVE TRIGGER** at 11:31 ET. ROC hit 2.0% threshold. Closed all remaining positions, locked in $690 net profit.
**MKT-020 (call tightening)**: Triggered on Entry #3 — progressive call OTM adjustment ensured minimum $1.00/side credit.
**MKT-021 (ROC gate)**: Blocked Entries #4 and #5 — ROC already >= 2% before entry times.

### Feb 23 (Monday - v1.3.2 Active, last day with MKT-016/017) — 2 stops + MKT-017 triggered

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC (NEUTRAL), C:6925/6980 P:6815/6755, $525 credit |
| | | VIX=20.7, OTM=55pts, spread=60pts, stop=$750/side |
| | | MKT-008: LC 6985→6980 (illiquidity), MKT-011: PASSED |
| Entry #2 placed | 10:35 | Full IC (NEUTRAL), C:6910/6970 P:6800/6740, $390 credit |
| | | VIX=20.9, OTM=55pts, spread=60pts, stop=$500/side |
| **Entry #1 PUT STOPPED** | **11:01:42** | **1st stop — SP fill: $9.00, LP fill: $1.25** |
| | | Close cost: ($9.00-$1.25)×100=$775, credit $380, net loss: $395 |
| **Entry #2 PUT STOPPED** | **11:02:52** | **2nd stop — SP fill: $6.10, LP fill: $0.95** |
| | | Close cost: ($6.10-$0.95)×100=$515, credit $255, net loss: $260 |
| **MKT-017 TRIGGERED** | **11:03:19** | **Realized P&L $-655 < -$500 threshold** |
| ~~Entry #3~~ | ~~11:05~~ | **BLOCKED by MKT-017** (daily loss limit) |
| ~~Entry #4~~ | ~~11:35~~ | **BLOCKED by MKT-017** |
| ~~Entry #5~~ | ~~12:05~~ | **BLOCKED by MKT-017** |
| MKT-018 shadow | 15:59 | ROC=-3.54% / 2.0% threshold — not triggered |
| Settlement | 00:00 (Feb 24) | Fix #82 bug: expired credits NOT processed |
| | | 4 call positions expired, $280 credits missed |
| | | Metrics manually corrected: -$685 → -$405 |

**Cascade breaker (threshold=3)**: Never triggers (only 2 stops). Not needed — MKT-017 triggered first based on loss magnitude.
**MKT-017 (daily loss limit)**: **FIRST LIVE TRIGGER** at 11:03 ET. Realized P&L = -$655 exceeds -$500 threshold. Blocked Entries #3-5.
**MKT-018 (early close)**: NOT triggered. ROC was negative all day (-3.54% at close).
**Fix #82**: Settlement at midnight processed empty registry but didn't add $280 expired call credits. Metrics corrected manually.

**Note**: MKT-016 and MKT-017 were removed post-market (v1.3.3). This was the last day these features were active.

### Feb 24 (Tuesday - v1.3.5→v1.3.8 Active, first day without MKT-016/017) — 1 stop + MKT-018 early close

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:06 | Full IC (NEUTRAL), C:6900/6950 P:6790/6730, $465 credit |
| | | VIX=20.6, stop=$670/side (MKT-019 virtual equal credit) |
| | | MKT-011: PASSED (Call $125, Put $340) |
| Entry #2 placed | 10:45 | Full IC (NEUTRAL), C:6910/6960 P:6825/6775, $265 credit |
| | | MKT-020: tightened short call 55pt→30pt OTM |
| | | MKT-011: PASSED (Call $130, Put $135) |
| Entry #3 placed | 11:05 | Call-only (NEUTRAL→MKT-011: put $90 < $100), C:6905/6955, $145 credit |
| | | MKT-020: tightened short call 55pt→30pt OTM |
| | | Stop=$290 (2× $145 for one-sided) |
| Entry #4 placed | 11:35 | Put-only (NEUTRAL→MKT-011: call $32.50 < $100), P:6815/6755, $100 credit |
| | | MKT-013/MKT-015: overlap adjustments applied |
| | | Stop=$200 (2× $100 for one-sided) |
| Entry #5 SKIPPED | 12:05 | MKT-011 v1.3.6: call $32.50 non-viable + NEUTRAL → skip |
| | | MKT-022: tightened put 55pt→45pt OTM (pre-skip analysis) |
| Entry #3 cushion drop | 12:09:41 | Call cushion 64% → rapidly declining |
| | 12:10:xx | Cushion 44% → 17% → 9% → 6% |
| **Entry #3 CALL STOPPED** | **12:11:49** | **1st (and only) stop — SC fill: $3.10, LC fill: $0.05** |
| | | Close cost: ($3.10-$0.05)×100=$305, credit $145, net loss: $160 |
| MKT-021 ROC gate | 14:16:15 | ROC 1.79% approaching threshold (all entries already attempted) |
| **MKT-018 EARLY CLOSE** | **14:17:34** | **ROC 2.02% >= 2.0% — closing all remaining positions** |
| | | MKT-023: CLOSE (close=$425 vs hold=$-315, CALLS_STRESSED) |
| | | Entry #1: 4 legs closed (LC $0.00 skipped per Fix #81) |
| | | Entry #2: 4 legs closed (LC $0.00 skipped per Fix #81) |
| | | Entry #4: 2 legs closed |
| Daily summary | 18:55 | Fired after Fix #83 deployed and bot restarted |
| | | Net P&L: $435, Commission: $55, Cumulative: $2,075 |

**MKT-016 (cascade breaker)**: REMOVED (v1.3.3). Would not have triggered anyway (only 1 stop).
**MKT-017 (daily loss limit)**: REMOVED (v1.3.3). Day was profitable — would not have triggered.
**MKT-018 (early close)**: **SECOND LIVE TRIGGER** at 14:17 ET. ROC hit 2.02% threshold. Closed all remaining positions, locked in $435 net profit.
**MKT-020 (call tightening)**: Triggered on Entries #2 and #3 — progressive call OTM adjustment (55→30pt) ensured minimum $1.00/side credit.
**MKT-021 (ROC gate)**: Triggered at 14:16 (ROC 1.79%) but all entries already attempted/skipped.
**MKT-022 (put tightening)**: **FIRST LIVE TRIGGER** on Entry #5 (55→45pt), but masked by MKT-011 skip.
**MKT-023 (hold check)**: **FIRST LIVE DAY** — consistently CLOSE. Worst-case hold ($-315) far worse than close-now ($425).
**MKT-011 v1.3.6**: **FIRST LIVE SKIP** — Entry #5 skipped (call non-viable + NEUTRAL = skip instead of convert).
**Fix #83**: Daily summary blocked by FIX-71 guard (midnight settlement stored clock time). Fixed in v1.3.8.

### Feb 25 (Wednesday - v1.3.9 Active) — 0 stops + MKT-018 early close

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC (NEUTRAL), C:6955/7005 P:6875/6825, $250 credit |
| | | VIX=18.87, MKT-020: call tightened 50→30pt OTM |
| | | MKT-011: PASSED (Call $105, Put $140) |
| Entry #2 placed | 10:35 | Full IC (NEUTRAL), C:6945/6995 P:6870/6820, $240 credit |
| | | MKT-020: call tightened 50→25pt OTM |
| | | MKT-011: PASSED (Call $135, Put $105) |
| Entry #3 SKIPPED | 11:05 | MKT-011: call $5.00 non-viable + NEUTRAL → skip |
| | | MKT-013: put shifted 6875→6865 (overlap with Entry #1) |
| | | MKT-020: call non-viable at 25pt floor ($5.00 < $100) |
| | | MKT-022: put tightened 60→40pt OTM ($100 ≥ $100) |
| **MKT-021 ROC GATE** | **11:15:17** | **ROC 2.0% >= 2.0% with 3 entries attempted — blocks #4/#5** |
| **MKT-018 EARLY CLOSE** | **11:15:17** | **ROC 2.0% >= 2.0% — closing all remaining positions** |
| | | Entry #1: 4 legs closed (SC $0.35, LC $0.05, SP $0.80, LP $0.20) |
| | | Entry #2: 4 legs closed (SC $1.15, LC $0.05, SP $0.70, LP $0.20) |
| | | 2 entries, 8 legs closed, 0 failed |
| Daily summary | 11:15 | Fired immediately after early close |
| | | Net P&L: $200, Commission: $40, Cumulative: $2,275 |
| Post-market deploys | 12:20-12:50 | v1.3.9 (MKT-021 gate=3), v1.3.10 (cum ROC), v1.3.11 (MKT-018 3%) |
| Settlement | 16:00 | FIX-71 caught duplicate (already sent at 11:15), no action |

**MKT-016 (cascade breaker)**: REMOVED (v1.3.3). No stops — would not have triggered.
**MKT-017 (daily loss limit)**: REMOVED (v1.3.3). Day was profitable — would not have triggered.
**MKT-018 (early close)**: **THIRD LIVE TRIGGER** at 11:15 ET. ROC hit 2.0% threshold. Earliest early close of the period. Closed all remaining positions, locked in $200 net profit.
**MKT-020 (call tightening)**: Triggered on Entries #1 (50→30pt) and #2 (50→25pt) — critical for viability at VIX 18.87.
**MKT-021 (ROC gate)**: **FIRST TRIGGER with gate=3** (v1.3.9). Blocked Entries #4/#5 after 3 entries attempted.
**MKT-022 (put tightening)**: Triggered on Entry #3 (60→40pt OTM), but masked by MKT-011 skip.
**MKT-011 v1.3.6**: **SECOND LIVE SKIP** — Entry #3 skipped (call $5.00 at 25pt floor, hopelessly non-viable in NEUTRAL market).

### Feb 26 (Thursday - v1.3.11 Active) — 4 stops, no early close

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC (NEUTRAL), C:6970/7020 P:6870/6820, $450 credit |
| | | EMA20=6922.96, EMA40=6930.70, divergence=-0.112% |
| | | MKT-020: Call tightened 50→45pt OTM |
| | | MKT-011: PASSED (Call $127.50, Put $372.50) |
| **Entry #1 PUT STOPPED** | **10:15:06** | **SP fill $9.20, LP fill $2.65 — 10 min after entry!** |
| | | SPX still plunging (hadn't hit 6861 low yet) |
| Entry #2 placed | 10:35 | Call-only (BEARISH -0.249%), C:6920/6980, $305 credit |
| | | First BEARISH signal since Feb 17 |
| | | MKT-011: PASSED (Call $310, Put $407.50) |
| Entry #3 placed | 11:05 | Full IC (NEUTRAL), C:6940/6990 P:6845/6795, $340 credit |
| | | MKT-020: Call tightened 50→45pt OTM |
| | | MKT-011: PASSED (Call $130, Put $247.50) |
| **Entry #2 CALL STOPPED** | **11:15:41** | **SC fill $6.50, LC fill $0.10 — V-shape recovery above 6920** |
| | | Close cost: ($6.50-$0.10)×100=$640, credit $305 |
| Entry #4 SKIPPED | 11:35 | MKT-011: call $72.50 non-viable + NEUTRAL → skip |
| | | MKT-020: Call tightened 50→40pt OTM |
| | | MKT-013: Short call 6940 overlaps Entry #3, shifted to 6945/6995 |
| Entry #5 placed | 12:05 | Full IC (NEUTRAL), C:6920/6975 P:6825/6775, $250 credit |
| | | MKT-020: Call tightened 55→40pt OTM |
| | | MKT-011: PASSED (Call $127.50, Put $127.50) |
| **Entry #3 PUT STOPPED** | **12:49:40** | **SP fill $5.20, LP fill $0.80** |
| | | Close cost: ($5.20-$0.80)×100=$440, credit $210 |
| **Entry #5 CALL STOPPED** | **14:16:45** | **SC fill $2.70, LC fill $0.05** |
| | | Close cost: ($2.70-$0.05)×100=$265, credit $130 |
| Entry #1 call EXPIRED | 17:00 | SC 6970 C, LC 7020 C → +$120 |
| Entry #3 call EXPIRED | 17:00 | SC 6940 C, LC 6990 C → +$130 |
| Entry #5 put EXPIRED | 17:00 | SP 6825 P, LP 6775 P → +$120 |
| Settlement | 17:00 | 6 positions expired, total expired credits: $370 |
| Daily summary | 17:00 | Net P&L: -$710, Commission: $55, Cumulative: $1,565 |

**MKT-016 (cascade breaker)**: REMOVED (v1.3.3). Stops were 1-1.5h apart — no cascade pattern.
**MKT-017 (daily loss limit)**: REMOVED (v1.3.3). Realized P&L exceeded -$500 after Entry #2 stop.
**MKT-018 (early close)**: NOT triggered. ROC was negative all day. First full day with 3% threshold (v1.3.11).
**MKT-020 (call tightening)**: Triggered on all 4 attempted entries — VIX 17.60 produced very low call premium.
**MKT-021 (ROC gate)**: NOT triggered. ROC was negative.
**MKT-011 v1.3.6**: **THIRD LIVE SKIP** — Entry #4 skipped (call $72.50 after MKT-013 overlap shift, non-viable in NEUTRAL market).
**MKT-013 (overlap)**: Triggered on Entry #4 — short call 6940 overlapped Entry #3, shifted to 6945/6995.
**9.3 EMA threshold (0.2%)**: Entry #1 at -0.112% was NEUTRAL (would have been BEARISH at 0.1%). Entry #2 at -0.249% was BEARISH at both thresholds.

---

## Appendix C: Threshold Sensitivity Analysis — Which Value Is Optimal?

**Purpose**: Rigorous comparison of threshold candidates (0.125%, 0.15%, 0.175%, 0.2%) against actual entry data to determine the optimal EMA neutral threshold.

### All Directional Entries — Precise Divergence Values

Sources: Feb 17 from state file `ema_20_at_entry`/`ema_40_at_entry` (authoritative). Feb 10-13 from VM logs (approximate, marked with ~).

| Day | Entry | Divergence | Flips to NEUTRAL at ≥ | Actual Outcome | Signal Correct? |
|-----|-------|-----------|----------------------|----------------|-----------------|
| Feb 11 | #2 | ~-0.182% | 0.182% | Call-only EXPIRED (+$130) | Yes |
| Feb 12 | #3 | ~-0.175% | 0.175% | Call-only EXPIRED (+$175) | Yes |
| Feb 12 | #4 | ~-0.204% | 0.204% | Call-only EXPIRED (+$250) | Yes |
| Feb 13 | #2 | ~+0.105% | 0.105% | Put-only STOPPED (-$450) | **No** |
| Feb 17 | #1 | -0.1377% (exact) | 0.138% | Call-only STOPPED (-$295) | **No** |
| Feb 17 | #4 | +0.1406% (exact) | 0.141% | Put-only STOPPED (-$225) | **No** |

### Impact Per Flipped Entry (One-Sided → Full IC)

**Key principle:** Full IC with one side stopped ≈ **breakeven** minus commission ≈ **-$5** net (with MEIC+). This is because `stop_level = total_credit`, so the stop debit ≈ total credit collected. The surviving side's expired credit is already included in the total credit — it doesn't add extra profit. See Appendix G for full formula.

**CORRECTION (Feb 18):** Previous version of this table used incorrect P&L projections that didn't properly apply the IC breakeven formula. All "Projected P&L (full IC)" values for stopped scenarios are now ~-$5 (MEIC+ breakeven).

| Entry | Actual P&L (one-sided) | Projected P&L (full IC) | Impact |
|-------|----------------------|------------------------|--------|
| Feb 13 #2 | -$450 (put stopped) | ~-$5 (put stopped, IC breakeven) | **+$445** |
| Feb 17 #1 | -$295 (call stopped) | ~-$5 (call stopped, IC breakeven) | **+$290** |
| Feb 17 #4 | -$225 (put stopped) | ~-$5 (put stopped, IC breakeven) | **~+$220** |
| Feb 12 #3 | +$175 (call expired) | ~-$5 (put stopped†, IC breakeven) | **-$180** |
| Feb 11 #2 | +$130 (call expired) | ~+$425 (both expire)‡ | **+$295** |
| Feb 12 #4 | +$250 (call expired) | ~-$5 (put stopped†, IC breakeven) | **~-$255** |

†Feb 12 had 100% put stop rate — all 4 put sides were stopped.
‡Feb 11 #2 put at P:6880 would be 30pts further OTM than stopped P:6910 — likely survives. If put also stopped: projected ≈ -$5, impact ≈ -$135.

### Threshold Comparison (With Cascade Breaker Active)

Note: Feb 17 Entry #4 is blocked by cascade breaker (MKT-016) at all thresholds, so its flip doesn't contribute to the combined impact.

| Threshold | Entries Flipped (non-cascade-blocked) | Net Impact | Key Trade-off |
|-----------|--------------------------------------|-----------|---------------|
| 0.1% (old) | None | $0 (baseline) | 3 wrong directional bets unchecked |
| **0.125%** | Feb 13 #2 | **+$445** | Catches worst wrong signal only |
| **0.15%** | +Feb 17 #1 | **+$735** | Catches both wrong signals |
| **0.175%** | +Feb 12 #3 | **+$555** | -$180 cost from flipping correct BEARISH on sell-off |
| **0.2%** | +Feb 11 #2 | **+$850** (or +$420)‡ | Best likely: Feb 11 #2 as full IC = +$295 more |
| 0.205%+ | +Feb 12 #4 | **~+$595** (or +$165)‡ | Flips another correct BEARISH (-$255) |

‡Range depends on whether Feb 11 #2's put at P:6880 survives (likely) or is stopped (unlikely). See impact table above.

### Optimal Range: 0.183% to 0.203%

Any threshold in this range flips the same set of entries:
- **Below 0.183%**: Misses Feb 11 #2 (0.182% stays BEARISH) — loses +$295 likely benefit
- **Above 0.203%**: Flips Feb 12 #4 (0.204% becomes NEUTRAL) — costs ~$255 on genuine trend days
- **0.2%** is the natural round number in this optimal range

### Why Not Lower Thresholds?

**0.15%** catches the two WRONG signals (Feb 13 #2, Feb 17 #1) but misses Feb 11 #2. That entry at -0.182% was correctly BEARISH (call-only expired profitably), but as a full IC it would have been *more profitable* (~+$425 vs +$130) because the additional put at P:6880 was 30pts further OTM than the day's stopped put at P:6910 and likely survived. However, going from 0.15% to 0.175% costs -$180 (Feb 12 #3 flips a correct BEARISH on a sell-off day), then 0.175% to 0.2% recovers via Feb 11 #2 (+$295). Net: 0.2% is still the best choice despite the Feb 12 cost.

**0.125%** only catches one of three wrong signals. Feb 17 Entry #1 at -0.138% stays BEARISH — the biggest single-entry loss that the threshold is designed to prevent.

### Conclusion

**0.2% is confirmed optimal** against 5 days of actual data (net impact: **+$850 likely**, +$420 worst case). It catches all wrong signals (saving +$445 and +$290), accepts a -$180 cost on Feb 12 (correct BEARISH flipped to full IC → put stopped on sell-off), but more than recovers via Feb 11 #2 (+$295 likely). It stops precisely before flipping the strongly correct BEARISH signal (Feb 12 #4 at -0.204%, which would cost another -$255).

---

## Appendix D: What-If — EMA Threshold 0.2% Impact by Day (Detail)

**Purpose**: Detailed P&L projections for the 0.2% threshold at the entry level.

**CORRECTION (Feb 18, 2026):** The original version of this appendix contained P&L projection errors. The calculations didn't properly apply the MEIC IC breakeven formula: when one side of a full IC is stopped, the stop debit ≈ total credit collected, so net P&L ≈ -$5 (with MEIC+) regardless of individual side credits. The original analysis incorrectly added "expired call credit" on top of the stop-debit calculation, double-counting credit that was already part of total_credit. All projections have been corrected below.

**Note on per-entry P&L precision:** "Actual P&L" values below are from the bot's recorded data and include fill slippage (market orders may execute $10-$20 beyond the stop level). Impact values (actual vs projected) should be treated as ±$10 approximations. The daily aggregate totals in Section 2 are authoritative.

### Feb 10: $0 impact
All entries NEUTRAL at both thresholds (max divergence 0.035%). No entries affected.

### Feb 11: Likely +$295 improvement

**Entry #2** changes from BEARISH (call-only, ~$140 credit) to NEUTRAL (full IC, ~$435 total):
- **Actual (call-only)**: Call expired → +$140 - $10 commission = **+$130 net**
- **Projected (full IC)**: Put at P:6880 is 30pts further OTM than Entry #1's stopped P:6910 → likely survives → both sides expire → ~$435 - $10 commission = **~+$425 net**
- **Impact**: **+$295**
- **Risk**: If put also stopped (unlikely), projected ≈ -$5, impact ≈ -$135

### Feb 12: -$180 (significant cost — correct BEARISH flipped on sell-off)

**Entry #3** changes from BEARISH (call-only, ~$185 credit) to NEUTRAL (full IC, ~$320 total):
- **Actual (call-only)**: Call expired → +$185 - $10 commission = **+$175 net**
- **Projected (full IC)**: Put side 100% stopped (all put sides stopped that day). IC breakeven: collected ~$320, stop debit ≈ $310 (MEIC+), commission ~$15 → **~-$5 net**
- **Impact**: **-$180**
- **Why it costs $180**: The BEARISH signal was correct — call expired profitably. Converting to full IC adds a put spread that gets stopped on this sell-off day. IC breakeven limits damage to ~-$5, but we lose the +$175 call-only profit.

### Feb 13: +$445 improvement

**Entry #2** changes from BULLISH (put-only, ~$430 credit) to NEUTRAL (full IC, ~$675 total):
- **Actual (put-only)**: Put stopped → stop ≈ 2×$430 - $10 (MEIC+) = $850, net loss = $850 - $430 = $420, plus $10 commission and ~$20 fill slippage = **~-$450 net** (recorded value)
- **Projected (full IC)**: Put still stopped (IC stop level = ~$675 < one-sided $860, so triggers sooner but at lower cost). IC breakeven: collected ~$675, stop debit ≈ $665 (MEIC+), commission ~$15 → **~-$5 net**
- **Impact**: **+$445**
- **Why it saves $445**: Wrong BULLISH signal caused -$450 as put-only. In a full IC, the breakeven design absorbs the stop — loss drops from -$450 to just -$5.

### Feb 17: +$290 improvement

**Entry #1** changes from BEARISH (call-only, ~$305 credit) to NEUTRAL (full IC, ~$695 total):
- **Actual (call-only)**: Call stopped at 11:11 → **-$295 net** (including MEIC+ and commission)
- **Projected (full IC)**: Call stopped, but IC breakeven absorbs it. Collected ~$695, stop debit ≈ $685 (MEIC+), commission ~$15 → **~-$5 net**
- **Impact**: **+$290**
- **Why it saves $290**: Wrong BEARISH signal caused -$295 as call-only. In a full IC, the surviving put side (at P:6720, far OTM) expires worthless — its credit is part of the total that offsets the call stop debit.

### Summary (0.2% Threshold Only, Without Cascade Breaker)

| Day | Affected Entry | Actual P&L | Projected P&L (0.2%) | Impact |
|-----|---------------|-----------|----------------------|--------|
| Feb 10 | None | — | — | **$0** |
| Feb 11 | #2 | +$130 | ~+$425 (both expire)† | **+$295** |
| Feb 12 | #3 | +$175 | ~-$5 (put stopped) | **-$180** |
| Feb 13 | #2 | -$450 | ~-$5 (put stopped) | **+$445** |
| Feb 17 | #1 | -$295 | ~-$5 (call stopped) | **+$290** |
| **TOTAL** | | | | **+$850** |

†Feb 11 #2 put at P:6880 likely survives. If also stopped: projected ≈ -$5, impact ≈ -$135, total ≈ **+$420**.

---

## Appendix E: What-If Analysis — Cascade Breaker Impact by Day

**Purpose**: Pre-computed impact of MKT-016 stop cascade breaker (threshold=3), so future sessions don't need to re-derive.

### Cascade Breaker Behavior
- **Type**: Cumulative total stops (NOT consecutive)
- **Trigger**: `call_stops_triggered + put_stops_triggered >= max_daily_stops_before_pause`
- **Action**: Skip all remaining entry attempts for the day
- **Existing entries**: Continue to be monitored for stops normally

### Threshold Sensitivity (Based on Feb 17 Data)

| Threshold | Triggers At | Entries Blocked | Net Impact | Assessment |
|-----------|------------|-----------------|------------|------------|
| 2 | 11:11 (2nd stop) | #3, #4, #5 | **-$1,080** (catastrophic — loses $265 debit but also $200+$250+$85 expiry credits) | TOO AGGRESSIVE |
| **3** | **11:13 (3rd stop)** | **#4, #5** | **+$195** | **OPTIMAL** |
| 4 | 12:11 (4th stop) | #5 only | **$0** (by 12:11, Entry #5 was placed at 12:05) | TOO LOOSE |

### Impact by Day

| Day | Total Stops | 3rd Stop Time | Last Entry Time | Entries Blocked | Net Impact |
|-----|------------|--------------|-----------------|-----------------|------------|
| Feb 10 | 1 | N/A | 12:05 | 0 | **$0** |
| Feb 11 | 2 | N/A | 12:35 | 0 | **$0** |
| Feb 12 | 4 | 12:54 | 12:35 | 0 (all placed) | **$0** |
| Feb 13 | 3 | ~14:59 | 12:06 | 0 (all placed) | **$0** |
| **Feb 17** | **5** | **11:13** | **11:05** | **2 (#4, #5)** | **+$195** |
| Feb 18 | 2 | N/A | 12:05 (skipped by MKT-011) | 0 | **$0** |
| **Feb 19** | **3** | **11:45** | **11:35** | **1 (#5)** | **Unknown*** |
| Feb 20 | 1 | N/A | 11:05 | 0 | **$0** |
| **Feb 23** | **2** | N/A (only 2 stops) | 10:35 | **0** | **$0** (MKT-017 blocked instead) |
| **TOTAL** | | | | | **+$195 confirmed + Feb 19 TBD** |

*Feb 19 Entry #5 was blocked by cascade breaker. Impact unknown — depends on what Entry #5 would have done (likely another MKT-011 put-only that may or may not have been stopped).

### Feb 17 Blocked Entry Breakdown

| Entry | Type | Credit | Stop Debit | Expired Credit | Commission | Actual P&L | If Blocked |
|-------|------|--------|-----------|----------------|------------|-----------|------------|
| #4 | Put-only (BULLISH) | $235 | $225 | $0 | $10 | -$225 | +$235 saved |
| #5 | Full IC (NEUTRAL) | $250 | $30 | $85 (call) | $15 | +$40 | -$40 cost |
| **Net** | | | | | | | **+$195** |

### Combined Impact: Both Improvements Together (v1.2.8)

The two improvements affect **different entries** on Feb 17, so their combined savings equals the simple sum — no overlap.

- **Threshold (Rec 9.3)** affects: Feb 11 #2, Feb 12 #3, Feb 13 #2, Feb 17 #1
- **Cascade (Rec 9.1)** affects: Feb 17 #4, Feb 17 #5
- Entry #4 would also be flipped by threshold (BULLISH→NEUTRAL), but cascade blocks it first, so threshold has no effect on Entry #4.

| Day | Threshold Impact | Cascade Impact | Combined Impact | Notes |
|-----|-----------------|----------------|-----------------|-------|
| Feb 10 | $0 | $0 | **$0** | No entries affected |
| Feb 11 | +$295 | $0 | **+$295** | No overlap |
| Feb 12 | -$180 | $0 | **-$180** | No overlap |
| Feb 13 | +$445 | $0 | **+$445** | No overlap |
| Feb 17 | +$290 (Entry #1) | +$195 (Entries #4, #5) | **+$485** | Different entries, no overlap |
| **TOTAL** | **+$850** | **+$195** | **+$1,045** | Simple sum (improvements are disjoint) |

**Likely combined: +$1,045. Worst case (if Feb 11 #2 put also stopped): +$615.**

Feb 17 combined breakdown:
- Entry #1: threshold flips to full IC → P&L improves from -$295 to ~-$5 → **+$290**
- Entry #4: cascade blocks → saves -$235 net (Appendix E) → **+$235**
- Entry #5: cascade blocks → loses +$40 net winner → **-$40**
- Combined Feb 17: $290 + $235 - $40 = **+$485**

---

## Appendix F: Strategy Configuration

### Baseline Config (v1.2.7, Feb 10-17 data)

```
Entries per day: 5
Entry times: 10:05, 10:35, 11:05, 11:35, 12:05 ET
EMA short period: 20
EMA long period: 40
EMA neutral threshold: 0.001 (0.1%)
Min viable credit per side: $0.50 (MKT-011)
Spread width: 50-60 pts (VIX-adjusted)
Stop level (full IC): total_credit
Stop level (one-sided): 2 × credit
MEIC+ enabled: Yes (stop = credit - $0.10 when credit > threshold)
Max daily stops before pause: N/A (not implemented)
```

### Config as of v1.3.2 (Feb 20-23, last version with MKT-016/017)

```
Entries per day: 5
Entry times: 10:05, 10:35, 11:05, 11:35, 12:05 ET
EMA short period: 20
EMA long period: 40
EMA neutral threshold: 0.002 (0.2%)              ← CHANGED from 0.001 (Rec 9.3, v1.2.8)
Min viable credit per side: $1.00 (MKT-011)      ← RAISED from $0.50 (v1.3.1)
Spread width: 50-60 pts (VIX-adjusted)
Stop level (full IC): total_credit
Stop level (one-sided): 2 × credit
MEIC+ enabled: Yes (stop = credit - $0.10 when credit > threshold)
Max daily stops before pause: 3                   ← (Rec 9.1, MKT-016, v1.2.8) — REMOVED in v1.3.3
Max daily loss: $500                              ← (MKT-017, v1.2.9) — REMOVED in v1.3.3
Early close enabled: Yes                          ← (MKT-018, v1.3.0)
Early close ROC threshold: 2.0%                   ← (MKT-018, v1.3.0)
Early close cost per position: $5.00              ← (MKT-018, v1.3.0)
Progressive call tightening: Yes                  ← (MKT-020, v1.3.2)
Pre-entry ROC gate: 2.0%                          ← (MKT-021, v1.3.2)
```

### Current Config (v1.3.8, deployed Feb 24)

```
Entries per day: 5
Entry times: 10:05, 10:35, 11:05, 11:35, 12:05 ET
EMA short period: 20
EMA long period: 40
EMA neutral threshold: 0.002 (0.2%)
Min viable credit per side: $1.00 (MKT-011)
Min call OTM distance: 25 pts                     ← (MKT-020 floor, v1.3.2)
Min put OTM distance: 25 pts                      ← (MKT-022 floor, v1.3.5)
Spread width: 50-60 pts (VIX-adjusted)
Stop level (full IC): total_credit
Stop level (one-sided): 2 × credit
MEIC+ enabled: Yes (stop = credit - $0.10 when credit > threshold)
Max daily stops before pause: REMOVED             ← MKT-016 removed (v1.3.3)
Max daily loss: REMOVED                           ← MKT-017 removed (v1.3.3)
Base MEIC loss limit: DISABLED                    ← Override returns False (v1.3.3)
Early close enabled: Yes                          ← (MKT-018, v1.3.0)
Early close ROC threshold: 2.0%                   ← (MKT-018, v1.3.0)
Early close cost per position: $5.00              ← (MKT-018, v1.3.0)
Hold check enabled: Yes                           ← (MKT-023, v1.3.7)
Hold check lean tolerance: 1.0%                   ← (MKT-023, v1.3.7)
Progressive call tightening: Yes                  ← (MKT-020, v1.3.2)
Progressive put tightening: Yes                   ← (MKT-022, v1.3.5)
MKT-011 NEUTRAL skip: Yes                         ← (v1.3.6) one side non-viable + NEUTRAL = skip
Pre-entry ROC gate: 2.0%                          ← (MKT-021, v1.3.2)
```

**Config location**: `bots/hydra/config/config.json` on VM at `/opt/calypso/`. Template at `bots/hydra/config/config.json.template` in repo.

## Appendix G: Formulas

- **Expected Move** = SPX × VIX / sqrt(252) / 100
- **Stop Level (full IC)** = Total credit collected for that IC
- **Stop Level (one-sided)** = 2 × credit collected for that side
- **Stop triggers when**: spread_value >= stop_level (cost-to-close exceeds threshold)
- **Net P&L** = Expired Credits - Stop Loss Debits - Commission
- **Net Capture Rate** = Net P&L / Total Credit Collected × 100
- **Win Rate** = Entries with 0 stops / Total entries × 100
- **Sortino Ratio** = daily_average_return / downside_deviation × sqrt(252)

### Commission Per Entry Type

Commission = $2.50 per leg per transaction (from `strategy.py` line 816: `commission_per_leg = 2.50`).

| Entry Type | Outcome | Legs Opened | Legs Closed | Total Commission |
|------------|---------|-------------|-------------|-----------------|
| Full IC | Both expire | 4 | 0 | **$10** |
| Full IC | One side stopped | 4 | 2 | **$15** |
| Full IC | Both stopped | 4 | 4 | **$20** |
| One-sided | Expires | 2 | 0 | **$5** |
| One-sided | Stopped | 2 | 2 | **$10** |

**Key**: Expired options have ZERO close commission (no transaction). Only stopped sides incur close commission.

### CRITICAL: IC Breakeven Formula (Used in All What-If Projections)

**Full IC with one side stopped (MEIC breakeven design):**
```
Collected at entry:     +total_credit  (call_credit + put_credit)
Stop closes one side:   -stop_level    ≈ total_credit (or total_credit - $10 with MEIC+)
Other side expires:     $0             (credit already counted in total_credit above)
Commission:             -$15           (4 legs entry + 2 legs stop close, at ~$5/spread)
─────────────────────────────────────
Net P&L:                ≈ -$5 (MEIC+) or -$15 (without MEIC+)
```

**Why the surviving side doesn't add extra profit:** The `total_credit` already includes both sides' credits. When the stop debit ≈ total_credit, ALL collected premium is consumed by the stop. The surviving side expiring worthless means no additional cash flow — its credit was already received at entry and spent on the stop debit.

**One-sided entry when stopped:**
```
Collected at entry:     +credit
Stop closes spread:     -2 × credit    (stop_level = 2× for one-sided)
Commission:             -$10           (2 legs entry + 2 legs close)
─────────────────────────────────────
Net P&L:                ≈ -credit - $10  (you lose everything you collected plus commission)
```

**Key asymmetry for what-if analysis:**
- Wrong one-sided → full IC: saves ~$credit (from -$credit to -$5). Typical savings: $290-$445
- Correct one-sided expired → full IC with stopped side: costs ~$credit (from +$credit to -$5). Typical cost: $180-$255
- Correct one-sided expired → full IC both expire: gains ~$credit (extra side also expires). Typical gain: $295

## Appendix H: File References

| File | Purpose | Location |
|------|---------|----------|
| Strategy code | HYDRA strategy | `bots/hydra/strategy.py` |
| Main loop | Entry scheduling, settlement | `bots/hydra/main.py` |
| State file | Daily state persistence | `/opt/calypso/data/hydra_state.json` (VM) |
| Metrics file | Cumulative metrics | `/opt/calypso/data/meic_metrics.json` (VM) |
| Strategy spec | MEIC base specification | `docs/MEIC_STRATEGY_SPECIFICATION.md` |
| Edge cases | 79 analyzed edge cases | `docs/MEIC_EDGE_CASES.md` |
| Bot README | HYDRA hybrid documentation | `bots/hydra/README.md` |
| Daily Summary | Google Sheets tab | "Daily Summary" tab in HYDRA spreadsheet |
| This document | Trading journal | `docs/HYDRA_TRADING_JOURNAL.md` |
