import { useEffect, useMemo, useState } from "react";
import { useHydraStore } from "../../store/hydraStore";
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
  const performancePnls = useHydraStore((s) => s.performancePnls);

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

  // Use WebSocket-pushed data (after market close) if available, otherwise API data
  const effectivePnls = performancePnls ?? dailyPnls;

  // useMemo MUST be called unconditionally (Rules of Hooks — no hooks after early returns)
  const stats = useMemo(() => {
    if (!effectivePnls || effectivePnls.length < 2) return null;
    return {
      sharpe: sharpeRatio(effectivePnls),
      sortino: sortinoRatio(effectivePnls),
      dd: maxDrawdown(effectivePnls),
      calmar: calmarRatio(effectivePnls),
      pf: profitFactor(effectivePnls),
      exp: expectancy(effectivePnls),
      wlRatio: avgWinLossRatio(effectivePnls),
    };
  }, [effectivePnls]);

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

  // Annualized ratios (Sharpe/Sortino/Calmar/Profit Factor/Win-Loss) are
  // statistically meaningless on a handful of days — a few flat-ish days makes
  // Sharpe explode (e.g. 111), and "no losing day yet" makes the others ∞. Gate
  // them behind a minimum sample and show "—" with a building note until then.
  // Max Drawdown and Expectancy are dollar values that are meaningful earlier.
  const MIN_DAYS_FOR_RATIOS = 20;
  const n = effectivePnls?.length ?? 0;
  const enoughForRatios = n >= MIN_DAYS_FOR_RATIOS;

  const ratio = (v: number, good: number, ok: number) => {
    const usable = enoughForRatios && isFinite(v) && !isNaN(v);
    return {
      value: usable ? v.toFixed(2) : "—",
      color: usable
        ? v >= good ? colors.profit : v >= ok ? colors.warning : colors.loss
        : colors.textDim,
    };
  };
  const sh = ratio(sharpe, 1, 0);
  const so = ratio(sortino, 1.5, 0);
  const ca = ratio(calmar, 2, 0);
  const pfc = ratio(pf, 1.5, 1);
  const wl = ratio(wlRatio, 1, 0);

  return (
    <div>
      <div className="flex items-baseline justify-between mb-2">
        <h3 className="label-upper">Performance</h3>
        {!enoughForRatios && (
          <span className="text-[10px] text-text-dim">
            ratios need ≥{MIN_DAYS_FOR_RATIOS} days · have {n}
          </span>
        )}
      </div>
      <div className="grid grid-cols-4 max-lg:grid-cols-2 max-sm:grid-cols-1 gap-2">
        <MetricCard label="Sharpe (ann.)" value={sh.value} color={sh.color} />
        <MetricCard label="Sortino (ann.)" value={so.value} color={so.color} />
        <MetricCard label="Max Drawdown" value={formatPnL(-dd.value)} color={colors.loss} />
        <MetricCard label="Calmar (ann.)" value={ca.value} color={ca.color} />
        <MetricCard label="Profit Factor" value={pfc.value} color={pfc.color} />
        <MetricCard label="Expectancy" value={formatPnL(exp)} color={pnlColor(exp)} />
        <MetricCard label="Win/Loss Ratio" value={wl.value} color={wl.color} />
      </div>
    </div>
  );
}
