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
import type { StrategyMarker } from "../../lib/strategyMarkers";

interface EquityCurveProps {
  dailySummaries: { date: string; net_pnl: number }[];
  /** Optional strategy-change markers: drawn as vertical lines + a "since" caption. */
  markers?: StrategyMarker[];
}

export function EquityCurve({ dailySummaries, markers }: EquityCurveProps) {
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

  // Each marker → the first plotted point on/after its date (x-axis is the short
  // label) + the cumulative net P&L booked on/after that date.
  const markerViews = useMemo(() => {
    if (!markers || markers.length === 0) return [];
    return markers
      .map((m) => {
        const pt = data.find((d) => d.date >= m.date);
        if (!pt) return null;
        const since = dailySummaries.filter((s) => s.date >= m.date);
        const pnl = since.reduce(
          (acc, s) => acc + (Number.isFinite(s.net_pnl) ? s.net_pnl : 0),
          0,
        );
        return { x: pt.label, label: m.label, date: m.date, pnl, days: since.length };
      })
      .filter(
        (x): x is { x: string; label: string; date: string; pnl: number; days: number } =>
          x !== null,
      );
  }, [markers, data, dailySummaries]);

  if (data.length < 2) return null;

  return (
    <div>
      <h3 className="label-upper mb-2">Equity Curve</h3>
      {markerViews.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-text-secondary">
          {markerViews.map((mv) => (
            <span key={mv.date}>
              since <span style={{ color: colors.info }}>{mv.label}</span> (
              {formatDateShort(mv.date)}):{" "}
              <span style={{ color: mv.pnl >= 0 ? colors.profit : colors.loss }}>
                {formatPnL(mv.pnl)}
              </span>
              <span className="text-text-dim"> · {mv.days}d</span>
            </span>
          ))}
        </div>
      )}
      <div className="bg-card rounded-lg border border-border-dim p-3">
        <ResponsiveContainer width="100%" height={250}>
          <ComposedChart data={data}>
            <defs>
              <linearGradient id={equityGradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={equityColor} stopOpacity={0.2} />
                <stop offset="100%" stopColor={equityColor} stopOpacity={0} />
              </linearGradient>
              <linearGradient id={ddGradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={colors.loss} stopOpacity={0.35} />
                <stop offset="100%" stopColor={colors.loss} stopOpacity={0.12} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: colors.textSecondary }}
              axisLine={{ stroke: "rgba(255,255,255,0.06)" }}
              tickLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fontSize: 11, fill: colors.textSecondary }}
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
            {markerViews.map((mv) => (
              <ReferenceLine
                key={mv.date}
                x={mv.x}
                stroke={colors.info}
                strokeDasharray="4 2"
                strokeOpacity={0.7}
                label={{
                  value: mv.label,
                  position: "insideTopRight",
                  fontSize: 9,
                  fill: colors.info,
                }}
              />
            ))}
            <Area
              type="monotone"
              dataKey="drawdown"
              stroke={colors.loss}
              strokeWidth={1}
              strokeOpacity={0.4}
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
