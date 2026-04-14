"""
E6 Upday Conditional — Alternative Configuration Analysis

For each historical upday day (SPX rose >=0.25% by 14:00 ET), test a matrix
of entry_time × OTM_target combinations to find configurations that would
have produced viable credits AND non-stopped outcomes.

Data sources:
- SPX paths: VM SQLite (market_ticks table, copied to /tmp/backtesting.db)
- Option chains: ThetaData parquet (backtest/data/cache/options_1min/)
- Upday detection: market_ticks SPX open → 14:00 rise check

CAVEATS:
- ThetaData quotes are ~65% more optimistic than Saxo (per today's earlier audit).
  So credits shown here are UPPER BOUNDS. Actual Saxo quotes would be lower.
- This is a "did it exist in the chain?" analysis — doesn't model slippage on fills.
- Buffer decay simplification: uses current buffer decay config (2.5×× × 4h)
  applied from hypothetical entry time.

Output: prints a matrix-style summary and writes detailed CSV.
"""

import sys
import os
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Optional

DB_PATH = '/tmp/backtesting.db'
CHAIN_DIR = Path('backtest/data/cache/options_1min')
SPREAD_WIDTH = 110  # MKT-027 cap at high VIX; use as default for simulation
COMMISSION_PER_LEG = 2.50
PUT_STOP_BUFFER = 175.0  # $1.75 × 100
BUFFER_DECAY_START = 2.5
BUFFER_DECAY_HOURS = 4.0

# Test grid
ENTRY_TIMES = ['12:30', '13:00', '13:30', '14:00', '14:30']  # HH:MM ET
OTM_TARGETS = [30, 45, 65, 85, 120]  # points below SPX at entry time

# Minimum credit scenarios to test (per side in $ × 100)
MIN_CREDITS = [0, 50, 100, 150, 200, 275]  # $0 removes gate, $2.75 is current default


def ms_of_day(time_str: str) -> int:
    """Convert HH:MM ET to milliseconds of day."""
    h, m = map(int, time_str.split(':'))
    return (h * 3600 + m * 60) * 1000


def round_to_5pt(strike: float) -> float:
    """Round to nearest SPX option increment."""
    return round(strike / 5) * 5


def get_spx_at_time(conn: sqlite3.Connection, date: str, time: str) -> Optional[float]:
    """Get SPX from market_ticks at or before given time."""
    row = conn.execute(
        """SELECT spx_price FROM market_ticks
           WHERE DATE(timestamp) = ? AND timestamp <= ?
           ORDER BY timestamp DESC LIMIT 1""",
        (date, f'{date} {time}:00')
    ).fetchone()
    return row[0] if row else None


def get_spx_path(conn: sqlite3.Connection, date: str, start_time: str,
                  end_time: str = '16:00:00') -> list:
    """Get all SPX ticks between start_time and end_time on given date."""
    rows = conn.execute(
        """SELECT timestamp, spx_price FROM market_ticks
           WHERE DATE(timestamp) = ? AND timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp""",
        (date, f'{date} {start_time}:00', f'{date} {end_time}')
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def get_vix_at_open(conn: sqlite3.Connection, date: str) -> Optional[float]:
    """Get VIX from daily_summaries."""
    row = conn.execute(
        "SELECT vix_open FROM daily_summaries WHERE date = ?", (date,)
    ).fetchone()
    return row[0] if row else None


def find_upday_days(conn: sqlite3.Connection, min_rise_pct: float = 0.0025) -> list:
    """Find all days where SPX rose ≥min_rise_pct from open to 14:00 ET."""
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT DATE(timestamp) FROM market_ticks ORDER BY DATE(timestamp)"
    ).fetchall()]

    uplist = []
    for date in dates:
        # Get open price (first tick at/after 9:30)
        open_row = conn.execute(
            """SELECT spx_price FROM market_ticks
               WHERE DATE(timestamp) = ? AND timestamp >= ?
               ORDER BY timestamp LIMIT 1""",
            (date, f'{date} 09:30:00')
        ).fetchone()
        if not open_row:
            continue
        spx_open = open_row[0]

        # SPX at 14:00
        spx_1400 = get_spx_at_time(conn, date, '14:00')
        if not spx_1400:
            continue

        rise_pct = (spx_1400 - spx_open) / spx_open
        if rise_pct >= min_rise_pct:
            uplist.append({
                'date': date,
                'spx_open': spx_open,
                'spx_at_1400': spx_1400,
                'rise_pct': rise_pct,
                'vix': get_vix_at_open(conn, date),
            })
    return uplist


def load_chain(date: str) -> Optional[pd.DataFrame]:
    """Load ThetaData option chain for given date."""
    date_clean = date.replace('-', '')
    path = CHAIN_DIR / f'SPXW_{date_clean}.parquet'
    if not path.exists():
        return None
    return pd.read_parquet(path)


def get_put_spread_credit(chain: pd.DataFrame, spx_ref: float, otm_points: int,
                           ms_time: int, spread_width: int = SPREAD_WIDTH) -> dict:
    """Compute put spread credit for given config at given time.

    Returns dict with short_strike, long_strike, short_bid, long_ask, credit.
    Uses ThetaData (which is ~65% optimistic vs Saxo per earlier audit).
    """
    short_strike = round_to_5pt(spx_ref - otm_points)
    long_strike = short_strike - spread_width

    # Find quotes nearest ms_time (tolerance ±60 sec)
    mask_time = (chain.ms_of_day >= ms_time - 60000) & (chain.ms_of_day <= ms_time + 60000)
    time_data = chain[mask_time]

    short = time_data[(time_data.strike == short_strike) & (time_data.right == 'P')]
    long = time_data[(time_data.strike == long_strike) & (time_data.right == 'P')]

    if short.empty or long.empty:
        return {
            'short_strike': short_strike, 'long_strike': long_strike,
            'short_bid': None, 'long_ask': None, 'credit': None, 'valid': False,
        }

    # Take the closest-to-ms entry
    short_row = short.iloc[(short.ms_of_day - ms_time).abs().argsort()[:1]].iloc[0]
    long_row = long.iloc[(long.ms_of_day - ms_time).abs().argsort()[:1]].iloc[0]

    short_bid = short_row.bid
    long_ask = long_row.ask
    credit_per_contract = short_bid - long_ask
    credit_dollars = credit_per_contract * 100  # standard SPX multiplier

    return {
        'short_strike': short_strike,
        'long_strike': long_strike,
        'short_bid': short_bid,
        'long_ask': long_ask,
        'credit_per_contract': credit_per_contract,
        'credit_dollars': credit_dollars,
        'valid': credit_dollars > 0,
    }


def simulate_outcome(conn: sqlite3.Connection, date: str, entry_time: str,
                     short_strike: float, credit_dollars: float,
                     vix: float) -> dict:
    """Simulate what the put spread would have done from entry_time to 4:00 PM.

    Returns outcome: 'expired' or 'stopped' with P&L.

    Stop logic simplified:
      - stop_level = credit + put_stop_buffer (in $)
      - Approximation: if SPX touches short_strike, spread value explodes.
      - Proxy: SPX hitting strike → stopped at credit + buffer + small slippage.
    """
    path = get_spx_path(conn, date, entry_time)
    if not path:
        return {'outcome': 'no_data', 'pnl': None}

    # Commission: entry only if expires, entry+exit if stopped
    entry_commission = 2 * COMMISSION_PER_LEG  # 2 legs × $2.50
    stop_commission = entry_commission + 2 * COMMISSION_PER_LEG

    # Buffer decay (simplified — uses starting multiplier)
    # In reality, buffer narrows over time. For simulation, use 2× buffer
    # as a conservative estimate mid-day.
    effective_buffer = PUT_STOP_BUFFER * 1.5  # approximate mid-decay
    stop_level_dollars = credit_dollars + effective_buffer

    # Check if SPX touched short strike
    spx_min = min(p[1] for p in path)
    breached = spx_min < short_strike

    if breached:
        # Stop triggered at approximately stop_level
        # Slippage estimate: +$30 worst-case on emergency close
        slippage = 30.0
        loss = stop_level_dollars - credit_dollars + slippage
        pnl = -loss - stop_commission + entry_commission  # minus entry already counted?
        # Cleaner: P&L = credit - (debit_to_close + commissions)
        pnl = credit_dollars - (stop_level_dollars + slippage) - stop_commission
        return {
            'outcome': 'stopped',
            'spx_min': spx_min,
            'breach_depth': short_strike - spx_min,
            'pnl': pnl,
        }
    else:
        # Expired worthless → keep credit - entry commission
        pnl = credit_dollars - entry_commission
        return {
            'outcome': 'expired',
            'spx_min': spx_min,
            'cushion': spx_min - short_strike,
            'pnl': pnl,
        }


def main():
    print('=' * 100)
    print('E6 UPDAY CONDITIONAL — ALTERNATIVE CONFIGURATION ANALYSIS')
    print('=' * 100)
    print()
    print('Tests: entry_time × OTM_target → outcome (expired/stopped) and P&L')
    print('Data: ThetaData option chains (CAVEAT: ~65% optimistic vs Saxo)')
    print()

    conn = sqlite3.connect(DB_PATH)

    # Find all upday days
    upday_days = find_upday_days(conn)
    print(f'Found {len(upday_days)} historical upday days (SPX rose ≥0.25% by 14:00)\n')

    # Collect results
    all_results = []

    for day in upday_days:
        date = day['date']
        chain = load_chain(date)
        if chain is None:
            print(f'  {date}: NO THETADATA CHAIN — skipping')
            continue

        for entry_time in ENTRY_TIMES:
            # SPX at this entry time (from market_ticks)
            spx_entry = get_spx_at_time(conn, date, entry_time)
            if not spx_entry:
                continue

            # Did the upday condition still hold at this time?
            rise_at_entry = (spx_entry - day['spx_open']) / day['spx_open']
            if rise_at_entry < 0.0025:
                continue  # SPX below threshold at this earlier time

            ms_time = ms_of_day(entry_time)

            for otm in OTM_TARGETS:
                quote = get_put_spread_credit(chain, spx_entry, otm, ms_time)
                if not quote['valid']:
                    continue
                if quote['credit_dollars'] <= 0:
                    continue

                outcome = simulate_outcome(
                    conn, date, entry_time,
                    quote['short_strike'], quote['credit_dollars'],
                    day['vix']
                )

                all_results.append({
                    'date': date,
                    'vix': day['vix'],
                    'rise_pct_at_entry': rise_at_entry,
                    'entry_time': entry_time,
                    'otm_target': otm,
                    'spx_entry': spx_entry,
                    'short_strike': quote['short_strike'],
                    'long_strike': quote['long_strike'],
                    'credit_$': quote['credit_dollars'],
                    'credit_per_contract': quote['credit_per_contract'],
                    'outcome': outcome.get('outcome'),
                    'pnl': outcome.get('pnl'),
                    'spx_min_after_entry': outcome.get('spx_min'),
                })

    if not all_results:
        print('No results collected')
        return

    df = pd.DataFrame(all_results)

    # Summary matrix: entry_time × OTM_target → (fires, wins, avg_pnl)
    print('=' * 100)
    print('MATRIX: entry_time × OTM_target — Average P&L across all upday days')
    print('=' * 100)
    print()

    matrix_avg = df.pivot_table(
        index='entry_time', columns='otm_target', values='pnl',
        aggfunc='mean', observed=True,
    ).round(0)
    print('Average P&L per fire:')
    print(matrix_avg.to_string())
    print()

    matrix_count = df.pivot_table(
        index='entry_time', columns='otm_target', values='date',
        aggfunc='count', observed=True,
    )
    print('N (days with tradeable credit):')
    print(matrix_count.to_string())
    print()

    # Win rate
    df['is_win'] = df['outcome'] == 'expired'
    matrix_wr = df.pivot_table(
        index='entry_time', columns='otm_target', values='is_win',
        aggfunc=lambda x: 100 * x.sum() / max(len(x), 1),
        observed=True,
    ).round(0)
    print('Win rate % (expired worthless):')
    print(matrix_wr.to_string())
    print()

    # Total P&L across all days
    matrix_total = df.pivot_table(
        index='entry_time', columns='otm_target', values='pnl',
        aggfunc='sum', observed=True,
    ).round(0)
    print('Total P&L (sum across all days):')
    print(matrix_total.to_string())
    print()

    # Credit gate impact
    print('=' * 100)
    print('CREDIT GATE IMPACT — What fires at each minimum credit threshold?')
    print('=' * 100)
    print()
    for min_cred in MIN_CREDITS:
        gated = df[df['credit_$'] >= min_cred]
        gated_expired = gated[gated['outcome'] == 'expired']
        gated_stopped = gated[gated['outcome'] == 'stopped']
        if len(gated) == 0:
            print(f'  min_credit ${min_cred:.0f}: 0 fires (all filtered)')
            continue
        total_pnl = gated['pnl'].sum()
        print(f'  min_credit ${min_cred:>3.0f}: {len(gated):>3} fires | '
              f'{len(gated_expired):>2} expired + {len(gated_stopped):>2} stopped | '
              f'total ${total_pnl:>+7.0f} | avg ${total_pnl/len(gated):>+5.0f}/fire')

    # Best config
    print('\n' + '=' * 100)
    print('BEST CONFIG by metric')
    print('=' * 100)
    best_total = matrix_total.stack().idxmax()
    best_avg = matrix_avg.stack().idxmax()
    best_wr = matrix_wr.stack().idxmax()
    print(f'Best TOTAL P&L: entry_time={best_total[0]}, OTM={best_total[1]}pt → ${matrix_total.loc[best_total[0], best_total[1]]:+.0f}')
    print(f'Best AVG P&L:   entry_time={best_avg[0]}, OTM={best_avg[1]}pt → ${matrix_avg.loc[best_avg[0], best_avg[1]]:+.0f}/fire')
    print(f'Best WIN RATE:  entry_time={best_wr[0]}, OTM={best_wr[1]}pt → {matrix_wr.loc[best_wr[0], best_wr[1]]:.0f}%')

    # Per-day detail for the current config (14:00 × 65pt)
    print('\n' + '=' * 100)
    print('CURRENT E6 CONFIG (14:00 × 65pt OTM) — per-day detail')
    print('=' * 100)
    current = df[(df['entry_time'] == '14:00') & (df['otm_target'] == 65)].copy()
    if len(current) > 0:
        print(current[['date', 'vix', 'short_strike', 'credit_$', 'outcome', 'pnl']].to_string(index=False))
    else:
        print('No fires in current config (credit gate filtered all)')

    # Save full detail
    output_csv = '/tmp/e6_alternatives_analysis.csv'
    df.to_csv(output_csv, index=False)
    print(f'\nFull detail saved to: {output_csv}')
    print(f'Total rows: {len(df)}')

    conn.close()


if __name__ == '__main__':
    main()
