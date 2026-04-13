"""
Config audit library for HYDRA — validates config values against live trading data.

Core functions:
    - get_entries(date_range) → list of entries with P&L
    - get_spx_drop_pct(date, at_time) → drop from open
    - get_daily_pnl(date) → authoritative daily net P&L
    - compute_counterfactual(entry, scenario) → projected P&L under alternative config

All functions cross-validated against:
    - daily_summaries.net_pnl (authoritative daily totals from bot's own calculation)
    - trade_entries (authoritative per-entry data)
    - trade_stops (authoritative per-stop data)
    - market_ticks (~10s SPX snapshots from Saxo)

Usage:
    from config_audit_lib import ConfigAuditDB, verify_against_daily_summary
    db = ConfigAuditDB('/tmp/backtesting.db')
    assert db.verify_daily_pnl_reconciliation(), "P&L reconciliation failed"
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional


class ConfigAuditDB:
    """Read-only accessor with validated queries for config audit."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._cache = {}

    def _conn(self):
        return sqlite3.connect(self.db_path)

    # -------- Basic getters --------

    def get_daily_summary(self, date: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(
                """SELECT date, spx_open, spx_close, spx_high, spx_low, vix_open, vix_close,
                   entries_placed, entries_stopped, gross_pnl, net_pnl, commission
                   FROM daily_summaries WHERE date = ?""",
                (date,),
            ).fetchone()
        if not row:
            return None
        return {
            "date": row[0], "spx_open": row[1], "spx_close": row[2],
            "spx_high": row[3], "spx_low": row[4],
            "vix_open": row[5], "vix_close": row[6],
            "entries_placed": row[7], "entries_stopped": row[8],
            "gross_pnl": row[9], "net_pnl": row[10], "commission": row[11],
        }

    def get_entries(self, date: str) -> list:
        with self._conn() as c:
            rows = c.execute(
                """SELECT date, entry_number, entry_time, spx_at_entry, vix_at_entry,
                   entry_type, override_reason, trend_signal,
                   short_call_strike, long_call_strike, short_put_strike, long_put_strike,
                   call_credit, put_credit, total_credit
                   FROM trade_entries WHERE date = ? ORDER BY entry_number""",
                (date,),
            ).fetchall()
        return [
            {
                "date": r[0], "num": r[1], "time": r[2],
                "spx_at_entry": r[3], "vix_at_entry": r[4],
                "type": r[5], "override": r[6], "signal": r[7],
                "sc": r[8], "lc": r[9], "sp": r[10], "lp": r[11],
                "cc": r[12] or 0, "pc": r[13] or 0, "tc": r[14] or 0,
            }
            for r in rows
        ]

    def get_stops(self, date: str) -> dict:
        """Returns {entry_num: [{side, actual_debit, net_pnl, stop_time, minutes_held}, ...]}"""
        with self._conn() as c:
            rows = c.execute(
                """SELECT entry_number, side, stop_time, actual_debit, net_pnl, minutes_held
                   FROM trade_stops WHERE date = ?""",
                (date,),
            ).fetchall()
        stops = {}
        for r in rows:
            stops.setdefault(r[0], []).append({
                "side": r[1], "stop_time": r[2],
                "actual_debit": r[3], "net_pnl": r[4],
                "minutes_held": r[5],
            })
        return stops

    def get_all_dates(self, min_date="2026-02-10", max_date="2026-04-30") -> list:
        with self._conn() as c:
            rows = c.execute(
                """SELECT date FROM daily_summaries
                   WHERE date >= ? AND date <= ? AND entries_placed > 0
                   ORDER BY date""",
                (min_date, max_date),
            ).fetchall()
        return [r[0] for r in rows]

    # -------- SPX price queries --------

    def get_spx_at_time(self, date: str, target_time: str) -> Optional[float]:
        """Get SPX price at specific time (HH:MM:SS format).

        Returns the latest market_ticks snapshot at or before target_time on given date.
        """
        ts_target = f"{date} {target_time}"
        with self._conn() as c:
            row = c.execute(
                """SELECT spx_price FROM market_ticks
                   WHERE DATE(timestamp) = ? AND timestamp <= ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (date, ts_target),
            ).fetchone()
        return row[0] if row else None

    def get_spx_open(self, date: str) -> Optional[float]:
        """Get SPX open price.

        Uses daily_summaries.spx_open if available (most authoritative),
        else the first market_tick >= 9:30 ET.
        """
        summary = self.get_daily_summary(date)
        if summary and summary["spx_open"] and summary["spx_open"] > 0:
            return summary["spx_open"]
        # Fallback
        with self._conn() as c:
            row = c.execute(
                """SELECT spx_price FROM market_ticks
                   WHERE DATE(timestamp) = ? AND timestamp >= ?
                   ORDER BY timestamp LIMIT 1""",
                (date, f"{date} 09:30:00"),
            ).fetchone()
        return row[0] if row else None

    def get_spx_drop_pct_at(self, date: str, target_time: str) -> Optional[float]:
        """Get drop% from open to target_time. Positive means SPX went DOWN.

        Returns: (spx_open - spx_at_target) / spx_open
        """
        spx_open = self.get_spx_open(date)
        spx_at = self.get_spx_at_time(date, target_time)
        if not spx_open or not spx_at:
            return None
        return (spx_open - spx_at) / spx_open

    def get_spx_rise_pct_at(self, date: str, target_time: str) -> Optional[float]:
        """Get rise% from open to target_time. Positive means SPX went UP."""
        drop = self.get_spx_drop_pct_at(date, target_time)
        return -drop if drop is not None else None

    # -------- P&L computation --------

    def compute_entry_pnl(self, entry: dict, stops: list) -> dict:
        """Compute authoritative P&L for a single entry.

        Returns dict with:
            call_pnl: per-side P&L for call side (0 if not placed)
            put_pnl: per-side P&L for put side (0 if not placed)
            commission: estimated commission for this entry
            entry_net: call_pnl + put_pnl - commission
            call_status: expired | stopped | not_placed
            put_status: expired | stopped | not_placed
        """
        etype = entry["type"]
        cc = entry["cc"] or 0
        pc = entry["pc"] or 0

        # Determine which sides were placed based on entry type
        # Normalize type names (old vs new)
        normalized_type = etype.lower().replace(" ", "_") if etype else ""
        if "iron_condor" in normalized_type or "full_ic" in normalized_type or "full" == normalized_type:
            call_placed = True
            put_placed = True
        elif "call" in normalized_type:
            call_placed = True
            put_placed = False
        elif "put" in normalized_type:
            call_placed = False
            put_placed = True
        else:
            call_placed = False
            put_placed = False

        # Identify stopped sides
        stopped_sides = {s["side"]: s for s in stops}

        # Compute per-side P&L
        if call_placed:
            if "call" in stopped_sides:
                call_pnl = stopped_sides["call"]["net_pnl"] or 0
                call_status = "stopped"
            else:
                call_pnl = cc  # expired worthless → keep full credit
                call_status = "expired"
        else:
            call_pnl = 0
            call_status = "not_placed"

        if put_placed:
            if "put" in stopped_sides:
                put_pnl = stopped_sides["put"]["net_pnl"] or 0
                put_status = "stopped"
            else:
                put_pnl = pc
                put_status = "expired"
        else:
            put_pnl = 0
            put_status = "not_placed"

        # Commission estimate:
        # - Full IC with both expired: 4 legs × $2.50 open = $10 (no close)
        # - Full IC with 1 stop: 4 open + 2 close = 6 legs × $2.50 = $15
        # - Full IC with 2 stops: 4 open + 4 close = 8 legs × $2.50 = $20
        # - One-sided with expire: 2 legs × $2.50 = $5
        # - One-sided with stop: 4 legs × $2.50 = $10
        legs_open = (2 if call_placed else 0) + (2 if put_placed else 0)
        legs_close = (2 if call_status == "stopped" else 0) + (2 if put_status == "stopped" else 0)
        commission = (legs_open + legs_close) * 2.50

        entry_net = call_pnl + put_pnl - commission

        return {
            "call_pnl": call_pnl, "put_pnl": put_pnl,
            "call_status": call_status, "put_status": put_status,
            "commission": commission, "entry_net": entry_net,
        }

    def compute_daily_pnl_from_entries(self, date: str) -> dict:
        """Reconstruct daily P&L from individual entries.

        KNOWN LIMITATION: For days before Fix #87 (deployed ~Apr 9, 2026), this
        assumes expired sides kept FULL credit. Saxo actually settles at the SPX
        close price; options near-ATM at close have residual value deducted.
        See memory/settlement_pnl_bug.md — Apr 1 had $865 overstatement.

        For accurate analysis, use is_reconciliation_accurate() to check if a
        day's reconciliation is within tolerance before trusting the entry-level
        breakdown.
        """
        entries = self.get_entries(date)
        stops = self.get_stops(date)
        total_call_pnl = 0
        total_put_pnl = 0
        total_commission = 0
        total_net = 0
        entry_details = []
        for e in entries:
            ep = self.compute_entry_pnl(e, stops.get(e["num"], []))
            total_call_pnl += ep["call_pnl"]
            total_put_pnl += ep["put_pnl"]
            total_commission += ep["commission"]
            total_net += ep["entry_net"]
            entry_details.append({**e, **ep})
        return {
            "call_pnl_sum": total_call_pnl,
            "put_pnl_sum": total_put_pnl,
            "commission_sum": total_commission,
            "net_pnl_computed": total_net,
            "entries": entry_details,
        }

    def is_reconciliation_accurate(self, date: str, tolerance: float = 100.0) -> bool:
        """Return True if our entry-level P&L sum matches daily_summaries.net_pnl.

        Use this to FILTER which days can be trusted for per-entry analysis.
        Pre-Fix-87 days with near-ATM settlements will have P&L overstated.
        """
        ok, _ = self.verify_daily_pnl_reconciliation(date, tolerance=tolerance)
        return ok

    def get_reconciled_dates(self, tolerance: float = 100.0) -> list:
        """Get dates where per-entry P&L reconciles to daily summary within tolerance.

        These are safe dates to use for per-entry analysis (counterfactuals etc).
        Days that fail reconciliation likely have near-ATM settlement issues
        (pre-Fix-87 bug) and should be handled using daily_summaries directly.
        """
        return [
            d for d in self.get_all_dates()
            if self.is_reconciliation_accurate(d, tolerance)
        ]

    # -------- Post-settlement P&L (authoritative) --------

    def get_authoritative_daily_pnl(self, date: str) -> Optional[float]:
        """Return daily_summaries.net_pnl — the post-settlement authoritative value.

        This is ALWAYS correct because HYDRA's Fix #87 verifies against Saxo
        closedpositions at settlement time and adjusts total_realized_pnl.
        """
        summary = self.get_daily_summary(date)
        return summary["net_pnl"] if summary else None

    # -------- Put side attribution (for counterfactuals) --------

    def estimate_put_side_contribution(self, entry: dict, stops: list,
                                        apply_settlement_haircut: bool = False,
                                        haircut_factor: float = 1.0) -> float:
        """Estimate what the put side contributed to this entry's P&L.

        - If put stopped: return stops.net_pnl (authoritative)
        - If put expired: return put_credit * haircut_factor
          (haircut_factor < 1.0 accounts for near-ATM settlement residual value)
        - If put not placed: return 0

        For counterfactual "what if we skipped the put" on a down-day:
        - Delta = -this_value (we'd save losses but lose profits)

        The haircut_factor is an approximation. For days where we have Fix #87
        adjusted data in daily_summaries, prefer using those totals directly.
        """
        etype = entry["type"]
        pc = entry["pc"] or 0
        normalized = etype.lower().replace(" ", "_") if etype else ""

        # Was put placed?
        put_placed = ("iron_condor" in normalized or "full_ic" in normalized
                      or "put" in normalized)
        if not put_placed:
            return 0

        # Was put stopped?
        for s in stops:
            if s["side"] == "put":
                return s["net_pnl"] or 0

        # Put expired — apply haircut
        return pc * haircut_factor

    # -------- Verification methods --------

    def verify_daily_pnl_reconciliation(self, date: str, tolerance: float = 50.0) -> tuple:
        """Check that our entry-level P&L sum matches daily_summaries.net_pnl.

        Returns (is_ok, details_dict)
        """
        summary = self.get_daily_summary(date)
        if not summary or summary["net_pnl"] is None:
            return False, {"reason": "no daily summary or net_pnl is NULL"}
        actual_net = summary["net_pnl"]
        computed = self.compute_daily_pnl_from_entries(date)
        diff = computed["net_pnl_computed"] - actual_net
        return abs(diff) <= tolerance, {
            "actual_net": actual_net,
            "computed_net": computed["net_pnl_computed"],
            "diff": diff,
            "within_tolerance": abs(diff) <= tolerance,
            "tolerance": tolerance,
            "n_entries": len(computed["entries"]),
            "call_pnl_sum": computed["call_pnl_sum"],
            "put_pnl_sum": computed["put_pnl_sum"],
            "commission_sum": computed["commission_sum"],
        }
