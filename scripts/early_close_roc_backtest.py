#!/usr/bin/env python3
"""
Early Close Backtest - Return on Capital (ROC) Method

Instead of measuring % of credit captured (early_close_backtest.py), this script
measures RETURN ON CAPITAL DEPLOYED. This normalizes across different premium
environments — on high-premium days (like Feb 13 with $3,045 credit), the P&L as
% of credit stays low, but the return on capital may already be excellent.

Key insight: A 1% daily return on capital is outstanding for any options strategy.
By using capital deployed as the denominator, the threshold triggers consistently
regardless of whether it's a low or high premium day.

Capital deployed comes from the bot's Daily Summary tab (authoritative).
It represents the total buying power reduction / margin requirement for all entries.

Methodology:
- Heartbeat P&L: Real net P&L logged every ~13s by the bot (realized + unrealized - commission)
- Close cost: $2.50 commission + $2.50 slippage = $5.00 per position to close
- Threshold: (heartbeat_net - close_cost) / capital_deployed >= X%
- Only checks AFTER last entry is placed
- Capital deployed is FIXED for the day (doesn't decrease when stops fire)

Commission note: $2.50/position to open (already in heartbeat P&L) + $2.50/position
to close + $2.50/position slippage on market close = $5.00/position close cost.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Tuple

# ── Constants ────────────────────────────────────────────────────────────

MARKET_CLOSE = 16.0  # 4:00 PM ET

# Close costs per POSITION (each contract = 1 position)
COMMISSION_PER_POSITION = 2.50
SLIPPAGE_PER_POSITION = 2.50
TOTAL_CLOSE_COST_PER_POSITION = COMMISSION_PER_POSITION + SLIPPAGE_PER_POSITION  # $5.00


@dataclass
class Entry:
    num: int
    time: float          # ET decimal hours (10.083 = 10:05 AM)
    credit: float        # total NET credit (short premium - long cost)
    positions: int       # 4 for IC, 2 for one-sided spread
    stop_time: Optional[float] = None   # when stop fired (None = expired)
    stop_cost: float = 0.0              # net loss from this stop event


@dataclass
class Day:
    date: str
    entries: list
    actual_net_pnl: float
    commission: float
    last_entry_time: float
    capital_deployed: float  # from Daily Summary tab (authoritative)
    # Actual heartbeat P&L data: list of (time_decimal, net_pnl)
    heartbeat: List[Tuple[float, float]] = field(default_factory=list)


# ── Heartbeat P&L Data (from VM journalctl logs) ────────────────────────
# Each tuple: (ET decimal hour, net P&L in dollars)
# Sampled from bot heartbeat logs at ~3-4 minute intervals

HB_FEB10 = [
    (10.12, 20), (10.18, 45), (10.24, -40), (10.30, -40), (10.36, 60),
    (10.42, 15), (10.48, 75), (10.54, 90), (10.60, 80), (10.66, 50),
    (10.73, 60), (10.79, 150), (10.85, 90), (10.91, 90), (10.97, 80),
    (11.03, 60), (11.10, 15), (11.16, -120), (11.24, -65), (11.31, 5),
    (11.38, -295), (11.43, -190), (11.49, -140), (11.55, -180),
    (11.63, -125), (11.69, -105), (11.76, -55), (11.82, -100),
    (11.87, -155), (11.94, -25), (12.01, -45), (12.07, -175),
    (12.14, 30), (12.20, 75), (12.26, 110), (12.32, 145),
    (12.38, 125), (12.44, 125), (12.50, 165), (12.56, 160),
    (12.63, 160), (12.69, 210), (12.75, 205), (12.81, 250),
    (12.87, 240), (12.93, 225), (12.99, 230), (13.05, 230),
    (13.11, 190), (13.17, 155), (13.23, 140), (13.29, -55),
    (13.35, -115), (13.42, -30), (13.49, -105), (13.56, -185),
    (13.63, -265), (13.69, -100), (13.75, -165), (13.82, -380),
    (13.90, -320), (13.96, -200), (14.03, -10), (14.09, -50),
    (14.15, -50), (14.21, 10), (14.27, 0), (14.33, -70),
    (14.39, 100), (14.45, 155), (14.51, 235), (14.57, 225),
    (14.63, 220), (14.69, 235), (14.75, 185), (14.81, 205),
    (14.87, 205), (14.93, 215), (14.99, 245), (15.05, 280),
    (15.11, 195), (15.17, 260), (15.23, 215), (15.29, 265),
    (15.35, 290), (15.42, 290), (15.47, 300), (15.54, 280),
    (15.60, 235), (15.66, 295), (15.72, 300), (15.78, 340),
    (15.84, 330), (15.90, 310), (15.97, 310),
]

HB_FEB11 = [
    (10.14, -25), (10.21, -120), (10.27, -120), (10.32, -105),
    (10.38, -90), (10.44, -85), (10.50, -80), (10.56, -80),
    (10.63, -80), (10.70, -80), (10.77, -70), (10.83, -25),
    (10.89, -140), (10.97, -105), (11.04, -95), (11.10, -45),
    (11.15, -70), (11.21, -30), (11.26, -30), (11.32, -5),
    (11.38, -10), (11.45, 10), (11.52, 15), (11.58, 50),
    (11.63, 5), (11.68, 50), (11.73, 85), (11.79, 100),
    (11.85, 100), (11.91, 120), (11.98, 65), (12.02, 15),
    (12.07, -5), (12.12, 25), (12.16, 145), (12.23, 205),
    (12.30, 225), (12.37, 210), (12.44, 220), (12.49, 240),
    (12.55, 295), (12.61, 270), (12.66, 330), (12.73, 360),
    (12.79, 375), (12.86, 385), (12.93, 390), (12.98, 320),
    (13.03, 255), (13.09, 355), (13.14, 270), (13.26, 50),
    (13.33, 180), (13.40, 230), (13.47, 290), (13.53, 280),
    (13.60, 280), (13.66, 305), (13.73, 290), (13.79, 315),
    (13.86, 310), (13.92, 320), (13.99, 330), (14.05, 340),
    (14.12, 340), (14.18, 340), (14.25, 355), (14.32, 350),
    (14.38, 350), (14.45, 335), (14.51, 365), (14.58, 385),
    (14.64, 385), (14.71, 385), (14.78, 355), (14.84, 385),
    (14.90, 390), (14.97, 395), (15.04, 405), (15.11, 410),
    (15.17, 400), (15.23, 415), (15.30, 420), (15.37, 415),
    (15.43, 410), (15.50, 420), (15.63, 425), (15.70, 425),
    (15.76, 425), (15.83, 425), (15.89, 425), (15.96, 425),
]

HB_FEB12 = [
    (10.12, -5), (10.19, -15), (10.26, 35), (10.33, 60),
    (10.40, 60), (10.46, 95), (10.53, 95), (10.60, -20),
    (10.66, -115), (10.72, -110), (10.78, -110), (10.84, -100),
    (10.90, -95), (10.96, -95), (11.02, -95), (11.08, -85),
    (11.14, -65), (11.20, -5), (11.27, 15), (11.33, 30),
    (11.39, 10), (11.45, 45), (11.51, 50), (11.57, 40),
    (11.64, 25), (11.69, 55), (11.75, 100), (11.81, 140),
    (11.87, 155), (11.94, 165), (12.00, 130), (12.07, 195),
    (12.17, 160), (12.22, 145), (12.27, 135), (12.31, 115),
    (12.36, 135), (12.41, 125), (12.45, 125), (12.50, 130),
    (12.55, 165), (12.60, 150), (12.65, 195), (12.70, 205),
    (12.75, 250), (12.80, 260), (12.86, 320), (12.91, 260),
    (12.96, 245), (13.01, 180), (13.08, 180), (13.17, 180),
    (13.23, 175), (13.31, 205), (13.38, 185), (13.45, 190),
    (13.52, 230), (13.58, 215), (13.65, 185), (13.72, 185),
    (13.79, 160), (13.86, 175), (13.92, 155), (13.99, 185),
    (14.06, 205), (14.13, 225), (14.19, 260), (14.26, 260),
    (14.33, 225), (14.40, 240), (14.46, 265), (14.53, 260),
    (14.60, 250), (14.68, 245), (14.75, 260), (14.82, 240),
    (14.90, 255), (14.97, 255), (15.04, 285), (15.11, 300),
    (15.18, 320), (15.25, 315), (15.33, 320), (15.40, 320),
    (15.47, 320), (15.53, 320), (15.62, 320), (15.69, 320),
    (15.76, 320), (15.83, 320), (15.90, 320), (15.98, 320),
]

HB_FEB13 = [
    (10.13, 20), (10.18, -20), (10.22, -10), (10.27, 70),
    (10.34, 105), (10.39, 130), (10.45, 120), (10.51, -60),
    (10.57, -205), (10.64, -260), (10.68, -700), (10.72, -625),
    (10.78, -840), (10.84, -860), (10.91, -735), (10.97, -720),
    (11.03, -690), (11.10, -640), (11.15, -625), (11.19, -660),
    (11.24, -650), (11.29, -600), (11.34, -605), (11.39, -660),
    (11.44, -595), (11.49, -530), (11.56, -485), (11.62, -455),
    (11.67, -425), (11.74, -355), (11.80, -355), (11.87, -300),
    (11.94, -250), (12.00, -170), (12.07, -145), (12.14, -30),
    (12.20, 0), (12.26, -55), (12.31, 55), (12.36, -35),
    (12.43, 95), (12.49, 140), (12.56, 105), (12.63, 210),
    (12.71, 245), (12.78, 280), (12.86, 345), (12.93, 380),
    (12.99, 355), (13.06, 360), (13.11, 400), (13.16, 460),
    (13.23, 525), (13.31, 535), (13.38, 515), (13.45, 547),
    (13.53, 585), (13.60, 585), (13.68, 640), (13.75, 630),
    (13.82, 685), (13.90, 665), (13.97, 700), (14.04, 735),
    (14.12, 750), (14.19, 775), (14.27, 795), (14.34, 815),
    (14.41, 810), (14.48, 775), (14.56, 830), (14.63, 820),
    (14.70, 660), (14.78, 785), (14.85, 780), (14.92, 750),
    (14.99, 455), (15.06, 460), (15.13, 515), (15.21, 510),
    (15.28, 475), (15.35, 510), (15.43, 460), (15.51, 260),
    (15.58, 330), (15.65, 435), (15.72, 355), (15.80, 515),
    (15.87, 580), (15.94, 600),
]

HB_FEB17 = [
    (10.13, 55), (10.20, 105), (10.26, 100), (10.33, -85),
    (10.39, 30), (10.45, 110), (10.51, 135), (10.57, 165),
    (10.63, 165), (10.67, 210), (10.72, 235), (10.78, 260),
    (10.82, 260), (10.87, 210), (10.92, 240), (10.97, 265),
    (11.01, 195), (11.07, -85), (11.13, -230), (11.18, -240),
    (11.23, -490), (11.30, -505), (11.36, -475), (11.43, -465),
    (11.49, -495), (11.56, -500), (11.63, -405), (11.70, -400),
    (11.76, -450), (11.82, -420), (11.88, -390), (11.94, -295),
    (12.01, -320), (12.07, -355), (12.14, -285), (12.19, -450),
    (12.25, -455), (12.31, -425), (12.38, -510), (12.43, -405),
    (12.50, -315), (12.58, -405), (12.65, -335), (12.71, -425),
    (12.77, -665), (12.82, -445), (12.87, -535), (12.92, -740),
    (12.99, -660), (13.06, -655), (13.12, -650), (13.19, -680),
    (13.26, -640), (13.33, -630), (13.40, -630), (13.46, -615),
    (13.53, -620), (13.60, -610), (13.66, -610), (13.73, -650),
    (13.80, -640), (13.86, -640), (13.93, -650), (13.99, -620),
    (14.06, -620), (14.13, -605), (14.19, -600), (14.26, -590),
    (14.33, -580), (14.39, -585), (14.46, -590), (14.52, -575),
    (14.60, -580), (14.66, -570), (14.73, -575), (14.80, -565),
    (14.86, -560), (14.93, -565), (15.00, -560), (15.06, -555),
    (15.13, -560), (15.20, -555), (15.27, -555), (15.33, -555),
    (15.40, -555), (15.47, -555), (15.54, -555), (15.60, -555),
    (15.67, -555), (15.73, -555), (15.80, -555), (15.87, -555),
    (15.93, -555),
]

HB_FEB18 = [
    (12.00, 290), (12.07, 380), (12.16, 510), (12.24, 495),
    (12.33, 545), (12.43, 515), (12.52, 505), (12.61, 510),
    (12.70, 525), (12.80, 560), (12.89, 605), (12.98, 600),
    (13.07, 645), (13.17, 620), (13.26, 645), (13.35, 640),
    (13.44, 580), (13.53, 515), (13.63, 580), (13.72, 335),
    (13.79, 505), (13.87, 265), (13.95, 185), (14.04, 235),
    (14.13, 210), (14.21, 210), (14.30, 160), (14.39, 220),
    (14.47, 210), (14.56, 180), (14.65, -75), (14.72, 45),
    (14.81, 145), (14.90, 20), (14.97, -105), (15.05, 110),
    (15.13, 105), (15.22, -45), (15.28, -55), (15.36, -30),
    (15.44, 70), (15.53, 240), (15.61, 280), (15.70, 300),
    (15.78, 315), (15.87, 315), (15.96, 315),
]

# ── Trading Data ─────────────────────────────────────────────────────────
# Capital Deployed from the bot's Daily Summary tab (authoritative)

DAYS = [
    Day("Feb 10", [
        Entry(1, 10.083, 210, 2),  # call-only
        Entry(2, 10.583, 150, 2),  # MKT-011 one-sided
        Entry(3, 11.083, 120, 2, stop_time=11.37, stop_cost=140),
        Entry(4, 11.583, 95, 2),
        Entry(5, 12.083, 65, 2),
    ], actual_net_pnl=350, commission=30, last_entry_time=12.083,
       capital_deployed=25000, heartbeat=HB_FEB10),

    Day("Feb 11", [
        Entry(1, 10.083, 435, 4, stop_time=10.17, stop_cost=155),
        Entry(2, 10.583, 140, 2),
        Entry(3, 11.083, 200, 2),
        Entry(4, 11.583, 170, 2),
        Entry(5, 12.083, 125, 2),
        Entry(6, 12.583, 100, 2, stop_time=13.22, stop_cost=135),
    ], actual_net_pnl=425, commission=45, last_entry_time=12.583,
       capital_deployed=30000, heartbeat=HB_FEB11),

    Day("Feb 12", [
        Entry(1, 10.083, 320, 4, stop_time=10.60, stop_cost=95),
        Entry(2, 10.583, 290, 4, stop_time=10.60, stop_cost=75),
        Entry(3, 11.083, 185, 2),
        Entry(4, 11.583, 250, 2),
        Entry(5, 12.150, 310, 4, stop_time=13.00, stop_cost=215),
        Entry(6, 12.583, 255, 4, stop_time=12.90, stop_cost=80),
    ], actual_net_pnl=360, commission=70, last_entry_time=12.583,
       capital_deployed=32000, heartbeat=HB_FEB12),

    Day("Feb 13", [
        Entry(1, 10.083, 1150, 4, stop_time=10.55, stop_cost=650),
        Entry(2, 10.583, 430, 2, stop_time=10.75, stop_cost=440),
        Entry(3, 11.083, 675, 4),
        Entry(4, 11.583, 475, 4),
        Entry(5, 12.100, 315, 4, stop_time=14.98, stop_cost=130),
    ], actual_net_pnl=675, commission=60, last_entry_time=12.100,
       capital_deployed=28000, heartbeat=HB_FEB13),

    Day("Feb 17", [
        Entry(1, 10.083, 305, 2, stop_time=11.18, stop_cost=295),
        Entry(2, 10.583, 695, 4, stop_time=11.03, stop_cost=335),
        Entry(3, 11.083, 400, 4, stop_time=11.23, stop_cost=265),
        Entry(4, 11.583, 235, 2, stop_time=12.88, stop_cost=225),
        Entry(5, 12.100, 250, 4, stop_time=12.18, stop_cost=30),
    ], actual_net_pnl=-740, commission=65, last_entry_time=12.100,
       capital_deployed=12500, heartbeat=HB_FEB17),

    Day("Feb 18", [
        Entry(1, 10.083, 390, 4),
        Entry(2, 10.583, 220, 2),
        Entry(3, 11.083, 115, 2, stop_time=13.88, stop_cost=125),
        Entry(4, 11.583, 85, 2, stop_time=13.88, stop_cost=135),
    ], actual_net_pnl=315, commission=35, last_entry_time=11.583,
       capital_deployed=20000, heartbeat=HB_FEB18),
]


# ── P&L Lookup (actual heartbeat data) ──────────────────────────────────

def get_heartbeat_pnl(heartbeat: List[Tuple[float, float]], t: float) -> Optional[float]:
    """Interpolate the actual P&L at time t from heartbeat data."""
    if not heartbeat:
        return None
    if t < heartbeat[0][0]:
        return None
    if t >= heartbeat[-1][0]:
        return heartbeat[-1][1]

    lo, hi = 0, len(heartbeat) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if heartbeat[mid][0] <= t:
            lo = mid
        else:
            hi = mid

    t0, p0 = heartbeat[lo]
    t1, p1 = heartbeat[hi]
    if t1 == t0:
        return p0
    frac = (t - t0) / (t1 - t0)
    return p0 + frac * (p1 - p0)


def get_active_positions(day: Day, t: float) -> int:
    """
    Count active positions at time t.
    - IC with one side stopped: 2 surviving positions
    - One-sided fully stopped: 0
    - Active entry: entry.positions
    """
    active = 0
    for entry in day.entries:
        if t < entry.time:
            continue
        if entry.stop_time and t >= entry.stop_time:
            if entry.positions == 4:
                active += 2  # IC: one side survives
        else:
            active += entry.positions
    return active


# ── Static ROC Threshold Test ────────────────────────────────────────────

def test_roc_threshold(threshold_pct: float, label: str = "") -> dict:
    """
    Close all positions when return on capital >= threshold%.
    ROC = (heartbeat_net - close_cost) / capital_deployed
    """
    results = []
    total_pnl = 0.0

    for day in DAYS:
        triggered = False
        trigger_time = None
        close_net = None
        trigger_roc = None

        t = day.last_entry_time
        while t < 15.917:  # Don't close in last 5 min
            pnl = get_heartbeat_pnl(day.heartbeat, t)
            if pnl is None:
                t += 1.0 / 60.0
                continue

            active_pos = get_active_positions(day, t)
            if day.capital_deployed <= 0:
                t += 1.0 / 60.0
                continue

            close_cost = active_pos * TOTAL_CLOSE_COST_PER_POSITION
            net = pnl - close_cost
            roc = net / day.capital_deployed

            if roc >= threshold_pct:
                triggered = True
                trigger_time = t
                close_net = net
                trigger_roc = roc
                break

            t += 1.0 / 60.0

        if triggered:
            final_net = close_net
        else:
            final_net = day.actual_net_pnl

        results.append({
            "date": day.date,
            "triggered": triggered,
            "trigger_time": trigger_time,
            "net_pnl": final_net,
            "actual_pnl": day.actual_net_pnl,
            "diff": final_net - day.actual_net_pnl,
            "capital": day.capital_deployed,
            "actual_roc": day.actual_net_pnl / day.capital_deployed if day.capital_deployed > 0 else 0,
            "trigger_roc": trigger_roc,
        })
        total_pnl += final_net

    return {
        "threshold": threshold_pct,
        "label": label or f"{threshold_pct*100:.2f}%",
        "total_pnl": total_pnl,
        "actual_total": sum(d.actual_net_pnl for d in DAYS),
        "improvement": total_pnl - sum(d.actual_net_pnl for d in DAYS),
        "days": results,
        "triggers": sum(1 for d in results if d["triggered"]),
    }


# ── Dynamic ROC Threshold Test ───────────────────────────────────────────

def test_dynamic_roc_threshold(base_pct: float, slope_pct: float) -> dict:
    """
    Time-varying ROC threshold: threshold = base + slope * (hours_left / hours_total)

    hours_total = market_close - last_entry_time (max window)
    hours_left = market_close - current_time

    Early in the window: threshold is HIGH (base + slope) — harder to trigger
    Near close: threshold is LOW (base) — easier to trigger

    Example: base=1.0%, slope=1.5%
      At last entry (3.5h left): threshold = 1.0% + 1.5% = 2.5%
      At 2:00 PM (2h left): threshold = 1.0% + 1.5%*(2/3.5) = 1.86%
      At 3:45 PM (15min left): threshold = 1.0% + 1.5%*(0.25/3.5) = 1.11%
    """
    results = []
    total_pnl = 0.0

    for day in DAYS:
        triggered = False
        trigger_time = None
        close_net = None
        trigger_roc = None
        trigger_threshold = None

        hours_total = MARKET_CLOSE - day.last_entry_time

        t = day.last_entry_time
        while t < 15.917:
            hours_left = MARKET_CLOSE - t
            if hours_left < 0.25:
                break

            # Dynamic threshold: higher early, lower near close
            if hours_total > 0:
                threshold = base_pct + slope_pct * (hours_left / hours_total)
            else:
                threshold = base_pct

            pnl = get_heartbeat_pnl(day.heartbeat, t)
            if pnl is None:
                t += 1.0 / 60.0
                continue

            active_pos = get_active_positions(day, t)
            if day.capital_deployed <= 0:
                t += 1.0 / 60.0
                continue

            close_cost = active_pos * TOTAL_CLOSE_COST_PER_POSITION
            net = pnl - close_cost
            roc = net / day.capital_deployed

            if roc >= threshold:
                triggered = True
                trigger_time = t
                close_net = net
                trigger_roc = roc
                trigger_threshold = threshold
                break

            t += 1.0 / 60.0

        if triggered:
            final_net = close_net
        else:
            final_net = day.actual_net_pnl

        results.append({
            "date": day.date,
            "triggered": triggered,
            "trigger_time": trigger_time,
            "net_pnl": final_net,
            "actual_pnl": day.actual_net_pnl,
            "diff": final_net - day.actual_net_pnl,
            "capital": day.capital_deployed,
            "actual_roc": day.actual_net_pnl / day.capital_deployed if day.capital_deployed > 0 else 0,
            "trigger_roc": trigger_roc,
            "trigger_threshold": trigger_threshold,
        })
        total_pnl += final_net

    label = f"D:{base_pct*100:.1f}%+{slope_pct*100:.1f}%"
    return {
        "threshold": f"{base_pct:.3f}+{slope_pct:.3f}",
        "label": label,
        "total_pnl": total_pnl,
        "actual_total": sum(d.actual_net_pnl for d in DAYS),
        "improvement": total_pnl - sum(d.actual_net_pnl for d in DAYS),
        "days": results,
        "triggers": sum(1 for d in results if d["triggered"]),
    }


# ── Formatted Output ─────────────────────────────────────────────────────

def fmt_time(t):
    if t is None:
        return "  --  "
    h = int(t)
    m = int((t - h) * 60)
    return f"{h:2d}:{m:02d}"


def print_results(result):
    label = result["label"]
    print(f"\n{'='*80}")
    print(f"  Threshold: {label} return on capital")
    print(f"  Triggers: {result['triggers']}/{len(result['days'])} days")
    print(f"  Total P&L: ${result['total_pnl']:+,.0f}  "
          f"(baseline: ${result['actual_total']:+,.0f}, "
          f"diff: ${result['improvement']:+,.0f})")
    print(f"{'='*80}")
    print(f"  {'Date':<8} {'Trig?':<6} {'Time':<7} {'Close P&L':>10} "
          f"{'Baseline':>10} {'Diff':>8} {'Capital':>10} {'ROC':>8} {'Actual ROC':>10}")
    print(f"  {'-'*8} {'-'*6} {'-'*7} {'-'*10} {'-'*10} {'-'*8} {'-'*10} {'-'*8} {'-'*10}")
    for d in result["days"]:
        trig = "YES" if d["triggered"] else "hold"
        time = fmt_time(d["trigger_time"])
        roc_str = f"{d['trigger_roc']*100:.2f}%" if d["trigger_roc"] else "  --  "
        actual_roc = f"{d['actual_roc']*100:.2f}%"
        print(f"  {d['date']:<8} {trig:<6} {time:<7} ${d['net_pnl']:>+9,.0f} "
              f"${d['actual_pnl']:>+9,.0f} ${d['diff']:>+7,.0f} "
              f"${d['capital']:>9,.0f} {roc_str:>8} {actual_roc:>10}")


def print_roc_trajectory():
    """Show P&L trajectory with ROC % at key times for each day."""
    print("\n" + "="*90)
    print("  RETURN ON CAPITAL TRAJECTORY (from bot heartbeat logs)")
    print("  Shows: net P&L | ROC% | active positions | close cost | net after close | ROC after close")
    print("="*90)

    for day in DAYS:
        total_credit = sum(e.credit for e in day.entries)
        actual_roc = day.actual_net_pnl / day.capital_deployed * 100 if day.capital_deployed > 0 else 0
        print(f"\n  {day.date} | credit: ${total_credit:,.0f} | capital: ${day.capital_deployed:,.0f} "
              f"| actual: ${day.actual_net_pnl:+,.0f} ({actual_roc:+.2f}% ROC)")
        print(f"  {'Time':<7} {'Net P&L':>9} {'ROC%':>8} {'ActPos':>7} "
              f"{'ClsCost':>8} {'AfterCls':>10} {'ROC-Cls%':>9}")
        print(f"  {'-'*7} {'-'*9} {'-'*8} {'-'*7} {'-'*8} {'-'*10} {'-'*9}")

        # Key times: every 30 min from last entry to 4 PM
        times = []
        t = day.last_entry_time
        while t <= 16.0:
            times.append(t)
            t += 0.5
        if 16.0 not in times:
            times.append(16.0)

        # Also find peak P&L time after last entry
        peak_t, peak_pnl = day.last_entry_time, -999999
        for ht, hp in day.heartbeat:
            if ht >= day.last_entry_time and hp > peak_pnl:
                peak_t, peak_pnl = ht, hp

        if peak_t not in times:
            times.append(peak_t)
        times.sort()

        for t in times:
            pnl = get_heartbeat_pnl(day.heartbeat, t)
            if pnl is None:
                continue
            active = get_active_positions(day, t)
            close_cost = active * TOTAL_CLOSE_COST_PER_POSITION
            after_close = pnl - close_cost
            roc_raw = pnl / day.capital_deployed * 100 if day.capital_deployed > 0 else 0
            roc_net = after_close / day.capital_deployed * 100 if day.capital_deployed > 0 else 0

            marker = " <-- PEAK" if abs(t - peak_t) < 0.01 else ""
            print(f"  {fmt_time(t):<7} ${pnl:>+8,.0f} {roc_raw:>+7.2f}% "
                  f"{active:>7} ${close_cost:>7,.0f} ${after_close:>+9,.0f} "
                  f"{roc_net:>+8.2f}%{marker}")


if __name__ == "__main__":
    print("=" * 80)
    print("  HYDRA EARLY CLOSE BACKTEST - RETURN ON CAPITAL (ROC) METHOD")
    print("  Using ACTUAL P&L from bot heartbeat logs (not theta model)")
    print("  Data: 6 trading days (Feb 10-13, 17-18, 2026)")
    print("  Close cost: $5.00/position ($2.50 comm + $2.50 slippage)")
    print("  Capital deployed from Daily Summary tab (authoritative)")
    print("=" * 80)

    baseline = sum(d.actual_net_pnl for d in DAYS)
    total_capital = sum(d.capital_deployed for d in DAYS)
    print(f"\n  Baseline (hold to expiry): ${baseline:+,.0f}")
    print(f"  Total capital deployed over 6 days: ${total_capital:,.0f}")
    print(f"  Average daily ROC: {baseline/len(DAYS)/sum(d.capital_deployed for d in DAYS)*len(DAYS)*100:.2f}%")

    print(f"\n  Per-day baseline:")
    for d in DAYS:
        roc = d.actual_net_pnl / d.capital_deployed * 100 if d.capital_deployed > 0 else 0
        print(f"    {d.date}: ${d.actual_net_pnl:>+6,.0f} on ${d.capital_deployed:>7,.0f} "
              f"= {roc:>+6.2f}% ROC")

    # ── ROC Trajectory ──────────────────────────────────────────────
    print_roc_trajectory()

    # ── Static ROC Thresholds ───────────────────────────────────────
    print("\n\n" + "#" * 80)
    print("  STATIC ROC THRESHOLD RESULTS")
    print("  Close when return on capital >= X%")
    print("#" * 80)

    static_results = []
    for pct in [0.005, 0.0075, 0.01, 0.0125, 0.015, 0.0175, 0.02, 0.025, 0.03]:
        r = test_roc_threshold(pct, f"{pct*100:.2f}%")
        static_results.append(r)
        print_results(r)

    # ── Dynamic ROC Thresholds ──────────────────────────────────────
    print("\n\n" + "#" * 80)
    print("  DYNAMIC ROC THRESHOLD RESULTS")
    print("  threshold = base + slope * (hours_left / hours_total)")
    print("  Higher early (harder to trigger), lower near close (easier)")
    print("#" * 80)

    dynamic_results = []
    for base, slope in [
        (0.005, 0.005),   # 0.5% + 0.5% → 1.0% early, 0.5% late
        (0.005, 0.010),   # 0.5% + 1.0% → 1.5% early, 0.5% late
        (0.0075, 0.0075), # 0.75% + 0.75% → 1.5% early, 0.75% late
        (0.010, 0.005),   # 1.0% + 0.5% → 1.5% early, 1.0% late
        (0.010, 0.010),   # 1.0% + 1.0% → 2.0% early, 1.0% late
        (0.010, 0.015),   # 1.0% + 1.5% → 2.5% early, 1.0% late
        (0.015, 0.010),   # 1.5% + 1.0% → 2.5% early, 1.5% late
        (0.015, 0.015),   # 1.5% + 1.5% → 3.0% early, 1.5% late
    ]:
        r = test_dynamic_roc_threshold(base, slope)
        dynamic_results.append(r)
        print_results(r)

    # ── Summary Table ───────────────────────────────────────────────
    print("\n\n" + "=" * 80)
    print("  SUMMARY: ALL CONFIGURATIONS (sorted by improvement)")
    print("=" * 80)
    print(f"  {'Config':<25} {'Triggers':>8} {'Total P&L':>12} {'vs Baseline':>12}")
    print(f"  {'-'*25} {'-'*8} {'-'*12} {'-'*12}")
    print(f"  {'Hold to expiry':<25} {'0/6':>8} ${baseline:>+11,.0f} {'$+0':>12}")

    all_results = static_results + dynamic_results
    all_results.sort(key=lambda x: x["improvement"], reverse=True)

    for r in all_results:
        print(f"  {r['label']:<25} {r['triggers']}/6{'':<3} "
              f"${r['total_pnl']:>+11,.0f} ${r['improvement']:>+11,.0f}")

    # ── Best Configuration ──────────────────────────────────────────
    best = max(all_results, key=lambda x: x["improvement"])
    print(f"\n  Best: {best['label']} → ${best['improvement']:+,.0f} vs baseline")

    if best["improvement"] > 0:
        print(f"\n  FINDING: Early close at {best['label']} ROC would have improved "
              f"P&L by ${best['improvement']:,.0f} over 6 days")
    else:
        print(f"\n  FINDING: No ROC-based early close configuration beat hold-to-expiry")

    # ── Comparison with credit-based approach ───────────────────────
    print("\n\n" + "=" * 80)
    print("  KEY DIFFERENCE: ROC vs CREDIT-BASED")
    print("=" * 80)
    print("  Credit-based: threshold = (net_pnl - close_cost) / total_credit")
    print("  ROC-based: threshold = (net_pnl - close_cost) / capital_deployed")
    print()
    print("  Per-day denominators:")
    for d in DAYS:
        total_credit = sum(e.credit for e in d.entries)
        ratio = d.capital_deployed / total_credit if total_credit > 0 else 0
        print(f"    {d.date}: credit=${total_credit:>6,.0f}  "
              f"capital=${d.capital_deployed:>7,.0f}  "
              f"ratio={ratio:.1f}x  "
              f"(1% ROC = {d.capital_deployed*0.01:.0f}$ = "
              f"{d.capital_deployed*0.01/total_credit*100:.1f}% of credit)")
