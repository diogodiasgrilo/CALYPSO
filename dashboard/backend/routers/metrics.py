"""Cumulative metrics and historical data endpoints."""

import re

from fastapi import APIRouter, Query

from dashboard.backend.config import settings
from dashboard.backend.services.metrics_reader import MetricsFileReader
from dashboard.backend.services.db_reader import BacktestingDBReader, apply_db_cumulative
from dashboard.backend.services.live_state import LiveStateProvider
from dashboard.backend.services.market_status import get_today_et, is_after_market_close

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

metrics_reader = MetricsFileReader(settings.hydra_metrics_file)
db_reader = BacktestingDBReader(settings.backtesting_db)

# The canonical reader (settings.backtesting_db) IS the live primary's DB, and
# `_live_state` tracks that same variant's state file — so today-from-live-state
# augmentation is only valid for the canonical id. Per-variant readers are built
# lazily so a missing settings field just makes that variant fall back, never
# crashes import.
_PRIMARY_ID = "c"
_variant_db_readers: dict[str, BacktestingDBReader] = {}


def _reader_for(strategy_id: str) -> tuple[BacktestingDBReader, bool]:
    """Return ``(reader, is_canonical)`` for a strategy id (History/Analytics
    per-strategy support). Empty / unknown / the primary id → the canonical
    reader (is_canonical=True, so today's live-state augmentation applies). A
    known non-primary variant → its own DB reader (is_canonical=False; we do
    NOT graft the primary's live `today` onto another variant's history)."""
    sid = (strategy_id or "").strip().lower()
    if not sid or sid == _PRIMARY_ID:
        return db_reader, True
    path = getattr(settings, f"variant_{sid}_backtesting_db", None)
    if path is None:
        return db_reader, True
    if sid not in _variant_db_readers:
        _variant_db_readers[sid] = BacktestingDBReader(path)
    return _variant_db_readers[sid], False

# Set by main.py at startup
_live_state: LiveStateProvider | None = None


def set_live_state(provider: LiveStateProvider) -> None:
    """Wire up the live state provider for today's data fallback."""
    global _live_state
    _live_state = provider


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _has_today(summaries: list[dict]) -> bool:
    """Check if today's date is already in the summaries."""
    today = get_today_et()
    return any(s.get("date") == today for s in summaries)


def _append_today_summary(summaries: list[dict]) -> list[dict]:
    """Append today's live summary only after market close (4:00 PM ET)."""
    if not is_after_market_close():
        return summaries
    if _live_state and not _has_today(summaries):
        today_summary = _live_state.get_today_summary()
        if today_summary:
            summaries = list(summaries) + [today_summary]
    return summaries


@router.get("/cumulative")
async def get_cumulative():
    """Lifetime cumulative metrics (DB-canonical — see apply_db_cumulative).

    Rebased to settings.baseline_date when set (sums only days >= baseline);
    the resolved baseline is echoed back so the UI can caption "since <date>".
    """
    data = metrics_reader.read_latest()
    overrides = await db_reader.get_cumulative_overrides(settings.baseline_date)
    data = apply_db_cumulative(data, overrides)
    if data is None:
        data = {}
    # Echo the rebase baseline (empty string = full history) for the UI caption.
    data["cumulative_baseline_date"] = settings.baseline_date
    return data


@router.get("/daily")
async def get_daily(
    days: int = Query(default=0, ge=0, le=9999),
    year: int = Query(default=0, ge=0, le=2099),
    strategy_id: str = Query(default=""),
):
    """Daily summaries for calendar heat map (per-strategy)."""
    reader, is_canonical = _reader_for(strategy_id)
    if year >= 2020:
        summaries = await reader.get_daily_summaries_by_year(year)
    elif days > 0:
        summaries = await reader.get_daily_summaries(limit=days)
    else:
        summaries = await reader.get_daily_summaries(limit=365)

    if is_canonical:
        summaries = _append_today_summary(summaries)
    return {"days": len(summaries), "summaries": summaries}


@router.get("/entries")
async def get_all_entries(strategy_id: str = Query(default="")):
    """All historical entries for analytics (per-strategy)."""
    reader, is_canonical = _reader_for(strategy_id)
    entries = await reader.get_all_entries()

    # Append today's entries from state file only after market close (canonical only)
    if is_canonical and _live_state and is_after_market_close():
        today = get_today_et()
        has_today = any(e.get("date") == today for e in entries)
        if not has_today:
            live_entries = _live_state.get_today_entries()
            if live_entries:
                entries = list(entries) + live_entries

    return {"count": len(entries), "entries": entries}


@router.get("/stops")
async def get_all_stops(strategy_id: str = Query(default="")):
    """All historical stops for analytics (per-strategy)."""
    reader, is_canonical = _reader_for(strategy_id)
    stops = await reader.get_all_stops()

    # Append today's stops from state file only after market close (canonical only)
    if is_canonical and _live_state and is_after_market_close():
        today = get_today_et()
        has_today = any(s.get("date") == today for s in stops)
        if not has_today:
            live_stops = _live_state.get_today_stops()
            if live_stops:
                stops = list(stops) + live_stops

    return {"count": len(stops), "stops": stops}


@router.get("/comparisons")
async def get_comparisons(strategy_id: str = Query(default="")):
    """Comparison statistics (averages across all trading days, per-strategy)."""
    reader, is_canonical = _reader_for(strategy_id)
    data = await reader.get_comparison_stats()

    # If we have DB data, augment with today's values only after market close (canonical only)
    if data and is_canonical and _live_state and is_after_market_close():
        today_summary = _live_state.get_today_summary()
        today_entries = _live_state.get_today_entries()
        if today_summary:
            n = data.get("total_days", 0)
            today_pnl = today_summary.get("net_pnl", 0)
            today_entries_count = today_summary.get("entries_placed", 0)
            today_stops = today_summary.get("entries_stopped", 0)
            today_credit = sum(e.get("total_credit", 0) for e in today_entries)

            # Update running averages: new_avg = (old_avg * n + today) / (n + 1)
            if n > 0:
                data = dict(data)  # Make mutable copy
                data["avg_pnl"] = ((data.get("avg_pnl") or 0) * n + today_pnl) / (n + 1)
                data["avg_entries"] = ((data.get("avg_entries") or 0) * n + today_entries_count) / (n + 1)
                data["avg_stops"] = ((data.get("avg_stops") or 0) * n + today_stops) / (n + 1)
                if data.get("avg_credit") is not None:
                    data["avg_credit"] = (data["avg_credit"] * n + today_credit) / (n + 1)
                data["best_day"] = max(data.get("best_day") or 0, today_pnl)
                data["worst_day"] = min(data.get("worst_day") or 0, today_pnl)
                data["total_days"] = n + 1

    if data is None:
        return {"error": "No data available"}
    return data


@router.get("/performance")
async def get_performance(strategy_id: str = Query(default="")):
    """Daily P&L values for client-side performance metric calculations
    (per-strategy).

    Rebased to settings.baseline_date so Sharpe/drawdown match the rebased card.
    """
    reader, is_canonical = _reader_for(strategy_id)
    pnls = await reader.get_daily_pnls(settings.baseline_date)

    # Append today's net P&L only after market close (canonical only)
    if is_canonical and _live_state and is_after_market_close():
        today_pnl = _live_state.get_today_net_pnl()
        if today_pnl is not None:
            # Check if today is already in DB by comparing count
            # (DB returns ordered by date, today would be last)
            summaries = await reader.get_daily_summaries(limit=1)
            today = get_today_et()
            if not summaries or summaries[0].get("date") != today:
                pnls = list(pnls) + [today_pnl]

    return {"count": len(pnls), "daily_pnls": pnls}


@router.get("/range")
async def get_date_range():
    """Available date range in database."""
    info = await db_reader.get_date_range()
    return info or {"first_date": None, "last_date": None, "total_days": 0}
