import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { colors, pnlColor } from "../../lib/tradingColors";
import { formatPnL, formatDateShort } from "../../lib/formatters";

interface SimEntryResult {
  entry_number: number;
  actual_type: string;
  simulated_type: string;
  actual_pnl: number;
  simulated_pnl: number;
  actual_stopped: boolean;
  simulated_stopped: boolean;
  skipped: boolean;
  newly_included: boolean;
  note: string;
}

interface SimDayData {
  date: string;
  actual_net_pnl: number;
  simulated_net_pnl: number;
  delta_pnl: number;
  actual_entries: number;
  simulated_entries: number;
  actual_stops: number;
  simulated_stops: number;
  simulation_tier: number;
  entries: SimEntryResult[];
}

interface SimDayTableProps {
  days: SimDayData[];
}

function EntryDetail({ entries }: { entries: SimEntryResult[] }) {
  return (
    <tr>
      <td colSpan={7} className="p-0">
        <div className="bg-bg rounded mx-2 mb-2 p-2">
          <table className="w-full text-[10px]">
            <thead>
              <tr className="text-text-dim">
                <th className="text-left py-0.5 font-medium">#</th>
                <th className="text-left py-0.5 font-medium">Actual Type</th>
                <th className="text-left py-0.5 font-medium">Sim Type</th>
                <th className="text-right py-0.5 font-medium">Actual P&L</th>
                <th className="text-right py-0.5 font-medium">Sim P&L</th>
                <th className="text-left py-0.5 font-medium pl-3">Note</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.entry_number} className="border-t border-border-dim">
                  <td className="py-1 text-text-secondary">E{e.entry_number}</td>
                  <td className="py-1 text-text-secondary">{e.actual_type}</td>
                  <td
                    className="py-1"
                    style={{
                      color: e.skipped
                        ? colors.textDim
                        : e.simulated_type !== e.actual_type
                          ? colors.info
                          : colors.textSecondary,
                    }}
                  >
                    {e.simulated_type}
                  </td>
                  <td className="py-1 text-right font-mono" style={{ color: pnlColor(e.actual_pnl) }}>
                    {formatPnL(e.actual_pnl, 0)}
                  </td>
                  <td
                    className="py-1 text-right font-mono"
                    style={{ color: e.skipped ? colors.textDim : pnlColor(e.simulated_pnl) }}
                  >
                    {e.skipped ? "—" : formatPnL(e.simulated_pnl, 0)}
                  </td>
                  <td className="py-1 pl-3 text-text-dim italic">{e.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </td>
    </tr>
  );
}

export function SimDayTable({ days }: SimDayTableProps) {
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<"date" | "delta">("date");
  const [sortAsc, setSortAsc] = useState(true);

  const sorted = [...days].sort((a, b) => {
    const mult = sortAsc ? 1 : -1;
    if (sortKey === "date") return a.date.localeCompare(b.date) * mult;
    return (a.delta_pnl - b.delta_pnl) * mult;
  });

  const toggleSort = (key: "date" | "delta") => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(key === "date"); }
  };

  return (
    <div>
      <h3 className="label-upper mb-2">Per-Day Breakdown</h3>
      <div className="bg-card rounded-lg border border-border-dim overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-dim border-b border-border-dim">
                <th className="text-left py-2 px-3 font-medium w-8"></th>
                <th
                  className="text-left py-2 px-2 font-medium cursor-pointer hover:text-text-primary"
                  onClick={() => toggleSort("date")}
                >
                  Date {sortKey === "date" ? (sortAsc ? "↑" : "↓") : ""}
                </th>
                <th className="text-right py-2 px-2 font-medium">Actual</th>
                <th className="text-right py-2 px-2 font-medium">Simulated</th>
                <th
                  className="text-right py-2 px-2 font-medium cursor-pointer hover:text-text-primary"
                  onClick={() => toggleSort("delta")}
                >
                  Delta {sortKey === "delta" ? (sortAsc ? "↑" : "↓") : ""}
                </th>
                <th className="text-center py-2 px-2 font-medium">Stops</th>
                <th className="text-center py-2 px-2 font-medium">Tier</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((day) => {
                const expanded = expandedDate === day.date;
                return (
                  <tbody key={day.date}>
                    <tr
                      className="border-t border-border-dim cursor-pointer transition-colors hover:bg-bg-elevated"
                      onClick={() => setExpandedDate(expanded ? null : day.date)}
                      style={{
                        borderLeft: `3px solid ${
                          day.delta_pnl > 5 ? colors.profit :
                          day.delta_pnl < -5 ? colors.loss :
                          "transparent"
                        }`,
                      }}
                    >
                      <td className="py-2 px-3 text-text-dim">
                        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      </td>
                      <td className="py-2 px-2 text-text-primary font-medium">
                        {formatDateShort(day.date)}
                      </td>
                      <td className="py-2 px-2 text-right font-mono" style={{ color: pnlColor(day.actual_net_pnl) }}>
                        {formatPnL(day.actual_net_pnl, 0)}
                      </td>
                      <td className="py-2 px-2 text-right font-mono" style={{ color: pnlColor(day.simulated_net_pnl) }}>
                        {formatPnL(day.simulated_net_pnl, 0)}
                      </td>
                      <td className="py-2 px-2 text-right font-mono font-medium" style={{ color: pnlColor(day.delta_pnl) }}>
                        {formatPnL(day.delta_pnl, 0)}
                      </td>
                      <td className="py-2 px-2 text-center text-text-secondary">
                        {day.actual_stops}→{day.simulated_stops}
                      </td>
                      <td className="py-2 px-2 text-center">
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded"
                          style={{
                            backgroundColor: day.simulation_tier === 1 ? `${colors.info}20` : `${colors.textDim}20`,
                            color: day.simulation_tier === 1 ? colors.info : colors.textDim,
                          }}
                        >
                          {day.simulation_tier === 1 ? "Full" : "Est."}
                        </span>
                      </td>
                    </tr>
                    {expanded && <EntryDetail entries={day.entries} />}
                  </tbody>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
