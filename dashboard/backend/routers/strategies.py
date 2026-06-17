"""Data-driven, taxonomy-sourced strategy API (additive — keeps /api/variants/*
and /api/dc/* working unchanged).

This is the new "strategy picker + group comparison" backend (design
``docs/STRATEGY_GROUPING_REDESIGN.md`` §2a). Identity comes from the single source
of truth ``shared.strategy_taxonomy`` (stdlib-only — importing it pulls in NO
trading/broker/google deps), NOT from a hardcoded list of letters. Every surface
that used to special-case ``if vid == "d"`` reads the taxonomy instead.

Endpoints (all under the same ``_api_guard`` as the other routers — an unguarded
data endpoint would be an info-leak regression):

  * ``GET /api/strategies/meta``                         — boot payload (groups + strategies)
  * ``GET /api/strategies/{id}/snapshot``                — per-strategy main-page payload
  * ``GET /api/strategies/groups/{group_id}/comparison`` — group-scoped H2H
  * ``GET /api/strategies/groups/{group_id}/aggregate``  — group-scoped cross-day

A strategy's ``data_kind`` (``ic_state`` vs ``dc_calendar``) drives which reader +
payload shape is used. The two kinds are SHAPE-DISTINCT: the calendar payload is
debit-native (no credit/buffer/spread-width fields). NEVER expose filesystem paths.

100% read-only — every reader tolerates a missing file (``available: false``,
never 500). No write path anywhere.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from shared import strategy_taxonomy as tax

from dashboard.backend.config import settings
from dashboard.backend.routers import variants as variants_router
from dashboard.backend.services.dc_db_reader import DCDBReader
from dashboard.backend.services.dc_reader import read_dc_status
from dashboard.backend.services.market_status import get_today_et

logger = logging.getLogger("dashboard.strategies")

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _run_coro(coro):
    """Run an async DB-reader coroutine to completion from the SYNC snapshot
    builders, whether or not an event loop is already running.

    The snapshot helpers are sync (``_ic_snapshot`` is called synchronously from
    the ``async`` route), but the ``BacktestingDBReader`` methods are
    ``to_thread``-wrapped coroutines. ``asyncio.run`` would raise
    ``RuntimeError`` when invoked from inside the FastAPI request loop, so when a
    loop is already running we drive the coroutine on its own loop in a worker
    thread (the reads are tiny one-day queries on a 2s poll — cheap). With no
    running loop (e.g. unit tests calling the helper directly) we use
    ``asyncio.run`` directly.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — safe to drive it directly.
        return asyncio.run(coro)

    # A loop is already running on this thread (the FastAPI request handler).
    # Run the coroutine on a fresh loop in a worker thread and block for it.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# Identity / configuration
# ---------------------------------------------------------------------------
# The CANONICAL / primary strategy id (the one the main dashboard defaults to).
# Today this is the live Brandon-narrow variant "c" (see config.py — the
# primary hydra_*/bot_config_file paths point at data/variant_c/*). It is NOT a
# taxonomy field (the taxonomy's status="live" is operational metadata, and two
# variants are "live") — the primary is a deployment choice, surfaced here so the
# frontend picker can default to it.
PRIMARY_ID = "c"


def _data_kind(m: tax.StrategyMeta) -> str:
    """``dc_calendar`` for net-debit double calendars, else ``ic_state``."""
    return "dc_calendar" if m.structure_family == "double_calendar" else "ic_state"


def _variant_dir(vid: str) -> Optional[Path]:
    """The data dir for a variant (where the DC sidecar + dc_calendar.db live),
    derived from its state-file setting. None when no state-file is configured.

    Internal only — paths are NEVER returned to the client.
    """
    state_file = getattr(settings, f"variant_{vid}_state_file", None)
    if state_file is None:
        return None
    return Path(state_file).parent


def _state_file_for(vid: str) -> Optional[Path]:
    return getattr(settings, f"variant_{vid}_state_file", None)


def _is_available(vid: str) -> bool:
    """True iff the variant's state file exists (its bot has run)."""
    sf = _state_file_for(vid)
    try:
        return sf is not None and Path(sf).exists()
    except OSError:
        return False


def _file_age_seconds(path: Optional[Path]) -> Optional[float]:
    try:
        if path is None or not Path(path).exists():
            return None
        return time.time() - Path(path).stat().st_mtime
    except OSError:
        return None


def _capabilities(m: tax.StrategyMeta) -> dict:
    """What dashboard surfaces a strategy can drive.

    - main_dashboard: every registered strategy can be the picker's selection.
    - comparison: only members of a *comparable* group.
    - history / analytics: today these pages are hard-bound to the canonical-C
      IC readers (metrics.py), so only the IC group can drive them until those
      routers are parameterized (audit AUD-3-F4). Calendar strategies expose
      calendar_cards instead.
    - calendar_cards: the DC-native open-calendars/outcomes view (dc_calendar).
    """
    is_ic = m.structure_family == "iron_condor"
    comparable = tax.group(m.id).comparable
    return {
        "main_dashboard": True,
        "comparison": comparable,
        "history": is_ic,
        "analytics": is_ic,
        "calendar_cards": _data_kind(m) == "dc_calendar",
    }


def _strategy_meta_dict(m: tax.StrategyMeta) -> dict:
    """Per-strategy row for /meta — taxonomy-sourced, NO file paths."""
    return {
        "id": m.id,
        "display_name": m.display_name,
        "group_id": m.group_id,
        "family": m.structure_family,  # spec: family (= structure_family)
        "pnl_shape": m.pnl_shape,
        "data_kind": _data_kind(m),
        "is_live": m.status == "live",
        "is_primary": m.id == PRIMARY_ID,
        "available": _is_available(m.id),
        "capabilities": _capabilities(m),
    }


# ---------------------------------------------------------------------------
# /meta — the boot payload
# ---------------------------------------------------------------------------


@router.get("/meta")
async def get_meta():
    """Boot payload: comparability groups + every registered strategy.

    Sourced entirely from ``shared.strategy_taxonomy``. ``groups[].member_ids``
    are derived from the taxonomy (the group->members mapping is data, not a
    hardcoded list). ``primary_id`` is the configured canonical strategy.
    """
    groups = []
    for gid, g in tax.GROUPS.items():
        groups.append({
            "id": g.id,
            "label": g.label,
            "pnl_shape": g.pnl_shape,
            "comparable": g.comparable,
            "member_ids": tax.members(gid),
            # The baseline a group's deltas are measured against — the first
            # registered member (definition order), surfaced so the comparison
            # endpoint isn't hardcoded to "A".
            "baseline_id": (tax.members(gid)[0] if tax.members(gid) else None),
        })

    strategies = [_strategy_meta_dict(tax.meta(sid)) for sid in tax.available_ids()]

    return {
        "primary_id": PRIMARY_ID,
        "groups": groups,
        "strategies": strategies,
    }


# ---------------------------------------------------------------------------
# /{id}/snapshot — per-strategy main-page payload
# ---------------------------------------------------------------------------


def _header_chrome(vid: str, m: tax.StrategyMeta) -> dict:
    """The FULL header chrome the frontend needs to re-bind to the selection
    (audit AUD-3-F1): label, dry_run, underlying_symbol, display_name.

    Read from the variant's OWN config.json (the dry_run + underlying are
    per-strategy — a QQQ/SPY calendar must NOT show the primary's SPX). Missing
    config degrades to taxonomy defaults, never raises.
    """
    cfg_path = getattr(settings, f"variant_{vid}_config_file", None)
    dry_run: Optional[bool] = None
    underlying: Optional[str] = None
    label = getattr(settings, f"variant_{vid}_label", None) or m.display_name
    if cfg_path is not None:
        try:
            import json
            with open(cfg_path) as f:
                cfg = json.load(f)
            dry_run = bool(cfg.get("dry_run", False))
            s = cfg.get("strategy", {}) or {}
            underlying = s.get("underlying_symbol") or cfg.get("underlying_symbol")
        except Exception as e:  # missing / unreadable config — degrade gracefully
            logger.debug(f"snapshot header chrome: could not read config for {vid}: {e}")
    return {
        "display_name": m.display_name,
        "label": label,
        "dry_run": dry_run,
        "underlying_symbol": underlying,
    }


def _read_variant_ohlc(db_path: Optional[Path]) -> list:
    """Today's 1-min SPX OHLC bars for a variant, from its OWN backtesting DB.

    Mirrors the WS broadcaster / ``/api/market/ohlc`` priority so the polled
    SPXChart gets the SAME ``{timestamp, open, high, low, close, vix}`` shape:
      1. HOMER's authoritative ``market_ohlc_1min`` (post-close), else
      2. dense bars computed live from that DB's ``market_ticks`` (intraday).
    Returns ``[]`` on missing DB / empty table (never raises). Read-only.

    NOTE: the BacktestingDBReader methods are async (``to_thread`` wrapped); this
    snapshot path is sync, so we drive them through a private event loop. The
    reads are tiny (one day of 1-min bars) and infrequent (2s poll), so the
    per-call loop is cheap and keeps this helper callable from the sync snapshot.
    """
    if db_path is None:
        return []
    try:
        if not Path(db_path).exists():
            return []
    except OSError:
        return []

    from dashboard.backend.services.db_reader import BacktestingDBReader

    reader = BacktestingDBReader(Path(db_path))
    today = get_today_et()

    async def _go() -> list:
        if not await reader.is_available():
            return []
        bars = await reader.get_today_ohlc(today)
        if bars:
            return bars
        return await reader.compute_ohlc_from_ticks(today)

    try:
        return _run_coro(_go()) or []
    except Exception as e:  # pragma: no cover — defensive, never block the snapshot
        logger.debug(f"snapshot ohlc read failed for {db_path}: {e}")
        return []


def _read_variant_cumulative(vid: str) -> dict:
    """The variant's OWN lifetime cumulative metrics, in the shape
    ``DailyPnLCard``'s "Cumulative" section reads (cumulative_pnl, winning_days,
    losing_days, total_credit_collected, roi_pct, avg_capital_per_day, ...).

    Sourced like ``/api/metrics/cumulative`` but per-variant: the variant's
    ``hydra_metrics.json`` file as the base, OVERLAID with DB-canonical
    aggregates from its OWN backtesting DB (``apply_db_cumulative``) so a stale
    metrics file can't drift the card. Rebased to the variant's own
    ``baseline_date`` when set. Returns ``{}`` when neither source exists.
    """
    from dashboard.backend.services.metrics_reader import MetricsFileReader
    from dashboard.backend.services.db_reader import (
        BacktestingDBReader,
        apply_db_cumulative,
    )

    metrics_file = getattr(settings, f"variant_{vid}_metrics_file", None)
    db_path = getattr(settings, f"variant_{vid}_backtesting_db", None)
    baseline = getattr(settings, f"variant_{vid}_baseline_date", "") or ""

    base: Optional[dict] = None
    if metrics_file is not None:
        try:
            base = MetricsFileReader(Path(metrics_file)).read_latest()
        except Exception as e:
            logger.debug(f"snapshot cumulative metrics read failed for {vid}: {e}")

    overrides: dict = {}
    if db_path is not None:
        try:
            if Path(db_path).exists():
                overrides = _run_coro(
                    BacktestingDBReader(Path(db_path)).get_cumulative_overrides(baseline)
                ) or {}
        except Exception as e:
            logger.debug(f"snapshot cumulative DB read failed for {vid}: {e}")

    merged = apply_db_cumulative(base, overrides)
    if merged is None:
        return {}
    merged = dict(merged)
    # Echo the rebase baseline (empty = full history) for the UI's "since" caption,
    # matching /api/metrics/cumulative's contract.
    merged["cumulative_baseline_date"] = baseline
    return merged


def _read_variant_performance(vid: str) -> dict:
    """Advanced stats (Sharpe/Sortino/Max Drawdown/Calmar/Profit Factor/
    Expectancy/Win-Loss) over the variant's OWN daily summaries.

    Returns ``{"daily_pnls": [...]}`` — the SAME shape ``/api/metrics/performance``
    returns — so the polled ``PerformanceMetrics`` computes the identical ratios
    client-side. ``{"daily_pnls": []}`` when the DB is missing / has no
    summaries (the widget then shows its "need ≥2 days" note). Rebased to the
    variant's own ``baseline_date`` when set.

    Also surfaces ``advanced`` (Sharpe / max_drawdown / best / worst) computed by
    ``variants_router._compute_advanced_stats`` — the SAME computation the
    Comparison/aggregate pages use — for callers/tests that want the
    server-computed numbers without re-deriving them from ``daily_pnls``.
    """
    from dashboard.backend.services.db_reader import BacktestingDBReader

    db_path = getattr(settings, f"variant_{vid}_backtesting_db", None)
    baseline = getattr(settings, f"variant_{vid}_baseline_date", "") or ""
    if db_path is None:
        return {"daily_pnls": [], "advanced": variants_router._compute_advanced_stats([])}
    try:
        if not Path(db_path).exists():
            return {"daily_pnls": [], "advanced": variants_router._compute_advanced_stats([])}
        pnls = _run_coro(BacktestingDBReader(Path(db_path)).get_daily_pnls(baseline)) or []
    except Exception as e:
        logger.debug(f"snapshot performance read failed for {vid}: {e}")
        pnls = []
    return {
        "daily_pnls": pnls,
        "advanced": variants_router._compute_advanced_stats(pnls),
    }


def _ic_snapshot(vid: str, m: tax.StrategyMeta) -> dict:
    """IC (``ic_state``) per-strategy snapshot — reuses the existing per-variant
    state/summary/entries readers exactly as /api/variants does. Missing state
    file → available:false (the variants payload already does this).

    FULL-MIRROR enrichment (2026-06): so a non-primary IC selection renders the
    SAME panels as the primary WS view, the body also carries the variant's OWN
    ``ohlc`` (SPX 1-min bars for the candle chart), ``cumulative`` (lifetime
    metrics for the DailyPnLCard "Cumulative" section), and ``performance``
    (daily P&L array + advanced stats for PerformanceMetrics). Every one of these
    reads the variant's OWN db/metrics files (NOT the canonical-C ones) and is
    missing-file-tolerant — an absent file degrades to ``[]`` / ``{}``, never a
    500."""
    # variants_router._variant_payload builds the same shape /api/variants and
    # the broadcaster use; but it only knows _VARIANT_IDS. For an id that lives
    # in settings but not _VARIANT_IDS we still build from settings paths via the
    # same readers, so reuse its building blocks rather than its registry.
    from dashboard.backend.services.state_reader import StateFileReader

    state_file = _state_file_for(vid)
    db_path = getattr(settings, f"variant_{vid}_backtesting_db", None)
    age = _file_age_seconds(state_file)
    if age is None:
        return {"available": False, "reason": "State file missing"}

    state = StateFileReader(state_file).read_latest() or {}
    entries = state.get("entries", [])
    body = {
        "available": True,
        "state_file_age_seconds": round(age, 1),
        "summary": variants_router._summary_from_state(state),
        "entries": variants_router._enrich_entries(entries),
        "pnl_history": state.get("pnl_history", []),
        "min_buffer_margin": variants_router._min_buffer_margin_pct(entries, db_path=db_path),
        "spx_price": variants_router._latest_spx_from_db(db_path),
        "spx_open": (state.get("market_data_ohlc") or {}).get("spx_open"),
        "vix_open": (state.get("market_data_ohlc") or {}).get("vix_open"),
        "spx_high": (state.get("market_data_ohlc") or {}).get("spx_high"),
        "spx_low": (state.get("market_data_ohlc") or {}).get("spx_low"),
        "state": state.get("state"),
        "date": state.get("date"),
        # Full-mirror enrichment — the variant's OWN data, each missing-file-tolerant.
        "ohlc": _read_variant_ohlc(db_path),
        "cumulative": _read_variant_cumulative(vid),
        "performance": _read_variant_performance(vid),
    }
    return body


def _dc_snapshot(vid: str, m: tax.StrategyMeta) -> dict:
    """Calendar (``dc_calendar``) per-strategy snapshot — reuses dc_reader's
    open-calendars + recent-outcomes view (the same one /api/dc/status serves).
    SHAPE-DISTINCT: no IC summary/entries/buffer fields."""
    vd = _variant_dir(vid)
    has_sidecar = vd is not None and (vd / "dc_open_trades.json").exists()
    has_db = vd is not None and (vd / "dc_calendar.db").exists()
    # Available iff the bot has run (state file present) OR it has produced any
    # calendar artifact. Otherwise the strategy hasn't started → available:false.
    if not (_is_available(vid) or has_sidecar or has_db):
        return {"available": False, "reason": "No calendar artifacts"}
    status = read_dc_status(
        str(vd / "dc_open_trades.json") if vd else None,
        str(vd / "dc_calendar.db") if vd else None,
    )
    return {
        "available": True,
        "open_calendars": status.get("open_calendars", []),
        "recent_outcomes": status.get("recent_outcomes", []),
        "calendar_summary": status.get("summary", {}),
    }


@router.get("/{strategy_id}/snapshot")
async def get_snapshot(strategy_id: str):
    """Per-strategy main-page payload. The envelope carries ``data_kind`` + the
    full header chrome (display_name/label/dry_run/underlying_symbol) so the
    frontend can re-bind the WHOLE header to the selection (audit AUD-3-F1).

    Body shape depends on ``data_kind``: ``ic_state`` (state/summary/entries) vs
    ``dc_calendar`` (open calendars/outcomes). A missing state file →
    ``available:false`` in the body, never a 500.
    """
    vid = (strategy_id or "").strip().lower()
    m = tax.meta(vid)  # unknown letter → safe sentinel (never KeyError)
    kind = _data_kind(m)

    if kind == "dc_calendar":
        body = _dc_snapshot(vid, m)
    else:
        body = _ic_snapshot(vid, m)

    return {
        "id": vid,
        "data_kind": kind,
        "group_id": m.group_id,
        "pnl_shape": m.pnl_shape,
        "is_primary": vid == PRIMARY_ID,
        **_header_chrome(vid, m),
        "body": body,
    }


# ---------------------------------------------------------------------------
# group-scoped comparison + aggregate
# ---------------------------------------------------------------------------


def _resolve_group(group_id: str) -> tax.GroupMeta:
    g = tax.GROUPS.get(group_id)
    if g is None:
        raise HTTPException(404, f"Unknown group '{group_id}'")
    return g


def _group_member_ids(group_id: str) -> list[str]:
    return tax.members(group_id)


def _group_baseline_id(group_id: str) -> str:
    """The baseline a group's H2H deltas are measured against — the first
    registered member (definition order), from group metadata. NOT hardcoded A."""
    mids = tax.members(group_id)
    return mids[0] if mids else ""


@router.get("/groups/{group_id}/comparison")
async def get_group_comparison(group_id: str):
    """Group-scoped head-to-head. Returns ONE ``family``/``pnl_shape`` per group
    + a ``baseline_id`` from group metadata. The payload SHAPE differs by family:

      * ``ic_0dte`` (credit) — delegates to variants.build_comparison scoped to
        the group's members (the IC leaderboard/strikes/buffer payload).
      * ``calendar_multiday`` (debit) — a SHAPE-DISTINCT calendar payload built
        from DCDBReader; carries NO IC keys (no total_credit/buffer/width).
    """
    g = _resolve_group(group_id)
    member_ids = _group_member_ids(group_id)
    baseline_id = _group_baseline_id(group_id)
    family = _group_family(member_ids)

    envelope = {
        "group_id": g.id,
        "label": g.label,
        "family": family,
        "pnl_shape": g.pnl_shape,
        "comparable": g.comparable,
        "baseline_id": baseline_id.upper() if baseline_id else None,
        "member_ids": member_ids,
        "date": get_today_et(),
    }

    if g.pnl_shape == "debit":
        envelope["calendars"] = await _calendar_group_comparison(member_ids)
    else:
        ic = variants_router.build_comparison(member_ids, baseline_id=baseline_id or "a")
        # Merge the IC payload (variants/leaderboard) under the group envelope.
        envelope["leaderboard"] = ic.get("leaderboard")
        envelope["variants"] = ic.get("variants")

    return envelope


@router.get("/groups/{group_id}/aggregate")
async def get_group_aggregate(group_id: str):
    """Group-scoped cross-day aggregate. Same family/shape rules as
    ``/comparison`` — IC delegates to variants.build_aggregate scoped to the
    group; calendar builds a debit-native cross-day rollup (no IC keys)."""
    g = _resolve_group(group_id)
    member_ids = _group_member_ids(group_id)
    family = _group_family(member_ids)

    envelope = {
        "group_id": g.id,
        "label": g.label,
        "family": family,
        "pnl_shape": g.pnl_shape,
        "comparable": g.comparable,
        "member_ids": member_ids,
    }

    if g.pnl_shape == "debit":
        envelope["calendars"] = await _calendar_group_aggregate(member_ids)
    else:
        agg = await variants_router.build_aggregate(member_ids)
        envelope["variants"] = agg.get("variants")
        envelope["head_to_head"] = agg.get("head_to_head")

    return envelope


def _group_family(member_ids: list[str]) -> str:
    """The single structure_family of a group's members (the apples-to-apples
    guarantee). Defaults to the first member's family; 'unknown' if empty."""
    for vid in member_ids:
        fam = tax.meta(vid).structure_family
        if fam and fam != "unknown":
            return fam
    return "unknown"


def _calendar_dc_reader(vid: str) -> Optional[DCDBReader]:
    """A DCDBReader for a calendar variant's dc_calendar.db, or None if its
    data dir isn't configured."""
    vd = _variant_dir(vid)
    if vd is None:
        return None
    return DCDBReader(vd / "dc_calendar.db")


async def _calendar_group_comparison(member_ids: list[str]) -> dict:
    """SHAPE-DISTINCT calendar comparison (debit-native). Per member: lifetime
    debit-native totals (capital_at_risk = SUM(net_debit)) + recent outcomes +
    open calendars. NO credit/buffer/spread-width keys anywhere.

    The leaderboard scores on lifetime ``cumulative_pnl`` (calendars are
    multi-day; there's no single "today net P&L" the way a 0DTE IC has)."""
    per_member: dict[str, dict] = {}
    scores: dict[str, float] = {}
    for vid in member_ids:
        m = tax.meta(vid)
        reader = _calendar_dc_reader(vid)
        if reader is None or not await reader.is_available():
            per_member[vid.upper()] = {
                "id": vid.upper(),
                "display_name": m.display_name,
                "available": False,
            }
            continue
        overrides = await reader.get_cumulative_overrides_calendar()
        outcomes = await reader.get_calendar_outcomes(limit=10)
        # Open calendars from the sidecar (D/E persist via dc_open_trades.json).
        vd = _variant_dir(vid)
        status = read_dc_status(
            str(vd / "dc_open_trades.json") if vd else None,
            str(vd / "dc_calendar.db") if vd else None,
        )
        per_member[vid.upper()] = {
            "id": vid.upper(),
            "display_name": m.display_name,
            "available": True,
            "lifetime": overrides,           # debit-native: cumulative_pnl, capital_at_risk, ...
            "recent_outcomes": outcomes,
            "open_calendars": status.get("open_calendars", []),
            "open_summary": status.get("summary", {}),
        }
        scores[vid.upper()] = overrides.get("cumulative_pnl", 0.0) or 0.0

    if not scores:
        winner = "n/a"
    else:
        best = max(scores.values())
        leaders = [vid for vid, s in scores.items() if abs(s - best) < 0.01]
        winner = leaders[0] if len(leaders) == 1 else "tie"

    return {
        "leaderboard": {
            "winner": winner,
            "scores": scores,  # {id: lifetime cumulative realized P&L (debit-native)}
            "basis": "lifetime_realized_pnl",
        },
        "members": per_member,
    }


async def _calendar_group_aggregate(member_ids: list[str]) -> dict:
    """Calendar cross-day aggregate (debit-native). Per member: a daily P&L
    series attributed to ``close_date`` + a running cumulative curve, plus the
    lifetime debit-native totals. No IC keys."""
    members_payload: dict[str, dict] = {}
    for vid in member_ids:
        m = tax.meta(vid)
        reader = _calendar_dc_reader(vid)
        if reader is None or not await reader.is_available():
            members_payload[vid.upper()] = {
                "id": vid.upper(),
                "display_name": m.display_name,
                "available": False,
            }
            continue
        daily = await reader.get_daily_calendar_summaries()
        overrides = await reader.get_cumulative_overrides_calendar()
        # Running cumulative curve (close-date attributed → monotonic in time).
        running = 0.0
        curve = []
        for d in daily:
            running += d.get("net_pnl") or 0.0
            curve.append({"date": d["date"], "net_pnl": d.get("net_pnl") or 0.0,
                          "cumulative": round(running, 2)})
        members_payload[vid.upper()] = {
            "id": vid.upper(),
            "display_name": m.display_name,
            "available": True,
            "lifetime": overrides,
            "daily": daily,
            "cumulative_curve": curve,
        }
    return {"members": members_payload}
