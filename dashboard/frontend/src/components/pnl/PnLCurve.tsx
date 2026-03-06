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

export function PnLCurve() {
  const { hydraState } = useHydraStore();

  // Build intraday P&L curve from entries
  const entries = hydraState?.entries ?? [];

  // Use the authoritative total_realized_pnl from state file (tracks actual
  // fill prices including slippage corrections). Show per-entry data points
  // for the timeline, but the final point uses the real P&L.
  const dataPoints: { time: string; pnl: number }[] = [];

  let cumulativePnl = 0;

  entries.forEach((entry) => {
    if (!entry.entry_time) return;

    const time = entry.entry_time.includes("T")
      ? entry.entry_time.split("T")[1]?.slice(0, 5)
      : entry.entry_time.slice(11, 16);

    // For completed entries, estimate per-entry P&L from credits and stop flags
    if (entry.call_side_stopped) {
      cumulativePnl -= entry.call_spread_credit; // Lost what was collected
    } else if (entry.call_side_expired) {
      cumulativePnl += entry.call_spread_credit;
    }

    if (entry.put_side_stopped) {
      cumulativePnl -= entry.put_spread_credit;
    } else if (entry.put_side_expired) {
      cumulativePnl += entry.put_spread_credit;
    }

    dataPoints.push({
      time,
      pnl: cumulativePnl,
    });
  });

  // Override final data point with authoritative P&L from state file
  const realizedPnl = hydraState?.total_realized_pnl ?? 0;
  const commission = hydraState?.total_commission ?? 0;
  const netPnl = realizedPnl - commission;
  if (dataPoints.length > 0 && netPnl !== 0) {
    dataPoints[dataPoints.length - 1].pnl = netPnl;
  }

  if (dataPoints.length === 0) {
    return (
      <div>
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
          Intraday P&L
        </h3>
        <div className="bg-card rounded-lg border border-border-dim p-8 flex items-center justify-center">
          <span className="text-text-dim text-xs">
            No entry data yet
          </span>
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
              <linearGradient id="pnlGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={colors.profit} stopOpacity={0.3} />
                <stop offset="100%" stopColor={colors.profit} stopOpacity={0} />
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
              stroke={colors.profit}
              fill="url(#pnlGradient)"
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
