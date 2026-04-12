"""
Calibrate slippage on ~20 days (Mar 10 - Apr 8) using 1-min data.
Compares to 3-day and 6-day calibration results.

Run: python -m backtest.calibrate_20day
"""
from backtest.config import live_config
from backtest.engine import run_backtest
from datetime import date
from concurrent.futures import ProcessPoolExecutor, as_completed

# All days with Sheets P&L (source of truth from HOMER DB)
LIVE = {
    "2026-03-10": -585,
    "2026-03-11": -310,
    "2026-03-12": 525,
    "2026-03-13": 385,
    "2026-03-16": 950,
    "2026-03-17": 120,
    "2026-03-18": 135,
    "2026-03-19": -1985,
    "2026-03-20": -1635,
    "2026-03-23": -935,
    "2026-03-31": -15,
    "2026-04-01": 330,
    "2026-04-02": 1055,
    "2026-04-06": 1475,
    "2026-04-07": -1100,
    "2026-04-08": -430,
}

SLIPPAGE = [0.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 60.0, 75.0, 100.0]
MARKUPS  = [0.0, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15]


def _run(args):
    slip, markup = args
    cfg = live_config()
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.start_date = date(2026, 3, 10)
    cfg.end_date = date(2026, 4, 8)
    cfg.stop_slippage_per_leg = slip
    cfg.stop_spread_markup_pct = markup

    results = run_backtest(cfg, verbose=False)

    total_error = 0
    bt_total = 0
    details = {}
    for r in results:
        d = r.date.isoformat()
        if d in LIVE:
            err = abs(r.net_pnl - LIVE[d])
            total_error += err
            bt_total += r.net_pnl
            details[d] = (r.net_pnl, LIVE[d], err, r.stops_hit)

    return {
        "slip": slip, "markup": markup,
        "total_error": total_error, "bt_total": bt_total,
        "live_total": sum(LIVE[d] for d in details),
        "total_stops": sum(r.stops_hit for r in results),
        "days": len(details), "details": details,
    }


if __name__ == "__main__":
    combos = [(s, m) for s in SLIPPAGE for m in MARKUPS]
    print(f"Running {len(combos)} combos on ~20 days (1-min data), 8 workers...")

    all_results = []
    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_run, c): c for c in combos}
        done = 0
        for f in as_completed(futures):
            r = f.result()
            all_results.append(r)
            done += 1
            if done % 20 == 0 or done == len(combos):
                print(f"  [{done}/{len(combos)}]")

    all_results.sort(key=lambda x: x["total_error"])

    print(f"\n{'='*90}")
    print(f"  CALIBRATION: {all_results[0]['days']} days (Mar 10 - Apr 8, 1-min data)")
    print(f"{'='*90}")
    print(f"\n  Top 20 by total error:")
    print(f"  {'Slip':>6} {'Markup':>7} {'Error':>9} {'BT Tot':>10} {'Live':>10} {'Diff':>9} {'Stops':>6}")
    print(f"  {'-'*60}")
    for r in all_results[:20]:
        diff = r["bt_total"] - r["live_total"]
        print(f"  ${r['slip']/100:.2f}  {r['markup']*100:>5.0f}%  ${r['total_error']:>8,.0f}  {r['bt_total']:>+10,.0f}  {r['live_total']:>10,.0f}  {r['bt_total']-r['live_total']:>+9,.0f}  {r['total_stops']:>6}")

    winner = all_results[0]
    baseline = next((r for r in all_results if r["slip"] == 0.0 and r["markup"] == 0.0), None)
    three_day = next((r for r in all_results if r["slip"] == 35.0 and r["markup"] == 0.10), None)
    six_day = next((r for r in all_results if r["slip"] == 35.0 and r["markup"] == 0.12), None)

    print(f"\n  COMPARISON ACROSS CALIBRATION WINDOWS:")
    print(f"  {'Config':<30} {'Error':>9} {'Avg/Day':>9}")
    print(f"  {'-'*50}")
    if baseline:
        print(f"  {'No slippage':<30} ${baseline['total_error']:>8,.0f}  ${baseline['total_error']/baseline['days']:>8,.0f}")
    if three_day:
        print(f"  {'3-day winner ($0.35, 10%)':<30} ${three_day['total_error']:>8,.0f}  ${three_day['total_error']/three_day['days']:>8,.0f}")
    if six_day:
        print(f"  {'6-day winner ($0.35, 12%)':<30} ${six_day['total_error']:>8,.0f}  ${six_day['total_error']/six_day['days']:>8,.0f}")
    wlabel = "20d win (${:.2f}, {:.0f}%)".format(winner['slip']/100, winner['markup']*100)
    print(f"  {wlabel:<30} ${winner['total_error']:>8,.0f}  ${winner['total_error']/winner['days']:>8,.0f}")

    # Per-day for 20-day winner
    print(f"\n  Per-day (20-day winner: slip=${winner['slip']/100:.2f}, mk={winner['markup']*100:.0f}%):")
    print(f"  {'Date':<12} {'Live':>8} {'BT':>8} {'Diff':>8} {'AbsErr':>8} {'Stops':>6}")
    print(f"  {'-'*52}")
    for d in sorted(winner["details"].keys()):
        bt, live, err, stops = winner["details"][d]
        print(f"  {d:<12} {live:>8,.0f} {bt:>8,.0f} {bt-live:>+8,.0f} {err:>8,.0f} {stops:>6}")
