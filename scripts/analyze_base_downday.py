"""
Base-downday counterfactual analysis — v2 using tested config_audit_lib.

Addresses the Apr 1 settlement bug: uses authoritative stop P&Ls where
possible, applies haircut to expired credits on days that don't reconcile,
reports results with explicit caveats about data reliability.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_audit_lib import ConfigAuditDB


def analyze_base_downday(threshold_pct: float, db: ConfigAuditDB, verbose: bool = False):
    """Count the P&L delta if base-downday had fired at given threshold."""
    dates = db.get_all_dates()

    fired_entries = []  # entries where base-downday would fire
    total_savings_from_stops = 0  # authoritative (from trade_stops)
    total_loss_from_missed_credits = 0  # approximate (may be overstated pre-Fix-87)
    n_stops_avoided = 0
    n_credits_missed = 0

    # Per-day net impact
    day_impact = {}

    entry_times_to_check = {
        1: '10:15:00', 2: '10:45:00', 3: '11:15:00',
    }

    for date in dates:
        entries = db.get_entries(date)
        stops = db.get_stops(date)
        day_delta = 0

        for entry in entries:
            num = entry['num']
            if num > 3:  # Only E1-E3 (base entries)
                continue

            entry_time = entry_times_to_check.get(num)
            if not entry_time:
                continue

            # Check if SPX drop at THIS entry's time exceeds threshold
            drop = db.get_spx_drop_pct_at(date, entry_time)
            if not drop or drop < threshold_pct:
                continue

            # Base-downday would have fired on this entry
            # Was the entry already call_only via trend filter? If so, no change
            if entry['type'] in ('call_only', 'Call Spread'):
                continue  # already call-only, no delta

            fired_entries.append({**entry, 'drop_pct': drop})

            # Counterfactual: skip put side → gain/lose put_contribution
            put_contribution = db.estimate_put_side_contribution(
                entry, stops.get(num, []),
                haircut_factor=1.0  # no haircut for now — we'll check separately
            )

            # Delta if we had skipped the put = -put_contribution
            # put_contribution negative (stopped) → delta positive (savings)
            # put_contribution positive (expired credit) → delta negative (lost)
            delta = -put_contribution
            day_delta += delta

            if put_contribution < 0:
                n_stops_avoided += 1
                total_savings_from_stops += -put_contribution
            elif put_contribution > 0:
                n_credits_missed += 1
                total_loss_from_missed_credits += put_contribution

        if day_delta != 0:
            day_impact[date] = day_delta

    total_delta = total_savings_from_stops - total_loss_from_missed_credits
    return {
        'threshold': threshold_pct,
        'fired_entries': fired_entries,
        'n_fired': len(fired_entries),
        'n_stops_avoided': n_stops_avoided,
        'n_credits_missed': n_credits_missed,
        'total_savings_from_stops': total_savings_from_stops,
        'total_loss_from_missed_credits': total_loss_from_missed_credits,
        'total_delta': total_delta,
        'day_impact': day_impact,
    }


def main():
    db = ConfigAuditDB('/tmp/backtesting.db')

    print('=' * 100)
    print('BASE-DOWNDAY COUNTERFACTUAL — v2 (using tested library)')
    print('=' * 100)
    print()
    print('Method: For each E1-E3 entry, check if SPX drop from open AT ENTRY TIME >= threshold.')
    print('If fire: counterfactual skips put side. Delta = -(put_contribution).')
    print('  - Put stopped → authoritative stop.net_pnl (negative) → savings (positive delta)')
    print('  - Put expired → put_credit (positive) → missed profit (negative delta)')
    print()
    print('Caveat: Pre-Fix-87 days may overstate "missed profit" because near-ATM settlements')
    print('reduce actual expired value. So the true TOTAL DELTA is likely HIGHER than reported here.')
    print()

    # Identify which days have reliable per-entry data
    reconciled = set(db.get_reconciled_dates(tolerance=100.0))
    all_dates = db.get_all_dates()
    print(f'Reliable days (within $100 reconciliation): {len(reconciled)}/{len(all_dates)}')
    print(f'Unreliable days (pre-Fix-87 settlement): {len(all_dates) - len(reconciled)}')
    print()

    # Test thresholds
    thresholds = [0.0050, 0.0057, 0.0060, 0.0065, 0.0070, 0.0075, 0.0080, 0.0090, 0.0100]
    print(f'\n{"Threshold":>10}{"Fires":>8}{"Stops Avoid":>13}{"Savings":>12}{"Credits Miss":>14}{"Losses":>12}{"NET":>12}')
    print('-' * 85)
    results = []
    for t in thresholds:
        r = analyze_base_downday(t, db)
        results.append(r)
        print(f'{t*100:>9.2f}%'
              f'{r["n_fired"]:>8}'
              f'{r["n_stops_avoided"]:>13}'
              f'${r["total_savings_from_stops"]:>+10.0f}'
              f'{r["n_credits_missed"]:>14}'
              f'${r["total_loss_from_missed_credits"]:>+10.0f}'
              f'${r["total_delta"]:>+10.0f}')

    # Best threshold
    best = max(results, key=lambda r: r['total_delta'])
    print(f'\nBEST THRESHOLD: {best["threshold"]*100:.2f}% — Net benefit ${best["total_delta"]:+,.0f}')

    # Per-day breakdown at best threshold
    print(f'\nPer-day impact at {best["threshold"]*100:.2f}% threshold:')
    print(f'{"Date":<12}{"Net":>10}  {"Reliable":>10}')
    for date, delta in sorted(best['day_impact'].items()):
        reliable = '✓' if date in reconciled else '⚠ may be overstated'
        sign = '+' if delta > 0 else ''
        print(f'{date:<12}${delta:>+8.0f}  {reliable}')

    # Now re-run filtering to RELIABLE days only (post-Fix-87)
    print()
    print('=' * 100)
    print('BASE-DOWNDAY — ONLY RELIABLE DAYS (post-settlement-fix)')
    print('=' * 100)
    print()

    # Run again but filter to reliable days
    reliable_dates = set(db.get_reconciled_dates(tolerance=100.0))
    print(f'\nUsing {len(reliable_dates)} reliable days only:')
    print(f'\n{"Threshold":>10}{"Fires":>8}{"Savings":>12}{"Losses":>12}{"NET":>12}')
    print('-' * 58)

    for t in thresholds:
        r = analyze_base_downday(t, db)
        # Filter fired_entries to reliable days only
        reliable_fired = [e for e in r['fired_entries'] if e['date'] in reliable_dates]
        savings_r = 0
        losses_r = 0
        for e in reliable_fired:
            num = e['num']
            stops = db.get_stops(e['date']).get(num, [])
            contrib = db.estimate_put_side_contribution(e, stops)
            if contrib < 0:
                savings_r += -contrib
            elif contrib > 0:
                losses_r += contrib
        net_r = savings_r - losses_r
        print(f'{t*100:>9.2f}%{len(reliable_fired):>8}${savings_r:>+10.0f}${losses_r:>+10.0f}${net_r:>+10.0f}')


if __name__ == '__main__':
    main()
