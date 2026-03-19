import { colors } from "../../lib/tradingColors";
import { formatPnL } from "../../lib/formatters";
import type { DayEntry, DayStop } from "./types";

/** Format entry time from various formats to "HH:MM" or "HH:MM:SS". */
function fmtTime(ts: string): string {
  if (!ts) return "\u2014";
  // "10:15:32 AM ET" or "10:15 AM ET" → "10:15:32" or "10:15"
  const ampm = ts.match(/(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)/i);
  if (ampm) {
    let h = parseInt(ampm[1], 10);
    const min = ampm[2];
    const sec = ampm[3]; // may be undefined
    const period = ampm[4].toUpperCase();
    if (period === "PM" && h !== 12) h += 12;
    if (period === "AM" && h === 12) h = 0;
    const base = `${String(h).padStart(2, "0")}:${min}`;
    return sec ? `${base}:${sec}` : base;
  }
  // "2026-03-06T11:15:32-05:00" or "2026-03-06 11:15:32"
  const iso = ts.match(/(\d{2}:\d{2}(?::\d{2})?)/);
  return iso ? iso[1] : "\u2014";
}

function entryTypeBadge(type: string) {
  if (type === "call_only") {
    return (
      <span className="inline-block px-1.5 py-0.5 rounded text-[9px] font-semibold bg-blue-500/20 text-blue-300">
        CALL
      </span>
    );
  }
  if (type === "put_only") {
    return (
      <span className="inline-block px-1.5 py-0.5 rounded text-[9px] font-semibold bg-purple-500/20 text-purple-300">
        PUT
      </span>
    );
  }
  return (
    <span className="inline-block px-1.5 py-0.5 rounded text-[9px] font-semibold bg-teal-500/20 text-teal-300">
      IC
    </span>
  );
}

function entryStatus(
  entry: DayEntry,
  stopMap: Map<number, DayStop[]>
) {
  const entryStops = stopMap.get(entry.entry_number) ?? [];
  if (entryStops.length === 0) {
    return (
      <span style={{ color: colors.profit }} className="font-semibold">
        Expired
      </span>
    );
  }
  const sides = entryStops.map((s) => s.side).join("+");
  return (
    <span style={{ color: colors.loss }} className="font-semibold">
      Stopped ({sides})
    </span>
  );
}

export function DayDetailEntries({
  entries,
  stops,
}: {
  entries: DayEntry[];
  stops: DayStop[];
}) {
  // Build stop lookup
  const stopMap = new Map<number, DayStop[]>();
  for (const s of stops) {
    if (!stopMap.has(s.entry_number)) stopMap.set(s.entry_number, []);
    stopMap.get(s.entry_number)!.push(s);
  }

  if (entries.length === 0) {
    return (
      <div className="flex items-center justify-center h-20 text-text-dim text-xs rounded-lg border border-border-dim">
        No entry data available for this date
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h4 className="text-[11px] font-semibold text-text-secondary uppercase tracking-wider">
        Entries ({entries.length})
      </h4>
      <div className="overflow-x-auto rounded-lg border border-border-dim">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border-dim bg-bg-elevated/30">
              <th className="text-left px-2 py-1.5 text-text-secondary font-semibold">#</th>
              <th className="text-left px-2 py-1.5 text-text-secondary font-semibold">Time</th>
              <th className="text-center px-2 py-1.5 text-text-secondary font-semibold">Type</th>
              <th className="text-left px-2 py-1.5 text-text-secondary font-semibold">Signal</th>
              <th className="text-right px-2 py-1.5 text-text-secondary font-semibold">Call</th>
              <th className="text-right px-2 py-1.5 text-text-secondary font-semibold">Put</th>
              <th className="text-right px-2 py-1.5 text-text-secondary font-semibold">Credit</th>
              <th className="text-center px-2 py-1.5 text-text-secondary font-semibold">OTM</th>
              <th className="text-left px-2 py-1.5 text-text-secondary font-semibold">Status</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr
                key={e.entry_number}
                className="border-b border-border-dim/50 hover:bg-bg-elevated/20"
              >
                <td className="px-2 py-1.5 font-mono font-semibold text-text-primary">
                  E{e.entry_number}
                </td>
                <td className="px-2 py-1.5 font-mono text-text-primary">
                  {fmtTime(e.entry_time)}
                </td>
                <td className="px-2 py-1.5 text-center">
                  {entryTypeBadge(e.entry_type)}
                </td>
                <td className="px-2 py-1.5 text-text-secondary text-[10px]">
                  {e.override_reason || e.trend_signal || "\u2014"}
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-text-secondary">
                  {e.entry_type === "PUT" || e.entry_type === "put_only"
                    ? "\u2014"
                    : e.short_call_strike > 0
                    ? `${e.short_call_strike}/${e.long_call_strike}`
                    : "\u2014"}
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-text-secondary">
                  {e.entry_type === "CALL" || e.entry_type === "call_only"
                    ? "\u2014"
                    : e.short_put_strike > 0
                    ? `${e.short_put_strike}/${e.long_put_strike}`
                    : "\u2014"}
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-text-primary">
                  ${(e.total_credit || 0).toFixed(2)}
                </td>
                <td className="px-2 py-1.5 text-center font-mono text-text-dim text-[10px]">
                  {e.otm_distance_call > 0 || e.otm_distance_put > 0
                    ? `${e.otm_distance_call?.toFixed(0) || "\u2014"}/${e.otm_distance_put?.toFixed(0) || "\u2014"}`
                    : "\u2014"}
                </td>
                <td className="px-2 py-1.5">
                  {entryStatus(e, stopMap)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Stops detail section */}
      {stops.length > 0 && (
        <>
          <h4 className="text-[11px] font-semibold text-text-secondary uppercase tracking-wider mt-4">
            Stop Losses ({stops.length})
          </h4>
          <div className="overflow-x-auto rounded-lg border border-border-dim">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border-dim bg-bg-elevated/30">
                  <th className="text-left px-2 py-1.5 text-text-secondary font-semibold">Entry</th>
                  <th className="text-left px-2 py-1.5 text-text-secondary font-semibold">Side</th>
                  <th className="text-left px-2 py-1.5 text-text-secondary font-semibold">Time</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary font-semibold">SPX</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary font-semibold">Debit</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary font-semibold">Net P&L</th>
                </tr>
              </thead>
              <tbody>
                {stops.map((s, i) => (
                  <tr
                    key={i}
                    className="border-b border-border-dim/50 hover:bg-bg-elevated/20"
                  >
                    <td className="px-2 py-1.5 font-mono font-semibold text-text-primary">
                      E{s.entry_number}
                    </td>
                    <td className="px-2 py-1.5">
                      <span
                        className="font-semibold"
                        style={{
                          color: s.side === "call" ? colors.info : colors.warning,
                        }}
                      >
                        {s.side === "call" ? "Call" : "Put"}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 font-mono text-text-primary">
                      {fmtTime(s.stop_time)}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-text-secondary">
                      {s.spx_at_stop?.toFixed(0) || "\u2014"}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-text-secondary">
                      ${(s.actual_debit || 0).toFixed(2)}
                    </td>
                    <td
                      className="px-2 py-1.5 text-right font-mono font-semibold"
                      style={{ color: colors.loss }}
                    >
                      {formatPnL(s.net_pnl || 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
