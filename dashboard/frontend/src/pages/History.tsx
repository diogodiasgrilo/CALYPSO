import { useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import { MonthCalendar, groupByMonth } from "../components/history/MonthCalendar";
import { DailySummaryTable } from "../components/history/DailySummaryTable";
import { DayDetailModal } from "../components/history/DayDetailModal";
import type { DaySummary } from "../components/history/types";

export function History() {
  const [summaries, setSummaries] = useState<DaySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedYear, setSelectedYear] = useState(
    new Date().getFullYear()
  );
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

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

  const currentYear = new Date().getFullYear();
  const startYear = 2026;
  const monthGroups = useMemo(() => groupByMonth(summaries), [summaries]);
  const maxPnl = useMemo(
    () => Math.max(...summaries.map((s) => Math.abs(s.net_pnl || 0)), 1),
    [summaries]
  );

  const selectedSummary = useMemo(
    () => summaries.find((s) => s.date === selectedDate) ?? null,
    [summaries, selectedDate]
  );

  const handleDayClick = (date: string) => setSelectedDate(date);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-text-dim">
        Loading history...
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Compact header: title + year dropdown + legend */}
      <div className="flex items-center gap-3">
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
          P&L Calendar
        </h2>
        <div className="relative">
          <select
            value={selectedYear}
            onChange={(e) => setSelectedYear(Number(e.target.value))}
            className="appearance-none bg-bg-elevated border border-border-dim rounded px-2 py-0.5 pr-5 text-xs font-mono font-semibold text-text-primary cursor-pointer hover:border-text-dim transition-colors focus:outline-none focus:border-text-secondary"
          >
            {Array.from({ length: currentYear - startYear + 1 }, (_, i) => startYear + i).map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
          <ChevronDown size={10} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-text-dim pointer-events-none" />
        </div>
        <div className="flex items-center gap-1.5 text-[9px] text-text-dim">
          <span>Loss</span>
          <div className="flex gap-px">
            {[0.7, 0.5, 0.3].map((o) => (
              <div
                key={`l${o}`}
                className="w-2 h-2 rounded-sm"
                style={{ backgroundColor: `rgba(248, 81, 73, ${o})` }}
              />
            ))}
            <div
              className="w-2 h-2 rounded-sm"
              style={{ backgroundColor: "rgba(90, 100, 120, 0.2)" }}
            />
            {[0.3, 0.5, 0.7].map((o) => (
              <div
                key={`p${o}`}
                className="w-2 h-2 rounded-sm"
                style={{ backgroundColor: `rgba(126, 232, 199, ${o})` }}
              />
            ))}
          </div>
          <span>Profit</span>
        </div>
      </div>

      {/* Month Calendar Grid */}
      <div className="bg-card rounded-lg border border-border-dim p-3">
        {monthGroups.size === 0 ? (
          <div className="text-center text-text-dim text-xs py-8">
            No trading data for {selectedYear}
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3 max-md:grid-cols-2 max-sm:grid-cols-1">
            {[...monthGroups.entries()].map(([monthKey, days]) => (
              <MonthCalendar
                key={monthKey}
                monthKey={monthKey}
                days={days}
                maxPnl={maxPnl}
                onDayClick={handleDayClick}
              />
            ))}
          </div>
        )}
      </div>

      {/* Sortable Daily Summary Table */}
      {summaries.length > 0 && (
        <DailySummaryTable summaries={summaries} onDayClick={handleDayClick} />
      )}

      {/* Day Detail Modal */}
      {selectedDate && (
        <DayDetailModal
          date={selectedDate}
          summary={selectedSummary}
          onClose={() => setSelectedDate(null)}
        />
      )}
    </div>
  );
}
