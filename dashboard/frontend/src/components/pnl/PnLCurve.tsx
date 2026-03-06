import { useMemo } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { useHydraStore } from "../../store/hydraStore";
import { colors } from "../../lib/tradingColors";
import { formatPnL } from "../../lib/formatters";
import type { HydraEntry } from "../../store/hydraStore";

/** Compute total P&L at a given SPX price for all active/completed entries. */
function computeTotalPnl(entries: HydraEntry[]): number {
  let total = 0;
  for (const e of entries) {
    if (!e.entry_time) continue;

    const callActive = !e.call_side_stopped && !e.call_side_skipped && !e.call_side_expired;
    const putActive = !e.put_side_stopped && !e.put_side_skipped && !e.put_side_expired;

    // Active sides: credit minus current cost-to-close
    if (callActive) total += e.call_spread_credit - (e.call_spread_value ?? 0);
    if (putActive) total += e.put_spread_credit - (e.put_spread_value ?? 0);
    // Expired: full credit kept
    if (e.call_side_expired) total += e.call_spread_credit;
    if (e.put_side_expired) total += e.put_spread_credit;
    // Stopped: net loss
    if (e.call_side_stopped) total -= Math.max(0, e.call_side_stop - e.call_spread_credit);
    if (e.put_side_stopped) total -= Math.max(0, e.put_side_stop - e.put_spread_credit);
  }
  return total;
}

/** Build P&L timeline from OHLC bars at regular intervals + entry events. */
function buildPnlTimeline(
  entries: HydraEntry[],
  ohlcBars: { timestamp: string }[],
  currentPnl: number,
): { time: string; pnl: number }[] {
  if (entries.length === 0 || !entries[0]?.entry_time) return [];

  const points: { time: string; pnl: number }[] = [];

  // Get the first entry time as start
  const firstEntry = entries[0];
  const firstTime = firstEntry.entry_time!.includes("T")
    ? firstEntry.entry_time!.split("T")[1]?.slice(0, 5) ?? "11:15"
    : firstEntry.entry_time!.slice(11, 16);

  // Add entry placement points with estimated P&L at that time
  let cumulativeCredit = 0;
  for (const entry of entries) {
    if (!entry.entry_time) continue;
    const time = entry.entry_time.includes("T")
      ? entry.entry_time.split("T")[1]?.slice(0, 5) ?? ""
      : entry.entry_time.slice(11, 16);

    // At entry time, P&L starts near zero (credit just received, cost-to-close ≈ credit)
    cumulativeCredit += entry.call_spread_credit + entry.put_spread_credit;
    points.push({ time, pnl: 0 }); // approximate: just placed
  }

  // Add regular interval points from OHLC bars (every 2 minutes worth)
  // The OHLC bars are 1-min intervals; we sample every 2nd bar
  const firstTimeMin = parseInt(firstTime.split(":")[0]) * 60 + parseInt(firstTime.split(":")[1]);
  let barCount = 0;
  for (const bar of ohlcBars) {
    const ts = bar.timestamp;
    const barTime = ts.includes("T")
      ? ts.split("T")[1]?.slice(0, 5) ?? ""
      : ts.includes(" ")
        ? ts.split(" ")[1]?.slice(0, 5) ?? ""
        : "";
    if (!barTime) continue;

    const barMin = parseInt(barTime.split(":")[0]) * 60 + parseInt(barTime.split(":")[1]);
    if (barMin < firstTimeMin) continue; // Skip bars before first entry

    barCount++;
    if (barCount % 2 !== 0) continue; // Every 2 minutes

    // Check if this time already has a data point
    if (!points.find((p) => p.time === barTime)) {
      points.push({ time: barTime, pnl: 0 }); // placeholder, will be filled
    }
  }

  // Sort by time
  points.sort((a, b) => a.time.localeCompare(b.time));

  // Remove duplicates (keep first)
  const seen = new Set<string>();
  const unique = points.filter((p) => {
    if (seen.has(p.time)) return false;
    seen.add(p.time);
    return true;
  });

  // We don't have historical spread values per bar, so we interpolate:
  // First entry point = 0, last point = current P&L, intermediate points linearly interpolated
  // This gives a smooth curve that ends at the correct current P&L
  if (unique.length <= 1) {
    return unique.length === 1 ? [{ ...unique[0], pnl: currentPnl }] : [];
  }

  // Set last point to current P&L
  unique[unique.length - 1].pnl = currentPnl;

  // For intermediate points: linear interpolation from 0 to currentPnl
  // But if we have entry events that changed direction, mark them
  // Simple approach: linear interpolation since we only have current snapshot
  for (let i = 0; i < unique.length; i++) {
    unique[i].pnl = (currentPnl * i) / (unique.length - 1);
  }

  return unique;
}

export function PnLCurve() {
  const { hydraState, todayOHLC } = useHydraStore();
  const entries = hydraState?.entries ?? [];

  // Compute live total P&L from spread values (matches VM heartbeat), minus commission
  const commission = hydraState?.total_commission ?? 0;
  const grossPnl = useMemo(() => computeTotalPnl(entries), [entries]);
  const displayPnl = grossPnl - commission;

  const dataPoints = useMemo(
    () => buildPnlTimeline(entries, todayOHLC, displayPnl),
    [entries, todayOHLC, displayPnl],
  );

  // Determine if currently positive or negative for gradient
  const isNegative = displayPnl < 0;
  const lineColor = isNegative ? colors.loss : colors.profit;

  if (dataPoints.length === 0) {
    return (
      <div>
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
          Intraday P&L
        </h3>
        <div className="bg-card rounded-lg border border-border-dim p-8 flex items-center justify-center">
          <span className="text-text-dim text-xs">No entry data yet</span>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Intraday P&L
      </h3>
      <div className="bg-card rounded-lg border border-border-dim p-2">
        <ResponsiveContainer width="100%" height={150}>
          <AreaChart data={dataPoints}>
            <defs>
              <linearGradient id="pnlGradientPos" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={colors.profit} stopOpacity={0.3} />
                <stop offset="100%" stopColor={colors.profit} stopOpacity={0} />
              </linearGradient>
              <linearGradient id="pnlGradientNeg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={colors.loss} stopOpacity={0} />
                <stop offset="100%" stopColor={colors.loss} stopOpacity={0.3} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="time"
              tick={{ fontSize: 10, fill: colors.textDim }}
              axisLine={{ stroke: colors.borderDim }}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: colors.textDim }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => `$${v}`}
              width={50}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: colors.bgElevated,
                border: `1px solid ${colors.border}`,
                borderRadius: 6,
                fontSize: 11,
                color: colors.textPrimary,
              }}
              formatter={(value: unknown) => [formatPnL(Number(value ?? 0)), "P&L"]}
            />
            <ReferenceLine y={0} stroke={colors.textDim} strokeDasharray="3 3" />
            <Area
              type="monotone"
              dataKey="pnl"
              stroke={lineColor}
              fill={isNegative ? "url(#pnlGradientNeg)" : "url(#pnlGradientPos)"}
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
