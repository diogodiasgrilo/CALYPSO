import { useEffect, useState } from "react";
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

export function History() {
  const [summaries, setSummaries] = useState<DaySummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/metrics/daily?days=90")
      .then((r) => r.json())
      .then((data) => {
        setSummaries(data.summaries ?? []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-text-dim">
        Loading history...
      </div>
    );
  }

  // Calendar heat map
  const maxPnl = Math.max(...summaries.map((s) => Math.abs(s.net_pnl || 0)), 1);

  return (
    <div className="space-y-4">
      <h2 className="text-sm font-semibold text-text-primary">
        Trading History
      </h2>

      {/* Calendar Heat Map */}
      <div className="bg-card rounded-lg border border-border-dim p-4">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
          Daily P&L Calendar
        </h3>
        <div className="flex flex-wrap gap-1">
          {summaries
            .slice()
            .reverse()
            .map((day) => {
              const pnl = day.net_pnl || 0;
              const intensity = Math.min(Math.abs(pnl) / maxPnl, 1);
              const bgColor =
                pnl > 0
                  ? `rgba(126, 232, 199, ${0.15 + intensity * 0.6})`
                  : pnl < 0
                  ? `rgba(248, 81, 73, ${0.15 + intensity * 0.6})`
                  : `rgba(90, 100, 120, 0.2)`;

              return (
                <div
                  key={day.date}
                  className="w-8 h-8 rounded-sm flex items-center justify-center text-[9px] font-mono cursor-default"
                  style={{ backgroundColor: bgColor }}
                  title={`${day.date}: ${formatPnL(pnl)} | ${day.entries_placed} entries, ${day.entries_stopped} stops`}
                >
                  {new Date(day.date + "T12:00:00").getDate()}
                </div>
              );
            })}
        </div>
        <div className="flex items-center gap-3 mt-3 text-[10px] text-text-dim">
          <span>Loss</span>
          <div className="flex gap-0.5">
            {[0.7, 0.5, 0.3, 0.1].map((o) => (
              <div
                key={o}
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
                key={o}
                className="w-3 h-3 rounded-sm"
                style={{ backgroundColor: `rgba(126, 232, 199, ${o})` }}
              />
            ))}
          </div>
          <span>Profit</span>
        </div>
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
