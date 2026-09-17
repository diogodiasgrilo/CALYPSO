/**
 * Pure risk maths for an undefined-risk (naked) strategy.
 *
 * Separate from the card component so these can be unit-tested against known
 * answers, and because a file that exports both a component and helpers breaks
 * react-refresh.
 *
 * `boundedLoss:false` strategies cannot show a max loss — there isn't one. What
 * CAN be quantified is how far the market is from the strike that starts losing
 * money, which is the figure that actually characterises the exposure.
 */

import type { HydraEntry } from "../store/hydraStore";

const TRADING_DAYS = 252;

/**
 * One-sigma expected move for the session: `spot x (VIX/100) / sqrt(252)`.
 * Null without both inputs — a fabricated move would make every distance below
 * meaningless, which is the error this whole card exists to avoid.
 */
export function expectedDailyMove(
  spot?: number | null,
  vix?: number | null,
): number | null {
  if (!spot || !vix || spot <= 0 || vix <= 0) return null;
  return (spot * (vix / 100)) / Math.sqrt(TRADING_DAYS);
}

/** A side still exposed — not stopped, expired or skipped. */
function sideIsOpen(e: HydraEntry, side: "call" | "put"): boolean {
  if (!e.entry_time) return false;
  const done =
    side === "call"
      ? e.call_side_stopped || e.call_side_expired || e.call_side_skipped
      : e.put_side_stopped || e.put_side_expired || e.put_side_skipped;
  return !done;
}

export interface NearestShort {
  /** Points from spot to the strike. NEGATIVE means spot is already through it. */
  points: number;
  strike: number;
  side: "call" | "put";
}

/**
 * Distance from spot to the NEAREST open naked short.
 *
 * Null when nothing is open — a flat strategy has no distance-to-breach, and
 * inventing one would be the same class of error as printing a max loss.
 */
export function nearestShortDistance(
  entries: HydraEntry[],
  spot?: number | null,
): NearestShort | null {
  if (!spot || spot <= 0) return null;
  let best: NearestShort | null = null;
  for (const e of entries) {
    for (const side of ["call", "put"] as const) {
      if (!sideIsOpen(e, side)) continue;
      const strike = side === "call" ? e.short_call_strike : e.short_put_strike;
      if (!strike || strike <= 0) continue;
      // Signed toward the short: a call is breached from below, a put from above.
      const points = side === "call" ? strike - spot : spot - strike;
      if (!best || points < best.points) best = { points, strike, side };
    }
  }
  return best;
}
