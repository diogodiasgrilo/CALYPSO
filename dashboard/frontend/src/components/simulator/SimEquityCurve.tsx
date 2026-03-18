import { useId, useMemo } from "react";
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Legend,
} from "recharts";
import { colors } from "../../lib/tradingColors";
import { formatPnL, formatDateShort } from "../../lib/formatters";

interface DayData {
  date: string;
  actual_net_pnl: number;
  simulated_net_pnl: number;
  simulation_tier: number;
}

interface SimEquityCurveProps {
  days: DayData[];
}

export function SimEquityCurve({ days }: SimEquityCurveProps) {
  const id = useId();
  const betterGradId = `simBetter-${id}`;
  const worseGradId = `simWorse-${id}`;

  const data = useMemo(() => {
    let actualCum = 0;
    let simCum = 0;
    return days.map((d) => {
      actualCum += d.actual_net_pnl;
      simCum += d.simulated_net_pnl;
      return {
        date: d.date,
        label: formatDateShort(d.date),
        actual: Math.round(actualCum),
        simulated: Math.round(simCum),
        delta: Math.round(simCum - actualCum),
        tier: d.simulation_tier,
      };
    });
  }, [days]);

  if (data.length < 2) return null;

  const allValues = data.flatMap((d) => [d.actual, d.simulated]);
  const yMin = Math.min(...allValues);
  const yMax = Math.max(...allValues);
  const padding = Math.max(100, (yMax - yMin) * 0.15);

  return (
    <div>
      <h3 className="label-upper mb-2">Equity Curves</h3>
      <div className="bg-card rounded-lg border border-border-dim p-3">
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={data}>
            <defs>
              <linearGradient id={betterGradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={colors.profit} stopOpacity={0.12} />
                <stop offset="100%" stopColor={colors.profit} stopOpacity={0} />
              </linearGradient>
              <linearGradient id={worseGradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={colors.loss} stopOpacity={0} />
                <stop offset="100%" stopColor={colors.loss} stopOpacity={0.12} />
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
              tickFormatter={(v: number) => (v < 0 ? `-$${Math.abs(v)}` : `$${v}`)}
              width={60}
              domain={[yMin - padding, yMax + padding]}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: colors.bgElevated,
                border: `1px solid ${colors.border}`,
                borderRadius: 6,
                fontSize: 11,
                color: colors.textPrimary,
              }}
              formatter={(value: unknown, name?: string) => {
                const v = Number(value ?? 0);
                const label =
                  name === "actual" ? "Actual" :
                  name === "simulated" ? "Simulated" : "Delta";
                return [formatPnL(v, 0), label];
              }}
            />
            <ReferenceLine y={0} stroke={colors.textDim} strokeDasharray="3 3" />
            <Line
              type="monotone"
              dataKey="actual"
              stroke={colors.profit}
              strokeWidth={2}
              dot={false}
              name="actual"
            />
            <Line
              type="monotone"
              dataKey="simulated"
              stroke={colors.info}
              strokeWidth={2}
              strokeDasharray="6 3"
              dot={false}
              name="simulated"
            />
            <Legend
              verticalAlign="top"
              height={28}
              formatter={(value: string) => (
                <span style={{ color: colors.textSecondary, fontSize: 11 }}>
                  {value === "actual" ? "Actual" : "Simulated"}
                </span>
              )}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
