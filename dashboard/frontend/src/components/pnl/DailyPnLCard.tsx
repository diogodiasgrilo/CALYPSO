import { useMemo } from "react";
import { useHydraStore } from "../../store/hydraStore";
import { formatPnL, winRate } from "../../lib/formatters";
import { pnlColor, colors } from "../../lib/tradingColors";
import { useAnimatedNumber } from "../../hooks/useAnimatedNumber";
import type { HydraEntry } from "../../store/hydraStore";

/** Compute unrealized P&L from active sides + surviving long leg values. */
function computeUnrealizedPnl(entries: HydraEntry[]): number {
  let total = 0;
  for (const e of entries) {
    if (!e.entry_time) continue;
    const callActive = !e.call_side_stopped && !e.call_side_skipped && !e.call_side_expired;
    const putActive = !e.put_side_stopped && !e.put_side_skipped && !e.put_side_expired;
    if (callActive) total += e.call_spread_credit - (e.call_spread_value ?? 0);
    if (putActive) total += e.put_spread_credit - (e.put_spread_value ?? 0);
    // Surviving long legs after MKT-025 stop (long stays open, not salvaged)
    // Their value offsets the long cost already deducted from total_realized_pnl
    total += (e.call_long_value ?? 0) + (e.put_long_value ?? 0);
  }
  return total;
}

export function DailyPnLCard() {
  const { hydraState, metrics } = useHydraStore();

  const entries = hydraState?.entries ?? [];
  const commission = hydraState?.total_commission ?? 0;
  const credit = hydraState?.total_credit_received ?? 0;
  const entriesCompleted = hydraState?.entries_completed ?? 0;
  const totalStops =
    (hydraState?.call_stops_triggered ?? 0) +
    (hydraState?.put_stops_triggered ?? 0);

  // Live P&L = realized (actual stop costs from bot) + unrealized (active spread values)
  // total_realized_pnl tracks actual execution prices including slippage
  const realizedPnl = hydraState?.total_realized_pnl ?? 0;
  const unrealizedPnl = useMemo(() => computeUnrealizedPnl(entries), [entries]);
  const netPnl = realizedPnl + unrealizedPnl - commission;

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
