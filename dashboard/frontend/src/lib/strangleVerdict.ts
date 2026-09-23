/**
 * The two numbers a long-strangle session reduces to.
 *
 * Extracted from LongStrangle.tsx rather than left inline, for the same reason
 * `undefinedRisk.ts` and `pnlShape.ts` are separate: this is arithmetic that
 * states a RESULT to the operator, so it has to be executable by a test rather
 * than merely grep-able. Both functions below are pure and import nothing.
 *
 * WHY THIS ARITHMETIC IS EASY TO GET WRONG
 * -----------------------------------------
 * Both sides of a strangle can break on the SAME session, and the first version
 * of this page reported whichever it checked first. On 2026-09-23 SPX ran
 * 7695.13 – 7760.02 against a 7760 call / 7720 put: the call was touched by
 * 0.02pt and the put was driven 24.87pt through. The page said "Broke the call
 * side by 0.0pt" — which reads as a near-miss on a day the put finished
 * twenty-five points in the money. The rule is to lead with the side that moved
 * FURTHEST, because that is the side that determined what the position was
 * worth.
 */

export type Side = "call" | "put";

export interface BandVerdict {
  /** Points the session ran BEYOND each strike. Non-positive means untouched. */
  callThrough: number;
  putThrough: number;
  brokeCall: boolean;
  brokePut: boolean;
  /** True if either side was exceeded at any point in the session. */
  broke: boolean;
  /** The LARGEST breach in points; 0 when the session stayed inside the band. */
  widest: number;
  /** The side that moved furthest beyond its strike. */
  widestSide: Side;
  /** The other side, and how far it went — meaningful only when both broke. */
  otherSide: Side;
  otherThrough: number;
  /**
   * Points the nearest side fell SHORT of its strike; 0 once anything broke.
   *
   * A session that ends 1.5pt inside the band and one that ends 60pt inside are
   * the same "total loss" in P&L and completely different evidence about whether
   * the strikes were placed well — which, for a variant whose whole job is to
   * measure, is the more useful of the two facts.
   */
  closestApproach: number;
}

/**
 * Compare a session's high/low against the two strikes.
 *
 * `widestSide` is defined even when nothing broke (it reports whichever side
 * came closer to breaking), so callers never have to handle a null; they gate
 * the sentence on `broke` instead.
 */
export function bandVerdict(
  hi: number,
  lo: number,
  callStrike: number,
  putStrike: number,
): BandVerdict {
  const callThrough = hi - callStrike;
  const putThrough = putStrike - lo;
  const brokeCall = callThrough > 0;
  const brokePut = putThrough > 0;
  const callLeads = callThrough >= putThrough;
  const widestSide: Side = callLeads ? "call" : "put";
  return {
    callThrough,
    putThrough,
    brokeCall,
    brokePut,
    broke: brokeCall || brokePut,
    widest: Math.max(0, callThrough, putThrough),
    widestSide,
    otherSide: callLeads ? "put" : "call",
    otherThrough: callLeads ? putThrough : callThrough,
    closestApproach: Math.max(0, -Math.max(callThrough, putThrough)),
  };
}

export interface StrangleValuation {
  /** Dollar value of both legs' intrinsic value at `spx`. */
  intrinsic: number;
  /** Intrinsic minus the debit paid. */
  pnl: number;
  /** `pnl` as a percentage of the debit. `null` when no debit is known. */
  pctOfDebit: number | null;
  /**
   * Whether `intrinsic` is the FINAL value or only a floor.
   *
   * SPXW is cash-settled at the close, so after the close intrinsic IS the
   * settlement. Before it, the position is still worth intrinsic PLUS whatever
   * time value remains — so a mid-session figure is a lower bound and the page
   * must not call it a settlement. This flag is what keeps that sentence honest
   * while the market is open.
   */
  settled: boolean;
}

/** Value both legs of a strangle at a given SPX level. */
export function valueStrangleAt(
  spx: number,
  callStrike: number,
  putStrike: number,
  debit: number | null,
  settled: boolean,
  contractMultiplier = 100,
): StrangleValuation {
  const intrinsic =
    (Math.max(0, spx - callStrike) + Math.max(0, putStrike - spx)) *
    contractMultiplier;
  const pnl = intrinsic - (debit ?? 0);
  return {
    intrinsic,
    pnl,
    pctOfDebit: debit && debit > 0 ? (pnl / debit) * 100 : null,
    settled,
  };
}

/**
 * Has the session whose last tick is `lastLabel` (an "HH:MM" ET label) finished?
 *
 * A session on a PAST date has settled no matter what its last tick says — the
 * feed simply stopped where it stopped. For TODAY, the last tick is only the
 * settlement once the cash close has passed.
 *
 * `closeLabel` defaults to the regular 16:00 ET close; pass "13:00" for an
 * early-close day so a half session is not reported as still running.
 */
export function sessionIsSettled(
  sessionDate: string,
  todayET: string,
  lastLabel: string,
  closeLabel = "16:00",
): boolean {
  if (!sessionDate || !todayET) return false;
  if (sessionDate < todayET) return true;
  return Boolean(lastLabel) && lastLabel >= closeLabel;
}

/** Today's date in New York, as `YYYY-MM-DD`. */
export function todayInNewYork(now: Date = new Date()): string {
  // `en-CA` renders as YYYY-MM-DD, which is what the API's date strings use.
  return now.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}
