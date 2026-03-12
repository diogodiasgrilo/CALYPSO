#!/usr/bin/env python3
"""
False Put Stop Solutions — Backtesting Analysis

Tests 3 approaches against 21-day dataset to reduce false put stop losses:
  1. ASYMMETRIC STOPS — Higher put stop level (e.g., credit × 1.3 instead of credit + $0.10)
  2. TIME-BASED DELAY — Don't arm put stops for first N minutes after entry
  3. NET PORTFOLIO STOP — Check net (put_cost - call_profit) instead of per-side

Run on VM: cd /opt/calypso && .venv/bin/python scripts/mkt036_solutions_backtest.py
"""
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = "/opt/calypso/data/backtesting.db"
SPREAD_WIDTH = 50  # points
CURRENT_BUFFER = 10  # $0.10 × 100 = $10

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# ═══════════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════════

cur.execute("SELECT date, net_pnl, spx_close FROM daily_summaries ORDER BY date")
daily = {}
for row in cur.fetchall():
    daily[row[0]] = {"net_pnl": row[1], "spx_close": row[2]}

cur.execute("""
    SELECT date, entry_number, short_call_strike, short_put_strike,
           total_credit, call_credit, put_credit, entry_time
    FROM trade_entries ORDER BY date, entry_number
""")
entries = {}
for row in cur.fetchall():
    entries[(row[0], row[1])] = {
        "sc": row[2], "sp": row[3], "tc": row[4],
        "cc": row[5] or 0, "pc": row[6] or 0,
        "entry_time": row[7]
    }

cur.execute("""
    SELECT date, entry_number, side, stop_time, spx_at_stop,
           trigger_level, actual_debit, net_pnl
    FROM trade_stops ORDER BY date, stop_time
""")
raw_stops = cur.fetchall()

dates = sorted(daily.keys())
total_days = len(dates)

# ═══════════════════════════════════════════════════════════════════════════════
# BUILD STOP RECORDS WITH TICK DATA
# ═══════════════════════════════════════════════════════════════════════════════

stops = []
for raw in raw_stops:
    date, enum, side, stop_time, spx_at_stop, trigger, debit, pnl = raw
    entry = entries.get((date, enum), {})
    spx_close = daily.get(date, {}).get("spx_close")
    entry_time = entry.get("entry_time")

    if side == "put":
        short_strike = entry.get("sp")
        credit = entry.get("pc", 0)
    else:
        short_strike = entry.get("sc")
        credit = entry.get("cc", 0)

    if not short_strike or not spx_close or not spx_at_stop or spx_at_stop == 0:
        continue

    # False = SPX closed OTM (stop was unnecessary)
    if side == "put":
        is_false = spx_close > short_strike
    else:
        is_false = spx_close < short_strike

    # Settlement P&L if held to expiry
    if side == "put":
        itm_amount = max(0, short_strike - spx_close)
    else:
        itm_amount = max(0, spx_close - short_strike)
    settle_value = min(itm_amount, SPREAD_WIDTH) * 100
    settle_pnl = credit - settle_value

    # Time from entry to stop
    minutes_since_entry = None
    if entry_time and stop_time:
        try:
            et = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
            st = datetime.strptime(stop_time, "%Y-%m-%d %H:%M:%S")
            minutes_since_entry = (st - et).total_seconds() / 60
        except:
            pass

    # OTM distance
    if side == "put":
        otm_dist = spx_at_stop - short_strike
    else:
        otm_dist = short_strike - spx_at_stop

    stops.append({
        "date": date, "entry": enum, "side": side,
        "spx_at_stop": spx_at_stop, "short_strike": short_strike,
        "spx_close": spx_close, "credit": credit,
        "total_credit": entry.get("tc", 0),
        "call_credit": entry.get("cc", 0),
        "put_credit": entry.get("pc", 0),
        "pnl": pnl, "is_false": is_false,
        "settle_pnl": settle_pnl, "settle_value": settle_value,
        "itm_amount": itm_amount,
        "otm_dist": otm_dist, "trigger": trigger,
        "stop_time": stop_time, "entry_time": entry_time,
        "minutes_since_entry": minutes_since_entry,
    })

put_stops = [s for s in stops if s["side"] == "put"]
call_stops = [s for s in stops if s["side"] == "call"]
false_put_stops = [s for s in put_stops if s["is_false"]]
true_put_stops = [s for s in put_stops if not s["is_false"]]

# Current system P&L from stops
current_stop_pnl = sum(s["pnl"] for s in stops)

print()
print("  " + "=" * 120)
print("  FALSE STOP SOLUTIONS — Backtesting Analysis")
print("  " + "=" * 120)
print()
print(f"  Dataset: {total_days} trading days ({dates[0]} to {dates[-1]})")
print(f"  Total stops: {len(stops)} ({len(put_stops)} put, {len(call_stops)} call)")
print(f"  False put stops: {len(false_put_stops)}/{len(put_stops)} ({100*len(false_put_stops)/len(put_stops):.0f}%)")
print(f"  True put stops:  {len(true_put_stops)}/{len(put_stops)} ({100*len(true_put_stops)/len(put_stops):.0f}%)")
print(f"  Current stop P&L: ${current_stop_pnl:+,.0f}")
print()

# ═══════════════════════════════════════════════════════════════════════════════
# SOLUTION 1: ASYMMETRIC PUT STOPS — Higher stop level for puts
# ═══════════════════════════════════════════════════════════════════════════════
# Instead of stop = credit + $0.10, use stop = credit × multiplier for puts
# Higher stop level = more room before stop triggers = fewer false stops
# BUT: when true stop hits, loss is larger

print("  " + "=" * 120)
print("  SOLUTION 1: ASYMMETRIC PUT STOPS — Raise put stop level")
print("  " + "=" * 120)
print()
print("  Current: stop = credit + $0.10 (same for both sides)")
print("  Proposal: stop = credit × multiplier for PUTS only (calls unchanged)")
print()
print(f"  {'Multiplier':<12} {'Put Stop Level':<16} {'False Avoided':<16} {'Savings':<12} {'Extra True Loss':<18} {'NET':<12} {'Notes'}")
print(f"  {'─'*12} {'─'*16} {'─'*16} {'─'*12} {'─'*18} {'─'*12} {'─'*40}")

best_asym_net = -999999
best_asym_mult = 0

for mult in [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0, 2.5, 3.0]:
    savings = 0
    extra_true_loss = 0
    false_avoided = 0
    notes = []

    for s in put_stops:
        current_stop_level = s["credit"] + CURRENT_BUFFER  # credit + $10
        new_stop_level = s["credit"] * mult  # credit × mult

        # Would the current stop have been avoided with higher level?
        # We need to estimate: at the moment the current stop fired,
        # what was spread_value? It was approximately = current_stop_level
        # (since spread_value >= stop_level triggered it)
        # With higher stop level, the stop wouldn't fire at that moment.

        # But the question is: would spread_value eventually reach new_stop_level?
        # We can estimate this from OTM distance and settlement outcome.

        # Simple model: the stop fired at spread_value ≈ current_stop_level
        # If new_stop_level > current_stop_level, the stop is avoided at that moment
        # But SPX could continue moving adversely...

        # Better approach: use actual_debit (what we actually paid to close)
        actual_cost = abs(s["pnl"]) + s["credit"]  # total debit paid to close

        if new_stop_level > actual_cost:
            # Higher stop level would have prevented this stop
            if s["is_false"]:
                # FALSE stop avoided — we save the loss AND keep the credit
                savings += abs(s["pnl"])  # we avoid this loss
                false_avoided += 1
            else:
                # TRUE stop avoided — but we'd be held to settlement
                # Settlement loss might be worse
                settlement_loss = s["settle_value"] - s["credit"]  # net loss at settlement
                current_loss = abs(s["pnl"])
                diff = settlement_loss - current_loss
                extra_true_loss += max(0, diff)  # only count if settlement is worse
                if diff > 0:
                    notes.append(f"E#{s['entry']} {s['date']}: settle -${settlement_loss:.0f} vs stop -${current_loss:.0f}")
        else:
            # The actual closing cost exceeded even the higher stop level
            # This stop would still fire (just later, when spread_value reaches new level)
            # Extra loss from delay = new_stop_level - current_stop_level (roughly)
            extra_loss = max(0, new_stop_level - current_stop_level)
            extra_true_loss += extra_loss if not s["is_false"] else 0

    net = savings - extra_true_loss
    marker = " ◄◄◄" if net == max(net, best_asym_net) and net > 0 else ""
    if net > best_asym_net:
        best_asym_net = net
        best_asym_mult = mult

    avg_stop = sum(s["credit"] for s in put_stops) / len(put_stops)
    print(f"  {mult:<12.2f} ${avg_stop * mult / 100:<13.2f}    {false_avoided:<16} ${savings:<10,.0f} ${extra_true_loss:<16,.0f} ${net:<10,}{marker}")

print()
print(f"  Best multiplier: {best_asym_mult:.2f}× → NET ${best_asym_net:+,.0f}")
print()

# Detail: show each false put stop's actual_cost vs credit
print(f"  {'─'*120}")
print(f"  Detail: False put stops — actual close cost vs credit")
print(f"  {'Date':<12} {'E#':<4} {'Credit':<10} {'Actual Cost':<14} {'Stop P&L':<12} {'Ratio':<8} {'Saved @1.3×?':<14} {'Saved @1.5×?'}")
print(f"  {'─'*12} {'─'*4} {'─'*10} {'─'*14} {'─'*12} {'─'*8} {'─'*14} {'─'*14}")

for s in sorted(false_put_stops, key=lambda x: x["date"]):
    actual_cost = abs(s["pnl"]) + s["credit"]
    ratio = actual_cost / s["credit"] if s["credit"] > 0 else 999
    saved_13 = "YES" if s["credit"] * 1.3 > actual_cost else "no"
    saved_15 = "YES" if s["credit"] * 1.5 > actual_cost else "no"
    print(f"  {s['date']:<12} #{s['entry']:<3} ${s['credit']:<8,.0f} ${actual_cost:<12,.0f} ${s['pnl']:<10,} {ratio:<8.2f} {saved_13:<14} {saved_15}")

print()

# ═══════════════════════════════════════════════════════════════════════════════
# SOLUTION 2: TIME-BASED DELAY — Don't arm put stops for first N minutes
# ═══════════════════════════════════════════════════════════════════════════════
# Many false stops happen in first 15-20 min after entry. Delay arming the
# stop gives the position time to "settle in" before monitoring.

print("  " + "=" * 120)
print("  SOLUTION 2: TIME-BASED DELAY — Don't arm put stops for first N minutes")
print("  " + "=" * 120)
print()
print("  If a put stop would fire within N minutes of entry, it's delayed until N minutes.")
print("  If SPX recovers by then, stop is avoided. If SPX still breached, stop fires.")
print()

# Distribution of minutes_since_entry for false put stops
print(f"  False put stop timing distribution:")
timing_buckets = defaultdict(int)
for s in false_put_stops:
    if s["minutes_since_entry"] is not None:
        bucket = int(s["minutes_since_entry"] // 5) * 5  # 5-min buckets
        timing_buckets[bucket] += 1
    else:
        timing_buckets[-1] += 1

for bucket in sorted(timing_buckets.keys()):
    if bucket == -1:
        label = "unknown"
    else:
        label = f"{bucket}-{bucket+5} min"
    count = timing_buckets[bucket]
    bar = "█" * count
    pct = 100 * count / len(false_put_stops)
    print(f"    {label:>12}: {bar} {count} ({pct:.0f}%)")

print()
print(f"  {'Delay (min)':<14} {'False Avoided':<16} {'Savings':<12} {'True Missed':<14} {'Extra Loss':<12} {'NET':<12}")
print(f"  {'─'*14} {'─'*16} {'─'*12} {'─'*14} {'─'*12} {'─'*12}")

best_delay_net = -999999
best_delay_min = 0

for delay_min in [5, 10, 15, 20, 25, 30, 45, 60, 90, 120]:
    false_avoided = 0
    savings = 0
    true_missed = 0
    extra_loss = 0

    for s in put_stops:
        if s["minutes_since_entry"] is None:
            continue
        if s["minutes_since_entry"] < delay_min:
            # This stop would have been delayed
            if s["is_false"]:
                # Would SPX recover by delay_min?
                # We know SPX closed OTM, so YES it eventually recovered
                # But would it still be breaching at delay_min after entry?
                # We can't know exactly without tick data from entry time
                # Conservative estimate: if stop fired < delay_min, it's saved
                savings += abs(s["pnl"])
                false_avoided += 1
            else:
                # True stop delayed — held to settlement
                settlement_loss = s["settle_value"] - s["credit"]
                current_loss = abs(s["pnl"])
                diff = settlement_loss - current_loss
                if diff > 0:
                    extra_loss += diff
                    true_missed += 1

    net = savings - extra_loss
    marker = " ◄◄◄" if net > best_delay_net else ""
    if net > best_delay_net:
        best_delay_net = net
        best_delay_min = delay_min

    print(f"  {delay_min:<14} {false_avoided:<16} ${savings:<10,.0f} {true_missed:<14} ${extra_loss:<10,.0f} ${net:<10,}{marker}")

print()
print(f"  Best delay: {best_delay_min} min → NET ${best_delay_net:+,.0f}")
print()

# Show individual false put stops with time data
print(f"  {'─'*120}")
print(f"  Detail: False put stops by time since entry")
print(f"  {'Date':<12} {'E#':<4} {'Entry Time':<14} {'Stop Time':<14} {'Min Since':<12} {'Stop P&L':<12} {'Settle P&L'}")
print(f"  {'─'*12} {'─'*4} {'─'*14} {'─'*14} {'─'*12} {'─'*12} {'─'*12}")

for s in sorted(false_put_stops, key=lambda x: x["minutes_since_entry"] or 999):
    et = s["entry_time"][-8:] if s["entry_time"] else "?"
    st = s["stop_time"][-8:] if s["stop_time"] else "?"
    mins = f"{s['minutes_since_entry']:.0f}" if s["minutes_since_entry"] is not None else "?"
    print(f"  {s['date']:<12} #{s['entry']:<3} {et:<14} {st:<14} {mins:<12} ${s['pnl']:<10,} ${s['settle_pnl']:<10,}")

print()

# ═══════════════════════════════════════════════════════════════════════════════
# SOLUTION 3: NET PORTFOLIO STOP — Check combined P&L before stopping
# ═══════════════════════════════════════════════════════════════════════════════
# Instead of stopping when put_spread_value >= put_stop_level,
# check if (put_loss - call_profit) >= threshold
# If calls are profitable, they offset put losses

print("  " + "=" * 120)
print("  SOLUTION 3: NET PORTFOLIO STOP — Combined P&L threshold")
print("  " + "=" * 120)
print()
print("  Instead of per-side stops, only stop when NET loss across both sides exceeds threshold.")
print("  If calls are profitable (common when puts are stressed), they offset put losses.")
print()

# For each put stop, check if calls were likely profitable at that moment
# We can estimate: when puts are stressed (SPX dropping), calls should be profitable
# Call spread value ≈ 0 when SPX drops significantly (calls expire worthless = full credit kept)
# So call profit ≈ call_credit when put is stressed

# Group stops by (date, entry) to find paired call+put data
entry_stops = defaultdict(list)
for s in stops:
    entry_stops[(s["date"], s["entry"])].append(s)

print(f"  When a put stop fires, what's the call side doing?")
print(f"  If SPX drops (put stress), call spreads are deep OTM = nearly worthless = call profit ≈ call credit")
print()

# For net portfolio approach:
# Current: stop when spread_value >= credit + buffer (per side)
# New: stop when (put_spread_value - call_spread_value) >= net_threshold
# Since call_spread_value ≈ 0 when puts are stressed:
#   put_spread_value - 0 >= net_threshold → same as per-side but with higher threshold
# The real benefit: on days where BOTH sides are stressed (choppy market),
#   the net offset reduces false stops

# Better model: net_stop fires when net_pnl (across both sides) < -threshold
# net_pnl = (call_credit - call_close_cost) + (put_credit - put_close_cost)
# At put stop moment: call_close_cost ≈ $0 (deep OTM), so net_pnl ≈ call_credit + put_credit - put_close_cost
# put_close_cost ≈ put_credit + buffer (current stop level)
# So net_pnl ≈ call_credit + put_credit - (put_credit + buffer) = call_credit - buffer
# This is ALWAYS positive! The net portfolio stop would NEVER fire when calls are deep OTM.

# That's the key insight — let's verify this with actual numbers

print(f"  {'Date':<12} {'E#':<4} {'Side':<6} {'Put Credit':<12} {'Call Credit':<12} {'Est Call Profit':<16} {'Put Loss':<12} {'Net P&L':<12} {'False?'}")
print(f"  {'─'*12} {'─'*4} {'─'*6} {'─'*12} {'─'*12} {'─'*16} {'─'*12} {'─'*12} {'─'*6}")

net_stop_analysis = []
for s in put_stops:
    entry = entries.get((s["date"], s["entry"]), {})
    cc = entry.get("cc", 0)
    pc = entry.get("pc", 0)

    # When put is stressed (SPX dropped), call spread is deep OTM
    # Call spread value ≈ $0-$20 (nearly worthless)
    # Conservative estimate: call profit = call_credit × 0.8 (80% of credit)
    est_call_profit = cc * 0.85  # conservative estimate

    put_loss = abs(s["pnl"])
    net_pnl = est_call_profit - put_loss

    net_stop_analysis.append({
        **s,
        "est_call_profit": est_call_profit,
        "net_pnl_est": net_pnl,
    })

    false_label = "FALSE" if s["is_false"] else "true"
    print(f"  {s['date']:<12} #{s['entry']:<3} {'PUT':<6} ${pc:<10,.0f} ${cc:<10,.0f} ${est_call_profit:<14,.0f} ${put_loss:<10,.0f} ${net_pnl:<10,.0f} {false_label}")

print()
print(f"  {'─'*120}")
print(f"  Net portfolio stop analysis:")
print()

print(f"  {'Threshold':<14} {'False Avoided':<16} {'True Avoided':<14} {'Savings':<12} {'Extra Loss':<14} {'NET':<12}")
print(f"  {'─'*14} {'─'*16} {'─'*14} {'─'*12} {'─'*14} {'─'*12}")

best_net_ev = -999999
best_net_thresh = 0

for thresh in [0, 25, 50, 75, 100, 150, 200, 300, 500]:
    false_avoided = 0
    true_avoided = 0
    savings = 0
    extra_loss = 0

    for s in net_stop_analysis:
        # Would this stop be avoided under net portfolio threshold?
        # Stop fires only when net loss > threshold
        # net loss = put_loss - est_call_profit
        net_loss = abs(s["pnl"]) - s["est_call_profit"]

        if net_loss < thresh:
            # Net loss is below threshold — stop avoided
            if s["is_false"]:
                savings += abs(s["pnl"])
                false_avoided += 1
            else:
                # True stop avoided — goes to settlement
                settlement_loss = s["settle_value"] - s["credit"]
                current_loss = abs(s["pnl"])
                diff = settlement_loss - current_loss
                if diff > 0:
                    extra_loss += diff
                true_avoided += 1

    net_ev = savings - extra_loss
    marker = " ◄◄◄" if net_ev > best_net_ev else ""
    if net_ev > best_net_ev:
        best_net_ev = net_ev
        best_net_thresh = thresh

    print(f"  ${thresh:<13} {false_avoided:<16} {true_avoided:<14} ${savings:<10,.0f} ${extra_loss:<12,.0f} ${net_ev:<10,}{marker}")

print()
print(f"  Best threshold: ${best_net_thresh} → NET ${best_net_ev:+,.0f}")
print()

# ═══════════════════════════════════════════════════════════════════════════════
# COMBINED COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

print("  " + "=" * 120)
print("  COMBINED COMPARISON")
print("  " + "=" * 120)
print()
print(f"  Current system stop P&L:    ${current_stop_pnl:+,.0f}")
print(f"  Current false put stop cost: ${sum(s['pnl'] for s in false_put_stops):+,.0f}")
print()
print(f"  Solution 1 (Asymmetric {best_asym_mult:.2f}×): NET improvement ${best_asym_net:+,.0f}")
print(f"  Solution 2 (Delay {best_delay_min} min):     NET improvement ${best_delay_net:+,.0f}")
print(f"  Solution 3 (Net portfolio ${best_net_thresh}): NET improvement ${best_net_ev:+,.0f}")
print()

# Perfect oracle for reference
perfect_savings = sum(abs(s["pnl"]) for s in false_put_stops)
print(f"  Perfect oracle (avoid ALL false put stops): ${perfect_savings:+,.0f}")
print(f"  Note: actual achievable is always less than oracle due to true stop costs")
print()

# ═══════════════════════════════════════════════════════════════════════════════
# SOLUTION 1 REFINED: What stop multiplier avoids FALSE stops without
# making true stop losses worse?
# ═══════════════════════════════════════════════════════════════════════════════

print("  " + "=" * 120)
print("  REFINED ANALYSIS: Actual close cost vs credit ratio (ALL put stops)")
print("  " + "=" * 120)
print()
print("  The ratio = actual_close_cost / credit tells us how far past the current")
print("  stop level the spread moved. Ratio < 1.0 = close cost was BELOW credit.")
print("  Current stop fires at ratio ≈ 1.0 (credit + $0.10 buffer).")
print()

# Distribution of actual_cost / credit ratios
ratios_false = []
ratios_true = []
for s in put_stops:
    actual_cost = abs(s["pnl"]) + s["credit"]
    ratio = actual_cost / s["credit"] if s["credit"] > 0 else 999
    if s["is_false"]:
        ratios_false.append((ratio, s))
    else:
        ratios_true.append((ratio, s))

print(f"  False put stops — cost/credit ratio distribution:")
for r_min in [0.8, 0.9, 1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.5, 2.0]:
    r_max = r_min + 0.1 if r_min < 2.0 else 999
    count = sum(1 for r, _ in ratios_false if r_min <= r < r_max)
    if count > 0:
        bar = "█" * count
        print(f"    {r_min:.2f}-{r_max:.2f}: {bar} {count}")

print()
print(f"  True put stops — cost/credit ratio distribution:")
for r_min in [0.8, 0.9, 1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.5, 2.0]:
    r_max = r_min + 0.1 if r_min < 2.0 else 999
    count = sum(1 for r, _ in ratios_true if r_min <= r < r_max)
    if count > 0:
        bar = "█" * count
        print(f"    {r_min:.2f}-{r_max:.2f}: {bar} {count}")

print()

# Also analyze: for false stops, if we had let them run, what's the max adverse
# move before recovery?
# We need tick data for this — load from market_ticks

print("  " + "=" * 120)
print("  DEEP DIVE: How far did SPX actually drop after each false put stop?")
print("  " + "=" * 120)
print()
print("  For each false put stop, shows the max adverse SPX move within 2 hours")
print("  of the stop time. If max adverse is small, a higher stop might work.")
print()

print(f"  {'Date':<12} {'E#':<4} {'SPX@Stop':<10} {'Strike':<8} {'OTM':<6} {'Credit':<8} {'MaxAdverse':<12} {'MaxAdverse$':<12} {'Min to Recovery':<16} {'Stop P&L'}")
print(f"  {'─'*12} {'─'*4} {'─'*10} {'─'*8} {'─'*6} {'─'*8} {'─'*12} {'─'*12} {'─'*16} {'─'*10}")

for s in sorted(false_put_stops, key=lambda x: x["date"]):
    # Get tick data for 2 hours after stop
    cur.execute(
        "SELECT timestamp, spx_price FROM market_ticks "
        "WHERE substr(timestamp, 1, 10) = ? "
        "AND timestamp >= ? AND timestamp <= datetime(?, '+7200 seconds') "
        "ORDER BY timestamp",
        (s["date"], s["stop_time"], s["stop_time"])
    )
    stop_dt = datetime.strptime(s["stop_time"], "%Y-%m-%d %H:%M:%S")

    max_adverse = 0
    recovery_sec = None
    for ts, px in cur.fetchall():
        tick_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        delta_s = (tick_dt - stop_dt).total_seconds()
        # For puts, adverse = SPX dropping further
        adverse = s["spx_at_stop"] - px  # positive = SPX dropped more
        if adverse > max_adverse:
            max_adverse = adverse
        # Recovery = SPX back to stop level or above
        if px >= s["spx_at_stop"] and recovery_sec is None and delta_s > 0:
            recovery_sec = delta_s

    # Estimate max adverse in $ terms (delta × move × 100)
    delta_est = 0.15 if s["otm_dist"] > 30 else 0.25 if s["otm_dist"] > 20 else 0.35
    max_adverse_dollar = max_adverse * delta_est * 100

    rec_str = f"{recovery_sec/60:.0f} min" if recovery_sec else ">2 hrs"
    print(f"  {s['date']:<12} #{s['entry']:<3} {s['spx_at_stop']:<10.1f} {s['short_strike']:<8.0f} {s['otm_dist']:<6.0f} ${s['credit']:<6,.0f} {max_adverse:<12.1f}pt ${max_adverse_dollar:<10,.0f} {rec_str:<16} ${s['pnl']:,}")

print()

conn.close()
print("  Analysis complete.")
print()
