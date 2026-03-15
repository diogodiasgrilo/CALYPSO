import { useEffect, useMemo, useState } from "react";
import {
  sharpeRatio,
  sortinoRatio,
  maxDrawdown,
  calmarRatio,
  profitFactor,
  expectancy,
  avgWinLossRatio,
} from "../../lib/statsUtils";
import { formatPnL } from "../../lib/formatters";
import { colors, pnlColor } from "../../lib/tradingColors";
import { Skeleton } from "../shared/Skeleton";

interface MetricCardProps {
  label: string;
  value: string;
  color?: string;
}

function MetricCard({ label, value, color }: MetricCardProps) {
  return (
    <div className="bg-card rounded-lg border border-border-dim p-3">
      <div className="label-upper mb-1">{label}</div>
      <div className="metric-lg" style={{ color: color ?? colors.textPrimary }}>
        {value}
      </div>
    </div>
  );
}

export function PerformanceMetrics() {
  const [dailyPnls, setDailyPnls] = useState<number[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetch("/api/metrics/performance")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (data.daily_pnls) setDailyPnls(data.daily_pnls);
        else setDailyPnls([]);
      })
      .catch(() => setError(true));
  }, []);

  // useMemo MUST be called unconditionally (Rules of Hooks — no hooks after early returns)
  const stats = useMemo(() => {
    if (!dailyPnls || dailyPnls.length < 2) return null;
    return {
      sharpe: sharpeRatio(dailyPnls),
      sortino: sortinoRatio(dailyPnls),
      dd: maxDrawdown(dailyPnls),
      calmar: calmarRatio(dailyPnls),
      pf: profitFactor(dailyPnls),
      exp: expectancy(dailyPnls),
      wlRatio: avgWinLossRatio(dailyPnls),
    };
  }, [dailyPnls]);

  if (error) {
    return (
      <div>
        <h3 className="label-upper mb-2">Performance</h3>
        <div className="bg-card rounded-lg border border-border-dim p-4 text-center">
          <span className="text-text-dim text-xs">
            Failed to load performance data
          </span>
        </div>
      </div>
    );
  }

  if (dailyPnls === null) {
    return (
      <div>
        <h3 className="label-upper mb-2">Performance</h3>
        <div className="grid grid-cols-4 max-lg:grid-cols-2 max-sm:grid-cols-1 gap-2">
          {Array.from({ length: 7 }).map((_, i) => (
            <Skeleton key={i} variant="metric" />
          ))}
        </div>
      </div>
    );
  }

  if (!stats) {
    return (
      <div>
        <h3 className="label-upper mb-2">Performance</h3>
        <div className="bg-card rounded-lg border border-border-dim p-4 text-center">
          <span className="text-text-dim text-xs">
            Need at least 2 trading days for statistics
          </span>
        </div>
      </div>
    );
  }

  const { sharpe, sortino, dd, calmar, pf, exp, wlRatio } = stats;

  const fmtRatio = (v: number) =>
    isNaN(v) ? "N/A" : v === Infinity ? "∞" : v === -Infinity ? "-∞" : v.toFixed(2);

  return (
    <div>
      <h3 className="label-upper mb-2">Performance</h3>
      <div className="grid grid-cols-4 max-lg:grid-cols-2 max-sm:grid-cols-1 gap-2">
        <MetricCard
          label="Sharpe"
          value={fmtRatio(sharpe)}
          color={sharpe >= 1 ? colors.profit : sharpe >= 0 ? colors.warning : colors.loss}
        />
        <MetricCard
          label="Sortino"
          value={fmtRatio(sortino)}
          color={sortino >= 1.5 ? colors.profit : sortino >= 0 ? colors.warning : colors.loss}
        />
        <MetricCard
          label="Max Drawdown"
          value={formatPnL(-dd.value)}
          color={colors.loss}
        />
        <MetricCard
          label="Calmar"
          value={fmtRatio(calmar)}
          color={calmar >= 2 ? colors.profit : calmar >= 0 ? colors.warning : colors.loss}
        />
        <MetricCard
          label="Profit Factor"
          value={fmtRatio(pf)}
          color={pf >= 1.5 ? colors.profit : pf >= 1 ? colors.warning : colors.loss}
        />
        <MetricCard
          label="Expectancy"
          value={formatPnL(exp)}
          color={pnlColor(exp)}
        />
        <MetricCard
          label="Win/Loss Ratio"
          value={fmtRatio(wlRatio)}
          color={wlRatio >= 1 ? colors.profit : colors.loss}
        />
      </div>
    </div>
  );
}
