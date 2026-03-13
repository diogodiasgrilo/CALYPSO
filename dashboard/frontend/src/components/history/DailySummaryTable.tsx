import { useMemo, useState } from "react";
import { ChevronUp, ChevronDown } from "lucide-react";
import { pnlColor, colors } from "../../lib/tradingColors";
import { formatPnL, formatDateShort } from "../../lib/formatters";
import type { DaySummary, SortKey, SortDir } from "./types";

const COLUMNS: { key: SortKey; label: string; align: string }[] = [
  { key: "date", label: "Date", align: "text-left" },
  { key: "net_pnl", label: "Net P&L", align: "text-right" },
  { key: "entries_placed", label: "Entries", align: "text-center" },
  { key: "entries_stopped", label: "Stops", align: "text-center" },
  { key: "spx_close", label: "SPX", align: "text-right" },
  { key: "vix_open", label: "VIX", align: "text-right" },
];

export function DailySummaryTable({
  summaries,
  onDayClick,
}: {
  summaries: DaySummary[];
  onDayClick: (date: string) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const sorted = useMemo(() => {
    const copy = [...summaries];
    copy.sort((a, b) => {
      const aVal = a[sortKey] ?? 0;
      const bVal = b[sortKey] ?? 0;
      if (typeof aVal === "string" && typeof bVal === "string") {
        return sortDir === "asc"
          ? aVal.localeCompare(bVal)
          : bVal.localeCompare(aVal);
      }
      const aNum = Number(aVal);
      const bNum = Number(bVal);
      return sortDir === "asc" ? aNum - bNum : bNum - aNum;
    });
    return copy;
  }, [summaries, sortKey, sortDir]);

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "date" ? "desc" : "desc");
    }
  }

  return (
    <div className="bg-card rounded-lg border border-border-dim overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border-dim">
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  className={`${col.align} px-3 py-2 text-text-secondary font-semibold cursor-pointer hover:text-text-primary select-none transition-colors`}
                  onClick={() => handleSort(col.key)}
                >
                  <span className="inline-flex items-center gap-0.5">
                    {col.label}
                    {sortKey === col.key && (
                      sortDir === "asc" ? (
                        <ChevronUp size={12} className="text-text-primary" />
                      ) : (
                        <ChevronDown size={12} className="text-text-primary" />
                      )
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((day) => (
              <tr
                key={day.date}
                className="border-b border-border-dim/50 hover:bg-bg-elevated/50 cursor-pointer transition-colors"
                onClick={() => onDayClick(day.date)}
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
                  {day.spx_close?.toFixed(0) || "\u2014"}
                </td>
                <td className="px-3 py-1.5 text-right text-text-secondary">
                  {day.vix_open?.toFixed(1) || "\u2014"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
