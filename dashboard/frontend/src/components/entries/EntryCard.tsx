import type { HydraEntry } from "../../store/hydraStore";
import { formatPnL, formatTime, formatCurrency } from "../../lib/formatters";
import { pnlColor, colors } from "../../lib/tradingColors";
import { useAnimatedNumber } from "../../hooks/useAnimatedNumber";
import { CushionBar } from "./CushionBar";
import { StatusBadge } from "../shared/StatusBadge";

interface EntryCardProps {
  entry: HydraEntry;
  spxPrice?: number;
}

function getEntryStatus(e: HydraEntry) {
  if (!e.entry_time) return "pending" as const;
  if (e.call_side_stopped || e.put_side_stopped) return "stopped" as const;
  if (e.call_side_expired || e.put_side_expired) return "expired" as const;
  if (e.call_side_skipped && e.put_side_skipped) return "skipped" as const;
  // is_complete means "entry placement finished" — if no terminal flags, it's LIVE
  if (e.is_complete) return "active" as const;
  return "placing" as const;
}

function computeCushion(
  shortStrike: number,
  spxPrice: number,
  side: "call" | "put"
): number {
  if (!shortStrike || !spxPrice) return 100;
  // Distance = how far SPX is from the short strike (positive = safe OTM)
  const distance =
    side === "call"
      ? shortStrike - spxPrice
      : spxPrice - shortStrike;
  if (distance <= 0) return 0; // Breached
  // Current distance IS the cushion. Normalize: 100% when distance >= $60 OTM,
  // scales linearly to 0% as SPX approaches the short strike.
  return Math.min(100, (distance / 60) * 100);
}

export function EntryCard({ entry, spxPrice = 0 }: EntryCardProps) {
  const status = getEntryStatus(entry);
  const totalCredit = entry.call_spread_credit + entry.put_spread_credit;

  // Per-entry P&L: for active entries show credit (profit if expires OTM);
  // live option prices aren't available from the state file.
  const entryPnl = status === "active" ? totalCredit : 0;
  const animatedPnl = useAnimatedNumber(entryPnl);

  const callCushion = computeCushion(entry.short_call_strike, spxPrice, "call");
  const putCushion = computeCushion(entry.short_put_strike, spxPrice, "put");

  // Trend signal badge
  const trendLabel =
    entry.override_reason === "mkt-011"
      ? "MKT-011"
      : entry.trend_signal ?? "";

  return (
    <div
      className="bg-card rounded-lg border border-border-dim p-3 hover:bg-card-hover transition-colors"
      style={{
        borderLeftColor:
          status === "active"
            ? colors.info
            : status === "stopped"
            ? colors.loss
            : status === "expired"
            ? colors.profit
            : colors.textDim,
        borderLeftWidth: 3,
      }}
    >
      {/* Header row */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-text-primary font-semibold text-sm">
            E{entry.entry_number}
          </span>
          {trendLabel && (
            <span className="text-[10px] px-1 py-0.5 rounded bg-bg-elevated text-text-secondary">
              {trendLabel}
            </span>
          )}
        </div>
        <StatusBadge status={status} />
      </div>

      {/* Time + Credit */}
      <div className="flex justify-between text-xs mb-2">
        <span className="text-text-secondary">
          {entry.entry_time ? formatTime(entry.entry_time) : "--:--"}
        </span>
        {totalCredit > 0 && (
          <span className="text-text-primary">
            {formatCurrency(totalCredit)}
          </span>
        )}
      </div>

      {/* P&L */}
      {status === "active" && (
        <div className="text-center mb-2">
          <span
            className="text-lg font-bold font-mono"
            style={{ color: pnlColor(animatedPnl) }}
          >
            {formatPnL(animatedPnl)}
          </span>
        </div>
      )}

      {/* Cushion bars */}
      {status === "active" && (
        <div className="space-y-1">
          <CushionBar
            label="C"
            percentage={callCushion}
            skipped={entry.call_side_skipped || entry.put_only}
          />
          <CushionBar
            label="P"
            percentage={putCushion}
            skipped={entry.put_side_skipped || entry.call_only}
          />
        </div>
      )}

      {/* Strikes (collapsed for completed entries) */}
      {entry.short_call_strike > 0 && (
        <div className="mt-2 text-[10px] text-text-dim flex justify-between">
          <span>
            C:{entry.short_call_strike}/{entry.long_call_strike}
          </span>
          <span>
            P:{entry.short_put_strike}/{entry.long_put_strike}
          </span>
        </div>
      )}
    </div>
  );
}
