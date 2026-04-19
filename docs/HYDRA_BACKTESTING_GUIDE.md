# HYDRA Backtesting Guide

## Overview

This document covers the complete backtesting infrastructure for HYDRA, including data sources, engine mechanics, parameter optimization methodology, and the converged final configuration. Written March 29, 2026 after a week-long optimization session.

---

## Data Infrastructure

### ThetaData Terminal

All historical options data comes from ThetaData's REST API running locally:
- **Host**: `http://127.0.0.1:25510`
- **Subscription**: Required for downloading new data
- **Data types**: Option chain quotes (bid/ask/mid), Greeks (delta/IV), SPX/VIX index prices

### Data Resolutions

| Resolution | Folder | Files | Size | Use |
|-----------|--------|-------|------|-----|
| 1-minute options | `cache/options_1min/` | 938 | ~700 MB | **Primary** — stop monitoring |
| 1-minute Greeks | `cache/greeks_1min/` | 938 | ~475 MB | **Primary** — strike selection |
| 5-minute options | `cache/options/` | 973 | ~207 MB | Legacy — do not use for optimization |
| 5-minute Greeks | `cache/greeks/` | 907 | ~113 MB | Legacy — do not use for optimization |
| 1-minute SPX/VIX index | `cache/index/` | monthly files | ~15 MB | Used by both resolutions |

**CRITICAL**: Always use 1-minute data (`data_resolution='1min'` in config). The 5-minute data misses ~200 stops that the 1-minute data catches, inflating Sharpe by ~40%. The live bot checks stops every 2 seconds — 1-minute is the closest we can get to real behavior.

### Downloading New Data

```bash
# Download 1-minute option quotes + Greeks for new dates
python -m backtest.download_1min 2026-03-28 2026-06-30

# Download 5-minute data (legacy, only if needed)
python -m backtest.downloader 2026-03-28 2026-06-30

# Download Greeks only
python -m backtest.download_1min --greeks-only 2026-03-28 2026-06-30
```

The downloader skips already-cached dates. Safe to re-run.

---

## Engine

### Location
`backtest/engine.py`

### How It Works

For each trading day:

1. **Load data**: 1-min option chain quotes, Greeks (if real-Greeks mode), SPX index, VIX index
2. **For each entry time** (10:15, 10:45, 11:15 + E6 at 14:00 if upday OR downday):
   - Note: E#1 at 10:15 dropped at all VIX levels in live since 2026-04-17 (`max_entries: [2,2,2,1]`); backtests can still simulate it for historical comparison
   - Find 8-delta OTM distance from Greeks
   - Multiply by starting OTM multiplier (3.5× calls, 4.0× puts)
   - Scan inward in 5pt steps until credit ≥ minimum ($1.35 call, $2.10 put)
   - MKT-029 fallback: re-scan at floor ($0.75 call, $2.07 put)
   - Determine entry type: full IC, call-only (down day), put-only (up day), or skip
   - Calculate stop levels: credit + buffer (asymmetric call/put)
3. **Monitor stops every 1 minute**: Get spread close cost from real bid/ask quotes
4. **Settlement at 4 PM**: Cash-settled, intrinsic value if ITM
5. **P&L**: credit - close_cost - commission

### Key Engine Settings

| Setting | Value | Notes |
|---------|-------|-------|
| `data_resolution` | `"1min"` | ALWAYS use 1-min for optimization |
| `use_real_greeks` | `True` | Strict mode — skips days without Greeks files |
| `monitor_interval_ms` | 300000 | Ignored — actual resolution comes from data timestamps |
| `commission_per_leg` | $2.50 | Saxo actual |
| `stop_slippage_per_leg` | 0.0 | Not modeled (conservative: real Sharpe may be slightly lower) |

### Fill Price Model

- **Entry**: `short_bid - long_ask` (you sell at bid, buy at ask)
- **Stop exit**: `short_ask - long_bid` (you buy back at ask, sell at bid)
- **Settlement**: Cash-settled at SPX closing price, no transaction

---

## Configuration

### Final Converged Config (March 29, 2026)

Located in `backtest/config.py` → `live_config()` function.

| Parameter | Value | Backtest Key | VM Config Key |
|-----------|-------|-------------|--------------|
| Entry times | 10:15, 10:45, 11:15 | `entry_times` | `entry_times` |
| E6 upday put-only | ON at 14:00 | `conditional_upday_e6_enabled` + `conditional_entry_times` | Same |
| E7 downday call-only | OFF | `conditional_e7_enabled` | Same |
| FOMC T+1 call-only | ON | `fomc_t1_callonly_enabled` | Same |
| VIX spread multiplier | 5.3× | `spread_vix_multiplier` | Same |
| Max spread width | 83pt | `max_spread_width` | Same |
| Min call credit | $1.35 | `min_call_credit` | `min_viable_credit_per_side` |
| Min put credit | $2.10 | `min_put_credit` | `min_viable_credit_put_side` |
| Call credit floor (MKT-029) | $0.75 | `call_credit_floor` | Same |
| Put credit floor (MKT-029) | $2.07 | `put_credit_floor` | Same |
| Call stop buffer | $0.35 | `call_stop_buffer` (×100 = 35.0) | `call_stop_buffer` (dollars = 0.35) |
| Put stop buffer | $1.55 | `put_stop_buffer` (×100 = 155.0) | `put_stop_buffer` (dollars = 1.55) |
| Down-day call-only threshold | 0.57% | `base_entry_downday_callonly_pct` (0.57) | `base_entry_downday_callonly_pct` (0.0057) |
| Upday threshold | 0.48% | `upday_threshold_pct` (0.48) | `upday_threshold_pct` (0.0048) |
| Theo put credit (call-only stops) | $2.60 | `downday_theoretical_put_credit` (×100 = 260.0) | `downday_theoretical_put_credit` (2.60) |
| One-sided entries | ON | `one_sided_entries_enabled` | Same |
| Put-only max VIX | 15.0 | `put_only_max_vix` | Same |
| Price-based stops | OFF (credit-based) | `price_based_stop_points` = None | Same |
| FOMC announcement skip | OFF | `fomc_announcement_skip` = False | Same |
| Whipsaw range skip | 1.50× EM | `whipsaw_range_skip_mult` = 1.50 | Same |

**UNIT WARNING**: The backtest config stores stop buffers and theo put credit in cents (×100). The VM config stores them in dollars. Do NOT copy values directly — divide by 100 when going backtest → VM.

### Parameters That Don't Matter

These were proven to have zero impact on results:
- `call_starting_otm_multiplier` (3.5×) — credit gate selects the strike, not the scan start
- `put_starting_otm_multiplier` (4.0×) — same reason
- `call_min_spread_width` (25pt) — never binding (VIX formula always gives wider)
- `put_min_spread_width` (25pt) — same
- `put_tighten_retries` (0) — MKT-029 fallback handles everything
- `call_tighten_retries` (0) — same
- `daily_loss_limit` — tested, no impact with honest implementation (losses are too small to trigger useful limits)
- `spread_value_cap_at_stop` — tested, zero impact (stops fire before spread reaches theoretical max)

---

## Optimization Methodology

### Process

1. **Coarse sweep**: Test each parameter independently with 7-20 values across a wide range
2. **Fine-grain**: Narrow to ±20% of winner with 2-3× finer steps
3. **Edge check**: If winner is at boundary, extend range and re-sweep
4. **Convergence test**: Sweep ALL 12 non-trivial parameters simultaneously with tight ranges. If any shifts > 0.02 Sharpe, update and re-run. Repeat until all converge.
5. **Joint 2D sweep**: For coupled parameters (spread_vix_multiplier × max_spread_width), sweep jointly to find the true optimum pair
6. **Validation**: Walk-forward + jitter tests to check for overfitting

### Convergence Testing

The most important step. Parameters interact — optimizing A can shift B's optimal.

**Tool**: `python -m backtest.sweep_dashboard_convergence`

**Rule**: Current value within 0.01 Sharpe of #1 → CONVERGED. Shifted > 0.02 → NEEDS UPDATE.

**Our experience**: Required 5 rounds to converge:
- Round 1: 7 shifted (first run after 1-min migration)
- Round 2: 3 shifted
- Round 3: 4 shifted (spread_vix_mult and max_spread_width oscillating)
- 2D joint sweep: Fixed the coupled pair
- Round 4: 5 shifted (other params adjusting to new width)
- Round 5: **0 shifted — ALL 12 CONVERGED**

**Lesson**: The `spread_vix_multiplier` and `max_spread_width` parameters are tightly coupled (formula: `min(VIX × mult, max_width)`). They must be swept jointly, not independently. Other parameter pairs may also couple — if convergence oscillates, do a 2D joint sweep of the oscillating pair.

### Coupled Parameters Identified

| Pair | Interaction | Solution |
|------|------------|---------|
| `spread_vix_multiplier` × `max_spread_width` | Formula: `min(VIX × mult, width)` | Joint 2D sweep |
| `min_call_credit` × `call_stop_buffer` | Higher credit → wider stop OK | Sequential convergence (settled after width fixed) |
| `min_put_credit` × `put_credit_floor` | Floor is fallback for primary | Keep floor ~$0.03-0.10 below primary |

### Validation Tests

| Test | What It Does | Pass Threshold |
|------|-------------|---------------|
| Walk-forward | Train 2022-2024, test 2025-2026 | TEST Sharpe ≥ 70% of TRAIN |
| Individual jitter ±10% | Perturb each param, check Sharpe drop | < 15% drop = stable |
| Multi-jitter ±5% | Perturb ALL params randomly, 15 trials | Avg degradation < 20% |

**Our results**:
- Walk-forward: TEST = 126% of TRAIN (PASS)
- Individual: 10/11 stable (PASS) — only `max_spread_width` fragile at -10% (cliff at 75pt)
- Multi-jitter: avg Sharpe 2.684, avg degradation 18.2% (PASS)
- Realistic live Sharpe estimate: **2.684**

---

## Dashboard Tools

All dashboards use Rich for terminal display, multiprocessing for parallel execution.

| Script | Purpose | Typical Configs | Time |
|--------|---------|----------------|------|
| `sweep_dashboard.py` | Full 8-param sweep (1-min) | 154 | ~45 min |
| `sweep_dashboard_fine.py` | Fine-grain 5 params | 55 | ~17 min |
| `sweep_dashboard_convergence.py` | Convergence test (12 params) | ~130 | ~43 min |
| `sweep_dashboard_validation.py` | Overfitting validation | 40 | ~13 min |
| `sweep_dashboard_joint_2d.py` | Joint sweep mult × width | 156 | ~53 min |
| `sweep_dashboard_edges.py` | Edge boundary check | 17 | ~6 min |
| `sweep_dashboard_complete.py` | All remaining gaps | 81 | ~27 min |
| `sweep_dashboard_1min_missing.py` | Re-test 5-min-only params on 1-min | 134 | ~46 min |
| `sweep_dashboard_final_gaps.py` | Final gap sweep | 28 | ~10 min |
| `sweep_dashboard_final_missing.py` | Final missing params | 37 | ~12 min |
| `sweep_dashboard_e6_timing.py` | E6 entry time sweep | 11 | ~5 min |
| `sweep_dashboard_max_spread_edge.py` | Max spread edge check | 11 | ~4 min |
| `sweep_dashboard_max_spread_fine.py` | Max spread fine-grain | 11 | ~5 min |
| `full_report.py` | Comprehensive backtest report | 1 | ~6 min |
| `compare_monday_vs_new.py` | Side-by-side config comparison | 2 | ~12 min |

### Running a Dashboard

```bash
cd "/Users/ddias/Desktop/CALYPSO/Git Repo"
python -m backtest.sweep_dashboard_convergence
```

All dashboards use 8 parallel workers by default (capped at CPU count).

---

## Quarterly Re-Optimization Procedure

### Step 1: Download New Data (~3-5 hours)

```bash
python -m backtest.download_1min 2026-03-28 2026-06-30
```

### Step 2: Run Convergence Test (~45 min)

```bash
python -m backtest.sweep_dashboard_convergence
```

**If ALL 12 CONVERGED**: Skip to Step 4.
**If any shifted**: Update `live_config()` in `backtest/config.py`, update VM config, and re-run until converged.

### Step 3: If Spread Mult/Width Oscillate

Run the 2D joint sweep:
```bash
python -m backtest.sweep_dashboard_joint_2d
```
Lock in the winning pair, then re-run convergence.

### Step 4: Validation (~13 min)

```bash
python -m backtest.sweep_dashboard_validation
```

Confirm walk-forward, jitter, and multi-jitter still pass.

### Step 5: Update VM Config

```bash
# SSH to VM and update config
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && python3 -c \"
import json
with open(\\\"bots/hydra/config/config.json\\\", \\\"r\\\") as f:
    c = json.load(f)
# Update changed values here
# c[\\\"strategy\\\"][\\\"param_name\\\"] = new_value
with open(\\\"bots/hydra/config/config.json\\\", \\\"w\\\") as f:
    json.dump(c, f, indent=2)
\"'"

# Restart HYDRA to apply
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"
```

### Step 6: Full Report (~6 min)

```bash
python -m backtest.full_report
```

---

## Final Performance (March 29, 2026)

| Metric | Value |
|--------|-------|
| Backtest Sharpe | 3.282 |
| Realistic live Sharpe (multi-jitter avg) | 2.684 |
| Total P&L (933 days) | $111,801 |
| Max Drawdown | $5,890 |
| Calmar | 5.127 |
| Ann. ROC on $35K | 86% |
| Margin breach days | 0 |
| Walk-forward TEST/TRAIN | 126% |

### Yearly P&L
| Year | P&L |
|------|-----|
| 2022 | $23,832 |
| 2023 | $27,019 |
| 2024 | $16,145 |
| 2025 | $35,580 |
| 2026 | $9,225 (54 days, ~$43K annualized) |

---

## Key Lessons Learned

1. **Always use 1-minute data**. 5-minute misses ~200 stops and inflates Sharpe by 40%.

2. **Sequential optimization doesn't converge**. Parameters interact. Must use convergence testing and joint 2D sweeps for coupled pairs.

3. **The spread_vix_multiplier × max_spread_width pair is tightly coupled**. Always sweep them jointly.

4. **Margin constraints change optimal parameters**. A config that's "best" on paper but breaches $35K on 23% of days isn't actually best. Always check breach days.

5. **Edge checking is essential**. If the winner is at the boundary of your sweep range, the true peak is likely outside your range.

6. **Walk-forward test is the most important validation**. If TEST Sharpe beats TRAIN, you're not overfit.

7. **The backtest config uses different units than the VM config**. Stop buffers and theo put credit are ×100 in the backtest. Always divide by 100 when updating VM.

8. **Tightening retries (put_tighten_retries, call_tighten_retries) are dead code**. MKT-029 fallback floors handle everything. Set to 0.

9. **OTM starting multipliers have zero impact**. The credit gate determines the final strike, not where the scan starts.

10. **The realistic live Sharpe is the multi-jitter average**, not the baseline. Expect ~2.7, not 3.3.
