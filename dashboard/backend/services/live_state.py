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

        # Count entries the SAME way the authoritative daily_summaries row does
        # (bots/hydra/strategy.py record_daily_summary, lines ~4798-4808): PER
        # ENTRY, with DISJOINT stopped/expired buckets. NOT a sum of per-side
        # counters (that double-counts a both-sides-stopped IC → 2 not 1) and
        # NOT a residual (completed - stopped mis-attributes). Per-entry on the
        # *_side_stopped flags also correctly counts Brandon TP/BREACH closes,
        # which set those flags but do NOT bump call_stops_triggered/
        # put_stops_triggered. This keeps the live fallback and the DB row
        # identical so the calendar/analytics numbers don't flip at the HOMER
        # handoff.
        #   entries_placed  = entries_completed (excludes skipped/failed)
        #   entries_stopped = entries with call_side_stopped OR put_side_stopped
        #   entries_expired = entries NOT stopped AND (call/put _side_expired)
        stopped = 0
        expired = 0
        for e in entries:
            if e.get("call_side_stopped") or e.get("put_side_stopped"):
                stopped += 1
            elif e.get("call_side_expired") or e.get("put_side_expired"):
                expired += 1
        completed = state.get("entries_completed")
        if completed is None:
            completed = sum(1 for e in entries if e.get("is_complete"))

        # After market close (4 PM ET), add unrealized credits from active entries
        # that will expire worthless. total_realized_pnl only includes settled entries,
        # but active entries' credits are guaranteed profit on 0DTE after 4 PM.
        try:
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

        # Today's SPX/VIX. Prefer the bot's tracked intraday OHLC, which is
        # ALWAYS written to state.market_data_ohlc. The per-entry
        # spx_at_entry/vix_at_entry are NOT written by the bot (audit FP6), so
        # the old reads were always None and relied on the DB fallback. The
        # state file has NO close price (market_data_ohlc only carries
        # open/high/low, and pnl_history points are {"time","pnl"} with no
        # price keys), so close comes only from the market_ticks DB fallback
        # below and otherwise falls back to open (see spx_close/vix_close `or`
        # defaulting after the fallback).
        ohlc = state.get("market_data_ohlc", {}) or {}
        spx_open = ohlc.get("spx_open") or (entries[0].get("spx_at_entry") if entries else None)
        spx_high = ohlc.get("spx_high")
        spx_low = ohlc.get("spx_low")
        spx_close = None
        vix_open = ohlc.get("vix_open") or (entries[0].get("vix_at_entry") if entries else None)
        vix_close = None

        # Fill any STILL-missing SPX/VIX (esp. close) from today's market_ticks.
        # Uses `or` so the exact OHLC above is preserved when present.
        if (spx_close is None or spx_open is None or vix_close is None) and self._db_reader:
            try:
                import sqlite3
                conn = sqlite3.connect(self._db_reader.db_path)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT spx_price, vix_level FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp",
                    (f"{get_today_et()}%",),
                ).fetchall()
                conn.close()
                spx_prices = [r["spx_price"] for r in rows if r["spx_price"]]
                vix_levels = [r["vix_level"] for r in rows if r["vix_level"]]
                if spx_prices:
                    spx_open = spx_open or spx_prices[0]
                    spx_close = spx_close or spx_prices[-1]
                    spx_high = spx_high or max(spx_prices)
                    spx_low = spx_low or min(spx_prices)
                if vix_levels:
                    vix_open = vix_open or vix_levels[0]
                    vix_close = vix_close or vix_levels[-1]
            except Exception as e:
                logger.debug(f"SPX/VIX tick fallback failed: {e}")
        spx_close = spx_close or spx_open
        vix_close = vix_close or vix_open

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
            "entries_placed": completed,
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
            for side, flag, time_key, stop_key, actual_key in [
                ("call", "call_side_stopped", "call_stop_time", "call_side_stop", "actual_call_stop_debit"),
                ("put", "put_side_stopped", "put_stop_time", "put_side_stop", "actual_put_stop_debit"),
            ]:
                if not e.get(flag):
                    continue
                credit_key = f"{side}_spread_credit"
                side_credit = e.get(credit_key, 0) or 0
                stop_level = e.get(stop_key, 0) or 0
                # Prefer the real fill cost (includes slippage) the bot serialized
                # in actual_*_stop_debit; fall back to the trigger level when it is
                # absent/zero (dry-run or fills unavailable). Mirrors the canonical
                # pattern in bots/hydra/base_strategy.py (~lines 473-491) and the
                # bot's own DB write (record_stop: trigger_level=stop_level,
                # actual_debit=actual_close_cost).
                actual_debit_val = e.get(actual_key, 0) or 0
                debit = actual_debit_val if actual_debit_val > 0 else stop_level
                result.append({
                    "date": today,
                    "entry_number": entry_num,
                    "side": side,
                    "stop_time": e.get(time_key),
                    "trigger_level": stop_level,
                    "actual_debit": debit,
                    "net_pnl": side_credit - debit if debit else None,
                })
        return result

    def get_today_net_pnl(self) -> Optional[float]:
        """Get today's net P&L for performance metrics.

        After market close (4 PM ET), includes unrealized credits from active
        entries that will expire worthless (0DTE). This matches the logic in
        get_today_summary() so Performance and Cumulative show consistent data.
        """
        summary = self.get_today_summary()
        if summary is None:
            return None
        return summary.get("net_pnl", 0)

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
