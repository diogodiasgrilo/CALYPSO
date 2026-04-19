"""
HYDRA Expected Value Strike Selection

Combines three historical datasets to compute expected P&L per strike:
  1. Empirical P(touched) by VIX × entry_time × OTM (from probability_of_touching.py)
  2. Empirical credit distribution at each (VIX, time, OTM) from ThetaData
  3. Simplified stop loss model

Output: For each VIX regime × entry time, the OTM distance with maximum
expected P&L per entry.

Expected Value formula:
  EV = P(expire) × (credit - entry_commission)
     + P(touched) × (credit - stop_loss - round_trip_commission)

Where:
  P(expire) = 1 - P(touched)
  stop_loss = (credit + buffer) slippage-adjusted
  commission = $5 entry, $10 if stopped (4 legs × $2.50)

Caveats documented:
  - Uses ThetaData credit (~34% Saxo discount applied)
  - P(touched) ≠ P(actually stopped) — bot has buffer decay, per-side stops, etc.
    The TOUCH is a worst-case trigger; actual stop happens at credit+buffer
  - Single-leg POT (strike touched), not spread value breach
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional

CACHE_DIR = Path('backtest/data/cache')
OPTIONS_DIR = CACHE_DIR / 'options_1min'
INDEX_DIR = CACHE_DIR / 'index'

# Configuration
ENTRY_TIMES = ['10:05', '10:15', '10:35', '10:45', '11:05', '11:15', '11:35', '11:45']
OTM_DISTANCES = [15, 20, 25, 30, 40, 50, 60, 75, 90, 110, 130, 160, 200]
SPREAD_WIDTH = 110  # MKT-027 cap

VIX_BUCKETS = [
    ('<14', 0, 14),
    ('14-18', 14, 18),
    ('18-22', 18, 22),
    ('22-26', 22, 26),
    ('26-30', 26, 30),
    ('30-35', 30, 35),
    ('35+', 35, 100),
]

# Economic parameters
ENTRY_COMMISSION = 5.0  # 2 legs × $2.50
ROUND_TRIP_COMMISSION = 10.0  # 4 legs × $2.50
CALL_STOP_BUFFER = 75.0  # per-contract × 100 = $75
PUT_STOP_BUFFER = 175.0
SAXO_CREDIT_FACTOR = 0.34  # ThetaData → Saxo estimate
SAXO_STOP_WIDENING = 1.3   # Saxo spreads 30% wider on stop fills
SLIPPAGE = 20.0  # typical slippage per stop


def load_month(year_month: str) -> tuple:
    """Load SPX, VIX for a month."""
    spx = pd.read_parquet(INDEX_DIR / f'SPX_{year_month}.parquet')
    vix = pd.read_parquet(INDEX_DIR / f'VIX_{year_month}.parquet')
    return spx, vix


def time_str_to_ms(t: str) -> int:
    h, m = map(int, t.split(':'))
    return (h * 3600 + m * 60) * 1000


def vix_bucket(vix: float) -> Optional[str]:
    for label, lo, hi in VIX_BUCKETS:
        if lo <= vix < hi:
            return label
    return None


def round_5(x: float) -> float:
    return round(x / 5) * 5


def load_chain(date_str: str) -> Optional[pd.DataFrame]:
    """Load SPXW chain for date (YYYY-MM-DD format)."""
    date_clean = date_str.replace('-', '')
    p = OPTIONS_DIR / f'SPXW_{date_clean}.parquet'
    if not p.exists():
        return None
    return pd.read_parquet(p)


def get_credit_at_strike(chain: pd.DataFrame, short_strike: float, long_strike: float,
                         right: str, ms_time: int, tolerance_ms: int = 60000) -> Optional[float]:
    """Get credit (short_bid - long_ask) × 100 for given strikes at given time."""
    mask_time = (chain.ms_of_day >= ms_time - tolerance_ms) & \
                (chain.ms_of_day <= ms_time + tolerance_ms)
    time_data = chain[mask_time]
    short_row = time_data[(time_data.strike == short_strike) & (time_data.right == right)]
    long_row = time_data[(time_data.strike == long_strike) & (time_data.right == right)]
    if short_row.empty or long_row.empty:
        return None
    # Take closest-to-target
    short_bid = short_row.iloc[0].bid
    long_ask = long_row.iloc[0].ask
    if short_bid <= 0:
        return None
    credit_per_contract = short_bid - long_ask
    return credit_per_contract * 100  # $ for 1 contract


def build_credit_table(start_date='2025-01-01', end_date='2026-04-13',
                       side='put'):
    """For each (date, entry_time, OTM_distance, side), compute credit.

    Returns DataFrame with VIX, credit, saxo_credit.
    """
    records = []
    right = 'P' if side == 'put' else 'C'

    months = sorted(set(
        f.stem.replace('SPX_', '')
        for f in INDEX_DIR.glob('SPX_*.parquet')
    ))

    for month in months:
        month_date = f'{month[:4]}-{month[4:]}'
        if month_date < start_date[:7] or month_date > end_date[:7]:
            continue
        try:
            spx_df, vix_df = load_month(month)
        except Exception:
            continue

        dates = spx_df['date'].unique()
        for date in dates:
            date_str = str(date)[:10]
            if date_str < start_date or date_str > end_date:
                continue

            spx_day = spx_df[spx_df['date'] == date]
            vix_day = vix_df[vix_df['date'] == date]
            if spx_day.empty or vix_day.empty:
                continue

            # VIX at open
            vix_subset = vix_day[vix_day['ms_of_day'] <= 34_500_000]
            if vix_subset.empty:
                continue
            vix_open = vix_subset.iloc[-1].price
            bucket = vix_bucket(vix_open)
            if not bucket:
                continue

            chain = load_chain(date_str)
            if chain is None:
                continue

            for entry_time in ENTRY_TIMES:
                entry_ms = time_str_to_ms(entry_time)
                spx_subset = spx_day[spx_day['ms_of_day'] <= entry_ms]
                if spx_subset.empty:
                    continue
                spx_at_entry = spx_subset.iloc[-1].price
                if spx_at_entry <= 0:
                    continue

                for otm in OTM_DISTANCES:
                    if side == 'call':
                        short_strike = round_5(spx_at_entry + otm)
                        long_strike = short_strike + SPREAD_WIDTH
                    else:
                        short_strike = round_5(spx_at_entry - otm)
                        long_strike = short_strike - SPREAD_WIDTH

                    credit = get_credit_at_strike(chain, short_strike, long_strike,
                                                   right, entry_ms)
                    if credit is None or credit <= 0:
                        continue

                    records.append({
                        'date': date_str,
                        'vix_open': vix_open,
                        'vix_bucket': bucket,
                        'entry_time': entry_time,
                        'spx_at_entry': spx_at_entry,
                        'otm_distance': otm,
                        'side': side,
                        'theta_credit': credit,
                        'saxo_credit': credit * SAXO_CREDIT_FACTOR,
                    })

    return pd.DataFrame(records)


def compute_expected_value(pot_df: pd.DataFrame, credit_df: pd.DataFrame) -> pd.DataFrame:
    """Combine POT data with credit data to compute expected value per strike.

    For each (vix_bucket, entry_time, otm_distance, side):
      median_credit = median Saxo credit across all days in bucket
      touch_rate = fraction of days where strike was touched
      stop_level = credit + buffer (Saxo-adjusted)

      EV = (1-touch_rate) × (credit - entry_commission)
         + touch_rate × (credit - stop_level - slippage - round_trip_commission)
    """
    # Aggregate credit by bucket × time × OTM × side
    cred_agg = credit_df.groupby(['vix_bucket', 'entry_time', 'otm_distance', 'side']).agg(
        median_credit=('saxo_credit', 'median'),
        mean_credit=('saxo_credit', 'mean'),
        p25_credit=('saxo_credit', lambda x: x.quantile(0.25)),
        p75_credit=('saxo_credit', lambda x: x.quantile(0.75)),
        n_cred=('saxo_credit', 'count'),
    ).reset_index()

    # Aggregate POT
    pot_agg = pot_df.groupby(['vix_bucket', 'entry_time', 'otm_distance', 'side']).agg(
        touch_rate=('touched', 'mean'),
        n_pot=('touched', 'count'),
    ).reset_index()

    # Merge
    merged = cred_agg.merge(pot_agg, on=['vix_bucket', 'entry_time', 'otm_distance', 'side'],
                             how='inner')

    # Expected value calculation per side
    def compute_ev(row):
        credit = row['median_credit']
        touch = row['touch_rate']
        buffer = CALL_STOP_BUFFER if row['side'] == 'call' else PUT_STOP_BUFFER

        # Saxo stop level = credit + buffer (with widening)
        stop_level = credit + buffer * SAXO_STOP_WIDENING

        # If expired: profit = credit - entry_commission
        expire_pnl = credit - ENTRY_COMMISSION

        # If touched (and therefore stopped): loss = credit - stop_level - slippage - round_trip_commission
        # This is a NEGATIVE number (loss)
        stop_pnl = credit - stop_level - SLIPPAGE - ROUND_TRIP_COMMISSION

        ev = (1 - touch) * expire_pnl + touch * stop_pnl
        return pd.Series({
            'expire_pnl': expire_pnl,
            'stop_pnl': stop_pnl,
            'expected_value': ev,
        })

    merged[['expire_pnl', 'stop_pnl', 'expected_value']] = merged.apply(compute_ev, axis=1)
    return merged


def find_optimal_strike_per_regime(ev_df: pd.DataFrame) -> pd.DataFrame:
    """For each (vix_bucket, entry_time, side), find OTM with max expected value."""
    # Only consider rows with meaningful sample size
    ev_df = ev_df[ev_df['n_pot'] >= 10].copy()
    # Get idxmax per group
    idx = ev_df.groupby(['vix_bucket', 'entry_time', 'side'])['expected_value'].idxmax()
    return ev_df.loc[idx].reset_index(drop=True)


def print_ev_analysis(ev_df: pd.DataFrame):
    """Print expected value matrix per VIX bucket."""
    for side in ['call', 'put']:
        print(f'\n\n{"="*130}')
        print(f'{side.upper()} SIDE — Expected P&L per OTM Distance by VIX × Entry Time')
        print(f'{"="*130}')

        for bucket_label, _, _ in VIX_BUCKETS:
            subset = ev_df[(ev_df['vix_bucket'] == bucket_label) & (ev_df['side'] == side)]
            if subset.empty:
                continue
            # Pivot for display: OTM rows, entry_time cols
            pivot = subset.pivot_table(
                index='otm_distance', columns='entry_time', values='expected_value',
                aggfunc='first',
            )
            if pivot.empty:
                continue
            n_days = subset.n_pot.max()
            print(f'\nVIX {bucket_label} (sample: ~{n_days} days):')
            print('Expected P&L ($) per OTM:')
            print(pivot.round(0).astype(int).to_string())


def print_optimal_strike_table(opt_df: pd.DataFrame):
    """Print recommended optimal OTM distance per regime."""
    print(f'\n\n{"="*130}')
    print('OPTIMAL OTM DISTANCE BY EXPECTED VALUE')
    print(f'{"="*130}')

    for side in ['call', 'put']:
        print(f'\n{side.upper()} SIDE:')
        print(f'{"VIX":<10}', end='')
        for et in ENTRY_TIMES:
            print(f'{et:>18}', end='')
        print()
        print('-' * (10 + 18 * len(ENTRY_TIMES)))

        for bucket_label, _, _ in VIX_BUCKETS:
            line = f'{bucket_label:<10}'
            for et in ENTRY_TIMES:
                r = opt_df[(opt_df['vix_bucket'] == bucket_label) &
                            (opt_df['entry_time'] == et) &
                            (opt_df['side'] == side)]
                if r.empty:
                    line += f'{"-":>18}'
                else:
                    row = r.iloc[0]
                    otm = int(row['otm_distance'])
                    ev = row['expected_value']
                    tr = row['touch_rate'] * 100
                    cred = row['median_credit']
                    # Format: OTM/EV/TR
                    cell = f'{otm}pt EV${ev:+.0f} T{tr:.0f}%'
                    line += f'{cell:>18}'
            print(line)


def print_per_regime_detail(ev_df: pd.DataFrame, opt_df: pd.DataFrame, vix_bucket: str):
    """Detailed analysis for a single VIX bucket."""
    print(f'\n{"="*130}')
    print(f'DETAIL: VIX {vix_bucket} — All OTM distances by entry time')
    print(f'{"="*130}')

    for side in ['call', 'put']:
        subset = ev_df[(ev_df['vix_bucket'] == vix_bucket) & (ev_df['side'] == side)]
        if subset.empty:
            continue

        print(f'\n{side.upper()} side:')
        print(f'{"Entry Time":<12}{"OTM":>5}{"TouchR":>8}{"Credit":>8}{"StopPnL":>10}{"ExpirePnL":>12}{"EV":>10}')
        print('-' * 65)

        for et in ENTRY_TIMES:
            et_subset = subset[subset['entry_time'] == et].sort_values('otm_distance')
            # Best row for this entry time
            if et_subset.empty:
                continue
            best_idx = et_subset['expected_value'].idxmax()
            for idx, row in et_subset.iterrows():
                is_best = idx == best_idx
                marker = ' ★' if is_best else '  '
                print(f'{et:<12}{row["otm_distance"]:>5.0f}{row["touch_rate"]*100:>7.0f}%'
                      f'${row["median_credit"]:>+7.0f}${row["stop_pnl"]:>+9.0f}'
                      f'${row["expire_pnl"]:>+10.0f}${row["expected_value"]:>+8.0f}{marker}')
            print()


def main():
    print('=' * 130)
    print('HYDRA EXPECTED VALUE STRIKE SELECTION — Combining POT + Credit Data')
    print('=' * 130)
    print()

    print('Step 1: Loading POT data from /tmp/pot_records.parquet...')
    try:
        pot_df = pd.read_parquet('/tmp/pot_records.parquet')
        print(f'  {len(pot_df):,} records, {pot_df.date.nunique()} unique days')
    except FileNotFoundError:
        print('  ERROR: Run scripts/probability_of_touching.py first.')
        return

    print('\nStep 2: Building credit table from ThetaData (this takes ~2-5 min)...')
    print('  Note: Only using 2025+ data for relevance to current market structure')
    put_credit_df = build_credit_table(start_date='2025-01-01', end_date='2026-04-13', side='put')
    call_credit_df = build_credit_table(start_date='2025-01-01', end_date='2026-04-13', side='call')
    credit_df = pd.concat([put_credit_df, call_credit_df], ignore_index=True)
    print(f'  Credit records: {len(credit_df):,}')

    # Filter POT to same date range
    pot_df = pot_df[(pot_df.date >= '2025-01-01') & (pot_df.date <= '2026-04-13')]
    print(f'  POT records (filtered 2025+): {len(pot_df):,}')

    print('\nStep 3: Computing expected values...')
    ev_df = compute_expected_value(pot_df, credit_df)
    opt_df = find_optimal_strike_per_regime(ev_df)

    # Save
    ev_df.to_csv('/tmp/ev_analysis.csv', index=False)
    opt_df.to_csv('/tmp/ev_optimal_strikes.csv', index=False)

    # Print results
    print_ev_analysis(ev_df)
    print_optimal_strike_table(opt_df)

    # Deep dive for typical bucket
    print_per_regime_detail(ev_df, opt_df, '18-22')
    print_per_regime_detail(ev_df, opt_df, '22-26')

    print('\n' + '=' * 130)
    print('Output files:')
    print('  /tmp/ev_analysis.csv — full EV by (vix, time, OTM, side)')
    print('  /tmp/ev_optimal_strikes.csv — optimal strike per regime')
    print('=' * 130)


if __name__ == '__main__':
    main()
