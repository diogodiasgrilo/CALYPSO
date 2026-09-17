/**
 * Which analytics charts are MEANINGFUL for a given strategy (defect D11).
 *
 * `pages/Analytics.tsx` is 1,334 lines with zero taxonomy references, so every
 * strategy got the same sixteen 0DTE iron-condor charts. Rendering "Rolling win
 * rate (10-day)" flat at 0% for a strategy with five traded days, or "Avg P&L
 * by day of week" for a position opened Monday and closed Thursday, is worse
 * than rendering nothing: a blank card reads as "no data yet", whereas a
 * confident axis of zeros reads as a result.
 *
 * WHY A DECLARATIVE REGISTRY RATHER THAN `if (id === "e")`. Per-variant
 * branching is how the dashboard got into this state — the taxonomy exists so a
 * new strategy needs no edit in a renderer. Each chart declares the FACTS it
 * depends on; the strategy supplies those facts; applicability falls out. A new
 * variant is then automatically correct, and a chart that starts depending on
 * something new declares it in one place.
 *
 * WHY HIDDEN CHARTS ARE NAMED RATHER THAN SILENTLY DROPPED. A reader comparing
 * two strategies must be able to tell "this chart does not apply here" from
 * "this chart is broken". `hiddenReasons()` gives the page the one-line
 * explanation to show under the grid.
 */

import type { StrategyInfo } from "../hooks/useStrategyMeta";

/** The taxonomy facts a chart may depend on. All optional; omitted = "don't care". */
export interface ChartRequires {
  /** Needs a net CREDIT to plot (credit-by-slot). A debit structure has none. */
  credit?: boolean;
  /** Needs the position to open and close within one session. */
  sameDaySession?: boolean;
  /** Needs BOTH a call and a put side to compare. */
  bothSides?: boolean;
  /** Needs defined wings, i.e. a spread width / OTM-distance concept. */
  definedWings?: boolean;
  /** Needs the 4-leg iron-condor entry-type taxonomy (full / call-only / put-only). */
  ironCondorEntryTypes?: boolean;
  /** Needs HYDRA's EMA trend-signal field, recorded only by the IC strategies. */
  trendSignal?: boolean;
}

export interface ChartSpec {
  /** Must match the ChartCard title exactly — that is the user-visible name. */
  title: string;
  requires?: ChartRequires;
}

/** Every Analytics chart, by tab. Titles mirror the ChartCard titles verbatim. */
export const ANALYTICS_CHARTS: Record<string, ChartSpec[]> = {
  performance: [
    { title: "Cumulative P&L" },
    { title: "Daily P&L Distribution" },
    { title: "Rolling Win Rate (10-day)" },
    { title: "Avg P&L by Day of Week", requires: { sameDaySession: true } },
  ],
  entries: [
    { title: "Avg Credit by Time Slot", requires: { credit: true, sameDaySession: true } },
    { title: "Entry Type Breakdown", requires: { ironCondorEntryTypes: true } },
    { title: "Avg P&L by Entry Number", requires: { sameDaySession: true } },
    { title: "Survival Rate by OTM Distance", requires: { definedWings: true } },
  ],
  stops: [
    { title: "Stop Rate by Time Slot", requires: { sameDaySession: true } },
    { title: "Call vs Put Stop Ratio", requires: { bothSides: true } },
    { title: "Stop Slippage Distribution" },
    { title: "Stops Per Day Distribution" },
  ],
  market: [
    { title: "VIX vs Daily P&L" },
    { title: "Day Range vs P&L" },
    { title: "Avg P&L by Market Direction" },
    { title: "Avg P&L by Trend Signal", requires: { trendSignal: true } },
  ],
};

/** The facts a strategy supplies, derived ONLY from taxonomy fields. */
export interface StrategyFacts {
  credit: boolean;
  sameDaySession: boolean;
  bothSides: boolean;
  definedWings: boolean;
  ironCondorEntryTypes: boolean;
  trendSignal: boolean;
}

export function factsFor(s: StrategyInfo | null | undefined): StrategyFacts {
  // Absent meta must not hide anything — an unknown strategy shows the full
  // grid, exactly as before this module existed.
  if (!s) {
    return {
      credit: true, sameDaySession: true, bothSides: true,
      definedWings: true, ironCondorEntryTypes: true, trendSignal: true,
    };
  }
  return {
    credit: s.pnl_shape !== "debit",
    // `dte_class` is the holding horizon. Anything not explicitly multi_day is
    // treated as same-session, so an unknown value degrades to showing more
    // rather than hiding something real.
    sameDaySession: s.dte_class !== "multi_day",
    bothSides: s.sides !== "one_sided",
    // A wingless structure has no spread width, so no OTM-distance bucketing.
    definedWings: s.capital_basis !== "broker_margin",
    // The full/call-only/put-only taxonomy belongs to the 4-leg iron condor.
    // A strangle has no wings and a one-sided vertical is always "one type".
    ironCondorEntryTypes: s.family === "iron_condor" && s.sides !== "one_sided",
    trendSignal: s.data_kind === "ic_state",
  };
}

/** Human explanation for why a chart does not apply — shown, never guessed at. */
const REASONS: Record<keyof ChartRequires, string> = {
  credit: "no credit — this is a net-debit structure",
  sameDaySession: "positions are held across multiple days",
  bothSides: "this strategy trades one side only",
  definedWings: "no spread width — risk is undefined, not capped",
  ironCondorEntryTypes: "not a four-leg iron condor",
  trendSignal: "this strategy does not record a trend signal",
};

export function chartApplies(spec: ChartSpec, facts: StrategyFacts): boolean {
  const req = spec.requires;
  if (!req) return true;
  return (Object.keys(req) as (keyof ChartRequires)[]).every(
    (k) => req[k] !== true || facts[k],
  );
}

export function applicableCharts(tab: string, facts: StrategyFacts): Set<string> {
  const specs = ANALYTICS_CHARTS[tab] ?? [];
  return new Set(specs.filter((s) => chartApplies(s, facts)).map((s) => s.title));
}

/** `[{title, reason}]` for the charts hidden on this tab, for the footnote. */
export function hiddenReasons(
  tab: string,
  facts: StrategyFacts,
): { title: string; reason: string }[] {
  const specs = ANALYTICS_CHARTS[tab] ?? [];
  const out: { title: string; reason: string }[] = [];
  for (const spec of specs) {
    if (chartApplies(spec, facts)) continue;
    const req = spec.requires ?? {};
    const failed = (Object.keys(req) as (keyof ChartRequires)[]).filter(
      (k) => req[k] === true && !facts[k],
    );
    out.push({ title: spec.title, reason: failed.map((k) => REASONS[k]).join("; ") });
  }
  return out;
}
