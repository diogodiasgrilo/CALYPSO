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
  605, 635, 665, 675, 695, 705, 725, 735, 755, 765, 795,
]; // 10:05,10:35,11:05,11:15,11:35,11:45,12:05,12:15,12:35,12:45,13:15

const SLOT_LABELS: Record<number, string> = {
  605: "10:05", 635: "10:35", 665: "11:05", 675: "11:15",
  695: "11:35", 705: "11:45", 725: "12:05", 735: "12:15",
  755: "12:35", 765: "12:45", 795: "13:15",
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

      {/* Tab content */}
      <div className="grid grid-cols-2 gap-3 max-lg:grid-cols-1">
        {activeTab === "performance" && (
          <PerformanceTab summaries={sortedSummaries} />
        )}
        {activeTab === "entries" && (
          <EntriesTab entries={entries} stopLookup={stopLookup} />
        )}
        {activeTab === "stops" && (
          <StopsTab entries={entries} stops={stops} />
        )}
        {activeTab === "market" && (
          <MarketTab
            summaries={sortedSummaries}
            entries={entries}
            stopLookup={stopLookup}
          />
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
      const type = e.entry_type || "full";
      const label =
        type === "full" ? "Full IC"
        : type === "call-only" ? "Call Only"
        : type === "put-only" ? "Put Only"
        : type;
      counts[label] = (counts[label] ?? 0) + 1;
    });
    const pieColors: Record<string, string> = {
      "Full IC": colors.info,
      "Call Only": colors.warning,
      "Put Only": colors.profit,
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

  // 4. OTM distance vs outcome (scatter)
  const otmData = useMemo(() => {
    const points: { otm: number; pnl: number; side: string; survived: boolean }[] = [];
    entries.forEach((e) => {
      const entryStops = stopLookup.get(`${e.date}_${e.entry_number}`) ?? [];
      const callStopped = entryStops.some((s) => s.side === "call");
      const putStopped = entryStops.some((s) => s.side === "put");

      if (e.otm_distance_call > 0) {
        points.push({
          otm: e.otm_distance_call,
          pnl: callStopped ? -1 : 1, // simplified: stopped vs survived
          side: "call",
          survived: !callStopped,
        });
      }
      if (e.otm_distance_put > 0) {
        points.push({
          otm: e.otm_distance_put,
          pnl: putStopped ? -1 : 1,
          side: "put",
          survived: !putStopped,
        });
      }
    });
    return points;
  }, [entries, stopLookup]);

  const otmSurvived = otmData.filter((d) => d.survived);
  const otmStopped = otmData.filter((d) => !d.survived);

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

      {/* OTM Distance vs Outcome */}
      <ChartCard title="OTM Distance vs Outcome">
        {otmData.length === 0 ? (
          <EmptyChart message="No OTM distance data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <ScatterChart>
              <CartesianGrid strokeDasharray="3 3" stroke={colors.borderDim} />
              <XAxis
                dataKey="otm"
                name="OTM Distance"
                tick={{ fontSize: 10, fill: colors.textDim }}
                axisLine={{ stroke: colors.borderDim }}
                label={{ value: "OTM (pts)", position: "insideBottom", offset: -2, fontSize: 9, fill: colors.textDim }}
              />
              <YAxis
                dataKey="pnl"
                name="Outcome"
                tick={false}
                axisLine={false}
                domain={[-2, 2]}
              />
              <Tooltip
                contentStyle={chartTooltipStyle}
                labelStyle={chartTooltipLabelStyle}
                itemStyle={chartTooltipItemStyle}
                cursor={{ strokeDasharray: "3 3", stroke: colors.textDim }}
                formatter={(value: unknown, name: unknown) => {
                  if (String(name) === "Outcome") {
                    return [Number(value) > 0 ? "Survived" : "Stopped", "Outcome"];
                  }
                  return [`${Number(value ?? 0).toFixed(1)} pts`, String(name)];
                }}
              />
              {otmSurvived.length > 0 && (
                <Scatter name="Survived" data={otmSurvived} fill={colors.profit} />
              )}
              {otmStopped.length > 0 && (
                <Scatter name="Stopped" data={otmStopped} fill={colors.loss} />
              )}
            </ScatterChart>
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

  // 3. Stop slippage distribution
  const slippageData = useMemo(() => {
    return stops
      .filter((s) => s.actual_debit > 0 && s.trigger_level > 0)
      .map((s, i) => ({
        idx: i + 1,
        slippage: s.actual_debit - s.trigger_level,
        label: `E${s.entry_number} ${s.side === "call" ? "C" : "P"}`,
      }))
      .sort((a, b) => b.slippage - a.slippage);
  }, [stops]);

  // 4. MKT-036 timer effectiveness
  const timerData = useMemo(() => {
    const saved = stops.filter((s) => (s.breach_recoveries ?? 0) > 0).length;
    const confirmed = stops.filter(
      (s) => (s.breach_recoveries ?? 0) === 0 && (s.confirmation_seconds ?? 0) > 0,
    ).length;
    const noTimer = stops.filter(
      (s) => (s.confirmation_seconds ?? 0) === 0 && (s.breach_recoveries ?? 0) === 0,
    ).length;
    if (saved === 0 && confirmed === 0 && noTimer === 0) return [];
    return [
      ...(saved > 0 ? [{ category: "Timer Saved", count: saved }] : []),
      ...(confirmed > 0 ? [{ category: "Timer Confirmed", count: confirmed }] : []),
      ...(noTimer > 0 ? [{ category: "No Timer", count: noTimer }] : []),
    ];
  }, [stops]);

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

      {/* Stop Slippage */}
      <ChartCard title="Stop Slippage (Actual vs Trigger)">
        {slippageData.length === 0 ? (
          <EmptyChart message="No stop debit data available" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={slippageData}>
              <XAxis
                dataKey="label"
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
                formatter={(value: unknown) => {
                  const v = Number(value ?? 0);
                  return [`$${v.toFixed(2)} ${v > 0 ? "(slippage)" : "(improvement)"}`, "Slippage"];
                }}
              />
              <Bar dataKey="slippage" radius={[3, 3, 0, 0]}>
                {slippageData.map((d, i) => (
                  <Cell key={i} fill={d.slippage > 0 ? colors.loss : colors.profit} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* MKT-036 Timer */}
      <ChartCard title="MKT-036 Timer Effectiveness">
        {timerData.length === 0 ? (
          <EmptyChart message="No timer data available" />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={timerData}>
              <XAxis
                dataKey="category"
                tick={{ fontSize: 10, fill: colors.textDim }}
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
                formatter={(value: unknown) => [`${value} stops`, "Count"]}
              />
              <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                {timerData.map((d, i) => (
                  <Cell
                    key={i}
                    fill={
                      d.category === "Timer Saved"
                        ? colors.profit
                        : d.category === "Timer Confirmed"
                          ? colors.warning
                          : colors.textDim
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

  // 3. P&L by market direction
  const directionData = useMemo(() => {
    const map = new Map<string, { total: number; count: number }>();
    summaries.forEach((s) => {
      const dir = s.day_type || "Unknown";
      const prev = map.get(dir) ?? { total: 0, count: 0 };
      prev.total += s.net_pnl || 0;
      prev.count += 1;
      map.set(dir, prev);
    });
    return [...map.entries()]
      .filter(([d]) => d !== "Unknown")
      .map(([dir, data]) => ({
        direction: dir,
        avgPnl: data.count > 0 ? data.total / data.count : 0,
        count: data.count,
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
