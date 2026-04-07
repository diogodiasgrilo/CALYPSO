"""Orchestrates file watchers and broadcasts changes via WebSocket."""

import asyncio
import logging
import time

from dashboard.backend.config import settings
from dashboard.backend.services.state_reader import StateFileReader
from dashboard.backend.services.metrics_reader import MetricsFileReader
from dashboard.backend.services.db_reader import BacktestingDBReader
from dashboard.backend.services.log_tailer import LogTailer
from dashboard.backend.services.live_ohlc import LiveOHLCBuilder
from dashboard.backend.services.live_state import LiveStateProvider
from dashboard.backend.services.market_status import get_current_status, get_today_et
from dashboard.backend.services.agent_reports import AgentReportReader
from dashboard.backend.ws.manager import ConnectionManager

logger = logging.getLogger("dashboard.broadcaster")


class Broadcaster:
    """Polls data sources and broadcasts changes to WebSocket clients."""

    def __init__(self, manager: ConnectionManager):
        self.manager = manager
        self.state_reader = StateFileReader(settings.hydra_state_file)
        self.metrics_reader = MetricsFileReader(settings.hydra_metrics_file)
        self.db_reader = BacktestingDBReader(settings.backtesting_db)
        self.log_tailer = LogTailer(settings.hydra_log_file)
        self.live_ohlc = LiveOHLCBuilder()
        self.live_state = LiveStateProvider(self.state_reader, db_reader=self.db_reader)
        self.agent_reader = AgentReportReader(settings.agent_intel_dir)
        self._tasks: list[asyncio.Task] = []
        self._last_ohlc_bar_count: int = 0
        self._last_stop_count: int = 0
        self._last_agent_status: list[dict] = []
        self._current_date: str = ""

    async def start(self) -> None:
        """Start all polling tasks."""
        logger.info("Starting broadcaster tasks")

        # Bootstrap live OHLC from today's log history (fills gap after restart)
        today = get_today_et()
        history = self.log_tailer.read_today_history(today)
        if history:
            changed = self.live_ohlc.process_log_lines(history)
            bars = self.live_ohlc.get_ohlc_bars()
            logger.info(f"Bootstrapped {len(bars)} live OHLC bars from log history")

        self.log_tailer.seek_to_end()

        self._tasks = [
            asyncio.create_task(self._poll_state(), name="state_watcher"),
            asyncio.create_task(self._poll_metrics(), name="metrics_watcher"),
            asyncio.create_task(self._poll_ohlc(), name="ohlc_watcher"),
            asyncio.create_task(self._poll_logs(), name="log_watcher"),
            asyncio.create_task(self._poll_market_status(), name="market_status"),
            asyncio.create_task(self._poll_agents(), name="agent_watcher"),
            asyncio.create_task(self._heartbeat(), name="ws_heartbeat"),
        ]

    async def stop(self) -> None:
        """Cancel all polling tasks."""
        for task in self._tasks:
            task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.error("Broadcaster tasks did not cancel within 10s")
        self._tasks.clear()
        logger.info("Broadcaster stopped")

    async def _get_merged_ohlc(self) -> list[dict]:
        """Get OHLC bars: SQLite historical + live heartbeat bars.

        During market hours, live bars from heartbeat parsing fill the gap
        until HOMER writes to SQLite post-market. Live bars for timestamps
        already in SQLite are skipped (SQLite is authoritative).
        """
        today = get_today_et()
        db_bars: list[dict] = []

        if await self.db_reader.is_available():
            db_bars = await self.db_reader.get_today_ohlc(today)

        live_bars = self.live_ohlc.get_ohlc_bars()

        if not live_bars:
            return db_bars
        if not db_bars:
            return live_bars

        # Merge: SQLite is authoritative. Only add live bars not in SQLite.
        db_timestamps = {b["timestamp"] for b in db_bars}
        merged = list(db_bars)
        for bar in live_bars:
            if bar["timestamp"] not in db_timestamps:
                merged.append(bar)

        merged.sort(key=lambda b: b["timestamp"])
        return merged

    async def get_snapshot(self) -> dict:
        """Build a full snapshot for newly connected clients."""
        state = self.state_reader.read_latest()
        metrics = self.metrics_reader.read_latest()

        today = get_today_et()
        entries = []
        stops = []

        if await self.db_reader.is_available():
            entries = await self.db_reader.get_entries_for_date(today)
            stops = await self.db_reader.get_stops_for_date(today)

        # Fall back to state file for today's entries/stops when DB is empty
        if not entries:
            entries = self.live_state.get_today_entries()
        if not stops:
            stops = self.live_state.get_today_stops()

        # Merge live-detected stop events (during market hours, DB is empty)
        live_stops = self.state_reader.get_stop_events()
        if live_stops:
            db_keys = {(s.get("entry_number"), s.get("side")) for s in stops}
            for ls in live_stops:
                if (ls["entry_number"], ls["side"]) not in db_keys:
                    stops.append(ls)

        ohlc = await self._get_merged_ohlc()
        market = get_current_status()
        agents = self.agent_reader.get_all_agent_status()
        comparisons = None
        if await self.db_reader.is_available():
            comparisons = await self.db_reader.get_comparison_stats()

        # After market close, augment comparisons with today's data
        from dashboard.backend.services.market_status import is_after_market_close as _is_amc
        if _is_amc() and comparisons and self.live_state:
            today_summary = self.live_state.get_today_summary()
            today_entries = self.live_state.get_today_entries()
            if today_summary:
                n = comparisons.get("total_days", 0)
                today_pnl = today_summary.get("net_pnl", 0)
                today_entries_count = today_summary.get("entries_placed", 0)
                today_stops = today_summary.get("entries_stopped", 0)
                today_credit = sum(e.get("total_credit", 0) for e in (today_entries or []))
                if n > 0:
                    comparisons = dict(comparisons)
                    comparisons["avg_pnl"] = ((comparisons.get("avg_pnl") or 0) * n + today_pnl) / (n + 1)
                    comparisons["avg_entries"] = ((comparisons.get("avg_entries") or 0) * n + today_entries_count) / (n + 1)
                    comparisons["avg_stops"] = ((comparisons.get("avg_stops") or 0) * n + today_stops) / (n + 1)
                    if comparisons.get("avg_credit") is not None:
                        comparisons["avg_credit"] = (comparisons["avg_credit"] * n + today_credit) / (n + 1)
                    comparisons["best_day"] = max(comparisons.get("best_day") or 0, today_pnl)
                    comparisons["worst_day"] = min(comparisons.get("worst_day") or 0, today_pnl)
                    comparisons["total_days"] = n + 1

        # After market close, augment metrics with today's live P&L
        # so late-connecting clients see updated Cumulative + Performance
        from dashboard.backend.services.market_status import is_after_market_close
        performance_pnls = None
        if is_after_market_close() and metrics:
            today_pnl = self.live_state.get_today_net_pnl()
            if today_pnl is not None:
                today = get_today_et()
                if metrics.get("last_updated") != today:
                    metrics = dict(metrics)
                    metrics["cumulative_pnl"] = metrics.get("cumulative_pnl", 0) + today_pnl
                    if today_pnl >= 0:
                        metrics["winning_days"] = metrics.get("winning_days", 0) + 1
                    else:
                        metrics["losing_days"] = metrics.get("losing_days", 0) + 1

                # Build performance P&L array with today included
                pnls = await self.db_reader.get_daily_pnls()
                summaries = await self.db_reader.get_daily_summaries(limit=1)
                if not summaries or summaries[0].get("date") != today:
                    pnls = list(pnls) + [today_pnl]
                performance_pnls = pnls

        snapshot = {
            "type": "snapshot",
            "state": state,
            "metrics": metrics,
            "market": market,
            "agents": agents,
            "comparisons": comparisons,
            "today_entries": entries,
            "today_stops": stops,
            "today_ohlc": ohlc,
            "clients": self.manager.client_count,
        }
        if performance_pnls is not None:
            snapshot["performance_pnls"] = performance_pnls
        return snapshot

    # -- Polling loops --

    def _check_day_rollover(self) -> None:
        """Reset day-scoped counters when the ET date changes."""
        today = get_today_et()
        if self._current_date and today != self._current_date:
            logger.info(f"Day rollover detected: {self._current_date} → {today}")
            self._last_ohlc_bar_count = 0
            self._last_stop_count = 0
            self.live_ohlc = LiveOHLCBuilder()
        self._current_date = today

    async def _poll_state(self) -> None:
        """Poll hydra_state.json for changes."""
        while True:
            try:
                self._check_day_rollover()
                data = self.state_reader.read_if_changed()
                if data is not None:
                    await self.manager.broadcast({
                        "type": "state_update",
                        "data": data,
                    })

                # Check for new stop events
                stop_events = self.state_reader.get_stop_events()
                if len(stop_events) > self._last_stop_count:
                    self._last_stop_count = len(stop_events)
                    await self.manager.broadcast({
                        "type": "stop_events",
                        "data": stop_events,
                    })
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"State poll error: {e}")
            await asyncio.sleep(settings.state_poll_interval)

    async def _poll_metrics(self) -> None:
        """Poll hydra_metrics.json for changes.

        After market close, augments cumulative metrics with today's live P&L
        (before the bot writes hydra_metrics.json) and pushes performance data
        so the dashboard updates immediately at 4:00 PM ET.
        """
        from dashboard.backend.services.market_status import is_after_market_close
        _sent_today_augmented = False

        while True:
            try:
                data = self.metrics_reader.read_if_changed()
                if data is not None:
                    # When metrics file updates (bot wrote it), reset flag for next day
                    _sent_today_augmented = False
                    await self.manager.broadcast({
                        "type": "metrics_update",
                        "data": data,
                    })

                # After market close, augment metrics with today's live P&L
                # so Cumulative + Performance update before bot writes metrics file
                if is_after_market_close() and not _sent_today_augmented:
                    today_pnl = self.live_state.get_today_net_pnl()
                    if today_pnl is not None:
                        _sent_today_augmented = True

                        # Augment cumulative metrics with today's P&L
                        base_metrics = self.metrics_reader.read_latest() or {}
                        if base_metrics:
                            today = get_today_et()
                            # Only augment if metrics file hasn't been updated for today yet
                            if base_metrics.get("last_updated") != today:
                                augmented = dict(base_metrics)
                                augmented["cumulative_pnl"] = base_metrics.get("cumulative_pnl", 0) + today_pnl
                                if today_pnl >= 0:
                                    augmented["winning_days"] = base_metrics.get("winning_days", 0) + 1
                                else:
                                    augmented["losing_days"] = base_metrics.get("losing_days", 0) + 1
                                await self.manager.broadcast({
                                    "type": "metrics_update",
                                    "data": augmented,
                                })

                        # Push performance data (daily P&L array + today)
                        pnls = await self.db_reader.get_daily_pnls()
                        today = get_today_et()
                        summaries = await self.db_reader.get_daily_summaries(limit=1)
                        if not summaries or summaries[0].get("date") != today:
                            pnls = list(pnls) + [today_pnl]
                        await self.manager.broadcast({
                            "type": "performance_update",
                            "data": {"count": len(pnls), "daily_pnls": pnls},
                        })

                        # Push augmented comparisons (avg P&L, avg stops, etc.)
                        comparisons = await self.db_reader.get_comparison_stats()
                        if comparisons:
                            today_summary = self.live_state.get_today_summary()
                            today_entries = self.live_state.get_today_entries()
                            if today_summary:
                                n = comparisons.get("total_days", 0)
                                if n > 0:
                                    comparisons = dict(comparisons)
                                    tp = today_summary.get("net_pnl", 0)
                                    te = today_summary.get("entries_placed", 0)
                                    ts = today_summary.get("entries_stopped", 0)
                                    tc = sum(e.get("total_credit", 0) for e in (today_entries or []))
                                    comparisons["avg_pnl"] = ((comparisons.get("avg_pnl") or 0) * n + tp) / (n + 1)
                                    comparisons["avg_entries"] = ((comparisons.get("avg_entries") or 0) * n + te) / (n + 1)
                                    comparisons["avg_stops"] = ((comparisons.get("avg_stops") or 0) * n + ts) / (n + 1)
                                    if comparisons.get("avg_credit") is not None:
                                        comparisons["avg_credit"] = (comparisons["avg_credit"] * n + tc) / (n + 1)
                                    comparisons["best_day"] = max(comparisons.get("best_day") or 0, tp)
                                    comparisons["worst_day"] = min(comparisons.get("worst_day") or 0, tp)
                                    comparisons["total_days"] = n + 1
                                    await self.manager.broadcast({
                                        "type": "comparisons_update",
                                        "data": comparisons,
                                    })

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Metrics poll error: {e}")
            await asyncio.sleep(settings.metrics_poll_interval)

    async def _poll_ohlc(self) -> None:
        """Periodically broadcast merged OHLC (SQLite + live bars)."""
        while True:
            try:
                ohlc = await self._get_merged_ohlc()
                if len(ohlc) > self._last_ohlc_bar_count:
                    self._last_ohlc_bar_count = len(ohlc)
                    await self.manager.broadcast({
                        "type": "ohlc_update",
                        "data": ohlc,
                    })
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"OHLC poll error: {e}")
            await asyncio.sleep(settings.db_poll_interval)

    async def _poll_logs(self) -> None:
        """Poll bot.log for new lines and feed to live OHLC builder."""
        while True:
            try:
                lines = self.log_tailer.read_new_lines()
                if lines:
                    # Feed heartbeat lines to live OHLC builder
                    ohlc_changed = self.live_ohlc.process_log_lines(lines)

                    # Broadcast log lines to clients
                    await self.manager.broadcast({
                        "type": "log_lines",
                        "data": lines,
                    })

                    # If OHLC bars changed, broadcast update immediately
                    if ohlc_changed:
                        ohlc = self.live_ohlc.get_ohlc_bars()
                        if len(ohlc) > self._last_ohlc_bar_count:
                            self._last_ohlc_bar_count = len(ohlc)
                        await self.manager.broadcast({
                            "type": "ohlc_update",
                            "data": await self._get_merged_ohlc(),
                        })
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Log poll error: {e}")
            await asyncio.sleep(settings.log_poll_interval)

    async def _poll_market_status(self) -> None:
        """Broadcast market status periodically."""
        while True:
            try:
                status = get_current_status()
                await self.manager.broadcast({
                    "type": "market_status",
                    "data": status,
                })
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Market status poll error: {e}")
            await asyncio.sleep(settings.market_status_interval)

    async def _poll_agents(self) -> None:
        """Poll agent report directories for status changes (every 60s)."""
        while True:
            try:
                agents = self.agent_reader.get_all_agent_status()
                # Only broadcast if status changed (compare last_run timestamps)
                serialized = str([(a.get("agent"), a.get("last_run")) for a in agents])
                last_serialized = str([(a.get("agent"), a.get("last_run")) for a in self._last_agent_status])
                if serialized != last_serialized:
                    self._last_agent_status = agents
                    await self.manager.broadcast({
                        "type": "agents_update",
                        "data": agents,
                    })
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Agent poll error: {e}")
            await asyncio.sleep(60)  # Check every 60 seconds

    async def _heartbeat(self) -> None:
        """Send periodic heartbeat to keep connections alive."""
        while True:
            try:
                await self.manager.broadcast({
                    "type": "heartbeat",
                    "timestamp": time.time(),
                    "clients": self.manager.client_count,
                })
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
            await asyncio.sleep(settings.ws_heartbeat_interval)
