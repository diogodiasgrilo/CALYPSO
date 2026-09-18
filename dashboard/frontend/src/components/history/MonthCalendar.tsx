import { useMemo } from "react";
import { pnlColor, colors } from "../../lib/tradingColors";
import { formatPnL } from "../../lib/formatters";
import type { DaySummary } from "./types";

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const WEEKDAY_LABELS = ["M", "T", "W", "T", "F"];

/* Heat ramps for the P&L calendar.
 *
 * These replace `rgba(126,232,199, 0.15 + intensity*0.6)` and its red twin.
 * Fading a bright colour toward the surface with RISING alpha meant the
 * strongest P&L days produced the LIGHTEST cells — and their day numbers fell
 * to 2.03:1 against white, so legibility was inversely correlated with how much
 * the day mattered. Measured 2026-09-18; see
 * tests/test_dashboard_contrast_aa_2026_09_18.py.
 *
 * No choice of text colour could rescue the old ramp: at the crossover
 * luminance neither white nor near-black reaches 4.5:1 (the best available is
 * 3.80:1). So the fill has to DARKEN as it saturates instead of lightening,
 * which these do — every step clears 4.5:1 with --color-text-primary, and the
 * test pins monotonicity as well as the endpoints, because a ramp that
 * brightened only in the middle would pass a per-step check and still
 * reintroduce the bug at whatever intensity happened not to be sampled.
 */
const PROFIT_HEAT = ["#1a3630", "#1c4a3d", "#1a5e4a", "#147055", "#0a7858"];
const LOSS_HEAT = ["#3b1f1f", "#4e2421", "#642823", "#7c2a24", "#962c24"];

/** Map a 0..1 intensity onto a ramp step. */
function heatStep(ramp: readonly string[], intensity: number): string {
  const i = Math.round(Math.max(0, Math.min(1, intensity)) * (ramp.length - 1));
  return ramp[i];
}

/** Group summaries by month key (YYYY-MM) in chronological order. */
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

/** Cell in the calendar grid — null means slot is outside the month. */
type GridCell = { dayNum: number; date: string; summary: DaySummary | null } | null;

/**
 * Build a week-based grid for a month.
 * Returns an array of weeks, each week is [Mon, Tue, Wed, Thu, Fri].
 * null = slot is outside the month (e.g., month starts on Wed → Mon/Tue are null).
 * { summary: null } = valid weekday but no trading data.
 */
function buildMonthGrid(monthKey: string, days: DaySummary[]): GridCell[][] {
  const dayMap = new Map(days.map((d) => [d.date, d]));
  const [yearStr, monthStr] = monthKey.split("-");
  const year = parseInt(yearStr);
  const month = parseInt(monthStr) - 1;

  const weeks: GridCell[][] = [];
  let currentWeek: GridCell[] = [null, null, null, null, null];
  let hasContent = false;

  const daysInMonth = new Date(year, month + 1, 0).getDate();

  for (let day = 1; day <= daysInMonth; day++) {
    const date = new Date(year, month, day);
    const dow = date.getDay();
    if (dow === 0 || dow === 6) continue;

    const weekdayIdx = dow - 1;
    if (weekdayIdx === 0 && hasContent) {
      weeks.push(currentWeek);
      currentWeek = [null, null, null, null, null];
      hasContent = false;
    }

    const dateStr = `${yearStr}-${monthStr}-${String(day).padStart(2, "0")}`;
    currentWeek[weekdayIdx] = {
      dayNum: day,
      date: dateStr,
      summary: dayMap.get(dateStr) ?? null,
    };
    hasContent = true;
  }

  if (hasContent) {
    weeks.push(currentWeek);
  }

  return weeks;
}

export function MonthCalendar({
  monthKey,
  days,
  onDayClick,
}: {
  monthKey: string;
  days: DaySummary[];
  onDayClick: (date: string) => void;
}) {
  const [, monthStr] = monthKey.split("-");
  const monthName = MONTH_NAMES[parseInt(monthStr) - 1];
  const weeks = useMemo(() => buildMonthGrid(monthKey, days), [monthKey, days]);

  const netPnl = days.reduce((sum, d) => sum + (d.net_pnl || 0), 0);
  const wins = days.filter((d) => (d.net_pnl || 0) > 0).length;
  const losses = days.filter((d) => (d.net_pnl || 0) < 0).length;
  // Scale heat-map intensity to THIS month's own range, not the whole year — a
  // single outsized day elsewhere shouldn't wash every other day toward flat.
  const monthMax = Math.max(1, ...days.map((d) => Math.abs(d.net_pnl || 0)));

  return (
    <div className="bg-card rounded-lg border border-border-dim p-3">
      <div className="text-xs font-semibold text-text-primary mb-2">
        {monthName}
      </div>

      <div className="flex gap-0.5 mb-1">
        {WEEKDAY_LABELS.map((label, i) => (
          <div
            key={i}
            className="flex-1 h-4 flex items-center justify-center text-3xs text-text-dim font-medium"
          >
            {label}
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-0.5">
        {weeks.map((week, wi) => (
          <div key={wi} className="flex gap-0.5">
            {week.map((cell, di) => {
              // Slot outside the month (e.g., month starts mid-week)
              if (!cell) {
                return (
                  <div key={di} className="flex-1 h-8 rounded-sm" />
                );
              }

              const { dayNum, date, summary } = cell;

              // Valid weekday but no trading data
              if (!summary) {
                return (
                  <div
                    key={di}
                    className="flex-1 h-8 rounded-sm flex items-center justify-center text-3xs font-mono text-text-dim/40"
                    style={{ backgroundColor: "rgba(90, 100, 120, 0.1)" }}
                    title={date}
                  >
                    {dayNum}
                  </div>
                );
              }

              // Trading day with data
              const pnl = summary.net_pnl || 0;
              const intensity = Math.min(Math.abs(pnl) / monthMax, 1);
              const bgColor =
                pnl > 0
                  ? heatStep(PROFIT_HEAT, intensity)
                  : pnl < 0
                  ? heatStep(LOSS_HEAT, intensity)
                  : "rgba(90, 100, 120, 0.2)";

              return (
                <div
                  key={di}
                  role="button"
                  tabIndex={0}
                  aria-label={`${date}: ${formatPnL(pnl)}`}
                  className="flex-1 h-8 rounded-sm flex items-center justify-center text-3xs font-mono cursor-pointer hover:ring-1 hover:ring-text-dim/50 hover:brightness-125 transition-all focus-visible:ring-2 focus-visible:ring-info focus-visible:outline-none"
                  style={{ backgroundColor: bgColor }}
                  title={`${date}: ${formatPnL(pnl)} | ${summary.entries_placed} entries, ${summary.actual_stops ?? summary.entries_stopped ?? 0} stops`}
                  onClick={() => onDayClick(date)}
                  onKeyDown={(e) => {
                    // Both keys: Space alone scrolls the page, Enter alone
                    // breaks the platform convention for role="button".
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onDayClick(date);
                    }
                  }}
                >
                  {dayNum}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <div className="flex flex-col items-center mt-2 pt-2 border-t border-border-dim/50 gap-0.5">
        <span
          className="text-xs font-mono font-bold"
          style={{ color: pnlColor(netPnl) }}
        >
          {formatPnL(netPnl)}
        </span>
        <span className="text-3xs text-text-dim">
          <span style={{ color: colors.profit }}>{wins}</span>
          <span className="text-text-dim">W</span>
          <span className="mx-0.5 text-text-dim">/</span>
          <span style={{ color: losses > 0 ? colors.loss : colors.textDim }}>
            {losses}
          </span>
          <span className="text-text-dim">L</span>
        </span>
      </div>
    </div>
  );
}
