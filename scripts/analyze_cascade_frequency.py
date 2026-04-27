#!/usr/bin/env python3
"""Analyze cascade-event frequency from backtest output.

Cascade-exit savings cannot be directly measured in the backtest because
the engine does not model MKT-046's 10s confirmation wait (stops fire at
the 1-min bar when trigger is breached, with constant slippage). What we
CAN measure: how often same-side cascades happen historically.

Combined with the per-cascade savings estimate from live data
(~$100-150/spread on cascaded stops), this gives an honest annualized
projection of cascade-exit value.

Cascade definition (same as the proposed live rule):
  - >=2 stops on the SAME side
  - within <= 5 minutes of each other
  - same trading day

Per-cascade savings assumption (from live MKT-046 data Apr 17-24):
  - Average slippage on cascade-second stops: $1.65-$2.75 per spread
  - Cascade exit skips the 10s wait → saves ~$1.00-$1.50 per spread
  - Conservative midpoint: $115 saved per cascade-second stop
"""
import sys
import time
from datetime import date, datetime
from pathlib import Path
from dataclasses import replace
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import live_config
from backtest.engine import run_backtest

# Per-cascade savings range (from live data observation)
SAVING_PER_CASCADE_LOW = 80
SAVING_PER_CASCADE_MID = 115
SAVING_PER_CASCADE_HIGH = 175

# Cascade window: stops within this many seconds count as cascaded
CASCADE_WINDOW_SECONDS = 300  # 5 minutes


def main():
    # Run backtest with live config over the available history
    cfg = replace(
        live_config(),
        start_date=date(2024, 1, 1),
        end_date=date(2026, 4, 24),
    )

    print("=" * 90)
    print("CASCADE FREQUENCY ANALYSIS")
    print(f"Period: {cfg.start_date} → {cfg.end_date}")
    print(f"Cascade window: {CASCADE_WINDOW_SECONDS}s ({CASCADE_WINDOW_SECONDS/60:.0f} min)")
    print("=" * 90)
    print(f"\nRunning backtest...")

    t0 = time.time()
    results = run_backtest(cfg, verbose=False)
    print(f"  done in {time.time()-t0:.1f}s — {len(results)} trading days\n")

    # Walk every day; collect stops with timestamps
    total_stops = 0
    total_call_stops = 0
    total_put_stops = 0
    cascade_events = []  # list of (date, side, n_stops_in_cascade, gap_minutes)
    cascade_second_stops = 0  # the "follow-on" stops within a cascade

    for day in results:
        # Build per-side ordered list of (stop_time_ms, entry_num)
        call_stops = []
        put_stops = []
        for e in day.entries:
            if e.entry_type == "skipped":
                continue
            if e.call_outcome == "stopped" and e.call_exit_ms:
                call_stops.append((e.call_exit_ms, e.entry_num))
                total_call_stops += 1
                total_stops += 1
            if e.put_outcome == "stopped" and e.put_exit_ms:
                put_stops.append((e.put_exit_ms, e.entry_num))
                total_put_stops += 1
                total_stops += 1

        # Detect cascades on each side independently
        for side, stops in (("call", call_stops), ("put", put_stops)):
            stops.sort()
            cluster = [stops[0]] if stops else []
            for i in range(1, len(stops)):
                prev_ms = stops[i-1][0]
                this_ms = stops[i][0]
                gap_s = (this_ms - prev_ms) / 1000.0
                if gap_s <= CASCADE_WINDOW_SECONDS:
                    cluster.append(stops[i])
                else:
                    # End previous cluster — if it's >=2, record cascade
                    if len(cluster) >= 2:
                        gap_min = (cluster[-1][0] - cluster[0][0]) / 1000 / 60
                        cascade_events.append((day.date, side, len(cluster), gap_min))
                        cascade_second_stops += len(cluster) - 1
                    cluster = [stops[i]]
            if len(cluster) >= 2:
                gap_min = (cluster[-1][0] - cluster[0][0]) / 1000 / 60
                cascade_events.append((day.date, side, len(cluster), gap_min))
                cascade_second_stops += len(cluster) - 1

    # Aggregate stats
    days_with_cascades = len(set(e[0] for e in cascade_events))
    days_with_stops = sum(
        1 for day in results
        if any(e.call_outcome == "stopped" or e.put_outcome == "stopped"
               for e in day.entries)
    )
    period_months = (cfg.end_date - cfg.start_date).days / 30.4

    print("=" * 90)
    print("RESULTS")
    print("=" * 90)
    print(f"Total trading days analyzed:       {len(results)}")
    print(f"Total stops fired:                 {total_stops}  ({total_call_stops} call, {total_put_stops} put)")
    print(f"Days with at least one stop:       {days_with_stops}")
    print(f"Days with at least one cascade:    {days_with_cascades}")
    print(f"Total cascade events:              {len(cascade_events)}")
    print(f"Total cascade-follow-on stops:     {cascade_second_stops}  (the stops that cascade-exit would target)")
    print()

    # Frequency
    cascade_rate_per_month = len(cascade_events) / period_months
    follow_on_rate_per_month = cascade_second_stops / period_months
    cascade_pct_of_stop_days = 100 * days_with_cascades / max(days_with_stops, 1)

    print(f"Cascades per month (avg):          {cascade_rate_per_month:.2f}")
    print(f"Cascade-follow-on stops per month: {follow_on_rate_per_month:.2f}")
    print(f"% of stop-days that had cascades:  {cascade_pct_of_stop_days:.1f}%")
    print()

    # Annualized savings projection
    print("=" * 90)
    print("PROJECTED ANNUAL SAVINGS")
    print("=" * 90)
    follow_on_per_year = follow_on_rate_per_month * 12
    print(f"Cascade-follow-on stops per year:  {follow_on_per_year:.1f}\n")
    for label, sav in [("Conservative", SAVING_PER_CASCADE_LOW),
                       ("Midpoint",     SAVING_PER_CASCADE_MID),
                       ("Optimistic",   SAVING_PER_CASCADE_HIGH)]:
        annual_1c = follow_on_per_year * sav
        annual_2c = annual_1c * 2
        print(f"  {label:<13} (${sav}/cascade): "
              f"${annual_1c:>7,.0f}/yr at 1c   ${annual_2c:>7,.0f}/yr at 2c")
    print()

    # Distribution of cascade sizes
    print("=" * 90)
    print("CASCADE SIZE DISTRIBUTION")
    print("=" * 90)
    size_dist = defaultdict(int)
    for ev in cascade_events:
        size_dist[ev[2]] += 1
    for size in sorted(size_dist):
        print(f"  {size}-stop cascades: {size_dist[size]}")
    print()

    # Sample of recent cascades
    print("=" * 90)
    print("MOST RECENT 10 CASCADES")
    print("=" * 90)
    for ev in sorted(cascade_events, key=lambda x: x[0])[-10:]:
        d, side, n, gap_m = ev
        print(f"  {d}  side={side:<4}  n={n}  gap={gap_m:>5.1f} min")


if __name__ == "__main__":
    main()
