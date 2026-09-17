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

interface PerformanceMetricsProps {
  /** Polled non-primary snapshot's performance daily-P&L array (the same shape
   *  /api/metrics/performance returns). When provided, the metrics compute from
   *  THIS array and the component does NOT fetch the (primary) performance
   *  endpoint. Omitted → fetch + WS store, byte-identical to the old behavior. */
  dailyPnls?: number[];
}

export function PerformanceMetrics({ dailyPnls: dailyPnlsProp }: PerformanceMetricsProps = {}) {
  const usingProps = dailyPnlsProp !== undefined;
  const [dailyPnls, setDailyPnls] = useState<number[] | null>(null);
  const [error, setError] = useState(false);
  const performancePnls = useHydraStore((s) => s.performancePnls);
  // EOD auto-update (operator request): re-fetch the one-shot performance
  // endpoint when the trading day ends OR when settlement writes new metrics —
  // so the section refreshes at close WITHOUT a manual reload. We key the fetch
  // effect on the market-open flag and the metrics' last_updated date so a
  // transition to closed / a fresh metrics write re-runs it. (The WS
  // performance_update push also feeds `performancePnls` below; this fetch is
  // the belt-and-suspenders for clients that connect after that one-shot push.)
  const marketOpen = useHydraStore((s) => s.market?.is_open ?? null);
  const metricsUpdated = useHydraStore((s) => s.metrics?.last_updated ?? null);

  useEffect(() => {
    // Prop mode: the daily P&L array is supplied by the polled snapshot (the
    // variant's OWN performance), so DON'T fetch the primary's endpoint.
    if (usingProps) return;
    let cancelled = false;
    fetch("/api/metrics/performance")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (cancelled) return;
        if (data.daily_pnls) setDailyPnls(data.daily_pnls);
        else setDailyPnls([]);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [usingProps, marketOpen, metricsUpdated]);

  // Prop mode: use the supplied array. WS mode: WebSocket-pushed data (after
  // market close) if available, otherwise API data.
  const effectivePnls = usingProps ? dailyPnlsProp : (performancePnls ?? dailyPnls);

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

  // Loading skeleton only in WS mode (the fetch hasn't resolved yet). In prop
  // mode the data is supplied synchronously, so there's no loading window.
  if (!usingProps && dailyPnls === null) {
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
  // The gate must count days the strategy was actually EXPOSED, not rows in the
  // array. `daily_pnls` includes every trading day since the baseline, so a
  // strategy that trades rarely accumulates $0.00 rows for days it held nothing.
  // Counting those let a 5-observation sample clear a 20-day gate:
  //
  //     variant   rows   traded   Sharpe(all rows)   Sharpe(traded only)
  //        d        48      14         -3.82               -7.45
  //        e        52       5         -3.03              -10.94
  //
  // Both published a ratio; E's came from FIVE real observations. Padding with
  // zeros shrinks the mean by k and the deviation by about sqrt(k), so the ratio
  // is COMPRESSED toward zero — it understates both good and bad performance
  // rather than exaggerating either. The real harm is the sample size, not the
  // direction. A/B/C are unaffected (they trade most days and still pass).
  //
  // The computation itself still runs over ALL rows, which is correct for the
  // sqrt(252) annualisation: capital was allocated on the idle days and earned
  // nothing. Only the SUFFICIENCY test changes.
  //
  // `!== 0` is the only exposure proxy available here — `daily_pnls` is a bare
  // number array with no entry count. A traded day netting exactly $0.00 is
  // therefore miscounted as idle; that is rare and errs toward withholding a
  // ratio, which is the safe direction.
  const MIN_DAYS_FOR_RATIOS = 20;
  const n = effectivePnls?.filter((v) => v !== 0).length ?? 0;
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
          <span className="text-3xs text-text-dim">
            ratios need ≥{MIN_DAYS_FOR_RATIOS} traded days · have {n}
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
