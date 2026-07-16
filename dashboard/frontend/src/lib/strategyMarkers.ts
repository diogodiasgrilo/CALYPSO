/**
 * Strategy-change markers for the equity curve.
 *
 * Non-destructive annotation of when a variant's behavior materially changed, so
 * the all-time cumulative curve stays intact (full track record) while still
 * making "before vs after the change" legible. Add a row here when a variant's
 * strategy changes; the equity curve draws a vertical marker on/after `date` and
 * a "since <label>" P&L caption for the days on/after it.
 */
export interface StrategyMarker {
  /** Effective date the change takes hold (YYYY-MM-DD, ET trading day). */
  date: string;
  /** Short label rendered on the chart + in the "since" caption. */
  label: string;
  /** Strategy ids (lowercase, e.g. "b","c") this marker applies to. */
  strategyIds: string[];
}

export const STRATEGY_MARKERS: StrategyMarker[] = [
  {
    date: "2026-07-17",
    label: "require-both-sides",
    strategyIds: ["b", "c"],
  },
];

/** Markers applicable to a given strategy id (case-insensitive). */
export function markersForStrategy(strategyId: string | undefined | null): StrategyMarker[] {
  const id = (strategyId || "").toLowerCase();
  return STRATEGY_MARKERS.filter((m) => m.strategyIds.includes(id));
}
