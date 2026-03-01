# HYDRA Early Close Analysis: Profit-Taking Strategies

**Created**: February 18, 2026
**Updated**: February 18, 2026 (v4 - ROC method added, slippage research, user-selected 2.00% ROC threshold)
**Author**: Claude Code analysis
**Data Period**: Feb 10-18, 2026 (6 trading days)
**Status**: RESEARCH COMPLETE - **2.00% ROC threshold selected, shadow mode logging recommended**

---

## Executive Summary

Two early close methods were analyzed using **actual P&L data from the bot's heartbeat logs**:

### Method 1: % of Total Credit (v3)

| Threshold | 6-Day P&L | vs Hold-to-Expiry | Triggers |
|-----------|----------|-------------------|----------|
| Hold to expiry | $+1,385 | baseline | 0/6 |
| **65% of credit** | **$+1,597** | **+$212** | **1/6** |
| 50% of credit | $+1,491 | +$106 | 1/6 |

**Limitation**: Only triggers on Feb 18. On high-premium days (Feb 13: $3,045 credit), the P&L as % of credit stays low even when the dollar return is excellent. The denominator (total credit) varies too much across days.

### Method 2: Return on Capital Deployed (v4 — RECOMMENDED)

| Threshold | 6-Day P&L | vs Hold-to-Expiry | Triggers |
|-----------|----------|-------------------|----------|
| Hold to expiry | $+1,385 | baseline | 0/6 |
| 2.50% ROC | $+1,602 | +$217 | 2/6 |
| 2.25% ROC | $+1,490 | +$105 | 2/6 |
| **2.00% ROC** | **$+1,377** | **-$8** | **2/6** |
| 1.75% ROC | $+1,261 | -$124 | 2/6 |

**Selected threshold: 2.00% ROC** — chosen for consistency and peace of mind. Triggers on both Feb 13 AND Feb 18 (vs credit-based which only catches Feb 18). Cost is essentially zero (-$8 over 6 days) while providing earlier exits on high-profit days.

**Key advantage of ROC**: Normalizes across different premium environments. A 1% daily return on capital is excellent for any options strategy regardless of whether it's a low or high premium day.

**Caveat**: Only 6 days of data. Recommend implementing as **shadow mode logging first** to collect 30+ days of data before activating.

---

## 1. The Core Question

HYDRA collects credit by selling 0DTE SPX iron condor spreads. The full credit is "earned" when options expire worthless at 4:00 PM. But the risk exposure is asymmetric:

- **Unrealized P&L can reach 60-80% of credit by early afternoon** (as seen on Feb 18)
- **Stop risk remains constant**: A late-day reversal costs the same as an early one
- **Feb 18 example**: $645 unrealized profit at 1:04 PM on $810 credit, ended at +$315 net after two late stops

**Question**: Should we close all positions early when we've captured "enough" profit?

---

## 2. Historical Data (6 Trading Days)

| Date | Entries | Total Credit | Capital Deployed | Stops | Stop Debits | Commission | Net P&L | Actual ROC |
|------|---------|-------------|-----------------|-------|-------------|------------|---------|-----------|
| Feb 10 | 5 | $640 | $25,000 | 1 | $140 | $30 | +$350 | +1.40% |
| Feb 11 | 6 | $1,170 | $30,000 | 2 | $290 | $45 | +$425 | +1.42% |
| Feb 12 | 6 | $1,610 | $32,000 | 4 | $410 | $70 | +$360 | +1.12% |
| Feb 13 | 5 | $3,045 | $28,000 | 3 | $1,145 | $60 | +$675 | +2.41% |
| Feb 17 | 5 | $1,885 | $12,500 | 5 | $1,335 | $65 | -$740 | -5.92% |
| Feb 18 | 4 | $810 | $20,000 | 2 | $260 | $35 | +$315 | +1.57% |
| **Total** | **31** | **$9,160** | **$147,500** | **17** | **$3,580** | **$305** | **+$1,385** | **+0.94% avg** |

Capital deployed comes from the bot's Daily Summary tab (authoritative). It represents the total buying power reduction / margin requirement for all entries and does NOT decrease when stops fire.

### Stop Loss Timing Distribution

| When Stop Fires | Count | % | Total Cost | Avoidable by Early Close? |
|----------------|-------|---|------------|---------------------------|
| Within 30 min of entry | 10 | 59% | $2,115 | NO (too fast) |
| 30 min - 2 hours after entry | 4 | 24% | $655 | MAYBE |
| 2+ hours after entry | 3 | 18% | $390 | YES (had high profit before) |
| **After 1:00 PM** | **6** | **35%** | **$810** | **Most likely avoidable** |

---

## 3. Why ROC Is Better Than % of Credit

The credit-based approach (v3) has a fundamental flaw: **the denominator (total credit) varies dramatically across days**, making the threshold inconsistent.

| Date | Total Credit | Capital Deployed | Credit/Capital Ratio | 1% ROC = ? % of Credit |
|------|-------------|-----------------|---------------------|----------------------|
| Feb 10 | $640 | $25,000 | 39.1x | 39.1% |
| Feb 11 | $1,170 | $30,000 | 25.6x | 25.6% |
| Feb 12 | $1,610 | $32,000 | 19.9x | 19.9% |
| **Feb 13** | **$3,045** | **$28,000** | **9.2x** | **9.2%** |
| Feb 17 | $1,885 | $12,500 | 6.6x | 6.6% |
| Feb 18 | $810 | $20,000 | 24.7x | 24.7% |

**Feb 13 is the key example**: Peak P&L was $830 = only 27% of the $3,045 credit (too low for any credit threshold). But that same $830 = **2.96% ROC on $28,000** — well above any reasonable ROC threshold.

With the credit-based approach, Feb 13 never triggers any threshold. With ROC, it triggers at 2.00% (or 2.50%), capturing the profit before the afternoon drop.

---

## 4. Methodology

### P&L Source: Actual Heartbeat Data

The bot logs net P&L every ~13 seconds using Saxo's `ProfitLossOnTrade` field. This captures the **real mark-to-market value** including delta, gamma, and theta effects. P&L data was extracted from `journalctl -u hydra` logs for all 6 trading days.

### Close Costs (Researched)

Each position close incurs two costs:

| Cost | Amount | Source | Notes |
|------|--------|--------|-------|
| **Commission** | $2.50/position | Saxo Bank fixed rate | Charged at both open AND close. Open commission already in heartbeat P&L. |
| **Slippage** | $2.50/position | Research estimate | Market order execution vs mid-price (see Section 8 for full research) |
| **Total close cost** | **$5.00/position** | — | Only the incremental cost of closing early |

- **Per spread (2 positions)**: $10.00
- **Per IC (4 positions)**: $20.00
- **Full day (10-14 positions)**: $50-$70

Only CLOSE costs matter — open costs are sunk (same whether you close early or hold).

### ROC Threshold Check

```
close_cost = active_positions × $5.00
roc = (heartbeat_net_pnl - close_cost) / capital_deployed
→ Close all positions if roc >= 2.00%
```

Only checked **after the last entry** is placed (before that, not all capital is deployed). Capital deployed is fixed for the day — it does NOT decrease when stops fire.

### How It Works in the Bot

The heartbeat already calculates `net_pnl` every ~13 seconds, which includes:
- Realized P&L from any stops that fired
- Unrealized P&L on remaining open positions
- Commissions paid so far (on entries)

The ROC check simply divides `(net_pnl - close_cost) / capital_deployed` and compares against the threshold. If met, close all remaining positions.

---

## 5. ROC P&L Trajectories

### Feb 13 — The Day ROC Catches but Credit-Based Misses

Capital deployed: $28,000. Total credit: $3,045. Actual net: +$675.

| Time ET | Net P&L | ROC | Active Pos | After Close Cost | ROC After Close |
|---------|---------|-----|-----------|-----------------|----------------|
| 12:05 | -$96 | -0.34% | 14 | -$166 | -0.59% |
| 12:35 | +$165 | +0.59% | 14 | +$95 | +0.34% |
| 13:05 | +$392 | +1.40% | 14 | +$322 | +1.15% |
| 13:35 | +$585 | +2.09% | 14 | +$515 | +1.84% |
| **13:40** | — | — | **14** | — | **2.00% ← TRIGGERS** |
| 14:05 | +$746 | +2.67% | 14 | +$676 | +2.42% |
| **14:33** | **+$830** | **+2.96%** | **14** | **+$760** | **+2.71% ← PEAK** |
| 15:05 | +$491 | +1.76% | 12 | +$431 | +1.54% |
| 16:00 | +$600 | +2.14% | 12 | +$540 | +1.93% |

**At 2.00% ROC threshold**: Closes at 1:40 PM for **+$561** instead of the actual +$675 → costs $114 on this day.

But this is the trade-off: you give up $114 of additional profit to **eliminate 2.5 hours of stop risk**. The $830 peak at 2:33 PM was followed by a -$339 drop (two stops at ~3 PM), recovering only partially to $600 by close.

Credit-based: Peak was 27% of credit. **No credit threshold would trigger** — the $3,045 denominator is too large.

### Feb 18 — Both Methods Catch This Day

Capital deployed: $20,000. Total credit: $810. Actual net: +$315.

| Time ET | Net P&L | ROC | Active Pos | After Close Cost | ROC After Close |
|---------|---------|-----|-----------|-----------------|----------------|
| 12:04 | +$399 | +1.99% | 10 | +$349 | +1.74% |
| **12:07** | — | — | **10** | — | **2.00% ← TRIGGERS** |
| 12:34 | +$509 | +2.54% | 10 | +$459 | +2.29% |
| **13:04** | **+$645** | **+3.23%** | **10** | **+$595** | **+2.97% ← PEAK** |
| 13:34 | +$549 | +2.75% | 10 | +$499 | +2.50% |
| 13:53 | — | — | — | — | **Two stops fire** |
| 14:04 | +$223 | +1.12% | 6 | +$193 | +0.97% |
| 16:00 | +$315 | +1.57% | 6 | +$285 | +1.43% |

**At 2.00% ROC threshold**: Closes at 12:07 PM for **+$421** instead of the actual +$315 → **saves $106**.

The two stops at 1:53 PM cost $260 and erased nearly half the gains. Early close at 2% ROC exits almost 2 hours before those stops.

### Feb 17 — The Losing Day (No Threshold Can Help)

Capital deployed: $12,500. Actual net: -$740.

P&L was **never positive** after last entry. Peak was -$285 at 12:08 (-2.28% ROC). No profit-taking threshold could trigger. All 5 stops fired within the first 2 hours.

### Feb 10, 11, 12 — Normal Winning Days (Threshold Doesn't Trigger)

| Date | Peak ROC (after close cost) | Triggers 2.00%? | Hold-to-Expiry |
|------|---------------------------|-----------------|---------------|
| Feb 10 | +1.20% at 15:46 | NO | +$350 (+1.40%) |
| Feb 11 | +1.25% at 15:37 | NO | +$425 (+1.42%) |
| Feb 12 | +0.75% at 12:51 | NO | +$360 (+1.12%) |

These days never reach 2.00% ROC after close costs. The threshold correctly avoids triggering on normal winning days where hold-to-expiry earns more.

---

## 6. ROC Results Summary

### Static Thresholds

| Threshold | Total P&L | vs Baseline | Triggers | Key Effect |
|-----------|----------|-------------|----------|-----------|
| 0.50% | $+190 | **-$1,195** | 5/6 | Devastating — exits almost every day for tiny gain |
| 0.75% | $+403 | **-$982** | 5/6 | Still far too early |
| 1.00% | $+718 | **-$667** | 4/6 | Triggers on winning days, locks in less than hold |
| 1.25% | $+964 | **-$421** | 3/6 | Still harmful — catches Feb 10+11+13 |
| 1.50% | $+1,126 | **-$259** | 2/6 | Feb 13 hurts (-$206), Feb 18 helps |
| 1.75% | $+1,261 | -$124 | 2/6 | Feb 13 still hurts, Feb 18 helps |
| **2.00%** | **$+1,377** | **-$8** | **2/6** | **Near breakeven — peace of mind** |
| 2.25% | $+1,490 | +$105 | 2/6 | Better returns, same trigger days |
| **2.50%** | **$+1,602** | **+$217** | **2/6** | **Best returns (Feb 13+18)** |
| 3.00% | $+1,385 | $0 | 0/6 | Never triggers |

### 2.00% vs 2.25% vs 2.50% — Detailed Comparison

| Day | 2.00% ROC | 2.25% ROC | 2.50% ROC | Hold |
|-----|-----------|-----------|-----------|------|
| Feb 10 | $+350 (hold) | $+350 (hold) | $+350 (hold) | $+350 |
| Feb 11 | $+425 (hold) | $+425 (hold) | $+425 (hold) | $+425 |
| Feb 12 | $+360 (hold) | $+360 (hold) | $+360 (hold) | $+360 |
| Feb 13 | **$+561** (1:40 PM) | **$+600** (1:53 PM) | **$+645** (2:00 PM) | $+675 |
| Feb 17 | -$740 (hold) | -$740 (hold) | -$740 (hold) | -$740 |
| Feb 18 | **$+421** (12:07 PM) | **$+495** (12:30 PM) | **$+527** (12:49 PM) | $+315 |
| **Total** | **$+1,377** | **$+1,490** | **$+1,602** | **$+1,385** |
| **vs Hold** | **-$8** | **+$105** | **+$217** | — |

**Decision**: User selected **2.00% ROC** for peace of mind and consistency:
- Exits earliest (12:07 PM on Feb 18, 1:40 PM on Feb 13)
- Cost is negligible (-$8 over 6 days, essentially breakeven)
- Provides the most "done for the day" time — off by early afternoon on good days
- The $113 difference vs 2.50% is the price of earlier exits and reduced stress

### Dynamic ROC Thresholds

Best: **D:1.5%+1.5%** (threshold = 1.5% + 1.5% × hours_remaining/6.5) → +$173 vs baseline

| Config | Total P&L | vs Baseline | Triggers |
|--------|----------|-------------|----------|
| D:1.5%+1.5% | $+1,558 | +$173 | 2/6 |
| D:1.5%+1.0% | $+1,450 | +$65 | 2/6 |
| D:1.0%+1.5% | $+1,267 | -$118 | 4/6 |

Dynamic thresholds outperform the static 2.00% but add complexity. Not recommended for the initial implementation.

---

## 7. Credit-Based Results (v3 — for reference)

The credit-based approach is documented here for completeness. It was superseded by the ROC approach (Section 6) because the credit denominator varies too much across days.

### Static Thresholds (Credit-Based)

| Threshold | Total P&L | vs Baseline | Triggers | Key Effect |
|-----------|----------|-------------|----------|-----------|
| 20% | $+867 | **-$518** | 4/6 | Triggers too early, forfeits gains |
| 30% | $+1,110 | **-$275** | 3/6 | Still too early on winning days |
| 45% | $+1,384 | -$1 | 2/6 | Near breakeven |
| **50%** | **$+1,491** | **+$106** | **1/6** | Feb 18 only |
| **65%** | **$+1,597** | **+$212** | **1/6** | Feb 18 only (best) |

**Key limitation**: Even at 50%, only triggers on Feb 18 (1/6 days). Feb 13's peak of 27% of credit is far below any useful threshold despite being a +$830 peak (+2.96% ROC).

---

## 8. Slippage Research

### SPX Option Market Order Slippage

SPX options trade with a **$0.05 minimum tick size** (not penny increments like SPY). This means the smallest possible price movement is $0.05 ($5.00 per contract).

| Option Price | Typical Bid-Ask Spread | Half-Spread (Slippage) | Per Position |
|-------------|----------------------|----------------------|-------------|
| $0.10 | $0.05-$0.10 | $0.025-$0.05 | $2.50-$5.00 |
| $0.50 | $0.05-$0.10 | $0.025-$0.05 | $2.50-$5.00 |
| $1.00 | $0.05-$0.10 | $0.025-$0.05 | $2.50-$5.00 |
| $2.00 | $0.10-$0.20 | $0.05-$0.10 | $5.00-$10.00 |

### Why $2.50/Position Is Reasonable

1. **Half of one tick**: $0.05/2 = $0.025 per contract × 100 multiplier = $2.50 — the minimum non-zero slippage given SPX's $0.05 tick
2. **Our option characteristics**: OTM options at ~8 delta, 50-pt spreads, "prominent" strikes (divisible by 5) — these are among the most liquid SPX options
3. **Single contract**: We trade only 1 contract per leg — zero market impact
4. **High 0DTE liquidity**: ~1.5 million 0DTE contracts trade daily, roughly half of all SPX options volume
5. **Practitioner data**: One trader reported "$2.50 price, $2.60 fill" = $0.10 slippage ($10/position), but this was a higher-value option. Our decayed options at close would be cheaper with tighter spreads

### Slippage on Each Leg at Early Close

When closing an IC, slippage hurts on **every leg** (buy back shorts at ask, sell longs at bid):
- Short legs (buying to close at ~$0.30-$0.80): spread ~$0.05, slippage ~$2.50/position
- Long legs (selling to close at ~$0.05-$0.15): spread ~$0.05, slippage ~$2.50/position

### Sensitivity Analysis

| Slippage/Position | 10-Position Close Cost | Effect on 2% ROC Feb 18 | Still Profitable? |
|-------------------|----------------------|------------------------|------------------|
| $1.50 | $15 + $25 comm = $40 | +$431 (vs +$315 hold) | YES (+$116) |
| **$2.50 (used)** | **$25 + $25 comm = $50** | **+$421 (vs +$315 hold)** | **YES (+$106)** |
| $5.00 | $50 + $25 comm = $75 | +$396 (vs +$315 hold) | YES (+$81) |
| $7.50 | $75 + $25 comm = $100 | +$371 (vs +$315 hold) | YES (+$56) |

The 2.00% ROC threshold remains beneficial on Feb 18 even with 3x the assumed slippage.

### Sources
- [Option Alpha: Backtest Slippage](https://optionalpha.com/blog/backtest-slippage-when-opening-and-closing-trades) — recommends $0.05-$0.10 from mid-price
- [Option Alpha: SPX Min Bid Petition](https://optionalpha.com/blog/spx-petition) — SPX $0.05 tick size, wider than SPY
- [SteadyOptions: SPX vs SPY](https://steadyoptions.com/articles/spx-options-vs-spy-options-which-should-i-trade-r807/) — RUT slippage 10-15 cents, SPX similar
- [Early Retirement Now: 2025 Options Review](https://earlyretirementnow.com/2026/01/30/options-trading-series-part-14-year-2025-review/) — practitioner execution data
- [Resonanz Capital: 0DTE Analysis](https://resonanzcapital.com/insights/same-day-options-same-day-alpha-institutional-lessons-from-0-dtes-boom) — 2024 spreads narrowed, execution improved
- [Hidden Fees in Options Trading](https://www.optionstrading.org/blog/hidden-costs-of-options-trading/) — low-liquidity options can have 50% spreads

---

## 9. Recommendation

### Phase 1: Shadow Mode Logging (Implement Now)

Add a log line to the heartbeat that tracks the ROC early close metric:

```python
# In heartbeat, after calculating net P&L and after last entry placed:
if self.daily_state.next_entry_index >= len(self.entry_times):
    capital_deployed = self.daily_state.capital_deployed  # from daily state
    if capital_deployed > 0:
        active_positions = sum(
            1 for e in self.daily_state.entries
            if e.time and not e.is_fully_done()
            for _ in range(e.positions)
        )
        close_cost = active_positions * 5.00
        roc = (net_pnl - close_cost) / capital_deployed
        if roc > 0.01:  # only log when ROC > 1%
            logger.info(
                f"MKT-018-SHADOW: ROC={roc:.2%} | "
                f"net={net_pnl:.0f} - close_cost={close_cost:.0f} "
                f"/ capital={capital_deployed:.0f}"
            )
```

**Zero operational risk.** Collects data passively.

### Phase 2: Review After 30+ Days

Once we have 30+ days of shadow data:
1. Count how often ROC exceeds 1.50%, 1.75%, 2.00%, 2.25%, 2.50%
2. For each occurrence, check if the day ended lower (i.e., early close would have helped)
3. Calculate the expected improvement per day
4. Verify that 2.00% ROC doesn't trigger on normal winning days

### Phase 3: Implement If Justified

If the data confirms the benefit:
- **Recommended threshold: 2.00% ROC** (selected for consistency and peace of mind)
- **Only check after last entry is placed**
- **Close all active positions** (not partial)
- **Only during market hours** (not in last 15 min before close)
- **Use market orders** for immediate execution

### What NOT to Do

- Do NOT use thresholds below 1.50% ROC — they consistently hurt returns
- Do NOT use credit-based thresholds — the denominator varies too much across days
- Do NOT implement early close as active feature without 30+ days of shadow data

---

## 10. Methodology Notes

### Backtest Scripts

| Script | Method | Best Config |
|--------|--------|------------|
| `scripts/early_close_backtest.py` | % of total credit (v3) | 65% = +$212 (1 trigger day) |
| `scripts/early_close_roc_backtest.py` | Return on capital (v4) | 2.50% = +$217 (2 trigger days) |

### Evolution of Analysis

| Version | Approach | Conclusion |
|---------|----------|-----------|
| v1-v2 | Theta decay model | "Don't implement" (model was 3x off) |
| v3 | % of credit, actual heartbeat P&L | "Promising at 50-65%, but only 1 trigger day" |
| **v4** | **ROC, actual heartbeat P&L** | **"2.00% ROC for consistency, 2 trigger days"** |

The credit-based approach was superseded because the credit denominator varies 5x across days ($640 to $3,045), making thresholds inconsistent. The ROC approach normalizes this: 2.00% ROC means "$200 on $10K" or "$600 on $30K" — consistent regardless of premium environment.

### Limitations

1. **Sample size**: Only 6 trading days. The benefit comes from 2 days (Feb 13, Feb 18). Need 30+ days minimum.
2. **Selection bias**: Feb 13 and Feb 18 may be unusual. Need to verify the threshold doesn't trigger on days where holding would be better.
3. **Close cost assumption**: $5/position ($2.50 commission + $2.50 slippage). Actual slippage varies with bid-ask spreads. See Section 8 for sensitivity analysis — results hold even at 3x assumed slippage.
4. **Active position count**: Derived from entry data, not directly from logs. Some inaccuracy possible around stop times.
5. **No transaction impact**: Closing 10 positions simultaneously might move prices (though 1 contract per position minimizes this).
6. **Capital deployed accuracy**: Uses Daily Summary tab values, which may have minor differences from real-time margin requirements.

---

## Sources

- Bot heartbeat logs: `journalctl -u hydra` (Feb 10-18, 2026)
- `scripts/early_close_backtest.py` v3 simulation code (credit-based)
- `scripts/early_close_roc_backtest.py` v4 simulation code (ROC-based)
- Daily Summary tab from Google Sheets trading log (capital deployed, P&L, commission)
- Slippage research: see Section 8 sources
