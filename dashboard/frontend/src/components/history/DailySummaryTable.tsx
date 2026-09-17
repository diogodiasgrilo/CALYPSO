import { useMemo, useState } from "react";
import { ChevronUp, ChevronDown } from "lucide-react";
import { pnlColor, colors } from "../../lib/tradingColors";
import { formatPnL, formatDateShort } from "../../lib/formatters";
import type { DaySummary, SortKey, SortDir } from "./types";

/** ``intradayOnly`` columns describe entries OPENED and STOPPED within the
 *  session. A multi-day calendar books P&L on the day a position CLOSES, which
 *  is usually not a day it opened anything — so those days legitimately read
 *  "0 entries" beside a real P&L, and the column turns a correct row into one
 *  that looks broken (defect D13: D 6 rows, E 4, F 2). Dropped for such
 *  strategies rather than explained away. */
const COLUMNS: { key: SortKey; label: string; align: string; intradayOnly?: boolean }[] = [
  { key: "date", label: "Date", align: "text-left" },
  { key: "net_pnl", label: "Net P&L", align: "text-right" },
  { key: "entries_placed", label: "Entries", align: "text-center", intradayOnly: true },
  { key: "entries_stopped", label: "Stops", align: "text-center", intradayOnly: true },
  { key: "spx_close", label: "SPX", align: "text-right" },
  { key: "vix_open", label: "VIX", align: "text-right" },
];

export function DailySummaryTable({
  summaries,
  onDayClick,
  intraday = true,
}: {
  summaries: DaySummary[];
  onDayClick: (date: string) => void;
  /** False for a multi-day strategy — drops the per-session entry/stop columns.
   *  Defaults true so every existing caller is unchanged. */
  intraday?: boolean;
}) {
  const columns = useMemo(
    () => COLUMNS.filter((c) => intraday || !c.intradayOnly),
    [intraday],
  );
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
              {columns.map((col) => (
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
                  <span className="text-text-dim ml-1 text-3xs">
                    {day.day_of_week?.slice(0, 3)}
                  </span>
                </td>
                <td
                  className="px-3 py-1.5 text-right font-mono font-semibold"
                  style={{ color: pnlColor(day.net_pnl || 0) }}
                >
                  {formatPnL(day.net_pnl || 0)}
                </td>
                {intraday && (
                  <td className="px-3 py-1.5 text-center text-text-primary">
                    {day.entries_placed}
                  </td>
                )}
                {intraday && (
                  <td
                    className="px-3 py-1.5 text-center"
                    style={{
                      color:
                        (day.actual_stops ?? day.entries_stopped ?? 0) > 0
                          ? colors.loss
                          : colors.textPrimary,
                    }}
                  >
                    {day.actual_stops ?? day.entries_stopped ?? 0}
                  </td>
                )}
                <td className="px-3 py-1.5 text-right text-text-secondary">
                  {day.spx_close?.toFixed(0) || "\u2014"}
                </td>
                {/* D12: `?? 0` elsewhere turned a market holiday's absent VIX
                    into a literal "0.0", an impossible reading. Falsy means
                    MISSING here — show an em dash. */}
                <td className="px-3 py-1.5 text-right text-text-secondary">
                  {day.vix_open ? day.vix_open.toFixed(1) : "\u2014"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
