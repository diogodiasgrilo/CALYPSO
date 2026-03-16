import type { HydraEntry } from "../../store/hydraStore";
import { formatPnL, formatTime, formatCurrency } from "../../lib/formatters";
import { pnlColor, colors, statusColor } from "../../lib/tradingColors";
import { useAnimatedNumber } from "../../hooks/useAnimatedNumber";
import { CushionBar } from "./CushionBar";
import { StatusBadge } from "../shared/StatusBadge";
import type { EntryStatus } from "../shared/StatusBadge";

interface EntryCardProps {
  entry: HydraEntry;
  /** Conditional entries (E6/E7) get distinct visual styling */
  isConditional?: boolean;
}

function getEntryStatus(e: HydraEntry): {
  status: EntryStatus;
  stoppedSide?: "call" | "put";
} {
  if (!e.entry_time) return { status: "pending" };
  if (e.call_side_skipped && e.put_side_skipped) return { status: "skipped" };

  const callStopped = e.call_side_stopped;
  const putStopped = e.put_side_stopped;
  const bothStopped = callStopped && putStopped;

  if (bothStopped) return { status: "stopped" }; // double stop = red
  if (callStopped) return { status: "stopped_single", stoppedSide: "call" };
  if (putStopped) return { status: "stopped_single", stoppedSide: "put" };

  if (e.call_side_expired || e.put_side_expired) return { status: "expired" };

  // Entry has entry_time and no terminal flags — it's LIVE
  // (is_complete may be null/undefined in state file for active entries)
  if (e.entry_time) return { status: "active" };
  return { status: "placing" };
}

// Matches bot's cushion formula: (stop_level - spread_value) / stop_level * 100
function computeCushion(spreadValue: number, stopLevel: number): number {
  if (!stopLevel || stopLevel <= 0) return 100;
  const cushion = ((stopLevel - spreadValue) / stopLevel) * 100;
  return Math.max(0, Math.min(100, cushion));
}

// Compute current + max P&L for an entry using theoretical values
function computeEntryPnl(e: HydraEntry) {
  const callActive = !e.call_side_stopped && !e.call_side_skipped && !e.call_side_expired;
  const putActive = !e.put_side_stopped && !e.put_side_skipped && !e.put_side_expired;
  const callStopped = e.call_side_stopped;
  const putStopped = e.put_side_stopped;

  // Max profit = credit from sides that can still expire worthless
  let maxProfit = 0;
  if (callActive) maxProfit += e.call_spread_credit;
  if (putActive) maxProfit += e.put_spread_credit;
  // Expired sides already earned their credit
  if (e.call_side_expired) maxProfit += e.call_spread_credit;
  if (e.put_side_expired) maxProfit += e.put_spread_credit;

  // Stopped sides: actual loss (or theoretical fallback) + surviving long value + salvage revenue
  if (callStopped) {
    const actualDebit = e.actual_call_stop_debit ?? 0;
    const loss = actualDebit > 0
      ? Math.max(0, actualDebit - e.call_spread_credit)
      : Math.max(0, e.call_side_stop - e.call_spread_credit);
    maxProfit -= loss;
    maxProfit += (e.call_long_value ?? 0) + (e.call_long_sold_revenue ?? 0);
  }
  if (putStopped) {
    const actualDebit = e.actual_put_stop_debit ?? 0;
    const loss = actualDebit > 0
      ? Math.max(0, actualDebit - e.put_spread_credit)
      : Math.max(0, e.put_side_stop - e.put_spread_credit);
    maxProfit -= loss;
    maxProfit += (e.put_long_value ?? 0) + (e.put_long_sold_revenue ?? 0);
  }

  // Current P&L = credit earned so far minus cost-to-close active sides
  let currentPnl = 0;
  // Active sides: credit minus current spread value
  if (callActive) currentPnl += e.call_spread_credit - (e.call_spread_value ?? 0);
  if (putActive) currentPnl += e.put_spread_credit - (e.put_spread_value ?? 0);
  // Expired sides: full credit kept
  if (e.call_side_expired) currentPnl += e.call_spread_credit;
  if (e.put_side_expired) currentPnl += e.put_spread_credit;
  // Stopped sides: actual loss (or theoretical fallback) + long leg recovery
  if (callStopped) {
    const actualDebit = e.actual_call_stop_debit ?? 0;
    currentPnl -= actualDebit > 0
      ? Math.max(0, actualDebit - e.call_spread_credit)
      : Math.max(0, e.call_side_stop - e.call_spread_credit);
    currentPnl += (e.call_long_value ?? 0);
    currentPnl += (e.call_long_sold_revenue ?? 0);
  }
  if (putStopped) {
    const actualDebit = e.actual_put_stop_debit ?? 0;
    currentPnl -= actualDebit > 0
      ? Math.max(0, actualDebit - e.put_spread_credit)
      : Math.max(0, e.put_side_stop - e.put_spread_credit);
    currentPnl += (e.put_long_value ?? 0);
    currentPnl += (e.put_long_sold_revenue ?? 0);
  }

  // Subtract commission for NET P&L (consistent with TODAY card)
  const commission = (e.open_commission ?? 0) + (e.close_commission ?? 0);
  currentPnl -= commission;
  maxProfit -= commission;

  return { currentPnl, maxProfit };
}

export function EntryCard({ entry, isConditional }: EntryCardProps) {
  const { status, stoppedSide } = getEntryStatus(entry);
  const totalCredit = entry.call_spread_credit + entry.put_spread_credit;

  // Fully-skipped entry: minimal card with reason
  if (status === "skipped" && entry.skip_reason) {
    return (
      <div
        className={`bg-card rounded-lg p-3 ${
          isConditional ? "border border-dashed border-border-dim" : "border border-border-dim"
        }`}
        style={{
          borderLeftColor: colors.textDim,
          borderLeftWidth: 3,
          borderLeftStyle: "solid",
          opacity: 0.7,
        }}
      >
        <div className="flex items-center justify-between mb-2">
          <span className="text-text-dim font-semibold text-sm">
            E{entry.entry_number}
          </span>
          <StatusBadge status="skipped" />
        </div>
        <div className="text-xs text-text-secondary mb-1">
          {entry.entry_time ? formatTime(entry.entry_time) : "--:--"}
        </div>
        <div className="text-[10px] text-text-dim leading-tight">
          {entry.skip_reason}
        </div>
      </div>
    );
  }

  const { currentPnl, maxProfit } = computeEntryPnl(entry);
  const animatedPnl = useAnimatedNumber(currentPnl);
  const animatedMax = useAnimatedNumber(maxProfit);

  // Progress toward max profit (0-100%)
  // When maxProfit <= 0 (stopped entry, best outcome is a loss), show full bar
  // since the P&L is realized — color (red/amber) indicates loss
  const progressPct =
    maxProfit > 0
      ? Math.max(0, Math.min(100, (currentPnl / maxProfit) * 100))
      : currentPnl !== 0 ? 100 : 0;

  // Use bot's actual cushion: (stop_level - spread_value) / stop_level
  const callCushion = computeCushion(entry.call_spread_value ?? 0, entry.call_side_stop);
  const putCushion = computeCushion(entry.put_spread_value ?? 0, entry.put_side_stop);

  // Trend signal badge
  const trendLabel =
    entry.override_reason === "mkt-011"
      ? "MKT-011"
      : entry.trend_signal ?? "";

  // Show live data for active entries AND single-stopped entries (surviving side still live)
  const showLiveData =
    status === "active" || status === "stopped_single" || status === "stopped";

  // Determine border color
  const borderColor =
    status === "active"
      ? colors.info
      : status === "stopped"
        ? colors.loss
        : status === "stopped_single"
          ? colors.warning
          : status === "expired"
            ? colors.profit
            : colors.textDim;

  // Determine which sides are still active (for cushion display on stopped entries)
  const callStillActive = !entry.call_side_stopped && !entry.call_side_skipped && !entry.call_side_expired;
  const putStillActive = !entry.put_side_stopped && !entry.put_side_skipped && !entry.put_side_expired;
  const showCushion = status === "active" || status === "stopped_single";

  return (
    <div
      className={`bg-card rounded-lg p-3 hover:bg-card-hover transition-colors ${
        isConditional ? "border border-dashed border-border-dim" : "border border-border-dim"
      }`}
      style={{
        borderLeftColor: borderColor,
        borderLeftWidth: 3,
        borderLeftStyle: "solid",
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
          {isConditional && (
            <span className="text-[9px] px-1 py-0.5 rounded text-text-dim" style={{ backgroundColor: `${colors.info}15`, color: colors.info }}>
              CALL ONLY
            </span>
          )}
        </div>
        <StatusBadge status={status} stoppedSide={stoppedSide} />
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

      {/* P&L: current vs max + progress bar */}
      {showLiveData && (
        <div className="mb-2">
          <div className="flex items-baseline justify-between mb-1">
            <span
              className="metric-body font-bold"
              style={{
                color: status === "stopped_single" && currentPnl < 0
                  ? statusColor("stopped_single") // amber for single stop loss
                  : pnlColor(animatedPnl),
              }}
            >
              {formatPnL(animatedPnl)}
            </span>
            <span className="text-[10px] text-text-dim">
              / {formatPnL(animatedMax)}
            </span>
          </div>
          {/* P&L progress bar */}
          <div className="h-1.5 rounded-full bg-bg-elevated overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500 ease-out"
              style={{
                width: `${progressPct}%`,
                backgroundColor:
                  currentPnl >= 0
                    ? colors.profit
                    : status === "stopped_single"
                      ? colors.warning
                      : colors.loss,
              }}
            />
          </div>
        </div>
      )}

      {/* Cushion bars — show for active entries AND single-stopped (surviving side) */}
      {showCushion && (
        <div className="space-y-1">
          <CushionBar
            label="C"
            percentage={callCushion}
            skipped={entry.call_side_skipped || entry.put_only}
            stopped={entry.call_side_stopped}
            active={callStillActive}
          />
          <CushionBar
            label="P"
            percentage={putCushion}
            skipped={entry.put_side_skipped || entry.call_only}
            stopped={entry.put_side_stopped}
            active={putStillActive}
          />
        </div>
      )}

      {/* Strikes */}
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
