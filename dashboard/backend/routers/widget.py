"""Widget endpoint for iOS Scriptable and Siri shortcuts."""

from fastapi import APIRouter

from dashboard.backend.config import settings
from dashboard.backend.services.state_reader import StateFileReader
from dashboard.backend.services.metrics_reader import MetricsFileReader
from dashboard.backend.services.market_status import get_current_status

router = APIRouter(tags=["widget"])

state_reader = StateFileReader(settings.hydra_state_file)
metrics_reader = MetricsFileReader(settings.hydra_metrics_file)


@router.get("/api/widget")
async def get_widget_data():
    """Flat JSON for iOS Scriptable widget and Siri shortcuts.

    Returns a simplified view optimized for small displays.
    """
    state = state_reader.get_cached() or state_reader.read_latest()
    metrics = metrics_reader.get_cached() or metrics_reader.read_latest()
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
    entry_dots = []
    for e in entries:
        if e.get("is_complete"):
            if e.get("call_side_stopped") or e.get("put_side_stopped"):
                entry_dots.append("stopped")
            else:
                entry_dots.append("expired")
        elif e.get("entry_time"):
            entry_dots.append("active")
        else:
            entry_dots.append("pending")

    # Pad to 4 entries (3 base + E6)
    while len(entry_dots) < 4:
        entry_dots.append("pending")

    cumulative_pnl = metrics.get("cumulative_pnl", 0) if metrics else 0

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
    }
