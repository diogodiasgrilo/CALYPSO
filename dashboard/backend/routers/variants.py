"""1v1 head-to-head variant comparison endpoints.

Variant A = the live HYDRA bot (current spread width, current config).
Variant B = a parallel HYDRA process running in dry mode with a different
            spread width, writing to data/variant_b/* and logs/hydra_variant_b/.

All endpoints return 503 when ``settings.comparison_mode_enabled`` is False so
the comparison UI can hide cleanly when the experiment isn't running. Variant
B's state file is allowed to be missing (variant might not be running yet) —
the dashboard surfaces that as ``available: false`` rather than a 500.
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

# Variant readers — one set per variant, reusing the same primitives as the
# main HYDRA endpoints. Variant A points at the live data files; variant B at
# the parallel data/variant_b/* tree.
_state_readers = {
    "a": StateFileReader(settings.hydra_state_file),
    "b": StateFileReader(settings.variant_b_state_file),
}
_metrics_readers = {
    "a": MetricsFileReader(settings.hydra_metrics_file),
    "b": MetricsFileReader(settings.variant_b_metrics_file),
}
_db_readers = {
    "a": BacktestingDBReader(settings.backtesting_db),
    "b": BacktestingDBReader(settings.variant_b_backtesting_db),
}


def _check_enabled() -> None:
    """Raise 503 if comparison mode is off — UI uses this as the on/off gate."""
    if not settings.comparison_mode_enabled:
        raise HTTPException(
            status_code=503,
            detail="Comparison mode disabled — set DASHBOARD_COMPARISON_MODE_ENABLED=true",
        )


def _file_age_seconds(path: Path) -> Optional[float]:
    """Seconds since the file was last modified, or None if it doesn't exist."""
    try:
        if not path.exists():
            return None
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def _read_variant_config(path: Path) -> dict:
    """Read a variant's config.json so the UI can show what's actually different."""
    try:
        with open(path) as f:
            cfg = json.load(f)
        s = cfg.get("strategy", {})
        return {
            "max_spread_width": s.get("max_spread_width"),
            "call_starting_otm_multiplier": s.get("call_starting_otm_multiplier"),
            "put_starting_otm_multiplier": s.get("put_starting_otm_multiplier"),
            "call_stop_buffer": s.get("call_stop_buffer"),
            "put_stop_buffer": s.get("put_stop_buffer"),
            "dry_run": cfg.get("dry_run"),
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
    so we can build a summary for either variant without code duplication.

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
        if not db_path.exists():
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
    the most stressful moments of the day. Today's call sides hit
    100% at 13:30 and 13:59 but the dashboard showed 0%.
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


def _variant_payload(variant_id: str) -> dict:
    """Full per-variant payload used by both /{id}/state and /comparison.

    Always returns a dict; if the variant isn't running yet, the dict has
    ``available: false`` and otherwise-empty fields. The frontend renders
    a placeholder card in that case rather than erroring out.
    """
    label = settings.variant_a_label if variant_id == "a" else settings.variant_b_label
    config_path = (
        settings.calypso_root / "bots/hydra/config/config.json"
        if variant_id == "a"
        else settings.variant_b_config_file
    )
    state_file = (
        settings.hydra_state_file if variant_id == "a" else settings.variant_b_state_file
    )

    state_age = _file_age_seconds(state_file)
    if state_age is None:
        return {
            "id": variant_id.upper(),
            "label": label,
            "available": False,
            "reason": f"State file missing ({state_file})",
            "config": _read_variant_config(config_path),
        }

    state = _state_readers[variant_id].read_latest() or {}
    entries = state.get("entries", [])

    # Variant DB path so _peak_buffer_pct can read spread_snapshots history
    # (current cost-to-close in state is just a live snapshot — peak today
    # may have been higher earlier, especially before stops fired).
    db_path = (
        settings.backtesting_db
        if variant_id == "a"
        else settings.variant_b_backtesting_db
    )

    return {
        "id": variant_id.upper(),
        "label": label,
        "available": True,
        "state_file_age_seconds": round(state_age, 1),
        "config": _read_variant_config(config_path),
        "summary": _summary_from_state(state),
        "entries": _enrich_entries(entries),
        "pnl_history": state.get("pnl_history", []),
        "peak_buffer": _peak_buffer_pct(entries, db_path=db_path),
        "spx_open": (state.get("market_data_ohlc") or {}).get("spx_open"),
        "vix_open": (state.get("market_data_ohlc") or {}).get("vix_open"),
        "spx_high": (state.get("market_data_ohlc") or {}).get("spx_high"),
        "spx_low": (state.get("market_data_ohlc") or {}).get("spx_low"),
    }


@router.get("/health")
async def get_health():
    """Always returns 200 (even when disabled) so the frontend can branch
    cleanly on whether comparison mode is available without an error path.

    The frontend hides the /comparison nav entry + page when ``enabled``
    is False — same gating as the backend endpoints, single source of truth.
    """
    return {
        "enabled": settings.comparison_mode_enabled,
        "variants": ["A", "B"],
        "variant_a_label": settings.variant_a_label,
        "variant_b_label": settings.variant_b_label,
    }


@router.get("/list")
async def list_variants():
    """Lightweight list of active variants — used by nav/breadcrumbs."""
    _check_enabled()
    return {
        "variants": [
            {
                "id": "A",
                "label": settings.variant_a_label,
                "available": _file_age_seconds(settings.hydra_state_file) is not None,
            },
            {
                "id": "B",
                "label": settings.variant_b_label,
                "available": _file_age_seconds(settings.variant_b_state_file) is not None,
            },
        ]
    }


@router.get("/{variant_id}/state")
async def get_variant_state(variant_id: str):
    """Full state of one variant (state file + enriched entries)."""
    _check_enabled()
    vid = variant_id.lower()
    if vid not in _state_readers:
        raise HTTPException(404, f"Unknown variant '{variant_id}' (expected A or B)")
    return _variant_payload(vid)


@router.get("/{variant_id}/summary")
async def get_variant_summary(variant_id: str):
    """Just the today-summary block for one variant (low-bandwidth poll)."""
    _check_enabled()
    vid = variant_id.lower()
    if vid not in _state_readers:
        raise HTTPException(404, f"Unknown variant '{variant_id}' (expected A or B)")
    state = _state_readers[vid].read_latest() or {}
    db_path = (
        settings.backtesting_db
        if vid == "a"
        else settings.variant_b_backtesting_db
    )
    return {
        "id": variant_id.upper(),
        "summary": _summary_from_state(state),
        "peak_buffer": _peak_buffer_pct(state.get("entries", []), db_path=db_path),
    }


@router.get("/comparison")
async def get_comparison():
    """The big one — both variants + a leaderboard delta computed server-side.

    Frontend polls this every ~2s. Returns enough data to render the entire
    Comparison page without further round-trips: leaderboard, strikes table,
    buffer bars, P&L line chart series.

    P&L line: each variant exposes its ``pnl_history`` list (heartbeat-written,
    one point per ~10s during market hours). Frontend can plot directly.

    The leaderboard's ``winner`` field uses NET P&L (realized - commission).
    Tie returns ``"tie"``. ``delta_net_pnl`` is signed: positive means A is
    ahead, negative means B is ahead.
    """
    _check_enabled()

    a = _variant_payload("a")
    b = _variant_payload("b")

    a_net = (a.get("summary") or {}).get("net_pnl", 0) if a.get("available") else 0
    b_net = (b.get("summary") or {}).get("net_pnl", 0) if b.get("available") else 0
    delta = a_net - b_net

    if not (a.get("available") and b.get("available")):
        winner = "n/a"
    elif abs(delta) < 0.01:
        winner = "tie"
    else:
        winner = "A" if delta > 0 else "B"

    return {
        "date": get_today_et(),
        "leaderboard": {
            "winner": winner,
            "a_net_pnl": a_net,
            "b_net_pnl": b_net,
            "delta_net_pnl": delta,  # signed: + = A leads, - = B leads
        },
        "variants": {"A": a, "B": b},
    }


@router.get("/{variant_id}/daily")
async def get_variant_daily(variant_id: str, days: int = 30):
    """Historical daily summaries for one variant (calendar/long-term view).

    Variant B's DB only has data from when comparison mode started, so the
    list will be short until the experiment has run for a few days.
    """
    _check_enabled()
    vid = variant_id.lower()
    if vid not in _db_readers:
        raise HTTPException(404, f"Unknown variant '{variant_id}'")
    summaries = await _db_readers[vid].get_daily_summaries(limit=days)
    return {"variant": variant_id.upper(), "days": len(summaries), "summaries": summaries}


def _per_variant_lifetime_stats(metrics: Optional[dict]) -> dict:
    """Distill the metrics file into a flat dict for the aggregate endpoint.
    Tolerates missing keys (variant B's metrics file may not exist yet).
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


@router.get("/aggregate")
async def get_aggregate():
    """Cross-variant lifetime + per-day aggregate for the cross-day view.

    Returns:
      - per-variant lifetime stats (cumulative_pnl, win_rate, sharpe, drawdown)
      - per-day aligned series of {date, a_net_pnl, b_net_pnl, delta} sorted
        by date ascending, suitable for direct Recharts ingestion
      - head-to-head counters: days_a_won, days_b_won, days_tied (only over
        the common-date intersection — days where one variant didn't run
        don't contribute to the H2H tally)
      - per-variant cumulative running totals (separate arrays so each
        variant's curve plots independently of the H2H window)

    Why align by date, not index: variant A's history goes back to Feb 10,
    variant B's starts the day comparison mode launched. Index alignment
    would silently compare A's Feb 10 to B's first-day, which is wrong.
    Date intersection means small N early but correct semantics.
    """
    _check_enabled()

    # Lifetime metrics (from JSON files)
    a_metrics = _metrics_readers["a"].read_latest()
    b_metrics = _metrics_readers["b"].read_latest()
    a_lifetime = _per_variant_lifetime_stats(a_metrics)
    b_lifetime = _per_variant_lifetime_stats(b_metrics)

    # Per-day data (from DBs)
    a_summaries = await _db_readers["a"].get_all_summaries()
    b_summaries = await _db_readers["b"].get_all_summaries()

    # Build date-keyed lookups so we can align without assuming order
    a_by_date = {s["date"]: s for s in a_summaries if s.get("date")}
    b_by_date = {s["date"]: s for s in b_summaries if s.get("date")}

    # Per-variant cumulative curves (each plots its own history — no alignment)
    def _cumulative_series(summaries: list[dict]) -> list[dict]:
        running = 0.0
        out = []
        for s in summaries:
            net = s.get("net_pnl") or 0.0
            running += net
            out.append({"date": s["date"], "net_pnl": net, "cumulative": round(running, 2)})
        return out

    a_curve = _cumulative_series(a_summaries)
    b_curve = _cumulative_series(b_summaries)

    # H2H aligned series — only dates where both variants have data
    common_dates = sorted(set(a_by_date) & set(b_by_date))
    per_day = []
    days_a_won = 0
    days_b_won = 0
    days_tied = 0
    cum_a = 0.0
    cum_b = 0.0
    for d in common_dates:
        a_pnl = a_by_date[d].get("net_pnl") or 0.0
        b_pnl = b_by_date[d].get("net_pnl") or 0.0
        delta = a_pnl - b_pnl
        cum_a += a_pnl
        cum_b += b_pnl
        if abs(delta) < 0.01:
            days_tied += 1
            winner = "tie"
        elif delta > 0:
            days_a_won += 1
            winner = "A"
        else:
            days_b_won += 1
            winner = "B"
        per_day.append({
            "date": d,
            "a_net_pnl": round(a_pnl, 2),
            "b_net_pnl": round(b_pnl, 2),
            "delta": round(delta, 2),
            "winner": winner,
            "cumulative_a": round(cum_a, 2),
            "cumulative_b": round(cum_b, 2),
        })

    # Advanced stats — computed per variant on each variant's full history,
    # NOT just the H2H intersection (so variant A's Sharpe reflects all 50+
    # days, not just the 5 since variant B started)
    a_pnls = [s.get("net_pnl") or 0.0 for s in a_summaries]
    b_pnls = [s.get("net_pnl") or 0.0 for s in b_summaries]
    a_advanced = _compute_advanced_stats(a_pnls)
    b_advanced = _compute_advanced_stats(b_pnls)

    # Win rates — same scope as advanced stats (full per-variant history)
    a_total = a_lifetime["winning_days"] + a_lifetime["losing_days"]
    b_total = b_lifetime["winning_days"] + b_lifetime["losing_days"]
    a_win_rate = (a_lifetime["winning_days"] / a_total) if a_total > 0 else 0.0
    b_win_rate = (b_lifetime["winning_days"] / b_total) if b_total > 0 else 0.0

    return {
        "variants": {
            "A": {
                "label": settings.variant_a_label,
                "lifetime": {**a_lifetime, "win_rate": round(a_win_rate, 4), **a_advanced},
                "cumulative_curve": a_curve,
                "total_days": len(a_summaries),
            },
            "B": {
                "label": settings.variant_b_label,
                "lifetime": {**b_lifetime, "win_rate": round(b_win_rate, 4), **b_advanced},
                "cumulative_curve": b_curve,
                "total_days": len(b_summaries),
            },
        },
        "head_to_head": {
            "common_days": len(common_dates),
            "days_a_won": days_a_won,
            "days_b_won": days_b_won,
            "days_tied": days_tied,
            "cumulative_delta_a_minus_b": round(cum_a - cum_b, 2),
            "per_day": per_day,
        },
    }
