import { useId, useMemo } from "react";
import {
  ComposedChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { colors } from "../../lib/tradingColors";
import { formatPnL, formatDateShort } from "../../lib/formatters";

interface EquityCurveProps {
  dailySummaries: { date: string; net_pnl: number }[];
}

export function EquityCurve({ dailySummaries }: EquityCurveProps) {
  const id = useId();
  const equityGradId = `equityGrad-${id}`;
  const ddGradId = `ddGrad-${id}`;

  const data = useMemo(() => {
    let cumulative = 0;
    let peak = 0;
    return dailySummaries.map((s) => {
      const pnl = Number.isFinite(s.net_pnl) ? s.net_pnl : 0;
      cumulative += pnl;
      if (cumulative > peak) peak = cumulative;
      const drawdown = peak - cumulative;
      return {
        date: s.date,
        label: formatDateShort(s.date),
        equity: cumulative,
        drawdown: drawdown > 0 ? -drawdown : 0,
      };
    });
  }, [dailySummaries]);

  const lastEquity = data.length > 0 ? data[data.length - 1].equity : 0;
  const equityColor = lastEquity >= 0 ? colors.profit : colors.loss;

  if (data.length < 2) return null;

  return (
    <div>
      <h3 className="label-upper mb-2">Equity Curve</h3>
      <div className="bg-card rounded-lg border border-border-dim p-3">
        <ResponsiveContainer width="100%" height={250}>
          <ComposedChart data={data}>
            <defs>
              <linearGradient id={equityGradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={equityColor} stopOpacity={0.2} />
                <stop offset="100%" stopColor={equityColor} stopOpacity={0} />
              </linearGradient>
              <linearGradient id={ddGradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={colors.loss} stopOpacity={0} />
                <stop offset="100%" stopColor={colors.loss} stopOpacity={0.15} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: colors.textDim }}
              axisLine={{ stroke: "rgba(255,255,255,0.06)" }}
              tickLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fontSize: 10, fill: colors.textDim }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v: number) => v < 0 ? `-$${Math.abs(v)}` : `$${v}`}
              width={55}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: colors.bgElevated,
                border: `1px solid ${colors.border}`,
                borderRadius: 6,
                fontSize: 11,
                color: colors.textPrimary,
              }}
              formatter={(value: unknown, name?: string) => [
                formatPnL(Number(value ?? 0)),
                name === "equity" ? "Cumulative P&L" : "Drawdown",
              ]}
            />
            <ReferenceLine y={0} stroke={colors.textDim} strokeDasharray="3 3" />
            <Area
              type="monotone"
              dataKey="drawdown"
              stroke="transparent"
              fill={`url(#${ddGradId})`}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke={equityColor}
              fill={`url(#${equityGradId})`}
              strokeWidth={2}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
