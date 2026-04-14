"""
Backtest: POT-based strike selection vs Actual HYDRA Production

For each HYDRA production day (Feb 10 - Apr 13, 2026), simulate what would
have happened if HYDRA used POT-based strike selection instead of
credit-based scanning. Compare P&L to actual.

DESIGN PRINCIPLES (anti-bugs):
1. Walk-forward: POT table uses ONLY data BEFORE the test date
2. Use current VIX regime config (3 entries at 10:15/10:45/11:15)
3. Respect regime drops (E#1 dropped at VIX >= 18)
4. Apply Saxo discount factor (34%) to ThetaData credits
5. Simulate stops using SPX intraday path (touched = stopped, simplified)
6. Handle E6 conditional (upday put-only at 14:00)

CAVEATS DOCUMENTED:
- "Touched" simulation is pessimistic — bot has buffer decay that may
  absorb brief touches. Expect actual stop rate < simulated.
- ThetaData credit may not match Saxo exactly (34% discount is avg estimate).
- Historical HYDRA used different configs over time; we compare against
  what HYDRA ACTUALLY did, not idealized HYDRA.

Usage:
    python scripts/backtest_pot_vs_hydra.py --target 0.10
"""

import argparse
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional

DB_PATH = '/tmp/backtesting.db'
POT_RECORDS_PATH = Path('/tmp/pot_records.parquet')
OPTIONS_DIR = Path('backtest/data/cache/options_1min')
INDEX_DIR = Path('backtest/data/cache/index')

VIX_BUCKETS = [
    ('<14', 0, 14),
    ('14-18', 14, 18),
    ('18-22', 18, 22),
    ('22-26', 22, 26),
    ('26-30', 26, 30),
    ('30-35', 30, 35),
    ('35+', 35, 100),
]

# Current HYDRA config (matches VM deployed)
BASE_ENTRY_TIMES = ['10:15', '10:45', '11:15']
E6_CONDITIONAL_TIME = '14:00'
E6_UPDAY_THRESHOLD = 0.0025  # 0.25%

# VIX regime config (matches VM deployed config)
VIX_REGIME_MAX_ENTRIES = {
    '<14':   3,  # deployed: null → default 3
    '14-18': 3,  # deployed: null → default 3
    '18-22': 2,  # deployed: 2
    '22-26': 2,  # deployed: 2
    '26-30': 2,  # deployed: 2 (treating same as 22-28 bucket in VM config)
    '30-35': 1,  # deployed: 1 (VIX 28+)
    '35+':   1,  # deployed: 1
}

# Economic parameters
COMMISSION_PER_LEG = 2.50
CALL_STOP_BUFFER = 75.0  # per-contract × 100 = $75 (updated VM config)
PUT_STOP_BUFFER = 175.0
SAXO_CREDIT_FACTOR = 0.34  # ThetaData → Saxo estimate
SAXO_STOP_SLIPPAGE = 20.0  # typical stop execution slippage ($)


def vix_bucket(vix: float) -> Optional[str]:
    for label, lo, hi in VIX_BUCKETS:
        if lo <= vix < hi:
            return label
    return None


def round_5(x: float) -> float:
    return round(x / 5) * 5


def time_str_to_ms(t: str) -> int:
    h, m = map(int, t.split(':'))
    return (h * 3600 + m * 60) * 1000


def vix_scaled_spread_width(vix: float) -> int:
    """MKT-027: Continuous formula round(VIX × 6.0 / 5) × 5, floor 25pt, cap 110pt"""
    width = round(vix * 6.0 / 5) * 5
    return int(max(25, min(110, width)))


def load_pot_records(cutoff_date: Optional[str] = None) -> pd.DataFrame:
    """Load POT records, optionally filtering to dates BEFORE cutoff (walk-forward)."""
    df = pd.read_parquet(POT_RECORDS_PATH)
    if cutoff_date:
        df = df[df.date < cutoff_date]
    return df


def get_pot_recommendation(pot_df: pd.DataFrame, vix_bucket: str, entry_time: str,
                            side: str, target_rate: float, min_n: int = 10
                            ) -> Optional[int]:
    """Find minimum OTM distance where touch_rate <= target_rate.

    Returns OTM distance in points, or None if unachievable with min_n sample.
    """
    subset = pot_df[
        (pot_df['vix_bucket'] == vix_bucket) &
        (pot_df['entry_time'] == entry_time) &
        (pot_df['side'] == side)
    ]
    if subset.empty:
        return None

    agg = subset.groupby('otm_distance').agg(
        n=('touched', 'count'),
        rate=('touched', 'mean'),
    ).reset_index()
    agg = agg[agg['n'] >= min_n].sort_values('otm_distance')

    qualifying = agg[agg['rate'] <= target_rate]
    if qualifying.empty:
        # Take farthest OTM available
        return int(agg['otm_distance'].max()) if not agg.empty else None
    return int(qualifying['otm_distance'].min())


def load_chain(date_str: str) -> Optional[pd.DataFrame]:
    date_clean = date_str.replace('-', '')
    p = OPTIONS_DIR / f'SPXW_{date_clean}.parquet'
    if not p.exists():
        return None
    return pd.read_parquet(p)


def load_spx_for_date(date_str: str) -> Optional[pd.DataFrame]:
    """Load SPX index data for specific date."""
    year_month = date_str[:4] + date_str[5:7]
    p = INDEX_DIR / f'SPX_{year_month}.parquet'
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df_day = df[df['date'] == pd.Timestamp(date_str)]
    return df_day if not df_day.empty else None


def get_chain_credit(chain: pd.DataFrame, short_strike: float, long_strike: float,
                     right: str, ms_time: int, tolerance_ms: int = 60000
                     ) -> Optional[tuple]:
    """Get credit at target strikes. Returns (theta_credit, saxo_credit) or None."""
    mask_time = (chain.ms_of_day >= ms_time - tolerance_ms) & \
                (chain.ms_of_day <= ms_time + tolerance_ms)
    time_data = chain[mask_time]
    short_rows = time_data[(time_data.strike == short_strike) & (time_data.right == right)]
    long_rows = time_data[(time_data.strike == long_strike) & (time_data.right == right)]
    if short_rows.empty or long_rows.empty:
        return None

    # Take closest-to-target ms
    short_row = short_rows.iloc[(short_rows['ms_of_day'] - ms_time).abs().argsort()].iloc[0]
    long_row = long_rows.iloc[(long_rows['ms_of_day'] - ms_time).abs().argsort()].iloc[0]

    short_bid = short_row.bid
    long_ask = long_row.ask
    if short_bid <= 0:
        return None
    theta_credit = (short_bid - long_ask) * 100
    saxo_credit = theta_credit * SAXO_CREDIT_FACTOR
    return theta_credit, saxo_credit


def simulate_entry(chain: pd.DataFrame, spx_df: pd.DataFrame, date: str, entry_time: str,
                    side: str, otm_distance: int, spread_width: int,
                    spx_at_entry: float
                    ) -> Optional[dict]:
    """Simulate a single side's entry and outcome.

    Returns dict with:
        short_strike, long_strike
        saxo_credit (entry credit, Saxo-adjusted)
        theta_credit (raw ThetaData)
        touched (bool)
        outcome ('expired' or 'stopped' or 'no_credit')
        pnl (Saxo-adjusted $)
    """
    ms_time = time_str_to_ms(entry_time)

    if side == 'call':
        short_strike = round_5(spx_at_entry + otm_distance)
        long_strike = short_strike + spread_width
        right = 'C'
    else:
        short_strike = round_5(spx_at_entry - otm_distance)
        long_strike = short_strike - spread_width
        right = 'P'

    # Get credit
    credit_data = get_chain_credit(chain, short_strike, long_strike, right, ms_time)
    if credit_data is None:
        return None
    theta_credit, saxo_credit = credit_data
    if saxo_credit <= 0:
        return None

    # Check if SPX touched strike between entry and 4:00 PM
    path = spx_df[(spx_df['ms_of_day'] > ms_time) & (spx_df['ms_of_day'] <= 57_600_000)]
    if path.empty:
        return None

    if side == 'call':
        spx_extreme = path['price'].max()
        touched = spx_extreme >= short_strike
    else:
        spx_extreme = path['price'].min()
        touched = spx_extreme <= short_strike

    # Compute P&L
    buffer = CALL_STOP_BUFFER if side == 'call' else PUT_STOP_BUFFER
    entry_commission = 2 * COMMISSION_PER_LEG  # 2 legs to open
    stop_commission = 4 * COMMISSION_PER_LEG   # 4 legs total (open + close)

    if touched:
        # Simulated stop: loss = buffer + slippage
        # bot's stop formula: stop_level = credit + buffer
        # we pay to close: credit + buffer + slippage
        # net: credit - (credit + buffer + slippage) = -buffer - slippage
        # minus commission
        stop_pnl = -buffer - SAXO_STOP_SLIPPAGE - stop_commission + entry_commission
        # Actually cleaner: paid entry_commission at entry, receive saxo_credit,
        # then pay stop (credit + buffer + slippage) + stop_commission
        # Net = saxo_credit - (saxo_credit + buffer + slippage) - stop_commission
        #     = -buffer - slippage - stop_commission
        pnl = -buffer - SAXO_STOP_SLIPPAGE - stop_commission
        outcome = 'stopped'
    else:
        # Expired worthless: keep credit - entry_commission
        pnl = saxo_credit - entry_commission
        outcome = 'expired'

    return {
        'short_strike': short_strike,
        'long_strike': long_strike,
        'theta_credit': theta_credit,
        'saxo_credit': saxo_credit,
        'touched': touched,
        'spx_extreme': spx_extreme,
        'outcome': outcome,
        'pnl': pnl,
    }


def backtest_day(date: str, actual_pnl: float, vix_open: float, pot_df: pd.DataFrame,
                  target_rate: float, walk_forward: bool = True
                  ) -> dict:
    """Simulate HYDRA's trading day using POT strike selection.

    Returns dict with entries, stops, total_pnl, comparison to actual.
    """
    # Determine regime (matches VM deployed config: [18, 22, 28])
    # but POT uses different buckets. We'll map them:
    def hydra_regime(vix):
        if vix < 18: return 0
        elif vix < 22: return 1
        elif vix < 28: return 2
        else: return 3

    def max_entries_for_vix(vix):
        """Match deployed VM config [null, 2, 2, 1]"""
        r = hydra_regime(vix)
        return [3, 2, 2, 1][r]

    # Filter POT data to walk-forward
    cutoff = date if walk_forward else None
    pot = pot_df[pot_df.date < cutoff] if cutoff else pot_df

    bucket = vix_bucket(vix_open)
    if bucket is None:
        return {'error': f'VIX {vix_open} out of range'}

    max_entries = max_entries_for_vix(vix_open)
    spread_width = vix_scaled_spread_width(vix_open)

    # Determine which entries fire (drop earliest per deployed code)
    if max_entries >= 3:
        entry_times = BASE_ENTRY_TIMES
    elif max_entries == 2:
        entry_times = BASE_ENTRY_TIMES[1:]  # drop E#1
    else:
        entry_times = BASE_ENTRY_TIMES[-1:]  # keep only E#3

    # Load chain and SPX
    chain = load_chain(date)
    spx_df = load_spx_for_date(date)
    if chain is None or spx_df is None:
        return {'error': 'no chain or spx data'}

    entries_simulated = []
    total_pnl = 0

    for entry_time in entry_times:
        ms_time = time_str_to_ms(entry_time)
        spx_subset = spx_df[spx_df['ms_of_day'] <= ms_time]
        if spx_subset.empty:
            continue
        spx_at_entry = spx_subset.iloc[-1].price

        # POT-based strike selection for both sides
        call_otm = get_pot_recommendation(pot, bucket, entry_time, 'call', target_rate)
        put_otm = get_pot_recommendation(pot, bucket, entry_time, 'put', target_rate)

        if call_otm is None or put_otm is None:
            continue

        # Simulate call side
        call_result = simulate_entry(chain, spx_df, date, entry_time, 'call',
                                       call_otm, spread_width, spx_at_entry)
        put_result = simulate_entry(chain, spx_df, date, entry_time, 'put',
                                      put_otm, spread_width, spx_at_entry)

        entry_pnl = 0
        if call_result:
            entry_pnl += call_result['pnl']
        if put_result:
            entry_pnl += put_result['pnl']

        # If both sides couldn't be placed, entry was skipped
        placed = (call_result is not None) or (put_result is not None)
        if placed:
            entries_simulated.append({
                'entry_time': entry_time,
                'spx_at_entry': spx_at_entry,
                'call': call_result,
                'put': put_result,
                'entry_pnl': entry_pnl,
            })
            total_pnl += entry_pnl

    # E6 upday conditional (14:00)
    # Fires if SPX at 14:00 >= 0.25% above open
    spx_open_row = spx_df[spx_df['ms_of_day'] >= 34_200_000].head(1)
    spx_1400_rows = spx_df[spx_df['ms_of_day'] <= 50_400_000]
    if not spx_open_row.empty and not spx_1400_rows.empty:
        spx_open_px = spx_open_row.iloc[0].price
        spx_1400 = spx_1400_rows.iloc[-1].price
        if spx_open_px > 0 and (spx_1400 - spx_open_px) / spx_open_px >= E6_UPDAY_THRESHOLD:
            # E6 fires — put-only at same target rate
            put_otm = get_pot_recommendation(pot, bucket, '11:45', 'put', target_rate)
            if put_otm is not None:
                put_result = simulate_entry(chain, spx_df, date, E6_CONDITIONAL_TIME, 'put',
                                              put_otm, spread_width, spx_1400)
                if put_result:
                    entries_simulated.append({
                        'entry_time': E6_CONDITIONAL_TIME,
                        'spx_at_entry': spx_1400,
                        'call': None,
                        'put': put_result,
                        'entry_pnl': put_result['pnl'],
                    })
                    total_pnl += put_result['pnl']

    return {
        'date': date,
        'vix_open': vix_open,
        'vix_bucket': bucket,
        'max_entries': max_entries,
        'entry_times': entry_times,
        'n_entries_placed': len(entries_simulated),
        'pot_total_pnl': total_pnl,
        'actual_pnl': actual_pnl,
        'delta': total_pnl - actual_pnl,
        'entries': entries_simulated,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=float, default=0.10,
                         help='Target touch rate (default 0.10 = 10%%)')
    parser.add_argument('--start', type=str, default='2026-02-10',
                         help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, default='2026-04-13',
                         help='End date (YYYY-MM-DD)')
    parser.add_argument('--no-walk-forward', action='store_true',
                         help='Disable walk-forward (use full POT data for all days)')
    args = parser.parse_args()

    print('=' * 120)
    print(f'POT-BASED STRATEGY BACKTEST vs ACTUAL HYDRA PRODUCTION')
    print('=' * 120)
    print(f'Target touch rate: {args.target*100:.0f}%')
    print(f'Date range: {args.start} to {args.end}')
    print(f'Walk-forward: {"ENABLED (honest)" if not args.no_walk_forward else "DISABLED (look-ahead bias)"}')
    print()

    # Load all POT data
    pot_df = load_pot_records()
    print(f'POT data: {len(pot_df):,} records, {pot_df.date.nunique()} days')

    # Get HYDRA production days
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """SELECT date, vix_open, net_pnl FROM daily_summaries
           WHERE date >= ? AND date <= ? AND vix_open IS NOT NULL
           ORDER BY date""",
        (args.start, args.end)
    ).fetchall()
    conn.close()

    print(f'HYDRA production days: {len(rows)}')
    print()

    # Run backtest for each day
    results = []
    for date, vix, actual_pnl in rows:
        result = backtest_day(date, actual_pnl or 0, vix, pot_df, args.target,
                               walk_forward=not args.no_walk_forward)
        if 'error' in result:
            continue
        results.append(result)

    # Print summary
    print(f'{"Date":<12}{"VIX":<6}{"Bucket":<8}{"Entries":<9}{"Actual":<10}{"POT":<10}{"Delta":<10}{"Note"}')
    print('-' * 120)

    total_actual = 0
    total_pot = 0
    total_delta = 0
    wins = 0
    losses = 0

    for r in results:
        total_actual += r['actual_pnl']
        total_pot += r['pot_total_pnl']
        total_delta += r['delta']
        if r['delta'] > 0:
            wins += 1
        elif r['delta'] < 0:
            losses += 1

        note = ''
        n_stops = sum(
            int(e['call']['outcome'] == 'stopped' if e['call'] else False) +
            int(e['put']['outcome'] == 'stopped' if e['put'] else False)
            for e in r['entries']
        )
        if n_stops > 0:
            note = f'{n_stops} stop(s)'

        flag = '✓' if r['delta'] > 100 else '✗' if r['delta'] < -100 else ' '
        print(f'{r["date"]:<12}{r["vix_open"]:<6.1f}{r["vix_bucket"]:<8}'
              f'{r["n_entries_placed"]:<9}${r["actual_pnl"]:<+8.0f}${r["pot_total_pnl"]:<+8.0f}'
              f'${r["delta"]:<+8.0f} {flag} {note}')

    print('-' * 120)
    print(f'{"TOTAL":<12}{"":<6}{"":<8}{"":<9}${total_actual:<+8.0f}${total_pot:<+8.0f}${total_delta:<+8.0f}')
    print()
    print(f'POT wins (beats actual by >$100): {wins}/{len(results)}')
    print(f'POT loses (worse than actual by >$100): {losses}/{len(results)}')
    print(f'Mean daily delta: ${total_delta/max(1,len(results)):+.0f}')

    # Save detailed results
    import json
    clean_results = []
    for r in results:
        r_clean = {k: v for k, v in r.items() if k != 'entries'}
        r_clean['entries_summary'] = [
            {
                'entry_time': e['entry_time'],
                'call_outcome': e['call']['outcome'] if e['call'] else 'skip',
                'call_pnl': e['call']['pnl'] if e['call'] else 0,
                'put_outcome': e['put']['outcome'] if e['put'] else 'skip',
                'put_pnl': e['put']['pnl'] if e['put'] else 0,
            }
            for e in r['entries']
        ]
        clean_results.append(r_clean)

    with open('/tmp/pot_backtest_results.json', 'w') as f:
        json.dump(clean_results, f, indent=2, default=str)
    print(f'\nDetailed results: /tmp/pot_backtest_results.json')


if __name__ == '__main__':
    main()
