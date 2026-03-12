#!/usr/bin/env python3
"""
MKT-036 Complete Timer Analysis — Full Timer Simulation

Simulates the ACTUAL MKT-036 timer behavior tick by tick:
  - Breach detected → timer starts
  - If spread recovers (SPX bounces favorably >= threshold) → timer RESETS
  - If SPX moves adversely again → timer RESTARTS from 0
  - If breach sustains for T consecutive seconds → stop FIRES (delayed)
  - If stop never fires within window → entry survives or held to settlement

This replaces the old "one reset = missed forever" model. In reality, after a
timer reset, the stop will re-trigger on continued adverse movement and fire
after another T seconds of sustained breach.

Three cost/benefit outcomes per stop:
  1. FIRES (possibly delayed): delay_cost = adverse_SPX_move(fire_time) * delta * 100
  2. SAVED (false stop, never re-fires): savings = |pnl| + credit
  3. MISSED TO SETTLEMENT (true stop, never re-fires): settle loss

Run on VM: cd /opt/calypso && .venv/bin/python scripts/mkt036_timer_analysis.py
"""
import sqlite3
from datetime import datetime
from collections import defaultdict

DB_PATH = "/opt/calypso/data/backtesting.db"
MAX_WINDOW = 2000      # seconds of tick data (33 min — enough for timer + multiple resets)
SPREAD_WIDTH = 50      # points

THRESHOLDS = [1.0, 2.0, 3.0]
THRESHOLD_LABELS = {1.0: "1pt (conservative)", 2.0: "2pt (moderate)", 3.0: "3pt (strict)"}

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: Load all data
# ═══════════════════════════════════════════════════════════════════════════════

cur.execute("SELECT date, net_pnl, spx_close FROM daily_summaries ORDER BY date")
daily = {}
for row in cur.fetchall():
    daily[row[0]] = {"net_pnl": row[1], "spx_close": row[2]}

cur.execute("""
    SELECT date, entry_number, short_call_strike, short_put_strike,
           total_credit, call_credit, put_credit
    FROM trade_entries ORDER BY date, entry_number
""")
entries = {}
for row in cur.fetchall():
    entries[(row[0], row[1])] = {
        "sc": row[2], "sp": row[3], "tc": row[4],
        "cc": row[5] or 0, "pc": row[6] or 0
    }

cur.execute("""
    SELECT date, entry_number, side, stop_time, spx_at_stop,
           trigger_level, actual_debit, net_pnl
    FROM trade_stops ORDER BY date, stop_time
""")
raw_stops = cur.fetchall()

dates = sorted(daily.keys())
total_days = len(dates)
cum_actual = sum(daily[d]["net_pnl"] for d in dates)

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Pre-compute per-stop data + breach intervals
# ═══════════════════════════════════════════════════════════════════════════════

stops = []
skipped = 0

for raw in raw_stops:
    date, enum, side, stop_time, spx_at_stop, trigger, debit, pnl = raw
    entry = entries.get((date, enum), {})
    spx_close = daily.get(date, {}).get("spx_close")

    if side == "put":
        short_strike = entry.get("sp")
        credit = entry.get("pc", 0)
    else:
        short_strike = entry.get("sc")
        credit = entry.get("cc", 0)

    if not short_strike or not spx_close or not spx_at_stop or spx_at_stop == 0:
        skipped += 1
        continue

    # Classify: false = SPX closed OTM (stop was unnecessary)
    if side == "put":
        is_false = spx_close > short_strike
    else:
        is_false = spx_close < short_strike

    # Settlement P&L if held
    if side == "put":
        itm_amount = max(0, short_strike - spx_close)
    else:
        itm_amount = max(0, spx_close - short_strike)
    settle_value = min(itm_amount, SPREAD_WIDTH) * 100
    settle_pnl = credit - settle_value
    missed_cost = pnl - settle_pnl

    # OTM distance + delta estimate
    if side == "put":
        otm_dist = spx_at_stop - short_strike
    else:
        otm_dist = short_strike - spx_at_stop

    if otm_dist > 40:
        delta_est = 0.10
    elif otm_dist > 30:
        delta_est = 0.15
    elif otm_dist > 20:
        delta_est = 0.25
    elif otm_dist > 10:
        delta_est = 0.35
    else:
        delta_est = 0.45

    # Fetch tick data
    cur.execute(
        "SELECT timestamp, spx_price FROM market_ticks "
        "WHERE substr(timestamp, 1, 10) = ? "
        "AND timestamp >= ? AND timestamp <= datetime(?, '+{} seconds') "
        "ORDER BY timestamp".format(MAX_WINDOW + 60),
        (date, stop_time, stop_time)
    )
    stop_dt = datetime.strptime(stop_time, "%Y-%m-%d %H:%M:%S")
    ticks = []
    for ts, px in cur.fetchall():
        tick_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        delta_s = (tick_dt - stop_dt).total_seconds()
        if 0 <= delta_s <= MAX_WINDOW:
            ticks.append((int(delta_s), px))

    # Build spx_at[s]: forward-filled SPX price at each second
    spx_at = [spx_at_stop] * (MAX_WINDOW + 1)
    last_px = spx_at_stop
    tick_ptr = 0
    for s in range(MAX_WINDOW + 1):
        while tick_ptr < len(ticks) and ticks[tick_ptr][0] <= s:
            last_px = ticks[tick_ptr][1]
            tick_ptr += 1
        spx_at[s] = last_px

    # Compute favorable_move at each second (threshold-independent)
    # Positive = SPX moved in favorable direction (away from short strike)
    favorable_at = [0.0] * (MAX_WINDOW + 1)
    for s in range(MAX_WINDOW + 1):
        if side == "put":
            favorable_at[s] = spx_at[s] - spx_at_stop  # positive = SPX went up
        else:
            favorable_at[s] = spx_at_stop - spx_at[s]  # positive = SPX went down

    # Pre-compute breach intervals for each threshold
    # An interval is (start_sec, end_sec) where the stop is breached
    # "breached" = favorable_move < threshold (SPX hasn't recovered enough)
    breach_intervals = {}
    for thresh in THRESHOLDS:
        intervals = []
        breach_start = 0  # breach starts at second 0 (the stop just triggered)
        for s in range(1, MAX_WINDOW + 1):
            recovered = favorable_at[s] >= thresh
            if recovered and breach_start is not None:
                intervals.append((breach_start, s))
                breach_start = None
            elif not recovered and breach_start is None:
                breach_start = s  # re-breach
        # If still breached at end of window
        if breach_start is not None:
            intervals.append((breach_start, MAX_WINDOW))
        breach_intervals[thresh] = intervals

    savings = abs(pnl) + credit

    stops.append({
        "date": date, "entry": enum, "side": side,
        "spx_at_stop": spx_at_stop, "short_strike": short_strike,
        "spx_close": spx_close, "credit": credit,
        "pnl": pnl, "is_false": is_false,
        "settle_pnl": settle_pnl, "missed_cost": missed_cost,
        "itm_amount": itm_amount, "settle_value": settle_value,
        "otm_dist": otm_dist, "delta_est": delta_est,
        "spx_at": spx_at, "favorable_at": favorable_at,
        "breach_intervals": breach_intervals,
        "savings": savings, "stop_time": stop_time,
        "tick_count": len(ticks),
        "last_tick_sec": ticks[-1][0] if ticks else 0
    })

n_false = sum(1 for s in stops if s["is_false"])
n_true = sum(1 for s in stops if not s["is_false"])


def get_fire_second(breach_intervals, timer_value):
    """Given breach intervals and timer value, return the second the stop fires, or None."""
    if timer_value == 0:
        return 0  # fires immediately
    for start, end in breach_intervals:
        duration = end - start
        if duration >= timer_value:
            return start + timer_value
    return None  # never fires within window


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: Display dataset info
# ═══════════════════════════════════════════════════════════════════════════════

print()
print("  " + "=" * 140)
print("  MKT-036 COMPLETE TIMER ANALYSIS — Full Timer Simulation")
print("  " + "=" * 140)
print()
print("  Dataset: {} trading days ({} to {})".format(total_days, dates[0], dates[-1]))
print("  Stops:   {} total ({} false, {} true) | Skipped: {} corrupt".format(
    len(stops), n_false, n_true, skipped))
print("  Spread:  {}pt (${:,} max) | Tick window: {}s ({:.0f} min)".format(
    SPREAD_WIDTH, SPREAD_WIDTH * 100, MAX_WINDOW, MAX_WINDOW / 60))
print("  Actual P&L: ${:+,.0f}".format(cum_actual))
print()
print("  Simulation: walks tick-by-tick, tracks breach/recovery/re-breach cycles.")
print("  After a timer reset, the stop RE-TRIGGERS on continued adverse movement.")
print("  A stop is only 'missed to settlement' if it NEVER sustains T consecutive")
print("  seconds of breach within the entire tick window.")
print()

# ═══════════════════════════════════════════════════════════════════════════════
# TRUE STOPS — Simulation detail
# ═══════════════════════════════════════════════════════════════════════════════

print("  " + "=" * 140)
print("  TRUE STOPS ({}) — Full simulation at 75s timer".format(n_true))
print("  " + "=" * 140)
print()

for s in stops:
    if s["is_false"]:
        continue

    print("  {:10s} E#{} {:4s} | SPX@stop={:.1f} Strike={:.0f} ({:.0f}pt OTM) | Stop P&L: ${:+,.0f} | Settle: ${:+,.0f}".format(
        s["date"], s["entry"], s["side"].upper(),
        s["spx_at_stop"], s["short_strike"], s["otm_dist"],
        s["pnl"], s["settle_pnl"]))

    for thresh in THRESHOLDS:
        intervals = s["breach_intervals"][thresh]
        fire_sec = get_fire_second(intervals, 75)
        n_resets = 0
        for start, end in intervals:
            if fire_sec is not None and start + 75 <= fire_sec:
                if end - start < 75:
                    n_resets += 1
            elif fire_sec is None and end - start < 75:
                n_resets += 1

        if fire_sec is not None:
            spx_at_fire = s["spx_at"][min(fire_sec, MAX_WINDOW)]
            if s["side"] == "put":
                adverse = s["spx_at_stop"] - spx_at_fire
            else:
                adverse = spx_at_fire - s["spx_at_stop"]
            extra = adverse * s["delta_est"] * 100
            print("    {}: FIRES at {:>4d}s ({} resets), SPX={:.1f} ({:+.1f}pt), delay cost ${:+,.0f}".format(
                THRESHOLD_LABELS[thresh], fire_sec, n_resets, spx_at_fire, adverse, extra))
        else:
            print("    {}: NEVER FIRES ({} resets, {} intervals, last tick {}s) → SETTLE ${:+,.0f}".format(
                THRESHOLD_LABELS[thresh], n_resets, len(intervals), s["last_tick_sec"], s["missed_cost"]))
    print()

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: Sweep (threshold × timer) with full simulation
# ═══════════════════════════════════════════════════════════════════════════════

test_timers = list(range(0, 125, 5)) + [150, 180, 205, 240, 300, 400, 500]
results_by_thresh = {}

for thresh in THRESHOLDS:
    results = []
    for T in test_timers:
        t_saved_count = 0
        t_savings = 0
        t_missed_count = 0
        t_missed_cost = 0
        t_fires_count = 0
        t_delay_cost = 0
        t_resets_total = 0
        t_day_impact = defaultdict(float)

        for s in stops:
            intervals = s["breach_intervals"][thresh]
            fire_sec = get_fire_second(intervals, T)

            if fire_sec is not None:
                # Stop fires (possibly delayed)
                t_fires_count += 1
                spx_at_fire = s["spx_at"][min(fire_sec, MAX_WINDOW)]
                if s["side"] == "put":
                    adverse = s["spx_at_stop"] - spx_at_fire
                else:
                    adverse = spx_at_fire - s["spx_at_stop"]
                cost = adverse * s["delta_est"] * 100
                t_delay_cost += cost
                t_day_impact[s["date"]] -= cost
            else:
                # Stop never fires within window
                if s["is_false"]:
                    # FALSE stop saved — entry survives to expiry
                    t_saved_count += 1
                    t_savings += s["savings"]
                    t_day_impact[s["date"]] += s["savings"]
                else:
                    # TRUE stop missed — held to settlement
                    t_missed_count += 1
                    t_missed_cost += s["missed_cost"]
                    t_day_impact[s["date"]] -= s["missed_cost"]

        net = t_savings - t_missed_cost - t_delay_cost
        adj_pnl = cum_actual + net

        t_wins = 0
        t_flipped = 0
        for d in dates:
            adj = daily[d]["net_pnl"] + t_day_impact.get(d, 0)
            if adj >= 0:
                t_wins += 1
            if daily[d]["net_pnl"] < 0 and adj >= 0:
                t_flipped += 1

        results.append({
            "timer": T, "saves": t_saved_count, "savings": t_savings,
            "missed": t_missed_count, "missed_cost": t_missed_cost,
            "fires": t_fires_count, "delay_cost": t_delay_cost,
            "net": net, "adj_pnl": adj_pnl,
            "wr": 100 * t_wins / total_days, "flipped": t_flipped
        })

    results_by_thresh[thresh] = results

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: Display tables
# ═══════════════════════════════════════════════════════════════════════════════

for thresh in THRESHOLDS:
    results = results_by_thresh[thresh]
    best = max(results, key=lambda x: x["net"])

    print("  " + "=" * 140)
    print("  TIMER SWEEP — {} (bounce >= {:.0f}pt resets timer, re-triggers on continued move)".format(
        THRESHOLD_LABELS[thresh], thresh))
    print("  " + "=" * 140)
    print()
    print("  {:>5s}  {:>5s}  {:>10s}  {:>6s}  {:>11s}  {:>5s}  {:>10s}  {:>12s}  {:>10s}  {:>5s}  {:>4s}".format(
        "Timer", "Saves", "Savings", "Missed", "MissedCost", "Fires", "DelayCost", "NET Benefit", "Adj P&L", "WR%", "L->W"))
    print("  " + "-" * 105)

    for r in results:
        marker = ""
        if r["timer"] == 75:
            marker = "  <-- CURRENT"
        if r["timer"] == best["timer"]:
            marker += "  <-- PEAK"
        print("  {:>4d}s  {:>5d}  ${:>9,.0f}  {:>6d}  ${:>10,.0f}  {:>5d}  ${:>9,.0f}  ${:>+11,.0f}  ${:>+9,.0f}  {:>4.0f}%  {:>4d}{}".format(
            r["timer"], r["saves"], r["savings"],
            r["missed"], r["missed_cost"],
            r["fires"], r["delay_cost"],
            r["net"], r["adj_pnl"], r["wr"], r["flipped"], marker))

    print()
    print("  Peak: ${:+,.0f} at {}s (saves {}, misses {}, delay ${:,.0f})".format(
        best["net"], best["timer"], best["saves"], best["missed"], best["delay_cost"]))
    print()

# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-THRESHOLD COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

key_timers = [0, 30, 60, 75, 90, 105, 120, 150, 180, 205]

print("  " + "=" * 140)
print("  CROSS-THRESHOLD COMPARISON")
print("  " + "=" * 140)
print()
print("  {:>5s}  |  {:>30s}  |  {:>30s}  |  {:>30s}".format(
    "Timer", "1pt conservative", "2pt moderate", "3pt strict"))
print("  {:>5s}  |  {:>5s} {:>6s} {:>12s}  |  {:>5s} {:>6s} {:>12s}  |  {:>5s} {:>6s} {:>12s}".format(
    "", "Saves", "Missed", "NET",
    "Saves", "Missed", "NET",
    "Saves", "Missed", "NET"))
print("  " + "-" * 110)

for T in key_timers:
    parts = []
    for thresh in THRESHOLDS:
        r = next(x for x in results_by_thresh[thresh] if x["timer"] == T)
        parts.append("{:>5d} {:>6d} ${:>+11,.0f}".format(r["saves"], r["missed"], r["net"]))
    marker = "  <-- CURRENT" if T == 75 else ""
    print("  {:>4d}s  |  {}  |  {}  |  {}{}".format(T, parts[0], parts[1], parts[2], marker))

# Deltas from 75s
print()
r75s = {thresh: next(x for x in results_by_thresh[thresh] if x["timer"] == 75) for thresh in THRESHOLDS}
for T in [90, 105, 120, 150]:
    parts = []
    for thresh in THRESHOLDS:
        r = next(x for x in results_by_thresh[thresh] if x["timer"] == T)
        delta_net = r["net"] - r75s[thresh]["net"]
        parts.append("${:+,.0f}".format(delta_net))
    print("  75s -> {:>3d}s:  {}  |  {}  |  {}".format(T, *parts))

# ═══════════════════════════════════════════════════════════════════════════════
# ROBUST OPTIMUM — Best worst-case timer
# ═══════════════════════════════════════════════════════════════════════════════

print()
print("  " + "=" * 140)
print("  ROBUST OPTIMUM — Best worst-case NET across all thresholds")
print("  " + "=" * 140)
print()

print("  {:>5s}  {:>12s}  {:>12s}  {:>12s}  {:>12s}".format(
    "Timer", "1pt NET", "2pt NET", "3pt NET", "WORST CASE"))
print("  " + "-" * 65)

best_worst = -999999
best_worst_timer = 0

for T in test_timers:
    nets = [next(x for x in results_by_thresh[thresh] if x["timer"] == T)["net"] for thresh in THRESHOLDS]
    worst = min(nets)
    if worst > best_worst:
        best_worst = worst
        best_worst_timer = T

    show = T in [0, 30, 60, 75, 80, 85, 90, 95, 100, 105, 110, 115, 120, 150, 180, 205, 300, 500]
    if show:
        marker = ""
        if T == 75:
            marker = "  <-- CURRENT"
        if T == best_worst_timer:
            marker += "  <-- BEST"
        print("  {:>4d}s  ${:>+11,.0f}  ${:>+11,.0f}  ${:>+11,.0f}  ${:>+11,.0f}{}".format(
            T, nets[0], nets[1], nets[2], worst, marker))

print()
print("  Best worst-case: {}s timer (min NET across thresholds: ${:+,.0f})".format(
    best_worst_timer, best_worst))

# ═══════════════════════════════════════════════════════════════════════════════
# SANITY CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

print()
print("  " + "=" * 140)
print("  SANITY CHECKS")
print("  " + "=" * 140)
print()

for thresh in THRESHOLDS:
    results = results_by_thresh[thresh]
    checks = 0
    total = 0

    # Timer 0 = baseline (all fire immediately, net=0)
    r0 = next(x for x in results if x["timer"] == 0)
    total += 1
    if r0["net"] == 0 and r0["saves"] == 0 and r0["missed"] == 0 and r0["fires"] == len(stops):
        checks += 1
    else:
        print("  [FAIL] {}: Timer 0 not baseline (saves={} missed={} fires={} net={:.0f})".format(
            THRESHOLD_LABELS[thresh], r0["saves"], r0["missed"], r0["fires"], r0["net"]))

    # saves + missed + fires = total for every row
    total += 1
    if all(r["saves"] + r["missed"] + r["fires"] == len(stops) for r in results):
        checks += 1
    else:
        print("  [FAIL] {}: Row totals don't match".format(THRESHOLD_LABELS[thresh]))

    # saves monotonically non-decreasing
    total += 1
    if all(results[i]["saves"] >= results[i-1]["saves"] for i in range(1, len(results))):
        checks += 1
    else:
        print("  [FAIL] {}: Saves not monotonic".format(THRESHOLD_LABELS[thresh]))

    # NET never exceeds max possible savings
    total += 1
    max_possible = sum(s["savings"] for s in stops if s["is_false"])
    if all(r["net"] <= max_possible for r in results):
        checks += 1
    else:
        print("  [FAIL] {}: NET exceeds max savings".format(THRESHOLD_LABELS[thresh]))

    # P&L identity
    total += 1
    if all(abs(r["adj_pnl"] - (cum_actual + r["net"])) < 0.01 for r in results):
        checks += 1
    else:
        print("  [FAIL] {}: P&L identity broken".format(THRESHOLD_LABELS[thresh]))

    # At timer 0, delay cost should be 0
    total += 1
    if r0["delay_cost"] == 0:
        checks += 1
    else:
        print("  [FAIL] {}: Delay cost at T=0 not zero".format(THRESHOLD_LABELS[thresh]))

    print("  {}: {}/{} checks passed".format(THRESHOLD_LABELS[thresh], checks, total))

# ═══════════════════════════════════════════════════════════════════════════════
# BOTTOM LINE
# ═══════════════════════════════════════════════════════════════════════════════

print()
print("  " + "=" * 140)
print("  BOTTOM LINE")
print("  " + "=" * 140)
print()
print("  Actual 21-day P&L (no timer): ${:+,.0f}".format(cum_actual))
print()

print("  75s timer (deployed):")
for thresh in THRESHOLDS:
    r = next(x for x in results_by_thresh[thresh] if x["timer"] == 75)
    print("    {}: NET ${:+,.0f} -> P&L ${:+,.0f} (saves {}, misses {}, fires {} delayed, WR {:.0f}%)".format(
        THRESHOLD_LABELS[thresh], r["net"], r["adj_pnl"],
        r["saves"], r["missed"], r["fires"], r["wr"]))

print()
print("  Robust optimum (best worst-case): {}s timer".format(best_worst_timer))
for thresh in THRESHOLDS:
    r = next(x for x in results_by_thresh[thresh] if x["timer"] == best_worst_timer)
    print("    {}: NET ${:+,.0f} -> P&L ${:+,.0f} (saves {}, misses {}, fires {} delayed, WR {:.0f}%)".format(
        THRESHOLD_LABELS[thresh], r["net"], r["adj_pnl"],
        r["saves"], r["missed"], r["fires"], r["wr"]))

# Show peak per threshold
print()
print("  Peak NET per threshold:")
for thresh in THRESHOLDS:
    results = results_by_thresh[thresh]
    peak = max(results, key=lambda x: x["net"])
    print("    {}: ${:+,.0f} at {}s (saves {}, misses {})".format(
        THRESHOLD_LABELS[thresh], peak["net"], peak["timer"], peak["saves"], peak["missed"]))

print()
conn.close()
