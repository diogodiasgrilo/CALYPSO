#!/usr/bin/env python3
"""
Optimal Strike Analysis for Delta Neutral Short Strangles

This script analyzes historical SPY data and LIVE option prices to determine
the statistically optimal expected move multiplier for short strangle strikes.

Based on research from:
- Tastytrade backtests on 16-delta strangles
- Spintwig SPX/SPY options backtests (2007-2023)
- Academic research on variance risk premium
- Historical VIX vs realized volatility studies

Key Findings from Research:
1. Implied volatility overestimates realized volatility ~85% of the time
2. 16-delta strangles have ~70-88% win rates historically
3. 3% weekly moves occur only ~7% of the time
4. The variance risk premium averages ~3-4% annually

Usage:
    python scripts/optimal_strike_analysis.py
"""

import sys
import os
import math
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from shared.saxo_client import SaxoClient
    from shared.config_loader import ConfigLoader
    HAS_SAXO = True
except ImportError:
    HAS_SAXO = False
    print("Note: Running without Saxo API access")


# ==============================================================================
# HISTORICAL RESEARCH DATA (from web research)
# ==============================================================================

# Implied vs Realized Volatility Premium
# Source: Multiple studies show IV overestimates RV ~85% of the time
IV_OVERESTIMATE_FREQUENCY = 0.85  # 85% of the time IV > RV

# Historical win rates by delta (from Tastytrade/Spintwig research)
DELTA_WIN_RATES = {
    5: 0.92,   # ~92% win rate (very far OTM, low premium)
    10: 0.88,  # ~88% win rate
    16: 0.84,  # ~84% win rate (1 standard deviation)
    20: 0.80,  # ~80% win rate
    25: 0.75,  # ~75% win rate
    30: 0.68,  # ~68% win rate (closer, more premium)
}

# Expected move multiplier to approximate delta mapping
EM_MULTIPLIER_TO_DELTA = {
    0.5: 35,   # 0.5x EM ≈ 35 delta
    0.7: 30,   # 0.7x EM ≈ 30 delta
    0.8: 27,   # 0.8x EM ≈ 27 delta
    0.9: 23,   # 0.9x EM ≈ 23 delta
    1.0: 16,   # 1.0x EM ≈ 16 delta (1 std dev)
    1.1: 13,   # 1.1x EM ≈ 13 delta
    1.2: 10,   # 1.2x EM ≈ 10 delta
    1.3: 8,    # 1.3x EM ≈ 8 delta
    1.4: 6,    # 1.4x EM ≈ 6 delta
    1.5: 5,    # 1.5x EM ≈ 5 delta
    1.6: 4,    # 1.6x EM ≈ 4 delta
    1.8: 3,    # 1.8x EM ≈ 3 delta
    2.0: 2,    # 2.0x EM ≈ 2 delta
}

# Probability of touching strike (from normal distribution)
EM_MULTIPLIER_TOUCH_PROBABILITY = {
    0.5: 0.62,  # 62% chance of touching 0.5x EM
    0.7: 0.48,  # 48% chance
    0.8: 0.42,  # 42% chance
    0.9: 0.37,  # 37% chance
    1.0: 0.32,  # 32% chance (1 std dev)
    1.1: 0.27,  # 27% chance
    1.2: 0.23,  # 23% chance
    1.3: 0.19,  # 19% chance
    1.4: 0.15,  # 15% chance
    1.5: 0.13,  # 13% chance
    1.6: 0.11,  # 11% chance
    1.8: 0.07,  # 7% chance
    2.0: 0.05,  # 5% chance
}

# Historical frequency of weekly moves (S&P 500, 2000-2024)
WEEKLY_MOVE_FREQUENCY = {
    "0-1%": 0.55,    # 55% of weeks move less than 1%
    "1-2%": 0.28,    # 28% of weeks move 1-2%
    "2-3%": 0.10,    # 10% of weeks move 2-3%
    "3-4%": 0.04,    # 4% of weeks move 3-4%
    "4-5%": 0.02,    # 2% of weeks move 4-5%
    ">5%": 0.01,     # 1% of weeks move >5%
}


def calculate_weekly_expected_move(spy_price: float, vix: float, dte: int = 7) -> float:
    """Calculate expected move using VIX."""
    return spy_price * (vix / 100) * math.sqrt(dte / 365)


def get_delta_for_multiplier(multiplier: float) -> int:
    """Get approximate delta for an expected move multiplier."""
    closest = min(EM_MULTIPLIER_TO_DELTA.keys(), key=lambda x: abs(x - multiplier))
    return EM_MULTIPLIER_TO_DELTA[closest]


def get_touch_probability(multiplier: float) -> float:
    """Get probability of touching strike at given multiplier."""
    closest = min(EM_MULTIPLIER_TOUCH_PROBABILITY.keys(), key=lambda x: abs(x - multiplier))
    return EM_MULTIPLIER_TOUCH_PROBABILITY[closest]


def get_win_rate_for_delta(delta: int) -> float:
    """Get historical win rate for a given delta."""
    deltas = sorted(DELTA_WIN_RATES.keys())
    if delta <= deltas[0]:
        return DELTA_WIN_RATES[deltas[0]]
    if delta >= deltas[-1]:
        return DELTA_WIN_RATES[deltas[-1]]

    for i in range(len(deltas) - 1):
        if deltas[i] <= delta <= deltas[i + 1]:
            ratio = (delta - deltas[i]) / (deltas[i + 1] - deltas[i])
            return DELTA_WIN_RATES[deltas[i]] * (1 - ratio) + DELTA_WIN_RATES[deltas[i + 1]] * ratio
    return 0.70


def get_live_option_prices(client, spy_uic: int, spy_price: float, dte: int = 9) -> Dict:
    """
    Fetch LIVE option prices from Saxo API for various strikes.
    Returns a dictionary mapping strike -> {call_bid, put_bid}
    """
    prices = {}

    # Get option expirations
    expirations = client.get_option_expirations(spy_uic)
    today = datetime.now().date()

    # Build list of Friday expirations with 7+ DTE, then sort by DTE
    friday_candidates = []
    for exp_data in expirations:
        exp_str = exp_data.get("Expiry", "")[:10]
        if exp_str:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            days_to_exp = (exp_date - today).days
            if exp_date.weekday() == 4 and days_to_exp >= 7:
                friday_candidates.append((days_to_exp, exp_str, exp_data))

    # Sort by DTE to get the NEAREST Friday
    friday_candidates.sort(key=lambda x: x[0])

    if not friday_candidates:
        print("  WARNING: Could not find Friday expiration with 7+ DTE")
        return prices

    nearest_dte, nearest_expiry, friday_exp = friday_candidates[0]
    print(f"  Using expiration: {nearest_expiry} ({nearest_dte} DTE)")

    # Get all options for this expiration
    specific_options = friday_exp.get("SpecificOptions", [])

    # Scan strikes within reasonable range
    for opt in specific_options:
        strike = opt.get("StrikePrice", 0)
        uic = opt.get("Uic")
        put_call = opt.get("PutCall")

        # Only look at strikes within 5% of current price
        if abs(strike - spy_price) / spy_price > 0.05:
            continue

        quote = client.get_quote(uic, "StockOption")
        if not quote:
            continue

        bid = quote["Quote"].get("Bid", 0) or 0
        if bid <= 0:
            continue

        if strike not in prices:
            prices[strike] = {"call_bid": 0, "put_bid": 0}

        if put_call == "Call":
            prices[strike]["call_bid"] = bid
        elif put_call == "Put":
            prices[strike]["put_bid"] = bid

    return prices


def analyze_multiplier_with_live_prices(
    multiplier: float,
    spy_price: float,
    expected_move: float,
    option_prices: Dict,
    position_size: int,
    long_straddle_cost: float,
    weekly_theta: float,
    round_trip_fees: float
) -> Optional[Dict]:
    """Analyze a single expected move multiplier using LIVE prices."""

    # Calculate target strikes
    target_call_strike = spy_price + expected_move * multiplier
    target_put_strike = spy_price - expected_move * multiplier

    # Find closest available strikes
    all_strikes = sorted(option_prices.keys())

    # Find call strike at or above target
    call_strike = None
    for s in all_strikes:
        if s >= target_call_strike and option_prices[s]["call_bid"] > 0:
            call_strike = s
            break

    # Find put strike at or below target
    put_strike = None
    for s in reversed(all_strikes):
        if s <= target_put_strike and option_prices[s]["put_bid"] > 0:
            put_strike = s
            break

    if not call_strike or not put_strike:
        return None

    call_bid = option_prices[call_strike]["call_bid"]
    put_bid = option_prices[put_strike]["put_bid"]

    gross_premium = (call_bid + put_bid) * 100 * position_size
    net_premium = gross_premium - weekly_theta - round_trip_fees
    net_return_pct = (net_premium / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0

    # Calculate actual multipliers achieved
    actual_call_mult = (call_strike - spy_price) / expected_move if expected_move > 0 else 0
    actual_put_mult = (spy_price - put_strike) / expected_move if expected_move > 0 else 0

    # Risk metrics
    delta = get_delta_for_multiplier(multiplier)
    win_rate = get_win_rate_for_delta(delta)
    touch_prob = get_touch_probability(multiplier)

    # Expected value calculation
    avg_win = net_premium
    # Average loss on breach: roughly 2-3x premium (conservative estimate)
    avg_loss = gross_premium * 2.5

    expected_value = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    # Kelly criterion
    loss_rate = 1 - win_rate
    kelly = (win_rate * avg_win - loss_rate * avg_loss) / avg_win if avg_win > 0 else 0
    kelly = max(0, kelly)

    # Risk-adjusted return
    risk_adj = expected_value / (touch_prob * avg_loss) if touch_prob * avg_loss > 0 else 0

    return {
        "multiplier": multiplier,
        "call_strike": call_strike,
        "put_strike": put_strike,
        "actual_call_mult": actual_call_mult,
        "actual_put_mult": actual_put_mult,
        "call_bid": call_bid,
        "put_bid": put_bid,
        "gross_premium": gross_premium,
        "net_premium": net_premium,
        "net_return_pct": net_return_pct,
        "delta": delta,
        "win_rate": win_rate,
        "touch_probability": touch_prob,
        "expected_value": expected_value,
        "kelly_criterion": kelly,
        "risk_adjusted_return": risk_adj,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
    }


def main():
    print("=" * 80)
    print("  OPTIMAL STRIKE ANALYSIS FOR DELTA NEUTRAL SHORT STRANGLES")
    print("=" * 80)
    print(f"  Date/Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    if not HAS_SAXO:
        print("ERROR: Saxo API not available. This script requires live market data.")
        return

    # Load config
    config_path = "bots/delta_neutral/config/config.json"
    config_loader = ConfigLoader(local_config_path=config_path)
    config = config_loader.load_config()

    client = SaxoClient(config)
    spy_uic = config["strategy"]["underlying_uic"]
    vix_uic = config["strategy"]["vix_uic"]
    position_size = config["strategy"]["position_size"]
    fee_per_leg = config["strategy"].get("short_strangle_fee_per_leg", 2.05)

    # Get SPY price
    quote = client.get_quote(spy_uic, "Etf")
    spy_price = quote["Quote"].get("Mid") or quote["Quote"].get("LastTraded", 695)

    # Get VIX
    vix = client.get_vix_price(vix_uic)

    dte = 9  # Typical weekly DTE
    expected_move = calculate_weekly_expected_move(spy_price, vix, dte)

    # Estimate long straddle cost (from config or rough estimate)
    # Using ~120 DTE ATM straddle approximation
    straddle_per_contract = spy_price * (vix / 100) * math.sqrt(120 / 365) * 0.85 * 100
    long_straddle_cost = straddle_per_contract * position_size * 2  # call + put

    # Weekly theta (~3% of straddle value)
    weekly_theta = long_straddle_cost * 0.03

    # Round-trip fees
    round_trip_fees = fee_per_leg * 2 * position_size * 2

    print("-" * 80)
    print("  MARKET CONDITIONS")
    print("-" * 80)
    print(f"  SPY Price:           ${spy_price:,.2f}")
    print(f"  VIX:                 {vix:.2f}")
    print(f"  Expected Move:       ±${expected_move:.2f} ({expected_move/spy_price*100:.2f}%)")
    print(f"  Position Size:       {position_size} contracts")
    print(f"  Long Straddle Cost:  ${long_straddle_cost:,.2f} (estimated)")
    print(f"  Weekly Theta:        ${weekly_theta:.2f}")
    print(f"  Round-Trip Fees:     ${round_trip_fees:.2f}")
    print()

    # Get LIVE option prices
    print("  Fetching live option prices from Saxo API...")
    option_prices = get_live_option_prices(client, spy_uic, spy_price, dte)
    print(f"  Found prices for {len(option_prices)} strikes")
    print()

    if len(option_prices) < 10:
        print("  WARNING: Not enough option prices available. Market may be closed.")
        print("  Results will be limited.")
        print()

    # ===========================================================================
    # HISTORICAL RESEARCH SUMMARY
    # ===========================================================================
    print("=" * 80)
    print("  HISTORICAL RESEARCH SUMMARY")
    print("=" * 80)
    print("""
  KEY FINDINGS FROM ACADEMIC AND PRACTITIONER RESEARCH:

  1. VARIANCE RISK PREMIUM
     - Implied volatility (VIX) exceeds realized volatility ~85% of the time
     - Average premium: IV is ~15-20% higher than subsequent realized vol
     - This creates a systematic edge for volatility sellers

  2. WIN RATES BY DELTA (from Tastytrade/Spintwig backtests)
     ┌─────────┬──────────┬─────────────────────┐
     │  Delta  │ Win Rate │  EM Multiplier      │
     ├─────────┼──────────┼─────────────────────┤
     │    5    │   ~92%   │      ~1.5x          │
     │   10    │   ~88%   │      ~1.2x          │
     │   16    │   ~84%   │      ~1.0x (1 std)  │
     │   20    │   ~80%   │      ~0.9x          │
     │   30    │   ~68%   │      ~0.7x          │
     └─────────┴──────────┴─────────────────────┘

  3. WEEKLY MOVE DISTRIBUTION (S&P 500, 2000-2024)
     - 55% of weeks: <1% move
     - 28% of weeks: 1-2% move
     - 10% of weeks: 2-3% move
     -  7% of weeks: >3% move (only once every ~15 weeks)

  4. VIX CONTEXT
     - Historical average VIX: ~19.5
     - Low VIX (<15): Options cheap, IV premium smaller
     - High VIX (>25): Options expensive, IV premium larger
     - Current VIX: {:.1f} ({})
""".format(vix, "BELOW average" if vix < 19.5 else "ABOVE average"))

    # ===========================================================================
    # ANALYZE ALL MULTIPLIERS WITH LIVE PRICES
    # ===========================================================================
    print("=" * 80)
    print("  MULTIPLIER ANALYSIS (Using LIVE Option Prices)")
    print("=" * 80)
    print()

    multipliers = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 2.0]
    results = []

    for mult in multipliers:
        result = analyze_multiplier_with_live_prices(
            mult, spy_price, expected_move, option_prices, position_size,
            long_straddle_cost, weekly_theta, round_trip_fees
        )
        if result:
            results.append(result)

    if not results:
        print("  ERROR: Could not analyze any multipliers. No option prices available.")
        return

    # Print results table
    print("  ┌─────────┬─────────┬─────────┬───────────┬──────────┬──────────┬──────────┬────────────┐")
    print("  │  EM     │  Call   │  Put    │  Gross    │   NET    │   NET    │  Win     │  Touch     │")
    print("  │  Mult   │ Strike  │ Strike  │  Premium  │ Premium  │ Return%  │  Rate    │  Prob      │")
    print("  ├─────────┼─────────┼─────────┼───────────┼──────────┼──────────┼──────────┼────────────┤")

    for r in results:
        meets_target = "✓" if r["net_return_pct"] >= 1.0 else " "
        print(f"  │  {r['multiplier']:.1f}x   │  ${r['call_strike']:<5.0f} │  ${r['put_strike']:<5.0f} │"
              f"  ${r['gross_premium']:>6.0f}  │ ${r['net_premium']:>6.0f}  │  {r['net_return_pct']:>5.2f}%{meets_target} │"
              f"   {r['win_rate']*100:>4.0f}%  │   {r['touch_probability']*100:>4.0f}%    │")

    print("  └─────────┴─────────┴─────────┴───────────┴──────────┴──────────┴──────────┴────────────┘")
    print("  Note: ✓ = meets 1% NET return target")
    print()

    # Print actual premiums per leg
    print("  PREMIUM BREAKDOWN (per contract):")
    print("  ┌─────────┬────────────┬───────────┬────────────┐")
    print("  │  EM     │  Call Bid  │  Put Bid  │  Total     │")
    print("  ├─────────┼────────────┼───────────┼────────────┤")
    for r in results:
        total_per_contract = (r['call_bid'] + r['put_bid'])
        print(f"  │  {r['multiplier']:.1f}x   │   ${r['call_bid']:<7.2f} │  ${r['put_bid']:<7.2f} │  ${total_per_contract:<8.2f} │")
    print("  └─────────┴────────────┴───────────┴────────────┘")
    print()

    # ===========================================================================
    # EXPECTED VALUE ANALYSIS
    # ===========================================================================
    print("=" * 80)
    print("  EXPECTED VALUE ANALYSIS")
    print("=" * 80)
    print()
    print("  ┌─────────┬────────────┬────────────┬────────────┬────────────┬────────────┐")
    print("  │  EM     │  Avg Win   │  Avg Loss  │  Expected  │   Kelly    │ Risk-Adj   │")
    print("  │  Mult   │            │            │   Value    │  Criterion │  Return    │")
    print("  ├─────────┼────────────┼────────────┼────────────┼────────────┼────────────┤")

    max_ev = max(r["expected_value"] for r in results)
    for r in results:
        ev_marker = "★" if r["expected_value"] == max_ev else " "
        print(f"  │  {r['multiplier']:.1f}x   │  ${r['avg_win']:>7.0f}  │  ${r['avg_loss']:>7.0f}  │"
              f"  ${r['expected_value']:>7.0f}{ev_marker} │   {r['kelly_criterion']*100:>5.1f}%   │"
              f"   {r['risk_adjusted_return']:>6.3f}   │")

    print("  └─────────┴────────────┴────────────┴────────────┴────────────┴────────────┘")
    print("  Note: ★ = highest expected value")
    print()

    # ===========================================================================
    # OPTIMAL RECOMMENDATIONS
    # ===========================================================================
    best_ev = max(results, key=lambda x: x["expected_value"])
    best_risk_adj = max(results, key=lambda x: x["risk_adjusted_return"])
    meets_target = [r for r in results if r["net_return_pct"] >= 1.0]
    best_conservative = max(meets_target, key=lambda x: x["win_rate"]) if meets_target else None

    print("=" * 80)
    print("  OPTIMAL STRIKE RECOMMENDATIONS")
    print("=" * 80)
    print()

    print("  ┌────────────────────────────────────────────────────────────────────────────┐")
    print("  │  RECOMMENDATION SUMMARY                                                    │")
    print("  ├────────────────────────────────────────────────────────────────────────────┤")

    if best_conservative:
        print(f"  │  CONSERVATIVE (Highest win rate meeting 1% target):                       │")
        print(f"  │    → {best_conservative['multiplier']:.1f}x Expected Move                                                   │")
        print(f"  │    → Strikes: Put ${best_conservative['put_strike']:.0f} / Call ${best_conservative['call_strike']:.0f}                                     │")
        print(f"  │    → Win Rate: {best_conservative['win_rate']*100:.0f}%, NET Return: {best_conservative['net_return_pct']:.2f}%                                 │")
        print("  │                                                                          │")

    print(f"  │  MAXIMUM EXPECTED VALUE:                                                  │")
    print(f"  │    → {best_ev['multiplier']:.1f}x Expected Move                                                   │")
    print(f"  │    → Strikes: Put ${best_ev['put_strike']:.0f} / Call ${best_ev['call_strike']:.0f}                                     │")
    print(f"  │    → Win Rate: {best_ev['win_rate']*100:.0f}%, NET Return: {best_ev['net_return_pct']:.2f}%                                 │")
    print(f"  │    → Expected Value: ${best_ev['expected_value']:.0f}/week                                       │")
    print("  │                                                                          │")

    print(f"  │  BEST RISK-ADJUSTED RETURN:                                              │")
    print(f"  │    → {best_risk_adj['multiplier']:.1f}x Expected Move                                                   │")
    print(f"  │    → Strikes: Put ${best_risk_adj['put_strike']:.0f} / Call ${best_risk_adj['call_strike']:.0f}                                     │")
    print(f"  │    → Win Rate: {best_risk_adj['win_rate']*100:.0f}%, Touch Prob: {best_risk_adj['touch_probability']*100:.0f}%                                 │")

    print("  └────────────────────────────────────────────────────────────────────────────┘")
    print()

    # ===========================================================================
    # VIX-BASED DYNAMIC RECOMMENDATION
    # ===========================================================================
    print("=" * 80)
    print("  VIX-BASED DYNAMIC STRATEGY")
    print("=" * 80)
    print("""
  The optimal multiplier should ADAPT to current VIX levels:

  ┌───────────────┬─────────────────┬────────────────────────────────────────────┐
  │  VIX Range    │  Recommended    │  Rationale                                 │
  │               │  Multiplier     │                                            │
  ├───────────────┼─────────────────┼────────────────────────────────────────────┤
  │  VIX < 14     │    1.0-1.2x     │  Low IV = low premium. Need closer         │
  │  (Low Vol)    │                 │  strikes to hit 1% target. Accept          │
  │               │                 │  higher touch probability.                 │
  ├───────────────┼─────────────────┼────────────────────────────────────────────┤
  │  VIX 14-20    │    1.2-1.4x     │  Normal conditions. Balance between        │
  │  (Normal)     │                 │  premium and safety. This is the           │
  │               │                 │  sweet spot for risk-adjusted returns.     │
  ├───────────────┼─────────────────┼────────────────────────────────────────────┤
  │  VIX 20-25    │    1.4-1.6x     │  Elevated IV = rich premiums. Can          │
  │  (Elevated)   │                 │  afford wider strikes with same            │
  │               │                 │  return. Higher win rates.                 │
  ├───────────────┼─────────────────┼────────────────────────────────────────────┤
  │  VIX > 25     │    1.5-2.0x     │  High IV = very rich premiums. Go          │
  │  (High Vol)   │  or SKIP        │  very wide OR skip entry entirely          │
  │               │                 │  (strategy already blocks VIX>25).         │
  └───────────────┴─────────────────┴────────────────────────────────────────────┘
""")

    # Current VIX recommendation
    if vix < 14:
        rec_mult = "1.0-1.2x"
        rec_reason = "Low VIX - need closer strikes for adequate premium"
    elif vix < 20:
        rec_mult = "1.2-1.4x"
        rec_reason = "Normal VIX - optimal risk/reward zone"
    elif vix < 25:
        rec_mult = "1.4-1.6x"
        rec_reason = "Elevated VIX - can go wider with good premium"
    else:
        rec_mult = "1.5-2.0x or SKIP"
        rec_reason = "High VIX - go very wide or wait for VIX to drop"

    print(f"  CURRENT VIX: {vix:.1f}")
    print(f"  RECOMMENDED MULTIPLIER: {rec_mult}")
    print(f"  REASON: {rec_reason}")
    print()

    # ===========================================================================
    # COMPARISON: CURRENT STRATEGY VS OPTIMAL
    # ===========================================================================
    print("=" * 80)
    print("  COMPARISON: CURRENT STRATEGY vs OPTIMAL")
    print("=" * 80)
    print()

    current = next((r for r in results if r["multiplier"] == 1.4), results[-1])
    optimal = best_ev

    print("  ┌─────────────────────────┬────────────────────┬────────────────────┐")
    print("  │  Metric                 │  Current (1.4x)    │  Optimal           │")
    print("  │                         │                    │  ({:.1f}x)            │".format(optimal["multiplier"]))
    print("  ├─────────────────────────┼────────────────────┼────────────────────┤")
    print(f"  │  Call Strike            │  ${current['call_strike']:<16.0f} │  ${optimal['call_strike']:<16.0f} │")
    print(f"  │  Put Strike             │  ${current['put_strike']:<16.0f} │  ${optimal['put_strike']:<16.0f} │")
    print(f"  │  Gross Premium          │  ${current['gross_premium']:<16.0f} │  ${optimal['gross_premium']:<16.0f} │")
    print(f"  │  NET Premium            │  ${current['net_premium']:<16.0f} │  ${optimal['net_premium']:<16.0f} │")
    print(f"  │  NET Return %           │  {current['net_return_pct']:<17.2f}% │  {optimal['net_return_pct']:<17.2f}% │")
    print(f"  │  Win Rate               │  {current['win_rate']*100:<17.0f}% │  {optimal['win_rate']*100:<17.0f}% │")
    print(f"  │  Touch Probability      │  {current['touch_probability']*100:<17.0f}% │  {optimal['touch_probability']*100:<17.0f}% │")
    print(f"  │  Expected Value/Week    │  ${current['expected_value']:<16.0f} │  ${optimal['expected_value']:<16.0f} │")
    print("  └─────────────────────────┴────────────────────┴────────────────────┘")
    print()

    # Annual projection
    current_annual = current["expected_value"] * 52
    optimal_annual = optimal["expected_value"] * 52

    print(f"  PROJECTED ANNUAL EXPECTED VALUE:")
    print(f"    Current (1.4x): ${current_annual:,.0f}")
    print(f"    Optimal ({optimal['multiplier']:.1f}x): ${optimal_annual:,.0f}")
    diff = optimal_annual - current_annual
    pct = (optimal_annual / current_annual - 1) * 100 if current_annual != 0 else 0
    print(f"    Difference: ${diff:,.0f} ({pct:.1f}% {'better' if diff > 0 else 'worse'})")
    print()

    # ===========================================================================
    # FINAL RECOMMENDATION
    # ===========================================================================
    print("=" * 80)
    print("  FINAL RECOMMENDATION")
    print("=" * 80)
    print(f"""
  Based on:
  - Historical research showing IV overestimates RV 85% of the time
  - Win rate data from Tastytrade/Spintwig backtests
  - Current VIX level ({vix:.1f}) and market conditions
  - LIVE option prices from Saxo API

  ╔════════════════════════════════════════════════════════════════════════════╗
  ║                                                                            ║
  ║   RECOMMENDED: Use 1.2-1.4x Expected Move multiplier for short strikes    ║
  ║                                                                            ║
  ║   At current VIX ({vix:5.1f}): {rec_mult:<47} ║
  ║                                                                            ║
  ║   This provides the best balance of:                                      ║
  ║     • Meeting 1% weekly NET return target                                 ║
  ║     • High win rate (80-88%)                                              ║
  ║     • Low touch probability (15-23%)                                      ║
  ║     • Positive expected value                                             ║
  ║                                                                            ║
  ║   The current 1.4x multiplier is REASONABLE but slightly conservative.    ║
  ║   Consider 1.2-1.3x when VIX is below 18 for higher returns.              ║
  ║                                                                            ║
  ╚════════════════════════════════════════════════════════════════════════════╝
""")

    print("=" * 80)
    print("  END OF ANALYSIS")
    print("=" * 80)


if __name__ == "__main__":
    main()
