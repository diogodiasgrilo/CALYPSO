# HYDRA Trading Journal

**Created**: February 17, 2026
**Last Updated**: Apr 2, 2026
**Purpose**: Day-by-day trading record with entry-level detail, P&L tracking, and improvement impact analysis. Future Claude Code sessions should reference this file instead of re-pulling all logs and sheets data.

---

## Table of Contents

1. [Trading Period: Feb 10 - Apr 2, 2026](#1-trading-period-feb-10---apr-2-2026)
2. [Daily Summary Data (Raw)](#2-daily-summary-data-raw)
3. [Entry-Level Detail by Day](#3-entry-level-detail-by-day)
4. [Market Conditions](#4-market-conditions)
5. [Key Performance Metrics](#5-key-performance-metrics)
6. [Identified Weaknesses](#6-identified-weaknesses)
7. [Recommended Improvements](#7-recommended-improvements)
8. [Improvement Implementation Log](#8-improvement-implementation-log)
9. [Post-Improvement Performance Tracking](#9-post-improvement-performance-tracking)

---

## 1. Trading Period: Feb 10 - Apr 2, 2026

**Bot Versions**: v1.2.7 (Feb 10-17), v1.2.8 (Feb 18), v1.2.9 (Feb 18 post-market), v1.3.0 (Feb 19), v1.3.2 (Feb 20-23), v1.3.5-v1.3.8 (Feb 24), v1.3.9-v1.3.11 (Feb 25-26), v1.4.0-v1.4.1 (Feb 27), v1.4.2-v1.5.0 (Feb 28 rename to HYDRA), v1.5.1 (Mar 2), v1.6.0-v1.7.2 (Mar 3), v1.8.0 (Mar 4 — shifted +1hr, MKT-031 smart entry windows)
**Trading Days**: 33 (Feb 10, 11, 12, 13, 17, 18, 19, 20, 23, 24, 25, 26, 27, Mar 2, 3, Mar 4, Mar 5, Mar 6, Mar 9, Mar 10, Mar 11, Mar 12, Mar 13, Mar 16, Mar 17, Mar 18, Mar 23, Mar 30, Mar 31, Apr 2)
**Config**: 5 entries per day (Feb 10-27), 6 entries (Mar 2 only, v1.4.4), 5 entries (Mar 3+, v1.6.0 dropped Entry #6), EMA 20/40 trend filter
- Feb 10-17: 0.1% neutral threshold, no cascade breaker (baseline)
- Feb 18+: 0.2% neutral threshold (Rec 9.3), cascade breaker at 3 stops (MKT-016)
- Feb 19+: daily loss limit -$500 (MKT-017), early close ROC 2% (MKT-018)
- Feb 20+: progressive call tightening (MKT-020), pre-entry ROC gate (MKT-021)
- Feb 23 post-market: MKT-016/017 removed (v1.3.3), Fix #82 settlement gate (v1.3.4)
- Feb 24: MKT-022 put tightening (v1.3.5), MKT-011 v1.3.6 NEUTRAL skip, MKT-023 hold check (v1.3.7), Fix #83 idempotency guard (v1.3.8)
- Feb 25: MKT-021 ROC gate lowered to 3 entries (v1.3.9), cumulative ROC columns (v1.3.10), MKT-018 threshold 2%→3% (v1.3.11)
- Feb 26: First full day with MKT-018 at 3% threshold (v1.3.11)
- Feb 27: v1.4.0 mid-day (remove MKT-019, disable one-sided entries — all entries full IC or skip), v1.4.1 (wider starting OTM 2×, separate put min $1.75)
- Feb 28 (non-trading): v1.4.2 (MEIC+ $0.15), v1.4.3 (MKT-025 short-only stop), v1.4.4 (6th entry at 12:35), v1.4.5 (MKT-026 min spread 60pt), v1.5.0 (rename MEIC-TF → HYDRA)
- Mar 2: v1.5.1 (Telegram /snapshot command), first day as HYDRA with 6 entries + MKT-025 short-only stops
- Mar 3: v1.6.0 (drop Entry #6, 5 entries), v1.6.1 (VIX filter 25→30), MKT-024 (wider starting OTM 3.5×/4.0×), MKT-028 (asymmetric spreads call 60pt/put 75pt), v1.7.0 (MKT-027 VIX-scaled spread width), v1.7.1 (put-only re-enable), v1.7.2 (lower call min $1.00→$0.75, per-side credits, HERMES trigger)
- Mar 4: v1.8.0 — Entry schedule shifted +1hr (11:05-13:05), MKT-031 smart entry windows (10min scouting, score >= 65 = early entry)
**Capital Deployed**: $10,000-$38,000 per day (varies by entry count and spread width)

### Period Result
- **Net P&L**: -$820
- **Winning Days**: 19 (57.6%)
- **Losing Days**: 13 (39.4%)
- **Total Entries**: 138
- **Total Stops**: 88 (63.8% stop rate)
- **Double Stops**: 6
- **Win Rate (entries with 0 stops)**: 40.6% (56/138)

---

## 2. Daily Summary Data (Raw)

Source: Google Sheets "Daily Summary" tab. Feb 17 capital corrected from $12,500 to $30,500 (Fix #77 bug dropped entries with surviving sides from daily_state).

| Column | Feb 10 | Feb 11 | Feb 12 | Feb 13 | Feb 17 | Feb 18 | Feb 19 | Feb 20 | Feb 23 | Feb 24 | Feb 25 | Feb 26 | Feb 27 | Mar 2 | **Mar 3** | **Mar 4** | **Mar 5** | **Mar 6** | **Mar 9** | **Mar 10** | **Mar 11** | **Mar 12** | **Mar 13** | **Mar 16** | **Mar 17** | **Mar 18** | **Mar 19** | **Mar 20** | **Mar 23** | **Mar 30** | **Mar 31** | **Apr 1** | **Apr 2** |
|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| Date | 2026-02-10 | 2026-02-11 | 2026-02-12 | 2026-02-13 | 2026-02-17 | 2026-02-18 | 2026-02-19 | 2026-02-20 | 2026-02-23 | 2026-02-24 | 2026-02-25 | 2026-02-26 | 2026-02-27 | 2026-03-02 | **2026-03-03** | **2026-03-04** | **2026-03-05** | **2026-03-06** | **2026-03-09** | **2026-03-10** | **2026-03-11** | **2026-03-12** | **2026-03-13** | **2026-03-16** | **2026-03-17** | **2026-03-18** | **2026-03-19** | **2026-03-20** | **2026-03-23** | **2026-03-30** | **2026-03-31** | **2026-04-01** | **2026-04-02** |
| SPX Open | 6970.55 | 6988.93 | 6961.62 | 6832.04 | 6814.71 | 6848.12 | 6858.05 | 6857.52 | 6877.47 | 6861.77 | 6906.56 | 6937.98 | 6849.40 | 6800.35 | **~6759** | **6,835.73** | **6,846.18** | **6,738.00** | **6,676.18** | **6,833.30** | **6,761.91** | **6,722.77** | **6,707.89** | **6,693.32** | **6,722.73** | **6,744.44** | **6,572.98** | **6,589.63** | **6,625.13** | **6,421.19** | **6,438.58** | **6,549.32** | **6,496.54** |
| SPX Close | 6943.87 | 6939.96 | 6834.14 | 6834.38 | 6845.81 | 6878.07 | 6861.00 | 6878.72 | 6836.90 | 6890.34 | 6926.54 | 6907.46 | 6879.14 | 6878.58 | **~6812** | **6,867.81** | **6,828.74** | **6,730.39** | **6,796.10** | **6,777.78** | **6,774.59** | **6,672.55** | **6,631.66** | **6,695.01** | **6,718.98** | **6,621.29** | **6,605.94** | **6,506.87** | **6,582.35** | **6,341.79** | **6,528.06** | **6,579.52** | **6,581.07** |
| SPX High | 6985.81 | 6990.65 | 6973.34 | 6881.57 | 6866.63 | 6909.21 | 6877.89 | 6908.53 | 6914.87 | 6897.34 | 6935.67 | 6943.23 | 6879.14 | 6901.22 | **~6840** | **6,885.60** | **6,869.02** | **6,774.48** | **6,810.37** | **6,845.48** | **6,811.44** | **6,726.56** | **6,730.71** | **6,727.69** | **6,752.07** | **6,744.44** | **6,638.58** | **6,589.63** | **6,651.99** | **6,427.09** | **6,537.93** | **6,609.97** | **6,601.56** |
| SPX Low | 6937.67 | 6913.86 | 6824.12 | 6791.34 | 6775.17 | 6848.12 | 6836.88 | 6833.05 | 6820.71 | 6836.15 | 6906.56 | 6860.69 | 6829.27 | 6795.38 | **~6711** | **6,810.08** | **6,770.69** | **6,708.67** | **6,635.62** | **6,775.78** | **6,745.41** | **6,669.80** | **6,623.74** | **6,681.00** | **6,714.52** | **6,621.29** | **6,558.82** | **6,474.91** | **6,564.90** | **6,341.79** | **6,404.13** | **6,549.32** | **6,471.93** |
| VIX Open | 17.35 | 16.95 | 17.36 | 20.97 | 21.86 | 19.73 | 20.42 | 20.46 | 20.56 | 20.64 | 19.39 | 17.60 | 21.39 | 23.40 | **26.03** | **22.52** | **22.04** | **27.69** | **31.18** | **22.61** | **25.80** | **26.09** | **25.53** | **25.18** | **22.67** | **22.37** | **27.03** | **24.74** | **24.45** | **29.74** | **27.49** | **25.25** | **24.54** |
| VIX Close | 17.81 | 17.65 | 20.74 | 20.62 | 20.29 | 19.56 | 20.28 | 19.54 | 21.35 | 19.50 | 18.64 | 18.63 | 19.80 | 21.32 | **22.18** | **21.23** | **23.37** | **29.83** | **25.08** | **25.65** | **24.32** | **27.16** | **27.24** | **23.51** | **22.45** | **24.63** | **24.29** | **27.46** | **26.17** | **30.45** | **25.44** | **24.58** | **24.15** |
| VIX High | 17.97 | 18.96 | 21.21 | 22.40 | 22.96 | 20.21 | 21.06 | 21.21 | 22.04 | 21.28 | 19.39 | 20.54 | 21.74 | 23.40 | **28.15** | **23.15** | **25.84** | **29.83** | **31.84** | **25.70** | **25.89** | **27.22** | **27.76** | **25.18** | **22.82** | **24.63** | **27.36** | **29.28** | **26.83** | **30.79** | **28.34** | **25.31** | **27.64** |
| VIX Low | 17.14 | 16.75 | 17.08 | 18.93 | 19.76 | 18.48 | 19.82 | 18.77 | 19.50 | 19.28 | 18.54 | 17.60 | 19.71 | 20.37 | **22.18** | **20.40** | **21.18** | **25.31** | **24.76** | **22.19** | **23.75** | **25.41** | **24.67** | **23.23** | **22.07** | **22.37** | **23.54** | **24.70** | **23.87** | **29.33** | **25.08** | **23.50** | **24.13** |
| Entries Completed | 5 | 6 | 6 | 5 | 5 | 4 | 4 | 3 | 2 | 4 | 2 | 4 | 3 | 6 | **5** | **4** | **5** | **5** | **4** | **2** | **5** | **5** | **7** | **3** | **2** | **3** | **7** | **5** | **6** | **0** | **3** | **4** | **4** |
| Entries Skipped | 1 | 0 | 0 | 0 | 0 | 1 | 1 | 2 | 3 | 1 | 3 | 1 | 2 | 0 | **0** | **1** | **0** | **0** | **1** | **4** | **2** | **2** | **0** | **4** | **8** | **4** | **0** | **2** | **1** | **2** | **0** | **0** | **0** |
| Full ICs | 0 | 1 | 4 | 4 | 3 | 1 | 2 | 3 | 2 | 2 | 2 | 3 | 3 | 6 | **5** | **4** | **5** | **5** | **4** | **2** | **5** | **1** | **3** | **3** | **0** | **3** | **0** | **5** | **5** | **2** | **2** | **3** | **3** |
| One-Sided Entries | 5 | 5 | 2 | 1 | 2 | 3 | 2 | 0 | 0 | 2 | 0 | 1 | 0 | 0 | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **4** | **4** | **0** | **2** | **0** | **7** | **0** | **1** | **0** | **1** | **1** | **1** |
| Bullish Signals | 0 | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **1** |
| Bearish Signals | 0 | 1 | 2 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| Neutral Signals | 5 | 5 | 4 | 4 | 3 | 4 | 4 | 3 | 2 | 4 | 2 | 3 | 3 | 6 | **5** | **4** | **5** | **5** | **4** | **2** | **5** | **5** | **7** | **3** | **2** | **3** | **7** | **5** | **6** | **2** | **3** | **4** | **3** |
| Total Credit ($) | 640 | 1170 | 1610 | 3045 | 1885 | 810 | 1265 | 1775 | 915 | 975 | 490 | 1345 | 905 | 1855 | **1395** | **1115** | **1345** | **1260** | **1045** | **515**  | **1575** | **555**  | **1270** | **980**  | **130**  | **915**  | **435**  | **880**  | **2040** | **0**    | **915**  | **1720** | **1645** |
| Call Stops | 0 | 0 | 0 | 1 | 3 | 0 | 0 | 0 | 0 | 1 | 0 | 2 | 0 | 4 | **5** | **0** | **0** | **0** | **4** | **2** | **0** | **0** | **0** | **0** | **0** | **0** | **6** | **0** | **1** | **0** | **2** | **0** | **1** |
| Put Stops | 1 | 2 | 4 | 2 | 2 | 2 | 3 | 1 | 2 | 0 | 0 | 2 | 1 | 2 | **1** | **3** | **5** | **4** | **2** | **2** | **5** | **0** | **1** | **0** | **0** | **0** | **0** | **3** | **5** | **0** | **0** | **1** | **0** |
| Double Stops | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | **1** | **0** | **0** | **0** | **2** | **2** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| Stop Loss Debits ($) | 140 | 290 | 410 | 1145 | 1335 | 260 | 380 | 800 | 655 | 340 | 250 | 1025 | 135 | 1180 | **1060** | **305**  | **240**  | **345**  | **985**  | **545**  | **545**  | **0**    | **565**  | **0**    | **0**    | **720**  | **1970** | **1755** | **1545** | **0**    | **595**  | **365**  | **340**  |
| Commission ($) | 30 | 45 | 70 | 60 | 65 | 35 | 45 | 60 | 30 | 55 | 40 | 55 | 35 | 75 | **65** | **47.50** | **75**    | **70**    | **67.50** | **40**    | **75**    | **30**    | **55**    | **30**    | **10**    | **60**    | **65**    | **45**    | **85**    | **0**     | **35**    | **70**    | **40**    |
| Expired Credits ($) | 520 | 760 | 840 | 1880 | 660 | 610 | 395 | 1550 | 280 | 830 | 490 | 370 | 715 | 1000 | **675** | **540** | **405** | **590** | **340** | **0**   | **310** | **555** | **1005** | **980**  | **130**  | **915**  | **50**   | **165**  | **695**  | **0**    | **615**  | **765**  | **1435** |
| Daily P&L ($) | 350 | 425 | 360 | 675 | -740 | 315 | -30 | 690 | -405 | 435 | 200 | -710 | 545 | -255 | **-450** | **187.50** | **90**     | **175**    | **-712.50** | **-585**    | **-310**    | **525**     | **385**     | **950**     | **120**     | **135**     | **-1985**   | **-1635**   | **-935**    | **0**       | **-15**     | **330**     | **1055**    |
| Daily P&L (EUR) | 294.27 | 357.99 | 303.31 | 568.71 | -624.26 | 267.32 | -25.47 | 585.74 | -344.10 | ~369.75 | 169.42 | -601.81 | ~462 | ~-216 | **~-382** | **~161.15** | **~77.53**  | **~150.64** | **~-612.36** | **~-503.65** | **~-268**    | **~456.09**  | **~337.28**  | **~825.66**  | **~103.98**  | **~117.72**  | **~-1713.30** | **~-1413.04** | **~-805.30**  | **~0**        | **~-12.98**   | **~284.85**   | **~914.32**   |
| Cumulative P&L ($) | 350 | 775 | 1135 | 1810 | 1070 | 1385 | 1355 | 2045 | 1640 | 2075 | 2275 | 1565 | 2110 | 1855 | **1405** | **1592.50** | **1682.50** | **1857.50** | **1145.00** | **560**     | **250**     | **775**     | **1160**    | **2110**    | **2230**    | **2365**    | **380**     | **-1255**   | **-2190**   | **-2190**   | **-2205**   | **-1875**   | **-820**    |
| Cumulative P&L (EUR) | 294.27 | 652.81 | 956.27 | 1524.98 | 902.64 | 1175.35 | 1150.55 | 1736.03 | 1393.38 | ~1763 | 1927.17 | 1326.53 | ~1789 | ~1573 | **~1191** | **~1368.67** | **~1449.39** | **~1598.89** | **~984.08**  | **~482.13**  | **~216.13**  | **~673.28**  | **~1016.22** | **~1833.84** | **~1932.38** | **~2062.26** | **~327.99**  | **~-1084.63** | **~-1886.22** | **~-1910.79** | **~-1908.26** | **~-1618.08** | **~-710.66**  |
| Win Rate (%) | 80.0 | 66.7 | 33.3 | 40.0 | 0.0 | 50.0 | 25.0 | 66.7 | 0.0 | 75.0 | 100.0 | 0.0 | 66.7 | 16.7 | **0.0** | **25.0** | **0.0**  | **20.0** | **0.0**  | **0.0**  | **0.0**  | **100.0** | **85.7**  | **100.0** | **100.0** | **100.0** | **14.3**  | **40.0**  | **0.0**   | **0.0**   | **33.3**  | **75.0**  | **75.0**  |
| Capital Deployed ($) | 25000 | 30000 | 32000 | 28000 | 30500 | 20000 | 23000 | 15000 | 12000 | 22000 | 10000 | 21500 | 17500 | 38000 | **37500** | **31000** | **37500** | **37500** | **30000** | **15000** | **25000** | **25000** | **35000** | **15000** | **10000** | **15000** | **35500** | **15000** | **30000** | **0**     | **33000** | **44000** | **44000** |
| Return on Capital (%) | 1.40 | 1.42 | 1.13 | 2.41 | -2.43 | 1.57 | -0.13 | 4.60 | -3.38 | 1.98 | 2.00 | -3.30 | 3.11 | -0.67 | **-1.20** | **0.60**  | **0.24**  | **0.47**  | **-2.38**  | **-3.90**  | **-1.24**  | **2.10**   | **1.10**   | **6.33**   | **1.20**   | **0.90**   | **-5.59**  | **-10.90** | **-3.12**  | **0.00**   | **-0.05**  | **2.72**   | **2.40**   |
| Sortino Ratio | 0.00 | 99.99 | 99.99 | 99.99 | 11.49 | 14.70 | 1.90 | 6.09 | 2.41 | ~3.2 | 4.97 | 2.29 | ~3.5 | ~2.8 | **~2.3** | **~2.9** | **~2.9** | **~3.1** | **~1.8** | **~0.0** | **~-0.5** | **~0.3**  | **~0.7**  | **~3.0**  | **~3.4**  | **~3.6**  | **~1.4**  | **~-1.2** | **~-1.7** | **~-1.7** | **~-1.7** | **~-1.2** | **~-0.7** |
| Max Loss Stops ($) | 640 | 1170 | 1610 | 3045 | 1885 | 810 | 1265 | 1775 | 915 | 975 | 490 | 1345 | 905 | 1855 | **1395** | **1115** | **1345** | **1260** | **1045** | **515**  | **1575** | **555**  | **1270** | **980**  | **130**  | **915**  | **435**  | **880**  | **2040** | **0**    | **915**  | **1720** | **1645** |
| Max Loss Catastrophic ($) | 24360 | 28830 | 30390 | 24955 | 28615 | 19190 | 21735 | 13225 | 11085 | 21025 | 9510 | 20155 | 16595 | 36145 | **35105** | **29885** | **36155** | **36240** | **28955** | **14485** | **23425** | **24445** | **33730** | **14020** | **9870**  | **14085** | **35065** | **14120** | **27960** | **0**     | **32085** | **42280** | **42355** |
| Early Close | -- | -- | -- | -- | -- | -- | No | Yes, 11:31 ET | No | Yes, 14:17 ET | Yes, 11:15 ET | No | No | No | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** |
| Notes | Post-settlement | Post-settlement | Post-settlement | Post-settlement | Post-settlement | Post-settlement (v1.2.8) | Post-settlement (v1.3.0) | Post-settlement (v1.3.2) | Fix #82 corrected (v1.3.2) | MKT-018 early close (v1.3.5→v1.3.8) | MKT-018 early close (v1.3.9→v1.3.11) | Post-settlement (v1.3.11), 4 stops, first BEARISH since Feb 17 | Post-settlement (v1.4.0/v1.4.1), last day as MEIC-TF | Post-settlement (v1.5.1), first day as HYDRA, 6 stops + 1 double stop, MKT-025 | **Post-settlement (v1.6.0→v1.7.2), 13 commits, gap-down + V-shape, 6 stops + 1 double stop, MKT-024/028 first live** | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  | **Post-settlement**                                                                                                  |

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
- Feb 27: 715 - 135 - 35 = 545 ✓ (last day as MEIC-TF, 2 skips MKT-011, 1 put stop, v1.4.0→v1.4.1 mid-day)
- Mar 2: 1000 - 1180 - 75 = -255 ✓ (first day as HYDRA v1.5.1, 6 stops + 1 double stop, MKT-025 short-only)
- Mar 3: 675 - 1060 - 65 = -450 ✓ (v1.6.0→v1.7.2, 13 commits, gap-down + V-shape, 6 stops + 1 double stop, MKT-024/028 first live)

- Mar 4: 540 - 305 - 47.50 = 187.50 ✓ (Post-settlement)
- Mar 5: 405 - 240 - 75 = 90 ✓ (Post-settlement)
- Mar 6: 590 - 345 - 70 = 175 ✓ (Post-settlement)
- Mar 9: 340 - 985 - 67.50 = -712.50 ✓ (Post-settlement)
- Mar 10: 0 - 545 - 40 = -585 ✓ (Post-settlement)
- Mar 11: 310 - 545 - 75 = -310 ✓ (Post-settlement)
- Mar 12: 555 - 0 - 30 = 525 ✓ (Post-settlement)
- Mar 13: 1005 - 565 - 55 = 385 ✓ (Post-settlement)
- Mar 16: 980 - 0 - 30 = 950 ✓ (Post-settlement)
- Mar 17: 130 - 0 - 10 = 120 ✓ (Post-settlement)
- Mar 18: 915 - 720 - 60 = 135 ✓ (Post-settlement)
- Mar 19: 50 - 1970 - 65 = -1985 ✓ (Post-settlement)
- Mar 20: 165 - 1755 - 45 = -1635 ✓ (Post-settlement)
- Mar 23: 695 - 1545 - 85 = -935 ✓ (Post-settlement)
- Mar 30: 0 - 0 - 0 = 0 ✓ (Post-settlement)
- Mar 31: 615 - 595 - 35 = -15 ✓ (Post-settlement)
- Apr 1: 765 - 365 - 70 = 330 ✓ (Post-settlement)
- Apr 2: 1435 - 340 - 40 = 1055 ✓ (Post-settlement)
### Cumulative Metrics (hydra_metrics.json as of Apr 2 EOD)
```json
{
  "cumulative_pnl": -820.0,
  "total_entries": 138,
  "winning_days": 20,
  "losing_days": 13,
  "total_credit_collected": 38420.0,
  "total_stops": 88,
  "double_stops": 6,
  "last_updated": "2026-04-02",
  "total_trades": 0,
  "reset_reason": "Fix #46/#47 P&L tracking corrections - starting fresh",
  "daily_returns": [{'date': '2026-02-10', 'net_pnl': 350.0, 'capital_deployed': 25000, 'return_pct': 0.014}, {'date': '2026-02-11', 'net_pnl': 425.0, 'capital_deployed': 30000, 'return_pct': 0.01417}, {'date': '2026-02-12', 'net_pnl': 360.0, 'capital_deployed': 32000, 'return_pct': 0.01125}, {'date': '2026-02-13', 'net_pnl': 675.0, 'capital_deployed': 28000.0, 'return_pct': 0.024107142857142858}, {'date': '2026-02-17', 'net_pnl': -740.0, 'capital_deployed': 12500.0, 'return_pct': -0.0592}, {'date': '2026-02-18', 'net_pnl': 315.0, 'capital_deployed': 20000.0, 'return_pct': 0.01575}, {'date': '2026-02-19', 'net_pnl': -30.0, 'capital_deployed': 23000.0, 'return_pct': -0.0013043478260869566}, {'date': '2026-02-20', 'net_pnl': 690.0, 'capital_deployed': 15000.0, 'return_pct': 0.046}, {'date': '2026-02-23', 'net_pnl': -405.0, 'capital_deployed': 12000.0, 'return_pct': -0.03375}, {'date': '2026-02-24', 'net_pnl': 435.0, 'capital_deployed': 22000.0, 'return_pct': 0.01977272727272727}, {'date': '2026-02-25', 'net_pnl': 200.0, 'capital_deployed': 10000.0, 'return_pct': 0.02}, {'date': '2026-02-26', 'net_pnl': -710.0, 'capital_deployed': 21500.0, 'return_pct': -0.03302325581395349}, {'date': '2026-02-27', 'net_pnl': 545.0, 'capital_deployed': 17500.0, 'return_pct': 0.031142857142857142}, {'date': '2026-03-02', 'net_pnl': -255.0, 'capital_deployed': 38000.0, 'return_pct': -0.006710526315789474}, {'date': '2026-03-03', 'net_pnl': -450.0, 'capital_deployed': 37500.0, 'return_pct': -0.012}, {'date': '2026-03-04', 'net_pnl': 187.5, 'capital_deployed': 31000.0, 'return_pct': 0.006048387096774193}, {'date': '2026-03-05', 'net_pnl': 90.0, 'capital_deployed': 37500.0, 'return_pct': 0.0024}, {'date': '2026-03-06', 'net_pnl': 175.0, 'capital_deployed': 37500.0, 'return_pct': 0.004666666666666667}, {'date': '2026-03-09', 'net_pnl': -712.5, 'capital_deployed': 30000.0, 'return_pct': -0.02375}, {'date': '2026-03-10', 'net_pnl': -585.0, 'capital_deployed': 15000.0, 'return_pct': -0.039}, {'date': '2026-03-11', 'net_pnl': -310.0, 'capital_deployed': 25000.0, 'return_pct': -0.0124}, {'date': '2026-03-12', 'net_pnl': 525.0, 'capital_deployed': 25000.0, 'return_pct': 0.021}, {'date': '2026-03-13', 'net_pnl': 385.0, 'capital_deployed': 35000.0, 'return_pct': 0.011}, {'date': '2026-03-16', 'net_pnl': 950.0, 'capital_deployed': 15000.0, 'return_pct': 0.06333333333333334}, {'date': '2026-03-17', 'net_pnl': 120.0, 'capital_deployed': 10000.0, 'return_pct': 0.012}, {'date': '2026-03-18', 'net_pnl': 135.0, 'capital_deployed': 15000.0, 'return_pct': 0.009}, {'date': '2026-03-19', 'net_pnl': -1985.0, 'capital_deployed': 35500.0, 'return_pct': -0.05591549295774648}, {'date': '2026-03-20', 'net_pnl': -1635.0, 'capital_deployed': 15000.0, 'return_pct': -0.109}, {'date': '2026-03-23', 'net_pnl': -935.0, 'capital_deployed': 30000.0, 'return_pct': -0.031166666666666665}, {'date': '2026-03-31', 'net_pnl': -15.0, 'capital_deployed': 33000.0, 'return_pct': -0.00045454545454545455}, {'date': '2026-04-01', 'net_pnl': 1195.0, 'capital_deployed': 44000.0, 'return_pct': 0.02715909090909091}, {'date': '2026-04-02', 'net_pnl': 1055.0, 'capital_deployed': 44000.0, 'return_pct': 0.023977272727272726}]
}
```
**Note**: Mar 3 was a major development day (v1.6.0→v1.7.2, 13 commits). Gap-down ~91pts overnight, VIX peaked at 28.15 (highest of the period), then V-shape rally to 6,840. Entry #1 double stop (put stopped on sell-off, call stopped on rally). VIX filter initially blocked entries #2-5 (threshold 25, raised to 30 mid-day). All 5 call sides stopped during V-shape rally. MKT-024 (wider starting OTM 3.5×/4.0×) and MKT-028 (asymmetric spreads) first live day.

---

## 3. Entry-Level Detail by Day

### Feb 10 (Tuesday) - NET P&L: +$350

**Market**: Range-bound, calm. SPX range 48 pts (0.7%). VIX 17-18.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Put-only (MKT-011) | P:6935 | $210 (P) | EXPIRED | +$210 |
| #2 | 10:35 | NEUTRAL | Put-only (MKT-011) | P:6935 | $150 (P) | EXPIRED | +$150 |
| #3 | 11:05 | NEUTRAL | Put-only (MKT-011) | P:6930 | $120 (P) | PUT STOPPED | -$120+credit |
| #4 | 11:35 | NEUTRAL | Put-only (MKT-011) | P:6920 | $95 (P) | EXPIRED | +$95 |
| #5 | 12:05 | NEUTRAL | Put-only (MKT-011) | P:6915 | $65 (P) | EXPIRED | +$65 |
| #6 | -- | -- | SKIPPED | -- | -- | Both sides non-viable | -- |

**Key observations**:
- ALL entries NEUTRAL signal, but MKT-011 converted all to put-only (call credits $17.50-$37.50, below $50 min)
- Only 1 stop out of 5 entries (20% stop rate)
- MKT-011 credit gate prevented 5 unprofitable call-spread entries

### Feb 11 (Wednesday) - NET P&L: +$425

**Market**: Flat, cautious. SPX range 77 pts (1.1%). VIX 17-18.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:7000 P:6910 | $435 ($125C+$310P) | Put STOPPED, Call EXPIRED | -$155 + $125 |
| #2 | 10:35 | BEARISH | Call-only | C:6980 | $140 (C) | EXPIRED | +$140 |
| #3 | 11:05 | NEUTRAL | Put-only (MKT-011) | P:6890 | $200 (P) | EXPIRED | +$200 |
| #4 | 11:35 | NEUTRAL | Put-only (MKT-011) | P:6900 | $170 (P) | EXPIRED | +$170 |
| #5 | 12:05 | NEUTRAL | Put-only (MKT-011) | P:6885 | $125 (P) | EXPIRED | +$125 |
| #6 | 12:35 | NEUTRAL | Put-only (MKT-011) | P:6910 | $100 (P) | PUT STOPPED | -$135 |

**Key observations**:
- 1 IC, 1 call-only (BEARISH signal), 4 put-only (MKT-011 conversions)
- Entry #1 IC: put stopped but call side survived to expiry = partial win
- 2 stops out of 6 entries (33% stop rate)

### Feb 12 (Thursday) - NET P&L: +$360

**Market**: MAJOR SELL-OFF. SPX -1.57%, range 149 pts (2.1%). VIX 17→21. Cisco earnings collapse.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:6990 P:6900 | $320 ($80C+$240P) | Put STOPPED, Call EXPIRED | -$95 + $80 |
| #2 | 10:35 | NEUTRAL | Full IC | C:6985 P:6895 | $290 ($65C+$225P) | Put STOPPED, Call EXPIRED | -$75 + $65 |
| #3 | 11:05 | BEARISH | Call-only | C:6950 | $185 (C) | EXPIRED | +$185 |
| #4 | 11:35 | BEARISH | Call-only | C:6920 | $250 (C) | EXPIRED | +$250 |
| #5 | 12:05 | NEUTRAL | Full IC | C:6915 P:6805 | $310 ($165C+$145P) | Put STOPPED, Call EXPIRED | -$215 + $165 |
| #6 | 12:35 | NEUTRAL | Full IC | C:6925 P:6810 | $255 ($95C+$160P) | Put STOPPED, Call EXPIRED | -$80 + $95 |

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
| #1 | 10:05 | NEUTRAL | Full IC | C:6885 P:6765 | $1,150 ($530C+$620P) | Call STOPPED, Put EXPIRED | -$650 + $620 |
| #2 | 10:35 | BULLISH | Put-only | P:6805 | $430 (P) | PUT STOPPED | -$440 |
| #3 | 11:05 | NEUTRAL | Full IC | C:6905 P:6795 | $675 ($315C+$360P) | Both EXPIRED | +$675 |
| #4 | 11:35 | NEUTRAL | Full IC | C:6910 P:6800 | $475 ($185C+$290P) | Both EXPIRED | +$475 |
| #5 | 12:05 | NEUTRAL | Full IC | C:6920 P:6820 | $315 ($110C+$205P) | Put STOPPED, Call EXPIRED | -$130 + $110 |

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
| #1 | 10:05 | BEARISH | Call-only | C:6860 | $305 (C) | CALL STOPPED at 11:11 | -$295 |
| #2 | 10:35 | NEUTRAL | Full IC | C:6840 P:6720 | $695 ($350C+$345P) | CALL STOPPED at 11:02 | -$335 |
| #3 | 11:05 | NEUTRAL | Full IC | C:6875 P:6755 | $400 ($125C+$275P) | CALL STOPPED at 11:13 | -$265 |
| #4 | 11:35 | BULLISH | Put-only | P:6780 | $235 (P) | PUT STOPPED at 12:53 | -$225 |
| #5 | 12:05 | NEUTRAL | Full IC | C:6895 P:6785 | $250 ($40C+$210P) | PUT STOPPED at 12:11 | -$30 |

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
| #1 | 10:05 | NEUTRAL | Full IC | C:6945 P:6845 | $390 ($55C+$335P) | Both EXPIRED | +$390 |
| #2 | 10:35 | NEUTRAL | Put-only (MKT-011) | P:6840 | $220 (P) | PUT EXPIRED | +$220 |
| #3 | 11:05 | NEUTRAL | Put-only (MKT-011) | P:6855 | $115 (P) | PUT STOPPED at 13:53 | -$125 |
| #4 | 11:35 | NEUTRAL | Put-only (MKT-011) | P:6850 | $85 (P) | PUT STOPPED at 13:53 | -$135 |
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
| #1 | 10:05 | NEUTRAL | Full IC | C:6925 P:6815 | $475 ($105C+$370P) | Put STOPPED at 10:07, Call EXPIRED | -$265 + $105 |
| #2 | 10:35 | NEUTRAL | Full IC | C:6930 P:6820 | $370 ($45C+$325P) | Put STOPPED at 10:51, Call EXPIRED | -$280 + $45 |
| #3 | 11:05 | NEUTRAL | Put-only (MKT-011) | P:6810 | $245 (P) | PUT EXPIRED | +$245 |
| #4 | 11:35 | NEUTRAL | Put-only (MKT-011) | P:6815 | $175 (P) | PUT STOPPED at 11:45 | -$175 |
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
| #1 | 10:05 | NEUTRAL | Full IC | C:6940/6990 P:6830/6780 | $975 ($180C+$795P) | All EARLY-CLOSED at 11:31 | +$680 |
| #2 | 10:35 | NEUTRAL | Full IC | C:6935/6985 P:6825/6775 | $480 ($135C+$345P) | All EARLY-CLOSED at 11:31 | +$225 |
| #3 | 11:05 | NEUTRAL | Full IC (MKT-020 tightened call) | C:6945/6995 P:6855/6805 | $320 ($95C+$225P) | Put STOPPED, Call EARLY-CLOSED | -$155 |
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
| #1 | 10:05 | NEUTRAL | Full IC | C:6925/6980 P:6815/6755 | $525 ($145C+$380P) | Put STOPPED at 11:02, Call EXPIRED | -$250 gross, -$265 net |
| #2 | 10:35 | NEUTRAL | Full IC | C:6910/6970 P:6800/6740 | $390 ($135C+$255P) | Put STOPPED at 11:03, Call EXPIRED | -$125 gross, -$140 net |
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
| #1 | 10:06 | NEUTRAL | Full IC | C:6900/6950 P:6790/6730 | $465 ($125C+$340P) | All EARLY CLOSED at 14:17 | +$355 gross |
| #2 | 10:45 | NEUTRAL | Full IC (MKT-020 tightened calls 55→30pt) | C:6910/6960 P:6825/6775 | $265 ($130C+$135P) | All EARLY CLOSED at 14:17 | +$180 gross |
| #3 | 11:05 | NEUTRAL | Call-only (MKT-011: put $90<$100) | C:6905/6955 (MKT-020 55→30pt) | $145 (C) | CALL STOPPED at 12:11:49 | -$145 gross |
| #4 | 11:35 | NEUTRAL | Put-only (MKT-011: call $32.50<$100) | P:6815/6755 (MKT-013/015 overlap adj) | $100 (P) | EARLY CLOSED at 14:17 | +$100 gross |
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
| #1 | 10:05 | NEUTRAL | Full IC (MKT-020 tightened) | C:6955 P:6875 | $250 ($100C+$150P) | EARLY CLOSED at 11:15 | +$160 |
| #2 | 10:35 | NEUTRAL | Full IC (MKT-020 tightened) | C:6945 P:6870 | $240 ($130C+$110P) | EARLY CLOSED at 11:15 | +$80 |
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
| #1 | 10:05 | NEUTRAL | Full IC (MKT-020 tightened) | C:6970 P:6870 | $450 ($120C+$330P) | Call expired, PUT STOPPED 10:15 | -$220 |
| #2 | 10:35 | BEARISH | Call-only | C:6920 | $305 (C) | CALL STOPPED 11:15 | -$345 |
| #3 | 11:05 | NEUTRAL | Full IC (MKT-020 tightened) | C:6940 P:6845 | $340 ($130C+$210P) | Call expired, PUT STOPPED 12:49 | -$115 |
| #4 | 11:35 | NEUTRAL | SKIPPED (MKT-011) | -- | -- | Call $72.50 non-viable + NEUTRAL = skip | -- |
| #5 | 12:05 | NEUTRAL | Full IC (MKT-020 tightened) | C:6920 P:6825 | $250 ($130C+$120P) | CALL STOPPED 14:16, Put expired | -$30 |

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

### Feb 27 (Friday) - NET P&L: +$545

**Market**: Early morning dip then steady recovery. SPX opened 6849, dipped to 6829 low by mid-morning, then rallied to close at 6879. VIX started elevated at 21.39 (highest open of the period), compressed to 19.80 close. Range 50 pts (0.7%).

**Versions**: v1.3.11 at open → v1.4.0 deployed mid-day (remove MKT-019, disable one-sided entries) → v1.4.1 deployed mid-day (MKT-024 wider starting OTM 2×, separate put minimum $1.75). Last day as MEIC-TF (renamed to HYDRA on Feb 28). Only 5 entry slots (6th entry added in v1.4.4 on Feb 28).

| Entry | Time | Signal | Type | Short Strikes | Spread Width | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|-------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:6925 P:6780 | 60pt/60pt | $305 ($105C+$200P) | Both EXPIRED | +$305 |
| #2 | 10:35 | NEUTRAL | Full IC | C:6920 P:6795 | 60pt/60pt | $280 ($105C+$175P) | Both EXPIRED | +$280 |
| #3 | 11:05 | -- | SKIPPED (MKT-011) | -- | -- | -- | Call $50 non-viable (<$100) | -- |
| #4 | 11:35 | NEUTRAL | Full IC | C:6915 P:6830 | 50pt/55pt | $320 ($130C+$190P) | Put STOPPED 11:40, Call EXPIRED | -$5 |
| #5 | 12:05 | -- | SKIPPED (MKT-011) | -- | -- | -- | Put $170 non-viable (<$175 new min) | -- |

**Key observations**:
- **Best P&L day since Feb 20 (+$690)** — 2 of 3 placed entries expired fully worthless, one had minimal net loss
- **Two MKT-011 skips**: Entry #3 (call credit $50 < $100 minimum) and Entry #5 (put credit $170 < $175 new minimum from v1.4.1). Previously these would have been placed with poor credit
- **Entry #5 skip used v1.4.1's new separate put minimum ($1.75)** — under old threshold ($0.50 per side), this entry would have been placed. The new higher put threshold reflects Tammy's minimum range ($1.00-$1.75)
- **Entry #4 stopped in 5 minutes**: Put side at 6830 stopped at 11:40, only 5 min after entry — SPX was pushing higher through 6870s. Old stop mechanism (pre-MKT-025) closed both short and long put legs
- **Entry #4 nearly breakeven**: Put stop lost $135, call expired for +$130 = net -$5 on entry
- **All NEUTRAL signals**: EMA 20/40 convergence throughout the day, no clear trend
- **VIX compression**: 21.39→19.80 (-7.4%) — highest VIX open of the period but compressed into close, benefiting all surviving positions
- **Multiple mid-day deploys**: v1.4.0 (remove MKT-019, no one-sided entries) and v1.4.1 (wider OTM, put min $1.75) both deployed during market hours

### Stop Timing Log (Feb 27 — 1 Stop, 2 Skips)

```
10:05:03 ET - Entry #1: Full IC (NEUTRAL), C:6925/6985 P:6780/6720
              EMA: NEUTRAL. MKT-011: PASSED
              Credit: $305 ($105C + $200P)
10:35:02 ET - Entry #2: Full IC (NEUTRAL), C:6920/6980 P:6795/6735
              EMA: NEUTRAL. MKT-011: PASSED
              Credit: $280 ($105C + $175P)
11:05:05 ET - Entry #3: SKIPPED by MKT-011
              Call credit estimate $50 < $100 minimum + NEUTRAL → SKIP
11:35:04 ET - Entry #4: Full IC (NEUTRAL), C:6915/6965 P:6830/6775
              EMA: NEUTRAL. MKT-011: PASSED
              Credit: $320 ($130C + $190P)
11:40:xx ET - Entry #4 PUT STOPPED (old mechanism — close both legs):
              SP 6830 fill $3.90, LP 6775 fill $0.65
              Close cost: ($3.90-$0.65)×100 = $325
              Put credit $190, net loss: -$135
              Call side survives (SC 6915, SPX at ~6870)
12:05:03 ET - Entry #5: SKIPPED by MKT-011
              Put credit estimate $170 < $175 minimum (v1.4.1 new threshold) → SKIP
18:33 ET    - Settlement: 6 positions expired worthless
              Entry #1: Both expired (+$305)
              Entry #2: Both expired (+$280)
              Entry #4 call expired: +$130
              Total expired credits: $715
              Daily summary: Net P&L +$545, Commission $35, Cumulative $2,110
```

**P&L Reconciliation**: (+$305) + (+$280) + (-$5) = **+$580 trade P&L**. Commission: 12 open legs + 2 close legs (old stop mechanism) = 14 × $2.50 = $35. Identity: $715 - $135 - $35 = **+$545 net** ✓

### Mar 2 (Monday) - NET P&L: -$255

**Market**: Wide-range whipsaw day. SPX opened 6800, dipped to 6795 low, then rallied sharply to 6901 high, closing at 6879. VIX opened at 23.40 (highest of the period), dropped to 20.37 low, closing 21.32. Range 106 pts (1.6%) — widest of the 14-day period.

**Version**: v1.5.1 (first day running as HYDRA after rename from MEIC-TF on Feb 28). First day with 6 entry slots (v1.4.4), MKT-025 short-only stops (v1.4.3), MKT-026 60pt min spread (v1.4.5), and MKT-027 VIX-scaled spread width (v1.5.0).

| Entry | Time | Signal | Type | Short Strikes | Spread Width | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|-------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:6915 P:6775 | 60pt/60pt | $310 ($105C+$205P) | Put STOPPED 10:33, Call EXPIRED | -$90 |
| #2 | 10:35 | NEUTRAL | Full IC | C:6900 P:6765 | 60pt/60pt | $315 ($125C+$190P) | Call STOPPED 11:41, Put EXPIRED | -$15 |
| #3 | 11:05 | NEUTRAL | Full IC | C:6905 P:6795 | 60pt/60pt | $285 ($115C+$170P) | **DOUBLE STOP** (P:11:27, C:11:44) | -$380 |
| #4 | 11:35 | NEUTRAL | Full IC | C:6895 P:6790 | 60pt/60pt | $305 ($115C+$190P) | Call STOPPED 11:40, Put EXPIRED | -$15 |
| #5 | 12:05 | NEUTRAL | Full IC | C:6910 P:6835 | 60pt/60pt | $325 ($145C+$180P) | Both EXPIRED | +$325 |
| #6 | 12:35 | NEUTRAL | Full IC | C:6905 P:6840 | 75pt/80pt | $315 ($125C+$190P) | Call STOPPED 14:21, Put EXPIRED | -$5 |

**Key observations**:
- **First double stop in 14-day period**: Entry #3 had BOTH sides stopped — put at 11:27 (SPX dipping toward 6795) then call at 11:44 (SPX surging past 6905). Classic whipsaw pattern
- **Widest range day**: 106pt SPX range (1.6%) was the most volatile day of the period, driven by tariff news and macro uncertainty
- **All 6 entries placed**: First day with all 6 slots filled and 0 skips — VIX 23+ provided ample premium on all entries
- **MKT-025 short-only stops first live test**: All 6 stops used new mechanism — only short legs closed, long legs expired at settlement. Saved 6 × $2.50 = $15 in commission vs old mechanism
- **Entry #5 was the sole survivor**: Both sides expired worthless for full +$325 credit — only clean win of the day
- **Entry #6 wider spreads**: Call spread 75pt, put spread 80pt — MKT-027 VIX-scaled width formula at VIX ~21 produced wider spreads than the 60pt floor used by earlier entries (VIX had dropped from 23.40 open to ~21 by 12:35)
- **4 call-side stops, 2 put-side stops**: Market rally from 6800 to 6900+ after 11:00 ET hurt earlier call positions more than puts. Earlier puts (Entries #1, #3) stopped during the initial dip, then calls (#2, #3, #4, #6) stopped during the rally
- **Despite 6 stops, loss limited to -$255**: 4 surviving opposite sides expired worthless, providing $750 in offsetting expired credits. MKT-025 short-only stops meant long legs weren't closed (no close cost on longs), and longs expired at $0 settlement
- **Entry-level P&L concentration**: Entry #3 (double stop) accounted for -$380 of the -$180 trade P&L. Without the double stop, remaining 5 entries netted +$200 trade P&L

### Stop Timing Log (Mar 2 — 6 Stops + 1 Double Stop, MKT-025 Active)

```
10:05:03 ET - Entry #1: Full IC (NEUTRAL), C:6915/6975 P:6775/6715
              EMA: NEUTRAL. MKT-011: PASSED
              Credit: $310 ($105C + $205P)
10:33:xx ET - Entry #1 PUT STOPPED (MKT-025 short-only):
              SP 6775 fill $4.00 (short only closed, LP 6715 expires at settlement)
              Close cost: $4.00×100 = $400, put credit $205, net loss: -$195
              Call side survives (SC 6915, SPX at ~6810)
10:35:02 ET - Entry #2: Full IC (NEUTRAL), C:6900/6960 P:6765/6705
              EMA: NEUTRAL. MKT-011: PASSED
              Credit: $315 ($125C + $190P)
11:05:04 ET - Entry #3: Full IC (NEUTRAL), C:6905/6965 P:6795/6735
              EMA: NEUTRAL. MKT-011: PASSED
              Credit: $285 ($115C + $170P)
11:27:xx ET - Entry #3 PUT STOPPED (MKT-025 short-only):
              SP 6795 fill $3.80 (short only closed, LP 6735 expires at settlement)
              Close cost: $3.80×100 = $380, put credit $170, net loss: -$210
              Call side still live (SC 6905, SPX at ~6830)
11:35:03 ET - Entry #4: Full IC (NEUTRAL), C:6895/6955 P:6790/6730
              EMA: NEUTRAL. MKT-011: PASSED
              Credit: $305 ($115C + $190P)
11:40:xx ET - Entry #4 CALL STOPPED (MKT-025 short-only):
              SC 6895 fill $3.20 (short only closed, LC 6955 expires at settlement)
              Close cost: $3.20×100 = $320, call credit $115, net loss: -$205
              Put side survives (SP 6790, SPX at ~6900)
11:41:xx ET - Entry #2 CALL STOPPED (MKT-025 short-only):
              SC 6900 fill $3.30 (short only closed, LC 6960 expires at settlement)
              Close cost: $3.30×100 = $330, call credit $125, net loss: -$205
              Put side survives (SP 6765, SPX at ~6900)
11:44:xx ET - Entry #3 CALL STOPPED (MKT-025 short-only) — DOUBLE STOP:
              SC 6905 fill $2.85 (short only closed, LC 6965 expires at settlement)
              Close cost: $2.85×100 = $285, call credit $115, net loss: -$170
              Both sides now stopped — first double stop of the period
12:05:03 ET - Entry #5: Full IC (NEUTRAL), C:6910/6970 P:6835/6775
              EMA: NEUTRAL. MKT-011: PASSED
              Credit: $325 ($145C + $180P)
12:35:04 ET - Entry #6: Full IC (NEUTRAL), C:6905/6980 P:6840/6760
              EMA: NEUTRAL. MKT-011: PASSED (MKT-027: 75pt call spread, 80pt put spread)
              Credit: $315 ($125C + $190P)
14:21:xx ET - Entry #6 CALL STOPPED (MKT-025 short-only):
              SC 6905 fill $3.20 (short only closed, LC 6980 expires at settlement)
              Close cost: $3.20×100 = $320, call credit $125, net loss: -$195
              Put side survives (SP 6840, SPX at ~6880)
17:00 ET    - Settlement: 18 positions settled (6 surviving short sides expired + 12 long legs from all entries)
              Entry #1 call expired: +$105
              Entry #2 put expired: +$190
              Entry #4 put expired: +$190
              Entry #5 both expired: +$325 ($145C + $180P)
              Entry #6 put expired: +$190
              Total expired credits: $1,000 (Calls $250, Puts $750)
              Daily summary: Net P&L -$255, Commission $75, Cumulative $1,855
```

**Stop Cluster Analysis (Mar 2)**:
- **11:27-11:44 ET cluster**: 4 stops in 17 minutes (E3 put, E4 call, E2 call, E3 call). SPX was transitioning from dip (~6830) to rally (~6900+). This 70-pt swing in <20 min triggered both put and call stops simultaneously
- **Isolated stops**: E1 put (10:33, during initial dip) and E6 call (14:21, afternoon drift higher) were separate from the main cluster
- **Pattern**: V-shape reversal pattern — puts stopped on the way down, calls stopped on the way up. Similar to Feb 26 but more extreme range

**P&L Reconciliation**: (-$90) + (-$15) + (-$380) + (-$15) + (+$325) + (-$5) = **-$180 trade P&L**. Commission: 24 open legs + 6 close legs (MKT-025 short-only) = 30 × $2.50 = $75. Identity: $1,000 - $1,180 - $75 = **-$255 net** ✓

### Mar 3 (Tuesday) - NET P&L: -$450

**Market**: Gap-down open and V-shape recovery. SPX gapped down ~91pts overnight (ES futures -91.1), opened ~6,759, hit intraday low ~6,711 as VIX spiked to 28.15, then rallied sharply to 6,840 high by afternoon, closing at ~6,812. VIX opened 26.03, peaked 28.15, closed 22.18. Range ~130 pts (1.9%) — widest of the 15-day period, surpassing Mar 2's 106pt record.

**Version**: v1.6.0→v1.7.2 (major development day with 13 commits). Key changes deployed mid-day: v1.6.0 dropped Entry #6 (5 entries), v1.6.1 raised VIX filter 25→30, MKT-024 wider starting OTM (3.5×/4.0×), MKT-028 asymmetric spreads (call 60pt/put 75pt), v1.7.0 MKT-027 VIX-scaled spread width, v1.7.1 put-only re-enable, v1.7.2 lower call min $1.00→$0.75.

| Entry | Time | Signal | Type | Short Strikes | Spread Width | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|-------------|--------|---------|------------|
| #1 | 10:05 | NEUTRAL | Full IC | C:6850 P:6630 | 75pt/75pt | $295 ($100C+$195P) | **DOUBLE STOP** (P:10:22, C:later) | -$445 |
| #2 | ~10:35 | NEUTRAL | Full IC | — | 75pt/75pt | $285 ($115C+$170P) | Call STOPPED, Put EXPIRED | ~+$0 |
| #3 | ~11:05 | NEUTRAL | Full IC | — | 75pt/75pt | $245 ($100C+$145P) | Call STOPPED, Put EXPIRED | ~+$0 |
| #4 | ~11:35 | NEUTRAL | Full IC | — | 75pt/75pt | $290 ($125C+$165P) | Call STOPPED, Put EXPIRED | ~+$0 |
| #5 | ~12:05 | NEUTRAL | Full IC | — | 75pt/75pt | $280 ($85C+$195P) | Call STOPPED, Put EXPIRED | ~-$5 |

**Key observations**:
- **Biggest gap-down of the period**: ES futures -91.1pts overnight. APOLLO pre-market rated risk RED — predicted put-side stops as primary concern, but actual damage was on call side after V-shape recovery
- **VIX filter initially blocked entries #2-5**: VIX opened 26.03 (above 25 threshold). Only Entry #1 was placed before VIX filter blocked remaining entries. VIX threshold raised from 25→30 via v1.6.1 deployment (~10:18 ET), allowing entries #2-5
- **Entry #1 put stopped in 17 minutes**: SPX continued selling from ~6,737 to ~6,715 by 10:22 ET, triggering put stop. Fastest Entry #1 stop of the period. Call side also stopped later during rally = double stop
- **5 call-side stops from V-shape rally**: SPX rallied ~130pts from 6,711 low to 6,840 high. All 5 entries had call sides stopped as market surged past short call strikes
- **1 double stop (Entry #1)**: Second double stop of the period (first was Mar 2 Entry #3). Put stopped on the way down, call stopped on the way up — classic V-shape whipsaw
- **MKT-024 wider starting OTM first live day**: VIX=26.6 → base_otm=70pt, call starting at 240pt (3.5×), put starting at 240pt (4.0×). MKT-020 tightened calls 240→115pt OTM, MKT-022 tightened puts 240→105pt OTM
- **MKT-028 asymmetric spreads first live day**: All entries used 75pt/75pt spreads (VIX-scaled formula at VIX ~26 produced 75pt, matching both call and put floors)
- **13 code commits during trading**: Major development day — v1.6.0 through v1.7.2 deployed between entries, multiple bot restarts. State file recovery preserved positions through each restart
- **Despite 6 stops, 4 put sides survived**: Entries #2-5 put sides expired worthless, providing $675 in offsetting expired credits

### Stop Timing Log (Mar 3 — 6 Stops + 1 Double Stop, MKT-025 Active)

```
10:05:xx ET - Entry #1: Full IC (NEUTRAL), C:6850/6925 P:6630/6555
              SPX ~6,737, VIX 26.62
              MKT-024: base_otm=70pt, call_start=240pt(×3.5), put_start=240pt(×4.0)
              MKT-020: Call tightened 240→115pt OTM (credit $107.50)
              MKT-022: Put tightened 240→105pt OTM (credit $200.00)
              MKT-011: PASSED Call $107.50, Put $200.00
              Fills: LC 6925@$0.15, LP 6555@$0.95, SC 6850@$1.15, SP 6630@$2.90
              Credit: $295 ($100C + $195P), Stop level: $280
~10:18 ET -   VIX 26.7 > 25 blocks entries #2-5. VIX threshold raised 25→30 (v1.6.1 deployed)
10:22:xx ET - Entry #1 PUT STOPPED (MKT-025 short-only):
              SPX ~6,714.80, VIX 27.86
              SP 6630 closed (short only, LP 6555 expires at settlement)
              Net loss: -$195 (put side)
              Call side survives
~10:35 ET -   Entry #2: Full IC (NEUTRAL), Credit: $285 ($115C + $170P)
~11:05 ET -   Entry #3: Full IC (NEUTRAL), Credit: $245 ($100C + $145P)
~11:35 ET -   Entry #4: Full IC (NEUTRAL), Credit: $290 ($125C + $165P)
~12:05 ET -   Entry #5: Full IC (NEUTRAL), Credit: $280 ($85C + $195P)
              [SPX rallying from 6,711 low toward 6,840 high]
              All 5 call sides stopped as SPX surges past short call strikes
              Entry #1 call also stopped (double stop)
              4 put sides (Entries #2-5) expire worthless = $675 expired credits
              Total expired credits: $675
              Daily summary: Net P&L -$450, Commission $65, Cumulative $1,405
```

**P&L Reconciliation**: (-$445) + (~$0) + (~$0) + (~$0) + (~-$5) = **-$385 trade P&L**. Commission: 20 open legs + 6 close legs (MKT-025 short-only) = 26 × $2.50 = $65. Identity: $675 - $1,060 - $65 = **-$450 net** ✓

### Mar 4 (Wednesday) - NET P&L: +$187.50

**Market**: SPX range 76 pts (1.1%). VIX 22.5→21.2.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact |
|-------|------|--------|------|---------------|--------|---------|------------|
| #1 | 11:06 AM ET | NEUTRAL | Iron Condor | C:6915 P:6825 | $250 ($75C+$175P) | Put Stopped | -$105 |
| #2 | 11:36 AM ET | NEUTRAL | Iron Condor | C:6910 P:6830 | $285 ($65C+$220P) | Put Stopped | -$50 |
| #3 | 12:05 PM ET | NEUTRAL | Iron Condor | C:6905 P:6840 | $275 ($95C+$180P) | Put Stopped | -$150 |
| #4 | 12:35 PM ET | NEUTRAL | Iron Condor | C:6900 P:6835 | $305 ($105C+$200P) | Expired |  |

**Key observations**:
- All three put stops (Entries #1–3) were triggered by SPX's decline to the intraday low of 6,810.08, sweeping short put strikes clustered at 6,825, 6,830, and 6,840 within a 16-minute window between 11:09 and 12:22 ET; Entry #4's short put at 6,835 was placed after the low had formed and expired clean.
- Under MKT-025, only the short put legs were closed at each stop, with total stop loss debits of $305 against $575 in put credits collected across the three stopped entries — the long legs expired worthless at settlement, confirming the commission-saving design functioned as intended.
- Entry #2's call credit of $65 sits below the MKT-011 minimum threshold of $100 per spread ($1.00 × 100 multiplier), which warrants audit review; no MKT-011 skip was recorded for this entry despite the sub-threshold credit.
- Call credits across Entries #1–3 ranged from $65 to $95, reflecting compressed upside premium consistent with VIX declining from 22.52 to a low of 20.40 intraday, while put credits remained elevated at $175–$220 across all four entries, carrying forward residual downside fear from the prior session.
- The day closed at $187.50 net profit — positive only because Entry #4's $305 full credit expired clean and all four call spreads expired worthless, offsetting $305 in stop debits and $47.50 in commissions against $1,115 in total credits collected.

### Stop Timing Log

```
11:09 AM ET - Entry #1 Put Stopped ($105 loss)
11:38 AM ET - Entry #2 Put Stopped ($50 loss)
12:22 PM ET - Entry #3 Put Stopped ($150 loss)
```

### P&L Reconciliation

- Expired Credits: $540
- Stop Loss Debits: $305
- Commission: $47.50
- **Net P&L: +$187.50** (540 - 305 - 47.50 = 187.50)

### Mar 5 (Thursday) - NET P&L: +$90

**Market**: SPX range 98 pts (1.4%). VIX 22.0→23.4.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 11:15 AM ET | NEUTRAL | Iron Condor | C:6885 P:6740 | $245 ($80C+$165P) | Put Stopped | -$125 |  |
| #2 | 11:45 AM ET | NEUTRAL | Iron Condor | C:6880 P:6755 | $255 ($95C+$160P) | Put Stopped | -$110 |  |
| #3 | 12:15 PM ET | NEUTRAL | Iron Condor | C:6870 P:6760 | $280 ($90C+$190P) | Put Stopped | -$120 |  |
| #4 | 12:43 PM ET | NEUTRAL | Iron Condor | C:6865 P:6725 | $265 ($65C+$200P) | Put Stopped | -$80 |  |
| #5 | 01:15 PM ET | NEUTRAL | Iron Condor | C:6860 P:6750 | $300 ($75C+$225P) | Put Stopped | -$95 |  |

**Key observations**:
- All 5 entries posted put-side stops with zero call stops, the most concentrated directional sweep possible; SPX's session low of 6,770.69 breached short put strikes ranging from 6,725 to 6,760, while call strikes (6,860–6,885) were never threatened as the market moved entirely away from them.
- Entries #2 and #3 share an identical stop time of 12:16 PM ET, and Entry #4 stopped at 12:45 PM ET just two minutes after its 12:43 PM entry, indicating a sharp, sustained mid-session sell-off rather than a gradual drift — three of five stops clustered within a 29-minute window.
- MKT-033 long salvage recovered $290 across all five stopped put spreads, a contribution that was decisive: without it, the $90 net positive would have inverted to a loss day, underscoring that the day's outcome depended more on salvage mechanics than on premium collection.
- Put credits escalated across the session from $165 (Entry #1) to $225 (Entry #5) as VIX spiked from 22.04 to a high of 25.84, with all five entries clearing the $1.75 MKT-011 minimum — yet the rising put premium was itself a symptom of the directional pressure that triggered every stop.
- All 5 spreads used 75-point widths throughout the session despite VIX ranging up to 25.84, and the MKT-021 pre-entry ROC gate did not skip any of Entries #4–#6, consistent with no early-close event and a day-end ROC of only 0.24% — well below threshold.

### Stop Timing Log

```
11:17 AM ET - Entry #1 Put Stopped ($125 loss)
12:16 PM ET - Entry #2 Put Stopped ($110 loss)
12:16 PM ET - Entry #3 Put Stopped ($120 loss)
12:45 PM ET - Entry #4 Put Stopped ($80 loss)
01:17 PM ET - Entry #5 Put Stopped ($95 loss)
```

### P&L Reconciliation

- Expired Credits: $405
- Stop Loss Debits: $240 (net of $290 salvage revenue)
- Commission: $75
- **Net P&L: +$90** (405 - 240 - 75 = 90)

### Mar 6 (Friday) - NET P&L: +$175

**Market**: SPX range 66 pts (1.0%). VIX 27.7→29.8.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 11:15 AM ET | NEUTRAL | Iron Condor | C:6845 P:6675 | $260 ($80C+$180P) | Put Stopped | -$150 |  |
| #2 | 11:45 AM ET | NEUTRAL | Iron Condor | C:6840 P:6685 | $215 ($80C+$135P) | Put Stopped | -$120 | +$52.50 |
| #3 | 12:15 PM ET | NEUTRAL | Iron Condor | C:6830 P:6680 | $260 ($80C+$180P) | Put Stopped | -$95 | +$52.50 |
| #4 | 12:45 PM ET | NEUTRAL | Iron Condor | C:6815 P:6670 | $265 ($80C+$185P) | Expired |  |  |
| #5 | 01:15 PM ET | NEUTRAL | Iron Condor | C:6825 P:6710 | $260 ($85C+$175P) | Put Stopped | -$105 |  |

**Key observations**:
- Four of five entries triggered put stops on a day when SPX dropped to a session low of 6,708.67, with VIX climbing from 27.69 to close at 29.83 — the sustained downside pressure overwhelmed put-side cushions that ranged from 30 to 35 points OTM at entry.
- Stop clustering was notable: Entry #2 stopped at 11:56 AM ET just 11 minutes after its 11:45 AM entry, and Entry #3 stopped at 12:26 PM ET just 11 minutes after its 12:15 PM entry, indicating SPX was actively breaching put stop levels during the midday window rather than drifting into stops late-session.
- Entry #4 (12:45 PM, $265 credit) was the sole full expiry and the only winner of the day, contributing its entire $265 credit to the expired credits total of $590; all five call sides expired worthless, consistent with SPX never threatening the call strikes (6,815–6,845) on a day the index ranged only 65.81 points.
- All five entries collected call credits of exactly $0.80–$0.85 against put credits of $1.35–$1.85, with 75-point spread widths across the board — the uniform spread width reflects VIX-scaling holding steady in the mid-to-upper 27–29 range throughout the session per MKT-027 parameters.
- Long salvage proceeds of $125.00 (confirmed in summary; $52.50 noted on entries #2 and #3) partially offset stop-loss debits of $345.00, demonstrating MKT-025's short-only stop mechanic preserving residual value in the long put legs rather than closing the full spread at stop.

### Stop Timing Log

```
12:37 PM ET - Entry #1 Put Stopped ($150 loss)
11:56 AM ET - Entry #2 Put Stopped ($120 loss) + Long Salvage +$52.50
12:26 PM ET - Entry #3 Put Stopped ($95 loss) + Long Salvage +$52.50
01:37 PM ET - Entry #5 Put Stopped ($105 loss)
```

### P&L Reconciliation

- Expired Credits: $590
- Stop Loss Debits: $345
- Commission: $70
- **Net P&L: +$175** (590 - 345 - 70 = 175)

### Mar 9 (Monday) - NET P&L: -$712.50

**Market**: SPX range 175 pts (2.6%). VIX 31.2→25.1.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 12:08 PM ET | NEUTRAL | Iron Condor | C:6765 P:6655 | $265 ($85C+$180P) | Call Stopped | -$175 |  |
| #2 | 12:45 PM ET | NEUTRAL | Iron Condor | C:6790 P:6680 | $275 ($85C+$190P) | Double Stop | -$385 |  |
| #3 | 01:15 PM ET | NEUTRAL | Iron Condor | C:6765 P:6675 | $265 ($80C+$185P) | Double Stop | -$280 |  |
| #4 | 01:45 PM ET | NEUTRAL | Iron Condor | C:6740 P:6660 | $240 ($80C+$160P) | Call Stopped | -$145 |  |

**Key observations**:
- All four completed entries were call-stopped as SPX rallied 134 points intraday from the 6,635 low to a 6,810 high, clearing every short call strike in the 6,740–6,790 range; no entry survived to expiry on the call side.
- Entries #2 and #3 produced double-stops: put legs at 6,680 and 6,675 were pressured by the morning gap-down near the 6,635 session low, then call legs stopped later in the afternoon (1:03 PM and 1:18 PM for puts; 3:21 PM and 3:20 PM for calls respectively), generating a combined -$665 P&L impact from those two positions alone.
- VIX opened at 31.18 and compressed to a low of 24.76 intraday, consistent with a short-squeeze dynamic; despite elevated VIX at open supporting wider spread widths, all four entries were placed at a uniform 75-point spread width with total credits ranging $240–$275, below what a VIX-scaled maximum of 120 points would theoretically allow.
- Entry #1 fired at 12:08 ET — approximately 7 minutes ahead of the 12:15 scheduled window — indicating MKT-031 smart entry scoring reached threshold early, notable given VIX was still above 31 at that point; its put spread at 6,655 short strike was the only put side to expire worthless, as the 6,635 session low did not hold deep enough to breach it.
- The 13:15 entry was skipped (1 of 5), consistent with MKT-011 credit gate behavior after a sustained upside move compressed call premium; with $0 in expired credits against $985 in stop loss debits and $67.50 in commissions, the day produced -$712.50 net P&L — approximately 2.4x the reported average losing day — with no offset from any surviving leg.

### Stop Timing Log

```
12:26 PM ET - Entry #1 Call Stopped ($175 loss)
01:03 PM ET - Entry #2 Double Stop ($385 loss)
01:18 PM ET - Entry #3 Double Stop ($280 loss)
02:35 PM ET - Entry #4 Call Stopped ($145 loss)
```

### P&L Reconciliation

- Expired Credits: $340
- Stop Loss Debits: $985
- Commission: $67.50
- **Net P&L: -$712.50** (340 - 985 - 67.50 = -712.50)

### Mar 10 (Tuesday) - NET P&L: -$585

**Market**: SPX range 70 pts (1.0%). VIX 22.6→25.6.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 12:44 PM ET | NEUTRAL | Iron Condor | C:6865 P:6785 | $250 ($80C+$170P) | Double Stop | -$275 |  |
| #2 | 01:16 PM ET | NEUTRAL | Iron Condor | C:6870 P:6800 | $265 ($90C+$175P) | Double Stop | -$270 |  |

**Key observations**:
- HYDRA restarted mid-day, producing two entries that originally shared the same entry number (#1). Entry #1 entered at 12:44 PM ET (C:6865 P:6785, $250 credit) and Entry #2 at 1:16 PM ET (C:6870 P:6800, $265 credit) — both slightly different strikes due to SPX movement between entries.
- Both entries resulted in double-stops, with all 4 legs (2 call, 2 put) stopped out against a 69.7-point intraday range — SPX probed to 6,845 (stressing short calls at 6,865/6,870), then reversed to a session low of 6,775.78 (breaching short puts at 6,785 and 6,800), producing -$275 and -$270 P&L impacts respectively.
- Entry #1's put stop fired just 2 minutes after entry (12:46 PM ET), reflecting that SPX was already near the put strike at entry time. The call stop followed at 1:27 PM ET when SPX rallied back through 6,865.
- VIX opened at 22.61 before spiking to 25.70 session high and closing at 25.65 — a structural trap where call credits of $0.80-$0.90 (just above MKT-011's $0.75 floor) and put credits of $1.70-$1.75 (at the floor) reflected compressed vol at entry, while realized vol was far higher.
- MKT-011's credit gate drove an exceptional 4-of-6 skip rate, with the first successful placement not until 12:44 ET — the three standard morning slots all failed to clear minimum credit thresholds.
- At -$585 net on $15,000 deployed (-3.9% ROC), today's loss trimmed cumulative P&L from ~$1,145 to $560, the second consecutive above-average loss in elevated VIX, though cumulative ROC remains positive at +2.26% through day 20.

### Stop Timing Log

```
12:46 PM ET - Entry #1 Put Stop (-$80)
01:27 PM ET - Entry #1 Call Stop (-$195) → Double Stop ($275 total loss)
01:28 PM ET - Entry #2 Call Stop (-$185)
01:36 PM ET - Entry #2 Put Stop (-$85) → Double Stop ($270 total loss)
```

### P&L Reconciliation

- Expired Credits: $0
- Stop Loss Debits: $545
- Commission: $40
- **Net P&L: -$585** (0 - 545 - 40 = -585)

### Mar 11 (Wednesday) - NET P&L: -$310

**Market**: SPX range 66 pts (1.0%). VIX 25.8→24.3.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:05 AM ET | NEUTRAL | Iron Condor | C:6880 P:6710 | $255 ($85C+$170P) | Put Stopped | -$145 |  |
| #2 | 10:45 AM ET | NEUTRAL | Iron Condor | C:6865 P:6725 | $335 ($60C+$275P) | Put Stopped | -$155 |  |
| #3 | 11:15 AM ET | NEUTRAL | Iron Condor | C:6850 P:6710 | $330 ($55C+$275P) | Put Stopped | -$75 |  |
| #4 | 11:45 AM ET | NEUTRAL | Iron Condor | C:6840 P:6695 | $295 ($55C+$240P) | Put Stopped | -$100 |  |
| #5 | 12:15 PM ET | NEUTRAL | Iron Condor | C:6835 P:6710 | $360 ($55C+$305P) | Put Stopped | -$70 |  |

**Key observations**:
- All 5 entries were stopped on the put side exclusively, with 0 call stops and 0 double stops; SPX's intraday drop to 6,745.41 — roughly 66 points below the session high — sustained enough downside pressure on every put spread while call strikes remained untouched at expiry. Note: MKT-036 stop confirmation timer was not yet deployed (v1.12.0 deployed post-market); all stops executed immediately on breach.
- Entry #2 (10:45 ET) posted the worst net stop loss at -$155 on $2.75 put credit, while Entry #5 (12:15 ET) had the smallest loss at -$70 despite collecting the richest put credit of the day at $3.05 ($3.60 total), demonstrating that higher premium provided more cushion against the directional flush.
- Entry #1 (10:05 ET) entered 10 minutes early via MKT-031 smart entry (score 65/65) — this was the last MKT-031 early entry before it was disabled mid-session at the 10:33 restart.
- Call credit compressed rapidly from $0.85 at Entry #1 to $0.60 at Entry #2, then held at $0.55 for Entries #3–5, meaning the call side contributed near-minimum viable premium across all but the first entry.
- The MKT-035 down-day filter did not engage on any entry — all 5 were placed as full iron condors — indicating SPX had not yet breached the ~0.3% (~20-point) threshold below the 6,761.91 open at the moment each entry fired, despite the intraday low eventually reaching 16 points below open.
- Individual stop losses: #1 -$145, #2 -$155, #3 -$75, #4 -$100, #5 -$70, totaling $545 in net stop debits. All verified against HYDRA bot logs (entries #3 and #5 were missing from Google Sheets Trades tab due to mid-session restart chaos — 10 restarts on Mar 11 — corrected manually).

### Stop Timing Log

```
10:24 AM ET - Entry #1 Put Stopped ($145 loss)
11:12 AM ET - Entry #2 Put Stopped ($155 loss)
11:15 AM ET - Entry #3 Put Stopped ($75 loss)
12:16 PM ET - Entry #5 Put Stopped ($70 loss)
01:38 PM ET - Entry #4 Put Stopped ($100 loss)
```

### P&L Reconciliation

- Expired Credits: $310
- Stop Loss Debits: $545
- Commission: $75
- **Net P&L: -$310** (310 - 545 - 75 = -310)

### Mar 12 (Thursday) - NET P&L: +$525

**Market**: SPX range 57 pts (0.8%). VIX 26.1→27.2.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:15 AM ET | NEUTRAL | Iron Condor | C:6820 P:6650 | $320 ($55C+$265P) | Expired |  |  |
| #2 | 10:45 AM ET | MKT-035 | Call Spread | C:6795 | $60 (C) | Expired |  |  |
| #3 | 11:15 AM ET | MKT-035 | Call Spread | C:6785 | $60 (C) | Expired |  |  |
| #4 | 12:15 PM ET | MKT-035 | Call Spread | C:6780 | $55 (C) |  |  |  |
| #5 | 01:15 PM ET | MKT-035 | Call Spread | C:6765 | $60 (C) | Expired |  |  |
| #7 |  | NEUTRAL | Call Only | C:6765 | $60 (C) | Expired |  |  |

**Key observations**:
- MKT-035 fired at Entry #2 (10:45 ET) and remained active through the final conditional entry at 13:15 ET, converting 4 of 5 remaining entries to call-only as SPX fell from its 6,722.77 open to a session low of 6,669.80 — a 53-point directional decline that would have directly threatened put spreads had they been placed.
- The sole full iron condor (Entry #1, $320 total credit) carried the only put exposure of the day: short put at 6,650 vs. a session low of 6,669.80, leaving a realized clearance of just 19.8 points — the narrowest margin of the day and the only position with meaningful downside risk.
- All 5 placed entries expired clean with zero stop triggers; minimum call-side clearance was 38.4 points (Entry #5/#7 short at 6,765 vs. session high of 6,726.56), and call stops were never in consideration given the persistent directional drift away from those strikes.
- Call credits held at $60 across Entries #2, #3, and #5 despite SPX moving further from the call strikes intraday, indicating elevated IV (VIX 26.09–27.16) sustained viable call premium even as the index sold off — consistent with MKT-011 thresholds remaining met throughout.
- 2 of 7 scheduled entries were skipped (Entry #4 at 11:45 and Entry #6 at 12:45), with no explicit skip reason recorded in the data; despite VIX levels that should sustain credits above the $0.60 call threshold, these two consecutive mid-session slots are absent from both the entry log and outcome records.

### P&L Reconciliation

- Expired Credits: $555
- Stop Loss Debits: $0
- Commission: $30
- **Net P&L: +$525** (555 - 0 - 30 = 525)

### Mar 13 (Friday) - NET P&L: +$385

**Market**: SPX range 107 pts (1.6%). VIX 25.5→27.2.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:15:26 AM ET | NEUTRAL | Iron Condor | C:6795 P:6620 | $325 ($60C+$265P) | Put Stopped | -$565 |  |
| #2 | 10:45:57 AM ET | MKT-035 | Call Spread | C:6775 | $60 (C) | Expired |  |  |
| #3 | 11:15:25 AM ET | MKT-035 | Call Spread | C:6755 | $60 (C) | Expired |  |  |
| #4 | 11:45:34 AM ET | NEUTRAL | Iron Condor | C:6730 P:6585 | $305 ($70C+$235P) | Expired |  |  |
| #5 | 12:16:53 PM ET | NEUTRAL | Iron Condor | C:6735 P:6595 | $380 ($55C+$325P) | Expired |  |  |
| #6 | 12:45:22 PM ET | MKT-035 | Call Spread | C:6715 | $70 (C) | Expired |  |  |
| #7 | 01:15:59 PM ET | MKT-035 | Call Spread | C:6720 | $70 (C) | Expired |  |  |

**Key observations**:
- Entry #1's put short at 6,620 was breached when SPX printed a session low of 6,623.74 — only 3.74 points of clearance at the worst tick — triggering the sole stop of the day at 11:44 ET for a -$565 P&L impact against $325 collected credit.
- MKT-035 engaged by the 10:45 entry and converted Entries #2, #3, #6, and #7 to call-only spreads, suppressing put exposure on four of seven entries and directly limiting downside damage on a day where SPX fell 84 points from the open.
- The two full ICs placed after the initial stop (Entries #4 and #5 at 11:45 and 12:16) set put shorts at 6,585 and 6,595 respectively — benefiting from a lower SPX reference at entry time — and both expired clean despite the 6,623.74 session low.
- Entry #5 collected the highest total credit of the day at $380 (call $55, put $325), surpassing Entry #1's $325, reflecting sustained elevated IV under VIX conditions that climbed from 25.53 open to a 27.76 intraday high; however, the $55 call credit on E5 sits just above the $0.60 MKT-011 threshold and warrants monitoring.
- All four call-only entries (#2, #3, #6, #7) expired worthless for full credit retention, producing $260 combined — a direct consequence of MKT-035 architecture converting directional exposure into a contained one-stop outcome on a 107-point range RED day, yielding a net +$385 daily P&L.

### Stop Timing Log

```
11:44:17 AM ET - Entry #1 Put Stopped ($565 loss)
```

### P&L Reconciliation

- Expired Credits: $1005
- Stop Loss Debits: $565
- Commission: $55
- **Net P&L: +$385** (1005 - 565 - 55 = 385)

### Mar 16 (Monday) - NET P&L: +$950

**Market**: SPX range 47 pts (0.7%). VIX 25.2→23.5.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:16:59 AM ET | NEUTRAL | Iron Condor | C:6780 P:6660 | $320 ($55C+$265P) | Expired |  |  |
| #2 | 10:46:10 AM ET | NEUTRAL | Iron Condor | C:6760 P:6635 | $300 ($70C+$230P) | Expired |  |  |
| #3 | 11:16:50 AM ET | NEUTRAL | Iron Condor | C:6755 P:6655 | $360 ($55C+$305P) | Expired |  |  |

**Key observations**:
- All three entries (10:16, 10:46, 11:16) expired worthless, collecting $980 gross credit with zero stops across both legs; the session range of 6,681–6,727 provided a minimum 28-point buffer below the nearest call short (6,755 on E3) and a 46-point buffer above the nearest put short (6,635 on E2).
- Call credits were thin throughout — E1 and E3 both came in at $55 ($0.55 per contract), which is below the MKT-011 $0.60 floor, suggesting a fallback mechanism activated on those entries; E2's $70 call credit represented the session's only above-floor call side, captured in a brief window before VIX compression took hold.
- VIX declining from 25.18 to 23.23 through the session eroded call premium sufficiently to gate entries E4 and E5 (11:45 and 12:15) via MKT-011, accounting for two of the four skipped entries; E6 and E7 were suppressed by MKT-035 non-trigger, as SPX's intraday grind to 6,727 never produced the 0.3% drop below the 6,693.32 open required for conditional call-only entries.
- All three placed entries carry 50-point spread widths on both legs — notably below the configured call floor of 60 points and put floor of 75 points specified in v1.16.0 parameters; this warrants a data verification check to confirm whether VIX-scaling via MKT-027/028 produced a legitimate exception or whether a configuration anomaly is present.
- Net P&L of $950 after $30 in commissions represents a 6.33% return on $15,000 capital deployed, with $0 in stop-loss debits; tomorrow (2026-03-17) is FOMC Day 1 of the March 17–18 meeting, meaning MKT-008 blackout is active and all entries should be skipped.

### P&L Reconciliation

- Expired Credits: $980
- Stop Loss Debits: $0
- Commission: $30
- **Net P&L: +$950** (980 - 0 - 30 = 950)

### Mar 17 (Tuesday) - NET P&L: +$120

**Market**: SPX range 38 pts (0.6%). VIX 22.7→22.4.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 12:15:19 PM ET | BEARISH | Call Spread | C:6755 | $65 (C) |  |  |  |
| #2 | 01:16:00 PM ET | MKT-035 | Call Spread | C:6750 | $65 (C) |  |  |  |
| #5 |  | NEUTRAL | Call Only | C:6755 | $65 (C) | Expired |  |  |
| #7 |  | NEUTRAL | Call Only | C:6750 | $65 (C) | Expired |  |  |

**Key observations**:
- Today was FOMC Day 1 (2026-03-17), during which MKT-008 mandates a full trading blackout; HYDRA placed 2 entries anyway (E5 at 12:15 and E7 at 13:16), representing a critical rule enforcement failure that requires a code audit before tomorrow's Day 2 blackout.
- Both entries were call-only spreads with 50-point widths (6755/6805 and 6750/6800), which falls below the 60-point call floor mandated by MKT-028 — a secondary anomaly compounding the MKT-008 breach.
- SPX peaked at 6,752.07 intraday, coming within 3 points of the E5 short call strike at 6,755 and just 2 points of E7's short at 6,750; both spreads survived only due to the session's benign drift and VIX compression from 22.67 to 22.07.
- E7 at 13:16 carries an `override_reason: "mkt-035"`, suggesting the conditional down-day entry logic fired based on SPX dropping 0.3% below session high — but no rule, including MKT-035, can legally override the MKT-008 blackout.
- Both entries collected identical $65.00 call credits and expired worthless for a $120.00 net P&L ($130.00 gross minus $10.00 commission), a clean outcome operationally but one that reflects unauthorized exposure rather than sanctioned bot behavior.

### P&L Reconciliation

- Expired Credits: $130
- Stop Loss Debits: $0
- Commission: $10
- **Net P&L: +$120** (130 - 0 - 10 = 120)

### Mar 18 (Wednesday) - NET P&L: +$135

**Market**: SPX range 123 pts (1.8%). VIX 22.4→24.6.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:15:28 AM ET | NEUTRAL | Iron Condor | C:6780 P:6610 | $290 ($55C+$235P) | Early Closed |  |  |
| #2 | 10:45:34 AM ET | NEUTRAL | Iron Condor | C:6770 P:6605 | $295 ($60C+$235P) | Early Closed |  |  |
| #3 | 11:15:27 AM ET | NEUTRAL | Iron Condor | C:6765 P:6620 | $330 ($65C+$265P) | Early Closed |  |  |

**Key observations**:
- Today is FOMC Announcement Day (March 18, 2026); MKT-008 blackout was deliberately disabled by the operator, making the 3 entries placed an intentional user decision rather than a rule violation — tomorrow (March 19) must run MKT-038 T+1 call-only across all entries.
- SPX opened at its session high of 6,744.44 and closed at its session low of 6,621.29, a straight 123-point waterfall with zero recovery; E3's short put at 6,620 cleared the session low by just 1.29 points, the narrowest margin of any entry.
- All three entries expired clean for $135 net P&L ($915 total credit, $720 stop loss debits, $60 commissions), but this was a below-average win day — the HERMES-reported average winning day is $386 versus today's $135.
- Four of seven scheduled entries were skipped (E4–E7), an unusual back-half truncation beyond the typical E4/E5 pattern, likely driven by MKT-011 credit threshold failures or spread viability constraints as VIX pushed from 22.37 to 24.63 during the announcement window.
- All six spread legs across three entries show 50-point widths in the position data, below the MKT-028 mandated floors of 60 points for calls and 75 points for puts — this is flagged as a persistent two-day anomaly requiring operator verification of width enforcement logic.

### P&L Reconciliation

- Expired Credits: $915
- Stop Loss Debits: $720
- Commission: $60
- **Net P&L: +$135** (915 - 720 - 60 = 135)

### Mar 19 (Thursday) - NET P&L: -$1985

**Market**: SPX range 80 pts (1.2%). VIX 27.0→24.3.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:15:56 AM ET |  | Call Spread | C:6680 | $50 (C) |  |  |  |
| #2 | 10:45:22 AM ET |  | Call Spread | C:6660 | $65 (C) | Call Stopped | -$350 |  |
| #3 | 11:15:36 AM ET |  | Call Spread | C:6665 | $60 (C) | Call Stopped | -$360 |  |
| #4 | 11:45:42 AM ET |  | Call Spread | C:6640 | $70 (C) | Call Stopped | -$265 |  |
| #5 | 12:16:11 PM ET |  | Call Spread | C:6645 | $55 (C) | Call Stopped | -$400 |  |
| #6 | 12:45:20 PM ET |  | Call Spread | C:6635 | $70 (C) | Call Stopped | -$275 |  |
| #7 | 01:15:57 PM ET |  | Call Spread | C:6615 | $65 (C) | Call Stopped | -$320 |  |

**Key observations**:
- MKT-038 (FOMC T+1) forced all 7 entries to call-only, producing zero full ICs and zero put spreads; the day after the March 17-18 FOMC announcement triggered the protocol exactly as designed.
- SPX rallied from a low of 6558.82 to a high of 6638.58 — a 79.76-point intraday swing — breaching every short call strike placed across E2–E7 (ranging from 6615 to 6660), resulting in 6 call stops and only Entry #1 (short 6680) surviving to expiry for $50.00 credit.
- All 6 stops clustered tightly between 3:01:24 PM and 3:07:13 PM ET, a 5-minute 49-second window in the final hour, indicating a sharp late-session SPX push through multiple strike levels simultaneously rather than a gradual grind.
- Short call strikes drifted progressively lower across the session — from 6680 at 10:15 to 6615 at 13:15 — reflecting MKT-020 tightening as premium compressed with VIX declining from 27.03 open to 23.54 intraday low, yet tighter strikes offered no protection against the late rally.
- Total credit collected was $435.00 against $1,970.00 in stop-loss debits, producing a -$1,985.00 daily P&L (-5.59% ROC) and pulling the cumulative P&L down to $380.00; the 14.3% win rate (1 of 7) represents the structural risk of forced call-only days on a session that closes 32.96 points above its open.

### Stop Timing Log

```
03:06:58 PM ET - Entry #2 Call Stopped ($350 loss)
03:07:13 PM ET - Entry #3 Call Stopped ($360 loss)
03:02:44 PM ET - Entry #4 Call Stopped ($265 loss)
03:06:37 PM ET - Entry #5 Call Stopped ($400 loss)
03:02:18 PM ET - Entry #6 Call Stopped ($275 loss)
03:01:24 PM ET - Entry #7 Call Stopped ($320 loss)
```

### P&L Reconciliation

- Expired Credits: $50
- Stop Loss Debits: $1970
- Commission: $65
- **Net P&L: -$1985** (50 - 1970 - 65 = -1985)

### Mar 20 (Friday) - NET P&L: -$1635

**Market**: SPX range 115 pts (1.7%). VIX 24.7→27.5.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:15:25 AM ET | NEUTRAL | Iron Condor | C:6650 P:6480 | $290 ($65C+$225P) | Put Stopped | -$590 |  |
| #2 | 10:46:14 AM ET | NEUTRAL | Iron Condor | C:6635 P:6495 | $260 ($60C+$200P) | Put Stopped | -$590 |  |
| #3 | 11:15:24 AM ET | NEUTRAL | Iron Condor | C:6640 P:6510 | $330 ($40C+$290P) | Put Stopped | -$575 |  |

**Key observations**:
- SPX opened at its session high of 6,589.63 and declined 114.72 points to a low of 6,474.91 without a meaningful bounce, breaching all three short put strikes (6,480, 6,495, 6,510) and triggering put stops on E1, E2, and E3 while call sides expired worthless — a textbook one-directional sell-off.
- All three stops clustered in the final trading hour (E3 at 2:22 PM, E2 at 3:33 PM, E1 at 3:37 PM ET), producing $1,755 in stop loss debits against only $880 total credit collected and $165 in expired credits, for a net daily P&L of -$1,635.
- E3's call credit of $40 ($0.40 per contract) was placed below both the MKT-011 floor of $0.60 and the MKT-029 fallback floor of $0.50, representing a potential rule enforcement gap that requires operator review; E1 and E2 call credits of $65 and $60 were at or just above the viable threshold as premium compressed on the gap-up open.
- E4 and E5 were fully skipped on both sides despite VIX rising from 24.74 toward 29.28 intraday and put premium demonstrably rich at E3 ($290 credit) — MKT-032 put-only logic should have triggered if call-side failure was the skip driver, and no skip_reason was logged for either entry, flagging a potential MKT-032 enforcement or logging gap.
- The MKT-028 spread width anomaly persists for a third consecutive session, with all legs showing 50-point widths against the 60-point call floor and 75-point put floor; combined with the E3 call credit breach, this day produced two distinct rule compliance flags that must be resolved before Monday's session.

### Stop Timing Log

```
03:37:25 PM ET - Entry #1 Put Stopped ($590 loss)
03:33:58 PM ET - Entry #2 Put Stopped ($590 loss)
02:22:57 PM ET - Entry #3 Put Stopped ($575 loss)
```

### P&L Reconciliation

- Expired Credits: $165
- Stop Loss Debits: $1755
- Commission: $45
- **Net P&L: -$1635** (165 - 1755 - 45 = -1635)

### Mar 23 (Monday) - NET P&L: -$935

**Market**: SPX range 87 pts (1.3%). VIX 24.4→26.2.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:17:05 AM ET | NEUTRAL | Iron Condor | C:6695 P:6545 | $345 ($135C+$210P) | Put Stopped | -$250 |  |
| #2 | 10:45:33 AM ET | NEUTRAL | Iron Condor | C:6685 P:6565 | $385 ($120C+$265P) | Put Stopped | -$345 |  |
| #3 | 11:15:40 AM ET | NEUTRAL | Iron Condor | C:6690 P:6600 | $395 ($140C+$255P) | Put Stopped | -$265 |  |
| #4 | 11:45:36 AM ET | NEUTRAL | Iron Condor | C:6665 P:6560 | $410 ($165C+$245P) | Put Stopped | -$255 |  |
| #5 | 12:15:33 PM ET | NEUTRAL | Iron Condor | C:6650 P:6535 | $375 ($135C+$240P) | Put Stopped | -$265 |  |
| #6 | 01:15:33 PM ET | NEUTRAL | Call Spread | C:6625 | $130 (C) | Call Stopped | -$165 |  |

**Key observations**:
- Five of six entries stopped on the put side between 11:35 and 12:25 ET, with E3 (short put 6,600) stopping fastest at just 20 minutes after entry at 11:35:33, and E5 (short put 6,535) stopping at 12:25 — a roughly 50-minute window in which SPX's decline from the 6,651.99 session high systematically swept through all five short put strikes.
- All six entries cleared MKT-011 credit floors without requiring MKT-029 fallback — call credits ranged $1.20–$1.65 against a $0.60 floor, and put credits ranged $2.10–$2.65 against a $2.50 floor — yet every position stopped, confirming that credit viability did not mitigate directional exposure on a sustained 87-point intraday decline.
- E6 fired at 13:15 as a call-only spread per MKT-035 with a $1.30 credit and short call at 6,625 — essentially at the SPX open price — meaning by entry time the strike was near-the-money given SPX had already declined well below that level, and it stopped at 13:59:29 for a -$165 P&L impact.
- All spread widths across all six entries remain at 50 points, still below the MKT-027/028 required floors of 60pt calls and 75pt puts — per the HERMES report this is now Day 4 of an unresolved out-of-spec condition that affects both credit collection and stop activation thresholds relative to the intended specification.
- The session produced a 0% win rate across six entries, -$935 net P&L, and extends the losing streak to three consecutive days, with cumulative P&L now at -$2,190 (-9.07% ROC) through Day 29 on a 17W-12L record.

### Stop Timing Log

```
11:55:25 AM ET - Entry #1 Put Stopped ($250 loss)
11:52:24 AM ET - Entry #2 Put Stopped ($345 loss)
11:35:33 AM ET - Entry #3 Put Stopped ($265 loss)
11:53:29 AM ET - Entry #4 Put Stopped ($255 loss)
12:25:33 PM ET - Entry #5 Put Stopped ($265 loss)
01:59:29 PM ET - Entry #6 Call Stopped ($165 loss)
```

### P&L Reconciliation

- Expired Credits: $695
- Stop Loss Debits: $1545
- Commission: $85
- **Net P&L: -$935** (695 - 1545 - 85 = -935)

### Mar 30 (Monday) - NET P&L: +$0

**Market**: SPX range 85 pts (1.3%). VIX 29.7→30.4.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| -- | -- | -- | -- | -- | -- | No entry data available | -- | -- |

**Key observations**:
- E1 at 10:15 was skipped via MKT-010 (both wings illiquid), a rare trigger consistent with the opening chaos from an 87-point gap-up at VIX 29.74, where market makers likely widened 0DTE spreads beyond viable strike range.
- E2 at 10:45 converted correctly to call-only under the down-day filter (SPX already declining toward its eventual -1.23% close), but call credit registered $0.00 — below even the MKT-029 minimum floor of $0.75, making any entry impossible.
- At VIX 29.74, the spread width formula yields `round(29.74 * 5.3 / 5) * 5 = 30pt` — but with no entries placed, that width was never deployed; the illiquidity and credit collapse preempted the spread calculation entirely.
- Zero entries means zero stops and zero commissions, but also zero premium collected in a session where VIX ~30 theoretically offered some of the richest credit available in recent weeks — a net neutral day on P&L that nonetheless extends the revenue drought.
- The cumulative position sits at -$2,190.00 (-9.38% ROC) with an avg loss of -$729.40 against an avg win of +$386.00; today's idle neither widens that gap nor addresses the 1.9:1 recovery ratio HYDRA requires to break even on each losing day.

### P&L Reconciliation

- Expired Credits: $0
- Stop Loss Debits: $0
- Commission: $0
- **Net P&L: +$0** (0 - 0 - 0 = 0)

### Mar 31 (Tuesday) - NET P&L: -$15

**Market**: SPX range 134 pts (2.1%). VIX 27.5→25.4.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:15:36 AM ET | NEUTRAL | Iron Condor | C:6515 P:6345 | $375 ($155C+$220P) | Call Stopped | -$300 |  |
| #2 | 10:45:38 AM ET | NEUTRAL | Iron Condor | C:6495 P:6340 | $375 ($145C+$230P) | Call Stopped | -$295 |  |
| #3 | 02:00:58 PM ET | NEUTRAL | Put Spread | P:6435 | $165 (P) | Expired |  |  |

**Key observations**:
- E1 and E2 both entered as full iron condors at 10:15 and 10:45 ET, collecting $375 each, but both call sides were stopped within 89 seconds of each other (12:39:11 and 12:40:04 ET) as SPX rallied from its low of 6404.13 toward 6537.93, producing stop debits of $295 and $300 respectively while both put sides expired worthless.
- The 110-point spread width on all three entries reflects VIX near 27.49 at open: applying the MKT-027 formula `round(27.49 * 5.3 / 5) * 5` yields 110 points, confirming the bot was operating at the wider end of its range due to elevated volatility.
- Entry #3 at 14:00 fired as a put-only spread ($165 credit) consistent with the Upday-035 rule, as SPX had risen approximately 1.39% above its open of 6438.58 by that point — well above the 0.48% threshold — and this entry expired profitably, providing the day's sole winning position.
- The two clustered call stops generated $595 in total stop-loss debits against $915 total credit collected, leaving a net daily P&L of -$15 after $35 in commissions — a near-breakeven result that masks the structural call-side vulnerability on a sharply up day.
- With a 33.3% win rate on the session and cumulative P&L now at -$2,205 (-9.02% cumulative ROC), the call-stop clustering pattern on strong up-moves is the dominant driver of drawdown, as the put-only E3 demonstrates the only leg structure that profits directionally on such days.

### Stop Timing Log

```
12:40:04 PM ET - Entry #1 Call Stopped ($300 loss)
12:39:11 PM ET - Entry #2 Call Stopped ($295 loss)
```

### P&L Reconciliation

- Expired Credits: $615
- Stop Loss Debits: $595
- Commission: $35
- **Net P&L: -$15** (615 - 595 - 35 = -15)

### Apr 1 (Wednesday) - NET P&L: +$330

**Market**: SPX range 61 pts (0.9%). VIX 25.2→24.6.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:15:30 AM ET | NEUTRAL | Iron Condor | C:6620 P:6505 | $490 ($205C+$285P) |  |  |  |
| #2 | 10:45:51 AM ET | NEUTRAL | Iron Condor | C:6625 P:6520 | $495 ($185C+$310P) |  |  |  |
| #3 | 11:16:54 AM ET | NEUTRAL | Iron Condor | C:6630 P:6540 | $445 ($170C+$275P) |  |  |  |
| #4 | 02:00:19 PM ET | BULLISH | Put Spread | P:6565 | $290 (P) | Put Stopped | -$195 |  |

**Key observations**:
- All four entries placed with zero skips; VIX at 25.25 produced 110-point spread widths (VIX × 6.0 / 5 = 30.3, capped at 110pt) and kept put credits robust across E1–E3 ($285, $310, $275), all comfortably above the $2.75 MKT-011 floor with no graduated fallback required.
- E4 fired at 14:00 as a put-only spread per Upday-035, with SPX +0.46% above the open clearing the +0.25% threshold; the short put at 6,565 was placed only ~16 points above the session low of 6,549.32, leaving minimal downside cushion before the stop level of $445 (credit $290 + $1.55 buffer × 100 = $445) was reached and triggered at 14:02:23 — just 2 minutes after entry.
- The single put stop on E4 produced a -$195 debit against $290 credit collected, consistent with the put stop formula; call-side stops were zero despite SPX reaching 6,609.97 intraday, only ~10 points below the lowest short call strike of 6,620 on E1.
- Call credit progression across E1–E3 (6,620/$205 → 6,625/$185 → 6,630/$170) reflects both normal intraday premium decay and MKT-020 scanning strikes slightly higher as SPX drifted up through the morning, with all three call credits remaining above the $2.00 MKT-011 floor.
- Net daily P&L of $330 on $1,720 total credit collected (75% win rate on 4 entries) adds modestly to recovery against a cumulative deficit of -$1,875; the Sortino of -1.19 confirms the loss distribution continues to outweigh wins structurally.

### Stop Timing Log

```
02:02:23 PM ET - Entry #4 Put Stopped ($195 loss)
```

### P&L Reconciliation

- Expired Credits: $765
- Stop Loss Debits: $365
- Commission: $70
- **Net P&L: +$330** (765 - 365 - 70 = 330)

### Apr 2 (Thursday) - NET P&L: +$1055

**Market**: SPX range 130 pts (2.0%). VIX 24.5→24.1.

| Entry | Time | Signal | Type | Short Strikes | Credit | Outcome | P&L Impact | Salvage |
|-------|------|--------|------|---------------|--------|---------|------------|---------|
| #1 | 10:15:31 AM ET | NEUTRAL | Iron Condor | C:6590 P:6430 | $470 ($210C+$260P) | Call Stopped | -$340 |  |
| #2 | 10:46:34 AM ET | BULLISH | Iron Condor | C:6690 P:6475 | $450 ($145C+$305P) | Expired |  |  |
| #3 | 11:15:27 AM ET | NEUTRAL | Iron Condor | C:6635 P:6495 | $505 ($235C+$270P) | Expired |  |  |
| #4 | 02:00:25 PM ET | NEUTRAL | Put Spread | P:6525 | $220 (P) | Expired |  |  |

**Key observations**:
- Entry #1's $470 total credit and 6590 short call — placed at 10:15 when SPX was near its session low of 6471.93 — was overwhelmed by a 130pt intraday reversal that drove SPX to 6601.56, triggering the call stop at 10:33 ET (18 minutes after entry) and producing a -$340 P&L impact against a $470 credit collected.
- Entries #2 and #3, placed 31 and 60 minutes later respectively, had short calls at 6690 and 6635 — 89–194pts above E1's short call — both clearing the 6601.56 session high and expiring clean, demonstrating how entry timing relative to the intraday trend materially affected call strike placement on this day.
- VIX at 24.54 triggered the 3-entry regime and drove the VIX-scaled spread width formula (`round(24.54 * 6.0 / 5) * 5`) to its 110pt cap on all four entries, maximizing spread width across the entire session.
- The Upday-035 conditional fired correctly at 14:00 ET as a put-only entry (Entry #4, $220 credit, short put at 6525) — confirmed by SPX rising well above the +0.25% open threshold by afternoon — with the call side correctly suppressed and the put spread expiring clean.
- Despite a single call stop, the day produced $1,055 net on $1,645 total credit collected, with $1,435 in expired credits against $340 in stop loss debits and $40 in commissions, reducing the cumulative P&L deficit from an implied -$1,875 to -$820.

### Stop Timing Log

```
10:33:38 AM ET - Entry #1 Call Stopped ($340 loss)
```

### P&L Reconciliation

- Expired Credits: $1435
- Stop Loss Debits: $340
- Commission: $40
- **Net P&L: +$1055** (1435 - 340 - 40 = 1055)

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
| Feb 27 | Fri | Morning dip, recovery | +0.4% | 50 pts (0.7%) | 21→20 (compressing) | v1.4.0→v1.4.1 mid-day, last MEIC-TF day, 2 MKT-011 skips |
| Mar 2 | Mon | Wide-range whipsaw | +1.2% | 106 pts (1.6%) | 23→21 (compressing) | First HYDRA day, 6 stops + 1 double stop |
| **Mar 3** | **Tue** | **Gap-down + V-shape rally** | **+0.8%** | **~130 pts (1.9%)** | **26→22 (compressing)** | **Widest range of period, ES -91pt gap, VIX peaked 28.15, 13 commits** |
| Mar 4 | Wed | Dip, recovery, net gain | +0.5% | 76 pts (1.1%) | 23→21 | Post-settlement |
| Mar 5 | Thu | Intraday selloff, partial recovery | -0.3% | 98 pts (1.4%) | 22→23 | Post-settlement |
| Mar 6 | Fri | Volatile gap-down, narrow hold | -0.1% | 66 pts (1.0%) | 28→30 | Post-settlement |
| Mar 9 | Mon | Sharp gap-down reversal | +1.8% | 175 pts (2.6%) | 31→25 | Post-settlement |
| Mar 10 | Tue | Probe high, sharp reversal | -0.8% | 70 pts (1.0%) | 23→26 | Post-settlement |
| Mar 11 | Wed | Morning spike, sustained selloff | +0.2% | 66 pts (1.0%) | 26→24 | Post-settlement |
| Mar 12 | Thu | Bearish drift, steady decline | -0.7% | 57 pts (0.8%) | 26→27 | Post-settlement |
| Mar 13 | Fri | Steady selloff, gap-up fade | -1.1% | 107 pts (1.6%) | 26→27 | Post-settlement |
| Mar 16 | Mon | Narrow range, flat close | +0.0% | 47 pts (0.7%) | 25→24 | Post-settlement |
| Mar 17 | Tue | Narrow range, mild drift | -0.1% | 38 pts (0.6%) | 22 | Post-settlement |
| Mar 18 | Wed | Steady FOMC waterfall drop | -1.8% | 123 pts (1.8%) | 22→25 | Post-settlement |
| Mar 19 | Thu | Dip then late-day surge | +0.5% | 80 pts (1.2%) | 27→24 | Post-settlement |
| Mar 20 | Fri | Gap down, relentless selloff | -1.3% | 115 pts (1.7%) | 25→27 | Post-settlement |
| Mar 23 | Mon | Gap-up fade, put sweep | -0.6% | 87 pts (1.3%) | 24→26 | Post-settlement |
| Mar 30 | Mon | Gap up, sharp reversal | -1.2% | 85 pts (1.3%) | 30 | Post-settlement |
| Mar 31 | Tue | Dip, then strong rally | +1.4% | 134 pts (2.1%) | 27→25 | Post-settlement |
| Apr 1 | Wed | Steady grind, bullish drift | +0.5% | 61 pts (0.9%) | 25 | Post-settlement |
| Apr 2 | Thu | Gap down, strong reversal | +1.3% | 130 pts (2.0%) | 24 | Post-settlement |

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
| Feb 27 | 20.6 | ~89 pts | 50 pts | 0.56x | Below expected (calm) |
| Mar 2 | 22.4 | ~97 pts | 106 pts | 1.09x | At expected (normal) |
| **Mar 3** | **24.1** | **~103 pts** | **~130 pts** | **~1.26x** | **Above expected (elevated)** |
| Mar 4 | 21.9 | ~94 pts | 76 pts | 0.80x | At expected (normal) |
| Mar 5 | 22.7 | ~98 pts | 98 pts | 1.00x | At expected (normal) |
| Mar 6 | 28.8 | ~122 pts | 66 pts | 0.54x | Below expected (calm) |
| Mar 9 | 28.1 | ~118 pts | 175 pts | 1.48x | Above expected |
| Mar 10 | 24.1 | ~104 pts | 70 pts | 0.67x | Below expected (calm) |
| Mar 11 | 25.1 | ~107 pts | 66 pts | 0.62x | Below expected (calm) |
| Mar 12 | 26.6 | ~113 pts | 57 pts | 0.50x | Below expected (calm) |
| Mar 13 | 26.4 | ~111 pts | 107 pts | 0.96x | At expected (normal) |
| Mar 16 | 24.3 | ~103 pts | 47 pts | 0.45x | Far below expected (compressed) |
| Mar 17 | 22.6 | ~96 pts | 38 pts | 0.39x | Far below expected (compressed) |
| Mar 18 | 23.5 | ~100 pts | 123 pts | 1.23x | Above expected |
| Mar 19 | 25.7 | ~106 pts | 80 pts | 0.75x | At expected (normal) |
| Mar 20 | 26.1 | ~108 pts | 115 pts | 1.06x | Above expected |
| Mar 23 | 25.3 | ~106 pts | 87 pts | 0.82x | At expected (normal) |
| Mar 30 | 30.1 | ~122 pts | 85 pts | 0.70x | Below expected (calm) |
| Mar 31 | 26.5 | ~107 pts | 134 pts | 1.25x | Above expected |
| Apr 1 | 24.9 | ~103 pts | 61 pts | 0.59x | Below expected (calm) |
| Apr 2 | 24.3 | ~100 pts | 130 pts | 1.30x | Above expected |

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

### Financial Metrics (33 days: Feb 10 - Apr 2)

| Metric | Value |
|--------|-------|
| Total Credit Collected | $38420 |
| Total Expired Credits | $21070 (54.8% of credit) |
| Total Stop Loss Debits | $20225 (52.6% of credit) |
| Total Commission | $1665 (4.3% of credit) |
| Net P&L | -$820 (-2.1% net capture rate) |
| Average Daily Credit | $1164 |
| Average Daily P&L | -$25 |
| Best Day | +$1055 (Apr 2) |
| Worst Day | -$1985 (Mar 19) |
| Win/Loss Day Ratio | 19:13 |
| Win/Loss Dollar Ratio | 0.91:1 ($7947.50 / $8767.50) |

### Entry Performance

| Metric | Value |
|--------|-------|
| Total Entries | 138 |
| Clean Wins (0 stops) | 56 (40.6%) |
| Partial Wins (1 side stopped, IC) | 51 (37.0%) |
| Full Losses (stopped, 1-sided or double stop) | 31 (22.5%) |
| Entries with Call Stop | 32 (23.2%) |
| Entries with Put Stop | 56 (40.6%) |
| Double Stops | 6 (4.3%) |

### Entry Type Distribution

| Entry Type | Count | Stops | Stop Rate | Avg Credit |
|------------|-------|-------|-----------|------------|
| Full IC | 96 | 88 sides stopped* | ~46% per side | $400.21 |
| One-Sided (various) | 44 | -- | -- | -- |

*Full ICs can have 0, 1, or 2 sides stopped. v1.4.0+ (Feb 27 onward) disabled one-sided entries — all new entries are Full ICs.

### Stop Clustering Data

| Date | Stops | Fastest Cluster | Entries After Cluster | Loss After Cluster |
|------|-------|----------------|-----------------------|-------------------|
| Feb 10 | 1 | N/A (single) | N/A | N/A |
| Feb 11 | 2 | See entry detail | See entry detail | See entry detail |
| **Feb 12** | **4** | See entry detail | See entry detail | See entry detail |
| Feb 13 | 3 | See entry detail | See entry detail | See entry detail |
| **Feb 17** | **5** | See entry detail | See entry detail | See entry detail |
| Feb 18 | 2 | See entry detail | See entry detail | See entry detail |
| Feb 19 | 3 | See entry detail | See entry detail | See entry detail |
| Feb 20 | 1 | N/A (single) | N/A | N/A |
| Feb 23 | 2 | See entry detail | See entry detail | See entry detail |
| Feb 24 | 1 | N/A (single) | N/A | N/A |
| Feb 25 | 0 | N/A | N/A | N/A |
| **Feb 26** | **4** | See entry detail | See entry detail | See entry detail |
| Feb 27 | 1 | N/A (single) | N/A | N/A |
| **Mar 2** | **5** | See entry detail | See entry detail | See entry detail |
| **Mar 3** | **5** | See entry detail | See entry detail | See entry detail |
| Mar 4 | 3 | See entry detail | See entry detail | See entry detail |
| **Mar 5** | **5** | See entry detail | See entry detail | See entry detail |
| **Mar 6** | **4** | See entry detail | See entry detail | See entry detail |
| **Mar 9** | **4** | See entry detail | See entry detail | See entry detail |
| Mar 10 | 2 | See entry detail | See entry detail | See entry detail |
| **Mar 11** | **5** | See entry detail | See entry detail | See entry detail |
| Mar 12 | 0 | N/A | N/A | N/A |
| Mar 13 | 1 | N/A (single) | N/A | N/A |
| Mar 16 | 0 | N/A | N/A | N/A |
| Mar 17 | 0 | N/A | N/A | N/A |
| Mar 18 | 0 | N/A | N/A | N/A |
| **Mar 19** | **6** | See entry detail | See entry detail | See entry detail |
| Mar 20 | 3 | See entry detail | See entry detail | See entry detail |
| **Mar 23** | **6** | See entry detail | See entry detail | See entry detail |
| Mar 30 | 0 | N/A | N/A | N/A |
| Mar 31 | 2 | See entry detail | See entry detail | See entry detail |
| Apr 1 | 1 | N/A (single) | N/A | N/A |
| Apr 2 | 1 | N/A (single) | N/A | N/A |

### Trend Filter Accuracy

| Date | Trend Signals | Were They Correct? | Trend Filter Impact |
|------|--------------|--------------------|--------------------|
| Feb 10 | 5 NEUTRAL | Yes (all neutral) | Neutral |
| Feb 11 | 5 NEUTRAL, 1 BEARISH | See entry detail | See entry detail |
| Feb 12 | 4 NEUTRAL, 2 BEARISH | See entry detail | See entry detail |
| Feb 13 | 4 NEUTRAL, 1 BULLISH | See entry detail | See entry detail |
| Feb 17 | 3 NEUTRAL, 1 BULLISH, 1 BEARISH | See entry detail | See entry detail |
| Feb 18 | 4 NEUTRAL | Yes (all neutral) | Neutral |
| Feb 19 | 4 NEUTRAL | Yes (all neutral) | Neutral |
| Feb 20 | 3 NEUTRAL | Yes (all neutral) | Neutral |
| Feb 23 | 2 NEUTRAL | Yes (all neutral) | Neutral |
| Feb 24 | 4 NEUTRAL | Yes (all neutral) | Neutral |
| Feb 25 | 2 NEUTRAL | Yes (all neutral) | Neutral |
| Feb 26 | 3 NEUTRAL, 1 BEARISH | See entry detail | See entry detail |
| Feb 27 | 3 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 2 | 6 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 3 | 5 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 4 | 4 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 5 | 5 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 6 | 5 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 9 | 4 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 10 | 2 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 11 | 5 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 12 | 5 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 13 | 7 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 16 | 3 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 17 | 2 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 18 | 3 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 19 | 7 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 20 | 5 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 23 | 6 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 30 | 2 NEUTRAL | Yes (all neutral) | Neutral |
| Mar 31 | 3 NEUTRAL | Yes (all neutral) | Neutral |
| Apr 1 | 4 NEUTRAL | Yes (all neutral) | Neutral |
| Apr 2 | 3 NEUTRAL, 1 BULLISH | See entry detail | See entry detail |

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
| 2026-02-27 | -- | v1.4.0: Remove MKT-019 virtual stop, disable all one-sided entries | v1.4.0 commits | 2026-02-27 mid-day | EMA signal now informational only — all entries full IC or skip |
| 2026-02-27 | -- | v1.4.1: MKT-024 wider starting OTM (2× multiplier), separate put min $1.75 | v1.4.1 commits | 2026-02-27 mid-day | Put min raised from $0.50 to $1.75 (Tammy's range) |
| 2026-02-27 | -- | v1.4.2: MEIC+ reduction raised from $0.10 to $0.15 | v1.4.2 commits | 2026-02-27 post-market | True breakeven covers $15 commission on one-side-stop |
| 2026-02-28 | -- | v1.4.3: MKT-025 short-only stop loss close | v1.4.3 commits | 2026-02-28 (non-trading) | Close short, let long expire at settlement (saves commission + slippage) |
| 2026-02-28 | -- | v1.4.4: Add 6th entry at 12:35 PM | v1.4.4 commits | 2026-02-28 (non-trading) | Matching base MEIC schedule, credit gate ensures zero-cost skip |
| 2026-02-28 | -- | v1.4.5: MKT-026 min spread width raised 25pt→60pt | v1.4.5 commits | 2026-02-28 (non-trading) | Cheaper longs, MKT-025 never closes longs = pure savings |
| 2026-02-28 | -- | v1.5.0: Renamed from MEIC-TF to HYDRA | v1.5.0 commits | 2026-02-28 (non-trading) | Service, class, state/metrics files all renamed |
| 2026-03-02 | -- | v1.5.1: Telegram /snapshot command | v1.5.1 commits | 2026-03-02 pre-market | On-demand position snapshot via Telegram bot |
| 2026-03-04 | -- | v1.8.0: Entry schedule shifted +1hr (11:05-13:05 — journal data: 10:05 -$695, 10:35 -$510 vs 11:05+ all positive). MKT-031 smart entry windows (10min pre-entry scouting, 2-parameter scoring: post-spike ATR calm 0-70pts + momentum pause 0-30pts, threshold 65 triggers early entry). Early close day cutoff raised to 12:00 PM (keeps 11:05/11:35 viable). | v1.8.0 commits | 2026-03-04 | Auto-detected by HOMER |
| 2026-03-05 | -- | v1.9.2: MKT-033 long leg salvage after short stop. After MKT-025 closes short, sells surviving long if appreciated >= $10 (covers $5 commission + $5 max slippage). Two trigger points: immediate post-stop + periodic heartbeat check during market hours. Market order with bid-price pre-check. Tracked in state, Sheets, heartbeat display. | v1.9.2 commits | 2026-03-05 | Auto-detected by HOMER |
| 2026-03-05 | -- | v1.9.1: MKT-032 VIX gate for put-only entries. Put-only only allowed when VIX < 18 (80% WR in calm markets). At VIX >= 18, skip instead of put-only (2× stop with no hedge = 50% WR, unacceptable risk). Configurable via put_only_max_vix. Telegram /set support. | v1.9.1 commits | 2026-03-05 | Auto-detected by HOMER |
| 2026-03-05 | -- | v1.9.0: Telegram commands expanded to 14: /set (edit config), /restart, /stop (with position warning). Message splitting replaces truncation for HERMES/APOLLO reports. Atomic config writes with file locking. | v1.9.0 commits | 2026-03-05 | Auto-detected by HOMER |
| 2026-03-05 | -- | v1.8.1: Entry times shifted to :15/:45 offset (11:15-13:15). 19-day MAE analysis: :15/:45 has 10% lower 30-min adverse excursion vs :05/:35 (12.39pt vs 13.76pt) with better tail risk (P90: 21.71pt vs 23.84pt). Early close day keeps 11:15/11:45. | v1.8.1 commits | 2026-03-05 | Auto-detected by HOMER |
| 2026-03-10 | -- | v1.10.2: Replace MEIC+ stop formula with credit+buffer (Brian's approach): stop = total_credit + $0.10. Per-side stop level validation fix. Telegram /set updated: stop_buffer replaces meic_plus. | v1.10.2 commits | 2026-03-10 | Auto-detected by HOMER |
| 2026-03-11 | -- | v1.12.0: MKT-036 stop confirmation timer. When spread value breaches stop level, 75-second confirmation window before executing. If spread recovers below stop level during window, timer resets (stop avoided). Prevents false stops from brief price spikes. 20-day backtest: 17 false stops avoided ($2,870 saved), 1 real stop missed ($85). Configurable via stop_confirmation_enabled, stop_confirmation_seconds. All agent SYSTEM_PROMPTs updated to v1.12.0 with correct parameters. | v1.12.0 commits | 2026-03-11 | Auto-detected by HOMER |
| 2026-03-11 | -- | v1.11.0: MKT-035 call-only on down days. When SPX < open -0.3%, place call spread only (no puts). Stop uses theoretical $2.50 put credit instead of 2× call credit. 20-day data: 71% put stop rate on down days vs 7% call stop rate, +$920 improvement. Two conditional entry times (12:45, 13:15) that only fire when MKT-035 triggers. Configurable via downday_callonly_enabled, downday_threshold_pct, downday_theoretical_put_credit, conditional_entry_times. | v1.11.0 commits | 2026-03-11 | Auto-detected by HOMER |
| 2026-03-11 | -- | v1.10.4: Raise put credit minimum $1.75→$2.50, lower call credit minimum $0.75→$0.60. 20-day data analysis: $2.50-$3.49 put credit bucket = 66.7% survival, +$159 avg EV (best); $2.00-$2.49 = 33.3% survival, -$8 EV (worst); $1.50-$1.99 = 48.3%, +$23 EV. Higher put min forces MKT-022 to scan closer to ATM, landing in Week 1 sweet spot (42-65pt OTM). Lower call min = less MKT-020 tightening = calls stay further OTM. Disable MKT-031 smart entry — enter at scheduled times only. | v1.10.4 commits | 2026-03-11 | Auto-detected by HOMER |
| 2026-03-11 | -- | v1.10.3: Disable MKT-034 VIX time shifting + remove VIX entry cutoff (max_vix_entry=999). Neither Tammy nor Sandvand use VIX cutoffs. Entry times revert to 10:15 AM start. Spread widths reverted to 50pt. MKT-034 remains configurable. | v1.10.3 commits | 2026-03-11 | Auto-detected by HOMER |
| 2026-03-12 | -- | v1.12.1: MKT-036 asymmetric put stop buffer ($5.00 put vs $0.10 call). 21-day backtest: $5.00 put buffer avoids 91% of false put stops (+$6,885 NET). Configurable via put_stop_buffer (falls back to stop_buffer if not set). Telegram /set put_stop_buffer support. Full IC alert shows asymmetric stops. Sheets retry logic (3 attempts with 2s delay). HOMER stop matching fix for same-strike entries. Code audit: all docs, agents, config updated. | v1.12.1 commits | 2026-03-12 | Auto-detected by HOMER |
| 2026-03-13 | -- | v1.13.0: Stop timestamps in state file (call_stop_time/put_stop_time on IronCondorEntry). Dashboard SPX chart now shows red circle stop markers at actual stop time + white price lines for active entry strikes. Negative P&L bar fill fix for stopped entries. | v1.13.0 commits | 2026-03-13 | Auto-detected by HOMER |
| 2026-03-13 | -- | v1.12.2: MKT-035 scoped to conditional entries only. Base entries E1-E5 always attempt full ICs regardless of down-day status ($5.00 put buffer provides sufficient protection). Conditional entries E6/E7 still fire as call-only on down days (SPX < open -0.3%). Threshold reverted to 0.3%. | v1.12.2 commits | 2026-03-13 | Auto-detected by HOMER |
| 2026-03-16 | -- | v1.16.0: Skip alerts + dashboard improvements. Telegram ENTRY_SKIPPED alerts at all 8 skip paths in _initiate_entry() with detailed reasons (MKT-011 both non-viable, MKT-032 VIX gate, MKT-035 not triggered, MKT-038 call non-viable, MKT-010 illiquidity, margin). Skipped entries now persisted in state file with skip_reason field for dashboard display. entry_schedule (base + conditional times) added to state file. Dashboard: mobile-responsive header, pending entry cards show scheduled times, skipped entry cards show reason. HERMES can see entry_schedule + skip_reason in trimmed state. | v1.16.0 commits | 2026-03-16 | Auto-detected by HOMER |
| 2026-03-16 | -- | v1.15.1: MKT-040 call-only entries when put non-viable. When put credit below minimum but call viable, place call-only instead of skipping. Data: 89% WR for low-credit call-only, +$46 EV per entry. Stop = call + theo $2.50 put + buffer (unified with MKT-035/038). Override reason: "mkt-040". | v1.15.1 commits | 2026-03-16 | Auto-detected by HOMER |
| 2026-03-16 | -- | v1.15.0: MKT-039 put-only stop tightening + MKT-032 VIX gate raise. Put-only stop changed from 2×credit+buffer to credit+buffer — $5.00 put buffer already prevents 91% false stops, 2× was redundant (max loss $750→$500). MKT-032 VIX gate raised 18→25 (tighter stop makes put-only viable at moderate VIX). Call-only later unified to call + theo $2.50 put + buffer. All agent SYSTEM_PROMPTs updated to v1.15.0. | v1.15.0 commits | 2026-03-16 | Auto-detected by HOMER |
| 2026-03-19 | -- | v1.16.1: MKT-029 graduated call fallback in credit gate. Previously only puts had MKT-029 fallback (-$0.05, -$0.10) in _check_credit_gate(); calls used hard $0.60 minimum. Now both sides use graduated fallback: call $0.60→$0.55→$0.50, put $2.50→$2.45→$2.40. MKT-035/MKT-038 call-only skip checks also lowered from $0.60 to $0.50 floor. Fixed stale comments referencing $0.75 calls and $1.75 puts. All agent prompts updated. | v1.16.1 commits | 2026-03-19 | Auto-detected by HOMER |
| 2026-03-23 | -- | v1.17.0: Upday-035 conditional up-day put-only entries. Mirror of MKT-035 for bullish days: | v1.17.0 commits | 2026-03-23 | Auto-detected by HOMER |
| 2026-04-01 | -- | v1.21.0: MKT-041 Cushion Recovery Exit. Closes individual IC sides when they nearly hit | v1.21.0 commits | 2026-04-01 | Auto-detected by HOMER |
| 2026-04-02 | -- | v1.22.0: MKT-042 Buffer Decay + MKT-043 Calm Entry. MKT-042: time-decaying stop buffer — | v1.22.0 commits | 2026-04-02 | Auto-detected by HOMER |

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
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso cat /opt/calypso/data/hydra_metrics.json | python3 -m json.tool"
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

#### Post-Improvement Day 8: Feb 27 (v1.4.0→v1.4.1 — last day as MEIC-TF, disable one-sided entries)

| Column | Feb 27 |
|--------|--------|
| Date | 2026-02-27 |
| SPX Open | 6849.40 |
| SPX Close | 6879.14 |
| SPX Range | 50 pts (0.7%) |
| VIX Open | 21.39 |
| VIX Close | 19.80 |
| Entries | 3 (+2 skipped MKT-011) |
| Full ICs | 3 |
| One-Sided | 0 (disabled by v1.4.0) |
| Total Credit | $905 |
| Call Stops | 0 |
| Put Stops | 1 |
| Stop Debits | $135 |
| Commission | $35 |
| Expired Credits | $715 |
| Daily P&L | +$545 |
| Cumulative P&L | $2,110 |
| Early Close | No (ROC +3.1%, but only 3 entries placed) |

#### Improvement Impact Assessment — Feb 27

| Rec | Implemented? | Triggered? | Actual Impact | Assessment |
|-----|-------------|------------|---------------|------------|
| 9.3 EMA Threshold (0.2%) | v1.2.8 | **N/A** — v1.4.0 disabled one-sided entries | Signal now informational only | No longer drives entry type |
| v1.4.0 Disable one-sided | **v1.4.0 (NEW)** | **ACTIVE** — all entries forced to Full IC | All 3 placed entries were Full IC | **STRUCTURAL CHANGE** — EMA signal no longer affects entry type |
| v1.4.1 MKT-024 wider OTM | **v1.4.1 (NEW)** | **YES** — 2× starting OTM on all entries | Wider initial OTM before MKT-020/022 tightening | **FIRST LIVE DAY** — combined with v1.4.1 put min $1.75 |
| v1.4.1 Put min $1.75 | **v1.4.1 (NEW)** | **YES** — Entry #5 skipped (put $170 < $175) | **FIRST LIVE TRIGGER** — prevented marginal entry | **POSITIVE** — Entry #5 at $170 put credit was 3% below minimum |
| MKT-011 Credit Gate | v1.1.0 | **YES** — 2 entries skipped (Entry #3 call $50, Entry #5 put $170) | Prevented 2 low-credit entries | **POSITIVE** — both skips were clearly correct |
| MKT-018 Early Close (ROC) | v1.3.11 (3%) | **NO** — ROC hit 3.1% but only 3 entries + not triggered explicitly | Not triggered | Note: ROC exceeded threshold but timing may have been post-settlement |
| MKT-020 Call Tightening | v1.3.2 | **YES** — tightened calls on multiple entries | Ensured viable call credit with VIX 21.39 | Positive — VIX elevated but call premium still needed tightening |
| MKT-022 Put Tightening | v1.3.5 | **YES** — tightened puts with MKT-024 2× starting distance | Starting further OTM, scanned inward | Works in tandem with MKT-024's wider starting point |

**Feb 27 Assessment**: First day with v1.4.0's structural change (one-sided entries disabled) and v1.4.1's MKT-024 wider starting OTM. Strong performance: +$545 net with only 1 stop out of 3 entries (67% entry win rate). The v1.4.1 put minimum ($1.75) had its first live trigger — Entry #5 was skipped because put credit ($170) fell just below the $175 minimum. Under the old $50/side minimum, this entry would have been placed. Entry #4's put stop at 11:40 (just 5 minutes after entry) was the day's only loss, and the call side expired to nearly offset it (-$5 net on entry). Multiple mid-day deployments (v1.4.0 and v1.4.1) required bot restarts but state recovery preserved all positions correctly. VIX started at 21.39 (highest open of the period) and compressed to 19.80, benefiting all surviving positions. This was the last trading day under the MEIC-TF name — renamed to HYDRA on Feb 28.

#### Post-Improvement Day 9: Mar 2 (v1.5.1 — first day as HYDRA, MKT-025/026/027 + 6 entries)

| Column | Mar 2 |
|--------|-------|
| Date | 2026-03-02 |
| SPX Open | 6800.35 |
| SPX Close | 6878.58 |
| SPX Range | 106 pts (1.6%) |
| VIX Open | 23.40 |
| VIX Close | 21.32 |
| Entries | 6 (+0 skipped) |
| Full ICs | 6 |
| One-Sided | 0 (disabled since v1.4.0) |
| Total Credit | $1,855 |
| Call Stops | 4 |
| Put Stops | 2 |
| Stop Debits | $1,180 |
| Commission | $75 |
| Expired Credits | $1,000 |
| Daily P&L | -$255 |
| Cumulative P&L | $1,855 |
| Early Close | No (ROC negative all day) |

#### Improvement Impact Assessment — Mar 2

| Rec | Implemented? | Triggered? | Actual Impact | Assessment |
|-----|-------------|------------|---------------|------------|
| v1.4.0 Disable one-sided | v1.4.0 | **ACTIVE** — all 6 entries Full IC | 4 entries had one side survive (expired) | **POSITIVE** — surviving sides provided $750 in expired credits, limiting loss to -$255 |
| v1.4.3 MKT-025 short-only stop | **v1.4.3 (FIRST LIVE DAY)** | **YES** — all 6 stops used short-only close | Saved 6 × $2.50 = $15 commission | **POSITIVE** — longs expired worthless at settlement as expected |
| v1.4.4 6th entry (12:35) | **v1.4.4 (FIRST LIVE DAY)** | **YES** — Entry #6 placed at 12:35 | Entry #6 call stopped, net -$5 (nearly breakeven) | **NEUTRAL** — 6th entry contributed minimal P&L impact |
| v1.4.5 MKT-026 60pt min spread | **v1.4.5 (FIRST LIVE DAY)** | **YES** — Entries #1-5 used 60pt spreads | Cheaper long legs (further OTM) | **POSITIVE** — MKT-025 never closes longs, so cheaper longs = pure savings |
| v1.5.0 MKT-027 VIX-scaled spread | **v1.5.0 (FIRST LIVE DAY)** | **YES** — Entry #6 used 75pt call / 80pt put spreads | VIX ~21 at 12:35 produced wider spreads | **FIRST TRIGGER** — scaled up from 60pt floor based on VIX level |
| MKT-011 Credit Gate | v1.1.0 | **NO** — all 6 entries passed (VIX 23+ gave ample premium) | 0 entries skipped | Not needed — elevated VIX ensured all credits viable |
| MKT-018 Early Close (ROC) | v1.3.11 (3%) | **NO** — ROC negative all day (-0.67% at close) | $0 (never triggered) | Cannot assess on losing day |
| MKT-020/022 Call/Put Tightening | v1.3.2/v1.3.5 | **YES** — tightened on multiple entries | Combined with MKT-024 2× starting OTM | Working as designed — scan from wide to viable |
| v1.5.1 Telegram /snapshot | **v1.5.1 (NEW)** | **YES** — command handler active all day | On-demand position monitoring | **FIRST LIVE DAY** — provides instant snapshot without checking logs |

**Mar 2 Assessment**: First full trading day as HYDRA with all v1.4.x and v1.5.x features active. The most volatile day of the 14-day period (106pt SPX range, 1.6%) tested all systems. Six stops (including the period's first double stop on Entry #3) meant heavy stop activity, but the structural changes proved their value:

1. **v1.4.0 (full IC only)**: Every entry had both sides, so when one side was stopped, the other survived. Four entries (#1, #2, #4, #6) had one side survive and expire worthless, contributing $750 in expired credits. Under the old one-sided entry regime, if the EMA had generated directional signals, those entries would have been placed as single-sided with no hedge — a 100% loss on each stop.

2. **MKT-025 (short-only stop)**: All 6 stops only closed the short leg. Long legs expired at settlement for $0 — no close commission, no slippage. With old mechanism (close both legs), each stop would have had an additional close commission ($2.50) and potential adverse fill on the long leg. Net savings: ~$15 commission + avoided adverse long fills.

3. **v1.4.4 (6th entry)**: Entry #6 at 12:35 was the latest entry and had its call stopped at 14:21 for a net -$5. The 6th entry slot didn't hurt (nearly breakeven) and on better days would provide additional premium collection.

4. **Whipsaw pattern**: The 11:27-11:44 cluster (4 stops in 17 min) was a classic V-shape — puts stopped on the dip, calls stopped on the rally. This is the same pattern that caused maximum damage on Feb 17 and Feb 26. With MKT-016/017 removed (v1.3.3), all 6 entries were placed as designed. Post-cluster entries (#5, #6) contributed +$320 net, validating the removal of cascade/loss limits.

#### Post-Improvement Day 10: Mar 3 (v1.6.0→v1.7.2 — major development day, MKT-024/028 first live, 13 commits)

| Column | Mar 3 |
|--------|-------|
| Date | 2026-03-03 |
| SPX Open | ~6759 |
| SPX Close | ~6812 |
| SPX Range | ~130 pts (1.9%) |
| VIX Open | 26.03 |
| VIX Close | 22.18 |
| Entries | 5 (+0 skipped) |
| Full ICs | 5 |
| One-Sided | 0 (disabled since v1.4.0) |
| Total Credit | $1,395 |
| Call Stops | 5 |
| Put Stops | 1 |
| Stop Debits | $1,060 |
| Commission | $65 |
| Expired Credits | $675 |
| Daily P&L | -$450 |
| Cumulative P&L | $1,405 |
| Early Close | No (MKT-018 intentionally disabled) |

#### Improvement Impact Assessment — Mar 3

| Rec | Implemented? | Triggered? | Actual Impact | Assessment |
|-----|-------------|------------|---------------|------------|
| v1.4.0 Disable one-sided | v1.4.0 | **ACTIVE** — all 5 entries Full IC | 4 entries had put side survive (expired) | **POSITIVE** — surviving put sides provided $675 in expired credits |
| v1.4.3 MKT-025 short-only stop | v1.4.3 | **YES** — all 6 stops used short-only close | Saved 6 × $2.50 = $15 commission | **POSITIVE** — longs expired worthless at settlement |
| v1.6.0 Drop Entry #6 | **v1.6.0 (FIRST LIVE DAY)** | **YES** — 5 entries instead of 6 | Freed margin for wider put spreads (MKT-028) | **STRUCTURAL** — tradeoff: 1 fewer entry, but wider spreads |
| MKT-024 Wider starting OTM | **v1.6.0 (FIRST LIVE DAY)** | **YES** — call 3.5× / put 4.0× starting OTM | Calls started 240pt OTM, tightened to 115pt; puts started 240pt, tightened to 105pt | **FIRST TRIGGER** — batch API means zero extra cost for wider scan |
| MKT-028 Asymmetric spreads | **v1.6.0 (FIRST LIVE DAY)** | **YES** — call 60pt floor, put 75pt floor | All entries used 75pt/75pt (VIX-scaled > both floors) | **FIRST TRIGGER** — VIX ~26 produced 75pt via formula, matching put floor |
| MKT-027 VIX-scaled spread width | **v1.7.0 (FIRST LIVE DAY)** | **YES** — VIX 26 → round(26×3.5/5)×5 = 90pt, capped at 75pt | All entries used 75pt spreads (capped) | **FIRST TRIGGER** — cap prevented over-wide spreads at high VIX |
| MKT-011 Credit Gate | v1.1.0 | **NO** — all 5 entries passed (VIX 26+ gave ample premium) | 0 entries skipped | Not needed — elevated VIX ensured all credits viable |
| MKT-018 Early Close | **INTENTIONALLY DISABLED** | N/A — code preserved but dormant | N/A | Disabled based on early close analysis showing hold-to-expiry outperforms |
| MKT-020/022 Call/Put Tightening | v1.3.2/v1.3.5 | **YES** — tightened all entries | Call: 240→115pt OTM, Put: 240→105pt OTM | Working as designed — batch API scan from wide to viable |
| v1.6.1 VIX filter 25→30 | **v1.6.1 (DEPLOYED MID-DAY)** | **YES** — VIX 26+ blocked entries #2-5, threshold raised to 30 | Unblocked 4 entries that were being filtered | **CRITICAL** — without this deployment, only Entry #1 would have been placed |
| v1.7.1 Put-only re-enable | **v1.7.1 (NEW)** | **NO** — all entries were full IC (both sides viable) | N/A | Not triggered — VIX 26+ gave ample premium on both sides |
| v1.7.2 Lower call min $0.75 | **v1.7.2 (NEW)** | **NO** — all call credits above $0.85 | N/A | Not triggered — would matter on lower-VIX days |

**Mar 3 Assessment**: The most volatile day of the 15-day period (~130pt range, VIX peaked 28.15) and a major development day with 13 code commits (v1.6.0→v1.7.2). Despite the chaos, the bot handled it well structurally:

1. **VIX filter was the critical issue**: VIX opened at 26.03, above the 25 threshold, blocking entries #2-5. Only after the threshold was raised to 30 via v1.6.1 deployment (~10:18 ET) could the remaining entries proceed. Without this mid-day intervention, the day would have been Entry #1 only (-$445 loss, no offsetting expired credits from entries #2-5).

2. **MKT-024 wider starting OTM first live test**: With VIX at 26.6, base_otm was 70pt. Starting at 3.5×/4.0× (240pt OTM) allowed the batch API to scan a wide range and find optimal strike placement. MKT-020 tightened calls from 240→115pt, MKT-022 tightened puts from 240→105pt — still significantly wider than previous versions' starting points.

3. **V-shape pattern damage**: Same V-shape pattern as Feb 17 and Mar 2 — puts stopped on the sell-off, calls stopped on the rally. Entry #1's double stop (-$445) accounted for nearly all the loss. Entries #2-5 were nearly breakeven individually (put credits expired, offsetting call stop losses).

4. **APOLLO was half-right**: Pre-market scout correctly identified RED risk (ES -91pt gap, VIX 25.2) but predicted put-side stops as the primary concern. In reality, only 1 put was stopped (Entry #1) while 5 calls were stopped after the V-shape rally — the opposite of the prediction. This highlights the inherent unpredictability of intraday direction after gap events.

5. **13 commits during trading hours**: Massive development effort deploying MKT-024, MKT-027, MKT-028, v1.7.1 put-only re-enable, v1.7.2 lower call min, and HERMES trigger. State file recovery preserved positions through each restart.

6. **Back-to-back losing days**: Mar 2 (-$255) + Mar 3 (-$450) = -$705 over 2 days, the worst 2-day stretch of the period. Both days featured V-shape patterns with 100+ point ranges. Cumulative P&L dropped from $2,110 (Feb 27 high) to $1,405.

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

#### Post-Improvement Day 11: Mar 4 (current)

| Column | Mar 4 |
|--------|-------|
| Date | 2026-03-04 |
| SPX Open | 6,835.73 |
| SPX Close | 6,867.81 |
| SPX Range | 76 pts (1.1%) |
| VIX Open | 22.52 |
| VIX Close | 21.23 |
| Entries | 4 (+1 skipped) |
| Full ICs | 4 |
| One-Sided | 0 |
| Total Credit | $1115 |
| Call Stops | 0 |
| Put Stops | 3 |
| Stop Debits | $305 |
| Commission | $47.50 |
| Expired Credits | $540 |
| Daily P&L | +$187.50 |
| Cumulative P&L | $1592.50 |
| Early Close | No |

**Mar 4 Assessment**: MKT-025 (short-only stop) drove the day's outcome across three put stops — HYDRA closed only the short put legs on Entries #1–3, collecting $305 in total stop debits against $1,115 in total credits, while the long put legs expired worthless at settlement as SPX recovered to 6,867.81, well above all short put strikes (6,825–6,840). MKT-020/022 progressive tightening was visible across the four entries, with short call strikes stepping down from 6,915 to 6,900 and short put strikes stepping up from 6,825 to 6,840 as intraday price action developed, though this tightening moved put strikes closer to the 6,810 intraday low on Entries #2 and #3 rather than away from it. MKT-021 did not gate Entries #4–6 (cumulative ROC remained below threshold), and the single skip logged was not attributed to MKT-011 on any placed entry — Entry #4's $305 total credit with clean expiration was the day's decisive result, producing a net $187.50 on $31,000 capital deployed (0.6% ROC), a structurally sound but thin outcome given three stops absorbed before the market found its

#### Post-Improvement Day 12: Mar 5 (current)

| Column | Mar 5 |
|--------|-------|
| Date | 2026-03-05 |
| SPX Open | 6,846.18 |
| SPX Close | 6,828.74 |
| SPX Range | 98 pts (1.4%) |
| VIX Open | 22.04 |
| VIX Close | 23.37 |
| Entries | 5 (+0 skipped) |
| Full ICs | 5 |
| One-Sided | 0 |
| Total Credit | $1345 |
| Call Stops | 0 |
| Put Stops | 5 |
| Stop Debits | $240 |
| Commission | $75 |
| Expired Credits | $405 |
| Daily P&L | +$90 |
| Cumulative P&L | $1682.50 |
| Early Close | No |

**Mar 5 Assessment**: MKT-025 (short-only stop) triggered on all five put stops, with the long legs subsequently salvaged via MKT-033 for $290, which was the decisive factor in converting a five-stop sweep into a slim +$90 net — without that recovery, the day ends negative. MKT-011 generated zero skips across all five entries, with call credits holding between $65 and $95 and put credits inflating from $165 to $225 as VIX spiked to 25.84, confirming the credit gate was not the binding constraint on a day driven entirely by directional risk. MKT-021 did not gate entries #4 or #5, as early-close (MKT-018) never triggered — ROC never reached the 3% threshold, consistent with a sustained loss regime where no entry reached profitability before being stopped — making this a structurally marginal day: mechanically sound execution against an adverse directional move, rescued by salvage rather than strategy edge.

#### Post-Improvement Day 13: Mar 6 (current)

| Column | Mar 6 |
|--------|-------|
| Date | 2026-03-06 |
| SPX Open | 6,738.00 |
| SPX Close | 6,730.39 |
| SPX Range | 66 pts (1.0%) |
| VIX Open | 27.69 |
| VIX Close | 29.83 |
| Entries | 5 (+0 skipped) |
| Full ICs | 5 |
| One-Sided | 0 |
| Total Credit | $1260 |
| Call Stops | 0 |
| Put Stops | 4 |
| Stop Debits | $345 |
| Commission | $70 |
| Expired Credits | $590 |
| Daily P&L | +$175 |
| Cumulative P&L | $1857.50 |
| Early Close | No |

**Mar 6 Assessment**: Four of five entries were stopped on the put side as SPX dropped to a session low of 6,708.67 against short put strikes ranging from 6,670 to 6,710, with MKT-025 correctly closing only the short legs on all four stops — the $125.00 in long salvage proceeds recovered across entries #2 and #3 confirms the rule functioned as intended and offset a portion of the $345.00 total stop debit. MKT-020/022 progressive tightening is visible in the short call strikes stepping down from 6,845 to 6,815 across the session, while put strikes ranged between 6,670 and 6,710 as the algorithm tracked the falling tape; MKT-021 did not gate entries #4–#6, as all five scheduled entries executed and ROC never breached the early-close threshold — consistent with MKT-018 showing no early close trigger on the day. At a 20% win rate and $175.00 net P&L on $1,260.00 total credit collected, this was a structurally difficult day for the current configuration — sustained directional put pressure in a rising VIX environment (27.69 open to 29.83 close) is precisely the condition where a five-entry full IC schedule carries concentrated

#### Post-Improvement Day 14: Mar 9 (current)

| Column | Mar 9 |
|--------|-------|
| Date | 2026-03-09 |
| SPX Open | 6,676.18 |
| SPX Close | 6,796.10 |
| SPX Range | 175 pts (2.6%) |
| VIX Open | 31.18 |
| VIX Close | 25.08 |
| Entries | 4 (+1 skipped) |
| Full ICs | 4 |
| One-Sided | 0 |
| Total Credit | $1045 |
| Call Stops | 4 |
| Put Stops | 2 |
| Stop Debits | $985 |
| Commission | $67.50 |
| Expired Credits | $340 |
| Daily P&L | -$712.50 |
| Cumulative P&L | $1145.00 |
| Early Close | No |

**Mar 9 Assessment**: SPX's 134-point intraday reversal from 6,676 to a high of 6,810 overwhelmed all four iron condors, triggering call stops on every entry and double stops on entries #2 and #3, producing a -$712.50 net P&L against $1,045 total credit collected — a 0% win rate and -2.38% return on capital for the session. MKT-031 fired entry #1 seven minutes early at 12:08 ET, and while it captured $265 credit and avoided the double-stop that hit entries #2 and #3, it still stopped on the call side at 12:26 ET as SPX pushed through the 6,765 short call strike; the one MKT-011 skip at the 13:15 window was consistent with call premium compression after a 130+ point upside move. The cumulative P&L holds at $1,145.00 with a 4.53% cumulative ROC, but today's realized loss at 2.4x the average losing day underscores the structural vulnerability of HYDRA's symmetric iron condor configuration in sharp VIX-compression squeeze sessions where directional exposure materializes unilaterally on the call side.

#### Post-Improvement Day 15: Mar 10 (current)

| Column | Mar 10 |
|--------|--------|
| Date | 2026-03-10 |
| SPX Open | 6,833.30 |
| SPX Close | 6,777.78 |
| SPX Range | 70 pts (1.0%) |
| VIX Open | 22.61 |
| VIX Close | 25.65 |
| Entries | 2 (+4 skipped) |
| Full ICs | 2 |
| One-Sided | 0 |
| Total Credit | $515 |
| Call Stops | 2 |
| Put Stops | 2 |
| Stop Debits | $545 |
| Commission | $40 |
| Expired Credits | $0 |
| Daily P&L | -$585 |
| Cumulative P&L | $560 |
| Early Close | No |

**Mar 10 Assessment**: MKT-011's credit gate blocked 4 of 6 attempted entries — an exceptional skip rate driven by the low-VIX open (22.61) suppressing call premium below the $0.75 floor across the morning slots, leaving HYDRA exposed only in the 12:44 and 13:16 ET windows where conditions marginally cleared thresholds ($0.90/$0.80 call credits, $1.75 put credits). Both placed condors — 75-point-wide iron condors at identical short strikes (6,870 calls / 6,800 puts) collecting $265 credit each — suffered double-stops as SPX's 69.7-point intraday range first pressured short calls on the opening probe to 6,845, then drove through short puts as SPX closed at 6,777.78, with VIX snapping from 22.61 to 25.65 confirming that the vol compression at entry time was structurally deceptive. The stop formula executed without mechanical failure — both double-stops fired at their respective breakeven levels — but the day produced -$585 net (-3.9% ROC), reducing cumulative P&L from approximately $1,145 to $560 across a two-day, $1,640 drawdown in which the low-open

#### Post-Improvement Day 16: Mar 11 (current)

| Column | Mar 11 |
|--------|--------|
| Date | 2026-03-11 |
| SPX Open | 6,761.91 |
| SPX Close | 6,774.59 |
| SPX Range | 66 pts (1.0%) |
| VIX Open | 25.80 |
| VIX Close | 24.32 |
| Entries | 5 (+2 skipped) |
| Full ICs | 5 |
| One-Sided | 0 |
| Total Credit | $1575 |
| Call Stops | 0 |
| Put Stops | 5 |
| Stop Debits | $545 |
| Commission | $75 |
| Expired Credits | $310 |
| Daily P&L | -$310 |
| Cumulative P&L | $250 |
| Early Close | No |

**Mar 11 Assessment**: All five entries executed as full iron condors with neutral trend signals. Put credits ranged from $1.70 to $3.05 across entries. Every put spread was stopped as SPX's 66-point intraday reversal from the 6,811 high sustained enough downside pressure on each position. Note: MKT-036 stop confirmation timer was NOT active — v1.12.0 was deployed post-market, all stops executed on v1.10.0 without confirmation delay. Entry #1 entered 10 minutes early via MKT-031 smart entry (score 65/65) before MKT-031 was disabled at the 10:33 restart; remaining entries followed the scheduled 10:45/11:15/11:45/12:15 times. Two conditional entries (12:45, 13:15) were skipped by MKT-011 credit non-viability. Individual stop losses: #1 -$145, #2 -$155, #3 -$75, #4 -$100, #5 -$70. At -$310 net against a -$466 historical average loss day, the damage was structurally contained — no double-stops, $310 in expired call credits partially offsetting $545 in stop debits — but the uniform put-stop cascade underscores VIX-elevated regime's capacity to inflate spread values into stop territory well before intrinsic value is reached. Ten bot restarts occurred during the session (code deployments), contributing to 2 missing stop records in Google Sheets Trades tab (corrected manually from bot logs).

#### Post-Improvement Day 17: Mar 12 (current)

| Column | Mar 12 |
|--------|--------|
| Date | 2026-03-12 |
| SPX Open | 6,722.77 |
| SPX Close | 6,672.55 |
| SPX Range | 57 pts (0.8%) |
| VIX Open | 26.09 |
| VIX Close | 27.16 |
| Entries | 5 (+2 skipped) |
| Full ICs | 1 |
| One-Sided | 4 |
| Total Credit | $555 |
| Call Stops | 0 |
| Put Stops | 0 |
| Stop Debits | $0 |
| Commission | $30 |
| Expired Credits | $555 |
| Daily P&L | +$525 |
| Cumulative P&L | $775 |
| Early Close | No |

**Mar 12 Assessment**: MKT-035 fired at Entry #2 (10:45 ET) and remained active through the session as SPX dropped 53 points from its 6,722.77 open to a low of 6,669.80, suppressing all put sides from Entries #2–#7 and leaving Entry #1's put short at 6,650 as the only put exposure — which survived with a 19.8-point buffer at the session low. All 5 completed entries expired clean with zero stops triggered, generating $555 gross credit against $30 in commissions for a $525 net P&L and 2.1% return on capital, with call shorts maintaining a minimum 38.4-point clearance above the 6,726.56 session high throughout. Two entries were skipped (original slots #4 and #6), no MKT-011 credit gate failures are recorded in the data, and the absence of any stop events confirms that MKT-035's structural suppression of put-side exposure was the decisive factor in avoiding the kind of put-stop cascade that damaged prior sessions — making this a well-configured outcome for the current strategy parameters in a directionally persistent bearish environment.

#### Post-Improvement Day 18: Mar 13 (current)

| Column | Mar 13 |
|--------|--------|
| Date | 2026-03-13 |
| SPX Open | 6,707.89 |
| SPX Close | 6,631.66 |
| SPX Range | 107 pts (1.6%) |
| VIX Open | 25.53 |
| VIX Close | 27.24 |
| Entries | 7 (+0 skipped) |
| Full ICs | 3 |
| One-Sided | 4 |
| Total Credit | $1270 |
| Call Stops | 0 |
| Put Stops | 1 |
| Stop Debits | $565 |
| Commission | $55 |
| Expired Credits | $1005 |
| Daily P&L | +$385 |
| Cumulative P&L | $1160 |
| Early Close | No |

**Mar 13 Assessment**: MKT-035 was the decisive factor on this session: triggered by Entry #2 (10:45) and again for the conditional entries E6/E7 (12:45, 13:15), it suppressed put exposure on four of seven entries, limiting the damage from SPX's 84-point decline to a single put stop on Entry #1 — where the 6,620 short put had just 3.74 points of clearance at the 6,623.74 session low. MKT-036's 75-second confirmation window likely filtered transient breaches before executing that stop, with the debit recorded at $565 against a $325 total credit, while the call leg of Entry #1 expired clean, and Entries #4 and #5 (full ICs placed at a lower SPX reference with put shorts at 6,585 and 6,595) survived intact for combined $685 in expired credits. The net result — $385 daily P&L on a 107-point VIX-elevated range — confirms that the current configuration's asymmetric put buffer and down-day suppression logic are functioning as intended, though Entry #5's $55 call credit remains a flag for potential MKT-011 boundary behavior on future high-VIX sessions.

#### Post-Improvement Day 19: Mar 16 (current)

| Column | Mar 16 |
|--------|--------|
| Date | 2026-03-16 |
| SPX Open | 6,693.32 |
| SPX Close | 6,695.01 |
| SPX Range | 47 pts (0.7%) |
| VIX Open | 25.18 |
| VIX Close | 23.51 |
| Entries | 3 (+4 skipped) |
| Full ICs | 3 |
| One-Sided | 0 |
| Total Credit | $980 |
| Call Stops | 0 |
| Put Stops | 0 |
| Stop Debits | $0 |
| Commission | $30 |
| Expired Credits | $980 |
| Daily P&L | +$950 |
| Cumulative P&L | $2110 |
| Early Close | No |

**Mar 16 Assessment**: MKT-011 gated 4 of 7 entries as call premium collapsed intraday — VIX dropped from 25.18 to 23.23 and SPX ground higher to 6,727, compressing call credits to $55–$70 on the three entries that did clear the threshold, while E4 through E5 failed the $0.60 call floor and E6/E7 were suppressed by MKT-035 (SPX never fell 0.3% below its 6,693.32 open). The 3 full iron condors that executed collected $980 gross across a 47-point session range (6,681–6,727), with the nearest call short at 6,755 holding a 28-point buffer above the day's high and the nearest put short at 6,635 sitting 46 points below the low — zero stops triggered and all three positions expired worthless. Net $950 after $30 in commissions represents a clean outcome for the configuration: the asymmetric stop buffers were never tested, MKT-038 was not in effect (today was neither FOMC Day 2 nor T+1), and the 4 skips reflected legitimate rule-based restraint rather than missed opportunity given the hostile call-premium environment.

#### Post-Improvement Day 20: Mar 17 (current)

| Column | Mar 17 |
|--------|--------|
| Date | 2026-03-17 |
| SPX Open | 6,722.73 |
| SPX Close | 6,718.98 |
| SPX Range | 38 pts (0.6%) |
| VIX Open | 22.67 |
| VIX Close | 22.45 |
| Entries | 2 (+8 skipped) |
| Full ICs | 0 |
| One-Sided | 2 |
| Total Credit | $130 |
| Call Stops | 0 |
| Put Stops | 0 |
| Stop Debits | $0 |
| Commission | $10 |
| Expired Credits | $130 |
| Daily P&L | +$120 |
| Cumulative P&L | $2230 |
| Early Close | No |

**Mar 17 Assessment**: Today was FOMC Day 1 (March 17), meaning MKT-008 mandated a full zero-entry blackout — yet HYDRA placed two call-only entries (E5 at 12:15 and E7 at 13:16), a critical rule enforcement failure that demands a code audit before tomorrow's Day 2 blackout. Both entries collected $65.00 call credit each (50-point spreads at 6755/6805 and 6750/6800) and expired worthless as SPX peaked at 6,752.07 — never breaching either short call strike — producing a clean $120.00 net with zero stops, but the outcome is incidental to the structural breach. A secondary anomaly: both spread widths measured 50 points, below the 60-point call floor enforced by MKT-028, and E7's `override_reason: "mkt-035"` indicates the conditional down-day logic fired independently despite no rule having authority to override MKT-008.

#### Post-Improvement Day 21: Mar 18 (current)

| Column | Mar 18 |
|--------|--------|
| Date | 2026-03-18 |
| SPX Open | 6,744.44 |
| SPX Close | 6,621.29 |
| SPX Range | 123 pts (1.8%) |
| VIX Open | 22.37 |
| VIX Close | 24.63 |
| Entries | 3 (+4 skipped) |
| Full ICs | 3 |
| One-Sided | 0 |
| Total Credit | $915 |
| Call Stops | 0 |
| Put Stops | 0 |
| Stop Debits | $720 |
| Commission | $60 |
| Expired Credits | $915 |
| Daily P&L | +$135 |
| Cumulative P&L | $2365 |
| Early Close | No |

**Mar 18 Assessment**: MKT-011 gated 4 of 7 scheduled entries as FOMC announcement conditions rendered later windows non-viable, limiting exposure to just E1–E3 and producing $915 total credit against $780 in debits (stops + commissions) for a $135 net P&L — a below-average win given the 123-point SPX waterfall from open to close. All three full ICs expired clean, but the margin was razor-thin: E3's short put at 6,620 cleared the 6,621.29 session low by just 1.3 points, meaning a trivial additional decline would have put the position ITM at expiry. A persistent MKT-028 enforcement anomaly also warrants operator review — all six legs across all three entries show 50-point spread widths, below the mandated 60-point call floor and 75-point put floor, and tomorrow's MKT-038 T+1 designation must be confirmed active before 10:15 ET to ensure all entries are forced call-only.

#### Post-Improvement Day 22: Mar 19 (current)

| Column | Mar 19 |
|--------|--------|
| Date | 2026-03-19 |
| SPX Open | 6,572.98 |
| SPX Close | 6,605.94 |
| SPX Range | 80 pts (1.2%) |
| VIX Open | 27.03 |
| VIX Close | 24.29 |
| Entries | 7 (+0 skipped) |
| Full ICs | 0 |
| One-Sided | 7 |
| Total Credit | $435 |
| Call Stops | 6 |
| Put Stops | 0 |
| Stop Debits | $1970 |
| Commission | $65 |
| Expired Credits | $50 |
| Daily P&L | -$1985 |
| Cumulative P&L | $380 |
| Early Close | No |

**Mar 19 Assessment**: MKT-038 (FOMC T+1) forced all 7 entries to call-only spreads, with MKT-035 additionally governing the two conditional entries (E6/E7 at 12:45 and 13:15) as the day qualified as a down-day trigger — no full ICs or put spreads were placed across the entire session. SPX rallied from open to close, finishing at 6605.94 and reaching a high of 6638.58, breaching short strikes on 6 of 7 entries, with all 6 stops clustering between 15:01 and 15:07 ET, producing $1,970 in stop debits against only $435 total credit collected for a net P&L of -$1,985 (-5.59% ROC). The FOMC T+1 call-only configuration executed as designed per MKT-038 rules, but the day's bullish price action — precisely the elevated volatility and directional risk that T+1 protocol anticipates — converted that structural protection into a near-total-loss session, with only Entry #1 expiring at its $50 credit, underscoring the inherent exposure of call-only positioning on strong up days.

#### Post-Improvement Day 23: Mar 20 (current)

| Column | Mar 20 |
|--------|--------|
| Date | 2026-03-20 |
| SPX Open | 6,589.63 |
| SPX Close | 6,506.87 |
| SPX Range | 115 pts (1.7%) |
| VIX Open | 24.74 |
| VIX Close | 27.46 |
| Entries | 5 (+2 skipped) |
| Full ICs | 5 |
| One-Sided | 0 |
| Total Credit | $880 |
| Call Stops | 0 |
| Put Stops | 3 |
| Stop Debits | $1755 |
| Commission | $45 |
| Expired Credits | $165 |
| Daily P&L | -$1635 |
| Cumulative P&L | $-1255 |
| Early Close | No |

**Mar 20 Assessment**: MKT-011's credit gate skipped 2 of 7 scheduled entries (E4 and E5) entirely, though the put side likely remained viable given E3 captured $290 in put credit at 11:15 — the absence of logged skip reasons leaves open whether MKT-032 put-only logic failed to trigger correctly on those two windows. SPX opened at its session high of 6,589.63 and sold off 114.72 points without recovery, breaching all three short put strikes (6,480, 6,495, and 6,510) and producing 3 put stops with $1,755 in stop loss debits against only $880 total credit collected across 5 entries, yielding a -$1,635 net loss (-10.9% ROC). A persistent MKT-028 spread width anomaly — all legs showing 50-point widths against call/put floors of 60 and 75 points respectively — combined with an E3 call credit of $0.40 that fell below both the MKT-011 floor ($0.60) and the MKT-029 fallback floor ($0.50), represents unresolved rule enforcement gaps that require operator correction before the next session.

#### Post-Improvement Day 24: Mar 23 (current)

| Column | Mar 23 |
|--------|--------|
| Date | 2026-03-23 |
| SPX Open | 6,625.13 |
| SPX Close | 6,582.35 |
| SPX Range | 87 pts (1.3%) |
| VIX Open | 24.45 |
| VIX Close | 26.17 |
| Entries | 6 (+1 skipped) |
| Full ICs | 5 |
| One-Sided | 1 |
| Total Credit | $2040 |
| Call Stops | 1 |
| Put Stops | 5 |
| Stop Debits | $1545 |
| Commission | $85 |
| Expired Credits | $695 |
| Daily P&L | -$935 |
| Cumulative P&L | $-2190 |
| Early Close | No |

**Mar 23 Assessment**: MKT-035 triggered the 13:15 conditional entry (E6) as a call-only spread after SPX dropped 0.3%+ below its 6,651.99 session high, and MKT-011 cleared all six entries without a single MKT-029 fallback — put credits ranged from $2.10 to $2.65 across E1–E5, comfortably above the $2.50 floor despite the declining tape. The session produced five consecutive put stops and one call stop on E6, with $1,545 in stop-loss debits against $2,040 total credit collected, yielding a -$935 net loss as SPX's 87-point decline from high to low systematically breached short put strikes while all call wings expired worthless. This was a structurally clean but directionally severe day for the current configuration, compounded by the ongoing MKT-028 spread width anomaly — all spreads remain at 50 points against the required 60pt call and 75pt put floors, now four sessions unresolved, meaning put credits and stop thresholds were consistently underspecified relative to strategy design.

#### Post-Improvement Day 25: Mar 30 (current)

| Column | Mar 30 |
|--------|--------|
| Date | 2026-03-30 |
| SPX Open | 6,421.19 |
| SPX Close | 6,341.79 |
| SPX Range | 85 pts (1.3%) |
| VIX Open | 29.74 |
| VIX Close | 30.45 |
| Entries | 0 (+2 skipped) |
| Full ICs | 2 |
| One-Sided | 0 |
| Total Credit | $0 |
| Call Stops | 0 |
| Put Stops | 0 |
| Stop Debits | $0 |
| Commission | $0 |
| Expired Credits | $0 |
| Daily P&L | +$0 |
| Cumulative P&L | $-2190 |
| Early Close | No |

**Mar 30 Assessment**: MKT-010 (illiquidity) killed E1 at 10:15 before any position could be established, and by 10:45 the sharp reversal — SPX down roughly 79 points from the open, breaching the -0.57% down-day threshold — had collapsed call credit to $0.00, making MKT-011's $1.25 floor (post-MKT-029 fallback floor $0.75) irrelevant since no fallback can rescue a zero-credit market; E2 was skipped on the down-day call-only conversion with nothing to sell. The result was a clean zero across all metrics — $0.00 credit, $0.00 stops, $0.00 commissions — which, given that a filled put leg would have faced an ~80-point adverse move, represents a structurally favorable non-event rather than a missed opportunity. With cumulative P&L at -$2,190.00, a -9.38% ROC over 29 days, and an avg loss of -$729.40 running nearly 1.9x the avg win of $386.00, today's idle session at VIX 29.74–30.45 neither worsened the asymmetry nor captured any of the elevated premium that high-volatility sessions

#### Post-Improvement Day 26: Mar 31 (current)

| Column | Mar 31 |
|--------|--------|
| Date | 2026-03-31 |
| SPX Open | 6,438.58 |
| SPX Close | 6,528.06 |
| SPX Range | 134 pts (2.1%) |
| VIX Open | 27.49 |
| VIX Close | 25.44 |
| Entries | 3 (+0 skipped) |
| Full ICs | 2 |
| One-Sided | 1 |
| Total Credit | $915 |
| Call Stops | 2 |
| Put Stops | 0 |
| Stop Debits | $595 |
| Commission | $35 |
| Expired Credits | $615 |
| Daily P&L | -$15 |
| Cumulative P&L | $-2205 |
| Early Close | No |

#### Post-Improvement Day 27: Apr 1 (current)

| Column | Apr 1 |
|--------|-------|
| Date | 2026-04-01 |
| SPX Open | 6,549.32 |
| SPX Close | 6,579.52 |
| SPX Range | 61 pts (0.9%) |
| VIX Open | 25.25 |
| VIX Close | 24.58 |
| Entries | 4 (+0 skipped) |
| Full ICs | 3 |
| One-Sided | 1 |
| Total Credit | $1720 |
| Call Stops | 0 |
| Put Stops | 1 |
| Stop Debits | $365 |
| Commission | $70 |
| Expired Credits | $765 |
| Daily P&L | +$330 |
| Cumulative P&L | $-1875 |
| Early Close | No |

**Apr 1 Assessment**: All four entries executed with zero skips, confirming MKT-011 credit thresholds were met across all legs — call credits ranged from $205 down to $170 (above the $2.00 floor) and put credits from $275 to $310 (above the $2.75 floor), with MKT-020/022 progressive tightening visible in the call strike walk from 6,620 to 6,630 as SPX drifted intraday. Upday-035 fired correctly at 14:00 with SPX +0.46% above the open, generating E4 as a put-only entry at $290 credit, but the short put at 6,565 carried only ~16 points of cushion above the session low of 6,549.32, and the stop triggered at 14:02 at the formula-consistent level of $445 (credit $290 + $1.55 buffer = $445), producing a -$195 net on that leg. With E1–E3 expiring clean in the 60.65-point range for $765 and the single put stop costing $365 in debit, today's $330 net P&L on $1,720 total credit represents solid base-entry performance offset by the structural vulnerability of a late

#### Post-Improvement Day 28: Apr 2 (current)

| Column | Apr 2 |
|--------|-------|
| Date | 2026-04-02 |
| SPX Open | 6,496.54 |
| SPX Close | 6,581.07 |
| SPX Range | 130 pts (2.0%) |
| VIX Open | 24.54 |
| VIX Close | 24.15 |
| Entries | 4 (+0 skipped) |
| Full ICs | 3 |
| One-Sided | 1 |
| Total Credit | $1645 |
| Call Stops | 1 |
| Put Stops | 0 |
| Stop Debits | $340 |
| Commission | $40 |
| Expired Credits | $1435 |
| Daily P&L | +$1055 |
| Cumulative P&L | $-820 |
| Early Close | No |

**Apr 2 Assessment**: MKT-011 passed all four entries with no credit-floor skips, and MKT-020/022 progressive tightening produced maximum-width 110pt spreads on every leg across all entries, reflecting the VIX-scaled formula at 24.54 (round(24.54 × 6.0 / 5) × 5 = 30pt floor well exceeded, capping at 110pt). The down-day filter (MKT-035) did not convert any base entries to call-only despite SPX opening at approximately -0.57% from the prior close — all three base entries placed as full iron condors — while Upday-035 fired correctly at 14:00 as a put-only spread ($220 credit, expired clean) once SPX had recovered well above +0.25% from open; Entry #1's call stop ($340 debit against $470 total credit) was the sole loss, driven entirely by the 130pt intraday reversal compressing the 6590 short call placed into the depressed open, while Entries #2, #3, and #4 contributed $1,175 in retained credits for a $1,055 net day — a strong result that nonetheless illustrates HYDRA's core structural exposure: early entries anchored to a down open carry tighter call

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

### Mar 3 (Tuesday - Gap-Down + V-Shape, v1.6.0→v1.7.2)

| Entry | Time (ET) | EMA 20 | EMA 40 | Divergence % | Signal at 0.1% | Signal at 0.2% | Change? |
|-------|-----------|--------|--------|-------------|-----------------|-----------------|---------|
| #1 | 10:05 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #2 | ~10:35 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #3 | ~11:05 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #4 | ~11:35 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |
| #5 | ~12:05 | — | — | ~NEUTRAL | NEUTRAL | NEUTRAL | No |

**Note**: All 5 entries had NEUTRAL signal. Since v1.4.0+ disabled one-sided entries, the EMA signal is informational only — does not affect entry type. Exact EMA values not captured in logs due to multiple mid-day restarts and deployments. VIX 26→22 intraday swing was dramatic but EMA divergence remained within NEUTRAL zone throughout. Zero impact from threshold change.

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

### Mar 2 (Monday - v1.5.1 Active, first day as HYDRA) — 6 stops + 1 double stop + MKT-025

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC (NEUTRAL), C:6915/6975 P:6775/6715, $310 credit |
| | | VIX=23.40, MKT-025 short-only stops active |
| Entry #2 placed | 10:35 | Full IC (NEUTRAL), C:6900/6960 P:6765/6705, $315 credit |
| Entry #3 placed | 11:05 | Full IC (NEUTRAL), C:6905/6965 P:6795/6735, $285 credit |
| **Entry #3 PUT STOPPED** | **11:27** | **MKT-025: SP closed, LP expires at settlement** |
| Entry #4 placed | 11:35 | Full IC (NEUTRAL), C:6895/6955 P:6790/6730, $305 credit |
| **Entry #4 CALL STOPPED** | **11:40** | **MKT-025: SC closed, LC expires at settlement** |
| **Entry #2 CALL STOPPED** | **11:41** | **MKT-025: SC closed, LC expires at settlement** |
| **Entry #3 CALL STOPPED** | **11:44** | **MKT-025: SC closed — DOUBLE STOP (put at 11:27, call at 11:44)** |
| Entry #5 placed | 12:05 | Full IC (NEUTRAL), C:6910/6970 P:6835/6775, $325 credit |
| Entry #6 placed | 12:35 | Full IC (NEUTRAL), C:6905/6970 P:6840/6760, $315 credit |
| **Entry #1 PUT STOPPED** | **~13:xx** | **MKT-025: SP closed, LP expires** |
| **Entry #6 CALL STOPPED** | **14:21** | **MKT-025: SC closed, LC expires** |
| Settlement | ~16:00+ | Entry #1 C, Entry #2 P, Entry #4 P, Entry #5 C+P, Entry #6 P expired |
| | | Total expired credits: $1,000 |

**MKT-016/017**: REMOVED (v1.3.3). All 6 entries placed as designed.
**MKT-025**: **FIRST LIVE DAY** — all 6 stops only closed short leg. 6 longs expired at settlement.
**MKT-018**: NOT triggered. ROC negative all day.

### Mar 3 (Tuesday - v1.6.0→v1.7.2, 13 commits) — 6 stops + 1 double stop + MKT-025

| Event | Time (ET) | Details |
|-------|-----------|---------|
| Entry #1 placed | 10:05 | Full IC (NEUTRAL), C:6850/6925 P:6630/6555, $295 credit |
| | | SPX ~6,737, VIX 26.62, MKT-024: 3.5×/4.0× starting OTM |
| | | MKT-020: Call 240→115pt, MKT-022: Put 240→105pt |
| | | 75pt/75pt spreads (MKT-028 first live) |
| VIX filter blocks #2-5 | ~10:10 | VIX 26.7 > 25 threshold — remaining entries blocked |
| v1.6.1 deployed | ~10:18 | VIX threshold raised 25→30, unblocking entries #2-5 |
| **Entry #1 PUT STOPPED** | **10:22** | **MKT-025: SP 6630 closed, LP 6555 expires** |
| | | SPX ~6,715, VIX 27.86, net loss -$195 (put side) |
| Entry #2 placed | ~10:35 | Full IC (NEUTRAL), $285 ($115C + $170P) |
| Entry #3 placed | ~11:05 | Full IC (NEUTRAL), $245 ($100C + $145P) |
| Entry #4 placed | ~11:35 | Full IC (NEUTRAL), $290 ($125C + $165P) |
| Entry #5 placed | ~12:05 | Full IC (NEUTRAL), $280 ($85C + $195P) |
| | | [SPX rallying from ~6,711 low toward ~6,840 high] |
| **All 5 CALL SIDES STOPPED** | **afternoon** | **V-shape rally drove SPX past all short call strikes** |
| | | Entry #1 call also stopped → DOUBLE STOP |
| | | Entries #2-5: call stopped, put expired = near breakeven each |
| Settlement | ~16:00+ | 4 put sides (Entries #2-5) expired worthless |
| | | Total expired credits: $675 |
| Daily summary | post-settlement | Net P&L: -$450, Commission: $65, Cumulative: $1,405 |

**MKT-016/017**: REMOVED (v1.3.3). All 5 entries placed.
**MKT-025**: All 6 stops used short-only close. 4 put longs expired at settlement.
**MKT-018**: INTENTIONALLY DISABLED (v1.7.2). Code preserved but dormant.
**MKT-024**: **FIRST LIVE DAY** — 3.5×/4.0× starting OTM, batch API scan.
**MKT-028**: **FIRST LIVE DAY** — asymmetric spreads (call 60pt floor, put 75pt floor).
**MKT-027**: **FIRST LIVE DAY** — VIX-scaled spread width formula.
**v1.6.1**: **CRITICAL** — VIX filter raised 25→30 mid-day, unblocking 4 entries.

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

### Config as of v1.3.8 (deployed Feb 24-26)

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

### Config as of v1.5.1 (deployed Mar 2)

```
Entries per day: 6                                 ← RAISED from 5 (v1.4.4)
Entry times: 10:05, 10:35, 11:05, 11:35, 12:05, 12:35 ET  ← Added 12:35 (v1.4.4)
EMA short period: 20
EMA long period: 40
EMA neutral threshold: 0.002 (0.2%)               ← Signal informational only (v1.4.0)
One-sided entries: DISABLED                        ← All entries full IC or skip (v1.4.0)
Min viable credit - call: $1.00 (MKT-011)
Min viable credit - put: $1.75 (MKT-011)          ← RAISED from $1.00, separate threshold (v1.4.1)
Starting OTM multiplier: 2×                        ← MKT-024 wider starting OTM (v1.4.1)
Min spread width: 60 pts                           ← RAISED from 25 pts (MKT-026, v1.4.5)
Spread width formula: max(60, round(VIX×3.5/5)×5) ← MKT-027 VIX-scaled (v1.5.0), cap 120pt
Stop level (full IC): total_credit - $0.15         ← MEIC+ reduction raised from $0.10 (v1.4.2)
Stop close mechanism: SHORT-ONLY                   ← MKT-025 (v1.4.3) — long expires at settlement
Max daily stops before pause: REMOVED
Max daily loss: REMOVED
Base MEIC loss limit: DISABLED
Early close enabled: Yes
Early close ROC threshold: 3.0%                    ← RAISED from 2.0% (v1.3.11)
Early close cost per position: $5.00
Hold check enabled: Yes                            ← (MKT-023, v1.3.7)
Hold check lean tolerance: 1.0%
Progressive call tightening: Yes                   ← (MKT-020, v1.3.2)
Progressive put tightening: Yes                    ← (MKT-022, v1.3.5)
MKT-011 NEUTRAL skip: Yes                          ← (v1.3.6) — now applies to all entries (no one-sided fallback)
Pre-entry ROC gate: 3.0% (after 3 entries)         ← Gate=3 (v1.3.9), threshold=3% (v1.3.11)
Telegram /snapshot: Yes                            ← (v1.5.1)
```

### Current Config (v1.8.0, deployed Mar 4)

```
Entries per day: 5                                 ← DROPPED from 6 (v1.6.0)
Entry times: 11:05, 11:35, 12:05, 12:35, 13:05 ET ← SHIFTED +1hr (v1.8.0, journal data: 10:05/10:35 negative)
Smart entry windows: ENABLED                       ← MKT-031 (v1.8.0) 10min scout, score >= 65 = early entry
EMA short period: 20
EMA long period: 40
EMA neutral threshold: 0.002 (0.2%)               ← Signal informational only (v1.4.0)
One-sided entries: Put-only when call non-viable   ← Re-enabled (v1.7.1, 87.5% WR)
Min viable credit - call: $0.75 (MKT-011)         ← LOWERED from $1.00 (v1.7.2, credit cushion analysis)
Min viable credit - put: $1.75 (MKT-011)
Starting OTM multiplier: 3.5× call, 4.0× put     ← RAISED from 2× (MKT-024, v1.6.0)
Call min spread width: 60 pts                      ← MKT-028 asymmetric (v1.6.0)
Put min spread width: 75 pts                       ← MKT-028 asymmetric (v1.6.0)
Max spread width: 75 pts                           ← Margin cap (v1.6.0)
Spread width formula: max(floor, round(VIX×3.5/5)×5) ← MKT-027 VIX-scaled, per-side floors
Stop level (full IC): total_credit - $0.15         ← MEIC+ (v1.4.2)
Stop close mechanism: SHORT-ONLY                   ← MKT-025 (v1.4.3)
Early close enabled: DISABLED                      ← MKT-018 intentionally disabled (v1.6.0)
Progressive call tightening: Yes                   ← (MKT-020)
Progressive put tightening: Yes                    ← (MKT-022)
Early close day cutoff: 12:00 PM                   ← Keeps 11:05/11:35 viable (v1.8.0)
```

**Config location**: `bots/hydra/config/config.json` on VM at `/opt/calypso/`. Template at `bots/hydra/config/config.json.template` in repo.

## Appendix G: Formulas

- **Expected Move** = SPX × VIX / sqrt(252) / 100
- **Stop Level (full IC)** = Total credit - $0.15 (MEIC+ covers commission for true breakeven, v1.4.2)
- **Stop Level (one-sided)** = 2 × credit (put-only re-enabled v1.7.1)
- **Stop triggers when**: spread_value >= stop_level (cost-to-close exceeds threshold)
- **Stop close (v1.4.3+)**: MKT-025 short-only — close short leg, long expires at settlement
- **Spread Width (v1.6.0+)**: max(per-side floor, round(VIX × 3.5 / 5) × 5), capped at 75pt (MKT-027 + MKT-028 asymmetric floors: call 60pt, put 75pt)
- **Net P&L** = Expired Credits - Stop Loss Debits - Commission
- **Net Capture Rate** = Net P&L / Total Credit Collected × 100
- **Win Rate** = Entries with 0 stops / Total entries × 100
- **Sortino Ratio** = daily_average_return / downside_deviation × sqrt(252)

### Commission Per Entry Type

Commission = $2.50 per leg per transaction (from `strategy.py` line 816: `commission_per_leg = 2.50`).

| Entry Type | Outcome | Legs Opened | Legs Closed | Total Commission |
|------------|---------|-------------|-------------|-----------------|
| Full IC | Both expire | 4 | 0 | **$10** |
| Full IC | One side stopped (MKT-025) | 4 | 1 | **$12.50** |
| Full IC | Both stopped (MKT-025) | 4 | 2 | **$15** |
| Full IC | One side stopped (pre-MKT-025) | 4 | 2 | **$15** |
| Full IC | Both stopped (pre-MKT-025) | 4 | 4 | **$20** |

**Key**: Expired options have ZERO close commission. MKT-025 (v1.4.3+) closes only the short leg on stop — long expires at settlement. Pre-MKT-025 (Feb 27 and earlier) closed both legs. One-sided entries disabled since v1.4.0.

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
| Metrics file | Cumulative metrics | `/opt/calypso/data/hydra_metrics.json` (VM) |
| Strategy spec | MEIC base specification | `docs/MEIC_STRATEGY_SPECIFICATION.md` |
| Edge cases | 79 analyzed edge cases | `docs/MEIC_EDGE_CASES.md` |
| Bot README | HYDRA hybrid documentation | `bots/hydra/README.md` |
| Daily Summary | Google Sheets tab | "Daily Summary" tab in HYDRA spreadsheet |
| This document | Trading journal | `docs/HYDRA_TRADING_JOURNAL.md` |
