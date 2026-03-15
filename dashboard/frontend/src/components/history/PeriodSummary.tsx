import { useState, useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { formatPnL } from "../../lib/formatters";
import { pnlColor, colors } from "../../lib/tradingColors";
import type { DaySummary } from "./types";

interface PeriodSummaryProps {
  summaries: DaySummary[];
}

/** Get current date in US Eastern Time (matches the timezone of all trading data). */
function getTodayET(): string {
  const now = new Date();
  // Intl.DateTimeFormat gives us the ET date regardless of user's local timezone
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now); // Returns "YYYY-MM-DD" with en-CA locale
  return parts;
}

function getThisWeekDays(summaries: DaySummary[]): DaySummary[] {
  const todayStr = getTodayET();
  // Parse ET today to find Monday (all string-based to avoid timezone drift)
  const [y, m, d] = todayStr.split("-").map(Number);
  const today = new Date(Date.UTC(y, m - 1, d));
  const dayOfWeek = today.getUTCDay(); // 0=Sun .. 6=Sat
  const mondayDate = new Date(today);
  mondayDate.setUTCDate(today.getUTCDate() - ((dayOfWeek + 6) % 7));
  const mondayStr = mondayDate.toISOString().slice(0, 10);
  return summaries.filter((s) => s.date >= mondayStr);
}

function getThisMonthDays(summaries: DaySummary[]): DaySummary[] {
  const todayStr = getTodayET();
  const prefix = todayStr.slice(0, 7); // "YYYY-MM"
  return summaries.filter((s) => s.date.startsWith(prefix));
}

function SummaryCard({
  label,
  days,
}: {
  label: string;
  days: DaySummary[];
}) {
  const [open, setOpen] = useState(true);

  const stats = useMemo(() => {
    const totalPnl = days.reduce((a, d) => a + (d.net_pnl ?? 0), 0);
    const totalEntries = days.reduce((a, d) => a + (d.entries_placed ?? 0), 0);
    const totalStops = days.reduce((a, d) => a + (d.entries_stopped ?? 0), 0);
    const wins = days.filter((d) => (d.net_pnl ?? 0) > 0).length;
    const losses = days.filter((d) => (d.net_pnl ?? 0) < 0).length;
    const best = days.length > 0 ? Math.max(...days.map((d) => d.net_pnl ?? 0)) : 0;
    const worst = days.length > 0 ? Math.min(...days.map((d) => d.net_pnl ?? 0)) : 0;
    return { totalPnl, totalEntries, totalStops, wins, losses, best, worst };
  }, [days]);

  const { totalPnl, totalEntries, totalStops, wins, losses, best, worst } = stats;

  if (days.length === 0) return null;

  return (
    <div className="bg-card rounded-lg border border-border-dim">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-card-hover transition-colors"
      >
        <div className="flex items-center gap-2">
          {open ? <ChevronDown size={12} className="text-text-dim" /> : <ChevronRight size={12} className="text-text-dim" />}
          <span className="label-upper">{label}</span>
        </div>
        <span className="metric-body font-bold" style={{ color: pnlColor(totalPnl) }}>
          {formatPnL(totalPnl)}
        </span>
      </button>
      {open && (
        <div className="px-3 pb-3 grid grid-cols-3 gap-x-4 gap-y-1 text-xs">
          <div className="flex justify-between">
            <span className="text-text-secondary">Days</span>
            <span className="text-text-primary">{days.length}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Entries</span>
            <span className="text-text-primary">{totalEntries}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Stops</span>
            <span style={{ color: totalStops > 0 ? colors.loss : colors.textPrimary }}>{totalStops}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">W/L</span>
            <span>
              <span style={{ color: colors.profit }}>{wins}</span>
              <span className="text-text-dim">/</span>
              <span style={{ color: colors.loss }}>{losses}</span>
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Best</span>
            <span style={{ color: pnlColor(best) }}>{formatPnL(best)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Worst</span>
            <span style={{ color: pnlColor(worst) }}>{formatPnL(worst)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

export function PeriodSummary({ summaries }: PeriodSummaryProps) {
  const thisWeek = useMemo(() => getThisWeekDays(summaries), [summaries]);
  const thisMonth = useMemo(() => getThisMonthDays(summaries), [summaries]);

  if (thisWeek.length === 0 && thisMonth.length === 0) return null;

  return (
    <div className="grid grid-cols-2 gap-3 max-sm:grid-cols-1">
      <SummaryCard label="This Week" days={thisWeek} />
      <SummaryCard label="This Month" days={thisMonth} />
    </div>
  );
}
