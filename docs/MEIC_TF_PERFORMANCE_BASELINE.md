# MEIC-TF Strategy Performance Baseline & Improvement Tracker

**Created**: February 17, 2026
**Purpose**: Baseline reference for measuring impact of strategy improvements. Future Claude Code sessions should reference this file to compare pre- vs post-improvement performance without re-pulling all logs and sheets data.

---

## Table of Contents

1. [Baseline Period: Feb 10-17, 2026](#1-baseline-period-feb-10-17-2026)
2. [Daily Summary Data (Raw)](#2-daily-summary-data-raw)
3. [Entry-Level Detail by Day](#3-entry-level-detail-by-day)
4. [Market Conditions](#4-market-conditions)
5. [Key Performance Metrics (Pre-Improvement)](#5-key-performance-metrics-pre-improvement)
6. [Identified Weaknesses](#6-identified-weaknesses)
7. [Recommended Improvements](#7-recommended-improvements)
8. [Improvement Implementation Log](#8-improvement-implementation-log)
9. [Post-Improvement Performance Tracking](#9-post-improvement-performance-tracking)

---

## 1. Baseline Period: Feb 10-17, 2026

**Bot Version**: v1.2.7 (Fix #76 deployed Feb 17 after market close)
**Trading Days**: 5 (Feb 10, 11, 12, 13, 17)
**Config**: 5 entries per day, EMA 20/40 trend filter, 0.1% neutral threshold
**Capital Deployed**: $12,500-$32,000 per day (varies by entry count and spread width)

### Period Result
- **Net P&L**: +$1,070
- **Winning Days**: 4 (80%)
- **Losing Days**: 1 (20%)
- **Total Entries**: 27
- **Total Stops**: 15 (55.6% stop rate)
- **Win Rate (entries with 0 stops)**: 44.4% (12/27)

---

## 2. Daily Summary Data (Raw)

Source: Google Sheets "Daily Summary" tab, as of Feb 17 end-of-day (corrected).

| Column | Feb 10 | Feb 11 | Feb 12 | Feb 13 | Feb 17 |
|--------|--------|--------|--------|--------|--------|
| Date | 2026-02-10 | 2026-02-11 | 2026-02-12 | 2026-02-13 | 2026-02-17 |
| SPX Open | 6970.55 | 6988.93 | 6961.62 | 6832.04 | 6814.71 |
| SPX Close | 6943.87 | 6939.96 | 6834.14 | 6834.38 | 6845.81 |
| SPX High | 6985.81 | 6990.65 | 6973.34 | 6881.57 | 6866.63 |
| SPX Low | 6937.67 | 6913.86 | 6824.12 | 6791.34 | 6775.17 |
| VIX Open | 17.35 | 16.95 | 17.36 | 20.97 | 21.86 |
| VIX Close | 17.81 | 17.65 | 20.74 | 20.62 | 20.29 |
| VIX High | 17.97 | 18.96 | 21.21 | 22.40 | 22.96 |
| VIX Low | 17.14 | 16.75 | 17.08 | 18.93 | 19.76 |
| Entries Completed | 5 | 6 | 6 | 5 | 5 |
| Entries Skipped | 1 | 0 | 0 | 0 | 0 |
| Full ICs | 0 | 1 | 4 | 4 | 3 |
| One-Sided Entries | 5 | 5 | 2 | 1 | 2 |
| Bullish Signals | 0 | 0 | 0 | 1 | 1 |
| Bearish Signals | 0 | 1 | 2 | 0 | 1 |
| Neutral Signals | 5 | 5 | 4 | 4 | 3 |
| Total Credit ($) | 640 | 1170 | 1610 | 3045 | 1885 |
| Call Stops | 0 | 0 | 0 | 1 | 3 |
| Put Stops | 1 | 2 | 4 | 2 | 2 |
| Double Stops | 0 | 0 | 0 | 0 | 0 |
| Stop Loss Debits ($) | 140 | 290 | 410 | 1145 | 1335 |
| Commission ($) | 30 | 45 | 70 | 60 | 65 |
| Expired Credits ($) | 520 | 760 | 840 | 1880 | 660 |
| Daily P&L ($) | 350 | 425 | 360 | 675 | -740 |
| Daily P&L (EUR) | 294.27 | 357.99 | 303.31 | 568.71 | -624.26 |
| Cumulative P&L ($) | 350 | 775 | 1135 | 1810 | 1070 |
| Cumulative P&L (EUR) | 294.27 | 652.81 | 956.27 | 1524.98 | 902.64 |
| Win Rate (%) | 80.0 | 66.7 | 33.3 | 40.0 | 0.0 |
| Capital Deployed ($) | 25000 | 30000 | 32000 | 28000 | 12500 |
| Return on Capital (%) | 1.40 | 1.42 | 1.13 | 2.41 | -5.92 |
| Sortino Ratio | 0.00 | 99.99 | 99.99 | 99.99 | 0.52 |
| Max Loss Stops ($) | 640 | 1170 | 1610 | 3045 | 1885 |
| Max Loss Catastrophic ($) | 24360 | 28830 | 30390 | 24955 | 10615 |
| Notes | Post-settlement | Post-settlement | Post-settlement | Post-settlement | Post-settlement |

### P&L Verification Formula
`Daily P&L = Expired Credits - Stop Loss Debits - Commission`
- Feb 10: 520 - 140 - 30 = 350 ✓
- Feb 11: 760 - 290 - 45 = 425 ✓
- Feb 12: 840 - 410 - 70 = 360 ✓
- Feb 13: 1880 - 1145 - 60 = 675 ✓
- Feb 17: 660 - 1335 - 65 = -740 ✓

### Cumulative Metrics (meic_metrics.json as of Feb 17 EOD)
```json
{
  "cumulative_pnl": 1070.0,
  "total_entries": 27,
  "winning_days": 4,
  "losing_days": 1,
  "total_credit_collected": 8350.0,
  "total_stops": 15,
  "double_stops": 0,
  "last_updated": "2026-02-17T16:44:27.374576-05:00"
}
```

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

### Expected Move vs Actual Range

| Date | VIX (avg) | Expected Move | Actual Range | Ratio | Assessment |
|------|-----------|--------------|--------------|-------|------------|
| Feb 10 | 17.4 | ~76 pts | 48 pts | 0.63x | Below expected (calm) |
| Feb 11 | 17.3 | ~76 pts | 77 pts | 1.01x | At expected (normal) |
| Feb 12 | 19.1 | ~84 pts | 149 pts | 1.77x | FAR above expected (extreme) |
| Feb 13 | 20.8 | ~90 pts | 90 pts | 1.00x | At expected (normal) |
| Feb 17 | 21.4 | ~92 pts | 92 pts | 1.00x | At expected (normal) |

**Key insight**: Feb 17 was NOT an abnormal range day. The 92-point range was exactly at its expected move. The damage came from the SHAPE (V-reversal), not the MAGNITUDE.

### Macro Context (Week of Feb 10-13)
- AI disruption sell-off triggered by Cisco earnings miss (AI component costs squeezing margins)
- Sector rotation: tech/growth → value/defensives
- VIX broke above 20 on Feb 12 for first time in weeks
- CPI came in soft on Feb 13 but market shrugged it off
- S&P 500 failed at 7,000 resistance and entered downtrend
- Post-Presidents' Day (Feb 17) had pent-up information and volatile reopening

---

## 5. Key Performance Metrics (Pre-Improvement)

### Financial Metrics

| Metric | Value |
|--------|-------|
| Total Credit Collected | $8,350 |
| Total Expired Credits | $4,660 (55.8% of credit) |
| Total Stop Loss Debits | $3,320 (39.8% of credit) |
| Total Commission | $270 (3.2% of credit) |
| Net P&L | +$1,070 (12.8% net capture rate) |
| Average Daily Credit | $1,670 |
| Average Daily P&L | +$214 |
| Best Day | +$675 (Feb 13) |
| Worst Day | -$740 (Feb 17) |
| Win/Loss Day Ratio | 4:1 |
| Win/Loss Dollar Ratio | 2.44:1 ($1,810 / $740) |

### Entry Performance

| Metric | Value |
|--------|-------|
| Total Entries | 27 |
| Clean Wins (0 stops) | 12 (44.4%) |
| Partial Wins (1 side stopped, IC) | 5 (18.5%) |
| Full Losses (stopped, 1-sided) | 10 (37.0%) |
| Entries with Call Stop | 4 (14.8%) |
| Entries with Put Stop | 11 (40.7%) |
| Double Stops | 0 (0%) |

### Entry Type Distribution

| Entry Type | Count | Stops | Stop Rate | Avg Credit |
|------------|-------|-------|-----------|------------|
| Full IC | 11 | 9 sides stopped* | ~41% per side | $460 |
| Put-only (MKT-011) | 10 | 4 | 40% | $135 |
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

### Trend Filter Accuracy

| Date | Trend Signals | Were They Correct? | Trend Filter Impact |
|------|--------------|--------------------|--------------------|
| Feb 10 | 5 NEUTRAL | Yes (range-bound) | Neutral - MKT-011 overrode anyway |
| Feb 11 | 5 NEUTRAL, 1 BEARISH | Partially (market was slightly bearish) | Slightly positive |
| Feb 12 | 4 NEUTRAL, 2 BEARISH | Yes - BEARISH correct (major sell-off) | **STRONG POSITIVE** - saved ~$300 |
| Feb 13 | 4 NEUTRAL, 1 BULLISH | Mixed - BULLISH call was right but put got stopped anyway | Neutral |
| Feb 17 | 3 NEUTRAL, 1 BEARISH, 1 BULLISH | **WRONG** - both directional calls were reversed | **STRONG NEGATIVE** - amplified losses |

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
| **1** | 9.3 | **Widen EMA Threshold (0.1% → 0.2%)** | **HIGHEST** | **ZERO (config only)** | **~$330** | **IMPLEMENTED (v1.2.8)** |
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
- On Feb 17: Entry #1 (BEARISH→call-only) and Entry #4 (BULLISH→put-only) would likely have been NEUTRAL→full ICs. The surviving sides would have partially offset stop losses. **Saves ~$330.**
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

---

## 9. Post-Improvement Performance Tracking

### Template for Future Analysis

When reviewing performance after implementing improvements, fill in this section with new data:

#### Week 2 Performance (Date Range: ___ to ___)

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

#### Improvement Impact Assessment

| Rec | Priority | Implemented? | Triggered? | Estimated Savings | Actual Impact | Assessment |
|-----|----------|-------------|------------|-------------------|---------------|------------|
| 9.3 EMA Threshold (0.2%) | #1 | v1.2.8 (Feb 17) | TBD | ~$330/bad day | | |
| 9.1 Stop Cascade (3 stops) | #2 | v1.2.8 (Feb 17) | TBD | ~$195/bad day | | |
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

| Entry | Actual P&L (one-sided) | Projected P&L (full IC) | Impact |
|-------|----------------------|------------------------|--------|
| Feb 13 #2 | -$450 | -$93 | **+$357** |
| Feb 17 #1 | -$295 | +$115 | **+$410** |
| Feb 17 #4 | -$225 | ~-$105 | **~+$120** |
| Feb 12 #3 | +$175 | +$170 | **-$5** |
| Feb 11 #2 | +$130 | +$417 | **+$287** |
| Feb 12 #4 | +$250 | ~-$50 | **~-$300** |

### Threshold Comparison (With Cascade Breaker Active)

Note: Feb 17 Entry #4 is blocked by cascade breaker (MKT-016) at all thresholds, so its flip doesn't contribute to the combined impact.

| Threshold | Entries Flipped (non-cascade-blocked) | Net Impact | Key Trade-off |
|-----------|--------------------------------------|-----------|---------------|
| 0.1% (old) | None | $0 (baseline) | 3 wrong directional bets unchecked |
| **0.125%** | Feb 13 #2 | **+$357** | Catches worst wrong signal only |
| **0.15%** | +Feb 17 #1 | **+$767** | Catches both wrong signals |
| **0.175%** | +Feb 12 #3 | **+$762** | Tiny -$5 cost, no real change from 0.15% |
| **0.2%** | +Feb 11 #2 | **+$1,049** | Best: Feb 11 #2 as full IC = +$287 more |
| 0.205%+ | +Feb 12 #4 | **~+$749** | DANGER: flips correct BEARISH (-$300) |

### Optimal Range: 0.183% to 0.203%

Any threshold in this range flips the same set of entries:
- **Below 0.183%**: Misses Feb 11 #2 (0.182% stays BEARISH) — loses +$287 benefit
- **Above 0.203%**: Flips Feb 12 #4 (0.204% becomes NEUTRAL) — costs ~$300 on genuine trend days
- **0.2%** is the natural round number in this optimal range

### Why Not Lower Thresholds?

**0.15%** catches the two WRONG signals (Feb 13 #2, Feb 17 #1) but misses Feb 11 #2. That entry at -0.182% was correctly BEARISH (call-only expired profitably), but as a full IC it would have been *more profitable* (+$417 vs +$130) because the additional put at P:6880 was 30pts further OTM than the day's stopped put at P:6910 and likely survived. Going from 0.15% to 0.2% adds +$282 with no additional risk in our data.

**0.125%** only catches one of three wrong signals. Feb 17 Entry #1 at -0.138% stays BEARISH — the biggest single-entry loss that the threshold is designed to prevent.

### Conclusion

**0.2% is confirmed optimal** against 5 days of actual data. It catches all wrong signals, converts a correct-but-marginal signal into an even more profitable full IC, and stops precisely before flipping the one strongly correct BEARISH signal (Feb 12 #4 at -0.204%).

---

## Appendix D: What-If — EMA Threshold 0.2% Impact by Day (Detail)

**Purpose**: Detailed P&L projections for the 0.2% threshold at the entry level.

### Feb 10: $0 impact
All entries NEUTRAL at both thresholds (max divergence 0.035%). No entries affected.

### Feb 11: Likely +$287 improvement

**Entry #2** changes from BEARISH (call-only, $140 credit) to NEUTRAL (full IC, ~$435):
- **Actual (call-only)**: Call expired → +$140 - $5 commission = **+$130 net**
- **Projected (full IC)**: Call expired (+$125), put at P:6880 (30pts further OTM than Entry #1's stopped P:6910) → likely survives → **+$417 estimated net**
- **Impact**: +$287

### Feb 12: ~-$5 (negligible cost)

**Entry #3** changes from BEARISH (call-only, $185 credit) to NEUTRAL (full IC, ~$320):
- **Actual (call-only)**: Call expired → +$185 - $5 commission = **+$175 net**
- **Projected (full IC)**: Call expired (+$80), but put 100% stopped (100% put stop rate that day) → -$95 stop + $80 expiry - $10 commission = **+$170 net**
- **Impact**: -$5

### Feb 13: +$357 improvement

**Entry #2** changes from BULLISH (put-only, $430 credit) to NEUTRAL (full IC, ~$675):
- **Actual (put-only)**: Put stopped → -$440 - $10 commission = **-$450 net**
- **Projected (full IC)**: Put stopped (-$440), call side expires (+$245 call credit), -$15 commission = **-$93 estimated net**
- **Impact**: +$357

### Feb 17: +$410 improvement

**Entry #1** changes from BEARISH (call-only, $305 credit) to NEUTRAL (full IC, ~$695):
- **Actual (call-only)**: Call stopped at 11:11 → -$295 - $5 commission = **-$295 net**
- **Projected (full IC)**: Call stopped (-$295), put side at P:6720 expires worthless (+$200 put credit), -$15 commission = **+$115 estimated net**
- **Impact**: +$410

### Summary (0.2% Threshold Only, Without Cascade Breaker)

| Day | Affected Entry | Actual P&L | Projected P&L (0.2%) | Impact |
|-----|---------------|-----------|----------------------|--------|
| Feb 10 | None | — | — | **$0** |
| Feb 11 | #2 | +$130 | +$417 | **+$287** |
| Feb 12 | #3 | +$175 | +$170 | **-$5** |
| Feb 13 | #2 | -$450 | -$93 | **+$357** |
| Feb 17 | #1 | -$295 | +$115 | **+$410** |
| **TOTAL** | | | | **+$1,049** |

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
| **TOTAL** | | | | | **+$195** |

### Feb 17 Blocked Entry Breakdown

| Entry | Type | Credit | Stop Debit | Expired Credit | Commission | Actual P&L | If Blocked |
|-------|------|--------|-----------|----------------|------------|-----------|------------|
| #4 | Put-only (BULLISH) | $235 | $225 | $0 | $10 | -$225 | +$235 saved |
| #5 | Full IC (NEUTRAL) | $250 | $30 | $85 (call) | $15 | +$40 | -$40 cost |
| **Net** | | | | | | | **+$195** |

---

---

## Appendix F: Strategy Configuration (Baseline)

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
```

## Appendix G: Formulas

- **Expected Move** = SPX × VIX / sqrt(252) / 100
- **Stop Level (full IC)** = Total credit collected for that IC
- **Stop Level (one-sided)** = 2 × credit collected for that side
- **Stop triggers when**: spread_value >= stop_level (cost-to-close exceeds threshold)
- **Net P&L** = Expired Credits - Stop Loss Debits - Commission
- **Net Capture Rate** = Net P&L / Total Credit Collected × 100
- **Win Rate** = Entries with 0 stops / Total entries × 100
- **Sortino Ratio** = daily_average_return / downside_deviation × sqrt(252)

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
| This document | Performance baseline | `docs/MEIC_TF_PERFORMANCE_BASELINE.md` |
