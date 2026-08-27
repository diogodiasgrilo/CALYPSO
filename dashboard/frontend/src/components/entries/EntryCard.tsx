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
  /** Override the "E{n}" label (e.g. canonical slot number when VIX regime
   * renumbers entries at runtime). Falls back to `entry.entry_number` when omitted. */
  label?: string;
}

function getEntryStatus(e: HydraEntry): {
  status: EntryStatus;
  stoppedSide?: "call" | "put";
} {
  if (!e.entry_time) return { status: "pending" };
  // 2026-07-31: a genuine order-placement FAILURE (broker accepted the order
  // but it never filled after exhausting retries) also sets call_side_skipped
  // AND put_side_skipped=True (so downstream "no real position" checks keep
  // working), so this must be checked BEFORE the generic skipped branch below
  // — otherwise a failure silently renders as a routine strategic skip, which
  // is exactly the bug this fixes (see skip_reason for the human-readable
  // detail either way).
  if (e.execution_failed) return { status: "failed" };
  if (e.call_side_skipped && e.put_side_skipped) return { status: "skipped" };

  // Prefer the explicit close_reason set at close time. The Brandon TP / breach
  // close path flips *_side_stopped AND *_side_expired (a generic "side closed"
  // marker), so flag-inference alone mislabels a PROFITABLE take-profit as a
  // "double stop". Mirror the backend _entry_disposition (routers/variants.py).
  const reason = (e.close_reason || "").toUpperCase();
  if (reason === "TP") return { status: "take_profit" };
  if (reason === "BREACH") return { status: "breach" };
  // EOD safety flatten / generic early-close reuses *_side_expired (and may set
  // *_side_stopped), so without this it mislabels as "expired"/"stopped". Render
  // a managed "Flattened" close: close_reason EOD_FLATTEN (new) OR early_closed
  // with no TP/BREACH reason (covers entries flattened before that tag existed).
  if (reason === "EOD_FLATTEN" || e.early_closed) return { status: "flattened" };

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
  // A Brandon TP / GEX-breach early-close flips BOTH *_side_expired AND
  // *_side_stopped (a generic "side closed" marker). Running both the expired
  // (+credit) and stopped (-loss) branches double-counts — for E#1's
  // negative-credit call this produced a wrong +$128.10 (true +$390.60). An
  // early-closed side has a SINGLE realized P&L = credit - close_cost; we use
  // actual_*_stop_debit as the close-cost mark (the exact fill-based cost lives
  // in the backend/DB trade_stops). The stopped/expired branches below are
  // gated on !earlyClosed so a TP runs ONLY the single early-closed term.
  const earlyClosed = !!e.early_closed || !!e.close_reason;

  const callActive = !e.call_side_stopped && !e.call_side_skipped && !e.call_side_expired;
  const putActive = !e.put_side_stopped && !e.put_side_skipped && !e.put_side_expired;
  const callStopped = e.call_side_stopped && !earlyClosed;
  const putStopped = e.put_side_stopped && !earlyClosed;
  const callExpired = e.call_side_expired && !earlyClosed;
  const putExpired = e.put_side_expired && !earlyClosed;
  const callClosed = earlyClosed && (e.call_side_stopped || e.call_side_expired);
  const putClosed = earlyClosed && (e.put_side_stopped || e.put_side_expired);
  // Realized for an early-closed side: credit kept minus the cost to close.
  // 2026-08-19 (review finding): `actual_*_stop_debit` defaults to 0.0 both
  // when a close was genuinely free AND when the real fill price simply
  // wasn't captured (IBKR has no deferred-correction retry for this path,
  // unlike the old Saxo-era fill-lookup) — the two cases are indistinguishable
  // from this field alone. Assuming "0 debit = free close" in that second case
  // silently shows a phantom profit on a side that may have closed at a real
  // loss. Mirror icEntryView.tsx's resolveSide() guard (`debit != null &&
  // debit > 0`): only credit the realized amount when a real debit was
  // actually confirmed; otherwise contribute $0 rather than assert a number
  // that can't be backed up. This can UNDER-state a genuine win (self-evident
  // as an odd $0 on an otherwise-favorable close) but can never OVER-state
  // one — the safer direction for a dollar figure a trader relies on.
  const callDebitConfirmed = (e.actual_call_stop_debit ?? 0) > 0;
  const putDebitConfirmed = (e.actual_put_stop_debit ?? 0) > 0;
  const callRealized = callDebitConfirmed ? e.call_spread_credit - e.actual_call_stop_debit! : 0;
  const putRealized = putDebitConfirmed ? e.put_spread_credit - e.actual_put_stop_debit! : 0;

  // Max profit = credit from sides that can still expire worthless
  let maxProfit = 0;
  if (callActive) maxProfit += e.call_spread_credit;
  if (putActive) maxProfit += e.put_spread_credit;
  // Expired sides already earned their credit
  if (callExpired) maxProfit += e.call_spread_credit;
  if (putExpired) maxProfit += e.put_spread_credit;
  // Early-closed (TP/breach) sides: single realized term.
  if (callClosed) maxProfit += callRealized;
  if (putClosed) maxProfit += putRealized;

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
  if (callExpired) currentPnl += e.call_spread_credit;
  if (putExpired) currentPnl += e.put_spread_credit;
  // Early-closed (TP/breach) sides: single realized term (no double-count).
  if (callClosed) currentPnl += callRealized;
  if (putClosed) currentPnl += putRealized;
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

export function EntryCard({ entry, isConditional, label }: EntryCardProps) {
  const { status, stoppedSide } = getEntryStatus(entry);
  const totalCredit = entry.call_spread_credit + entry.put_spread_credit;
  const displayLabel = label ?? `E${entry.entry_number}`;

  // Execution FAILURE (2026-07-31): the broker accepted the order but it never
  // filled after exhausting retries — a broker/execution-layer problem, not a
  // strategic choice. Distinct from "skipped" deliberately: full opacity (not
  // faded) and a loss-colored left border so it's impossible to mistake for a
  // routine skip at a glance. Reuses skip_reason for the error detail (set to
  // a clear "Execution failed after N attempts: ..." message by the backend).
  if (status === "failed") {
    return (
      <div
        className="bg-card rounded-lg p-3 border border-border-dim"
        style={{
          borderLeftColor: colors.loss,
          borderLeftWidth: 3,
          borderLeftStyle: "solid",
        }}
      >
        <div className="flex items-center justify-between mb-2">
          <span className="font-semibold text-sm" style={{ color: colors.loss }}>
            {displayLabel}
          </span>
          <StatusBadge status="failed" />
        </div>
        <div className="text-xs text-text-secondary mb-1">
          {entry.entry_time ? formatTime(entry.entry_time) : "--:--"}
        </div>
        {entry.skip_reason && (
          <div className="text-[10px] leading-tight" style={{ color: colors.loss }}>
            {entry.skip_reason}
          </div>
        )}
      </div>
    );
  }

  // Fully-skipped entry: minimal card with reason (guard handles legacy data without skip_reason)
  if (status === "skipped") {
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
            {displayLabel}
          </span>
          <StatusBadge status="skipped" />
        </div>
        <div className="text-xs text-text-secondary mb-1">
          {entry.entry_time ? formatTime(entry.entry_time) : "--:--"}
        </div>
        {(entry.call_spread_credit > 0 || entry.put_spread_credit > 0) && (
          <div className="flex gap-3 text-[10px] mt-1 mb-1">
            {entry.call_spread_credit > 0 && (
              <span className="text-text-secondary">
                Call: <span className="text-text-secondary">${entry.call_spread_credit.toFixed(0)}</span>
              </span>
            )}
            {entry.put_spread_credit > 0 && (
              <span className="text-text-secondary">
                Put: <span className="text-text-secondary">${entry.put_spread_credit.toFixed(0)}</span>
              </span>
            )}
          </div>
        )}
        {entry.skip_reason && (
          <div className="text-[10px] text-text-dim leading-tight">
            {entry.skip_reason}
          </div>
        )}
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
  const callCushion = computeCushion(entry.call_spread_value ?? 0, entry.effective_call_stop ?? entry.call_side_stop);
  const putCushion = computeCushion(entry.put_spread_value ?? 0, entry.effective_put_stop ?? entry.put_side_stop);

  // Trend signal badge — show override reason if it drove the entry type
  const trendLabel =
    entry.override_reason === "mkt-011" ? "MKT-011"
    : entry.override_reason === "mkt-035" ? "MKT-035"
    : entry.override_reason === "mkt-038" ? "MKT-038"
    : entry.override_reason === "mkt-040" ? "MKT-040"
    : entry.override_reason === "mkt-039" ? "MKT-039"
    : entry.override_reason === "upday-035" ? "Upday-035"
    : entry.override_reason === "downday-035" ? "Downday-035"
    : entry.override_reason === "base-downday" ? "Base-Downday"
    // Variant F (Ghauri) — without these, an EM-touch entry falls through to
    // entry.trend_signal (HYDRA's inherited EMA label), which is meaningless
    // for a touch-triggered strategy that doesn't use the trend filter.
    : entry.override_reason === "EM_UPPER_TOUCH" ? "EM Upper Touch"
    : entry.override_reason === "EM_LOWER_TOUCH" ? "EM Lower Touch"
    : entry.trend_signal ?? "";

  // Show the P&L line for any entry with a meaningful realized/live P&L — active,
  // stopped, AND the Brandon early-closes (take_profit / breach / flattened).
  // Omitting take_profit here was the regression that left a TP card showing
  // only its credit ($665) with no P&L; "flattened" (EOD safety-flatten,
  // MKT-047) had the identical bug — computeEntryPnl() already handles it
  // correctly via the earlyClosed branch above, this line was just never
  // updated when the "flattened" status was introduced (2026-08-19 fix).
  const showLiveData =
    status === "active" || status === "stopped_single" || status === "stopped" ||
    status === "take_profit" || status === "breach" || status === "flattened";

  // Determine border color
  const borderColor =
    status === "active"
      ? colors.info
      : status === "take_profit"
        ? colors.profit // profitable close = green
        : status === "breach"
          ? colors.warning // defensive GEX-breach close = amber
          : status === "stopped"
            ? colors.loss
            : status === "stopped_single"
              ? colors.warning
              : status === "expired"
                ? colors.profit
                : status === "flattened"
                  // Unlike take_profit (always favorable) or stopped (always a
                  // loss), a managed EOD flatten can land either way — key the
                  // border off the actual computed result instead of a fixed
                  // color (2026-08-19, alongside the showLiveData fix that
                  // first made this status's real P&L visible at all).
                  ? (currentPnl >= 0 ? colors.profit : colors.loss)
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
            {displayLabel}
          </span>
          {trendLabel && (
            <span className="text-[10px] px-1 py-0.5 rounded bg-bg-elevated text-text-secondary">
              {trendLabel}
            </span>
          )}
          {/* Was gated on isConditional (E6/E7 conditional-slot cards only), which
              meant this never rendered for a variant where EVERY entry is
              one-sided by design (Variant F / Ghauri) rather than an occasional
              conditional fallback. B/C's own one-sided entries are suppressed
              entirely (require-both-sides) so this gate change has no effect
              on their cards. */}
          {(entry.put_only || entry.call_only) && (
            <span
              className="text-[9px] px-1 py-0.5 rounded"
              style={{
                backgroundColor: `${entry.put_only ? "#a371f7" : colors.info}15`,
                color: entry.put_only ? "#a371f7" : colors.info,
              }}
            >
              {entry.put_only ? "PUT ONLY" : "CALL ONLY"}
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

      {/* Strikes — show whenever EITHER side was placed (a put-only / call-only
          entry has the other short at 0; the old `short_call_strike > 0` gate
          hid the strikes entirely for put-only entries). Label the un-placed
          side "skipped" instead of "0/0". A strangle (Variant G) has no long
          wing at all — long_*_strike is always 0, not "skipped" — so show just
          the naked short strike instead of a misleading "short/0". */}
      {(entry.short_call_strike > 0 || entry.short_put_strike > 0) && (
        <div className="mt-2 text-[10px] text-text-dim flex justify-between">
          <span>
            C:{entry.call_side_skipped || !(entry.short_call_strike > 0)
              ? 'skipped'
              : entry.long_call_strike > 0
              ? `${entry.short_call_strike}/${entry.long_call_strike}`
              : `${entry.short_call_strike} (naked)`}
          </span>
          <span>
            P:{entry.put_side_skipped || !(entry.short_put_strike > 0)
              ? 'skipped'
              : entry.long_put_strike > 0
              ? `${entry.short_put_strike}/${entry.long_put_strike}`
              : `${entry.short_put_strike} (naked)`}
          </span>
        </div>
      )}

      {/* Brandon defensive overlay(s) — a hedge structure (debit spread before
          12:30 ET, butterfly after) stacked on this IC when a short side is
          threatened. Previously invisible on the dashboard; on variant B these
          drive most of the daily P&L. `pnl` is a display value from the legs'
          intrinsic at the current/close SPX (matches how the bot books it). */}
      {Array.isArray(entry.overlays) && entry.overlays.length > 0 && (
        <div className="mt-2 space-y-1">
          {entry.overlays.map((ov, i) => {
            const strikes = ov.legs.map((l) => l.strike).join("/");
            const name = `${ov.threatened_side} ${String(ov.structure).replace(/_/g, " ")}`;
            return (
              <div
                key={i}
                className="rounded px-2 py-1 text-[10px]"
                style={{ backgroundColor: colors.bgElevated, borderLeft: `2px solid ${colors.warning}` }}
              >
                <div className="flex items-center justify-between">
                  <span className="uppercase tracking-wide" style={{ color: colors.warning }}>
                    ⚡ Overlay · {name}
                  </span>
                  {ov.pnl != null && (
                    <span style={{ color: pnlColor(ov.pnl) }}>{formatPnL(ov.pnl)}</span>
                  )}
                </div>
                <div className="flex items-center justify-between mt-0.5 text-text-secondary">
                  <span>{strikes}</span>
                  <span>debit ${Math.abs(ov.debit).toFixed(0)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
