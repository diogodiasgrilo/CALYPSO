"""Orchestrates file watchers and broadcasts changes via WebSocket."""

import asyncio
import logging
import time
from pathlib import Path

from dashboard.backend.config import settings
from dashboard.backend.services.state_reader import StateFileReader
from dashboard.backend.services.metrics_reader import MetricsFileReader
from dashboard.backend.services.db_reader import BacktestingDBReader, apply_db_cumulative
from dashboard.backend.services.log_tailer import LogTailer
from dashboard.backend.services.live_ohlc import LiveOHLCBuilder
from dashboard.backend.services.live_state import LiveStateProvider
from dashboard.backend.services.market_status import get_current_status, get_today_et
from dashboard.backend.services.agent_reports import AgentReportReader
from dashboard.backend.services.brandon_hedge_reader import read_overlays_by_entry
from dashboard.backend.ws.manager import ConnectionManager

logger = logging.getLogger("dashboard.broadcaster")


def _on_or_after_baseline(date_str: str) -> bool:
    """True if date_str (ISO YYYY-MM-DD) is on/after the cumulative rebase
    baseline, or if no baseline is set. Gates today's-P&L augmentation so a
    pre-baseline 'today' (e.g. baseline=tomorrow) isn't folded into the rebased
    'since <baseline>' cumulative card/curve."""
    baseline = (settings.baseline_date or "").strip()
    return (not baseline) or (str(date_str) >= baseline)


class Broadcaster:
    """Polls data sources and broadcasts changes to WebSocket clients."""

    def __init__(self, manager: ConnectionManager):
        self.manager = manager
        # Stream the CURRENT live seat (dry_run=false among b/c), resolved at
        # startup — so the WS "main" view follows a C<->B swap once the dashboard
        # restarts (the swap procedure restarts it). The request endpoints follow
        # per-request without a restart; the WS follows at its next start.
        from dashboard.backend.services.variant_readers import (
            live_state_file, live_metrics_file, live_log_file, live_backtesting_db,
        )
        self.state_reader = StateFileReader(live_state_file())
        self.metrics_reader = MetricsFileReader(live_metrics_file())
        self.db_reader = BacktestingDBReader(live_backtesting_db())
        # Brandon defensive-overlay hedge sidecar for the live seat — resolved
        # once at startup like the readers above (a B<->C swap needs a
        # dashboard restart to follow, same as every other "live" path here).
        # Harmless when the live variant isn't a Brandon strategy: the reader
        # returns {} for a missing sidecar file.
        self._hedge_sidecar_path = Path(live_state_file()).parent / "brandon_hedge_legs.json"
        self._live_db_path = live_backtesting_db()
        # SPX/VIX price chart is account-agnostic — source it from the densest
        # recorder (config.market_data_db, e.g. A at ~4-8 ticks/min) so candles
        # have real bodies, not the flat dojis C's ~1 tick/min produces.
        self.market_db_reader = BacktestingDBReader(settings.market_data_db)
        self.log_tailer = LogTailer(live_log_file())
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
        """Get dense SPX OHLC bars for the price chart.

        Sourced from the market-data DB (densest recorder, e.g. A), which gives
        real candle bodies. Priority:
          1. HOMER's authoritative market_ohlc_1min (post-close).
          2. Live: compute dense bars from that DB's market_ticks (~4-8/min)
             — covers today before HOMER runs, and survives dashboard restarts
             (DB-backed, unlike the log-parsed builder which loses its place on
             a bot log-rotation).
          3. Last resort (market-data DB has nothing): the log-parsed live
             builder. Sparse (~1/min), but better than an empty chart; the
             frontend renders it as a line rather than crosses.
        """
        today = get_today_et()

        if await self.market_db_reader.is_available():
            db_bars = await self.market_db_reader.get_today_ohlc(today)
            if db_bars:
                return db_bars
            tick_bars = await self.market_db_reader.compute_ohlc_from_ticks(today)
            if tick_bars:
                return tick_bars

        return self.live_ohlc.get_ohlc_bars()

    def _merge_overlays(self, entries: list[dict], state: dict | None) -> list[dict]:
        """Attach Brandon defensive-overlay hedge data (the debit spread /
        butterfly placed against a threatened IC side) to each entry, the
        same enrichment strategies.py:_ic_snapshot already does for the
        non-primary polled-snapshot path — this is the primary/live WS path,
        which never carried it (2026-08-15 fix; overlays regularly drive most
        of B's daily P&L but were invisible on the default dashboard view).

        Cheap no-op when there's nothing to attach (no sidecar file yet, or a
        non-Brandon live variant) — returns entries unchanged rather than
        copying every dict on every 1s poll tick for no reason. ``state`` can
        be None (e.g. hydra_state.json doesn't exist yet) — get_snapshot()
        passes state_reader.read_latest() straight through unguarded, so this
        must not assume a dict (2026-08-17: a missing-state-file test caught
        this crashing the whole snapshot with AttributeError).
        """
        from dashboard.backend.routers.variants import _overlay_valuation_spx

        spx = _overlay_valuation_spx(self._live_db_path, state or {})
        overlays_by_entry = read_overlays_by_entry(str(self._hedge_sidecar_path), spx)
        if not overlays_by_entry:
            return entries
        merged = []
        for e in entries:
            e2 = dict(e)
            e2["overlays"] = overlays_by_entry.get(str(e2.get("entry_number")), [])
            merged.append(e2)
        return merged

    async def get_snapshot(self) -> dict:
        """Build a full snapshot for newly connected clients."""
        state = self.state_reader.read_latest()
        metrics = apply_db_cumulative(
            self.metrics_reader.read_latest(),
            await self.db_reader.get_cumulative_overrides(settings.baseline_date),
        )

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

        entries = self._merge_overlays(entries, state)

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
                # Don't fold today into the rebased cumulative if today is BEFORE
                # the baseline (e.g. baseline=tomorrow while today already traded):
                # the "since <baseline>" total must stay 0 until the baseline day.
                today_in_baseline = _on_or_after_baseline(today)
                if today_in_baseline and metrics.get("last_updated") != today:
                    metrics = dict(metrics)
                    metrics["cumulative_pnl"] = metrics.get("cumulative_pnl", 0) + today_pnl
                    if today_pnl >= 0:
                        metrics["winning_days"] = metrics.get("winning_days", 0) + 1
                    else:
                        metrics["losing_days"] = metrics.get("losing_days", 0) + 1

                # Build performance P&L array with today included
                pnls = await self.db_reader.get_daily_pnls(settings.baseline_date)
                summaries = await self.db_reader.get_daily_summaries(limit=1)
                if today_in_baseline and (not summaries or summaries[0].get("date") != today):
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
                    if isinstance(data.get("entries"), list):
                        data = dict(data)
                        data["entries"] = self._merge_overlays(data["entries"], data)
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
                    data = apply_db_cumulative(
                        data, await self.db_reader.get_cumulative_overrides(settings.baseline_date)
                    )
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

                        # Augment cumulative metrics with today's P&L. DB-canonical
                        # first so last_updated reflects the DB — if today's row is
                        # already in the DB the != today guard below skips the add
                        # (no double-count); during hours the DB lacks today so it adds.
                        base_metrics = apply_db_cumulative(
                            self.metrics_reader.read_latest() or {},
                            await self.db_reader.get_cumulative_overrides(settings.baseline_date),
                        ) or {}
                        today = get_today_et()
                        today_in_baseline = _on_or_after_baseline(today)
                        if base_metrics and today_in_baseline:
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
                        pnls = await self.db_reader.get_daily_pnls(settings.baseline_date)
                        summaries = await self.db_reader.get_daily_summaries(limit=1)
                        if today_in_baseline and (not summaries or summaries[0].get("date") != today):
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
