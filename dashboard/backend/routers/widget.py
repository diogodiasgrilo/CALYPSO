"""Widget endpoint for iOS Scriptable and Siri shortcuts."""

from fastapi import APIRouter

from dashboard.backend.config import settings
from dashboard.backend.services.state_reader import StateFileReader
from dashboard.backend.services.metrics_reader import MetricsFileReader
from dashboard.backend.services.market_status import get_current_status
from dashboard.backend.services.variant_readers import (
    live_state_file, live_metrics_file,
)

router = APIRouter(tags=["widget"])

# Readers for the CURRENT live seat, cached per resolved path so the iOS widget
# follows a C<->B swap with no restart.
_state_readers: dict[str, StateFileReader] = {}
_metrics_readers: dict[str, MetricsFileReader] = {}


def _live_state_reader() -> StateFileReader:
    key = str(live_state_file())
    if key not in _state_readers:
        _state_readers[key] = StateFileReader(live_state_file())
    return _state_readers[key]


def _live_metrics_reader() -> MetricsFileReader:
    key = str(live_metrics_file())
    if key not in _metrics_readers:
        _metrics_readers[key] = MetricsFileReader(live_metrics_file())
    return _metrics_readers[key]


def _entry_dot(e: dict) -> str:
    """Classify one entry into a widget status dot.

    A SKIPPED entry (no trade placed) gets its OWN 'skipped' dot rather than
    falling through to 'expired' — 'expired' reads as a kept-credit win, so a
    no-trade day of skips would otherwise look like a day of winners (dashboard
    audit 2026-07-22).
    """
    if e.get("is_complete"):
        if e.get("call_side_stopped") or e.get("put_side_stopped"):
            return "stopped"
        if e.get("call_side_skipped") and e.get("put_side_skipped"):
            return "skipped"
        return "expired"
    if e.get("entry_time"):
        return "active"
    return "pending"


@router.get("/api/widget")
async def get_widget_data():
    """Flat JSON for iOS Scriptable widget and Siri shortcuts.

    Returns a simplified view optimized for small displays.
    """
    state = _live_state_reader().get_cached() or _live_state_reader().read_latest()
    metrics = _live_metrics_reader().get_cached() or _live_metrics_reader().read_latest()
    market = get_current_status()

    if not state:
        return {
            "status": "offline",
            "summary": "HYDRA dashboard cannot read state file.",
        }

    entries = state.get("entries", [])
    completed = state.get("entries_completed", 0)
    total_stops = state.get("call_stops_triggered", 0) + state.get("put_stops_triggered", 0)
    net_pnl = state.get("total_realized_pnl", 0) - state.get("total_commission", 0)
    credit = state.get("total_credit_received", 0)
    bot_state = state.get("state", "Unknown")

    # Build spoken summary for Siri
    pnl_word = "plus" if net_pnl >= 0 else "minus"
    pnl_abs = abs(net_pnl)
    summary = (
        f"HYDRA {bot_state.lower()}. "
        f"{completed} entries, {total_stops} stops. "
        f"Net P and L: {pnl_word} {pnl_abs:.0f} dollars."
    )

    # Entry status dots for medium widget
    entry_dots = [_entry_dot(e) for e in entries]

    # Pad to scheduled entry count (base + conditional, read from state)
    schedule = state.get("entry_schedule", {})
    total_scheduled = len(schedule.get("base", [])) + len(schedule.get("conditional", []))
    pad_to = max(total_scheduled, 3)  # at least 3
    while len(entry_dots) < pad_to:
        entry_dots.append("pending")

    # Lifetime P&L must match the web dashboard's /api/metrics/cumulative card:
    # apply the SAME DB-canonical override + baseline rebase, else the widget
    # shows the raw metrics-file value (which still includes pre-baseline legacy
    # history the rest of the dashboard excludes) — the iOS widget and the web
    # card disagreed by ~$1,966 (dashboard audit 2026-07-22).
    from dashboard.backend.services.db_reader import apply_db_cumulative
    from dashboard.backend.services.variant_readers import canonical_db_reader, live_baseline_date

    cumulative_pnl = 0
    if metrics:
        try:
            overrides = await canonical_db_reader().get_cumulative_overrides(live_baseline_date())
            rebased = apply_db_cumulative(dict(metrics), overrides) or {}
            cumulative_pnl = rebased.get("cumulative_pnl", metrics.get("cumulative_pnl", 0))
        except Exception:
            cumulative_pnl = metrics.get("cumulative_pnl", 0)

    # Phase 2 X-1: expose contract count so iOS widget can show a [Nc] badge
    # next to P&L. Prefer state file's explicit field, fall back to max across
    # per-entry contracts, then 1.
    contracts = (
        state.get("contracts_per_entry")
        or max((e.get("contracts", 1) for e in entries), default=1)
        or 1
    )

    return {
        "status": bot_state.lower(),
        "market_open": market.get("is_open", False),
        "net_pnl": round(net_pnl, 2),
        "gross_pnl": round(state.get("total_realized_pnl", 0), 2),
        "credit": round(credit, 2),
        "commission": round(state.get("total_commission", 0), 2),
        "entries": completed,
        "stops": total_stops,
        "entry_dots": entry_dots,
        "cumulative_pnl": round(cumulative_pnl, 2),
        "date": state.get("date", ""),
        "summary": summary,
        "contracts_per_entry": contracts,
    }
