"""Per-SLOT edge analyzer for the 0DTE iron-condor variants (A/B/C).

Variant B ("Brandon Narrow (4-slot)") is the uncapped "take everything" tester:
it fires at every slot (09:45 / 10:45 / 11:15 / 11:45 + the E6 conditional), so
its forward dry-run record is the dataset that tells us which SLOTS are the best
winners — i.e. which entries the live variant C should keep, drop, or add. This
module reads that edge off a variant's ``backtesting.db`` and aggregates it by
entry SLOT, with the honesty properties dc_edge.py makes load-bearing.

PER-ENTRY P&L RECONSTRUCTION (per side, summed) — the dry-run path does not store
a single per-entry realized-P&L field, so we reconstruct it from the parts:

  • side did NOT stop           → kept its full credit (expired worthless): +credit
  • side stopped, net_pnl set    → use trade_stops.net_pnl (the side's realized P&L,
       already = side_credit − close_debit, contract-scaled)
  • side stopped, net_pnl NULL   → reconstruct from the spread_snapshots value
       nearest the stop time (the debit to close): side_credit − spread_value
  • side stopped, no snapshot     → the entry is UNSCORED: counted, but EXCLUDED
       from the P&L means so a data gap never silently biases a slot.

Reconstructed totals are sanity-checked against ``daily_summaries.net_pnl`` and the
discrepancy is disclosed in the report.

METRIC: per-entry NET P&L in account dollars (already contract-scaled — credit and
net_pnl are both totals; B holds contracts constant so dollars are comparable
across its slots). Each slot's verdict gates on a one-sample Student-t 95% CI on
mean P&L/entry (t, not z) AND a sample-size floor, so a thin slot is reported
INSUFFICIENT rather than ranked on noise. The CI assumes independent entries.

Pure stdlib (sqlite3 + statistics), read-only (``mode=ro``), importable without the
broker stack — same contract as dc_edge.py / dc_status.py.

⚠ KNOWN DATA CAVEAT — the Brandon variants (B, C) are NOT yet analyzable here.
On B/C the credit+buffer HYDRA stop runs in SHADOW (it never acts — Brandon
closes via take-profit / GEX-breach / overlay / expiry). But the shadow stop's
*hypothetical* fills are written to ``trade_stops`` with a real-looking
``net_pnl``, so reconstruction sees phantom losses: e.g. 2026-06-12 booked
``entries_stopped=4, net_pnl=+1732`` (a PROFITABLE day) yet trade_stops shows all
8 sides "stopped" at ~-1760 (-13.4k). Brandon's REAL per-entry outcome is not
recorded anywhere — only the daily total (``daily_summaries.net_pnl``) is. So a
trustworthy per-SLOT ranking for B/C is blocked until each Brandon close records
its real realized P&L per entry (then this analyzer consumes it directly). The
reconstruction logic here is correct and is validated for variants whose
``trade_stops`` are REAL acted stops; the ``daily_summaries`` cross-check in the
report is what surfaces the B/C pollution (a large drift = do not trust the
absolute P&L OR the ranking).
"""

from __future__ import annotations

import os
import sqlite3
from statistics import mean, stdev
from typing import Optional

# 95% two-sided Student-t critical values by df (mirrors dc_edge.py). t (not z)
# keeps the CI honest at the sample-size gate; >120 -> z; untabulated df uses the
# largest tabulated df <= ours (slightly WIDER = conservative).
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042, 40: 2.021, 50: 2.009, 60: 2.000, 80: 1.990, 100: 1.984,
    120: 1.980,
}
_Z95 = 1.960
_CENT = 2
_SCRATCH_EPS = 0.005  # |pnl| at/below this (after rounding) is a scratch, not win/loss


def _t_crit(df: int) -> float:
    """95% two-sided t critical value for df, conservative for untabulated df."""
    if df <= 0:
        return float("inf")
    if df in _T95:
        return _T95[df]
    if df > 120:
        return _Z95
    lower = max(k for k in _T95 if k <= df)
    return _T95[lower]


def _mean_ci(xs: list) -> Optional[list]:
    """[mean, lo, hi] 95% Student-t CI on the mean; None if < 2 samples."""
    n = len(xs)
    if n < 2:
        return None
    m = mean(xs)
    sd = stdev(xs)
    half = _t_crit(n - 1) * sd / (n ** 0.5)
    return [m, m - half, m + half]


# ──────────────────────────────────────────────────────────────────────────────
# Slot bucketing
# ──────────────────────────────────────────────────────────────────────────────

# Canonical 0DTE entry slots across A/B/C (minutes from ET midnight). E6 is the
# 14:00 conditional. An entry is mapped to the nearest slot within _SLOT_TOL_MIN;
# anything further (off-schedule / manual) buckets to "other".
CANONICAL_SLOTS = {
    "09:45": 9 * 60 + 45,
    "10:15": 10 * 60 + 15,
    "10:45": 10 * 60 + 45,
    "11:15": 11 * 60 + 15,
    "11:45": 11 * 60 + 45,
    "12:15": 12 * 60 + 15,
    "E6 14:00": 14 * 60,
}
_SLOT_ORDER = list(CANONICAL_SLOTS.keys()) + ["other"]
_SLOT_TOL_MIN = 12


def slot_for(entry_time: Optional[str]) -> str:
    """Map an entry_time ('YYYY-MM-DD HH:MM:SS' or 'HH:MM[:SS]') to a canonical slot."""
    if not entry_time:
        return "other"
    hm = entry_time[11:16] if len(entry_time) >= 16 and entry_time[10] == " " else entry_time[:5]
    try:
        h, m = hm.split(":")[:2]
        mins = int(h) * 60 + int(m)
    except (ValueError, IndexError):
        return "other"
    best, best_d = "other", _SLOT_TOL_MIN + 1
    for label, smin in CANONICAL_SLOTS.items():
        d = abs(mins - smin)
        if d < best_d:
            best, best_d = label, d
    return best


# ──────────────────────────────────────────────────────────────────────────────
# Per-entry P&L reconstruction
# ──────────────────────────────────────────────────────────────────────────────

def reconstruct_entry_pnl(call_credit: float, put_credit: float, stops: dict) -> tuple:
    """Reconstruct one entry's realized net P&L from its credits + stop outcomes.

    ``stops`` maps side ('call'/'put') -> {'net_pnl': float|None}.

    Returns ``(pnl, scored)``: ``scored`` is False when a stopped side has no
    recorded net_pnl — then ``pnl`` is None and the caller EXCLUDES the entry from
    P&L means (but still counts it). We deliberately do NOT reconstruct a missing
    debit from spread_snapshots: its value column has ambiguous per-contract-vs-
    total units, and silently mis-scaling a loss would bias a slot far worse than
    honestly dropping it (the report discloses the unscored count per slot).
    """
    pnl = 0.0
    for side, credit in (("call", call_credit or 0.0), ("put", put_credit or 0.0)):
        st = stops.get(side)
        if st is None:
            pnl += credit  # side expired worthless → kept full credit
            continue
        npnl = st.get("net_pnl")
        if npnl is None:
            return None, False
        pnl += float(npnl)
    return round(pnl, _CENT), True


# ──────────────────────────────────────────────────────────────────────────────
# Data access + analysis
# ──────────────────────────────────────────────────────────────────────────────

def _connect_ro(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def analyze_slots(db_path: str, *, min_preliminary: int = 10, min_confident: int = 25) -> dict:
    """Aggregate per-entry reconstructed P&L by slot. Returns a result dict."""
    if not os.path.exists(db_path):
        return {"ok": False, "error": f"no db at {db_path}"}
    con = _connect_ro(db_path)
    try:
        entries = con.execute(
            "SELECT date, entry_number, entry_time, call_credit, put_credit, "
            "total_credit, contracts FROM trade_entries"
        ).fetchall()
        # stops keyed by (date, entry_number) -> {side: {net_pnl, stop_time}}
        stops_by_entry: dict = {}
        for r in con.execute(
            "SELECT date, entry_number, side, net_pnl, stop_time FROM trade_stops"
        ):
            stops_by_entry.setdefault((r["date"], r["entry_number"]), {})[r["side"]] = {
                "net_pnl": r["net_pnl"], "stop_time": r["stop_time"],
            }

        slots: dict = {s: {"pnls": [], "credits": [], "n": 0, "unscored": 0,
                           "stopped": 0} for s in _SLOT_ORDER}
        scored_total = 0.0
        for e in entries:
            key = (e["date"], e["entry_number"])
            stops = stops_by_entry.get(key, {})
            slabel = slot_for(e["entry_time"])
            s = slots[slabel]
            s["n"] += 1
            s["credits"].append(e["total_credit"] or 0.0)
            if stops:
                s["stopped"] += 1
            pnl, scored = reconstruct_entry_pnl(e["call_credit"], e["put_credit"], stops)
            if scored:
                s["pnls"].append(pnl)
                scored_total += pnl
            else:
                s["unscored"] += 1

        # daily_summaries cross-check
        ds_total = con.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM daily_summaries").fetchone()[0]
        n_days = con.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()[0]
    finally:
        con.close()

    rows = []
    for label in _SLOT_ORDER:
        s = slots[label]
        if s["n"] == 0:
            continue
        pnls = s["pnls"]
        ci = _mean_ci(pnls)
        n_scored = len(pnls)
        wins = sum(1 for p in pnls if p > _SCRATCH_EPS)
        avg_pnl = (sum(pnls) / n_scored) if n_scored else None
        rows.append({
            "slot": label,
            "n": s["n"],
            "n_scored": n_scored,
            "unscored": s["unscored"],
            "stop_rate": s["stopped"] / s["n"],
            "avg_credit": sum(s["credits"]) / s["n"] if s["n"] else None,
            "win_rate": (wins / n_scored) if n_scored else None,
            "avg_pnl": avg_pnl,
            "total_pnl": sum(pnls) if pnls else 0.0,
            "ci": ci,
            "verdict": _slot_verdict(n_scored, ci, min_preliminary, min_confident),
        })
    rows.sort(key=lambda r: (r["avg_pnl"] is not None, r["avg_pnl"] or 0.0), reverse=True)
    return {
        "ok": True,
        "db_path": db_path,
        "n_entries": sum(r["n"] for r in rows),
        "scored_total": round(scored_total, _CENT),
        "daily_summaries_total": round(ds_total or 0.0, _CENT),
        "n_days": n_days,
        "slots": rows,
        "min_preliminary": min_preliminary,
        "min_confident": min_confident,
    }


def _slot_verdict(n_scored: int, ci: Optional[list],
                  min_preliminary: int, min_confident: int) -> str:
    if n_scored < min_preliminary:
        return "INSUFFICIENT"
    if ci is None:
        return "INSUFFICIENT"
    _, lo, hi = ci
    tag = "" if n_scored >= min_confident else " (preliminary)"
    if lo > 0:
        return "POSITIVE" + tag
    if hi < 0:
        return "NEGATIVE" + tag
    return "INCONCLUSIVE" + tag


# ──────────────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────────────

def _d(v: Optional[float]) -> str:
    return "    n/a" if v is None else f"{v:+8.0f}"


def format_slot_report(result: dict, title: str = "Variant B — per-slot edge") -> str:
    if not result.get("ok"):
        return f"{title}\n  ERROR: {result.get('error')}"
    L = [title, "=" * len(title)]
    L.append(f"{result['n_entries']} entries over {result['n_days']} days.  "
             f"Reconstructed total P&L ${result['scored_total']:,.0f} vs "
             f"daily_summaries ${result['daily_summaries_total']:,.0f}.")
    drift = result["scored_total"] - result["daily_summaries_total"]
    if abs(drift) > 0.05 * max(1.0, abs(result["daily_summaries_total"])):
        L.append(f"  ⚠ reconstruction drifts ${drift:,.0f} from the daily totals — "
                 f"treat absolute P&L as approximate; the RANKING is robust.")
    L.append("")
    L.append("slot        n  scored  stop%   win%   avgCred   avgP&L/entry   95% CI (P&L/entry)        verdict")
    for r in result["slots"]:
        ci = r["ci"]
        ci_s = "—" if ci is None else f"[{ci[1]:+7.0f}, {ci[2]:+7.0f}]"
        unsc = f" (+{r['unscored']} unscored)" if r["unscored"] else ""
        L.append(
            f"{r['slot']:<9} {r['n']:3d}  {r['n_scored']:5d}  "
            f"{100*r['stop_rate']:4.0f}%  "
            f"{(100*r['win_rate']) if r['win_rate'] is not None else 0:4.0f}%  "
            f"{(r['avg_credit'] or 0):8.0f}  {_d(r['avg_pnl'])}      "
            f"{ci_s:<24} {r['verdict']}{unsc}"
        )
    L.append("")
    L.append("Verdict gates: POSITIVE/NEGATIVE = 95% t-CI on mean P&L/entry clears zero; "
             "INCONCLUSIVE = straddles zero; INSUFFICIENT = < min samples. "
             "P&L is account dollars (contract-scaled); CI assumes independent entries.")
    return "\n".join(L)


def main(argv: Optional[list] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Per-slot edge analyzer for IC variants A/B/C.")
    p.add_argument("--variant", default="b", help="variant id (a/b/c) → data/variant_<id>/backtesting.db; 'a' → data/backtesting.db")
    p.add_argument("--db", default=None, help="explicit backtesting.db path (overrides --variant)")
    p.add_argument("--root", default=".", help="repo root (for the default db path)")
    p.add_argument("--min-preliminary", type=int, default=10)
    p.add_argument("--min-confident", type=int, default=25)
    a = p.parse_args(argv)
    if a.db:
        db = a.db
    elif a.variant == "a":
        db = os.path.join(a.root, "data", "backtesting.db")
    else:
        db = os.path.join(a.root, "data", f"variant_{a.variant}", "backtesting.db")
    res = analyze_slots(db, min_preliminary=a.min_preliminary, min_confident=a.min_confident)
    print(format_slot_report(res, title=f"Variant {a.variant.upper()} — per-slot edge"))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
