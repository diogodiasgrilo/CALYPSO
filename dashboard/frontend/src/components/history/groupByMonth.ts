/** Group daily summaries by "YYYY-MM".
 *
 * Extracted from MonthCalendar.tsx (2026-09-18) to satisfy
 * react-refresh/only-export-components: a module that exports BOTH a component
 * and a plain function breaks Fast Refresh, because the bundler cannot tell
 * whether a change should re-render or reload. Behaviour is byte-identical —
 * this is the same function, moved.
 */

import type { DaySummary } from "./types";

export function groupByMonth(summaries: DaySummary[]): Map<string, DaySummary[]> {
  const map = new Map<string, DaySummary[]>();
  const sorted = [...summaries].sort((a, b) => a.date.localeCompare(b.date));
  for (const s of sorted) {
    const key = s.date.slice(0, 7);
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(s);
  }
  return map;
}
