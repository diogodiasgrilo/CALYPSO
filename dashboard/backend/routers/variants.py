"""N-way head-to-head variant comparison endpoints.

Variant A is the live HYDRA bot (current spread width, current config).
Variants B, C, ... are parallel HYDRA processes running in dry mode with
different configs (typically a different spread width), each writing to
data/variant_<id>/* and logs/hydra_variant_<id>/.

The variant set is built at import time from ``settings`` — to add a new
variant you only need to (1) add 5 ``variant_<id>_*`` fields to
``dashboard/backend/config.py`` and (2) install a matching systemd service
on the VM. This router auto-discovers it.

All endpoints return 503 when ``settings.comparison_mode_enabled`` is False so
the comparison UI can hide cleanly when the experiment isn't running. A
variant's state file is allowed to be missing (its bot may not be running yet)
— the dashboard surfaces that as ``available: false`` rather than a 500.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from dashboard.backend.config import settings
from dashboard.backend.services.state_reader import StateFileReader
from dashboard.backend.services.metrics_reader import MetricsFileReader
from dashboard.backend.services.db_reader import BacktestingDBReader
from dashboard.backend.services.market_status import get_today_et

logger = logging.getLogger("dashboard.variants")

router = APIRouter(prefix="/api/variants", tags=["variants"])


# ----------------------------------------------------------------------------
# Variant registry
# ----------------------------------------------------------------------------
# Each entry maps a lowercase variant id ("a", "b", "c", ...) to the four
# file paths and label that define it. Built once at import — adding a new
# variant means adding a new ``variant_<id>_*`` group to settings + appending
# its id to ``_VARIANT_IDS`` below.
#
# Variant A is special-cased: it points at the canonical hydra_* paths
# (so the live bot's data IS variant A's data without any duplication).
# All other variants point at their parallel ``data/variant_<id>/*`` tree.

_VARIANT_IDS: list[str] = ["a", "b", "c"]


def _variant_paths(vid: str) -> dict:
    """Resolve the 5 paths + label for a given variant id from settings.

    Returns ``None`` for any field whose corresponding settings attribute
    isn't defined — that lets us list a variant id even if its settings
    haven't been added yet (defensive against typos in _VARIANT_IDS).
    """
    if vid == "a":
        return {
            "label": settings.variant_a_label,
            "state_file": settings.hydra_state_file,
            "metrics_file": settings.hydra_metrics_file,
            "backtesting_db": settings.backtesting_db,
            "log_file": settings.hydra_log_file,
            "config_file": settings.calypso_root / "bots/hydra/config/config.json",
        }
    return {
        "label": getattr(settings, f"variant_{vid}_label", f"Variant {vid.upper()}"),
        "state_file": getattr(settings, f"variant_{vid}_state_file", None),
        "metrics_file": getattr(settings, f"variant_{vid}_metrics_file", None),
        "backtesting_db": getattr(settings, f"variant_{vid}_backtesting_db", None),
        "log_file": getattr(settings, f"variant_{vid}_log_file", None),
        "config_file": getattr(settings, f"variant_{vid}_config_file", None),
    }


_VARIANTS: dict[str, dict] = {vid: _variant_paths(vid) for vid in _VARIANT_IDS}

# Reader pools — one per variant. Built lazily so a missing settings field
# doesn't crash module import, just makes that variant unavailable.
_state_readers: dict[str, StateFileReader] = {
    vid: StateFileReader(p["state_file"])
    for vid, p in _VARIANTS.items()
    if p.get("state_file") is not None
}
_metrics_readers: dict[str, MetricsFileReader] = {
    vid: MetricsFileReader(p["metrics_file"])
    for vid, p in _VARIANTS.items()
    if p.get("metrics_file") is not None
}
_db_readers: dict[str, BacktestingDBReader] = {
    vid: BacktestingDBReader(p["backtesting_db"])
    for vid, p in _VARIANTS.items()
    if p.get("backtesting_db") is not None
}


# Visualization accent colors per variant — lifted from the frontend palette
# so backend-side aggregations could carry them through if ever needed. The
# frontend currently picks its own accents but we keep the mapping centralized.
_VARIANT_ACCENT = {"a": "info", "b": "warning", "c": "profit"}


def _check_enabled() -> None:
    """Raise 503 if comparison mode is off — UI uses this as the on/off gate."""
    if not settings.comparison_mode_enabled:
        raise HTTPException(
            status_code=503,
            detail="Comparison mode disabled — set DASHBOARD_COMPARISON_MODE_ENABLED=true",
        )


def _validate_variant(vid: str) -> str:
    """Lowercase + check it's a known variant. Raise 404 with helpful message."""
    v = vid.lower()
    if v not in _state_readers:
        known = ", ".join(sorted(_state_readers.keys())).upper()
        raise HTTPException(404, f"Unknown variant '{vid}' (known: {known})")
    return v


def _file_age_seconds(path: Optional[Path]) -> Optional[float]:
    """Seconds since the file was last modified, or None if it doesn't exist."""
    try:
        if path is None or not path.exists():
            return None
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def _read_variant_config(path: Optional[Path]) -> dict:
    """Read a variant's config.json so the UI can show what's actually different.

    Exposes the Brandon Trojan Horse stack (v1.27, 2026-05-04) per-feature so
    the Comparison page's ConfigDelta table can show variant A (no Brandon)
    vs B/C (Brandon stack live), and within B vs C the narrow_spread
    differentiator. The legacy directional_pivot fields are still exposed for
    historical snapshots; in v1.27 pivot is disabled across all variants.
    """
    if path is None:
        return {}
    try:
        with open(path) as f:
            cfg = json.load(f)
        s = cfg.get("strategy", {})
        pivot = s.get("directional_pivot") or {}
        brandon = s.get("brandon") or {}
        b_tp = brandon.get("take_profit") or {}
        b_gex = brandon.get("gex") or {}
        b_overlay = brandon.get("defensive_overlay") or {}
        b_narrow = brandon.get("narrow_spread") or {}
        b_shadow = brandon.get("hydra_stop_shadow") or {}
        return {
            "max_spread_width": s.get("max_spread_width"),
            "contracts_per_entry": s.get("contracts_per_entry"),
            "entry_times": s.get("entry_times"),
            "call_starting_otm_multiplier": s.get("call_starting_otm_multiplier"),
            "put_starting_otm_multiplier": s.get("put_starting_otm_multiplier"),
            "call_stop_buffer": s.get("call_stop_buffer"),
            "put_stop_buffer": s.get("put_stop_buffer"),
            "dry_run": cfg.get("dry_run"),
            # Brandon Trojan Horse stack (v1.27.x, 2026-05-04). Primary
            # differentiator between A (no Brandon) and B/C (full stack live).
            "brandon_enabled": bool(brandon.get("enabled", False)),
            "brandon_tp_enabled": bool(b_tp.get("enabled", False)),
            "brandon_tp_threshold": b_tp.get("threshold") if b_tp.get("enabled") else None,
            "brandon_gex_strike_adjuster_enabled": bool(b_gex.get("strike_adjuster_enabled", False)),
            "brandon_gex_breach_exit_enabled": bool(b_gex.get("breach_exit_enabled", False)),
            "brandon_overlay_enabled": bool(b_overlay.get("enabled", False)),
            "brandon_narrow_spread_enabled": bool(b_narrow.get("enabled", False)),
            "brandon_hydra_stop_shadow_enabled": bool(b_shadow.get("enabled", False)),
            # Directional pivot — preserved for historical snapshots; disabled in v1.27.
            "directional_pivot_enabled": bool(pivot.get("enabled", False)),
            "directional_pivot_close_mode": pivot.get("close_mode") if pivot.get("enabled") else None,
            "directional_pivot_threshold_pct": pivot.get("threshold_pct") if pivot.get("enabled") else None,
            "directional_pivot_defer_minutes": pivot.get("pre_entry_defer_minutes") if pivot.get("enabled") else None,
        }
    except Exception as e:
        logger.warning(f"Could not read variant config {path}: {e}")
        return {}


def _side_active(entry: dict, side: str) -> bool:
    """True when an entry's side is still live (not stopped/expired/skipped).

    Note: do NOT use entry.is_complete to gate this. is_complete is set
    immediately after entry PLACEMENT (meic/strategy.py:1808) so it's True
    for monitoring entries — it only becomes meaningful at settlement when
    _process_expired_credits sets it again based on per-side flags. The
    side-status flags are the only reliable "is this still live?" signal
    during the trading day.
    """
    return (
        not entry.get(f"{side}_side_stopped")
        and not entry.get(f"{side}_side_expired")
        and not entry.get(f"{side}_side_skipped")
    )


def _compute_unrealized_pnl(entries: list[dict]) -> float:
    """Sum unrealized P&L across all entries' active sides.

    Per side: unrealized = credit_received − cost_to_close (spread_value).
    Done sides (stopped/expired/skipped) contribute 0 since their P&L is
    already in total_realized_pnl. Sides without populated spread_value
    (heartbeat hasn't run yet) contribute 0 — return what we know.
    """
    total = 0.0
    for e in entries or []:
        if _side_active(e, "call"):
            credit = e.get("call_spread_credit") or 0
            value = e.get("call_spread_value")
            if credit > 0 and value is not None:
                total += credit - value
        if _side_active(e, "put"):
            credit = e.get("put_spread_credit") or 0
            value = e.get("put_spread_value")
            if credit > 0 and value is not None:
                total += credit - value
    return total


def _summary_from_state(state: dict) -> dict:
    """Mirror dashboard.routers.hydra._summary, but takes a state dict directly
    so we can build a summary for any variant without code duplication.

    Net P&L is LIVE (realized + unrealized − commission) so the leaderboard
    and Day Summary cells track the same race the P&L chart is plotting.
    Realized-only is exposed separately as `realized_pnl` for analysts who
    want the locked-in figure.
    """
    if not state:
        return {}
    entries = state.get("entries", [])
    total_credit = state.get("total_credit_received", 0) or 0
    realized = state.get("total_realized_pnl", 0) or 0
    commission = state.get("total_commission", 0) or 0
    unrealized = _compute_unrealized_pnl(entries)
    call_stops = state.get("call_stops_triggered", 0) or 0
    put_stops = state.get("put_stops_triggered", 0) or 0
    contracts = (
        state.get("contracts_per_entry")
        or max((e.get("contracts", 1) for e in entries), default=1)
        or 1
    )
    # An entry is "active" if either side is still live. is_complete-only
    # gating mis-counted entries during monitoring (it goes True at placement).
    active_count = sum(
        1 for e in entries if _side_active(e, "call") or _side_active(e, "put")
    )
    return {
        "date": state.get("date"),
        "state": state.get("state"),
        "entries_completed": state.get("entries_completed", 0),
        "entries_failed": state.get("entries_failed", 0),
        "entries_skipped": state.get("entries_skipped", 0),
        "total_credit_received": total_credit,
        "total_realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_commission": commission,
        "net_pnl": realized + unrealized - commission,  # LIVE: matches chart
        "call_stops": call_stops,
        "put_stops": put_stops,
        "total_stops": call_stops + put_stops,
        "active_entries": active_count,
        "total_entries": len(entries),
        "contracts_per_entry": contracts,
    }


def _compute_buffer_utilization(entry: dict) -> dict:
    """For a single entry, return per-side buffer utilization based on the
    most recent cost-to-close vs the trigger level.

    Cost-to-close for a side is the ``call_spread_value`` / ``put_spread_value``
    fields, written by the bot during heartbeat. ``call_side_stop`` /
    ``put_side_stop`` is the trigger threshold. Utilization = cost / stop.

    Per-side gating uses the actual side-status flags (stopped/expired/skipped),
    NOT entry.is_complete — the latter goes True immediately after placement
    (meic/strategy.py:1808) and would suppress the bar for monitoring entries.
    A done side returns None so the UI renders a placeholder instead of a
    misleading 0%.
    """
    out = {"call_pct": None, "put_pct": None, "call_value": None, "put_value": None}

    if _side_active(entry, "call"):
        csv = entry.get("call_spread_value")
        if csv is not None:
            out["call_value"] = csv
            css = entry.get("call_side_stop")
            if css and css > 0:
                out["call_pct"] = round(min(100.0, max(0.0, csv / css * 100)), 1)

    if _side_active(entry, "put"):
        psv = entry.get("put_spread_value")
        if psv is not None:
            out["put_value"] = psv
            pss = entry.get("put_side_stop")
            if pss and pss > 0:
                out["put_pct"] = round(min(100.0, max(0.0, psv / pss * 100)), 1)

    return out


def _enrich_entries(entries: list[dict]) -> list[dict]:
    """Add buffer-utilization fields to each entry for the comparison panel."""
    out = []
    for e in entries:
        copy = dict(e)
        copy["buffer"] = _compute_buffer_utilization(e)
        out.append(copy)
    return out


def _query_peak_spread_values(db_path, today: str) -> dict:
    """Read the historical peak cost-to-close per entry per side from the
    `spread_snapshots` table — the bot writes one row every ~10s during
    monitoring with current call/put `*_spread_value` numbers. The MAX
    across the day is the true peak used for the buffer-stress display.

    Returns {entry_number: (max_call_spread_value, max_put_spread_value)}
    or {} on any error / missing DB. Read-only connection with a 2s
    timeout so a slow disk doesn't block the dashboard.
    """
    import sqlite3
    try:
        if db_path is None or not db_path.exists():
            return {}
        # Read-only URI so we can't accidentally write through this path.
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=2
        )
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT entry_number,
                      MAX(call_spread_value) AS max_call,
                      MAX(put_spread_value) AS max_put
               FROM spread_snapshots
               WHERE substr(timestamp, 1, 10) = ?
               GROUP BY entry_number""",
            (today,),
        ).fetchall()
        conn.close()
        return {
            r["entry_number"]: (r["max_call"], r["max_put"]) for r in rows
        }
    except Exception as e:
        logger.debug(f"Could not query spread_snapshots peaks: {e}")
        return {}


def _peak_buffer_pct(entries: list[dict], db_path=None) -> dict:
    """Largest call/put buffer utilization across today's entries.

    Per side, takes the MAX of three signals (whichever is highest):
      1. **100%** if the side is `*_side_stopped` — by definition the
         cost-to-close reached the trigger level when the stop fired,
         so peak buffer use was at least 100%.
      2. **Historical peak from spread_snapshots** — the bot writes
         a snapshot every ~10s; MAX(call_spread_value)/stop_level is
         the true peak observed today even if it has since recovered.
      3. **Current cost-to-close** — `call_spread_value` from the
         state file, divided by stop level, as a live floor.

    Without (1) and (2) the previous version reported 0% for stopped
    sides (state file zeroes out spread_value after stop), missing
    the most stressful moments of the day.
    """
    today = get_today_et()
    peak_snapshots = _query_peak_spread_values(db_path, today) if db_path else {}

    peak_call = 0.0
    peak_put = 0.0

    for e in entries or []:
        n = e.get("entry_number")
        snap_call, snap_put = peak_snapshots.get(n, (None, None))

        # ---------- CALL SIDE ----------
        if e.get("call_side_stopped"):
            peak_call = max(peak_call, 100.0)
        css = e.get("call_side_stop")
        if css and css > 0:
            # historical peak from snapshots
            if snap_call is not None:
                pct = min(120.0, max(0.0, snap_call / css * 100))
                peak_call = max(peak_call, pct)
            # live floor (only if side is still active and value is meaningful)
            if _side_active(e, "call"):
                csv = e.get("call_spread_value")
                if csv is not None and csv > 0:
                    pct = min(120.0, csv / css * 100)
                    peak_call = max(peak_call, pct)

        # ---------- PUT SIDE ----------
        if e.get("put_side_stopped"):
            peak_put = max(peak_put, 100.0)
        pss = e.get("put_side_stop")
        if pss and pss > 0:
            if snap_put is not None:
                pct = min(120.0, max(0.0, snap_put / pss * 100))
                peak_put = max(peak_put, pct)
            if _side_active(e, "put"):
                psv = e.get("put_spread_value")
                if psv is not None and psv > 0:
                    pct = min(120.0, psv / pss * 100)
                    peak_put = max(peak_put, pct)

    return {"call_pct": round(peak_call, 1), "put_pct": round(peak_put, 1)}


def _variant_payload(vid: str) -> dict:
    """Full per-variant payload used by both /{id}/state and /comparison.

    Always returns a dict; if the variant isn't running yet, the dict has
    ``available: false`` and otherwise-empty fields. The frontend renders
    a placeholder card in that case rather than erroring out.
    """
    paths = _VARIANTS[vid]
    state_file = paths["state_file"]
    state_age = _file_age_seconds(state_file)

    if state_age is None:
        return {
            "id": vid.upper(),
            "label": paths["label"],
            "available": False,
            "reason": f"State file missing ({state_file})",
            "config": _read_variant_config(paths["config_file"]),
        }

    state = _state_readers[vid].read_latest() or {}
    entries = state.get("entries", [])

    return {
        "id": vid.upper(),
        "label": paths["label"],
        "available": True,
        "state_file_age_seconds": round(state_age, 1),
        "config": _read_variant_config(paths["config_file"]),
        "summary": _summary_from_state(state),
        "entries": _enrich_entries(entries),
        "pnl_history": state.get("pnl_history", []),
        "peak_buffer": _peak_buffer_pct(entries, db_path=paths["backtesting_db"]),
        "spx_open": (state.get("market_data_ohlc") or {}).get("spx_open"),
        "vix_open": (state.get("market_data_ohlc") or {}).get("vix_open"),
        "spx_high": (state.get("market_data_ohlc") or {}).get("spx_high"),
        "spx_low": (state.get("market_data_ohlc") or {}).get("spx_low"),
    }


def _all_variant_ids_upper() -> list[str]:
    """The known variant ids in uppercase, in canonical order (A, B, C, ...)."""
    return [vid.upper() for vid in _VARIANT_IDS if vid in _state_readers]


def _all_variant_labels() -> dict[str, str]:
    """Map of "A" -> label, "B" -> label, ..."""
    return {vid.upper(): _VARIANTS[vid]["label"] for vid in _VARIANT_IDS if vid in _state_readers}


@router.get("/health")
async def get_health():
    """Always returns 200 (even when disabled) so the frontend can branch
    cleanly on whether comparison mode is available without an error path.

    The frontend hides the /comparison nav entry + page when ``enabled``
    is False — same gating as the backend endpoints, single source of truth.

    The ``variants`` array drives N-variant rendering on the frontend; adding
    a new variant id automatically surfaces it without UI code changes.
    """
    return {
        "enabled": settings.comparison_mode_enabled,
        "variants": _all_variant_ids_upper(),
        "labels": _all_variant_labels(),
    }


@router.get("/list")
async def list_variants():
    """Lightweight list of active variants — used by nav/breadcrumbs.

    ``available`` reflects whether the variant's bot has produced a state
    file yet, so the frontend can mark not-yet-started variants distinctly.
    """
    _check_enabled()
    return {
        "variants": [
            {
                "id": vid.upper(),
                "label": _VARIANTS[vid]["label"],
                "available": _file_age_seconds(_VARIANTS[vid]["state_file"]) is not None,
            }
            for vid in _VARIANT_IDS
            if vid in _state_readers
        ]
    }


@router.get("/{variant_id}/state")
async def get_variant_state(variant_id: str):
    """Full state of one variant (state file + enriched entries)."""
    _check_enabled()
    vid = _validate_variant(variant_id)
    return _variant_payload(vid)


@router.get("/{variant_id}/summary")
async def get_variant_summary(variant_id: str):
    """Just the today-summary block for one variant (low-bandwidth poll)."""
    _check_enabled()
    vid = _validate_variant(variant_id)
    state = _state_readers[vid].read_latest() or {}
    db_path = _VARIANTS[vid]["backtesting_db"]
    return {
        "id": variant_id.upper(),
        "summary": _summary_from_state(state),
        "peak_buffer": _peak_buffer_pct(state.get("entries", []), db_path=db_path),
    }


@router.get("/comparison")
async def get_comparison():
    """All variants + leaderboard delta computed server-side.

    Frontend polls this every ~2s. Returns enough data to render the entire
    Comparison page without further round-trips: leaderboard, strikes table,
    buffer bars, P&L line chart series.

    The leaderboard's ``winner`` field is the variant id with the highest
    NET P&L (realized + unrealized − commission) among AVAILABLE variants.
    Tie returns ``"tie"``. ``deltas`` exposes per-variant deltas vs the
    canonical variant A so multi-way leaderboards can show "B is +$50 vs A,
    C is −$120 vs A" without re-deriving on the client.

    Backwards compat: ``a_net_pnl`` / ``b_net_pnl`` / ``delta_net_pnl`` are
    kept so older frontend builds don't 500 mid-deploy. New frontend code
    should read ``leaderboard.scores`` (a dict of id→net_pnl) and
    ``leaderboard.deltas_vs_a`` instead.
    """
    _check_enabled()

    payloads = {vid.upper(): _variant_payload(vid) for vid in _VARIANT_IDS if vid in _state_readers}

    # Score table: only count available variants in the winner determination.
    scores: dict[str, float] = {}
    for vid_upper, p in payloads.items():
        if p.get("available"):
            scores[vid_upper] = (p.get("summary") or {}).get("net_pnl", 0) or 0

    if not scores:
        winner = "n/a"
    else:
        best = max(scores.values())
        leaders = [vid for vid, s in scores.items() if abs(s - best) < 0.01]
        winner = leaders[0] if len(leaders) == 1 else "tie"

    a_score = scores.get("A", 0)
    deltas_vs_a = {vid: round(score - a_score, 2) for vid, score in scores.items() if vid != "A"}

    return {
        "date": get_today_et(),
        "leaderboard": {
            "winner": winner,
            "scores": scores,           # {id: net_pnl} — only available variants
            "deltas_vs_a": deltas_vs_a,  # signed: + = beats A, − = behind A
            # Legacy fields (kept for in-flight frontend builds):
            "a_net_pnl": scores.get("A", 0),
            "b_net_pnl": scores.get("B", 0),
            "delta_net_pnl": scores.get("A", 0) - scores.get("B", 0),
        },
        "variants": payloads,
    }


@router.get("/{variant_id}/daily")
async def get_variant_daily(variant_id: str, days: int = 30):
    """Historical daily summaries for one variant (calendar/long-term view).

    Non-A variants' DBs only have data from when their experiment started, so
    the list will be short until they've run for a few days.
    """
    _check_enabled()
    vid = _validate_variant(variant_id)
    summaries = await _db_readers[vid].get_daily_summaries(limit=days)
    return {"variant": variant_id.upper(), "days": len(summaries), "summaries": summaries}


def _per_variant_lifetime_stats(metrics: Optional[dict]) -> dict:
    """Distill the metrics file into a flat dict for the aggregate endpoint.
    Tolerates missing keys (a variant's metrics file may not exist yet).
    """
    if not metrics:
        return {
            "cumulative_pnl": 0.0,
            "winning_days": 0,
            "losing_days": 0,
            "total_credit_collected": 0.0,
            "total_stops": 0,
            "total_entries": 0,
            "daily_returns_count": 0,
        }
    return {
        "cumulative_pnl": metrics.get("cumulative_pnl", 0.0),
        "winning_days": metrics.get("winning_days", 0),
        "losing_days": metrics.get("losing_days", 0),
        "total_credit_collected": metrics.get("total_credit_collected", 0.0),
        "total_stops": metrics.get("total_stops", 0),
        "total_entries": metrics.get("total_entries", 0),
        "daily_returns_count": len(metrics.get("daily_returns", []) or []),
    }


def _compute_advanced_stats(daily_pnls: list[float]) -> dict:
    """Sharpe-like ratio + max drawdown + best/worst from a P&L array.

    Sharpe is daily-return-mean / daily-return-stddev (no risk-free rate
    subtraction — daily 0DTE strategy on minutes-of-decay isn't comparable
    to T-bills). Returns 0.0 for any stat that needs more data than we have.
    """
    n = len(daily_pnls)
    if n == 0:
        return {"sharpe": 0.0, "max_drawdown": 0.0, "best_day": 0.0, "worst_day": 0.0}

    mean = sum(daily_pnls) / n
    if n < 2:
        sharpe = 0.0
    else:
        var = sum((x - mean) ** 2 for x in daily_pnls) / (n - 1)
        std = var ** 0.5
        sharpe = (mean / std) if std > 0 else 0.0

    # Max drawdown: largest peak-to-trough drop on the running cumulative curve
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in daily_pnls:
        running += p
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    return {
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(max_dd, 2),
        "best_day": round(max(daily_pnls), 2),
        "worst_day": round(min(daily_pnls), 2),
    }


def _cumulative_series(summaries: list[dict]) -> list[dict]:
    """Build a running-cumulative series from a list of daily summaries.
    Each output point: {date, net_pnl, cumulative}.
    """
    running = 0.0
    out = []
    for s in summaries:
        net = s.get("net_pnl") or 0.0
        running += net
        out.append({"date": s["date"], "net_pnl": net, "cumulative": round(running, 2)})
    return out


@router.get("/aggregate")
async def get_aggregate():
    """Cross-variant lifetime + per-day aggregate for the cross-day view.

    Returns:
      - per-variant lifetime stats (cumulative_pnl, win_rate, sharpe, drawdown)
      - per-day aligned series: each row has {date, <id>_net_pnl for each
        variant, winner, cumulative_<id> for each variant}, suitable for
        direct Recharts ingestion
      - head-to-head counters: days_<id>_won counts how many days each
        variant beat ALL others on (only over the common-date intersection
        — days where any variant didn't run don't contribute to H2H tally)
      - per-variant cumulative running totals (separate arrays so each
        variant's curve plots independently of the H2H window)

    Why align by date, not index: variant A's history goes back to Feb 10,
    other variants' histories start the day they launched. Index alignment
    would silently compare A's Feb 10 to B's first-day, which is wrong.
    Date intersection means small N early but correct semantics.

    Backwards compat: legacy ``A``/``B`` keys are kept under ``variants`` and
    legacy ``a_net_pnl``/``b_net_pnl``/``cumulative_a``/``cumulative_b`` are
    kept on each per_day row so older frontend builds don't 500 mid-deploy.
    """
    _check_enabled()

    available_ids_lower = [vid for vid in _VARIANT_IDS if vid in _state_readers]
    available_ids_upper = [vid.upper() for vid in available_ids_lower]

    # ---- Lifetime metrics + per-variant DB summaries ----
    lifetimes: dict[str, dict] = {}
    cumulative_curves: dict[str, list] = {}
    summaries_by_variant: dict[str, list[dict]] = {}

    for vid in available_ids_lower:
        vid_upper = vid.upper()
        metrics = _metrics_readers[vid].read_latest()
        lifetimes[vid_upper] = _per_variant_lifetime_stats(metrics)
        summaries = await _db_readers[vid].get_all_summaries()
        summaries_by_variant[vid_upper] = summaries
        cumulative_curves[vid_upper] = _cumulative_series(summaries)

    # ---- Date-keyed lookups for alignment ----
    by_date_per_variant: dict[str, dict[str, dict]] = {
        vid_upper: {s["date"]: s for s in summaries if s.get("date")}
        for vid_upper, summaries in summaries_by_variant.items()
    }

    # ---- H2H: only dates where ALL available variants have data ----
    if by_date_per_variant:
        common_dates_set = set.intersection(
            *(set(d.keys()) for d in by_date_per_variant.values())
        )
    else:
        common_dates_set = set()
    common_dates = sorted(common_dates_set)

    # Per-day rows + per-variant H2H win counters + per-variant H2H cumulative
    per_day: list[dict] = []
    days_won = {vid_upper: 0 for vid_upper in available_ids_upper}
    days_tied = 0
    cum_h2h: dict[str, float] = {vid_upper: 0.0 for vid_upper in available_ids_upper}

    for d in common_dates:
        row: dict = {"date": d}
        pnls: dict[str, float] = {}
        for vid_upper in available_ids_upper:
            pnl = by_date_per_variant[vid_upper][d].get("net_pnl") or 0.0
            pnls[vid_upper] = pnl
            cum_h2h[vid_upper] += pnl
            row[f"{vid_upper.lower()}_net_pnl"] = round(pnl, 2)
            row[f"cumulative_{vid_upper.lower()}"] = round(cum_h2h[vid_upper], 2)

        # Winner determination — tightest tolerance for "tied"
        best = max(pnls.values())
        leaders = [vid for vid, p in pnls.items() if abs(p - best) < 0.01]
        if len(leaders) == 1:
            winner = leaders[0]
            days_won[winner] += 1
        else:
            winner = "tie"
            days_tied += 1
        row["winner"] = winner
        # Delta vs A (legacy + still useful for the bar chart's signed-bar logic)
        a_pnl = pnls.get("A", 0)
        row["delta"] = round(pnls.get("B", 0) - a_pnl, 2)  # legacy A−B
        row["a_net_pnl"] = round(a_pnl, 2)
        row["b_net_pnl"] = round(pnls.get("B", 0), 2)
        row["cumulative_a"] = round(cum_h2h.get("A", 0), 2)
        row["cumulative_b"] = round(cum_h2h.get("B", 0), 2)
        per_day.append(row)

    # ---- Advanced stats per variant ----
    variants_payload: dict[str, dict] = {}
    for vid_upper in available_ids_upper:
        summaries = summaries_by_variant[vid_upper]
        pnls = [s.get("net_pnl") or 0.0 for s in summaries]
        advanced = _compute_advanced_stats(pnls)
        win_total = lifetimes[vid_upper]["winning_days"] + lifetimes[vid_upper]["losing_days"]
        win_rate = (lifetimes[vid_upper]["winning_days"] / win_total) if win_total > 0 else 0.0

        variants_payload[vid_upper] = {
            "label": _VARIANTS[vid_upper.lower()]["label"],
            "lifetime": {**lifetimes[vid_upper], "win_rate": round(win_rate, 4), **advanced},
            "cumulative_curve": cumulative_curves[vid_upper],
            "total_days": len(summaries),
        }

    # ---- H2H summary block (N-way) ----
    head_to_head: dict = {
        "common_days": len(common_dates),
        "days_tied": days_tied,
        "per_day": per_day,
        # New N-way fields
        "days_won_per_variant": days_won,
        "cumulative_per_variant": {vid: round(cum, 2) for vid, cum in cum_h2h.items()},
        # Legacy 2-way fields (so older frontend builds keep working)
        "days_a_won": days_won.get("A", 0),
        "days_b_won": days_won.get("B", 0),
        "cumulative_delta_a_minus_b": round(cum_h2h.get("A", 0) - cum_h2h.get("B", 0), 2),
    }

    return {
        "variants": variants_payload,
        "head_to_head": head_to_head,
    }
