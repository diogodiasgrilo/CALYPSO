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

/** Compact comparison badge */
function CompareBadge({ value, avg, invert = false, prefix = "$" }: { value: number; avg: number; invert?: boolean; prefix?: string }) {
  if (avg === 0 || !Number.isFinite(value) || !Number.isFinite(avg)) return null;
  const isAbove = value > avg;
  const isBelow = value < avg;
  const arrow = isAbove ? "\u2191" : isBelow ? "\u2193" : "";
  const goodColor = invert ? colors.loss : colors.profit;
  const badColor = invert ? colors.profit : colors.loss;
  const color = isAbove ? goodColor : isBelow ? badColor : colors.textDim;
  return (
    <span className="text-[9px] ml-1 opacity-70" style={{ color }}>
      {arrow}{prefix}{Math.abs(avg).toFixed(0)}
    </span>
  );
}

/** Stat cell — label on top, value below */
function StatCell({ label, children, className = "" }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`text-center ${className}`}>
      <div className="text-[10px] text-text-dim uppercase tracking-wider mb-0.5">{label}</div>
      <div className="text-sm font-semibold text-text-primary">{children}</div>
    </div>
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

  const schedule = hydraState?.entry_schedule;
  const baseCount = schedule?.base?.length ?? 3;
  const baseEntries = entries.filter((e) => e.entry_number <= baseCount).length;
  const conditionalEntries = entries.filter((e) => e.entry_number > baseCount).length;

  return (
    <div className="space-y-3">
      {/* Today */}
      <div className="bg-card rounded-lg border border-border-dim p-4">
        <h3 className="label-upper mb-2">Today</h3>

        {/* Hero P&L */}
        <div className="text-center mb-4">
          <span
            className="metric-hero"
            style={{ color: pnlColor(animatedPnl) }}
          >
            {formatPnL(animatedPnl)}
          </span>
          {comparisons && (
            <div className="text-[10px] mt-0.5 opacity-60" style={{ color: pnlColor(avgPnl) }}>
              avg {formatPnL(avgPnl)}
            </div>
          )}
        </div>

        {/* Stat grid — 4 columns, centered */}
        <div className="grid grid-cols-4 gap-1 pt-3 border-t border-border-dim">
          <StatCell label="Entries">
            {baseEntries}/{baseCount}
            {conditionalEntries > 0 && (
              <span className="text-text-dim text-xs">+{conditionalEntries}</span>
            )}
          </StatCell>
          <StatCell label="Stops">
            <span style={{ color: totalStops > 0 ? colors.loss : colors.textPrimary }}>
              {totalStops}
            </span>
            {comparisons && <CompareBadge value={totalStops} avg={avgStops} invert prefix="" />}
          </StatCell>
          <StatCell label="Credit">
            ${credit.toFixed(0)}
            {comparisons && avgCredit > 0 && <CompareBadge value={credit} avg={avgCredit} />}
          </StatCell>
          <StatCell label="Comm.">
            ${commission.toFixed(0)}
          </StatCell>
        </div>
      </div>

      {/* Cumulative */}
      <div className="bg-card rounded-lg border border-border-dim p-4">
        <h3 className="label-upper mb-2">Cumulative</h3>

        {/* Hero cumulative P&L */}
        <div className="text-center mb-4">
          <span
            className="metric-lg"
            style={{ color: pnlColor(cumulativePnl) }}
          >
            {formatPnL(cumulativePnl)}
          </span>
        </div>

        {/* Stat grid — 4 columns, centered */}
        <div className="grid grid-cols-4 gap-1 pt-3 border-t border-border-dim">
          <StatCell label="Days">
            {totalDays}
          </StatCell>
          <StatCell label="Win Rate">
            {winRate(winningDays, losingDays)}
          </StatCell>
          <StatCell label="W/L">
            <span style={{ color: colors.profit }}>{winningDays}</span>
            <span className="text-text-dim">/</span>
            <span style={{ color: colors.loss }}>{losingDays}</span>
          </StatCell>
          <StatCell label="Avg/Day">
            <span style={{ color: pnlColor(avgPerDay) }}>
              {formatPnL(avgPerDay)}
            </span>
          </StatCell>
        </div>
      </div>
    </div>
  );
}
