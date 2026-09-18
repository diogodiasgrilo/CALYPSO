/** The IC entry MODEL — types plus the side resolver, with no components.
 *
 * Split out of icEntryView.tsx (2026-09-18) for
 * react-refresh/only-export-components: a module exporting BOTH components and
 * plain functions breaks Fast Refresh, because the bundler cannot tell whether
 * an edit should re-render or reload the page.
 *
 * Nothing here changed — the type, the interface and the function are moved
 * verbatim. `resolveSide` is the single place that decides what a side's state
 * IS (open / stopped / expired / skipped) and what its numbers mean, so keeping
 * it away from the rendering is the right split regardless of the lint rule.
 */

import type { ICSnapshotEntry } from "../../hooks/useStrategySnapshot";

export type ICEntry = ICSnapshotEntry;

type SideState = "live" | "stopped" | "breach" | "expired" | "skipped" | "absent";

function fmtClock(iso?: string): string | undefined {
  if (!iso || iso.length < 16) return undefined;
  return iso.slice(11, 16);
}

export interface SideInfo {
  state: SideState;
  shortStrike?: number;
  longStrike?: number;
  credit?: number;
  cushionPct: number | null;
  cost: number | null;
  stop?: number;
  distancePt: number | null;
  unrealized: number | null;
  realized: number | null;
  closeTime?: string;
}

export function resolveSide(entry: ICEntry, side: "call" | "put", spx?: number): SideInfo {
  const short = side === "call" ? entry.short_call_strike : entry.short_put_strike;
  const long = side === "call" ? entry.long_call_strike : entry.long_put_strike;
  const credit = side === "call" ? entry.call_spread_credit : entry.put_spread_credit;
  const staticStop = side === "call" ? entry.call_side_stop : entry.put_side_stop;
  const effStop = (side === "call" ? entry.effective_call_stop : entry.effective_put_stop) ?? staticStop;
  const stopped = side === "call" ? entry.call_side_stopped : entry.put_side_stopped;
  const expired = side === "call" ? entry.call_side_expired : entry.put_side_expired;
  const skipped = side === "call" ? entry.call_side_skipped : entry.put_side_skipped;
  const pivot = side === "call" ? entry.call_side_pivot_closed : entry.put_side_pivot_closed;
  const debit = side === "call" ? entry.actual_call_stop_debit : entry.actual_put_stop_debit;
  const stopTime = side === "call" ? entry.call_stop_time : entry.put_stop_time;
  const cost = (side === "call" ? entry.buffer?.call_value : entry.buffer?.put_value) ?? null;
  const backendPct = (side === "call" ? entry.buffer?.call_pct : entry.buffer?.put_pct) ?? null;

  if (!short || short <= 0) {
    return { state: "absent", cushionPct: null, cost: null, distancePt: null, unrealized: null, realized: null };
  }

  let cushionPct = backendPct;
  if (effStop && effStop > 0 && cost != null) {
    cushionPct = Math.round(Math.max(0, Math.min(100, ((effStop - cost) / effStop) * 100)) * 10) / 10;
  }
  const distancePt = spx && spx > 0 ? (side === "call" ? short - spx : spx - short) : null;
  const unrealized = cost != null && credit != null ? credit - cost : null;

  const haveDebit = debit != null && debit > 0;
  const closedPnl = haveDebit ? (credit ?? 0) - (debit ?? 0) : null;
  const tp = entry.close_reason === "TP";

  let state: SideState = "live";
  let realized: number | null = null;
  let closeTime: string | undefined;
  if (skipped) {
    state = "skipped";
  } else if (tp && (stopped || expired)) {
    state = "expired";
    realized = closedPnl;
    closeTime = fmtClock(stopTime);
  } else if (pivot) {
    state = "breach";
    realized = closedPnl;
    closeTime = fmtClock(stopTime);
  } else if (stopped) {
    state = "stopped";
    realized = closedPnl;
    closeTime = fmtClock(stopTime);
  } else if (expired) {
    state = "expired";
    realized = credit ?? null;
  }

  const live = state === "live";
  return {
    state, shortStrike: short, longStrike: long, credit,
    cushionPct: live ? cushionPct : null,
    cost, stop: effStop,
    distancePt: live ? distancePt : null,
    unrealized: live ? unrealized : null,
    realized, closeTime,
  };
}
