#!/usr/bin/env python3
"""More rigorous simulation of E6 call-only on down days.
Check intraday HIGHS between 14:00-16:00 vs simulated short call strike."""
import sqlite3
from collections import defaultdict

conn = sqlite3.connect("data/backtesting.db")

# Get all trading days and their 14:00 SPX direction
days = conn.execute(
    "SELECT DISTINCT date FROM daily_summaries WHERE date >= '2026-02-10' ORDER BY date"
).fetchall()

down_days = []
for d_row in days:
    d = d_row[0]
    open_row = conn.execute(
        "SELECT open FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '09:30' ORDER BY timestamp LIMIT 1",
        (d,)
    ).fetchone()
    aft_row = conn.execute(
        "SELECT close FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '14:00' ORDER BY timestamp LIMIT 1",
        (d,)
    ).fetchone()
    if not open_row or not aft_row:
        continue
    open_px = open_row[0]
    px_1400 = aft_row[0]
    if not open_px or not px_1400:
        continue
    pct = (px_1400 - open_px) / open_px
    if pct <= -0.0025:
        down_days.append((d, open_px, px_1400, pct))

print(f"Down days at 14:00 found: {len(down_days)}")
print()
print(f"{'Date':>12} | {'14:00':>8} | {'14-16 High':>10} | {'Close':>8} | {'55pt Strike':>11} | {'Touched?':>10} | {'Max Breach':>10}")
print("-" * 95)

wins_55pt = 0
stops_55pt = 0
max_breaches = []

for d, open_px, px_1400, pct in down_days:
    # Get max high between 14:00 and 16:00
    high_row = conn.execute(
        "SELECT MAX(high) FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '14:00' AND time(timestamp) < '16:00'",
        (d,)
    ).fetchone()
    max_high = high_row[0] if high_row and high_row[0] else px_1400

    close_row = conn.execute(
        "SELECT close FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '15:55' ORDER BY timestamp DESC LIMIT 1",
        (d,)
    ).fetchone()
    close_px = close_row[0] if close_row else px_1400

    # Simulated strike: 55pt above SPX at 14:00, rounded to 5pt
    strike_55 = round((px_1400 + 55) / 5) * 5

    # Did intraday high reach or exceed strike? If yes, likely a stop (depending on buffer)
    # If close < strike by 5+ pts, it expired worthless (win)
    touched = max_high >= strike_55
    result = "WIN" if close_px < strike_55 - 5 else ("STOP" if max_high >= strike_55 + 10 else "CLOSE")

    if result == "WIN":
        wins_55pt += 1
    else:
        stops_55pt += 1

    max_breach = max(0, max_high - strike_55)
    max_breaches.append(max_breach)

    print(f"  {d} | {px_1400:>7.2f} | {max_high:>9.2f} | {close_px:>7.2f} | {strike_55:>10.0f} | {'YES' if touched else 'no':>10} | {max_breach:>+9.1f}pt")

print()
print(f"At 55pt OTM strike (naive):")
print(f"  Wins: {wins_55pt}/{len(down_days)} ({100*wins_55pt/len(down_days):.0f}% WR)")
print(f"  Stops: {stops_55pt}")

# More realistic: use VIX-scaled OTM distance
# VIX 15 = ~45pt OTM, VIX 20 = ~60pt OTM, VIX 25 = ~75pt OTM
print()
print("=" * 95)
print("MORE REALISTIC: VIX-scaled OTM distance + 10pt safety margin")
print("=" * 95)
print(f"{'Date':>12} | {'VIX':>5} | {'14:00':>8} | {'14-16 High':>10} | {'OTM':>5} | {'Strike':>8} | {'Breached?':>10}")

wins = 0
stops = 0
risky = 0

for d, open_px, px_1400, pct in down_days:
    # Get VIX for the day
    vix_row = conn.execute(
        "SELECT vix_at_entry FROM trade_entries WHERE date=? ORDER BY entry_number LIMIT 1",
        (d,)
    ).fetchone()
    vix = vix_row[0] if vix_row else 18.0

    # VIX-scaled OTM: SPX * VIX/100 / sqrt(252) for daily expected move
    # Simplified: OTM = VIX * 3 (approximate)
    otm_pts = max(45, round(vix * 3))  # min 45pt, scales with VIX
    strike = round((px_1400 + otm_pts) / 5) * 5

    high_row = conn.execute(
        "SELECT MAX(high) FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '14:00' AND time(timestamp) < '16:00'",
        (d,)
    ).fetchone()
    max_high = high_row[0] if high_row and high_row[0] else px_1400

    close_row = conn.execute(
        "SELECT close FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '15:55' ORDER BY timestamp DESC LIMIT 1",
        (d,)
    ).fetchone()
    close_px = close_row[0] if close_row else px_1400

    if close_px < strike - 5:
        result = "WIN"
        wins += 1
    elif max_high >= strike + 15:
        result = "STOP"
        stops += 1
    else:
        result = "CLOSE"
        risky += 1

    breach = max(0, max_high - strike)
    print(f"  {d} | {vix:>5.1f} | {px_1400:>7.2f} | {max_high:>9.2f} | {otm_pts:>4}pt | {strike:>7.0f} | {'+' + str(round(breach)) + 'pt' if breach else '--':>10} {result}")

print()
print(f"At VIX-scaled OTM:")
print(f"  Wins (expired): {wins}")
print(f"  Stops (breached by 15+pt): {stops}")
print(f"  Close calls (touched but didn't breach): {risky}")
total = wins + stops + risky
if total:
    print(f"  Win rate: {100*wins/total:.0f}%")
    # Expected P&L estimate
    # Assume ~$40 credit per contract at 55pt OTM
    # Win = +$40 - $5 = +$35
    # Stop = -$100 to -$200
    expected_win = 35 * wins
    expected_loss = -150 * stops
    expected_close = 10 * risky  # tiny profit
    print(f"  Estimated total P&L: +${expected_win} - ${abs(expected_loss)} + ${expected_close} = ${expected_win + expected_loss + expected_close}")
