import { colors } from "../../lib/tradingColors";
import { formatPnL } from "../../lib/formatters";

interface SimResultData {
  actual_total_pnl: number;
  simulated_total_pnl: number;
  delta_total_pnl: number;
  actual_win_rate: number;
  simulated_win_rate: number;
  actual_max_drawdown: number;
  simulated_max_drawdown: number;
  actual_sharpe: number;
  simulated_sharpe: number;
  actual_avg_pnl: number;
  simulated_avg_pnl: number;
  actual_total_stops: number;
  simulated_total_stops: number;
  total_days: number;
  tier1_days: number;
  tier2_days: number;
}

function MetricCard({
  label,
  actual,
  simulated,
  format,
  higherIsBetter = true,
}: {
  label: string;
  actual: number;
  simulated: number;
  format: (v: number) => string;
  higherIsBetter?: boolean;
}) {
  const delta = simulated - actual;
  const improved = higherIsBetter ? delta > 0 : delta < 0;
  const deltaColor = Math.abs(delta) < 0.01 ? colors.textDim : improved ? colors.profit : colors.loss;
  const arrow = delta > 0 ? "+" : "";

  return (
    <div className="bg-card rounded-lg border border-border-dim p-3">
      <div className="text-[10px] uppercase tracking-widest text-text-dim mb-2">{label}</div>
      <div className="text-xs text-text-dim mb-0.5">{format(actual)}</div>
      <div className="text-lg font-bold text-text-primary">{format(simulated)}</div>
      <div className="text-xs font-medium mt-1" style={{ color: deltaColor }}>
        {arrow}{format(delta)} {improved ? "▲" : Math.abs(delta) < 0.01 ? "—" : "▼"}
      </div>
    </div>
  );
}

interface SimResultsProps {
  result: SimResultData;
}

export function SimResults({ result }: SimResultsProps) {
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h3 className="label-upper">Comparison</h3>
        <div className="flex gap-3 text-[10px] text-text-dim">
          <span>{result.total_days} days</span>
          <span style={{ color: colors.info }}>{result.tier1_days} full sim</span>
          <span>{result.tier2_days} heuristic</span>
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <MetricCard
          label="Total P&L"
          actual={result.actual_total_pnl}
          simulated={result.simulated_total_pnl}
          format={(v) => formatPnL(v, 0)}
        />
        <MetricCard
          label="Win Rate"
          actual={result.actual_win_rate}
          simulated={result.simulated_win_rate}
          format={(v) => `${v.toFixed(1)}%`}
        />
        <MetricCard
          label="Max Drawdown"
          actual={result.actual_max_drawdown}
          simulated={result.simulated_max_drawdown}
          format={(v) => formatPnL(v, 0)}
          higherIsBetter={false}
        />
        <MetricCard
          label="Sharpe Ratio"
          actual={result.actual_sharpe}
          simulated={result.simulated_sharpe}
          format={(v) => v.toFixed(2)}
        />
        <MetricCard
          label="Avg P&L/Day"
          actual={result.actual_avg_pnl}
          simulated={result.simulated_avg_pnl}
          format={(v) => formatPnL(v, 0)}
        />
        <MetricCard
          label="Total Stops"
          actual={result.actual_total_stops}
          simulated={result.simulated_total_stops}
          format={(v) => String(Math.round(v))}
          higherIsBetter={false}
        />
      </div>
    </div>
  );
}
