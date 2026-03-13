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
      <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">
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

  return (
    <div className="space-y-3">
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
          value={String(summary.entries_stopped || 0)}
          color={(summary.entries_stopped || 0) > 0 ? colors.loss : colors.textPrimary}
        />
        <StatCard
          label="Expired"
          value={String(summary.entries_expired || 0)}
          color={(summary.entries_expired || 0) > 0 ? colors.profit : colors.textPrimary}
        />
      </div>

      <div className="flex items-center gap-4 text-[11px] text-text-secondary">
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
