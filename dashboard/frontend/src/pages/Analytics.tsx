import { useEffect, useState, useMemo } from "react";
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
  AreaChart,
  Area,
  LineChart,
  Line,
  PieChart,
  Pie,
} from "recharts";
import { colors } from "../lib/tradingColors";
import { formatPnL } from "../lib/formatters";
import { EquityCurve } from "../components/pnl/EquityCurve";
import { CorrelationHeatmap } from "../components/market/CorrelationHeatmap";
import { Download } from "lucide-react";
import { exportEntriesCSV } from "../lib/exportUtils";

// ── Interfaces ──────────────────────────────────────────────────────────────

interface TradeEntry {
  date: string;
  entry_number: number;
  entry_time: string;
  total_credit: number;
  trend_signal: string;
  entry_type: string;
  override_reason: string;
  vix_at_entry: number;
  spx_at_entry: number;
  otm_distance_call: number;
  otm_distance_put: number;
}

interface TradeStop {
  date: string;
  entry_number: number;
  side: string;
  actual_debit: number;
  trigger_level: number;
  net_pnl: number;
  confirmation_seconds: number;
  breach_recoveries: number;
}

interface DaySummary {
  date: string;
  net_pnl: number;
  gross_pnl: number;
  commission: number;
  vix_open: number;
  entries_placed: number;
  entries_stopped: number;
  entries_expired: number;
  day_of_week: string;
  day_type: string;
  day_range: number;
  spx_open: number;
  spx_close: number;
  spx_high: number;
  spx_low: number;
}

// ── Constants ───────────────────────────────────────────────────────────────

type AnalyticsTab = "performance" | "entries" | "stops" | "market";

const TAB_LABELS: Record<AnalyticsTab, string> = {
  performance: "Performance",
  entries: "Entries",
  stops: "Stops",
  market: "Market",
};

const SCHEDULED_SLOTS = [
  615, 645, 675, 840,
]; // 10:15,10:45,11:15,14:00 (canonical slots; E#1 dropped at all VIX levels since 2026-04-17)

const SLOT_LABELS: Record<number, string> = {
  615: "10:15", 645: "10:45", 675: "11:15", 840: "14:00",
};

// ── Shared Styles ───────────────────────────────────────────────────────────

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

// ── Helpers ─────────────────────────────────────────────────────────────────

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
  let best = SCHEDULED_SLOTS[0];
  let bestDist = Math.abs(totalMin - best);
  for (const s of SCHEDULED_SLOTS) {
    const d = Math.abs(totalMin - s);
    if (d < bestDist) { best = s; bestDist = d; }
  }
  return bestDist <= 10 ? best : null;
}

/** Chart card wrapper */
function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-lg border border-border-dim p-4">
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
        {title}
      </h3>
      {children}
    </div>
  );
}

/** Empty state for charts with no data */
function EmptyChart({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center h-[200px] text-text-dim text-xs">
      {message}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ══════════════════════════════════════════════════════════════════════════════

export function Analytics() {
  const [entries, setEntries] = useState<TradeEntry[]>([]);
  const [stops, setStops] = useState<TradeStop[]>([]);
  const [summaries, setSummaries] = useState<DaySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<AnalyticsTab>("performance");

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

  // ── Computed data (shared across tabs) ──

  // Stop lookup: "date_entryNumber" → list of stops
  const stopLookup = useMemo(() => {
    const map = new Map<string, TradeStop[]>();
    stops.forEach((s) => {
      const key = `${s.date}_${s.entry_number}`;
      const arr = map.get(key) ?? [];
      arr.push(s);
      map.set(key, arr);
    });
    return map;
  }, [stops]);

  // Sorted summaries (ascending by date)
  const sortedSummaries = useMemo(
    () => [...summaries].sort((a, b) => a.date.localeCompare(b.date)),
    [summaries],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-text-dim">
        Loading analytics...
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header + Tab Bar */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text-primary">Analytics</h2>
      </div>

      {/* Tab selector */}
      <div className="flex gap-1 bg-bg-deep rounded-lg p-1">
        {(Object.keys(TAB_LABELS) as AnalyticsTab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`flex-1 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider rounded-md transition-colors ${
              activeTab === tab
                ? "bg-bg-elevated text-text-primary shadow-sm"
                : "text-text-dim hover:text-text-secondary"
            }`}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {/* Export button */}
      <div className="flex justify-end">
        <button
          onClick={() => {
            if (activeTab === "entries" || activeTab === "stops") {
              exportEntriesCSV((activeTab === "entries" ? entries : stops) as unknown as Record<string, unknown>[]);
            } else {
              exportEntriesCSV(summaries as unknown as Record<string, unknown>[]);
            }
          }}
          className="flex items-center gap-1.5 text-xs text-text-secondary hover:text-text-primary transition-colors px-2 py-1 rounded border border-border-dim hover:border-text-dim"
        >
          <Download size={12} />
          Export CSV
        </button>
      </div>

      {/* Tab content */}
      <div className="grid grid-cols-2 gap-3 max-lg:grid-cols-1">
        {activeTab === "performance" && (
          <>
            <PerformanceTab summaries={sortedSummaries} />
            <div className="col-span-2 max-lg:col-span-1">
              <EquityCurve dailySummaries={sortedSummaries} />
            </div>
          </>
        )}
        {activeTab === "entries" && (
          <EntriesTab entries={entries} stopLookup={stopLookup} />
        )}
        {activeTab === "stops" && (
          <StopsTab entries={entries} stops={stops} />
        )}
        {activeTab === "market" && (
          <>
            <MarketTab
              summaries={sortedSummaries}
              entries={entries}
              stopLookup={stopLookup}
            />
            <div className="col-span-2 max-lg:col-span-1">
              <CorrelationHeatmap data={sortedSummaries} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// PERFORMANCE TAB
// ══════════════════════════════════════════════════════════════════════════════

function PerformanceTab({ summaries }: { summaries: DaySummary[] }) {
  // 1. Cumulative P&L curve
  const cumulativeData = useMemo(() => {
    let running = 0;
    return summaries.map((s) => {
      running += s.net_pnl || 0;
      return { date: s.date.slice(5), cumPnl: running, rawDate: s.date };
    });
  }, [summaries]);

  // 2. Daily P&L histogram ($50 buckets)
  const histogramData = useMemo(() => {
    if (summaries.length === 0) return [];
    const bucketSize = 50;
    const pnls = summaries.map((s) => s.net_pnl || 0);
    const minPnl = Math.floor(Math.min(...pnls) / bucketSize) * bucketSize;
    const maxPnl = Math.ceil(Math.max(...pnls) / bucketSize) * bucketSize;
    const buckets = new Map<number, number>();
    for (let b = minPnl; b <= maxPnl; b += bucketSize) {
      buckets.set(b, 0);
    }
    pnls.forEach((p) => {
      const b = Math.floor(p / bucketSize) * bucketSize;
      buckets.set(b, (buckets.get(b) ?? 0) + 1);
    });
    return [...buckets.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([bucket, count]) => ({
        range: `$${bucket}`,
        count,
        bucket,
      }));
  }, [summaries]);

  // 3. Rolling 10-day win rate
  const rollingWinRate = useMemo(() => {
    if (summaries.length < 2) return [];
    const windowSize = Math.min(10, summaries.length);
    return summaries.map((s, i) => {
      const start = Math.max(0, i - windowSize + 1);
      const window = summaries.slice(start, i + 1);
      const wins = window.filter((d) => (d.net_pnl || 0) > 0).length;
      return {
        date: s.date.slice(5),
        winRate: (wins / window.length) * 100,
        rawDate: s.date,
      };
    });
  }, [summaries]);

  // 4. Day of week avg P&L
  const dowData = useMemo(() => {
    const dowMap = new Map<string, { total: number; count: number }>();
    summaries.forEach((s) => {
      const dow = s.day_of_week ?? "Unknown";
      const prev = dowMap.get(dow) ?? { total: 0, count: 0 };
      prev.total += s.net_pnl || 0;
      prev.count += 1;
      dowMap.set(dow, prev);
    });
    const dayOrder = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
    return dayOrder
      .filter((d) => dowMap.has(d))
      .map((d) => ({
        day: d.slice(0, 3),
        avgPnl: dowMap.get(d)!.total / dowMap.get(d)!.count,
        count: dowMap.get(d)!.count,
      }));
  }, [summaries]);

  return (
    <>
      {/* Cumulative P&L */}
      <ChartCard title="Cumulative P&L">
        {cumulativeData.length === 0 ? (
          <EmptyChart message="No daily data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={cumulativeData}>
              <defs>
                <linearGradient id="cumPnlGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={colors.profit} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={colors.profit} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke={colors.borderDim} />
              <XAxis
                dataKey="date"
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
                formatter={(value: unknown) => [formatPnL(Number(value ?? 0)), "Cumulative"]}
                labelFormatter={(label) => {
                  const item = cumulativeData.find((d) => d.date === label);
                  return item?.rawDate ?? label;
                }}
              />
              <Area
                type="monotone"
                dataKey="cumPnl"
                stroke={colors.profit}
                fill="url(#cumPnlGrad)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* Daily P&L Histogram */}
      <ChartCard title="Daily P&L Distribution">
        {histogramData.length === 0 ? (
          <EmptyChart message="No daily data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={histogramData}>
              <XAxis
                dataKey="range"
                tick={{ fontSize: 9, fill: colors.textDim }}
                axisLine={{ stroke: colors.borderDim }}
              />
              <YAxis
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={false}
                allowDecimals={false}
              />
              <Tooltip
                contentStyle={chartTooltipStyle}
                labelStyle={chartTooltipLabelStyle}
                itemStyle={chartTooltipItemStyle}
                cursor={chartCursor}
                formatter={(value: unknown) => [`${Number(value ?? 0)} days`, "Count"]}
              />
              <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                {histogramData.map((d, i) => (
                  <Cell key={i} fill={d.bucket >= 0 ? colors.profit : colors.loss} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* Rolling Win Rate */}
      <ChartCard title="Rolling Win Rate (10-day)">
        {rollingWinRate.length === 0 ? (
          <EmptyChart message="Need at least 2 trading days" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={rollingWinRate}>
              <CartesianGrid strokeDasharray="3 3" stroke={colors.borderDim} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 9, fill: colors.textDim }}
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
                formatter={(value: unknown) => [`${Number(value ?? 0).toFixed(0)}%`, "Win Rate"]}
                labelFormatter={(label) => {
                  const item = rollingWinRate.find((d) => d.date === label);
                  return item?.rawDate ?? label;
                }}
              />
              <Line
                type="monotone"
                dataKey="winRate"
                stroke={colors.info}
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* Day of Week */}
      <ChartCard title="Avg P&L by Day of Week">
        {dowData.length === 0 ? (
          <EmptyChart message="No daily data yet" />
        ) : (
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
        )}
      </ChartCard>
    </>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// ENTRIES TAB
// ══════════════════════════════════════════════════════════════════════════════

function EntriesTab({
  entries,
  stopLookup,
}: {
  entries: TradeEntry[];
  stopLookup: Map<string, TradeStop[]>;
}) {
  // 1. Credit by time slot
  const creditBySlot = useMemo(() => {
    const map = new Map<number, { total: number; count: number }>();
    entries.forEach((e) => {
      const slot = parseEntryTimeToSlot(e.entry_time);
      if (slot == null) return;
      const prev = map.get(slot) ?? { total: 0, count: 0 };
      prev.total += e.total_credit || 0;
      prev.count += 1;
      map.set(slot, prev);
    });
    return [...map.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([slot, data]) => ({
        slot: SLOT_LABELS[slot] ?? `${Math.floor(slot / 60)}:${String(slot % 60).padStart(2, "0")}`,
        avgCredit: data.count > 0 ? data.total / data.count : 0,
        count: data.count,
      }));
  }, [entries]);

  // 2. Entry type breakdown (pie)
  const entryTypeData = useMemo(() => {
    const counts: Record<string, number> = {};
    entries.forEach((e) => {
      const type = (e.entry_type || "").toLowerCase();
      const label =
        type.includes("iron") || type === "full" ? "Iron Condor"
        : type.includes("put") ? "Put Spread"
        : type.includes("call") ? "Call Spread"
        : e.entry_type || "Other";
      counts[label] = (counts[label] ?? 0) + 1;
    });
    const pieColors: Record<string, string> = {
      "Iron Condor": colors.info,
      "Call Spread": colors.warning,
      "Put Spread": colors.profit,
    };
    return Object.entries(counts).map(([name, value]) => ({
      name,
      value,
      fill: pieColors[name] ?? colors.textDim,
    }));
  }, [entries]);

  // 3. P&L by entry number
  const pnlByEntry = useMemo(() => {
    const map = new Map<number, { totalPnl: number; count: number }>();
    entries.forEach((e) => {
      const entryStops = stopLookup.get(`${e.date}_${e.entry_number}`) ?? [];
      // P&L = credit - sum of actual debits from stops
      let pnl = e.total_credit || 0;
      entryStops.forEach((s) => {
        pnl -= s.actual_debit || 0;
      });
      const prev = map.get(e.entry_number) ?? { totalPnl: 0, count: 0 };
      prev.totalPnl += pnl;
      prev.count += 1;
      map.set(e.entry_number, prev);
    });
    return [...map.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([num, data]) => ({
        entry: `E${num}`,
        avgPnl: data.count > 0 ? data.totalPnl / data.count : 0,
        count: data.count,
      }));
  }, [entries, stopLookup]);

  // 4. OTM distance: survival rate by OTM bucket
  const otmBucketData = useMemo(() => {
    const bucketSize = 5; // 5-point OTM buckets
    const buckets = new Map<number, { total: number; stopped: number }>();

    entries.forEach((e) => {
      const entryStops = stopLookup.get(`${e.date}_${e.entry_number}`) ?? [];
      const callStopped = entryStops.some((s) => s.side === "call");
      const putStopped = entryStops.some((s) => s.side === "put");

      // Process call side
      if (e.otm_distance_call != null && e.otm_distance_call > 0) {
        const b = Math.round(e.otm_distance_call / bucketSize) * bucketSize;
        const prev = buckets.get(b) ?? { total: 0, stopped: 0 };
        prev.total += 1;
        if (callStopped) prev.stopped += 1;
        buckets.set(b, prev);
      }
      // Process put side
      if (e.otm_distance_put != null && e.otm_distance_put > 0) {
        const b = Math.round(e.otm_distance_put / bucketSize) * bucketSize;
        const prev = buckets.get(b) ?? { total: 0, stopped: 0 };
        prev.total += 1;
        if (putStopped) prev.stopped += 1;
        buckets.set(b, prev);
      }
    });

    return [...buckets.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([otm, data]) => ({
        otm: `${otm}pt`,
        survivalRate: data.total > 0 ? ((data.total - data.stopped) / data.total) * 100 : 100,
        total: data.total,
        stopped: data.stopped,
      }));
  }, [entries, stopLookup]);

  return (
    <>
      {/* Credit by Time Slot */}
      <ChartCard title="Avg Credit by Time Slot">
        {creditBySlot.length === 0 ? (
          <EmptyChart message="No entry data yet" />
        ) : (
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
        )}
      </ChartCard>

      {/* Entry Type Breakdown */}
      <ChartCard title="Entry Type Breakdown">
        {entryTypeData.length === 0 ? (
          <EmptyChart message="No entry data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie
                data={entryTypeData}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={80}
                dataKey="value"
                nameKey="name"
                label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}
                labelLine={false}
              >
                {entryTypeData.map((d, i) => (
                  <Cell key={i} fill={d.fill} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={chartTooltipStyle}
                itemStyle={chartTooltipItemStyle}
                formatter={(value: unknown, name: unknown) => [`${value} entries`, String(name)]}
              />
            </PieChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* P&L by Entry Number */}
      <ChartCard title="Avg P&L by Entry Number">
        {pnlByEntry.length === 0 ? (
          <EmptyChart message="No entry data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={pnlByEntry}>
              <XAxis
                dataKey="entry"
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
                formatter={(value: unknown, _name: unknown, props: { payload?: { count?: number } }) => {
                  const n = props?.payload?.count ?? 0;
                  return [`${formatPnL(Number(value ?? 0))} (${n} entries)`, "Avg P&L"];
                }}
              />
              <Bar dataKey="avgPnl" radius={[3, 3, 0, 0]}>
                {pnlByEntry.map((d, i) => (
                  <Cell key={i} fill={d.avgPnl >= 0 ? colors.profit : colors.loss} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* OTM Distance Survival Rate */}
      <ChartCard title="Survival Rate by OTM Distance">
        {otmBucketData.length === 0 ? (
          <EmptyChart message="No OTM distance data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={otmBucketData}>
              <XAxis
                dataKey="otm"
                tick={{ fontSize: 9, fill: colors.textDim }}
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
                formatter={(value: unknown, _name: unknown, props: { payload?: { total?: number; stopped?: number } }) => {
                  const v = Number(value ?? 0);
                  const p = props?.payload;
                  return [`${v.toFixed(0)}% (${(p?.total ?? 0) - (p?.stopped ?? 0)}/${p?.total} survived)`, "Survival"];
                }}
              />
              <Bar dataKey="survivalRate" radius={[3, 3, 0, 0]}>
                {otmBucketData.map((d, i) => (
                  <Cell key={i} fill={d.survivalRate >= 70 ? colors.profit : d.survivalRate >= 40 ? colors.warning : colors.loss} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>
    </>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// STOPS TAB
// ══════════════════════════════════════════════════════════════════════════════

function StopsTab({
  entries,
  stops,
}: {
  entries: TradeEntry[];
  stops: TradeStop[];
}) {
  // 1. Stop rate by time slot
  const stopRateByEntry = useMemo(() => {
    const entrySlotLookup = new Map<string, number>();
    entries.forEach((e) => {
      const slot = parseEntryTimeToSlot(e.entry_time);
      if (slot != null) entrySlotLookup.set(`${e.date}_${e.entry_number}`, slot);
    });

    const map = new Map<number, { total: number; callStops: number; putStops: number }>();
    entries.forEach((e) => {
      const slot = parseEntryTimeToSlot(e.entry_time);
      if (slot == null) return;
      const prev = map.get(slot) ?? { total: 0, callStops: 0, putStops: 0 };
      prev.total += 1;
      map.set(slot, prev);
    });
    stops.forEach((s) => {
      const slot = entrySlotLookup.get(`${s.date}_${s.entry_number}`);
      if (slot == null) return;
      const prev = map.get(slot) ?? { total: 0, callStops: 0, putStops: 0 };
      if (s.side === "call") prev.callStops += 1;
      else prev.putStops += 1;
      map.set(slot, prev);
    });

    return [...map.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([slot, data]) => ({
        entry: SLOT_LABELS[slot] ?? `${Math.floor(slot / 60)}:${String(slot % 60).padStart(2, "0")}`,
        callStopPct: data.total > 0 ? (data.callStops / data.total) * 100 : 0,
        putStopPct: data.total > 0 ? (data.putStops / data.total) * 100 : 0,
        total: data.total,
        callStops: data.callStops,
        putStops: data.putStops,
      }));
  }, [entries, stops]);

  // 2. Call vs Put ratio (pie)
  const callPutRatio = useMemo(() => {
    let callCount = 0;
    let putCount = 0;
    stops.forEach((s) => {
      if (s.side === "call") callCount++;
      else putCount++;
    });
    if (callCount === 0 && putCount === 0) return [];
    return [
      { name: "Call Stops", value: callCount, fill: colors.warning },
      { name: "Put Stops", value: putCount, fill: colors.loss },
    ];
  }, [stops]);

  // 3. Stop slippage distribution (histogram with $10 buckets)
  const slippageData = useMemo(() => {
    const rawSlippages = stops
      .filter((s) => s.actual_debit > 0 && s.trigger_level > 0)
      .map((s) => s.actual_debit - s.trigger_level);
    if (rawSlippages.length === 0) return [];

    const bucketSize = 10;
    const minSlip = Math.floor(Math.min(...rawSlippages) / bucketSize) * bucketSize;
    const maxSlip = Math.ceil(Math.max(...rawSlippages) / bucketSize) * bucketSize;
    const buckets = new Map<number, number>();
    for (let b = minSlip; b <= maxSlip; b += bucketSize) {
      buckets.set(b, 0);
    }
    rawSlippages.forEach((s) => {
      const b = Math.floor(s / bucketSize) * bucketSize;
      buckets.set(b, (buckets.get(b) ?? 0) + 1);
    });
    return [...buckets.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([bucket, count]) => ({
        range: `$${bucket}`,
        count,
        bucket,
      }));
  }, [stops]);

  // 4. Stops per day distribution (histogram)
  const stopsPerDayData = useMemo(() => {
    const dayStops = new Map<string, number>();
    stops.forEach((s) => {
      dayStops.set(s.date, (dayStops.get(s.date) ?? 0) + 1);
    });
    // Count how many days had 0, 1, 2, 3+ stops
    const counts = new Map<number, number>();
    // Include days with 0 stops
    const allDates = new Set([...dayStops.keys()]);
    entries.forEach((e) => allDates.add(e.date));
    allDates.forEach((date) => {
      const n = dayStops.get(date) ?? 0;
      const bucket = Math.min(n, 6); // cap at 6+
      counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
    });
    return [...counts.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([stopCount, dayCount]) => ({
        stops: stopCount >= 6 ? "6+" : String(stopCount),
        days: dayCount,
        stopCount,
      }));
  }, [stops, entries]);

  return (
    <>
      {/* Stop Rate by Time Slot */}
      <ChartCard title="Stop Rate by Time Slot">
        {stops.length === 0 ? (
          <EmptyChart message="No stops recorded yet" />
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
      </ChartCard>

      {/* Call vs Put Stop Ratio */}
      <ChartCard title="Call vs Put Stop Ratio">
        {callPutRatio.length === 0 ? (
          <EmptyChart message="No stops recorded yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie
                data={callPutRatio}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={80}
                dataKey="value"
                nameKey="name"
                label={({ name, value }) => `${name}: ${value}`}
                labelLine={false}
              >
                {callPutRatio.map((d, i) => (
                  <Cell key={i} fill={d.fill} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={chartTooltipStyle}
                itemStyle={chartTooltipItemStyle}
                formatter={(value: unknown, name: unknown) => [`${value} stops`, String(name)]}
              />
            </PieChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* Stop Slippage Distribution */}
      <ChartCard title="Stop Slippage Distribution">
        {slippageData.length === 0 ? (
          <EmptyChart message="No stop debit data available" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={slippageData}>
              <XAxis
                dataKey="range"
                tick={{ fontSize: 9, fill: colors.textDim }}
                axisLine={{ stroke: colors.borderDim }}
              />
              <YAxis
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={false}
                allowDecimals={false}
              />
              <Tooltip
                contentStyle={chartTooltipStyle}
                labelStyle={chartTooltipLabelStyle}
                itemStyle={chartTooltipItemStyle}
                cursor={chartCursor}
                formatter={(value: unknown) => [`${Number(value ?? 0)} stops`, "Count"]}
              />
              <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                {slippageData.map((d, i) => (
                  <Cell key={i} fill={d.bucket > 0 ? colors.loss : d.bucket < 0 ? colors.profit : colors.textDim} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* Stops Per Day */}
      <ChartCard title="Stops Per Day Distribution">
        {stopsPerDayData.length === 0 ? (
          <EmptyChart message="No stop data available" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={stopsPerDayData}>
              <XAxis
                dataKey="stops"
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={{ stroke: colors.borderDim }}
                label={{ value: "# Stops", position: "insideBottom", offset: -2, fontSize: 9, fill: colors.textDim }}
              />
              <YAxis
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={false}
                allowDecimals={false}
                label={{ value: "Days", angle: -90, position: "insideLeft", fontSize: 9, fill: colors.textDim }}
              />
              <Tooltip
                contentStyle={chartTooltipStyle}
                labelStyle={chartTooltipLabelStyle}
                itemStyle={chartTooltipItemStyle}
                cursor={chartCursor}
                formatter={(value: unknown) => [`${value} days`, "Count"]}
              />
              <Bar dataKey="days" radius={[3, 3, 0, 0]}>
                {stopsPerDayData.map((d, i) => (
                  <Cell
                    key={i}
                    fill={
                      d.stopCount === 0 ? colors.profit
                      : d.stopCount <= 2 ? colors.warning
                      : colors.loss
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>
    </>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// MARKET TAB
// ══════════════════════════════════════════════════════════════════════════════

function MarketTab({
  summaries,
  entries,
  stopLookup,
}: {
  summaries: DaySummary[];
  entries: TradeEntry[];
  stopLookup: Map<string, TradeStop[]>;
}) {
  // 1. VIX vs P&L scatter
  const vixPnlData = useMemo(
    () =>
      summaries
        .filter((s) => s.vix_open && s.net_pnl != null)
        .map((s) => ({ vix: s.vix_open, pnl: s.net_pnl })),
    [summaries],
  );

  // 2. Day Range vs P&L scatter
  const rangePnlData = useMemo(
    () =>
      summaries
        .filter((s) => s.day_range > 0 && s.net_pnl != null)
        .map((s) => ({ range: s.day_range, pnl: s.net_pnl })),
    [summaries],
  );

  // 3. P&L by market direction (computed from SPX open/close)
  const directionData = useMemo(() => {
    const map = new Map<string, { total: number; count: number }>();
    summaries.forEach((s) => {
      if (!s.spx_open || !s.spx_close) return;
      const change = s.spx_close - s.spx_open;
      const dir = change > 5 ? "UP" : change < -5 ? "DOWN" : "FLAT";
      const prev = map.get(dir) ?? { total: 0, count: 0 };
      prev.total += s.net_pnl || 0;
      prev.count += 1;
      map.set(dir, prev);
    });
    const order = ["UP", "FLAT", "DOWN"];
    return order
      .filter((d) => map.has(d))
      .map((dir) => ({
        direction: dir,
        avgPnl: map.get(dir)!.total / map.get(dir)!.count,
        count: map.get(dir)!.count,
      }));
  }, [summaries]);

  // 4. Trend signal accuracy
  const trendData = useMemo(() => {
    const map = new Map<string, { totalPnl: number; count: number }>();
    entries.forEach((e) => {
      const signal = e.trend_signal || "Unknown";
      if (signal === "Unknown") return;
      const entryStops = stopLookup.get(`${e.date}_${e.entry_number}`) ?? [];
      let pnl = e.total_credit || 0;
      entryStops.forEach((s) => {
        pnl -= s.actual_debit || 0;
      });
      const prev = map.get(signal) ?? { totalPnl: 0, count: 0 };
      prev.totalPnl += pnl;
      prev.count += 1;
      map.set(signal, prev);
    });
    const order = ["BULLISH", "NEUTRAL", "BEARISH"];
    return order
      .filter((s) => map.has(s))
      .map((signal) => ({
        signal,
        avgPnl: map.get(signal)!.totalPnl / map.get(signal)!.count,
        count: map.get(signal)!.count,
      }));
  }, [entries, stopLookup]);

  return (
    <>
      {/* VIX vs P&L */}
      <ChartCard title="VIX vs Daily P&L">
        {vixPnlData.length === 0 ? (
          <EmptyChart message="No VIX data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <ScatterChart>
              <CartesianGrid strokeDasharray="3 3" stroke={colors.borderDim} />
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
        )}
      </ChartCard>

      {/* Day Range vs P&L */}
      <ChartCard title="Day Range vs P&L">
        {rangePnlData.length === 0 ? (
          <EmptyChart message="No day range data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <ScatterChart>
              <CartesianGrid strokeDasharray="3 3" stroke={colors.borderDim} />
              <XAxis
                dataKey="range"
                name="Range"
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={{ stroke: colors.borderDim }}
                label={{ value: "SPX Range (pts)", position: "insideBottom", offset: -2, fontSize: 9, fill: colors.textDim }}
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
                  return [n === "P&L" ? formatPnL(v) : `${v.toFixed(1)} pts`, n];
                }}
              />
              <Scatter data={rangePnlData} fill={colors.warning} />
            </ScatterChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* P&L by Market Direction */}
      <ChartCard title="Avg P&L by Market Direction">
        {directionData.length === 0 ? (
          <EmptyChart message="No direction data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={directionData}>
              <XAxis
                dataKey="direction"
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
                formatter={(value: unknown, _name: unknown, props: { payload?: { count?: number } }) => {
                  const n = props?.payload?.count ?? 0;
                  return [`${formatPnL(Number(value ?? 0))} (${n} days)`, "Avg P&L"];
                }}
              />
              <Bar dataKey="avgPnl" radius={[3, 3, 0, 0]}>
                {directionData.map((d, i) => (
                  <Cell key={i} fill={d.avgPnl >= 0 ? colors.profit : colors.loss} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* Trend Signal Accuracy */}
      <ChartCard title="Avg P&L by Trend Signal">
        {trendData.length === 0 ? (
          <EmptyChart message="No trend signal data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={trendData}>
              <XAxis
                dataKey="signal"
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
                formatter={(value: unknown, _name: unknown, props: { payload?: { count?: number } }) => {
                  const n = props?.payload?.count ?? 0;
                  return [`${formatPnL(Number(value ?? 0))} (${n} entries)`, "Avg P&L"];
                }}
              />
              <Bar dataKey="avgPnl" radius={[3, 3, 0, 0]}>
                {trendData.map((d, i) => (
                  <Cell key={i} fill={d.avgPnl >= 0 ? colors.profit : colors.loss} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>
    </>
  );
}
