"""Calendar-strategy (D, E) EDGE reader — the "read its edge off the dry-run record" tool.

Strategy D/E run dry-run-LOCKED. The MVL-D audit
(`docs/migration/D_GOLIVE_SCOPE_AND_AUDIT.md`) returned NO-GO on a live flip and
named the one cheap, gating question to answer FIRST, from the forward dry-run
record alone (its "V1 — edge sanity"):

    Is a debit double calendar + 20%-debit stop NON-NEGATIVE EV over a
    meaningful window?  (No transform claim to validate — the transform's
    "risk-free" edge is a mid-pricing artifact the audit distrusts.)

This module answers exactly that from a calendar variant's isolated
``dc_calendar.db`` (the ``dc_outcomes`` terminal-P&L table), with properties the
audit makes load-bearing:

1.  **Transform segmentation.** Outcomes that TRANSFORMED are reported in a
    separate, loudly-caveated bucket and are EXCLUDED from the verdict — their
    dry-run P&L is computed from mids and would not survive real fills (audit
    §0.3). Detection keys on the per-outcome ``dc_outcomes.transform_credit``
    (always > 0 for a real transform, which only fires when the locked-in credit
    clears the debit+wing threshold). We deliberately do NOT join
    ``dc_transformations``: its ``date`` is the TRANSFORM day, not the entry day,
    and ``entry_number`` resets daily, so a date-keyed join both misses real
    multi-day transforms and can false-match a *different* day's trade (audit F1).
2.  **Commission-net.** ``realized_pnl`` is booked GROSS (= ``calendar_value −
    net_debit``); the ~4-leg round-trip commission is tracked separately and is
    NOT in ``realized_pnl``. The verdict therefore gates on a commission-NET
    series: ``realized_pnl − 2 × close_commission`` (the recorded close side, ×2
    for the symmetric, unrecorded 4-leg open). Gross is reported alongside.
3.  **Scale-free metric.** The verdict is computed on per-trade RETURN-ON-DEBIT
    (net_pnl / net_debit), so a change in contract size or debit does not distort
    the edge estimate.
4.  **Sample-size honesty.** Below ``min_preliminary`` trustworthy trades the
    verdict is INSUFFICIENT_DATA. A "confident" verdict requires
    ``min_confident`` trades AND a one-sample **t** CI (not z) that clears zero —
    and the report discloses that the CI assumes independent trades (overlapping
    multi-day calendars violate this, so a marginal positive is treated cautiously).

Pure stdlib (sqlite3 + statistics) so it is unit-testable and importable without
the broker stack — same contract as ``dc_status.py``. Read-only (``mode=ro``).
"""

from __future__ import annotations

import os
import sqlite3
from statistics import mean, stdev
from typing import Optional, Tuple

# 95% two-sided Student-t critical values by df. Using t (not the normal z) keeps
# the CI honest at the n>=min_confident gate boundary, where z is ~4% too narrow
# (audit F4). df 1..30 exact, then checkpoints; >120 -> z. For an untabulated df
# we use the largest tabulated df <= ours (slightly WIDER = conservative).
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042, 40: 2.021, 50: 2.009, 60: 2.000, 80: 1.990, 100: 1.984,
    120: 1.980,
}
_Z95 = 1.960  # df -> inf


def _t_crit(df: int) -> float:
    """95% two-sided t critical value for df, conservative for untabulated df."""
    if df <= 0:
        return float("inf")
    if df in _T95:
        return _T95[df]
    if df > 120:
        return _Z95
    lower = max(k for k in _T95 if k <= df)  # largest tabulated df below ours
    return _T95[lower]


# Sub-cent realized_pnl is float noise from the sim (e.g. -229.999999999999);
# round every P&L to cents before classifying / aggregating.
_CENT = 2
_SCRATCH_EPS = 0.005  # |pnl| below this (after rounding) is a scratch, not win/loss


# ──────────────────────────────────────────────────────────────────────────────
# Data access
# ──────────────────────────────────────────────────────────────────────────────

def _load_outcomes(db_path: str) -> Tuple[list, str]:
    """Read dc_outcomes joined to its entry (for contracts).

    Returns (rows, status) where status ∈ {"ok", "not_found", "unreadable"} so the
    caller can distinguish "DB present but no trades" from "wrong/missing file" —
    an operator pointed at the wrong path must not read it as "D hasn't traded"
    (audit F9). rows are ordered by (close_date, entry_number) — the realized
    sequence, so drawdown is computed in the order trades actually closed.

    NOTE: dc_transformations is intentionally NOT joined (see module docstring,
    point 1) — transform detection is per-outcome via transform_credit.
    """
    if not db_path or not os.path.exists(db_path):
        return [], "not_found"
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return [], "unreadable"
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            """
            SELECT o.entry_date, o.close_date, o.entry_number, o.terminal_state,
                   o.realized_pnl, o.net_debit, o.transform_credit, o.close_commission,
                   e.contracts AS contracts, e.net_debit AS entry_net_debit
            FROM dc_outcomes o
            LEFT JOIN dc_calendar_entries e
                   ON e.date = o.entry_date AND e.entry_number = o.entry_number
            ORDER BY o.close_date, o.entry_number
            """
        )
        return [dict(r) for r in cur.fetchall()], "ok"
    except sqlite3.Error:
        return [], "unreadable"
    finally:
        con.close()


def _is_transformed(row: dict) -> bool:
    """True iff this outcome transformed (→ untrusted, mid-priced 'risk-free').

    Keys on the per-outcome ``transform_credit`` (the recorder writes
    ``entry.transform_credit``, which a real transform sets > 0 — the transform
    gate only fires when the locked-in credit clears the debit+wing threshold).
    A non-transformed calendar carries 0.0. We do not use dc_transformations
    (its date semantics make a join unreliable — module docstring point 1).
    """
    tc = row.get("transform_credit")
    try:
        return tc is not None and abs(float(tc)) > _SCRATCH_EPS
    except (TypeError, ValueError):
        return False


def _round_trip_commission(row: dict) -> float:
    """Estimated round-trip commission = 2 × recorded close-side commission.

    ``dc_outcomes.close_commission`` is the close side only (4 legs); the open
    side is the same 4-leg structure and is not separately recorded, so the
    symmetric round trip is 2×. Conservative bias (slightly over-charging a
    settled-leg edge case) is the safe direction for a build/no-build gate.
    """
    try:
        c = float(row.get("close_commission") or 0.0)
    except (TypeError, ValueError):
        c = 0.0
    return 2.0 * abs(c)


# ──────────────────────────────────────────────────────────────────────────────
# Statistics (pure)
# ──────────────────────────────────────────────────────────────────────────────

def _mean_ci(xs: list) -> Optional[list]:
    """95% one-sample Student-t CI on the mean, or None when n < 2.

    Degenerate zero-variance input returns the point [m, m]; callers gate on a
    degenerate CI separately (audit F7) rather than treating it as confidence.
    """
    n = len(xs)
    if n < 2:
        return None
    m = mean(xs)
    sd = stdev(xs)
    if sd == 0.0:
        return [round(m, 6), round(m, 6)]
    half = _t_crit(n - 1) * sd / (n ** 0.5)
    return [round(m - half, 6), round(m + half, 6)]


def _max_drawdown(pnls_in_close_order: list) -> Optional[float]:
    """Peak-to-trough of the cumulative realized-P&L curve (>= 0). None if empty."""
    if not pnls_in_close_order:
        return None
    cum = peak = max_dd = 0.0
    for p in pnls_in_close_order:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return round(max_dd, 2)


def _summarize(rows: list) -> dict:
    """Per-segment stats. All headline metrics are COMMISSION-NET; gross
    companions are reported for transparency. Win/loss is classified on net P&L
    (a gross-positive trade that loses to commission is a real loss)."""
    n = len(rows)
    empty = {
        "n": 0, "total_pnl_net": 0.0, "total_pnl_gross": 0.0, "commission_total": 0.0,
        "ev_dollars_net": None, "ev_dollars_net_ci95": None, "ev_dollars_gross": None,
        "ev_per_contract_net": None,
        "return_on_debit_net_pct": None, "return_on_debit_net_ci95_pct": None,
        "return_on_debit_gross_pct": None,
        "win_rate": None, "wins": 0, "losses": 0, "scratches": 0,
        "avg_win": None, "avg_loss": None, "profit_factor": None,
        "best": None, "worst": None, "max_drawdown": None, "terminal_states": {},
        "_rod_net_pct": [],
    }
    if n == 0:
        return empty

    gross = [round(float(r.get("realized_pnl") or 0.0), _CENT) for r in rows]
    comms = [round(_round_trip_commission(r), _CENT) for r in rows]
    net = [round(g - c, _CENT) for g, c in zip(gross, comms)]

    rod_net_pct = []          # net return on debit, percent (verdict basis)
    rod_gross_pct = []
    per_contract_net = []
    for r, g, nv in zip(rows, gross, net):
        debit = r.get("net_debit")
        if debit is None:
            debit = r.get("entry_net_debit")
        try:
            d = abs(float(debit))
        except (TypeError, ValueError):
            d = 0.0
        if d > _SCRATCH_EPS:
            rod_net_pct.append(100.0 * nv / d)
            rod_gross_pct.append(100.0 * g / d)
        c = r.get("contracts")
        try:
            c = int(c)
        except (TypeError, ValueError):
            c = 0
        if c > 0:
            per_contract_net.append(nv / c)

    wins = [p for p in net if p > _SCRATCH_EPS]
    losses = [p for p in net if p < -_SCRATCH_EPS]
    scratches = n - len(wins) - len(losses)
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else None)

    states: dict = {}
    for r in rows:
        s = r.get("terminal_state") or "unknown"
        states[s] = states.get(s, 0) + 1

    return {
        "n": n,
        "total_pnl_net": round(sum(net), 2),
        "total_pnl_gross": round(sum(gross), 2),
        "commission_total": round(sum(comms), 2),
        "ev_dollars_net": round(mean(net), 2),
        "ev_dollars_net_ci95": _mean_ci(net),
        "ev_dollars_gross": round(mean(gross), 2),
        "ev_per_contract_net": round(mean(per_contract_net), 2) if per_contract_net else None,
        "return_on_debit_net_pct": round(mean(rod_net_pct), 3) if rod_net_pct else None,
        "return_on_debit_net_ci95_pct": _mean_ci(rod_net_pct),
        "return_on_debit_gross_pct": round(mean(rod_gross_pct), 3) if rod_gross_pct else None,
        "win_rate": round(len(wins) / n, 4),
        "wins": len(wins), "losses": len(losses), "scratches": scratches,
        "avg_win": round(mean(wins), 2) if wins else None,
        "avg_loss": round(mean(losses), 2) if losses else None,
        "profit_factor": (round(pf, 3) if pf not in (None, float("inf")) else pf),
        "best": round(max(net), 2), "worst": round(min(net), 2),
        "max_drawdown": _max_drawdown(net),
        "terminal_states": states,
        "_rod_net_pct": rod_net_pct,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Verdict
# ──────────────────────────────────────────────────────────────────────────────

_INDEP_CAVEAT = ("CI assumes independent trades; overlapping multi-day calendars share "
                 "regime/underlying moves, so the true interval is wider — treat a marginal "
                 "positive as preliminary.")


def _verdict(mvl: dict, min_preliminary: int, min_confident: int) -> dict:
    """GO/NO-GO read of the MVL question on the NON-transformed, COMMISSION-NET
    per-trade return-on-debit series. Gates on the size of THAT series
    (`len(rod)`), not the raw row count (audit F3)."""
    rod = mvl.get("_rod_net_pct") or []
    m = len(rod)  # the sample that actually enters the CI
    thresholds = {"min_preliminary": min_preliminary, "min_confident": min_confident}

    if m < min_preliminary:
        if m == 0 and mvl["n"] > 0:
            head = (f"{mvl['n']} closed outcome(s) but none have a usable net debit — "
                    "no return series to read. Keep recording.")
        else:
            head = (f"Insufficient sample: {m} trustworthy (non-transformed) outcome(s) "
                    f"(need ≥{min_preliminary} for a preliminary read, ≥{min_confident} for a "
                    "confident one). Keep D recording in dry-run.")
        return {
            "code": "INSUFFICIENT_DATA", "headline": head,
            "rationale": [
                "An edge cannot be read off this few trades — the estimate is dominated by noise.",
                "No live flip is justified; there is no information loss in staying dry-run (audit §0.4).",
            ],
            "thresholds": thresholds, "sample_used": m,
        }

    pt = mean(rod)
    ci = _mean_ci(rod)
    sign = "POSITIVE" if pt >= 0 else "NEGATIVE"

    if m < min_confident:
        return {
            "code": f"PRELIMINARY_{sign}",
            "headline": (f"Preliminary {sign.lower()} lean: mean net return-on-debit {pt:+.2f}% "
                         f"over {m} trades (CI {ci[0]:+.2f}%…{ci[1]:+.2f}%). Not significant — "
                         f"need ≥{min_confident} trades to confirm."),
            "rationale": [
                f"Point estimate leans {sign.lower()}, but the sample is below the confidence floor.",
                "Treat as a watch-item, not a GO signal. Keep recording.",
            ],
            "thresholds": thresholds, "sample_used": m,
        }

    lo, hi = ci
    if lo == hi:  # zero-variance / degenerate — not real confidence (audit F7)
        return {
            "code": "DEGENERATE_SAMPLE",
            "headline": (f"Degenerate sample: all {m} net returns identical ({pt:+.2f}%), zero "
                         "variance. This is a recorder/feed artifact, not a real edge."),
            "rationale": ["Investigate why every outcome is identical before trusting any verdict."],
            "thresholds": thresholds, "sample_used": m,
        }

    if lo >= 0:
        code = "EDGE_POSITIVE"
        head = (f"Confident NON-NEGATIVE edge: mean net return-on-debit {pt:+.2f}% "
                f"(95% t-CI {lo:+.2f}%…{hi:+.2f}%) over {m} trades — the CI lower bound is ≥ 0.")
        rat = [
            "The MVL question (debit calendar + 20% stop is non-negative EV, net of commission) "
            "is answered YES.",
            "This justifies SCOPING Micro-MVL-D (1c, EOD-day-1 close) — NOT an immediate flip: the "
            "real 4-leg exec path still must be built + tested (audit §0.1) and the coexistence "
            "guards soaked.",
            _INDEP_CAVEAT,
        ]
    elif hi <= 0:
        code = "EDGE_NEGATIVE"
        head = (f"Confident LOSING edge: mean net return-on-debit {pt:+.2f}% "
                f"(95% t-CI {lo:+.2f}%…{hi:+.2f}%) over {m} trades — the CI is entirely below 0.")
        rat = [
            "The MVL question is answered NO — the plain debit calendar + 20% stop loses money "
            "net of commission.",
            "Do NOT build MVL-D. Retire or redesign D rather than spend ~7 eng-weeks on a negative edge.",
        ]
    else:
        code = "INCONCLUSIVE"
        head = (f"No demonstrated edge: mean net return-on-debit {pt:+.2f}% "
                f"(95% t-CI {lo:+.2f}%…{hi:+.2f}%) over {m} trades — the CI straddles 0.")
        rat = [
            "Even with an adequate sample, the edge is statistically indistinguishable from zero.",
            "No GO. Keep gathering data to tighten the CI, or treat D as edgeless.",
            _INDEP_CAVEAT,
        ]
    return {"code": code, "headline": head, "rationale": rat,
            "thresholds": thresholds, "sample_used": m}


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def analyze_calendar_edge(db_path: str, *, min_preliminary: int = 10,
                          min_confident: int = 30) -> dict:
    """Read a calendar variant's dry-run edge from its dc_calendar.db.

    Returns a dict with ``db_status`` ("ok"/"not_found"/"unreadable"),
    ``total_outcomes``, two ``segments`` (``calendar_mvl`` = trustworthy
    non-transformed signal, ``transformed_untrusted`` = excluded mid-priced
    outcomes), and a ``verdict`` computed on the MVL segment only. Never raises.
    """
    rows, status = _load_outcomes(db_path)
    transformed = [r for r in rows if _is_transformed(r)]
    mvl = [r for r in rows if not _is_transformed(r)]

    mvl_summary = _summarize(mvl)
    tr_summary = _summarize(transformed)
    verdict = _verdict(mvl_summary, min_preliminary, min_confident)

    mvl_summary.pop("_rod_net_pct", None)
    tr_summary.pop("_rod_net_pct", None)
    tr_summary["caveat"] = (
        "EXCLUDED from the verdict. Transformed outcomes are priced off MIDS in dry-run; the "
        "'risk-free' conversion does not survive real fills + commissions (audit §0.3)."
    )

    return {
        "db_path": db_path,
        "db_status": status,
        "total_outcomes": len(rows),
        "segments": {"calendar_mvl": mvl_summary, "transformed_untrusted": tr_summary},
        "verdict": verdict,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Text rendering
# ──────────────────────────────────────────────────────────────────────────────

def _m(v: Optional[float], suffix: str = "") -> str:
    return "—" if v is None else f"{v:,.2f}{suffix}"


def _ci(v: Optional[list], suffix: str = "") -> str:
    return "—" if not v else f"[{v[0]:+,.2f}{suffix} … {v[1]:+,.2f}{suffix}]"


def _pf(v) -> str:
    return "∞" if v == float("inf") else _m(v)


def _fmt_segment(name: str, seg: dict) -> list:
    lines = [f"  {name}: n={seg['n']}"]
    if seg["n"] == 0:
        lines.append("    (no outcomes)")
        return lines
    return lines + [
        f"    EV/trade (net)   : ${_m(seg['ev_dollars_net'])}  (gross ${_m(seg['ev_dollars_gross'])})"
        f"   CI95 {_ci(seg['ev_dollars_net_ci95'])}",
        f"    Return on debit  : net {_m(seg['return_on_debit_net_pct'], '%')}  "
        f"(gross {_m(seg['return_on_debit_gross_pct'], '%')})   "
        f"CI95 {_ci(seg['return_on_debit_net_ci95_pct'], '%')}",
        f"    Win rate (net)   : {(seg['win_rate'] * 100):.1f}%  "
        f"({seg['wins']}W / {seg['losses']}L / {seg['scratches']} scratch)",
        f"    Avg win / loss   : ${_m(seg['avg_win'])} / ${_m(seg['avg_loss'])}   PF {_pf(seg['profit_factor'])}",
        f"    Best / worst     : ${_m(seg['best'])} / ${_m(seg['worst'])}   MaxDD ${_m(seg['max_drawdown'])}",
        f"    Total net / comm : ${_m(seg['total_pnl_net'])}  (commission ${_m(seg['commission_total'])})",
        f"    Terminal states  : {seg['terminal_states'] or '—'}",
    ]


def format_edge_report(result: dict, title: str = "Strategy D — DC Time Machine") -> str:
    v = result["verdict"]
    lines = [
        f"=== {title} — dry-run edge read ===",
        f"DB: {result['db_path']}  [{result.get('db_status', 'ok')}]",
    ]
    if result.get("db_status") == "not_found":
        lines.append("  ⚠ DB file not found — this is NOT 'D hasn't traded'; check the path/variant.")
    elif result.get("db_status") == "unreadable":
        lines.append("  ⚠ DB present but unreadable (corrupt/locked) — verdict is not based on real data.")
    lines += [
        f"Total closed outcomes: {result['total_outcomes']}",
        "Metrics are COMMISSION-NET (round-trip ≈ 2× recorded close-side commission); gross shown in ().",
        "",
        f"VERDICT: {v['code']}",
        f"  {v['headline']}",
    ]
    lines += [f"  • {r}" for r in v.get("rationale", [])]
    lines += ["", "Segments:",
              *_fmt_segment("calendar (MVL — trustworthy)", result["segments"]["calendar_mvl"])]
    tr = result["segments"]["transformed_untrusted"]
    if tr["n"] > 0:
        lines += _fmt_segment("transformed (UNTRUSTED — excluded)", tr)
        lines.append(f"    ⚠ {tr.get('caveat', '')}")
    else:
        lines.append("  transformed (UNTRUSTED): n=0  (none transformed yet)")
    return "\n".join(lines)
