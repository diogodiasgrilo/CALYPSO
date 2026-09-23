#!/usr/bin/env python3
"""
The counterfactual for skipped entries: would the trades we DIDN'T take have won?

Primarily this answers the long-open GEX question — *the strike adjuster vetoed
more entries than it placed; was that veto worth it?* — but it works for any skip
reason that recorded its strikes.

PERSISTING THE RESULT (--apply)
------------------------------
`DataRecorder.update_skipped_entry_backtest` writes `would_have_stopped` /
`theoretical_pnl` back onto the row, and until now it had ZERO callers repo-wide
— so those columns were structurally empty (198 rows, 0 populated) and any
outcome computed here lived only in stdout. `--apply` gives it its caller.

It is given that caller from HERE, not from the bot's settlement path,
deliberately: the computation is not time-sensitive (both inputs are persisted),
so putting it in the trading process would add risk for no benefit. DRY-RUN BY
DEFAULT, and `--apply` backs the database up first, matching
scripts/backfill_overlay_residual.py.

⚠️ IT WRITES A MODELLED NUMBER. `theoretical_pnl` is not measured — see below.
Anything reading that column must treat it as an estimate on a stated stop model,
which is why `--pct-of-width` and `--contracts` are recorded in the run output.

WHY THIS IS AN OFFLINE SCRIPT, NOT A SETTLEMENT HOOK
----------------------------------------------------
Both inputs are already persisted: the proposed strikes (from 2026-09-11) and the
full intraday `market_ticks` series. So the outcome can be recomputed at ANY
time, retroactively, and re-run whenever the loss model changes. Putting it in
the bot's settlement path would add risk to the trading process to compute
something that was never time-sensitive. `DataRecorder.update_skipped_entry_backtest`
exists for writing the result back and remains available, but nothing here
requires the bot to be involved.

TWO SCORING DEFECTS, FIXED 2026-09-23 — read this before comparing to an old run
--------------------------------------------------------------------------------
Both changed the numbers this script reports, and the second changed what
``--apply`` WRITES, so a run from before that date is not comparable.

1. **Attribution.** The GEX adjuster vetoes **one side**; this scored the **whole
   entry** and filtered on nothing but ``skip_reason LIKE '%GEX%'``. A veto got
   credit for whatever the entry avoided, regardless of which side it objected
   to. On 2026-09-21 the adjuster vetoed the **put**, the put was **never
   breached**, and the entry was saved by the **call** breach — which
   require-both-sides caught, not GEX. Two wrong calls scored as one win. Now
   joined to ``gex_decisions`` (``gex_vetoed_sides``) and scored per side
   (``score_gex_veto``).
2. **Arithmetic.** A one-sided breach was modelled as a full-entry loss,
   discarding the surviving side's kept credit and pricing the loss at the
   CALL's width even when the PUT breached. See ``_side_pnl``.

WHAT IS MEASURED vs WHAT IS MODELLED — read this before quoting a number
-----------------------------------------------------------------------
MEASURED (hard facts from market_ticks):
  * whether SPX BREACHED a proposed short strike after the skip time.
    Call side breached if max(SPX after skip) >= short_call.
    Put  side breached if min(SPX after skip) <= short_put.
MODELLED (an assumption, and the weakest link):
  * the dollar outcome. Unbreached -> we keep the estimated credit, which is
    close to measured. Breached -> we model the loss as B's ACTING stop, i.e.
    `pct_of_width x width x 100 x contracts` (A2, default 40%). That is a MODEL:
    the real loss depends on when the stop fired and what the spread cost to
    close, neither of which exists for a trade that never happened.

So treat the BREACH RATE as evidence and the DOLLARS as an estimate. A breach is
also not automatically a loss — SPX can touch a short strike and settle back
inside — which is why both are reported separately rather than collapsed.

THE HISTORICAL SET IS NOT RECOVERABLE. Strikes were only recorded from
2026-09-11; variant B's 95 earlier live-era GEX vetoes have none and never will.
This measures forward only, and says so rather than quietly reporting a small n
as if it were the whole history.

USAGE (on the VM, as calypso)
  .venv/bin/python -m scripts.analyze_skipped_entry_outcomes --variant b
  .venv/bin/python -m scripts.analyze_skipped_entry_outcomes --variant b --reason GEX

The GEX run also prints an ATTRIBUTION block: of the vetoes traceable to a
``gex_decisions`` row, how many objected to a side that actually breached. A
veto marked ``wrong`` whose entry still avoided a loss was saved by something
else — that distinction is the whole reason the join exists.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.analysis_eras import LIVE_ERA_SINCE, era_banner  # noqa: E402

#: Strikes were only recorded from this date (commit ec71967). Rows before it
#: carry NULL strikes and are structurally unmeasurable — not "missing data" that
#: might turn up later.
STRIKES_RECORDED_SINCE = "2026-09-11"


def _rows(db, since, reason):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = ("SELECT date, entry_number, skip_time, skip_reason, spx_at_skip, "
         "theoretical_short_call, theoretical_long_call, "
         "theoretical_short_put, theoretical_long_put, "
         "estimated_call_credit, estimated_put_credit "
         "FROM skipped_entries WHERE date >= ?")
    args = [since]
    if reason:
        q += " AND skip_reason LIKE ?"
        args.append(f"%{reason}%")
    try:
        return [dict(r) for r in con.execute(q + " ORDER BY date, entry_number", args)]
    finally:
        con.close()


def _spx_extremes_after(db, date_str, after_time):
    """(min, max) SPX from `after_time` to the end of that day, or (None, None).

    Bounded to AFTER the skip: what SPX did earlier in the session cannot breach
    a position that would have been opened later.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        r = con.execute(
            "SELECT MIN(spx_price), MAX(spx_price) FROM market_ticks "
            "WHERE date(timestamp) = ? AND timestamp >= ? AND spx_price > 0",
            (date_str, after_time or f"{date_str} 00:00:00"),
        ).fetchone()
        return (r[0], r[1]) if r else (None, None)
    finally:
        con.close()


def gex_vetoed_sides(db, variant):
    """``{(date, entry_number): {"call"|"put", ...}}`` — the sides the GEX
    strike-adjuster actually vetoed, from ``gex_decisions``.

    WHY THIS JOIN EXISTS (found 2026-09-23). The adjuster vetoes **one side**;
    this analyzer scored **the whole entry** and filtered on nothing but
    ``skip_reason LIKE '%GEX%'``. So a veto got credit for whatever the entry
    avoided, regardless of which side it objected to.

    On 2026-09-21 that produced a verdict that was wrong twice over: the
    adjuster vetoed the **put**, the put was **never breached**, and the entry
    was saved by the **call** breach — which require-both-sides caught, not GEX.
    Two incorrect calls were scored as one win.

    Returns ``{}`` when the table is absent or empty (pre-schema-v16 databases),
    which the caller must treat as "attribution unavailable" and say so, rather
    than as "no side was vetoed".
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = {}
        for r in con.execute(
            "SELECT date, entry_number, side FROM gex_decisions "
            "WHERE consumer = 'adjuster' AND UPPER(COALESCE(live_action,'')) = 'SKIP' "
            "AND (variant IS NULL OR LOWER(variant) = ?)",
            (str(variant).lower(),),
        ):
            side = (r[2] or "").strip().lower()
            if side in ("call", "put") and r[1] is not None:
                out.setdefault((r[0], r[1]), set()).add(side)
        return out
    except sqlite3.Error:
        return {}               # no gex_decisions table — attribution unavailable
    finally:
        con.close()


def score_gex_veto(outcome, vetoed_sides):
    """Was the veto right ON ITS OWN TERMS — did the side IT objected to breach?

    Deliberately separate from the entry's P&L. An entry can be saved while the
    veto that is credited with saving it was wrong about which side was
    dangerous, and collapsing the two hides exactly that.

    Returns one of ``"correct"`` (a vetoed side breached), ``"wrong"`` (no
    vetoed side breached), or ``"unattributed"`` (no gex_decisions row).
    """
    if not vetoed_sides:
        return "unattributed"
    breached = {s for s in ("call", "put")
                if outcome.get(f"{s}_breach")}
    return "correct" if (vetoed_sides & breached) else "wrong"


def _side_pnl(breached, credit_ps, width, *, pct_of_width, contracts):
    """One side's modelled dollars, or None when it breached with no width.

    THE SIDES ARE INDEPENDENT, and modelling them together was a real defect
    (found 2026-09-23). An iron condor whose CALL stops out does not forfeit the
    PUT: the put rides to expiry and keeps its credit. The previous version
    charged a full stop for ANY breach and discarded BOTH sides' credit, which
    overstates the loss on every one-sided breach — the common case, since SPX
    rarely breaches both sides of the same condor in one session.

    Returns None rather than 0.0 for "breached but no width recorded": a stop
    whose size is unknown is not a stop worth zero, and the caller must drop the
    row instead of persisting a number it invented.
    """
    if breached:
        return -(pct_of_width * width * 100 * contracts) if width else None
    return (credit_ps or 0.0) * 100 * contracts


def evaluate(row, lo, hi, *, pct_of_width, contracts):
    """Outcome for one skipped entry. Returns None when unmeasurable.

    Each side is modelled on ITS OWN strikes, width and credit — see
    ``_side_pnl`` for why modelling them jointly was wrong.
    """
    sc, sp = row.get("theoretical_short_call"), row.get("theoretical_short_put")
    if not sc and not sp:
        return None            # strikes never recorded — structurally unmeasurable
    if lo is None or hi is None:
        return None            # no tick coverage after the skip

    call_breach = bool(sc) and hi is not None and hi >= sc
    put_breach = bool(sp) and lo is not None and lo <= sp

    # Per-side width. The old code took the CALL width and fell back to the put
    # only when no call existed — so a put-side breach on an asymmetric condor
    # was priced at the call's width (MKT-028 exists precisely to allow 60/75pt
    # asymmetry, so this is not hypothetical).
    call_width = (abs(row["theoretical_long_call"] - sc)
                  if sc and row.get("theoretical_long_call") else 0.0)
    put_width = (abs(sp - row["theoretical_long_put"])
                 if sp and row.get("theoretical_long_put") else 0.0)

    call_pnl = (_side_pnl(call_breach, row.get("estimated_call_credit"), call_width,
                          pct_of_width=pct_of_width, contracts=contracts)
                if sc else 0.0)
    put_pnl = (_side_pnl(put_breach, row.get("estimated_put_credit"), put_width,
                         pct_of_width=pct_of_width, contracts=contracts)
               if sp else 0.0)

    # A side that breached with no recorded width makes the ENTRY unmodellable —
    # better to report it as such than to persist a partial figure as a total.
    modelled = None if (call_pnl is None or put_pnl is None) else call_pnl + put_pnl

    credit = ((row.get("estimated_call_credit") or 0.0)
              + (row.get("estimated_put_credit") or 0.0)) * 100 * contracts

    return {
        "date": row["date"], "entry": row["entry_number"],
        "short_call": sc, "short_put": sp,
        "width": max(call_width, put_width),     # display only
        "call_width": call_width, "put_width": put_width,
        "spx_lo": lo, "spx_hi": hi,
        "call_breach": call_breach, "put_breach": put_breach,
        "breached": call_breach or put_breach,
        "call_pnl": call_pnl, "put_pnl": put_pnl,
        "credit": credit, "modelled_pnl": modelled,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Counterfactual outcomes for skipped entries.")
    p.add_argument("--variant", default="b")
    p.add_argument("--db", default=None)
    p.add_argument("--root", default=".")
    p.add_argument("--reason", default=None, help="substring filter, e.g. 'GEX'")
    p.add_argument("--since", default=LIVE_ERA_SINCE)
    p.add_argument("--pct-of-width", type=float, default=0.40,
                   help="B's acting A2 stop fraction (loss MODEL)")
    p.add_argument("--contracts", type=int, default=7)
    p.add_argument("--apply", action="store_true",
                   help="WRITE would_have_stopped/theoretical_pnl back to the DB "
                        "(default is a dry run). Backs the DB up first.")
    a = p.parse_args(argv)

    db = a.db or (os.path.join(a.root, "data", "backtesting.db") if a.variant == "a"
                  else os.path.join(a.root, "data", f"variant_{a.variant}", "backtesting.db"))
    if not os.path.exists(db):
        print(f"ERROR: no db at {db}", file=sys.stderr)
        return 2

    since = None if str(a.since).lower() in ("all", "none") else a.since
    print(era_banner(since))
    rows = _rows(db, since or "0000-00-00", a.reason)
    print(f"\nskipped entries matched: {len(rows)}"
          + (f"  (reason ~ {a.reason!r})" if a.reason else ""))

    veto_map = gex_vetoed_sides(db, a.variant)

    measurable, unmeasurable = [], 0
    for r in rows:
        lo, hi = _spx_extremes_after(db, r["date"], r.get("skip_time"))
        res = evaluate(r, lo, hi, pct_of_width=a.pct_of_width, contracts=a.contracts)
        if res is None:
            unmeasurable += 1
        else:
            sides = veto_map.get((r["date"], r["entry_number"]), set())
            res["vetoed_sides"] = sides
            res["veto_verdict"] = score_gex_veto(res, sides)
            measurable.append(res)

    print(f"  measurable (strikes recorded + tick coverage): {len(measurable)}")
    print(f"  UNMEASURABLE (no strikes recorded)           : {unmeasurable}")
    if unmeasurable:
        print(f"  → strikes are only recorded from {STRIKES_RECORDED_SINCE}; earlier")
        print(f"    rows carry NULL strikes and can never be measured.")

    if not measurable:
        print("\nNo measurable skips yet. This measures FORWARD only — come back "
              "after a few sessions.")
        return 0

    print(f"\n  {'date':12} {'e#':>3} {'SC':>7} {'SP':>7} {'SPX lo/hi':>17} "
          f"{'breach':>8} {'vetoed':>7} {'verdict':>12} {'modelled $':>11}")
    for m in measurable:
        br = ("CALL" if m["call_breach"] else "") + ("PUT" if m["put_breach"] else "")
        pnl_s = "—" if m["modelled_pnl"] is None else f"{m['modelled_pnl']:,.0f}"
        rng = f"{m['spx_lo']:.1f}/{m['spx_hi']:.1f}"
        vs = ",".join(sorted(m.get("vetoed_sides") or [])) or "-"
        print(f"  {m['date']:12} {m['entry']:>3} {str(m['short_call'] or '-'):>7} "
              f"{str(m['short_put'] or '-'):>7} {rng:>17} "
              f"{br or '-':>8} {vs:>7} {m.get('veto_verdict','-'):>12} {pnl_s:>11}")

    n = len(measurable)
    br = sum(1 for m in measurable if m["breached"])
    pnl = [m["modelled_pnl"] for m in measurable if m["modelled_pnl"] is not None]
    print("\n" + "=" * 66)
    print(f"  MEASURED : {br}/{n} vetoed entries would have had a short breached "
          f"({br/n*100:.0f}%)")
    if pnl:
        print(f"  MODELLED : net ${sum(pnl):,.2f} over {len(pnl)} entries "
              f"(${sum(pnl)/len(pnl):,.2f}/entry)")
        print(f"             positive => vetoing COST us; negative => vetoing SAVED us")
    # ATTRIBUTION — separate from the entry's P&L on purpose. An entry can be
    # saved while the veto credited with saving it was wrong about WHICH side
    # was dangerous; collapsing the two hides exactly that (2026-09-21).
    verdicts = [m.get("veto_verdict") for m in measurable]
    n_attr = sum(1 for v in verdicts if v in ("correct", "wrong"))
    if n_attr:
        right = sum(1 for v in verdicts if v == "correct")
        print(f"\n  ATTRIBUTION: of {n_attr} veto(es) traceable to a gex_decisions "
              f"row, {right} objected to a side that")
        print(f"               ACTUALLY breached and {n_attr - right} did not. A "
              f"'wrong' veto whose entry still")
        print(f"               avoided a loss was saved by something ELSE — "
              f"require-both-sides, not GEX.")
    unattr = sum(1 for v in verdicts if v == "unattributed")
    if unattr:
        print(f"\n  ⚠️  {unattr} of {n} row(s) have NO gex_decisions match — their "
              f"veto cannot be attributed")
        print(f"      to a side, so they are counted in the dollars but NOT in the "
              f"verdict above.")

    print(f"\n  The breach rate is MEASURED. The dollars are MODELLED on a "
          f"{a.pct_of_width:.0%}-of-width")
    print(f"  stop at {a.contracts}c — the real loss depends on when the stop fired and "
          f"what the")
    print(f"  spread cost to close, neither of which exists for a trade never taken.")
    print(f"  A breach is also not automatically a loss: SPX can touch a short and "
          f"settle back inside.")
    if n < 20:
        print(f"\n  ⚠️  n={n}. Directional at best. Do not act on this yet.")

    writable = [m for m in measurable if m["modelled_pnl"] is not None]
    print(f"\n  {len(writable)} of {n} row(s) have a modellable outcome to persist.")
    if not a.apply:
        print("  DRY RUN — nothing written. Re-run with --apply.")
        return 0
    if not writable:
        print("  Nothing to write.")
        return 0

    from shared.db_backup import safe_db_backup
    backup = safe_db_backup(db, "pre_skip_backfill")
    print(f"  backup: {backup}")

    from shared.data_recorder import DataRecorder
    rec = DataRecorder(db)
    ok = 0
    for m in writable:
        if rec.update_skipped_entry_backtest(
            m["date"], m["entry"], bool(m["breached"]), float(m["modelled_pnl"])
        ):
            ok += 1
    print(f"  wrote {ok}/{len(writable)} row(s) "
          f"(model: {a.pct_of_width:.0%}-of-width stop at {a.contracts}c).")
    if ok != len(writable):
        print(f"  ⚠️  {len(writable)-ok} write(s) FAILED — DataRecorder swallows write")
        print(f"      errors by design so the trading loop is never affected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
