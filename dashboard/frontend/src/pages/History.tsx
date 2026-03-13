import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { pnlColor, colors } from "../lib/tradingColors";
import { formatPnL, formatDateShort } from "../lib/formatters";

interface DaySummary {
  date: string;
  net_pnl: number;
  gross_pnl: number;
  entries_placed: number;
  entries_stopped: number;
  entries_expired: number;
  commission: number;
  spx_open: number;
  spx_close: number;
  vix_open: number;
  day_type: string;
  day_of_week: string;
}

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const WEEKDAY_LABELS = ["M", "T", "W", "T", "F"];

/** Group summaries by month key (YYYY-MM) in chronological order. */
function groupByMonth(summaries: DaySummary[]): Map<string, DaySummary[]> {
  const map = new Map<string, DaySummary[]>();
  // Sort chronologically
  const sorted = [...summaries].sort((a, b) => a.date.localeCompare(b.date));
  for (const s of sorted) {
    const key = s.date.slice(0, 7); // "YYYY-MM"
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(s);
  }
  return map;
}

/**
 * Build a week-based grid for a month.
 * Returns an array of weeks, each week is [Mon, Tue, Wed, Thu, Fri] (null if no trading day).
 */
function buildMonthGrid(
  monthKey: string,
  days: DaySummary[]
): (DaySummary | null)[][] {
  // Create a lookup by date
  const dayMap = new Map(days.map((d) => [d.date, d]));

  const [yearStr, monthStr] = monthKey.split("-");
  const year = parseInt(yearStr);
  const month = parseInt(monthStr) - 1; // JS months are 0-indexed

  // Find all weekdays in this month
  const weeks: (DaySummary | null)[][] = [];
  let currentWeek: (DaySummary | null)[] = [null, null, null, null, null];
  let hasContent = false;

  const daysInMonth = new Date(year, month + 1, 0).getDate();

  for (let day = 1; day <= daysInMonth; day++) {
    const date = new Date(year, month, day);
    const dow = date.getDay(); // 0=Sun, 1=Mon, ..., 5=Fri, 6=Sat

    if (dow === 0 || dow === 6) continue; // Skip weekends

    const weekdayIdx = dow - 1; // 0=Mon, 1=Tue, ..., 4=Fri

    // Start a new week if we're back to Monday and current week has content
    if (weekdayIdx === 0 && hasContent) {
      weeks.push(currentWeek);
      currentWeek = [null, null, null, null, null];
      hasContent = false;
    }

    const dateStr = `${yearStr}-${monthStr}-${String(day).padStart(2, "0")}`;
    currentWeek[weekdayIdx] = dayMap.get(dateStr) ?? null;
    hasContent = true;
  }

  // Push the last week if it has content
  if (hasContent) {
    weeks.push(currentWeek);
  }

  return weeks;
}

function MonthCard({
  monthKey,
  days,
  maxPnl,
}: {
  monthKey: string;
  days: DaySummary[];
  maxPnl: number;
}) {
  const [, monthStr] = monthKey.split("-");
  const monthName = MONTH_NAMES[parseInt(monthStr) - 1];
  const weeks = useMemo(() => buildMonthGrid(monthKey, days), [monthKey, days]);

  // Month stats
  const netPnl = days.reduce((sum, d) => sum + (d.net_pnl || 0), 0);
  const wins = days.filter((d) => (d.net_pnl || 0) > 0).length;
  const losses = days.filter((d) => (d.net_pnl || 0) < 0).length;

  return (
    <div className="bg-card rounded-lg border border-border-dim p-3">
      {/* Month header */}
      <div className="text-xs font-semibold text-text-primary mb-2">
        {monthName}
      </div>

      {/* Weekday headers */}
      <div className="flex gap-0.5 mb-1">
        {WEEKDAY_LABELS.map((label, i) => (
          <div
            key={i}
            className="w-7 h-4 flex items-center justify-center text-[9px] text-text-dim font-medium"
          >
            {label}
          </div>
        ))}
      </div>

      {/* Week rows */}
      <div className="flex flex-col gap-0.5">
        {weeks.map((week, wi) => (
          <div key={wi} className="flex gap-0.5">
            {week.map((day, di) => {
              if (!day) {
                return (
                  <div
                    key={di}
                    className="w-7 h-7 rounded-sm"
                    style={{ backgroundColor: "rgba(90, 100, 120, 0.06)" }}
                  />
                );
              }

              const pnl = day.net_pnl || 0;
              const intensity = Math.min(Math.abs(pnl) / maxPnl, 1);
              const bgColor =
                pnl > 0
                  ? `rgba(126, 232, 199, ${0.15 + intensity * 0.6})`
                  : pnl < 0
                  ? `rgba(248, 81, 73, ${0.15 + intensity * 0.6})`
                  : "rgba(90, 100, 120, 0.2)";

              const dateNum = new Date(day.date + "T12:00:00").getDate();

              return (
                <div
                  key={di}
                  className="w-7 h-7 rounded-sm flex items-center justify-center text-[9px] font-mono cursor-default"
                  style={{ backgroundColor: bgColor }}
                  title={`${day.date}: ${formatPnL(pnl)} | ${day.entries_placed} entries, ${day.entries_stopped} stops`}
                >
                  {dateNum}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* Month summary footer */}
      <div className="flex items-center justify-between mt-2 pt-2 border-t border-border-dim/50">
        <span
          className="text-[10px] font-mono font-semibold"
          style={{ color: pnlColor(netPnl) }}
        >
          {formatPnL(netPnl)}
        </span>
        <span className="text-[10px] text-text-dim">
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

export function History() {
  const [summaries, setSummaries] = useState<DaySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedYear, setSelectedYear] = useState(
    new Date().getFullYear()
  );

  useEffect(() => {
    setLoading(true);
    fetch(`/api/metrics/daily?year=${selectedYear}`)
      .then((r) => r.json())
      .then((data) => {
        setSummaries(data.summaries ?? []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [selectedYear]);

  // Determine available years (hardcode start year, go to current)
  const currentYear = new Date().getFullYear();
  const startYear = 2026; // HYDRA started trading
  const canGoBack = selectedYear > startYear;
  const canGoForward = selectedYear < currentYear;

  // Group by month
  const monthGroups = useMemo(() => groupByMonth(summaries), [summaries]);

  // Max P&L for intensity scaling (across all days in the year)
  const maxPnl = useMemo(
    () => Math.max(...summaries.map((s) => Math.abs(s.net_pnl || 0)), 1),
    [summaries]
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-text-dim">
        Loading history...
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header with year navigation */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text-primary">
          Trading History
        </h2>

        {/* Year navigation */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => canGoBack && setSelectedYear((y) => y - 1)}
            disabled={!canGoBack}
            className="p-1 rounded hover:bg-bg-elevated disabled:opacity-20 transition-opacity"
          >
            <ChevronLeft size={14} className="text-text-secondary" />
          </button>
          <span className="text-sm font-semibold text-text-primary font-mono min-w-[3rem] text-center">
            {selectedYear}
          </span>
          <button
            onClick={() => canGoForward && setSelectedYear((y) => y + 1)}
            disabled={!canGoForward}
            className="p-1 rounded hover:bg-bg-elevated disabled:opacity-20 transition-opacity"
          >
            <ChevronRight size={14} className="text-text-secondary" />
          </button>
        </div>
      </div>

      {/* Month Calendar Grid */}
      <div className="bg-card rounded-lg border border-border-dim p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
            Daily P&L Calendar
          </h3>

          {/* Legend */}
          <div className="flex items-center gap-2 text-[10px] text-text-dim">
            <span>Loss</span>
            <div className="flex gap-0.5">
              {[0.7, 0.5, 0.3, 0.1].map((o) => (
                <div
                  key={`l${o}`}
                  className="w-3 h-3 rounded-sm"
                  style={{ backgroundColor: `rgba(248, 81, 73, ${o})` }}
                />
              ))}
              <div
                className="w-3 h-3 rounded-sm"
                style={{ backgroundColor: "rgba(90, 100, 120, 0.2)" }}
              />
              {[0.1, 0.3, 0.5, 0.7].map((o) => (
                <div
                  key={`p${o}`}
                  className="w-3 h-3 rounded-sm"
                  style={{ backgroundColor: `rgba(126, 232, 199, ${o})` }}
                />
              ))}
            </div>
            <span>Profit</span>
          </div>
        </div>

        {monthGroups.size === 0 ? (
          <div className="text-center text-text-dim text-xs py-8">
            No trading data for {selectedYear}
          </div>
        ) : (
          <div className="grid grid-cols-4 gap-3 max-lg:grid-cols-3 max-md:grid-cols-2 max-sm:grid-cols-1">
            {[...monthGroups.entries()].map(([monthKey, days]) => (
              <MonthCard
                key={monthKey}
                monthKey={monthKey}
                days={days}
                maxPnl={maxPnl}
              />
            ))}
          </div>
        )}
      </div>

      {/* Daily Summary Table */}
      <div className="bg-card rounded-lg border border-border-dim overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border-dim">
              <th className="text-left px-3 py-2 text-text-secondary font-semibold">
                Date
              </th>
              <th className="text-right px-3 py-2 text-text-secondary font-semibold">
                Net P&L
              </th>
              <th className="text-center px-3 py-2 text-text-secondary font-semibold">
                Entries
              </th>
              <th className="text-center px-3 py-2 text-text-secondary font-semibold">
                Stops
              </th>
              <th className="text-right px-3 py-2 text-text-secondary font-semibold">
                SPX
              </th>
              <th className="text-right px-3 py-2 text-text-secondary font-semibold">
                VIX
              </th>
            </tr>
          </thead>
          <tbody>
            {summaries.map((day) => (
              <tr
                key={day.date}
                className="border-b border-border-dim/50 hover:bg-bg-elevated/30"
              >
                <td className="px-3 py-1.5 text-text-primary">
                  {formatDateShort(day.date)}
                  <span className="text-text-dim ml-1 text-[10px]">
                    {day.day_of_week?.slice(0, 3)}
                  </span>
                </td>
                <td
                  className="px-3 py-1.5 text-right font-mono font-semibold"
                  style={{ color: pnlColor(day.net_pnl || 0) }}
                >
                  {formatPnL(day.net_pnl || 0)}
                </td>
                <td className="px-3 py-1.5 text-center text-text-primary">
                  {day.entries_placed}
                </td>
                <td
                  className="px-3 py-1.5 text-center"
                  style={{
                    color:
                      (day.entries_stopped || 0) > 0
                        ? colors.loss
                        : colors.textPrimary,
                  }}
                >
                  {day.entries_stopped || 0}
                </td>
                <td className="px-3 py-1.5 text-right text-text-secondary">
                  {day.spx_close?.toFixed(0) || "—"}
                </td>
                <td className="px-3 py-1.5 text-right text-text-secondary">
                  {day.vix_open?.toFixed(1) || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
