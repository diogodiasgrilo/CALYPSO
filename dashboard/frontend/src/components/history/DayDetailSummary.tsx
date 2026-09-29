import { pnlColor, colors } from "../../lib/tradingColors";
import { formatPnL } from "../../lib/formatters";
import type { DaySummary } from "./types";

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div className="bg-card rounded-lg border border-border-dim p-3">
      <div className="text-3xs text-text-secondary uppercase tracking-wider mb-1">
        {label}
      </div>
      <div className="text-sm font-mono font-semibold" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

export function DayDetailSummary({ summary }: { summary: DaySummary }) {
  const spxChange = summary.spx_close && summary.spx_open
    ? summary.spx_close - summary.spx_open
    : 0;
  const spxChangePct = summary.spx_open
    ? (spxChange / summary.spx_open) * 100
    : 0;

  /* A day's headline can be dominated by P&L that was never a trade. See
     DaySummary.unattributed_overlay_pnl — on 2026-09-24 a +$5,995 failed-entry
     unwind turned a −$2,663 trading day into a +$3,331 headline. The headline
     stays (it is what the account actually made) and the trading number is
     shown beside it, rather than silently conflating the two. */
  const unattributed = summary.unattributed_overlay_pnl || 0;
  const tradingPnl = (summary.net_pnl || 0) - unattributed;
  const distorted = Math.abs(unattributed) >= 0.01;

  return (
    <div className="space-y-3">
      {distorted && (
        <div className="rounded-lg border border-border-dim bg-bg-elevated/60 px-3 py-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="text-3xs uppercase tracking-wider text-text-dim">
            Of that headline
          </span>
          <span className="text-xs">
            <span className="text-text-dim">traded </span>
            <span
              className="font-semibold tabular-nums"
              style={{ color: pnlColor(tradingPnl) }}
            >
              {formatPnL(tradingPnl)}
            </span>
          </span>
          <span className="text-xs">
            <span className="text-text-dim">failed-entry unwind </span>
            <span
              className="font-semibold tabular-nums"
              style={{ color: pnlColor(unattributed) }}
            >
              {formatPnL(unattributed)}
            </span>
          </span>
          <span className="text-3xs text-text-dim flex-1 min-w-[14rem]">
            real money, but not a trading result — it belongs to the day, not to
            any entry
          </span>
        </div>
      )}
      <div className="grid grid-cols-3 gap-2 max-sm:grid-cols-2">
        <StatCard
          label="Net P&L"
          value={formatPnL(summary.net_pnl || 0)}
          color={pnlColor(summary.net_pnl || 0)}
        />
        <StatCard
          label="Gross P&L"
          value={formatPnL(summary.gross_pnl || 0)}
          color={pnlColor(summary.gross_pnl || 0)}
        />
        <StatCard
          label="Commission"
          value={`$${(summary.commission || 0).toFixed(2)}`}
          color={colors.textPrimary}
        />
        <StatCard
          label="Entries"
          value={String(summary.entries_placed || 0)}
          color={colors.textPrimary}
        />
        <StatCard
          label="Stops"
          value={String(summary.actual_stops ?? summary.entries_stopped ?? 0)}
          color={(summary.actual_stops ?? summary.entries_stopped ?? 0) > 0 ? colors.loss : colors.textPrimary}
        />
        <StatCard
          label="Expired"
          value={String(summary.entries_expired || 0)}
          color={(summary.entries_expired || 0) > 0 ? colors.profit : colors.textPrimary}
        />
      </div>

      <div className="flex items-center gap-4 text-2xs text-text-secondary">
        <span>
          SPX: {summary.spx_open?.toFixed(0) || "\u2014"} → {summary.spx_close?.toFixed(0) || "\u2014"}
          {spxChange !== 0 && (
            <span
              className="ml-1 font-mono"
              style={{ color: pnlColor(spxChange) }}
            >
              ({spxChange > 0 ? "+" : ""}{spxChangePct.toFixed(2)}%)
            </span>
          )}
        </span>
        <span>
          VIX: {summary.vix_open?.toFixed(1) || "\u2014"}
        </span>
        {summary.day_type && (
          <span className="text-text-dim">{summary.day_type}</span>
        )}
      </div>
    </div>
  );
}
