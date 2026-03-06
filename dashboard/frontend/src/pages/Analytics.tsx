import { useEffect, useState } from "react";
import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  CartesianGrid,
} from "recharts";
import { colors } from "../lib/tradingColors";
import { formatPnL } from "../lib/formatters";

interface TradeEntry {
  date: string;
  entry_number: number;
  entry_time: string;
  total_credit: number;
  trend_signal: string;
  entry_type: string;
  vix_at_entry: number;
  spx_at_entry: number;
  otm_distance_call: number;
  otm_distance_put: number;
}

interface TradeStop {
  date: string;
  entry_number: number;
  side: string;
}

interface DaySummary {
  date: string;
  net_pnl: number;
  vix_open: number;
  entries_placed: number;
  entries_stopped: number;
  day_of_week: string;
}

export function Analytics() {
  const [entries, setEntries] = useState<TradeEntry[]>([]);
  const [stops, setStops] = useState<TradeStop[]>([]);
  const [summaries, setSummaries] = useState<DaySummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch("/api/metrics/entries").then((r) => r.json()),
      fetch("/api/metrics/stops").then((r) => r.json()),
      fetch("/api/metrics/daily?days=365").then((r) => r.json()),
    ])
      .then(([entryData, stopData, summaryData]) => {
        setEntries(entryData.entries ?? []);
        setStops(stopData.stops ?? []);
        setSummaries(summaryData.summaries ?? []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-text-dim">
        Loading analytics...
      </div>
    );
  }

  // ── Helper: parse "HH:MM AM/PM ET" to minutes since midnight ──
  const SCHEDULED_SLOTS = [
    605, 635, 665, 675, 695, 705, 725, 735, 755, 765, 795,
  ]; // 10:05,10:35,11:05,11:15,11:35,11:45,12:05,12:15,12:35,12:45,13:15
  const SLOT_LABELS: Record<number, string> = {
    605: "10:05", 635: "10:35", 665: "11:05", 675: "11:15",
    695: "11:35", 705: "11:45", 725: "12:05", 735: "12:15",
    755: "12:35", 765: "12:45", 795: "13:15",
  };

  function parseEntryTimeToSlot(timeStr: string): number | null {
    if (!timeStr) return null;
    const m = timeStr.match(/(\d{1,2}):(\d{2})\s*(AM|PM)/i);
    if (!m) return null;
    let h = parseInt(m[1], 10);
    const min = parseInt(m[2], 10);
    const ampm = m[3].toUpperCase();
    if (ampm === "PM" && h !== 12) h += 12;
    if (ampm === "AM" && h === 12) h = 0;
    const totalMin = h * 60 + min;
    // Find nearest scheduled slot
    let best = SCHEDULED_SLOTS[0];
    let bestDist = Math.abs(totalMin - best);
    for (const s of SCHEDULED_SLOTS) {
      const d = Math.abs(totalMin - s);
      if (d < bestDist) { best = s; bestDist = d; }
    }
    return bestDist <= 10 ? best : null; // within 10 min of a known slot
  }

  // ── Avg Credit by Time Slot ──
  const creditMap = new Map<number, { total: number; count: number }>();
  entries.forEach((e) => {
    const slot = parseEntryTimeToSlot(e.entry_time);
    if (slot == null) return;
    const prev = creditMap.get(slot) ?? { total: 0, count: 0 };
    prev.total += e.total_credit || 0;
    prev.count += 1;
    creditMap.set(slot, prev);
  });
  const creditBySlot = [...creditMap.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([slot, data]) => ({
      slot: SLOT_LABELS[slot] ?? `${Math.floor(slot / 60)}:${String(slot % 60).padStart(2, "0")}`,
      avgCredit: data.count > 0 ? data.total / data.count : 0,
      count: data.count,
    }));

  // ── Day of Week Performance ──
  const dowMap = new Map<string, { total: number; count: number }>();
  summaries.forEach((s) => {
    const dow = s.day_of_week ?? "Unknown";
    const prev = dowMap.get(dow) ?? { total: 0, count: 0 };
    prev.total += s.net_pnl || 0;
    prev.count += 1;
    dowMap.set(dow, prev);
  });
  const dayOrder = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
  const dowData = dayOrder
    .filter((d) => dowMap.has(d))
    .map((d) => ({
      day: d.slice(0, 3),
      avgPnl: (dowMap.get(d)!.total / dowMap.get(d)!.count),
      count: dowMap.get(d)!.count,
    }));

  // ── VIX vs P&L Scatter ──
  const vixPnlData = summaries
    .filter((s) => s.vix_open && s.net_pnl != null)
    .map((s) => ({
      vix: s.vix_open,
      pnl: s.net_pnl,
    }));

  // ── Stop Rate by Time Slot ──
  // Build a lookup: date+entry_number → time slot
  const entrySlotLookup = new Map<string, number>();
  entries.forEach((e) => {
    const slot = parseEntryTimeToSlot(e.entry_time);
    if (slot != null) entrySlotLookup.set(`${e.date}_${e.entry_number}`, slot);
  });

  const stopRateMap = new Map<number, { total: number; callStops: number; putStops: number }>();
  // Count entries per slot
  entries.forEach((e) => {
    const slot = parseEntryTimeToSlot(e.entry_time);
    if (slot == null) return;
    const prev = stopRateMap.get(slot) ?? { total: 0, callStops: 0, putStops: 0 };
    prev.total += 1;
    stopRateMap.set(slot, prev);
  });
  // Count stops per slot (via entry lookup)
  stops.forEach((s) => {
    const slot = entrySlotLookup.get(`${s.date}_${s.entry_number}`);
    if (slot == null) return;
    const prev = stopRateMap.get(slot) ?? { total: 0, callStops: 0, putStops: 0 };
    if (s.side === "call") prev.callStops += 1;
    else prev.putStops += 1;
    stopRateMap.set(slot, prev);
  });

  const stopRateByEntry = [...stopRateMap.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([slot, data]) => ({
      entry: SLOT_LABELS[slot] ?? `${Math.floor(slot / 60)}:${String(slot % 60).padStart(2, "0")}`,
      callStopPct: data.total > 0 ? (data.callStops / data.total) * 100 : 0,
      putStopPct: data.total > 0 ? (data.putStops / data.total) * 100 : 0,
      total: data.total,
      callStops: data.callStops,
      putStops: data.putStops,
    }));

  const chartTooltipStyle = {
    backgroundColor: colors.bgElevated,
    border: `1px solid ${colors.border}`,
    borderRadius: 6,
    fontSize: 11,
    color: colors.textPrimary,
    boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
    padding: "8px 12px",
  };

  const chartTooltipLabelStyle = {
    color: colors.textPrimary,
    fontSize: 11,
    fontWeight: 600,
    marginBottom: 4,
  };

  const chartTooltipItemStyle = {
    color: colors.textPrimary,
    fontSize: 11,
  };

  const chartCursor = { fill: "rgba(126, 232, 199, 0.06)", stroke: colors.borderDim };

  return (
    <div className="space-y-4">
      <h2 className="text-sm font-semibold text-text-primary">Analytics</h2>

      <div className="grid grid-cols-2 gap-3 max-lg:grid-cols-1">
        {/* Avg Credit by Time Slot */}
        <div className="bg-card rounded-lg border border-border-dim p-4">
          <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
            Avg Credit by Time Slot
          </h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={creditBySlot}>
              <XAxis
                dataKey="slot"
                tick={{ fontSize: 9, fill: colors.textDim }}
                axisLine={{ stroke: colors.borderDim }}
              />
              <YAxis
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={false}
                tickFormatter={(v) => `$${v}`}
              />
              <Tooltip
                contentStyle={chartTooltipStyle}
                labelStyle={chartTooltipLabelStyle}
                itemStyle={chartTooltipItemStyle}
                cursor={chartCursor}
                formatter={(value: unknown, _name: unknown, props: { payload?: { count?: number } }) => {
                  const v = Number(value ?? 0);
                  const n = props?.payload?.count ?? 0;
                  return [`$${v.toFixed(2)} (${n} entries)`, "Avg Credit"];
                }}
              />
              <Bar dataKey="avgCredit" name="Avg Credit" fill={colors.profit} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Day of Week */}
        <div className="bg-card rounded-lg border border-border-dim p-4">
          <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
            Avg P&L by Day of Week
          </h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={dowData}>
              <XAxis
                dataKey="day"
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={{ stroke: colors.borderDim }}
              />
              <YAxis
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={false}
                tickFormatter={(v) => `$${v}`}
              />
              <Tooltip
                contentStyle={chartTooltipStyle}
                labelStyle={chartTooltipLabelStyle}
                itemStyle={chartTooltipItemStyle}
                cursor={chartCursor}
                formatter={(value: unknown) => [formatPnL(Number(value ?? 0)), "Avg P&L"]}
              />
              <Bar dataKey="avgPnl" radius={[3, 3, 0, 0]}>
                {dowData.map((d, i) => (
                  <Cell key={i} fill={d.avgPnl >= 0 ? colors.profit : colors.loss} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* VIX vs P&L Scatter */}
        <div className="bg-card rounded-lg border border-border-dim p-4">
          <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
            VIX vs Daily P&L
          </h3>
          <ResponsiveContainer width="100%" height={200}>
            <ScatterChart>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke={colors.borderDim}
              />
              <XAxis
                dataKey="vix"
                name="VIX"
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={{ stroke: colors.borderDim }}
              />
              <YAxis
                dataKey="pnl"
                name="P&L"
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={false}
                tickFormatter={(v) => `$${v}`}
              />
              <Tooltip
                contentStyle={chartTooltipStyle}
                labelStyle={chartTooltipLabelStyle}
                itemStyle={chartTooltipItemStyle}
                cursor={{ strokeDasharray: "3 3", stroke: colors.textDim }}
                formatter={(value: unknown, name: unknown) => {
                  const v = Number(value ?? 0);
                  const n = String(name ?? "");
                  return [n === "P&L" ? formatPnL(v) : v.toFixed(1), n];
                }}
              />
              <Scatter data={vixPnlData} fill={colors.info} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>

        {/* Stop Rate by Time Slot */}
        <div className="bg-card rounded-lg border border-border-dim p-4">
          <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
            Stop Rate by Time Slot
          </h3>
          {stops.length === 0 ? (
            <div className="flex items-center justify-center h-[200px] text-text-dim text-xs">
              No stops recorded yet — data will appear after stop events occur
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={stopRateByEntry}>
                <XAxis
                  dataKey="entry"
                  tick={{ fontSize: 10, fill: colors.textDim }}
                  axisLine={{ stroke: colors.borderDim }}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: colors.textDim }}
                  axisLine={false}
                  tickFormatter={(v) => `${v}%`}
                  domain={[0, 100]}
                />
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  labelStyle={chartTooltipLabelStyle}
                  itemStyle={chartTooltipItemStyle}
                  cursor={chartCursor}
                  formatter={(value: unknown, name: unknown, props: { payload?: { total?: number; callStops?: number; putStops?: number } }) => {
                    const v = Number(value ?? 0);
                    const p = props?.payload;
                    const label = String(name ?? "");
                    return [`${v.toFixed(0)}% (${label === "Call Stops" ? p?.callStops : p?.putStops}/${p?.total})`, label];
                  }}
                />
                <Bar dataKey="callStopPct" name="Call Stops" fill={colors.warning} radius={[3, 3, 0, 0]} stackId="stops" />
                <Bar dataKey="putStopPct" name="Put Stops" fill={colors.loss} radius={[3, 3, 0, 0]} stackId="stops" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
}
