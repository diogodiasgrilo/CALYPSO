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

## 2026-W23 (2026-06-01)

- Regime 0 + monotonic uptrend + VIX >15.0 produces a systematic three-way skip cascade on 05-28 and 05-29: call credit falls below the $1.00 Regime 0 floor by E#2 (11:15), MKT-032 put-only fallback is blocked because VIX >15.0 (`put_only_max_vix: 15.0`), and E#3 Upday-035 fires as put-only but finds $0 premium. This cascade requires all three conditions simultaneously; any one condition absent breaks the cascade.
- MKT-032's `put_only_max_vix: 15.0` gate blocks put-only fallback for most of Regime 0 in practice: with Regime 0 spanning VIX < 18.0, the gate only permits put-only in the bottom 17% of the regime range (<15.0). On 05-28 at VIX 16.0, E#1 put credit was $140 (above the $1.25 floor) but MKT-032 blocked the fallback at E#2 — put premium was available but inaccessible due to the VIX gate design asymmetry.
- The $1.00 Regime 0 call credit floor produces elevated skip rates at SPX 7,500+: on 05-29 (SPX open 7,591.85, VIX 15.24), all five entry attempts skipped on call-side credit gate failures. At 2.5× MKT-024 OTM from 7,592, short calls land ~40–44pt OTM where VIX 15.24 generates sub-$1.00 premium on 75pt-wide spreads. The $1.00 absolute floor has not been reviewed since SPX was ~200–300 points lower.
- Apollo's prediction accuracy is materially higher when it explicitly names the binding MKT rule constraint rather than issuing regime-level generalizations: the 05-29 GREEN briefing explicitly identified `put_only_max_vix: 15.0` as the binding gate and correctly forecast all 5 skip events; the 05-28 GREEN briefing stated "MKT-011 skips unlikely" without simulating the trending-session credit-compression dynamic, producing an inaccurate call.
- Commission-drag days (-$10 to -$20, zero stops) are recorded as "losses" in the winning/losing streak counter and cumulative P&L despite representing zero risk events: the four days 05-22, 05-26, 05-27, 05-28 accumulated -$60 in commission drag with no stop exposure. The L15 losing streak ending 05-29 includes at least 8 such commission-only days; a "stop-triggered loss day" series would show a meaningfully shorter streak and different structural picture.
