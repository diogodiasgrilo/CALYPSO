"""
Calibrate slippage on 6 days (Mar 31 - Apr 8) using 5-sec data.
Run: python -m backtest.calibrate_6day
"""
from backtest.config import live_config
from backtest.engine import run_backtest
from datetime import date
from concurrent.futures import ProcessPoolExecutor, as_completed

LIVE = {
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
    cfg.data_resolution = "5sec"
    cfg.use_real_greeks = True
    cfg.start_date = date(2026, 3, 31)
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
    print(f"Running {len(combos)} combos on 6 days (5-sec data), 8 workers...")

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
    print(f"  CALIBRATION: 6 days (Mar 31 - Apr 8)")
    print(f"{'='*90}")
    print(f"\n  Top 20 by total error:")
    print(f"  {'Slip':>6} {'Markup':>7} {'6d Err':>8} {'BT Tot':>9} {'Live':>9} {'Diff':>8} {'Stops':>6}")
    print(f"  {'-'*57}")
    for r in all_results[:20]:
        print(f"  ${r['slip']/100:.2f}  {r['markup']*100:>5.0f}%  ${r['total_error']:>7,.0f}  {r['bt_total']:>+9,.0f}  {r['live_total']:>9,.0f}  {r['bt_total']-r['live_total']:>+8,.0f}  {r['total_stops']:>6}")

    # Show 3-day winner on 6 days
    three_day = next((r for r in all_results if r["slip"] == 35.0 and r["markup"] == 0.10), None)
    six_day = all_results[0]

    print(f"\n  3-day winner (slip=$0.35, mk=10%): 6-day error = ${three_day['total_error']:,.0f}" if three_day else "")
    print(f"  6-day winner (slip=${six_day['slip']/100:.2f}, mk={six_day['markup']*100:.0f}%): 6-day error = ${six_day['total_error']:,.0f}")

    # Per-day for 6-day winner
    print(f"\n  Per-day breakdown (6-day winner):")
    print(f"  {'Date':<12} {'Live':>8} {'BT':>8} {'Diff':>8} {'Stops':>6}")
    print(f"  {'-'*44}")
    for d in sorted(six_day["details"].keys()):
        bt, live, err, stops = six_day["details"][d]
        print(f"  {d:<12} {live:>8,.0f} {bt:>8,.0f} {bt-live:>+8,.0f} {stops:>6}")

    # Per-day for 3-day winner
    if three_day:
        print(f"\n  Per-day breakdown (3-day winner on 6 days):")
        print(f"  {'Date':<12} {'Live':>8} {'BT':>8} {'Diff':>8} {'Stops':>6}")
        print(f"  {'-'*44}")
        for d in sorted(three_day["details"].keys()):
            bt, live, err, stops = three_day["details"][d]
            print(f"  {d:<12} {live:>8,.0f} {bt:>8,.0f} {bt-live:>+8,.0f} {stops:>6}")

    # No slippage baseline
    baseline = next((r for r in all_results if r["slip"] == 0.0 and r["markup"] == 0.0), None)
    if baseline:
        print(f"\n  No slippage baseline: 6-day error = ${baseline['total_error']:,.0f}")
