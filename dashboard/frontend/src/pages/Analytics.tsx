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

  // ── Avg Credit by Entry Slot (E1–E5) ──
  const ENTRY_TIMES = ["11:15", "11:45", "12:15", "12:45", "13:15"];
  const creditBySlot = Array.from({ length: 5 }, (_, i) => {
    const entryNum = i + 1;
    const matching = entries.filter((e) => e.entry_number === entryNum);
    const totalCredit = matching.reduce((sum, e) => sum + (e.total_credit || 0), 0);
    return {
      slot: `E${entryNum} (${ENTRY_TIMES[i]})`,
      avgCredit: matching.length > 0 ? totalCredit / matching.length : 0,
      count: matching.length,
    };
  });

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

  // ── Stop Rate by Entry Slot ──
  const stopRateByEntry = Array.from({ length: 5 }, (_, i) => {
    const entryNum = i + 1;
    const matching = entries.filter((e) => e.entry_number === entryNum);
    const entryStops = stops.filter((s) => s.entry_number === entryNum);
    const callStops = entryStops.filter((s) => s.side === "call").length;
    const putStops = entryStops.filter((s) => s.side === "put").length;
    const total = matching.length;
    return {
      entry: `E${entryNum}`,
      callStopPct: total > 0 ? (callStops / total) * 100 : 0,
      putStopPct: total > 0 ? (putStops / total) * 100 : 0,
      total,
      callStops,
      putStops,
    };
  });

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
        {/* Avg Credit by Entry Slot */}
        <div className="bg-card rounded-lg border border-border-dim p-4">
          <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
            Avg Credit by Entry Slot
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

        {/* Stop Rate by Entry Slot */}
        <div className="bg-card rounded-lg border border-border-dim p-4">
          <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
            Stop Rate by Entry Slot
          </h3>
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
        </div>
      </div>
    </div>
  );
}
