/**
 * Whose FOMC policy the banner is describing.
 *
 * Extracted so a test can EXECUTE this rather than grep for it. The first pass
 * of the fix was covered only by string-presence assertions, and a build with
 * `const policy = null` — i.e. the bug restored — kept two of the three green.
 * Pure, importing nothing, same contract as `strangleVerdict.ts`.
 *
 * WHY THE SCOPING MATTERS
 * ------------------------
 * The banner used to read `fomc_announcement_skip` / `fomc_t1_skip_enabled`
 * from the PRIMARY seat's state for every selection. The live seat is B, which
 * **does** skip announcement days. D, E, F, G and H all trade straight through
 * them. So on an announcement day, selecting any of those five showed "All
 * entries skipped" while the strategy was actively taking positions — the
 * banner reporting a strategy as flat on exactly the day it was most exposed,
 * and G is an undefined-risk naked strangle.
 */

export interface FomcFlags {
  announcement_skip: boolean;
  t1_skip: boolean;
}

/** What the primary seat's live state carries (both optional/loose). */
export interface PrimaryFomcState {
  fomc_announcement_skip?: boolean | null;
  fomc_t1_skip_enabled?: boolean | null;
}

/**
 * The flags the banner should describe.
 *
 * A selected variant's own policy always wins. With nothing selected — the
 * default dashboard view — it falls back to the primary's live state, keeping
 * that view's long-standing behaviour including its defaults: `t1_skip` is
 * treated as ON unless explicitly `false` (the VM's configuration), while
 * `announcement_skip` is OFF unless explicitly `true`.
 */
export function resolveFomcPolicy(
  selected: FomcFlags | null | undefined,
  primary: PrimaryFomcState | null | undefined,
): FomcFlags {
  if (selected) {
    return {
      announcement_skip: selected.announcement_skip === true,
      t1_skip: selected.t1_skip === true,
    };
  }
  return {
    announcement_skip: primary?.fomc_announcement_skip === true,
    t1_skip: primary?.fomc_t1_skip_enabled !== false,
  };
}
