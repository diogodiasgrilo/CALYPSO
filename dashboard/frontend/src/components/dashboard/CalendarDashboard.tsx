/**
 * Calendar (dc_calendar) main-dashboard view — extracted from DoubleCalendar.tsx.
 *
 * Debit-native + SHAPE-DISTINCT: net debit, transform credit, open calendars,
 * recent outcomes. NO credit/buffer/spread-width fields (those are IC-only).
 *
 * Fed by the polled /api/strategies/{id}/snapshot dc_calendar body (props), not
 * a self-owned fetch — so StrategyDashboard owns the single poll and the Header
 * re-binds to the same selection.
 */

import { CalendarClock, ShieldCheck } from "lucide-react";
import { colors, pnlColor } from "../../lib/tradingColors";
import { formatPnL, formatCurrency, formatDateShort } from "../../lib/formatters";
import type {
  DCSnapshotBody,
  DCOpenCalendar,
  DCRecentOutcome,
} from "../../hooks/useStrategySnapshot";

function isTransformed(phase: string | null): boolean {
  return (phase ?? "").toLowerCase() === "transformed";
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-text-secondary">{label}</div>
      <div className="text-base font-mono" style={color ? { color } : { color: colors.textPrimary }}>
        {value}
      </div>
    </div>
  );
}

function PhaseBadge({ phase, riskFree }: { phase: string | null; riskFree: boolean }) {
  const transformed = isTransformed(phase);
  const label = transformed ? "TRANSFORMED" : (phase ?? "calendar").toUpperCase();
  const color = transformed ? colors.profit : colors.warning;
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-[9px] font-mono px-1 py-px rounded" style={{ color, border: `1px solid ${color}` }}>
        {label}
      </span>
      {riskFree && (
        <span
          className="flex items-center gap-0.5 text-[9px] font-mono px-1 py-px rounded"
          style={{ color: colors.profit, border: `1px solid ${colors.profit}` }}
          title="transform credit ≥ net debit + wing risk — locked risk-free"
        >
          <ShieldCheck size={9} /> RISK-FREE
        </span>
      )}
    </span>
  );
}

function CalendarCard({ c }: { c: DCOpenCalendar }) {
  const transformed = isTransformed(c.dc_phase);
  const debit = c.net_debit ?? 0;
  const credit = c.transform_credit ?? 0;
  const lockedDelta = transformed ? credit - debit : null;
  return (
    <div className="rounded border border-border-dim bg-card p-4 space-y-3">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-xs uppercase tracking-wide" style={{ color: colors.info }}>
          Calendar #{c.entry_number ?? "—"}
          {c.contracts ? <span className="text-text-dim"> · {c.contracts}c</span> : null}
        </div>
        <PhaseBadge phase={c.dc_phase} riskFree={c.is_risk_free} />
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Metric label="Call Strike" value={c.call_strike != null ? String(c.call_strike) : "—"} />
        <Metric label="Put Strike" value={c.put_strike != null ? String(c.put_strike) : "—"} />
        <Metric label="Short Exp" value={c.short_expiry ? formatDateShort(c.short_expiry) : "—"} />
        <Metric label="Long Exp" value={c.long_expiry ? formatDateShort(c.long_expiry) : "—"} />
      </div>

      <div className="grid grid-cols-2 gap-2 border-t border-border-dim pt-2">
        <Metric label="Net Debit" value={formatCurrency(debit)} />
        {transformed ? (
          <Metric label="Transform Credit" value={formatCurrency(credit)} color={colors.profit} />
        ) : (
          <Metric label="Transform Credit" value="—" color={colors.textDim} />
        )}
      </div>

      {lockedDelta != null && (
        <div className="text-[11px] text-text-secondary">
          Locked vs debit:{" "}
          <span className="font-mono" style={{ color: pnlColor(lockedDelta) }}>
            {formatPnL(lockedDelta)}
          </span>
        </div>
      )}
    </div>
  );
}

function OutcomesTable({ outcomes }: { outcomes: DCRecentOutcome[] }) {
  if (!outcomes.length) {
    return <div className="text-sm text-text-secondary">No closed calendars yet.</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[10px] uppercase tracking-wide text-text-secondary border-b border-border-dim">
            <th className="text-left font-medium py-1.5 pr-3">Opened</th>
            <th className="text-left font-medium py-1.5 pr-3">Closed</th>
            <th className="text-left font-medium py-1.5 pr-3">#</th>
            <th className="text-left font-medium py-1.5 pr-3">Outcome</th>
            <th className="text-right font-medium py-1.5">Realized P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {outcomes.map((o, i) => (
            <tr key={`${o.entry_date}-${o.entry_number}-${i}`} className="border-b border-border-dim/50">
              <td className="py-1.5 pr-3 text-text-secondary">{formatDateShort(o.entry_date)}</td>
              <td className="py-1.5 pr-3 text-text-secondary">{formatDateShort(o.close_date)}</td>
              <td className="py-1.5 pr-3 font-mono">{o.entry_number}</td>
              <td className="py-1.5 pr-3 text-text-primary">{o.terminal_state}</td>
              <td className="py-1.5 text-right font-mono" style={{ color: pnlColor(o.realized_pnl) }}>
                {formatPnL(o.realized_pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface CalendarDashboardProps {
  body: DCSnapshotBody;
  /** Human label from the snapshot envelope (display_name / label). */
  displayName: string;
  /** Whether this strategy is dry-run (for the subtitle note). */
  dryRun: boolean | null;
}

export function CalendarDashboard({ body, displayName, dryRun }: CalendarDashboardProps) {
  if (!body.available) {
    return (
      <div className="p-6 text-text-secondary">
        {displayName} status is unavailable. This calendar variant may not be running.
      </div>
    );
  }

  const s = body.calendar_summary ?? {
    open_count: 0,
    transformed_count: 0,
    risk_free_count: 0,
    realized_pnl_recent: 0,
  };
  const openCalendars = body.open_calendars ?? [];
  const outcomes = body.recent_outcomes ?? [];

  return (
    <div className="space-y-4 p-1">
      <div className="flex items-center gap-2">
        <CalendarClock size={18} style={{ color: colors.info }} />
        <div>
          <h1 className="text-sm font-semibold text-text-primary">{displayName}</h1>
          <div className="text-[11px] text-text-secondary">
            Multi-day double calendar (net debit).{" "}
            {dryRun !== false && (
              <span style={{ color: colors.textDim }}>Dry-run — places no real orders.</span>
            )}
          </div>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded border border-border-dim bg-card p-3">
          <Metric label="Open Calendars" value={String(s.open_count)} />
        </div>
        <div className="rounded border border-border-dim bg-card p-3">
          <Metric label="Transformed" value={String(s.transformed_count)} />
        </div>
        <div className="rounded border border-border-dim bg-card p-3">
          <Metric
            label="Risk-Free"
            value={String(s.risk_free_count)}
            color={s.risk_free_count > 0 ? colors.profit : undefined}
          />
        </div>
        <div className="rounded border border-border-dim bg-card p-3">
          <Metric
            label="Recent Realized P&L"
            value={formatPnL(s.realized_pnl_recent)}
            color={pnlColor(s.realized_pnl_recent)}
          />
        </div>
      </div>

      {/* Open calendars */}
      <div>
        <h2 className="text-[10px] uppercase tracking-wide text-text-secondary mb-2">Open Calendars</h2>
        {openCalendars.length ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {openCalendars.map((c, i) => (
              <CalendarCard key={`${c.strategy_id ?? c.entry_number}-${i}`} c={c} />
            ))}
          </div>
        ) : (
          <div className="text-sm text-text-secondary">No open calendars right now.</div>
        )}
      </div>

      {/* Recent outcomes */}
      <div>
        <h2 className="text-[10px] uppercase tracking-wide text-text-secondary mb-2">Recent Outcomes</h2>
        <div className="rounded border border-border-dim bg-card p-4">
          <OutcomesTable outcomes={outcomes} />
        </div>
      </div>
    </div>
  );
}
