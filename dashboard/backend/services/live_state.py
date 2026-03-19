"""Extract today's live data from hydra_state.json in DB-compatible formats.

During trading hours (and until HOMER runs at 5:30 PM ET), SQLite has no data
for today. This module bridges the gap by converting state file data into the
same schema the DB readers return, so REST endpoints can fall back to live data.
"""

import logging
from datetime import datetime
from typing import Optional

from dashboard.backend.services.state_reader import StateFileReader
from dashboard.backend.services.market_status import get_today_et

logger = logging.getLogger("dashboard.live_state")


class LiveStateProvider:
    """Provides today's data from state file in DB-compatible format."""

    def __init__(self, state_reader: StateFileReader, db_reader=None):
        self._reader = state_reader
        self._db_reader = db_reader

    def _get_today_state(self) -> Optional[dict]:
        """Get state if it's for today."""
        state = self._reader.get_cached() or self._reader.read_latest()
        if not state:
            return None
        if state.get("date") != get_today_et():
            return None
        return state

    def get_today_summary(self) -> Optional[dict]:
        """Build a daily_summaries-compatible row from today's state."""
        state = self._get_today_state()
        if not state:
            return None

        entries = state.get("entries", [])
        if not entries:
            return None

        gross_pnl = state.get("total_realized_pnl", 0)
        commission = state.get("total_commission", 0)

        # Count stopped and expired entries (per entry, not per side)
        stopped = 0
        expired = 0
        for e in entries:
            if e.get("call_side_stopped") or e.get("put_side_stopped"):
                stopped += 1
            if e.get("call_side_expired") or e.get("put_side_expired"):
                expired += 1

        # After market close (4 PM ET), add unrealized credits from active entries
        # that will expire worthless. total_realized_pnl only includes settled entries,
        # but active entries' credits are guaranteed profit on 0DTE after 4 PM.
        try:
            now_et = datetime.now()
            # Simple ET approximation: check if hour >= 16 (4 PM)
            # The state file date check above ensures we're looking at today
            import zoneinfo
            et_tz = zoneinfo.ZoneInfo("America/New_York")
            now_et = datetime.now(et_tz)
            if now_et.hour >= 16:
                for e in entries:
                    call_done = e.get("call_side_stopped") or e.get("call_side_expired") or e.get("call_side_skipped")
                    put_done = e.get("put_side_stopped") or e.get("put_side_expired") or e.get("put_side_skipped")
                    if not call_done:
                        # Active call side will expire — add its credit
                        gross_pnl += e.get("call_spread_credit", 0) or 0
                    if not put_done:
                        # Active put side will expire — add its credit
                        gross_pnl += e.get("put_spread_credit", 0) or 0
        except Exception:
            pass  # Fall back to pre-settlement value

        net_pnl = gross_pnl - commission

        # Get SPX/VIX from first and last entry or pnl_history
        pnl_history = state.get("pnl_history", [])
        spx_open = entries[0].get("spx_at_entry") if entries else None
        spx_close = pnl_history[-1].get("spx") if pnl_history else spx_open
        spx_values = [e.get("spx_at_entry", 0) for e in entries if e.get("spx_at_entry")]
        if pnl_history:
            spx_values.extend(p.get("spx", 0) for p in pnl_history if p.get("spx"))
        spx_high = max(spx_values) if spx_values else None
        spx_low = min(spx_values) if spx_values else None

        vix_open = entries[0].get("vix_at_entry") if entries else None
        vix_close = (pnl_history[-1].get("vix") if pnl_history else vix_open) or vix_open

        # Fallback: if SPX/VIX still None, try market_ticks from DB
        if (spx_open is None or spx_close is None) and self._db_reader:
            try:
                import sqlite3
                from asyncio import get_event_loop
                conn = sqlite3.connect(self._db_reader._db_path)
                conn.row_factory = sqlite3.Row
                today = get_today_et()
                rows = conn.execute(
                    "SELECT spx_price, vix_level FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp",
                    (f"{today}%",),
                ).fetchall()
                conn.close()
                if rows:
                    spx_prices = [r["spx_price"] for r in rows if r["spx_price"]]
                    vix_levels = [r["vix_level"] for r in rows if r["vix_level"]]
                    if spx_prices:
                        spx_open = spx_open or spx_prices[0]
                        spx_close = spx_prices[-1]
                        spx_high = max(spx_prices)
                        spx_low = min(spx_prices)
                    if vix_levels:
                        vix_open = vix_open or vix_levels[0]
                        vix_close = vix_levels[-1]
            except Exception as e:
                logger.debug(f"SPX/VIX tick fallback failed: {e}")

        today = get_today_et()
        try:
            day_of_week = datetime.strptime(today, "%Y-%m-%d").strftime("%A")
        except ValueError:
            day_of_week = ""

        return {
            "date": today,
            "spx_open": spx_open,
            "spx_close": spx_close,
            "spx_high": spx_high,
            "spx_low": spx_low,
            "day_range": round(spx_high - spx_low, 2) if spx_high and spx_low else None,
            "vix_open": vix_open,
            "vix_close": vix_close,
            "entries_placed": len(entries),
            "entries_stopped": stopped,
            "entries_expired": expired,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "commission": commission,
            "day_of_week": day_of_week,
        }

    def get_today_entries(self) -> list[dict]:
        """Build trade_entries-compatible rows from today's state."""
        state = self._get_today_state()
        if not state:
            return []

        today = get_today_et()
        result = []
        for e in state.get("entries", []):
            call_credit = e.get("call_spread_credit", 0) or 0
            put_credit = e.get("put_spread_credit", 0) or 0

            entry_type = "IC"
            if e.get("call_only"):
                entry_type = "CALL"
            elif e.get("put_only"):
                entry_type = "PUT"

            # Hide strikes for skipped sides (call-only → no put strikes, put-only → no call strikes)
            is_call_only = e.get("call_only", False)
            is_put_only = e.get("put_only", False)

            result.append({
                "date": today,
                "entry_number": e.get("entry_number"),
                "entry_time": e.get("entry_time"),
                "spx_at_entry": e.get("spx_at_entry"),
                "vix_at_entry": e.get("vix_at_entry"),
                "trend_signal": e.get("trend_signal", "neutral"),
                "entry_type": entry_type,
                "override_reason": e.get("override_reason", ""),
                "short_call_strike": e.get("short_call_strike") if not is_put_only else None,
                "long_call_strike": e.get("long_call_strike") if not is_put_only else None,
                "short_put_strike": e.get("short_put_strike") if not is_call_only else None,
                "long_put_strike": e.get("long_put_strike") if not is_call_only else None,
                "call_credit": call_credit if not is_put_only else 0,
                "put_credit": put_credit if not is_call_only else 0,
                "total_credit": call_credit + put_credit,
                "otm_distance_call": e.get("otm_distance_call") if not is_put_only else None,
                "otm_distance_put": e.get("otm_distance_put") if not is_call_only else None,
            })
        return result

    def get_today_stops(self) -> list[dict]:
        """Build trade_stops-compatible rows from today's state."""
        state = self._get_today_state()
        if not state:
            return []

        today = get_today_et()
        result = []
        for e in state.get("entries", []):
            entry_num = e.get("entry_number")
            for side, flag, time_key, stop_key in [
                ("call", "call_side_stopped", "call_stop_time", "call_side_stop"),
                ("put", "put_side_stopped", "put_stop_time", "put_side_stop"),
            ]:
                if not e.get(flag):
                    continue
                credit_key = f"{side}_spread_credit"
                side_credit = e.get(credit_key, 0) or 0
                stop_level = e.get(stop_key, 0) or 0
                result.append({
                    "date": today,
                    "entry_number": entry_num,
                    "side": side,
                    "stop_time": e.get(time_key),
                    "trigger_level": stop_level,
                    "actual_debit": stop_level,  # Best estimate from state
                    "net_pnl": side_credit - stop_level if stop_level else None,
                })
        return result

    def get_today_net_pnl(self) -> Optional[float]:
        """Get today's net P&L for performance metrics."""
        state = self._get_today_state()
        if not state:
            return None
        entries = state.get("entries", [])
        if not entries:
            return None
        gross = state.get("total_realized_pnl", 0)
        commission = state.get("total_commission", 0)
        return gross - commission

    def get_today_replay_pnl(self) -> list[dict]:
        """Build replay P&L curve from pnl_history in state file."""
        state = self._get_today_state()
        if not state:
            return []

        pnl_history = state.get("pnl_history", [])
        if not pnl_history:
            return []

        result = []
        for point in pnl_history:
            ts = point.get("time", "")
            if not ts:
                continue
            # pnl_history format: {"time": "HH:MM", "pnl": float}
            # or possibly "HH:MM:SS" or "YYYY-MM-DD HH:MM:SS"
            time_part = ts[-5:] if len(ts) >= 5 else ts  # Extract "HH:MM"
            result.append({
                "time": time_part,
                "pnl": round(point.get("pnl", 0), 2),
            })
        return result
