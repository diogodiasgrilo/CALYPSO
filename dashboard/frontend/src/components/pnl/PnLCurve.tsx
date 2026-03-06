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
  const { pnlHistory } = useHydraStore();

  // Determine current P&L from last data point
  const lastPoint = pnlHistory[pnlHistory.length - 1];
  const displayPnl = lastPoint?.pnl ?? 0;

  // Determine if currently positive or negative for gradient
  const isNegative = displayPnl < 0;
  const lineColor = isNegative ? colors.loss : colors.profit;

  if (pnlHistory.length === 0) {
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
          <AreaChart data={pnlHistory}>
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
