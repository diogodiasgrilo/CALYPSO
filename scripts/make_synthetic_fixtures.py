#!/usr/bin/env python3
"""Generate dashboard fixtures containing NO real trading data.

WHY THIS EXISTS. The visual-audit harness (`dashboard/frontend/uiaudit/`) needs
~105 API payloads to render the dashboard without credentials. Until now those
were captured from the production VM and gitignored, because they carry real
positions and P&L. That made the whole harness unrunnable by CI and unrunnable
by anyone without VM access — so the 42-surface audit could only ever be a thing
a human remembered to do.

HOW IT AVOIDS DRIFT. It does NOT hand-author JSON. It builds a synthetic data
tree — real `DataRecorder` schema, real state-file shape — points `settings` at
it, and calls the REAL FastAPI router handlers. Every payload therefore has the
shape the API actually returns; a backend change that alters a response changes
the fixture too, instead of leaving the harness quietly testing a shape that no
longer exists.

The numbers are deliberately unmistakable as fabrications (round hundreds,
strikes on clean 25-pt boundaries) so no screenshot from this set can ever be
mistaken for a real session.

    python -m scripts.make_synthetic_fixtures --out dashboard/frontend/uiaudit/fixtures_synthetic
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402

#: Deliberately round, obviously-synthetic values.
SPX_BASE = 7000.0
VIX_BASE = 16.0
N_DAYS = 24            # enough to clear the ratio gate for some variants, not all
SESSION_DATE = "2026-09-16"


def _iso(d: str, hh: int, mm: int) -> str:
    return f"{d}T{hh:02d}:{mm:02d}:00-04:00"


def _build_db(db_path: Path, vid: str, meta) -> None:
    """Create a real-schema DB via DataRecorder and fill it with synthetic rows."""
    from shared.data_recorder import DataRecorder

    rec = DataRecorder(str(db_path))
    # Creates the base tables on a fresh DB — the recorder does not do this in
    # __init__, and every write is _safe_write'd, so a missing schema shows up
    # as a warning and an empty fixture rather than an error.
    rec.ensure_schema()
    wingless = meta.capital_basis == "broker_margin"
    calendar = meta.dte_class == "multi_day"
    one_sided = meta.sides == "one_sided"
    contracts = 7 if vid == "b" else 1
    width = 0.0 if wingless else (5.0 if vid in ("b", "c") else 25.0)

    start = date.fromisoformat(SESSION_DATE) - timedelta(days=N_DAYS + 10)
    d = start
    written = 0
    while written < N_DAYS:
        d += timedelta(days=1)
        if d.weekday() >= 5:                      # weekdays only
            continue
        ds = d.isoformat()
        written += 1
        # A calendar trades rarely; the 0DTE variants trade most days.
        trades = (written % 5 == 0) if calendar else (written % 7 != 0)
        spx_o = SPX_BASE + written * 5
        pnl = 0.0
        entries = 0
        stops = 0
        if trades:
            n_entries = 1 if (calendar or one_sided) else 2
            for i in range(1, n_entries + 1):
                sc = 0.0 if (one_sided or wingless and False) else spx_o + 100
                sp = spx_o - 100
                rec.record_entry({
                    "date": ds, "entry_number": i,
                    "entry_time": f"{ds} {9 + i}:45:00",
                    "entry_type": "put_only" if one_sided else "full",
                    "spx_at_entry": spx_o, "vix_at_entry": VIX_BASE,
                    "short_call_strike": 0.0 if one_sided else sc,
                    "long_call_strike": 0.0 if one_sided else sc + width,
                    "short_put_strike": sp, "long_put_strike": sp - width,
                    "call_credit": 0.0 if one_sided else 100.0 * contracts,
                    "put_credit": 100.0 * contracts,
                    "total_credit": (100.0 if one_sided else 200.0) * contracts,
                    "call_spread_width": 0.0 if one_sided else width,
                    "put_spread_width": width,
                    "contracts": contracts,
                })
                entries += 1
            # One stop every third trading day, so both outcomes are represented.
            if written % 3 == 0:
                rec.record_stop({
                    "date": ds, "entry_number": 1, "side": "put",
                    "stop_time": "11:30:00", "exit_reason": "stop_loss",
                    "actual_debit": 300.0 * contracts,
                    "net_pnl": -200.0 * contracts, "contracts": contracts,
                })
                stops = 1
                pnl = -200.0 * contracts
            else:
                pnl = 100.0 * contracts
        rec.record_daily_summary({
            "date": ds, "net_pnl": pnl, "gross_pnl": pnl,
            "entries_placed": entries, "entries_stopped": stops,
            "entries_expired": max(0, entries - stops),
            "total_credit": 200.0 * contracts if trades else 0.0,
            "commission": 0.0,
            "spx_open": spx_o, "spx_high": spx_o + 30,
            "spx_low": spx_o - 30, "spx_close": spx_o + 10,
            # One market holiday with no VIX, so the "missing VIX" path is
            # covered by the fixture set rather than only by a unit test.
            "vix_open": 0.0 if written == 4 else VIX_BASE,
            "vix_close": VIX_BASE, "contracts_per_entry": contracts,
            "day_of_week": d.strftime("%A"),
        })
    # Ticks for the session date so the SPX chart has bars to draw; without
    # them the chart renders empty and the audit never exercises it.
    for m in range(0, 390, 3):
        hh, mm = 9 + (30 + m) // 60, (30 + m) % 60
        rec.record_tick(
            timestamp=f"{SESSION_DATE} {hh:02d}:{mm:02d}:00",
            spx_price=SPX_BASE + 10 + (m % 60) - 30,
            vix_level=VIX_BASE, trend_signal="NEUTRAL",
            bot_state="Monitoring", entry_count=2, active_count=1,
        )
    try:
        rec.close()
    except Exception:
        pass


def _state(vid: str, meta) -> dict:
    """A state file shaped like the bot's, for the live/snapshot views."""
    one_sided = meta.sides == "one_sided"
    contracts = 7 if vid == "b" else 1
    entries = []
    for i in (1, 2):
        entries.append({
            "entry_number": i,
            "entry_time": _iso(SESSION_DATE, 9 + i, 45),
            "short_call_strike": 0.0 if one_sided else SPX_BASE + 100,
            "long_call_strike": 0.0 if one_sided else SPX_BASE + 125,
            "short_put_strike": SPX_BASE - 100,
            "long_put_strike": SPX_BASE - 125,
            "call_spread_credit": 0.0 if one_sided else 100.0 * contracts,
            "put_spread_credit": 100.0 * contracts,
            "contracts": contracts, "is_complete": True,
            "call_only": False, "put_only": one_sided,
            "call_side_skipped": one_sided, "put_side_skipped": False,
            "call_side_stopped": False, "put_side_stopped": i == 2,
            "call_side_expired": False, "put_side_expired": i == 1,
            "call_stop_time": "", "put_stop_time": "11:30:00" if i == 2 else "",
            "overlays": [],
            # EVERY numeric field the widgets read. The POLLED path runs these
            # through `coerceEntry`, which defaults a missing number to 0; the
            # WS path (the PRIMARY strategy) does NOT coerce, so an incomplete
            # entry renders "$NaN / $NaN" there and nowhere else. The bot always
            # writes complete entries, so this does not bite in production — but
            # a fixture that omits them tests the wrong thing.
            "call_side_stop": 0.0, "put_side_stop": 300.0 * contracts,
            "effective_call_stop": 0.0, "effective_put_stop": 300.0 * contracts,
            "actual_call_stop_debit": 0.0,
            "actual_put_stop_debit": 300.0 * contracts if i == 2 else 0.0,
            "call_spread_value": 0.0, "put_spread_value": 0.0,
            "call_long_value": 0.0, "put_long_value": 0.0,
            "short_call_fill_price": 0.0, "long_call_fill_price": 0.0,
            "short_put_fill_price": 1.0, "long_put_fill_price": 0.5,
            "call_long_sold": False, "put_long_sold": False,
            "call_long_sold_revenue": 0.0, "put_long_sold_revenue": 0.0,
            "open_commission": 0.0, "close_commission": 0.0,
            "close_reason": "stop_loss" if i == 2 else None,
            "trend_signal": None, "override_reason": None,
        })
    return {
        "date": SESSION_DATE, "state": "DailyComplete", "entries": entries,
        "entries_completed": 2, "entries_failed": 0, "entries_skipped": 0,
        "total_credit_received": 200.0 * contracts,
        "total_realized_pnl": -100.0 * contracts,
        "total_commission": 0.0,
        "call_stops_triggered": 0, "put_stops_triggered": 1,
        "one_sided_entries": 2 if one_sided else 0,
        "current_price": SPX_BASE + 10,
        "market_data_ohlc": {"spx_open": SPX_BASE, "spx_high": SPX_BASE + 30,
                             "spx_low": SPX_BASE - 30, "vix_open": VIX_BASE,
                             "vix_high": VIX_BASE + 1, "vix_low": VIX_BASE - 1},
        "pnl_history": [{"timestamp": _iso(SESSION_DATE, 10 + (m // 60), m % 60),
                         "pnl": -10.0 * m} for m in range(0, 120, 10)],
    }


def _metrics(vid: str, meta) -> dict:
    contracts = 7 if vid == "b" else 1
    return {
        "cumulative_pnl": 1000.0 * contracts, "total_entries": N_DAYS,
        "winning_days": 12, "losing_days": 6,
        "total_credit_collected": 5000.0 * contracts,
        "total_stops": 6, "double_stops": 0,
        "last_updated": SESSION_DATE,
        "daily_returns": [], "capital_deployed": 0.0,
        "entry_days": 18, "roi_pct": 0.0, "avg_capital_per_day": 0.0,
        "cumulative_baseline_date": "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="fixture output directory")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="synthfix-"))
    ids = tax.available_ids()

    # Build the synthetic data tree BEFORE importing the backend, so settings
    # can be pointed at it at import time.
    for vid in ids:
        meta = tax.meta(vid)
        vdir = tmp / f"variant_{vid}"
        vdir.mkdir(parents=True, exist_ok=True)
        _build_db(vdir / "backtesting.db", vid, meta)
        (vdir / "hydra_state.json").write_text(json.dumps(_state(vid, meta)))
        (vdir / "hydra_metrics.json").write_text(json.dumps(_metrics(vid, meta)))

    os.environ.setdefault("DASHBOARD_COMPARISON_MODE_ENABLED", "true")
    sys.path.insert(0, str(ROOT / "dashboard" / "backend"))
    from dashboard.backend.config import settings

    for vid in ids:
        vdir = tmp / f"variant_{vid}"
        for field, name in (("state_file", "hydra_state.json"),
                            ("metrics_file", "hydra_metrics.json"),
                            ("backtesting_db", "backtesting.db")):
            setattr(settings, f"variant_{vid}_{field}", vdir / name)
        setattr(settings, f"variant_{vid}_baseline_date", "")
    settings.hydra_state_file = tmp / "variant_b" / "hydra_state.json"
    settings.backtesting_db = tmp / "variant_b" / "backtesting.db"
    settings.hydra_metrics_file = tmp / "variant_b" / "hydra_metrics.json"

    from dashboard.backend.routers import strategies as S
    from dashboard.backend.routers import metrics as M
    from dashboard.backend.routers import hydra as H
    from dashboard.backend.routers import market as MK
    from dashboard.backend.routers import agents as AG
    from dashboard.backend.routers import variants as V
    from dashboard.backend.routers import dc as DC

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    def run(c):
        return asyncio.get_event_loop().run_until_complete(c) if asyncio.iscoroutine(c) else c

    def save(name, coro):
        try:
            json.dump(run(coro), open(out / f"{name}.json", "w"), default=str)
            return True
        except Exception as e:  # noqa: BLE001 — a missing fixture must not abort the set
            print(f"  SKIP {name}: {type(e).__name__}: {str(e)[:80]}")
            return False

    asyncio.set_event_loop(asyncio.new_event_loop())
    n = 0
    n += save("strategies_meta", S.get_meta())
    n += save("market_status", MK.get_status())
    n += save("market_ohlc", MK.get_ohlc(SESSION_DATE))
    n += save("market_ticks", MK.get_ticks(SESSION_DATE))
    # Missing either of these makes the dashboard render BLANK with
    # "t.map is not a function" — the mock returns an error object where the
    # UI expects a list, which is a better CI signal than a silent 404.
    n += save("agents_status", AG.get_agent_status())
    n += save("variants_health", V.get_health())
    for vid in ids:
        n += save(f"snapshot_{vid}", S.get_snapshot(vid))
        n += save(f"hydra_state_{vid}", H.get_state(strategy_id=vid))
        n += save(f"hydra_summary_{vid}", H.get_summary(strategy_id=vid))
        n += save(f"hydra_entries_{vid}", H.get_entries(strategy_id=vid))
        n += save(f"metrics_cumulative_{vid}", M.get_cumulative(strategy_id=vid))
        # Every param passed explicitly: FastAPI Query defaults are Query
        # OBJECTS, not values, when a handler is called directly.
        n += save(f"metrics_daily_{vid}", M.get_daily(days=0, year=0, strategy_id=vid))
        n += save(f"metrics_entries_{vid}", M.get_all_entries(strategy_id=vid))
        n += save(f"metrics_stops_{vid}", M.get_all_stops(strategy_id=vid))
        n += save(f"metrics_performance_{vid}", M.get_performance(strategy_id=vid))
        n += save(f"metrics_comparisons_{vid}", M.get_comparisons(strategy_id=vid))
        n += save(f"replay_pnl_{vid}", MK.get_replay_pnl(SESSION_DATE, vid))
        n += save(f"hydra_botconfig_{vid}", H.get_bot_config(strategy_id=vid))
        n += save(f"metrics_range_{vid}", M.get_date_range(strategy_id=vid))
        if tax.group(vid).pnl_shape == "debit":
            n += save(f"dc_status_{vid}", DC.dc_status(strategy_id=vid))
    for g in {tax.group(v).id for v in ids}:
        n += save(f"group_comparison_{g}", S.get_group_comparison(g))
        n += save(f"group_aggregate_{g}", S.get_group_aggregate(g))

    # The PRIMARY strategy renders from the live WebSocket, not /snapshot, so a
    # fixture set without this one cannot exercise the live seat at all.
    prim = tax.available_ids()[1] if len(ids) > 1 else ids[0]
    st = json.loads((tmp / f"variant_{prim}" / "hydra_state.json").read_text())
    ohlc = json.load(open(out / "market_ohlc.json")) if (out / "market_ohlc.json").exists() else []
    json.dump({
        "type": "snapshot", "state": st,
        "metrics": json.loads((tmp / f"variant_{prim}" / "hydra_metrics.json").read_text()),
        "market": json.load(open(out / "market_status.json")),
        "agents": (json.load(open(out / "agents_status.json")).get("agents") or []
                   if (out / "agents_status.json").exists() else []),
        "comparisons": {}, "today_entries": st["entries"], "today_stops": [],
        # /api/market/ohlc returns {date, count, bars}; the WS payload carries
        # the BARS. Passing the envelope leaves the chart empty.
        "today_ohlc": (ohlc.get("bars") if isinstance(ohlc, dict) else ohlc) or [],
        "clients": 0,
    }, open(out / "ws_snapshot.json", "w"), default=str)
    n += 1

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"  wrote {n} synthetic fixtures -> {out}")
    print("  contains NO real trading data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
