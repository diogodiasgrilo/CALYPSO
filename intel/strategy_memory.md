# CALYPSO Strategy Memory

Cumulative knowledge learned by CLIO from weekly analysis.
This file is updated automatically by Clio and committed to git.

---

<!-- New learnings will be appended below this line -->

## 2026-W13 (2026-03-28)

- Ghost FOMC flag caused 4 consecutive idle days (2026-03-24 through 2026-03-27): the `fomc_announcement_skip` flag set during the March 18 blackout persisted in the state file for at least 7 calendar days past its valid scope. Apollo correctly identified the bug on all three days it warned (03-25, 03-26, 03-27) but the fix was not applied before any session. Root cause: flag is state-persisted rather than dynamically computed from the hardcoded FOMC calendar. Highest-priority known defect as of W13 close.
- VIX futures/cash discrepancy of 2.4pt observed on 2026-03-23 (Apollo overnight futures: 26.85; Google Sheets cash open: 24.45). Apollo pre-market strike range projections were overstated for E1 because MKT-020/022 uses live cash VIX at entry time (~24.45), not the overnight futures VIX Apollo cited. When futures VIX exceeds expected cash VIX by more than ~1.5pt, Apollo's implied early-entry strike distances are not reliable.
- On 2026-03-23 (VIX open 24.45, SPX high-to-low 87pt), all 5 full ICs stopped on the put side despite all entries clearing MKT-011 put credit floors ($2.10–$2.65 per side) with no MKT-029 fallback needed. Elevated credit in a gap-up/fade session did not predict safety — it reflected correct market pricing of the directional move that materialised. MKT-011 credit viability is not a reliable stop-avoidance indicator on sustained selloff days.
- MKT-028 spread width anomaly persisted for at least 4 consecutive trading days as of 2026-03-23: all position legs show `spread_width: 50` vs. the required 60pt call / 75pt put floors. All five put stops on 03-23 occurred on 50pt spreads. Any stop-level analysis from approximately 03-17 onward should be treated as against a non-spec structure; the 75pt put buffer assumptions from the 21-day backtest do not apply to this period.
- Apollo `accurate: false` flags on idle days (2026-03-26 and 2026-03-27) reflect a classification error: the logic evaluates market outcome (SPX flat = "positive day") against risk level (RED), producing false negatives on days with zero trades. Apollo's actual predictions on both days were operational (HYDRA will idle unless flag cleared), and both were correct. The accuracy metric requires a null/not-applicable mode for sessions with zero entries.
