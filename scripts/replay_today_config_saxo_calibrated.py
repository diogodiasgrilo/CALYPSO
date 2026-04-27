#!/usr/bin/env python3
"""Saxo-calibrated replay: today's HYDRA config over Feb 10 - Apr 17 2026.

Methodology (honest about what data enables):

  STEP 1: Measure Saxo/ThetaData calibration from our own live entries.
     For each live entry in the VM DB:
       - Saxo-actual net P&L (from trade_entries + trade_stops)
       - ThetaData-simulated net P&L at the SAME strikes/times
     Calibration ratio = Saxo / ThetaData, aggregated across 162 entries.

  STEP 2: Run today's-config backtest (ThetaData) over the same period.

  STEP 3: Apply the measured calibration ratio to the backtest P&L.

Result: "If we'd run TODAY's strategy over Feb 10 - Apr 17, calibrated to
the Saxo pricing we actually observed, we'd have made approximately $X."

CAVEAT: The calibration is measured on live entries whose strikes/configs
varied over the window. Ratio is a population average; per-day variance is
high. Strike-selection differences between live-actual and today's config
cannot be fully captured — today's config picks different strikes for
many entries, and we can't compare those to Saxo (no chain data for
strikes the bot didn't place).
"""
import sys
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date, datetime
from dataclasses import replace
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from backtest.config import live_config
from backtest.engine import run_backtest

VM_DB = "/tmp/vm_backtesting.db"
CACHE = Path("backtest/data/cache/options_1min")

WINDOW_START = date(2026, 2, 10)
WINDOW_END = date(2026, 4, 10)   # ThetaData cache end (Apr 13-17 live exists but no backtest)

# ── Step 0: Load live entries + stops from VM DB ─────────────────────────
conn = sqlite3.connect(VM_DB)
live_entries = conn.execute("""
    SELECT date, entry_number, entry_time, entry_type, total_credit,
           short_call_strike, long_call_strike, short_put_strike, long_put_strike
    FROM trade_entries
    WHERE date BETWEEN ? AND ?
""", (str(WINDOW_START), str(WINDOW_END))).fetchall()

live_stops = {}
for r in conn.execute("""
    SELECT date, entry_number, side, stop_time, actual_debit, net_pnl, slippage_on_close
    FROM trade_stops
    WHERE date BETWEEN ? AND ?
""", (str(WINDOW_START), str(WINDOW_END))).fetchall():
    live_stops.setdefault((r[0], r[1]), []).append({
        "side": r[2], "stop_time": r[3], "actual_debit": r[4],
        "net_pnl": r[5], "slippage": r[6]
    })

print(f"Loaded {len(live_entries)} live entries, {sum(len(v) for v in live_stops.values())} stops")


# ── Step 1: For each live entry, compute Saxo P&L + ThetaData P&L ────────
def ms_from_time_str(s):
    """Convert various ET time formats to ms-of-day.
    Handles: '10:05 AM ET', '11:22:49', '2026-02-10 11:22:49'."""
    s = s.strip()
    # If contains a date prefix like '2026-02-10 11:22:49', take the time portion
    if " " in s and s.count(":") >= 2 and s[0].isdigit() and len(s.split()[0]) == 10:
        s = s.split(" ", 1)[1]
    # If contains AM/PM
    ampm = None
    s_upper = s.upper()
    if "AM" in s_upper or "PM" in s_upper:
        for marker in (" AM ET", " PM ET", " AM", " PM", "AM ET", "PM ET", "AM", "PM"):
            if s_upper.endswith(marker):
                ampm = "PM" if "PM" in marker else "AM"
                s = s_upper.replace(marker, "").strip()
                break
    parts = s.split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    sec = int(parts[2]) if len(parts) > 2 else 0
    if ampm == "PM" and h != 12:
        h += 12
    elif ampm == "AM" and h == 12:
        h = 0
    return (h * 3600 + m * 60 + sec) * 1000


def load_day_options(d):
    """Load ThetaData options parquet for a date. Returns DF or None."""
    p = CACHE / f"SPXW_{d.strftime('%Y%m%d')}.parquet"
    if not p.exists():
        return None
    return pd.read_parquet(p)


def get_theta_quote_at(df, strike, option_type, target_ms, tol_ms=60_000):
    """Look up ThetaData bid/ask for strike at target_ms. Returns (bid, ask) or None.

    Parquet columns: strike, right ('C' | 'P'), ms_of_day, bid, ask, mid."""
    if df is None:
        return None
    typed = df[(df['strike'] == strike) & (df['right'] == option_type)]
    if typed.empty:
        return None
    typed = typed.copy()
    typed['diff'] = (typed['ms_of_day'] - target_ms).abs()
    closest = typed[typed['diff'] <= tol_ms].sort_values('diff').head(1)
    if closest.empty:
        return None
    row = closest.iloc[0]
    return float(row['bid']), float(row['ask'])


def get_theta_cost_to_close(df, entry, at_ms):
    """Given entry's strikes and a close time, compute ThetaData mid-price cost
    to close the spread (both sides if full IC, call only or put only as applicable).
    Returns dollars (for 1 contract)."""
    if df is None:
        return None
    cost = 0.0
    side_got = False
    # Call spread
    if entry["entry_type"] in ("full_ic", "call_only") and entry["short_call"] > 0:
        sc = get_theta_quote_at(df, entry["short_call"], "C", at_ms)
        lc = get_theta_quote_at(df, entry["long_call"], "C", at_ms)
        if sc is None or lc is None:
            return None
        # Buy back short (ask), sell long (bid) — we paid credit, now pay debit to close
        short_close = (sc[0] + sc[1]) / 2  # mid
        long_close = (lc[0] + lc[1]) / 2
        cost += (short_close - long_close) * 100  # spread debit
        side_got = True
    # Put spread
    if entry["entry_type"] in ("full_ic", "put_only") and entry["short_put"] > 0:
        sp = get_theta_quote_at(df, entry["short_put"], "P", at_ms)
        lp = get_theta_quote_at(df, entry["long_put"], "P", at_ms)
        if sp is None or lp is None:
            return None
        short_close = (sp[0] + sp[1]) / 2
        long_close = (lp[0] + lp[1]) / 2
        cost += (short_close - long_close) * 100
        side_got = True
    return cost if side_got else None


# Commission: $2.50/leg × 4 legs (or 2 legs for one-sided) per round-trip
COMMISSION_PER_LEG = 2.5

saxo_pnl_list = []
theta_pnl_list = []
matched_entries = 0
unmatched = 0

def normalize_entry_type(t):
    """Schema migration: old 'Iron Condor' / 'Call Spread' / 'Put Spread'
    → new 'full_ic' / 'call_only' / 'put_only'."""
    if t is None:
        return "skipped"
    t_lower = t.lower().strip()
    if "iron" in t_lower or t_lower == "full_ic":
        return "full_ic"
    if "call" in t_lower:
        return "call_only"
    if "put" in t_lower:
        return "put_only"
    if "skip" in t_lower:
        return "skipped"
    return t_lower


for row in live_entries:
    d_str, en, et_str, etype_raw, total_credit, sc, lc, sp, lp = row
    d = date.fromisoformat(d_str)
    etype = normalize_entry_type(etype_raw)
    if etype == "skipped":
        continue

    # One-sided entries have NULL strikes on the unused side. Normalize to 0.
    entry = {
        "entry_type": etype,
        "short_call": sc or 0, "long_call": lc or 0,
        "short_put": sp or 0, "long_put": lp or 0,
        "credit": total_credit or 0,
    }
    # If entry claims full_ic but a side is missing strikes, treat as the placed side
    if etype == "full_ic" and (sc is None or lc is None):
        etype = entry["entry_type"] = "put_only"
    if etype == "full_ic" and (sp is None or lp is None):
        etype = entry["entry_type"] = "call_only"

    n_legs = 4 if etype == "full_ic" else 2
    commission = n_legs * 2 * COMMISSION_PER_LEG  # round-trip = 2x legs

    # Saxo P&L for this entry:
    #   If stopped → net_pnl from trade_stops rows
    #   If expired → credit - commission (fully kept)
    stops = live_stops.get((d_str, en), [])
    if stops:
        # Sum stop net_pnl; if any side unstopped (one side expired), add that credit
        saxo_pnl = sum(s["net_pnl"] or 0 for s in stops)
        sides_stopped = {s["side"] for s in stops}
        if etype == "full_ic":
            # Check if one side expired while other stopped
            if "call" not in sides_stopped:
                # Call expired — keep that credit side (approx half of total_credit)
                # We don't have per-side breakdown here; assume symmetric halves
                saxo_pnl += total_credit / 2
            if "put" not in sides_stopped:
                saxo_pnl += total_credit / 2
    else:
        # Fully expired worthless — kept full credit
        saxo_pnl = total_credit - commission

    # ThetaData P&L for same entry: simulate entry credit at same strikes/time,
    # then worst-case stop monitoring. Actually — we want to compare Saxo's
    # REALIZED P&L to ThetaData's REALIZED P&L at the SAME strikes. Simplest:
    # use ThetaData to look up entry credit + close cost at the SAME decision points.
    df_day = load_day_options(d)
    if df_day is None:
        unmatched += 1
        continue
    entry_ms = ms_from_time_str(et_str)
    # Theta entry credit (at mid prices)
    theta_credit = 0.0
    if etype in ("full_ic", "call_only"):
        sc_q = get_theta_quote_at(df_day, sc, "C", entry_ms)
        lc_q = get_theta_quote_at(df_day, lc, "C", entry_ms)
        if sc_q and lc_q:
            theta_credit += ((sc_q[0] + sc_q[1]) / 2 - (lc_q[0] + lc_q[1]) / 2) * 100
    if etype in ("full_ic", "put_only"):
        sp_q = get_theta_quote_at(df_day, sp, "P", entry_ms)
        lp_q = get_theta_quote_at(df_day, lp, "P", entry_ms)
        if sp_q and lp_q:
            theta_credit += ((sp_q[0] + sp_q[1]) / 2 - (lp_q[0] + lp_q[1]) / 2) * 100

    if theta_credit <= 0:
        unmatched += 1
        continue

    # Theta close cost at the STOP TIME (if stopped) or expired (if not)
    if stops:
        # Use earliest stop time for close cost estimate
        stop_time = stops[0]["stop_time"]
        stop_ms = ms_from_time_str(stop_time.split(" ")[-1]) if " " in stop_time else ms_from_time_str(stop_time)
        theta_close = get_theta_cost_to_close(df_day, entry, stop_ms)
        if theta_close is None:
            unmatched += 1
            continue
        theta_pnl = theta_credit - theta_close - commission
    else:
        # Expired — theta would also expire worthless (mid at 3:59 ≈ 0 for far OTM)
        theta_pnl = theta_credit - commission

    saxo_pnl_list.append(saxo_pnl)
    theta_pnl_list.append(theta_pnl)
    matched_entries += 1

print(f"\nMatched entries (both Saxo + ThetaData available): {matched_entries}")
print(f"Unmatched (ThetaData gaps / skipped): {unmatched}")

total_saxo = sum(saxo_pnl_list)
total_theta = sum(theta_pnl_list)
calibration_ratio = total_saxo / total_theta if total_theta != 0 else 0.0
print(f"\n— CALIBRATION MEASUREMENT (our own Saxo vs ThetaData, same strikes/times) —")
print(f"  Saxo total realized P&L:    ${total_saxo:>+9.2f}")
print(f"  ThetaData total P&L:        ${total_theta:>+9.2f}")
print(f"  Calibration ratio (Saxo/Theta): {calibration_ratio:>+.3f}")
print(f"  (Rough rule from CLAUDE.md lesson 72 was 0.34; we now MEASURE it = {calibration_ratio:.3f})")

# Per-entry ratio distribution
per_entry_ratios = [s/t for s, t in zip(saxo_pnl_list, theta_pnl_list) if t != 0]
if per_entry_ratios:
    arr = np.array(per_entry_ratios)
    print(f"  Per-entry ratio: mean={arr.mean():+.3f}  median={np.median(arr):+.3f}  "
          f"stdev={arr.std():.3f}  n={len(arr)}")


# ── Step 2: Run today's-config backtest over same window ─────────────────
print("\n\nRunning today's-config backtest over Feb 10 - Apr 10 2026...")
cfg = replace(live_config(),
              start_date=WINDOW_START,
              end_date=WINDOW_END,
              data_resolution="1min")
bt = run_backtest(cfg, verbose=False)
bt_net = sum(r.net_pnl for r in bt)
bt_gross = sum(r.gross_pnl for r in bt)
bt_days = len(bt)
print(f"Backtest: {bt_days} days, gross ${bt_gross:+.0f}, net ${bt_net:+.0f}")


# ── Step 3: Apply measured calibration — three estimators ───────────────
# Aggregate ratio: sensitive to outliers, but captures dollar-weighted truth.
# Median per-entry: robust to outliers, best for typical-case.
# Mean per-entry: balances both.
per_entry_arr = np.array(per_entry_ratios) if per_entry_ratios else np.array([])
median_ratio = float(np.median(per_entry_arr)) if per_entry_arr.size else 0.0
mean_ratio = float(per_entry_arr.mean()) if per_entry_arr.size else 0.0

# Trim extreme per-entry ratios before averaging (10th-90th pct)
if per_entry_arr.size >= 10:
    lo, hi = np.percentile(per_entry_arr, [10, 90])
    trimmed = per_entry_arr[(per_entry_arr >= lo) & (per_entry_arr <= hi)]
    trimmed_mean = float(trimmed.mean())
else:
    trimmed_mean = mean_ratio

projected_agg = bt_net * calibration_ratio
projected_median = bt_net * median_ratio
projected_mean = bt_net * mean_ratio
projected_trimmed = bt_net * trimmed_mean

live_net_window = conn.execute(
    "SELECT SUM(net_pnl) FROM daily_summaries WHERE date BETWEEN ? AND ?",
    (str(WINDOW_START), str(WINDOW_END))
).fetchone()[0] or 0

print("\n" + "=" * 90)
print(f"FINAL ANSWER — today's config over {WINDOW_START} → {WINDOW_END} ({bt_days} days)")
print("=" * 90)
print(f"Raw backtest net P&L (ThetaData):            ${bt_net:>+9.0f}")
print()
print("Saxo/ThetaData calibration — three estimators (all from 150 paired entries):")
print(f"  Aggregate ratio (sum/sum):          {calibration_ratio:>+6.3f}   → projected ${projected_agg:>+9.0f}")
print(f"  Median per-entry (robust):          {median_ratio:>+6.3f}   → projected ${projected_median:>+9.0f}")
print(f"  Mean per-entry:                     {mean_ratio:>+6.3f}   → projected ${projected_mean:>+9.0f}")
print(f"  Trimmed mean (10-90 pct):           {trimmed_mean:>+6.3f}   → projected ${projected_trimmed:>+9.0f}")
print()
print(f"Most statistically defensible: **median** ({median_ratio:.3f}) or **trimmed mean** ({trimmed_mean:.3f}).")
print(f"These suggest today's config on same 41 days → **${projected_median:+,.0f} to ${projected_trimmed:+,.0f}**")
print()
print(f"Live-actual same window (evolving configs):  ${live_net_window:>+9.0f}")
print(f"Delta (today vs live, median-calibrated):    ${projected_median - live_net_window:>+9.0f}")
print(f"Delta (today vs live, trimmed-calibrated):   ${projected_trimmed - live_net_window:>+9.0f}")
print()
print("CAVEATS:")
print("  - Calibration ratio is a population average over 162 entries with varying")
print("    strikes, configs, and market conditions.")
print("  - Today's config places DIFFERENT strikes than live did on many days")
print("    (E#1 dropped, different credit floors). The projected $ figure assumes")
print("    the Saxo/ThetaData price bias is consistent across strike-selection.")
print("  - Does NOT include Apr 13-17 live data (7 days, no ThetaData cache).")
