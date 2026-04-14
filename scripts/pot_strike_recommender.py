"""
HYDRA Strike Recommender — Using Empirical Probability of Touching (POT)

Uses the /tmp/pot_records.parquet data (989 days, 2022-2026) to recommend
optimal OTM distances based purely on empirical touch probability.

Approach: Pick strike at the MINIMUM OTM distance where P(touched) ≤ target.
This is a clean, deterministic framework with no credit modeling assumptions.

Trade-off: Each target_touch_rate represents a different philosophy:
  5%  — conservative (big OTM, small credit, rare stops)
  10% — balanced (moderate OTM, moderate credit)
  15% — aggressive (closer OTM, bigger credit, more stops)
  20% — very aggressive (close to ATM, rich credit, frequent stops)

Usage:
    python scripts/pot_strike_recommender.py

    # Or with a specific VIX and entry time:
    python scripts/pot_strike_recommender.py --vix 21.5 --time 10:45
"""

import argparse
import pandas as pd
from pathlib import Path
from typing import Optional

POT_RECORDS_PATH = Path('/tmp/pot_records.parquet')

VIX_BUCKETS = [
    ('<14', 0, 14),
    ('14-18', 14, 18),
    ('18-22', 18, 22),
    ('22-26', 22, 26),
    ('26-30', 26, 30),
    ('30-35', 30, 35),
    ('35+', 35, 100),
]


def get_vix_bucket(vix: float) -> Optional[str]:
    for label, lo, hi in VIX_BUCKETS:
        if lo <= vix < hi:
            return label
    return None


def load_pot_data() -> pd.DataFrame:
    if not POT_RECORDS_PATH.exists():
        raise FileNotFoundError(
            f'{POT_RECORDS_PATH} not found. Run scripts/probability_of_touching.py first.'
        )
    return pd.read_parquet(POT_RECORDS_PATH)


def compute_touch_rates(pot_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate touch rates by vix_bucket × entry_time × otm_distance × side."""
    return pot_df.groupby(['vix_bucket', 'entry_time', 'otm_distance', 'side']).agg(
        n=('touched', 'count'),
        touched=('touched', 'sum'),
    ).reset_index().assign(
        touch_rate=lambda df: df['touched'] / df['n']
    )


def recommend_strike(rates: pd.DataFrame, vix_bucket: str, entry_time: str,
                      side: str, target_rate: float, min_n: int = 10) -> Optional[dict]:
    """Find minimum OTM distance where touch_rate <= target_rate.

    Returns dict with otm, touch_rate, n_days, or None if target unachievable.
    """
    subset = rates[
        (rates['vix_bucket'] == vix_bucket) &
        (rates['entry_time'] == entry_time) &
        (rates['side'] == side) &
        (rates['n'] >= min_n)
    ].sort_values('otm_distance')

    if subset.empty:
        return None

    # Find first OTM where touch_rate ≤ target
    qualifying = subset[subset['touch_rate'] <= target_rate]
    if qualifying.empty:
        # Target impossible — return the farthest OTM we have (minimum touch rate)
        best = subset.loc[subset['touch_rate'].idxmin()]
        return {
            'otm': int(best['otm_distance']),
            'touch_rate': best['touch_rate'],
            'n_days': int(best['n']),
            'target_met': False,
        }

    best = qualifying.iloc[0]
    return {
        'otm': int(best['otm_distance']),
        'touch_rate': best['touch_rate'],
        'n_days': int(best['n']),
        'target_met': True,
    }


def print_recommendation_tables(rates: pd.DataFrame):
    """Print complete recommendation tables for multiple risk tolerances."""
    entry_times = sorted(rates['entry_time'].unique())

    for target in [0.05, 0.10, 0.15, 0.20]:
        print(f'\n{"="*130}')
        print(f'RECOMMENDED OTM DISTANCE — Target Touch Rate ≤ {target*100:.0f}%')
        print(f'{"="*130}')

        for side in ['call', 'put']:
            print(f'\n{side.upper()} SIDE (pt OTM / actual touch rate / days in sample):')
            header = f'{"VIX":<10}'
            for et in entry_times:
                header += f'{et:>14}'
            print(header)
            print('-' * len(header))

            for bucket_label, _, _ in VIX_BUCKETS:
                line = f'{bucket_label:<10}'
                for et in entry_times:
                    rec = recommend_strike(rates, bucket_label, et, side, target)
                    if rec is None:
                        line += f'{"n/a":>14}'
                    else:
                        flag = '' if rec['target_met'] else '!'
                        cell = f"{rec['otm']}pt/{rec['touch_rate']*100:.0f}%/n{rec['n_days']}{flag}"
                        line += f'{cell:>14}'
                print(line)


def print_full_curve(rates: pd.DataFrame, vix_bucket: str, entry_time: str):
    """Print full touch rate curve for a specific regime."""
    print(f'\n{"="*80}')
    print(f'FULL TOUCH RATE CURVE: VIX {vix_bucket}, Entry {entry_time}')
    print(f'{"="*80}')

    for side in ['call', 'put']:
        subset = rates[
            (rates['vix_bucket'] == vix_bucket) &
            (rates['entry_time'] == entry_time) &
            (rates['side'] == side)
        ].sort_values('otm_distance')

        if subset.empty:
            print(f'\n{side.upper()}: no data')
            continue

        print(f'\n{side.upper()} side:')
        print(f'  {"OTM":<6}{"Touch Rate":>13}{"N":>6}  Visualization')
        for _, row in subset.iterrows():
            tr = row['touch_rate']
            bar = '█' * int(tr * 50)  # visual bar
            print(f'  {row["otm_distance"]:>4}pt{tr*100:>11.1f}%{row["n"]:>6}  {bar}')


def query_specific_recommendation(rates: pd.DataFrame, vix: float, entry_time: str,
                                    targets: list = [0.05, 0.10, 0.15, 0.20]):
    """Print recommendations for specific VIX / entry time at multiple risk tolerances."""
    bucket = get_vix_bucket(vix)
    if bucket is None:
        print(f'VIX {vix} out of range')
        return

    print(f'\n{"="*80}')
    print(f'RECOMMENDATIONS FOR VIX {vix} (bucket {bucket}), Entry time {entry_time}')
    print(f'{"="*80}\n')

    for side in ['call', 'put']:
        print(f'{side.upper()} SIDE:')
        for target in targets:
            rec = recommend_strike(rates, bucket, entry_time, side, target)
            if rec is None:
                print(f'  ≤{target*100:.0f}% target: no data available')
                continue
            flag = '  ★' if rec['target_met'] else '  (target not achievable, best option)'
            print(f'  ≤{target*100:>2.0f}% target: {rec["otm"]:>4}pt OTM  '
                  f'actual rate {rec["touch_rate"]*100:.1f}%  '
                  f'n={rec["n_days"]}{flag}')
        print()


def compare_to_current_hydra(rates: pd.DataFrame):
    """Compare current HYDRA strike selection to what POT recommends."""
    print(f'\n{"="*130}')
    print('COMPARISON: Current HYDRA strike selection vs POT recommendations')
    print(f'{"="*130}\n')

    # Typical HYDRA behavior: credit-based scan lands at ~25-60pt OTM depending on market
    # Compare this to POT-optimal at different risk tolerances
    test_cases = [
        ('Calm market', 16, '10:15'),
        ('Normal', 20, '10:45'),
        ('Elevated', 24, '10:45'),
        ('High', 28, '10:45'),
    ]

    print(f'{"Scenario":<18}{"VIX":<6}{"Time":<8}{"Current":<20}{"5% POT":<18}{"10% POT":<18}{"15% POT":<18}')
    print('-' * 120)

    for label, vix, et in test_cases:
        bucket = get_vix_bucket(vix)

        # Approximate current behavior: bot lands at ~25-40pt OTM (credit gate driven)
        current_estimate = '25-40pt (scan)'

        recs = {}
        for target in [0.05, 0.10, 0.15]:
            # Average of call+put at this target
            call_rec = recommend_strike(rates, bucket, et, 'call', target)
            put_rec = recommend_strike(rates, bucket, et, 'put', target)
            if call_rec and put_rec:
                recs[target] = f"C{call_rec['otm']}/P{put_rec['otm']}"
            else:
                recs[target] = 'n/a'

        print(f'{label:<18}{vix:<6}{et:<8}{current_estimate:<20}'
              f'{recs.get(0.05, "n/a"):<18}{recs.get(0.10, "n/a"):<18}{recs.get(0.15, "n/a"):<18}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--vix', type=float, help='Specific VIX level')
    parser.add_argument('--time', type=str, help='Entry time HH:MM')
    parser.add_argument('--bucket', type=str, help='Show full curve for bucket (e.g., 18-22)')
    parser.add_argument('--target', type=float, default=0.10, help='Target touch rate (0-1)')
    args = parser.parse_args()

    print('Loading POT data...')
    pot_df = load_pot_data()
    print(f'  {len(pot_df):,} records, {pot_df.date.nunique()} unique days, '
          f'{pot_df.date.min()} to {pot_df.date.max()}\n')

    print('Computing touch rates...')
    rates = compute_touch_rates(pot_df)

    # Handle specific query
    if args.vix is not None and args.time is not None:
        query_specific_recommendation(rates, args.vix, args.time)
        return

    # Handle full curve for bucket
    if args.bucket and args.time:
        print_full_curve(rates, args.bucket, args.time)
        return

    # Default: print comprehensive tables
    print_recommendation_tables(rates)
    compare_to_current_hydra(rates)

    print(f'\n{"="*130}')
    print('HOW TO READ:')
    print('  Cell format: "OTMpt/TouchRate/sample_size"')
    print('  e.g., "50pt/9%/n365" = recommend 50pt OTM strike, 9% actual touch rate, 365 days in sample')
    print('  ! suffix = target not achievable in data, this is best available')
    print('\nTypical decision framework:')
    print('  - Risk-averse: use 5% target → bigger OTM, tiny credit, rare stops')
    print('  - Balanced:    use 10% target → moderate OTM, moderate credit')
    print('  - Aggressive:  use 15% target → closer to ATM, richer credit, more stops')
    print(f'{"="*130}')


if __name__ == '__main__':
    main()
