# MEIC-TF Trading Journal

**Created**: February 17, 2026
**Last Updated**: February 23, 2026
**Purpose**: Day-by-day trading record with entry-level detail, P&L tracking, and improvement impact analysis. Future Claude Code sessions should reference this file instead of re-pulling all logs and sheets data.

---

## Table of Contents

1. [Trading Period: Feb 10-23, 2026](#1-trading-period-feb-10-23-2026)
2. [Daily Summary Data (Raw)](#2-daily-summary-data-raw)
3. [Entry-Level Detail by Day](#3-entry-level-detail-by-day)
4. [Market Conditions](#4-market-conditions)
5. [Key Performance Metrics](#5-key-performance-metrics)
6. [Identified Weaknesses](#6-identified-weaknesses)
7. [Recommended Improvements](#7-recommended-improvements)
8. [Improvement Implementation Log](#8-improvement-implementation-log)
9. [Post-Improvement Performance Tracking](#9-post-improvement-performance-tracking)

---

## 1. Trading Period: Feb 10-23, 2026

**Bot Versions**: v1.2.7 (Feb 10-17), v1.2.8 (Feb 18), v1.2.9 (Feb 18 post-market), v1.3.0 (Feb 19), v1.3.2 (Feb 20-23)
**Trading Days**: 9 (Feb 10, 11, 12, 13, 17, 18, 19, 20, 23)
**Config**: 5 entries per day, EMA 20/40 trend filter
- Feb 10-17: 0.1% neutral threshold, no cascade breaker (baseline)
- Feb 18+: 0.2% neutral threshold (Rec 9.3), cascade breaker at 3 stops (MKT-016)
- Feb 19+: daily loss limit -$500 (MKT-017), early close ROC 2% (MKT-018)
- Feb 20+: progressive call tightening (MKT-020), pre-entry ROC gate (MKT-021)
- Feb 23 post-market: MKT-016/017 removed (v1.3.3), Fix #82 settlement gate (v1.3.4)
**Capital Deployed**: $12,000-$32,000 per day (varies by entry count and spread width)

### Period Result
- **Net P&L**: +$1,640
- **Winning Days**: 6 (66.7%)
- **Losing Days**: 3 (33.3%)
- **Total Entries**: 40
- **Total Stops**: 23 (57.5% stop rate)
- **Win Rate (entries with 0 stops)**: 42.5% (17/40)

---

## 2. Daily Summary Data (Raw)

Source: Google Sheets "Daily Summary" tab. Feb 17 capital corrected from $12,500 to $30,500 (Fix #77 bug dropped entries with surviving sides from daily_state).

| Column | Feb 10 | Feb 11 | Feb 12 | Feb 13 | Feb 17 | Feb 18 | Feb 19 | Feb 20 | **Feb 23** |
|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| Date | 2026-02-10 | 2026-02-11 | 2026-02-12 | 2026-02-13 | 2026-02-17 | 2026-02-18 | 2026-02-19 | 2026-02-20 | **2026-02-23** |
| SPX Open | 6970.55 | 6988.93 | 6961.62 | 6832.04 | 6814.71 | 6848.12 | 6858.05 | 6857.52 | **6877.47** |
| SPX Close | 6943.87 | 6939.96 | 6834.14 | 6834.38 | 6845.81 | 6878.07 | 6861.00 | 6878.72 | **6836.90** |
| SPX High | 6985.81 | 6990.65 | 6973.34 | 6881.57 | 6866.63 | 6909.21 | 6877.89 | 6908.53 | **6914.87** |
| SPX Low | 6937.67 | 6913.86 | 6824.12 | 6791.34 | 6775.17 | 6848.12 | 6836.88 | 6833.05 | **6820.71** |
| VIX Open | 17.35 | 16.95 | 17.36 | 20.97 | 21.86 | 19.73 | 20.42 | 20.46 | **20.56** |
| VIX Close | 17.81 | 17.65 | 20.74 | 20.62 | 20.29 | 19.56 | 20.28 | 19.54 | **21.35** |
| VIX High | 17.97 | 18.96 | 21.21 | 22.40 | 22.96 | 20.21 | 21.06 | 21.21 | **22.04** |
| VIX Low | 17.14 | 16.75 | 17.08 | 18.93 | 19.76 | 18.48 | 19.82 | 18.77 | **19.50** |
| Entries Completed | 5 | 6 | 6 | 5 | 5 | 4 | 4 | 3 | **2** |
| Entries Skipped | 1 | 0 | 0 | 0 | 0 | 1 | 1 | 2 | **3** |
| Full ICs | 0 | 1 | 4 | 4 | 3 | 1 | 2 | 3 | **2** |
| One-Sided Entries | 5 | 5 | 2 | 1 | 2 | 3 | 2 | 0 | **0** |
| Bullish Signals | 0 | 0 | 0 | 1 | 1 | 0 | 0 | 0 | **0** |
| Bearish Signals | 0 | 1 | 2 | 0 | 1 | 0 | 0 | 0 | **0** |
| Neutral Signals | 5 | 5 | 4 | 4 | 3 | 4 | 4 | 3 | **2** |
| Total Credit ($) | 640 | 1170 | 1610 | 3045 | 1885 | 810 | 1265 | 1775 | **915** |
| Call Stops | 0 | 0 | 0 | 1 | 3 | 0 | 0 | 0 | **0** |
| Put Stops | 1 | 2 | 4 | 2 | 2 | 2 | 3 | 1 | **2** |
| Double Stops | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| Stop Loss Debits ($) | 140 | 290 | 410 | 1145 | 1335 | 260 | 380 | 800 | **655** |
| Commission ($) | 30 | 45 | 70 | 60 | 65 | 35 | 45 | 60 | **30** |
| Expired Credits ($) | 520 | 760 | 840 | 1880 | 660 | 610 | 395 | 1550 | **280** |
| Daily P&L ($) | 350 | 425 | 360 | 675 | -740 | 315 | -30 | 690 | **-405** |
| Daily P&L (EUR) | 294.27 | 357.99 | 303.31 | 568.71 | -624.26 | 267.32 | -25.47 | 585.74 | **-344.10** |
| Cumulative P&L ($) | 350 | 775 | 1135 | 1810 | 1070 | 1385 | 1355 | 2045 | **1640** |
| Cumulative P&L (EUR) | 294.27 | 652.81 | 956.27 | 1524.98 | 902.64 | 1175.35 | 1150.55 | 1736.03 | **1393.38** |
| Win Rate (%) | 80.0 | 66.7 | 33.3 | 40.0 | 0.0 | 50.0 | 25.0 | 66.7 | **0.0** |
| Capital Deployed ($) | 25000 | 30000 | 32000 | 28000 | 30500 | 20000 | 23000 | 15000 | **12000** |
| Return on Capital (%) | 1.40 | 1.42 | 1.13 | 2.41 | -2.43 | 1.57 | -0.13 | 4.60 | **-3.38** |
| Sortino Ratio | 0.00 | 99.99 | 99.99 | 99.99 | 11.49 | 14.70 | 1.90 | 6.09 | **2.41** |
| Max Loss Stops ($) | 640 | 1170 | 1610 | 3045 | 1885 | 810 | 1265 | 1775 | **915** |
| Max Loss Catastrophic ($) | 24360 | 28830 | 30390 | 24955 | 28615 | 19190 | 21735 | 13225 | **11085** |
| Early Close | -- | -- | -- | -- | -- | -- | No | Yes, 11:31 ET | **No** |
| Notes | Post-settlement | Post-settlement | Post-settlement | Post-settlement | Post-settlement | Post-settlement (v1.2.8) | Post-settlement (v1.3.0) | Post-settlement (v1.3.2) | **Fix #82 corrected (v1.3.2)** |

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

### Cumulative Metrics (meic_metrics.json as of Feb 23 EOD, corrected)
```json
{
  "cumulative_pnl": 1640.0,
  "total_entries": 40,
  "winning_days": 6,
  "losing_days": 3,
  "total_credit_collected": 13115.0,
  "total_stops": 23,
  "double_stops": 0,
  "last_updated": "2026-02-23"
}
```
**Note**: Feb 23 metrics were manually corrected — Fix #82 (settlement gate lock bug) caused the bot to record -$685 instead of correct -$405 (missing $280 expired call credits). Saxo platform confirms -$405.00 total realized P&L.

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
- This is exactly the scenario MEIC-TF was designed for

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

### Financial Metrics (9 days: Feb 10-23)

| Metric | Value |
|--------|-------|
| Total Credit Collected | $13,115 |
| Total Expired Credits | $7,495 (57.1% of credit) |
| Total Stop Loss Debits | $5,415 (41.3% of credit) |
| Total Commission | $440 (3.4% of credit) |
| Net P&L | +$1,640 (12.5% net capture rate) |
| Average Daily Credit | $1,457 |
| Average Daily P&L | +$182 |
| Best Day | +$690 (Feb 20) |
| Worst Day | -$740 (Feb 17) |
| Win/Loss Day Ratio | 6:3 |
| Win/Loss Dollar Ratio | 2.43:1 ($2,815 / $1,175) |

### Entry Performance

| Metric | Value |
|--------|-------|
| Total Entries | 40 |
| Clean Wins (0 stops) | 17 (42.5%) |
| Partial Wins (1 side stopped, IC) | 15 (37.5%) |
| Full Losses (stopped, 1-sided) | 8 (20.0%) |
| Entries with Call Stop | 4 (10.0%) |
| Entries with Put Stop | 19 (47.5%) |
| Double Stops | 0 (0%) |

### Entry Type Distribution

| Entry Type | Count | Stops | Stop Rate | Avg Credit |
|------------|-------|-------|-----------|------------|
| Full IC | 19 | 14 sides stopped* | ~37% per side | $480 |
| Put-only (MKT-011) | 15 | 8 | 53.3% | $145 |
| Call-only (trend) | 4 | 3 | 75% | $220 |
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
| **Feb 23** | **2** | **70 sec apart (11:01-11:03)** | **3 (#3-5 blocked by MKT-017)** | **$0 (saved)** |

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
| **Feb 23** | **2 NEUTRAL** | **Yes (sustained sell-off, EMA divergence -0.085% to -0.094%)** | **Neutral — full ICs, but call sides survived while puts stopped** |

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

**Code verification**: Line 194 of `bots/meic_tf/strategy.py` reads `self.trend_config.get("ema_neutral_threshold", 0.001)`. Lines 294-299 use strict `>` and `<` operators. Pure config change.

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

---

## 9. Post-Improvement Performance Tracking

### How to Do a Weekly Review

**Step 1: Pull daily summary data from Google Sheets**
- Open the "Calypso_MEIC-TF_Live_Data" spreadsheet → "Daily Summary" tab
- Copy the rows for the review period into the template table below

**Step 2: Pull EMA divergence data from VM logs**
```bash
# Get all trend signal logs for a specific date (replace DATE)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u meic_tf --since 'DATE 14:00' --until 'DATE 21:00' --no-pager | grep -E '(EMA|trend_signal|divergence|BULLISH|BEARISH|NEUTRAL|cascade|MKT-016)'"
```
Note: journalctl timestamps are UTC. Market hours 9:30-4:00 ET = 14:30-21:00 UTC.

**Step 3: Check if cascade breaker triggered**
```bash
# Look for MKT-016 cascade events
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u meic_tf --since 'DATE 14:00' --until 'DATE 21:00' --no-pager | grep -i 'cascade\|MKT-016\|pause.*entry\|skipping.*entry'"
```

**Step 4: Check state file for EMA values (most precise)**
```bash
# View today's state file (has exact ema_20_at_entry / ema_40_at_entry)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso cat /opt/calypso/data/meic_tf_state.json | python3 -m json.tool"
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

**Source**: `journalctl -u meic_tf` on calypso-bot VM, pulled Feb 17 2026.
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

### Summary: Entries Affected by 0.2% Threshold

| Day | Entry | Old Signal | New Signal | Old Type | New Type |
|-----|-------|-----------|-----------|----------|----------|
| Feb 11 | #2 | BEARISH (-0.182%) | NEUTRAL | Call-only | Full IC |
| Feb 12 | #3 | BEARISH (-0.175%) | NEUTRAL | Call-only | Full IC |
| Feb 13 | #2 | BULLISH (+0.105%) | NEUTRAL | Put-only | Full IC |
| Feb 17 | #1 | BEARISH (-0.138%) | NEUTRAL | Call-only | Full IC |
| Feb 17 | #4 | BULLISH (+0.141%)* | NEUTRAL | Put-only | Full IC |

*Corrected from initial ~0.21% estimate. Cascade breaker blocks Entry #4 regardless, so this flip has no practical impact when both improvements are active.

---

## Appendix B: Stop and Entry Timing Data (From VM Logs)

**Source**: `journalctl -u meic_tf` on calypso-bot VM, pulled Feb 17 2026.
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

### Current Config (v1.3.4, deployed Feb 23 post-market)

```
Entries per day: 5
Entry times: 10:05, 10:35, 11:05, 11:35, 12:05 ET
EMA short period: 20
EMA long period: 40
EMA neutral threshold: 0.002 (0.2%)
Min viable credit per side: $1.00 (MKT-011)
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
Progressive call tightening: Yes                  ← (MKT-020, v1.3.2)
Pre-entry ROC gate: 2.0%                          ← (MKT-021, v1.3.2)
```

**Config location**: `bots/meic_tf/config/config.json` on VM at `/opt/calypso/`. Template at `bots/meic_tf/config/config.json.template` in repo.

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
| Strategy code | MEIC-TF strategy | `bots/meic_tf/strategy.py` |
| Main loop | Entry scheduling, settlement | `bots/meic_tf/main.py` |
| State file | Daily state persistence | `/opt/calypso/data/meic_tf_state.json` (VM) |
| Metrics file | Cumulative metrics | `/opt/calypso/data/meic_metrics.json` (VM) |
| Strategy spec | MEIC base specification | `docs/MEIC_STRATEGY_SPECIFICATION.md` |
| Edge cases | 79 analyzed edge cases | `docs/MEIC_EDGE_CASES.md` |
| Bot README | MEIC-TF hybrid documentation | `bots/meic_tf/README.md` |
| Daily Summary | Google Sheets tab | "Daily Summary" tab in MEIC-TF spreadsheet |
| This document | Trading journal | `docs/MEIC_TF_TRADING_JOURNAL.md` |
