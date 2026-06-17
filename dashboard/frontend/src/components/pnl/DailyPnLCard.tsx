import { useMemo } from "react";
import { useHydraStore } from "../../store/hydraStore";
import { formatPnL, winRate } from "../../lib/formatters";
import { pnlColor, colors } from "../../lib/tradingColors";
import { useAnimatedNumber } from "../../hooks/useAnimatedNumber";
import type { HydraEntry, CumulativeMetrics } from "../../store/hydraStore";
import type {
  ICSnapshotSummary,
  ICSnapshotCumulative,
} from "../../hooks/useStrategySnapshot";

/** Compute unrealized P&L from active sides + surviving long leg values. */
function computeUnrealizedPnl(entries: HydraEntry[]): number {
  let total = 0;
  for (const e of entries) {
    if (!e.entry_time) continue;
    const callActive = !e.call_side_stopped && !e.call_side_skipped && !e.call_side_expired;
    const putActive = !e.put_side_stopped && !e.put_side_skipped && !e.put_side_expired;
    if (callActive) total += e.call_spread_credit - (e.call_spread_value ?? 0);
    if (putActive) total += e.put_spread_credit - (e.put_spread_value ?? 0);
    // Surviving long leg after a MKT-025 short-only stop (the long stays open).
    // Only the STOPPED side's long survives separately; for an active side the
    // long value is already inside spread_value, so adding it here double-counts
    // (the 2026-06 phantom-$750 bug). Gate strictly on the stopped flag.
    if (e.call_side_stopped) total += (e.call_long_value ?? 0);
    if (e.put_side_stopped) total += (e.put_long_value ?? 0);
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

interface DailyPnLCardProps {
  /** Polled non-primary snapshot's today-summary. When provided, the "Today"
   *  section reads from THIS (net_pnl/credit/commission/stops/entries) instead
   *  of the WS store. Omitted → WS store, byte-identical to the old behavior. */
  summary?: ICSnapshotSummary;
  /** Polled non-primary snapshot's lifetime cumulative metrics. When provided,
   *  the "Cumulative" section reads from THIS instead of the WS metrics store. */
  cumulative?: ICSnapshotCumulative;
}

export function DailyPnLCard({ summary, cumulative }: DailyPnLCardProps = {}) {
  // Hooks always called; props (when present) override the WS-store reads so the
  // primary path is unchanged when no prop is passed.
  const { hydraState, metrics: wsMetrics, comparisons: wsComparisons } = useHydraStore();

  const usingProps = summary !== undefined || cumulative !== undefined;
  // Off-primary there is no historical-comparison stream (avg/best/worst) — it's
  // a WS-only augmentation — so the avg badges/threshold simply don't render.
  const comparisons = usingProps ? null : wsComparisons;
  const metrics: CumulativeMetrics | ICSnapshotCumulative | null = usingProps
    ? (cumulative ?? null)
    : wsMetrics;

  const entries = hydraState?.entries ?? [];

  // ── Today section ──
  // Prop mode: the snapshot summary already carries the LIVE net P&L
  // (realized + unrealized − commission) + credit/commission/stop totals.
  // WS mode: derive net P&L from the live store exactly as before.
  const wsCommission = hydraState?.total_commission ?? 0;
  const wsCredit = hydraState?.total_credit_received ?? 0;
  const wsTotalStops =
    (hydraState?.call_stops_triggered ?? 0) +
    (hydraState?.put_stops_triggered ?? 0);
  const wsRealizedPnl = hydraState?.total_realized_pnl ?? 0;
  const wsUnrealizedPnl = useMemo(() => computeUnrealizedPnl(entries), [entries]);

  const commission = summary ? summary.total_commission ?? 0 : wsCommission;
  const credit = summary ? summary.total_credit_received ?? 0 : wsCredit;
  const totalStops = summary ? summary.total_stops ?? 0 : wsTotalStops;
  const netPnl = summary ? summary.net_pnl ?? 0 : wsRealizedPnl + wsUnrealizedPnl - wsCommission;

  const animatedPnl = useAnimatedNumber(netPnl);

  // Cumulative
  const cumulativePnl = metrics?.cumulative_pnl ?? 0;
  const winningDays = metrics?.winning_days ?? 0;
  const losingDays = metrics?.losing_days ?? 0;
  const totalDays = winningDays + losingDays;
  const avgPerDay = totalDays > 0 ? cumulativePnl / totalDays : 0;
  const baselineDate = metrics?.cumulative_baseline_date || "";
  const roiPct = metrics?.roi_pct ?? 0;
  const avgCapitalPerDay = metrics?.avg_capital_per_day ?? 0;

  // Comparisons
  const avgPnl = comparisons?.avg_pnl ?? 0;
  const avgCredit = comparisons?.avg_credit ?? 0;
  const avgStops = comparisons?.avg_stops ?? 0;

  const schedule = hydraState?.entry_schedule;
  // Normal path: schedule.base is post-VIX-regime truncation (2 slots at regime
  // 0-2, 1 slot at regime 3). With live bot v1.24.0+ effective numbering, Entry
  // #1 = 10:45, Entry #2 = 11:15, Entry #3 = 14:00 (conditional). baseCount is
  // used to classify entries as base (entry_number <= baseCount) vs conditional.
  // Fallback 2 matches regime 0-2 (most common); only used when state hasn't loaded.
  // (Prior fallback of 3 matched canonical pre-regime numbering and would mis-
  // classify Entry #3 as base in the rare state-unavailable window.)
  const baseCount = schedule?.base?.length ?? 2;
  // Prop mode: the summary's entries_completed is the authoritative "placed"
  // count (the polled body doesn't carry the WS store's schedule/entries here).
  const baseEntries = summary
    ? summary.entries_completed ?? 0
    : entries.filter((e) => e.entry_number <= baseCount).length;
  const conditionalEntries = summary
    ? 0
    : entries.filter((e) => e.entry_number > baseCount).length;

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
        <h3 className="label-upper mb-2">
          Cumulative
          {baselineDate && (
            <span className="ml-1 normal-case text-text-dim font-normal">
              · since {baselineDate}
            </span>
          )}
        </h3>

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

        {/* Capital efficiency row — ROI on capital deployed + avg capital/day */}
        <div className="grid grid-cols-2 gap-1 pt-3 mt-3 border-t border-border-dim">
          <StatCell label="ROI (on capital)">
            <span style={{ color: pnlColor(roiPct) }}>
              {roiPct >= 0 ? "+" : ""}{roiPct.toFixed(2)}%
            </span>
          </StatCell>
          <StatCell label="Capital / Day">
            ${Math.round(avgCapitalPerDay).toLocaleString()}
          </StatCell>
        </div>
      </div>
    </div>
  );
}
