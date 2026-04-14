"""
HYDRA Probability-Of-Touching (POT) Framework

Builds empirical probability tables of SPX touching a given OTM strike distance,
segmented by VIX regime and entry time. Used to select strikes mathematically
based on risk/return rather than credit-based scanning.

Data sources:
- ThetaData greeks (backtest/data/cache/greeks_1min/) — 947 days of delta/gamma/theta
- ThetaData index (backtest/data/cache/index/) — VIX and SPX by month
- ThetaData options_1min (backtest/data/cache/options_1min/) — full chain bid/ask

Approach: For each historical day:
  1. Get VIX at open (regime bucket)
  2. Get SPX at each entry time (10:15, 10:45, 11:15)
  3. Compute each 5pt OTM distance's short strike for both call/put
  4. Check if SPX touched that strike intraday (after entry time)
  5. Record (VIX_bucket, entry_time, OTM_distance, touched?)

Then aggregate: P(touch | VIX_bucket, entry_time, OTM_distance)

Output: probability tables + expected-value-optimal strike recommendations

Usage:
    python scripts/probability_of_touching.py
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional
from collections import defaultdict

CACHE_DIR = Path('backtest/data/cache')
OPTIONS_DIR = CACHE_DIR / 'options_1min'
GREEKS_DIR = CACHE_DIR / 'greeks_1min'
INDEX_DIR = CACHE_DIR / 'index'

# Configuration
ENTRY_TIMES = ['10:05', '10:15', '10:35', '10:45', '11:05', '11:15', '11:35', '11:45']
OTM_DISTANCES = [15, 20, 25, 30, 40, 50, 60, 75, 90, 110, 130, 160, 200]  # points
# VIX regime buckets
VIX_BUCKETS = [
    ('<14', 0, 14),
    ('14-18', 14, 18),
    ('18-22', 18, 22),
    ('22-26', 22, 26),
    ('26-30', 26, 30),
    ('30-35', 30, 35),
    ('35+', 35, 100),
]


def load_spx_and_vix_for_month(year_month: str) -> tuple:
    """Load SPX and VIX minute bars for a month (YYYYMM format)."""
    spx_path = INDEX_DIR / f'SPX_{year_month}.parquet'
    vix_path = INDEX_DIR / f'VIX_{year_month}.parquet'
    spx = pd.read_parquet(spx_path) if spx_path.exists() else None
    vix = pd.read_parquet(vix_path) if vix_path.exists() else None
    return spx, vix


def get_spx_at_ms(spx_df: pd.DataFrame, ms_of_day: int) -> Optional[float]:
    """Get SPX price at or before given ms_of_day."""
    if spx_df is None or spx_df.empty:
        return None
    subset = spx_df[spx_df['ms_of_day'] <= ms_of_day]
    if subset.empty:
        return None
    return subset.iloc[-1].get('price') or subset.iloc[-1].get('close')


def get_spx_path_after(spx_df: pd.DataFrame, start_ms: int, end_ms: int = 57600000) -> pd.DataFrame:
    """Get SPX path from start_ms (exclusive) to end_ms (default 4:00 PM = 57600000 ms)."""
    if spx_df is None or spx_df.empty:
        return pd.DataFrame()
    return spx_df[(spx_df['ms_of_day'] > start_ms) & (spx_df['ms_of_day'] <= end_ms)]


def vix_at_ms(vix_df: pd.DataFrame, ms_of_day: int) -> Optional[float]:
    """Get VIX level at or before ms_of_day."""
    if vix_df is None or vix_df.empty:
        return None
    subset = vix_df[vix_df['ms_of_day'] <= ms_of_day]
    if subset.empty:
        return None
    return subset.iloc[-1].get('price') or subset.iloc[-1].get('close')


def time_str_to_ms(time_str: str) -> int:
    """Convert HH:MM to milliseconds of day."""
    h, m = map(int, time_str.split(':'))
    return (h * 3600 + m * 60) * 1000


def get_vix_bucket(vix: float) -> Optional[str]:
    """Classify VIX into regime bucket."""
    for label, lo, hi in VIX_BUCKETS:
        if lo <= vix < hi:
            return label
    return None


def build_probability_table(start_date: Optional[str] = None,
                             end_date: Optional[str] = None) -> pd.DataFrame:
    """Build empirical probability of touching table.

    For each (date, entry_time, otm_distance, side), record whether SPX
    touched the strike between entry_time and 4:00 PM.

    Returns DataFrame with columns:
      date, day_of_week, vix_open, vix_bucket, entry_time,
      spx_at_entry, otm_distance, side, short_strike, touched, max_adverse_excursion
    """
    records = []

    # Iterate months that have both SPX and VIX data
    months = sorted(set(
        f.stem.replace('SPX_', '')
        for f in INDEX_DIR.glob('SPX_*.parquet')
    ))

    for month in months:
        spx_df, vix_df = load_spx_and_vix_for_month(month)
        if spx_df is None or vix_df is None:
            continue

        # Get each day in this month
        if 'date' not in spx_df.columns:
            # Try common column names
            date_col = next((c for c in spx_df.columns if 'date' in c.lower()), None)
            if not date_col:
                # Might be indexed by date
                dates = spx_df.index.get_level_values('date').unique() if 'date' in spx_df.index.names else []
            else:
                dates = spx_df[date_col].unique()
        else:
            dates = spx_df['date'].unique()

        for date in dates:
            date_str = str(date)[:10] if not isinstance(date, str) else date[:10]
            if start_date and date_str < start_date:
                continue
            if end_date and date_str > end_date:
                continue

            # Filter to this day's data
            spx_day = spx_df[spx_df['date'] == date] if 'date' in spx_df.columns else spx_df
            vix_day = vix_df[vix_df['date'] == date] if 'date' in vix_df.columns else vix_df

            if spx_day.empty or vix_day.empty:
                continue

            # VIX at open (~9:30 ET = 34,200,000 ms)
            vix_open = vix_at_ms(vix_day, 34_500_000)  # slight buffer
            if vix_open is None:
                continue
            vix_bucket = get_vix_bucket(vix_open)
            if not vix_bucket:
                continue

            # For each entry time and OTM distance, record touch outcome
            for entry_time in ENTRY_TIMES:
                entry_ms = time_str_to_ms(entry_time)
                spx_at_entry = get_spx_at_ms(spx_day, entry_ms)
                if spx_at_entry is None:
                    continue

                # SPX path from entry_time to 4:00 PM
                path = get_spx_path_after(spx_day, entry_ms)
                if path.empty:
                    continue

                price_col = 'price' if 'price' in path.columns else 'close'
                spx_max = path[price_col].max()
                spx_min = path[price_col].min()

                for otm in OTM_DISTANCES:
                    # Call side: short strike = spx_at_entry + otm, touched if spx_max >= short_strike
                    call_strike = spx_at_entry + otm
                    call_touched = spx_max >= call_strike
                    call_adverse = (spx_max - call_strike) if call_touched else 0
                    records.append({
                        'date': date_str,
                        'vix_open': vix_open,
                        'vix_bucket': vix_bucket,
                        'entry_time': entry_time,
                        'spx_at_entry': spx_at_entry,
                        'otm_distance': otm,
                        'side': 'call',
                        'short_strike': call_strike,
                        'spx_extreme': spx_max,
                        'touched': int(call_touched),
                        'adverse': call_adverse,
                    })

                    # Put side: short strike = spx_at_entry - otm, touched if spx_min <= short_strike
                    put_strike = spx_at_entry - otm
                    put_touched = spx_min <= put_strike
                    put_adverse = (put_strike - spx_min) if put_touched else 0
                    records.append({
                        'date': date_str,
                        'vix_open': vix_open,
                        'vix_bucket': vix_bucket,
                        'entry_time': entry_time,
                        'spx_at_entry': spx_at_entry,
                        'otm_distance': otm,
                        'side': 'put',
                        'short_strike': put_strike,
                        'spx_extreme': spx_min,
                        'touched': int(put_touched),
                        'adverse': put_adverse,
                    })

    return pd.DataFrame(records)


def aggregate_probability_table(records: pd.DataFrame) -> pd.DataFrame:
    """Aggregate raw records into probability summary.

    Returns: vix_bucket × entry_time × otm_distance × side → (N, touch_rate, mean_adverse)
    """
    grouped = records.groupby(['vix_bucket', 'entry_time', 'otm_distance', 'side']).agg(
        n=('touched', 'count'),
        touch_count=('touched', 'sum'),
        mean_adverse_when_touched=('adverse', lambda x: x[x > 0].mean() if (x > 0).any() else 0),
    ).reset_index()
    grouped['touch_rate'] = grouped['touch_count'] / grouped['n']
    return grouped


def print_summary_tables(agg: pd.DataFrame):
    """Print per-VIX-bucket tables of touch rates."""
    print('=' * 120)
    print('EMPIRICAL PROBABILITY OF TOUCHING (POT) — Historical SPX 0DTE Data')
    print('=' * 120)

    for side in ['call', 'put']:
        print(f'\n\n{"="*120}')
        print(f'{side.upper()} SIDE — P(strike touched) by VIX regime × entry_time × OTM distance')
        print(f'{"="*120}')

        for bucket_label, _, _ in VIX_BUCKETS:
            subset = agg[(agg['vix_bucket'] == bucket_label) & (agg['side'] == side)]
            if subset.empty:
                continue
            # Pivot to matrix
            matrix = subset.pivot_table(
                index='otm_distance', columns='entry_time', values='touch_rate',
                aggfunc='first',
            )
            if matrix.empty:
                continue
            n_total = subset.n.max()
            print(f'\nVIX {bucket_label} (sample: ~{n_total} days per cell):')
            print('P(touched) %:')
            print((matrix * 100).round(0).astype(int).to_string())


def find_optimal_otm_per_bucket(agg: pd.DataFrame, target_touch_rate: float = 0.10):
    """Find minimum OTM distance achieving target touch rate for each VIX bucket × entry time × side.

    target_touch_rate: max probability of being touched (e.g., 0.10 = 10%)
    """
    print('\n' + '=' * 120)
    print(f'OPTIMAL OTM DISTANCE PER REGIME (target: touch rate ≤ {target_touch_rate*100:.0f}%)')
    print('=' * 120)

    for side in ['call', 'put']:
        print(f'\n{side.upper()} SIDE:')
        print(f'{"VIX Bucket":<10}', end='')
        for et in ENTRY_TIMES:
            print(f'{et:>9}', end='')
        print()
        print('-' * (10 + 9 * len(ENTRY_TIMES)))

        for bucket_label, _, _ in VIX_BUCKETS:
            row = [bucket_label]
            subset = agg[(agg['vix_bucket'] == bucket_label) & (agg['side'] == side)]
            if subset.empty:
                continue
            line = f'{bucket_label:<10}'
            for et in ENTRY_TIMES:
                et_subset = subset[subset['entry_time'] == et].sort_values('otm_distance')
                if et_subset.empty:
                    line += f'{"-":>9}'
                    continue
                # Find smallest OTM where touch_rate <= target
                qualifying = et_subset[et_subset['touch_rate'] <= target_touch_rate]
                if qualifying.empty:
                    line += f'{">200":>9}'
                else:
                    min_otm = int(qualifying.otm_distance.min())
                    line += f'{min_otm:>9}'
            print(line)


def main():
    print('Building probability table from historical data...\n')
    records = build_probability_table(start_date='2022-01-01', end_date='2026-04-13')
    if records.empty:
        print('ERROR: No data records built. Check data paths.')
        return

    print(f'Total records: {len(records):,}')
    print(f'Unique days: {records.date.nunique()}')
    print(f'Date range: {records.date.min()} to {records.date.max()}')
    print(f'\nDays per VIX bucket:')
    for label, _, _ in VIX_BUCKETS:
        n_days = records[records.vix_bucket == label].date.nunique()
        print(f'  VIX {label}: {n_days} days')

    # Save raw records
    records.to_parquet('/tmp/pot_records.parquet')
    print(f'\nRaw records saved to /tmp/pot_records.parquet')

    # Aggregate
    agg = aggregate_probability_table(records)
    agg.to_csv('/tmp/pot_aggregated.csv', index=False)

    # Print summary tables
    print_summary_tables(agg)

    # Find optimal OTM distances for different target touch rates
    for target in [0.05, 0.10, 0.15, 0.20]:
        find_optimal_otm_per_bucket(agg, target_touch_rate=target)


if __name__ == '__main__':
    main()
