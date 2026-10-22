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

    2026-07-31: a genuine order-EXECUTION FAILURE also sets both *_side_skipped
    flags (see dashboard/backend/routers/variants.py _entry_disposition's
    docstring for the full incident) — must be checked before the generic
    'skipped' branch, else it renders identically to a routine strategic skip
    on the widget too, the same blind spot this fix closes everywhere else.
    """
    if e.get("is_complete"):
        if e.get("execution_failed"):
            return "failed"
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


# ---------------------------------------------------------------------------
# Fleet view (2026-10-22) — every strategy, grouped by COMPARABILITY
# ---------------------------------------------------------------------------
#
# /api/widget serves the live seat alone, which is how it was written in March.
# That makes the home screen a very narrow window on a 9-strategy fleet, so this
# adds the whole board for the Large widget.
#
# Deliberately NOT a single ranked leaderboard, for reasons that are properties
# of the data rather than taste:
#
#   * Cross-group ranking is the one comparison this system exists to prevent.
#     A credit iron condor, a net-debit calendar and a naked strangle do not
#     share a P&L axis — hence `comparable` on GroupMeta and the group-scoping of
#     every /compare command.
#   * Raw dollars rank by SIZE, not edge. A is 1 contract at 75pt; bl/b/c are 7
#     at 5pt; H trades SPY (~1/70th of bl's notional at 1 contract). On the same
#     55 days A returned -$23.55 against B-A's -$432.10 — size scaled the loss.
#   * Lifetimes span DIFFERENT ERAS. Measured 2026-10-22: bl has 9 days of
#     history against c's 94. `days` is returned per row so the number can be
#     read honestly rather than compared blind.
#   * 8 of the 9 are dry-run. Ranking simulated fills against the one live seat
#     would be a category error.
#
# So: grouped, best-first WITHIN a group, live flagged, no global #1.

_FLEET_TTL_S = 60.0
_fleet_cache: dict = {"at": 0.0, "payload": None}


def _variant_today_pnl(vid: str) -> float:
    """Today's net from the variant's STATE file, not daily_summaries.

    daily_summaries is only written at settlement, so during the session it
    carries yesterday — reading it here would show a stale number all day on
    the one view meant to be glanceable. Mirrors the live-seat calculation in
    get_widget_data above.
    """
    try:
        from dashboard.backend.services.variant_readers import state_file_for
        import json as _json
        with open(state_file_for(vid)) as fh:
            st = _json.load(fh)
        return round(float(st.get("total_realized_pnl", 0) or 0)
                     - float(st.get("total_commission", 0) or 0), 2)
    except Exception:  # noqa: BLE001
        return 0.0


@router.get("/api/widget/fleet")
async def get_widget_fleet():
    """Every strategy's today + lifetime, grouped by comparability group."""
    import time as _time
    now = _time.monotonic()
    if _fleet_cache["payload"] is not None and now - _fleet_cache["at"] < _FLEET_TTL_S:
        return _fleet_cache["payload"]

    from dashboard.backend.config import settings as _settings
    from dashboard.backend.services.variant_readers import (
        db_reader_for_variant, live_seat_id, state_file_for,
    )
    from shared import strategy_taxonomy as tax

    market = get_current_status()
    seat = live_seat_id()
    groups: dict[str, dict] = {}

    for sid, meta in tax.STRATEGIES.items():
        # Skip anything with no state file on this host — that is how `bm`
        # (real-money, built but never installed) excludes itself, instead of a
        # hardcoded name that a future variant would silently fall foul of.
        try:
            if not state_file_for(sid).exists():
                continue
        except Exception:  # noqa: BLE001
            continue

        lifetime, days = None, 0
        try:
            baseline = getattr(_settings, f"variant_{sid}_baseline_date", "") or ""
            reader = db_reader_for_variant(sid)
            ov = await reader.get_cumulative_overrides(baseline)
            lifetime = (ov or {}).get("cumulative_pnl")
            days = len(await reader.get_all_summaries(baseline))
        except Exception:  # noqa: BLE001
            pass  # a variant with no DB yet shows "-", never breaks the board

        g = tax.GROUPS.get(meta.group_id)
        bucket = groups.setdefault(meta.group_id, {
            "id": meta.group_id,
            "label": getattr(g, "label", meta.group_id) if g else meta.group_id,
            "pnl_shape": getattr(g, "pnl_shape", meta.pnl_shape) if g else meta.pnl_shape,
            "comparable": bool(getattr(g, "comparable", False)) if g else False,
            "rows": [],
        })
        bucket["rows"].append({
            "id": sid,
            "name": meta.ui_name or sid.upper(),
            "today": _variant_today_pnl(sid),
            "lifetime": round(lifetime, 2) if isinstance(lifetime, (int, float)) else None,
            "days": days,
            "live": sid == seat,
            "status": str(meta.status),
        })

    # Best-first WITHIN each group. A row with no lifetime sorts last rather
    # than being treated as a zero, which would rank "no data yet" above a
    # genuine loss.
    for b in groups.values():
        b["rows"].sort(key=lambda r: (r["lifetime"] is None,
                                      -(r["lifetime"] or 0.0)))

    ordered = [groups[gid] for gid in tax.GROUPS if gid in groups]
    live_row = next((r for b in ordered for r in b["rows"] if r["live"]), None)

    # The ET trading date, from the project's canonical exchange clock.
    # get_current_status() carries NO date field — using market.get("date")
    # would have shipped "" straight into the widget's staleness check, which
    # treats a falsy date as "cannot tell" and would silently disable the stale
    # warning on the fleet view forever.
    try:
        from shared.market_hours import get_us_market_time
        et_date = get_us_market_time().strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        et_date = ""

    payload = {
        "date": et_date,
        "market_open": market.get("is_open", False),
        "live": live_row,
        "groups": ordered,
    }
    _fleet_cache.update(at=now, payload=payload)
    return payload
