"""Brandon %-of-width stop SHADOW aggregator — should the live variant flip its stop?

The acting **credit+buffer** stop on Brandon's narrow (5pt) spreads triggers at
`credit + a fixed buffer`, which on a thin-credit 5pt spread is most of the
spread's max loss — so on a trend day it exits LATE, near max loss (2026-06-24
E#2 put realized **−$2,225**). The **%-of-width** stop triggers at a fixed
fraction of width regardless of credit, so it caps the tail earlier — but it can
stop a side prematurely that would have recovered (whipsaw).

The A2-SHADOW journal lines only capture a single first-crossing per side per
day at one `pct`. This tool instead reconstructs the FULL counterfactual from the
per-tick `spread_snapshots` (cost-to-close every ~10s) over a variant's entire
recorded history, for ANY `pct`, and compares it to the ACTUAL credit+buffer
outcome (`trade_stops.net_pnl`). So the flip decision rests on the whole record,
not one day.

For each entry-side with credit > 0:
  • shadow realized = credit − SV at the FIRST tick SV ≥ pct·width·100·contracts
    (SV clamped to [0, max_loss]); if SV never crosses → the shadow never fires →
    no change vs actual.
  • actual realized = `trade_stops.net_pnl` if that side closed via a stop/flatten
    event, else credit − last-snapshot SV (the side rode to expiry/settlement).
  • impact = shadow − actual: POSITIVE = the %-width stop would have done better
    (tail-capping win), NEGATIVE = it would have stopped a recoverer prematurely.

Pure stdlib (sqlite3 + statistics); read-only (`mode=ro`). Same contract as dc_edge.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional, Tuple

_SIDES = ("call", "put")


# ──────────────────────────────────────────────────────────────────────────────
# Data access
# ──────────────────────────────────────────────────────────────────────────────

def _load(db_path: str):
    """Return (entries, sv_series, stops, status).

    entries: {(date, entry_number): {credit/width/contracts per side, entry_type}}
    sv_series: {(date, entry_number): [(call_sv, put_sv), ...] ordered by time}
    stops: {(date, entry_number, side): net_pnl}
    """
    if not db_path or not os.path.exists(db_path):
        return {}, {}, {}, "not_found"
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
    except sqlite3.Error:
        return {}, {}, {}, "unreadable"
    try:
        entries = {}
        for r in con.execute(
            "SELECT date, entry_number, entry_type, contracts, "
            "call_credit, put_credit, call_spread_width, put_spread_width "
            "FROM trade_entries"
        ):
            entries[(r["date"], r["entry_number"])] = {
                "entry_type": r["entry_type"],
                "contracts": int(r["contracts"] or 1),
                "call": {"credit": r["call_credit"] or 0.0, "width": r["call_spread_width"] or 0.0},
                "put": {"credit": r["put_credit"] or 0.0, "width": r["put_spread_width"] or 0.0},
            }
        sv_series = {}
        for r in con.execute(
            "SELECT date, entry_number, call_spread_value, put_spread_value "
            "FROM spread_snapshots ORDER BY date, entry_number, timestamp"
        ):
            sv_series.setdefault((r["date"], r["entry_number"]), []).append(
                (r["call_spread_value"], r["put_spread_value"])
            )
        stops = {}
        for r in con.execute(
            "SELECT date, entry_number, side, net_pnl FROM trade_stops"
        ):
            stops[(r["date"], r["entry_number"], r["side"])] = r["net_pnl"]
        return entries, sv_series, stops, "ok"
    except sqlite3.Error:
        return {}, {}, {}, "unreadable"
    finally:
        con.close()


# ──────────────────────────────────────────────────────────────────────────────
# Per-side simulation
# ──────────────────────────────────────────────────────────────────────────────

def _clamp(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)


def simulate_side(credit: float, width: float, contracts: int,
                  sv_list: list, actual_net_pnl: Optional[float],
                  pct: float) -> Optional[dict]:
    """Counterfactual for one entry-side at threshold `pct`.

    Returns None when the side has no credit/width or no snapshots (nothing to
    reconstruct). When the %-width stop never fires (SV never crosses), returns a
    row with fired=False and impact=0 (it changes nothing vs actual).
    """
    if credit <= 0 or width <= 0 or contracts <= 0 or not sv_list:
        return None
    max_loss = width * 100.0 * contracts
    trigger = pct * width * 100.0 * contracts

    clean = [_clamp(float(sv), 0.0, max_loss) for sv in sv_list if sv is not None]
    if not clean:
        return None

    # actual realized: prefer the recorded stop/flatten P&L; else the side rode to
    # expiry/settlement → credit − last cost-to-close.
    if actual_net_pnl is not None:
        actual = float(actual_net_pnl)
    else:
        actual = credit - clean[-1]

    shadow_sv = next((sv for sv in clean if sv >= trigger), None)
    if shadow_sv is None:
        return {"fired": False, "shadow": actual, "actual": actual, "impact": 0.0,
                "actual_stopped": actual_net_pnl is not None}
    shadow = credit - shadow_sv
    return {"fired": True, "shadow": round(shadow, 2), "actual": round(actual, 2),
            "impact": round(shadow - actual, 2),
            "actual_stopped": actual_net_pnl is not None}


# ──────────────────────────────────────────────────────────────────────────────
# Aggregate
# ──────────────────────────────────────────────────────────────────────────────

def analyze_pct(entries, sv_series, stops, pct: float, *, width_max: Optional[float] = None) -> dict:
    """Aggregate the flip impact across every entry-side at one `pct`."""
    rows = []
    for key, e in entries.items():
        date, en = key
        svs = sv_series.get(key)
        if not svs:
            continue
        for i, side in enumerate(_SIDES):
            s = e[side]
            if width_max is not None and s["width"] and s["width"] > width_max:
                continue
            sv_list = [pair[i] for pair in svs]
            r = simulate_side(s["credit"], s["width"], e["contracts"], sv_list,
                              stops.get((date, en, side)), pct)
            if r is None:
                continue
            r.update({"date": date, "entry": en, "side": side})
            rows.append(r)

    fired = [r for r in rows if r["fired"]]
    wins = [r for r in fired if r["impact"] > 0]
    losses = [r for r in fired if r["impact"] < 0]
    # tail-capping = fired AND the actual side closed via a stop at a worse number
    tail = [r for r in fired if r["actual_stopped"] and r["impact"] > 0]
    premature = [r for r in fired if not r["actual_stopped"] and r["impact"] < 0]
    return {
        "pct": pct,
        "sides_total": len(rows),
        "sides_fired": len(fired),
        "net_impact": round(sum(r["impact"] for r in fired), 2),
        "win_count": len(wins), "win_total": round(sum(r["impact"] for r in wins), 2),
        "loss_count": len(losses), "loss_total": round(sum(r["impact"] for r in losses), 2),
        "tail_capping_count": len(tail), "tail_capping_total": round(sum(r["impact"] for r in tail), 2),
        "premature_count": len(premature), "premature_total": round(sum(r["impact"] for r in premature), 2),
        "worst_actual": min((r["actual"] for r in fired), default=None),
        "rows": fired,
    }


def analyze(db_path: str, *, pcts=(0.25, 0.40, 0.50, 0.65),
            width_max: Optional[float] = None) -> dict:
    """Full report: the flip impact at several `pct` thresholds. Never raises."""
    entries, sv_series, stops, status = _load(db_path)
    dates = sorted({d for (d, _) in entries})
    results = [analyze_pct(entries, sv_series, stops, p, width_max=width_max) for p in pcts]
    return {
        "db_path": db_path, "db_status": status,
        "n_entries": len(entries), "n_actual_stops": len(stops),
        "date_min": dates[0] if dates else None, "date_max": dates[-1] if dates else None,
        "width_max": width_max,
        "by_pct": results,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Rendering
# ──────────────────────────────────────────────────────────────────────────────

def _m(v):
    return "—" if v is None else f"{v:+,.0f}"


def format_report(result: dict, title: str = "Variant C") -> str:
    lines = [
        f"=== {title} — %-of-width stop SHADOW vs acting credit+buffer ===",
        f"DB: {result['db_path']}  [{result['db_status']}]",
        f"History: {result['date_min']} → {result['date_max']}  "
        f"({result['n_entries']} entries, {result['n_actual_stops']} recorded side-closes)"
        + (f"  width≤{result['width_max']:.0f}pt" if result.get("width_max") else ""),
        "",
        "Net $ the %-of-width stop WOULD have added (+) or cost (−) vs the actual",
        "credit+buffer outcome, by threshold. 'tail-capping' = bigger loss the actual",
        "stop took late; 'premature' = a recoverer the %-width would have stopped early.",
        "",
        f"  {'pct':>5} | {'fired':>5} | {'NET $':>9} | {'tail-cap (n)':>16} | {'premature (n)':>16}",
        f"  {'-'*5}-+-{'-'*5}-+-{'-'*9}-+-{'-'*16}-+-{'-'*16}",
    ]
    for r in result["by_pct"]:
        lines.append(
            f"  {r['pct']*100:4.0f}% | {r['sides_fired']:>5} | {_m(r['net_impact']):>9} | "
            f"{_m(r['tail_capping_total'])+' ('+str(r['tail_capping_count'])+')':>16} | "
            f"{_m(r['premature_total'])+' ('+str(r['premature_count'])+')':>16}"
        )
    # spotlight the best
    best = max(result["by_pct"], key=lambda r: r["net_impact"], default=None)
    if best and best["sides_fired"]:
        lines += [
            "",
            f"Best threshold by net: {best['pct']*100:.0f}%  →  net {_m(best['net_impact'])}  "
            f"(wins {_m(best['win_total'])} over {best['win_count']}, "
            f"losses {_m(best['loss_total'])} over {best['loss_count']}).",
            f"Worst single actual loss in the fired set: ${best['worst_actual']:,.0f} "
            "(what the %-width stop would have capped earlier).",
        ]
    return "\n".join(lines)
