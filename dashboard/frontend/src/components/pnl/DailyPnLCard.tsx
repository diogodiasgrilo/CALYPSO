import { useMemo } from "react";
import { useHydraStore } from "../../store/hydraStore";
import { formatPnL, winRate } from "../../lib/formatters";
import { pnlColor, colors } from "../../lib/tradingColors";
import { useAnimatedNumber } from "../../hooks/useAnimatedNumber";
import type { HydraEntry } from "../../store/hydraStore";

/** Compute live total P&L from all entries (same as VM heartbeat). */
function computeLivePnl(entries: HydraEntry[]): number {
  let total = 0;
  for (const e of entries) {
    if (!e.entry_time) continue;

    const callActive = !e.call_side_stopped && !e.call_side_skipped && !e.call_side_expired;
    const putActive = !e.put_side_stopped && !e.put_side_skipped && !e.put_side_expired;

    // Active sides: credit minus current cost-to-close
    if (callActive) total += e.call_spread_credit - (e.call_spread_value ?? 0);
    if (putActive) total += e.put_spread_credit - (e.put_spread_value ?? 0);
    // Expired: full credit kept
    if (e.call_side_expired) total += e.call_spread_credit;
    if (e.put_side_expired) total += e.put_spread_credit;
    // Stopped: net loss
    if (e.call_side_stopped) total -= Math.max(0, e.call_side_stop - e.call_spread_credit);
    if (e.put_side_stopped) total -= Math.max(0, e.put_side_stop - e.put_spread_credit);
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

  // Live P&L from spread values (matches VM heartbeat display)
  const grossPnl = useMemo(() => computeLivePnl(entries), [entries]);
  const netPnl = grossPnl - commission;

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
