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
    // Only count long values for sides that were actually opened
    if (!e.call_side_skipped) total += (e.call_long_value ?? 0);
    if (!e.put_side_skipped) total += (e.put_long_value ?? 0);
  }
  return total;
}

/** Comparison arrow indicator. invert=true flips colors (for metrics where lower is better, like stops). */
function CompareArrow({ value, avg, suffix = "avg", invert = false }: { value: number; avg: number; suffix?: string; invert?: boolean }) {
  if (avg === 0 || !Number.isFinite(value) || !Number.isFinite(avg)) return null;
  const isAbove = value > avg;
  const isBelow = value < avg;
  const arrow = isAbove ? "\u2191" : isBelow ? "\u2193" : "\u2192";
  const goodColor = invert ? colors.loss : colors.profit;
  const badColor = invert ? colors.profit : colors.loss;
  const arrowColor = isAbove ? goodColor : isBelow ? badColor : colors.textDim;
  return (
    <span className="text-[10px] ml-1" style={{ color: arrowColor }}>
      {arrow} vs ${Math.abs(avg).toFixed(0)} {suffix}
    </span>
  );
}

export function DailyPnLCard() {
  const { hydraState, metrics, comparisons } = useHydraStore();

  const entries = hydraState?.entries ?? [];
  const commission = hydraState?.total_commission ?? 0;
  const credit = hydraState?.total_credit_received ?? 0;
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

  // Comparisons
  const avgPnl = comparisons?.avg_pnl ?? 0;
  const avgCredit = comparisons?.avg_credit ?? 0;
  const avgStops = comparisons?.avg_stops ?? 0;

  return (
    <div className="space-y-3">
      {/* Today */}
      <div className="bg-card rounded-lg border border-border-dim p-4">
        <h3 className="label-upper mb-3">Today</h3>
        <div className="text-center mb-3">
          <span
            className="metric-hero"
            style={{ color: pnlColor(animatedPnl) }}
          >
            {formatPnL(animatedPnl)}
          </span>
          {comparisons && (
            <div className="mt-1">
              <CompareArrow value={netPnl} avg={avgPnl} />
            </div>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="flex justify-between">
            <span className="text-text-secondary">Entries</span>
            <span className="text-text-primary">
              {entries.filter((e) => e.entry_number <= 5).length}/5
              {entries.some((e) => e.entry_number >= 6) && (
                <span className="text-text-dim ml-0.5">
                  +{entries.filter((e) => e.entry_number >= 6).length}
                </span>
              )}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Stops</span>
            <span>
              <span style={{ color: totalStops > 0 ? colors.loss : colors.textPrimary }}>
                {totalStops}
              </span>
              {comparisons && (
                <CompareArrow value={totalStops} avg={avgStops} invert />
              )}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Credit</span>
            <span>
              <span className="text-text-primary">${credit.toFixed(2)}</span>
              {comparisons && avgCredit > 0 && (
                <CompareArrow value={credit} avg={avgCredit} />
              )}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Commission</span>
            <span className="text-text-primary">${commission.toFixed(2)}</span>
          </div>
        </div>
      </div>

      {/* Cumulative */}
      <div className="bg-card rounded-lg border border-border-dim p-4">
        <h3 className="label-upper mb-3">Cumulative</h3>
        <div className="text-center mb-3">
          <span
            className="metric-lg"
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
