import { useHydraStore } from "../../store/hydraStore";
import { formatPnL, winRate } from "../../lib/formatters";
import { pnlColor, colors } from "../../lib/tradingColors";
import { useAnimatedNumber } from "../../hooks/useAnimatedNumber";

export function DailyPnLCard() {
  const { hydraState, metrics } = useHydraStore();

  const grossPnl = hydraState?.total_realized_pnl ?? 0;
  const commission = hydraState?.total_commission ?? 0;
  const netPnl = grossPnl - commission;
  const credit = hydraState?.total_credit_received ?? 0;
  const entriesCompleted = hydraState?.entries_completed ?? 0;
  const totalStops =
    (hydraState?.call_stops_triggered ?? 0) +
    (hydraState?.put_stops_triggered ?? 0);

  const animatedPnl = useAnimatedNumber(netPnl);

  // Cumulative
  const cumulativePnl = metrics?.cumulative_pnl ?? 0;
  const winningDays = metrics?.winning_days ?? 0;
  const losingDays = metrics?.losing_days ?? 0;
  const totalDays = winningDays + losingDays;
  const avgPerDay = totalDays > 0 ? cumulativePnl / totalDays : 0;

  return (
    <div className="space-y-3">
      {/* Today */}
      <div className="bg-card rounded-lg border border-border-dim p-4">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
          Today
        </h3>
        <div className="text-center mb-3">
          <span
            className="text-3xl font-bold font-mono"
            style={{ color: pnlColor(animatedPnl) }}
          >
            {formatPnL(animatedPnl)}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="flex justify-between">
            <span className="text-text-secondary">Entries</span>
            <span className="text-text-primary">{entriesCompleted}/5</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Stops</span>
            <span style={{ color: totalStops > 0 ? colors.loss : colors.textPrimary }}>
              {totalStops}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Credit</span>
            <span className="text-text-primary">${credit.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Commission</span>
            <span className="text-text-primary">${commission.toFixed(2)}</span>
          </div>
        </div>
      </div>

      {/* Cumulative */}
      <div className="bg-card rounded-lg border border-border-dim p-4">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
          Cumulative
        </h3>
        <div className="text-center mb-3">
          <span
            className="text-xl font-bold font-mono"
            style={{ color: pnlColor(cumulativePnl) }}
          >
            {formatPnL(cumulativePnl)}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="flex justify-between">
            <span className="text-text-secondary">Days</span>
            <span className="text-text-primary">{totalDays}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Win Rate</span>
            <span className="text-text-primary">
              {winRate(winningDays, losingDays)}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">W/L</span>
            <span>
              <span style={{ color: colors.profit }}>{winningDays}</span>
              <span className="text-text-dim">/</span>
              <span style={{ color: colors.loss }}>{losingDays}</span>
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Avg/Day</span>
            <span style={{ color: pnlColor(avgPerDay) }}>
              {formatPnL(avgPerDay)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
