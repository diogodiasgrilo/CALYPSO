"""
Compare new optimal config vs old live config for March 12-23, 2026.
Also shows actual live trading results from the VM's SQLite DB.

Run: python -m backtest.compare_march
"""
from datetime import date
from backtest.engine import run_backtest
from backtest.config import BacktestConfig

# ── Actual results from VM SQLite DB (daily_summaries, queried 2026-03-24) ──
ACTUAL = [
    {"date": "2026-03-12", "net_pnl": 525.0,   "entries": 5, "stops": 0, "vix": 26.09},
    {"date": "2026-03-13", "net_pnl": 385.0,   "entries": 7, "stops": 1, "vix": 25.53},
    {"date": "2026-03-16", "net_pnl": 950.0,   "entries": 3, "stops": 0, "vix": 25.18},
    {"date": "2026-03-17", "net_pnl": 120.0,   "entries": 2, "stops": 0, "vix": 22.67},
    {"date": "2026-03-18", "net_pnl": 135.0,   "entries": 3, "stops": 0, "vix": 22.37},
    {"date": "2026-03-19", "net_pnl": -1985.0, "entries": 7, "stops": 6, "vix": 27.03},
    {"date": "2026-03-20", "net_pnl": -1635.0, "entries": 5, "stops": 3, "vix": 24.74},
    {"date": "2026-03-23", "net_pnl": -935.0,  "entries": 6, "stops": 6, "vix": 24.45},
]
ACTUAL_DATES = {r["date"] for r in ACTUAL}

# ── Old config (live on VM before 2026-03-24 changes) ───────────────────────
def old_config() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2026, 3, 12),
        end_date=date(2026, 3, 23),
        entry_times=["10:15", "10:45", "11:15", "11:45", "12:15"],
        conditional_e6_enabled=False,
        conditional_e7_enabled=True,
        conditional_upday_e6_enabled=True,
        conditional_upday_e7_enabled=False,
        downday_threshold_pct=0.30,
        upday_threshold_pct=0.40,          # old
        base_entry_downday_callonly_pct=0.40,  # old
        downday_theoretical_put_credit=500.0,  # $5.00 × 100 (old VM value)
        upday_theoretical_call_credit=0,
        fomc_t1_callonly_enabled=True,
        min_call_credit=1.25,
        min_put_credit=2.25,               # old VM value
        put_stop_buffer=100.0,
        stop_buffer=10.0,
        one_sided_entries_enabled=True,
        put_only_max_vix=25.0,
        price_based_stop_points=None,      # credit-based stop
        stop_slippage_per_leg=0.0,
        target_delta=8.0,
    )

# ── New config (all optimisations from 2026-03-24 sweep) ────────────────────
def new_config() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2026, 3, 12),
        end_date=date(2026, 3, 23),
        entry_times=["10:15", "10:45", "11:15", "11:45", "12:15"],
        conditional_e6_enabled=False,
        conditional_e7_enabled=True,
        conditional_upday_e6_enabled=True,
        conditional_upday_e7_enabled=False,
        downday_threshold_pct=0.30,
        upday_threshold_pct=0.60,          # new
        base_entry_downday_callonly_pct=0.30,  # new
        downday_theoretical_put_credit=150.0,  # $1.50 × 100 (backtest optimal)
        upday_theoretical_call_credit=0,
        fomc_t1_callonly_enabled=False,
        min_call_credit=1.25,
        min_put_credit=1.75,               # new (backtest optimal)
        put_stop_buffer=100.0,
        stop_buffer=10.0,
        one_sided_entries_enabled=True,
        put_only_max_vix=25.0,
        price_based_stop_points=0.1,       # new price-based stop
        stop_slippage_per_leg=0.0,
        target_delta=8.0,
    )


def run_and_index(cfg: BacktestConfig):
    results = run_backtest(cfg)
    return {str(r.date): r for r in results if str(r.date) in ACTUAL_DATES}


if __name__ == "__main__":
    print("Running OLD config backtest (March 12-23)...")
    old_days = run_and_index(old_config())

    print("Running NEW config backtest (March 12-23)...")
    new_days = run_and_index(new_config())

    print()
    print(f"{'Date':<12} {'Actual':>10} {'Old BT':>10} {'New BT':>10}  {'Act Stops':>10} {'Old Stops':>10} {'New Stops':>10}  {'VIX':>6}")
    print("─" * 90)

    actual_total = 0
    old_total = 0
    new_total = 0
    actual_stops = 0
    old_stops = 0
    new_stops = 0

    for row in ACTUAL:
        d = row["date"]
        actual_pnl = row["net_pnl"]
        actual_total += actual_pnl

        old_r = old_days.get(d)
        new_r = new_days.get(d)

        old_pnl = old_r.net_pnl if old_r else 0
        new_pnl = new_r.net_pnl if new_r else 0
        old_total += old_pnl
        new_total += new_pnl

        old_s = sum(1 for e in old_r.entries if e.call_outcome == "stopped" or e.put_outcome == "stopped") if old_r else 0
        new_s = sum(1 for e in new_r.entries if e.call_outcome == "stopped" or e.put_outcome == "stopped") if new_r else 0
        actual_stops += row["stops"]
        old_stops += old_s
        new_stops += new_s

        print(
            f"{d:<12} {actual_pnl:>+10.0f} {old_pnl:>+10.0f} {new_pnl:>+10.0f}  "
            f"{row['stops']:>10} {old_s:>10} {new_s:>10}  {row['vix']:>6.2f}"
        )

    print("─" * 90)
    print(
        f"{'TOTAL':<12} {actual_total:>+10.0f} {old_total:>+10.0f} {new_total:>+10.0f}  "
        f"{actual_stops:>10} {old_stops:>10} {new_stops:>10}"
    )
    print()
    print(f"  Actual live P&L:  ${actual_total:+,.0f}")
    print(f"  Old config BT:    ${old_total:+,.0f}  (diff vs actual: ${old_total - actual_total:+,.0f})")
    print(f"  New config BT:    ${new_total:+,.0f}  (diff vs actual: ${new_total - actual_total:+,.0f})")
    print(f"  New vs Old:       ${new_total - old_total:+,.0f}")
