/**
 * DEBIT-shape (double-calendar) group comparison renderer.
 *
 * Fed by /api/strategies/groups/{calendar_multiday}/comparison + /aggregate.
 * SHAPE-DISTINCT from the IC comparison: debit-native fields only — net debit,
 * capital at risk (= SUM(net_debit)), open calendars, recent outcomes,
 * cumulative realized P&L attributed to close_date. NO credit / buffer /
 * spread-width fields, ever (audit AUD-3-F2 / AUD-3-F5). A single chart never
 * mixes credit and debit because this renderer only ever receives debit data.
 */

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Legend,
  ReferenceLine,
} from "recharts";
import { colors, pnlColor } from "../../lib/tradingColors";
import { formatPnL, formatCurrency, formatDateShort } from "../../lib/formatters";
import { accentForStrategy } from "../../lib/pnlShape";
import type {
  DCOpenCalendar,
  DCRecentOutcome,
} from "../../hooks/useStrategySnapshot";

// ── Payload shapes (debit-native; mirror strategies.py _calendar_group_*) ──
interface CalendarLifetime {
  cumulative_pnl?: number;
  capital_at_risk?: number;
  total_calendars?: number;
  winning_calendars?: number;
  losing_calendars?: number;
  win_rate?: number;
  roi_pct?: number;
  [key: string]: number | undefined;
}

export interface CalendarMember {
  id: string;
  display_name: string;
  available: boolean;
  lifetime?: CalendarLifetime;
  recent_outcomes?: DCRecentOutcome[];
  open_calendars?: DCOpenCalendar[];
  open_summary?: {
    open_count?: number;
    transformed_count?: number;
    risk_free_count?: number;
    realized_pnl_recent?: number;
  };
}

export interface CalendarComparisonPayload {
  group_id: string;
  label: string;
  family: string;
  pnl_shape: string;
  baseline_id: string | null;
  member_ids: string[];
  calendars: {
    leaderboard: { winner: string; scores: Record<string, number>; basis: string };
    members: Record<string, CalendarMember>;
  };
}

interface CalendarAggMember {
  id: string;
  display_name: string;
  available: boolean;
  lifetime?: CalendarLifetime;
  daily?: Array<{ date: string; net_pnl: number }>;
  cumulative_curve?: Array<{ date: string; net_pnl: number; cumulative: number }>;
}

export interface CalendarAggregatePayload {
  // The backend's group /aggregate nests the calendar payload under `calendars`
  // as `{members}`; group_id lives on the outer envelope, so it's optional here.
  group_id?: string;
  members: Record<string, CalendarAggMember>;
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-text-secondary">{label}</div>
      <div className="text-base font-mono mt-0.5" style={color ? { color } : { color: colors.textPrimary }}>
        {value}
      </div>
    </div>
  );
}

function MemberPanel({ m, accent }: { m: CalendarMember; accent: string }) {
  if (!m.available) {
    return (
      <div className="rounded border border-border-dim bg-card p-4">
        <div className="text-xs uppercase tracking-wide" style={{ color: accent }}>
          {m.display_name}
        </div>
        <div className="mt-2 text-text-secondary text-sm">Variant not running.</div>
      </div>
    );
  }
  const lt = m.lifetime ?? {};
  const open = m.open_summary ?? {};
  const cumulative = lt.cumulative_pnl ?? 0;
  const capital = lt.capital_at_risk ?? 0;
  const outcomes = m.recent_outcomes ?? [];
  const openCalendars = m.open_calendars ?? [];

  return (
    <div className="rounded border border-border-dim bg-card p-4 space-y-3">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-xs uppercase tracking-wide truncate" style={{ color: accent }}>
          {m.display_name}
        </div>
        <div className="text-xs text-text-secondary whitespace-nowrap">
          {open.open_count ?? openCalendars.length} open
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-sm">
        <Metric label="Cumulative P&L" value={formatPnL(cumulative)} color={pnlColor(cumulative)} />
        <Metric label="Capital at Risk" value={formatCurrency(capital)} />
        <Metric label="Win Rate" value={`${((lt.win_rate ?? 0) * 100).toFixed(0)}%`} />
      </div>

      {/* Open calendars (debit-native) */}
      {openCalendars.length > 0 && (
        <div className="space-y-1 border-t border-border-dim pt-2">
          <div className="text-[10px] uppercase tracking-wide text-text-secondary">Open Calendars</div>
          {openCalendars.map((c, i) => (
            <div key={i} className="text-xs font-mono flex justify-between text-text-secondary">
              <span>
                #{c.entry_number ?? "—"} · C{c.call_strike ?? "—"}/P{c.put_strike ?? "—"}
              </span>
              <span className="text-text-dim">debit {formatCurrency(c.net_debit ?? 0)}</span>
            </div>
          ))}
        </div>
      )}

      {/* Recent outcomes */}
      <div className="border-t border-border-dim pt-2">
        <div className="text-[10px] uppercase tracking-wide text-text-secondary mb-1">Recent Outcomes</div>
        {outcomes.length === 0 ? (
          <div className="text-xs text-text-dim italic">No closed calendars yet.</div>
        ) : (
          outcomes.slice(0, 5).map((o, i) => (
            <div key={i} className="text-xs font-mono flex justify-between">
              <span className="text-text-secondary">
                {formatDateShort(o.close_date)} · {o.terminal_state}
              </span>
              <span style={{ color: pnlColor(o.realized_pnl) }}>{formatPnL(o.realized_pnl)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function CumulativeChart({ agg, memberIds }: { agg: CalendarAggregatePayload | null; memberIds: string[] }) {
  if (!agg) {
    return (
      <div className="rounded border border-border-dim bg-card p-3">
        <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">Cumulative P&amp;L (debit-native)</div>
        <div className="text-sm text-text-dim italic">Loading…</div>
      </div>
    );
  }

  // Merge each member's close-date-attributed cumulative curve onto a shared
  // date axis. Close-date attribution → the curve is monotonic in time and does
  // not retroactively mutate (audit AUD-4-F4).
  type Curve = NonNullable<CalendarAggMember["cumulative_curve"]>;
  const series = memberIds
    .map((id) => {
      const m = agg.members[id.toUpperCase()];
      return m?.available
        ? { id: id.toUpperCase(), label: m.display_name, curve: (m.cumulative_curve ?? []) as Curve }
        : null;
    })
    .filter((x): x is { id: string; label: string; curve: Curve } => !!x);

  const totalPoints = series.reduce((acc, s) => acc + s.curve.length, 0);
  if (totalPoints === 0) {
    return (
      <div className="rounded border border-border-dim bg-card p-3">
        <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">Cumulative P&amp;L (debit-native)</div>
        <div className="text-sm text-text-dim italic">
          No closed calendars yet — the curve appears once a calendar settles (P&L attributed to close date).
        </div>
      </div>
    );
  }

  const allDates = new Set<string>();
  const lookups = series.map((s) => {
    const map = new Map<string, number>();
    for (const p of s.curve) {
      map.set(p.date, p.cumulative);
      allDates.add(p.date);
    }
    return { id: s.id, label: s.label, map };
  });
  const sortedDates = [...allDates].sort();
  const merged = sortedDates.map((d) => {
    const row: Record<string, string | number | null> = { date: d };
    for (const l of lookups) row[`cum_${l.id.toLowerCase()}`] = l.map.has(d) ? (l.map.get(d) as number) : null;
    return row;
  });
  const labelByKey = Object.fromEntries(series.map((s) => [`cum_${s.id.toLowerCase()}`, s.label]));

  return (
    <div className="rounded border border-border-dim bg-card p-3">
      <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
        Cumulative Realized P&amp;L <span className="text-text-dim normal-case">(net debit · attributed to close date)</span>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={merged} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={colors.borderDim} strokeDasharray="3 3" />
          <XAxis dataKey="date" stroke={colors.textSecondary} tick={{ fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis stroke={colors.textSecondary} tick={{ fontSize: 10 }} tickFormatter={(v) => `$${v}`} />
          <Tooltip
            contentStyle={{ backgroundColor: colors.bgElevated, border: `1px solid ${colors.border}`, borderRadius: 4, fontSize: 12 }}
            formatter={(value, name) => {
              const label = labelByKey[String(name)] ?? String(name);
              if (value === null || value === undefined) return ["—", label];
              return [formatPnL(Number(value)), label];
            }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} formatter={(v) => labelByKey[String(v)] ?? String(v)} />
          <ReferenceLine y={0} stroke={colors.border} />
          {series.map((s) => (
            <Line
              key={s.id}
              type="monotone"
              dataKey={`cum_${s.id.toLowerCase()}`}
              stroke={accentForStrategy(s.id)}
              strokeWidth={2}
              dot={{ r: 3 }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function CalendarComparison({
  data,
  aggregate,
}: {
  data: CalendarComparisonPayload;
  aggregate: CalendarAggregatePayload | null;
}) {
  const memberIds = data.member_ids;
  const members = data.calendars.members;
  const lb = data.calendars.leaderboard;

  const winnerMember = members[lb.winner];
  const winnerLabel =
    lb.winner === "tie" ? "Tied" : lb.winner === "n/a" ? "—" : winnerMember?.display_name ?? lb.winner;
  const winnerColor =
    lb.winner === "tie" || lb.winner === "n/a" ? colors.textSecondary : accentForStrategy(lb.winner);

  const orderedMembers = memberIds.map((id) => members[id.toUpperCase()]).filter(Boolean);
  const gridCols =
    orderedMembers.length <= 1
      ? "grid-cols-1"
      : orderedMembers.length === 2
      ? "grid-cols-2 max-lg:grid-cols-1"
      : "grid-cols-3 max-xl:grid-cols-2 max-md:grid-cols-1";

  return (
    <div className="space-y-3 px-3 pb-3">
      {/* Leaderboard (lifetime realized P&L basis) */}
      <div className="rounded border border-border-dim bg-card p-4">
        <div className="flex items-baseline justify-between gap-4 max-md:flex-col max-md:items-start">
          <div>
            <div className="text-xs uppercase tracking-wide text-text-secondary">
              Leader <span className="text-text-dim normal-case">(lifetime realized P&L)</span>
            </div>
            <div className="text-2xl font-semibold mt-1" style={{ color: winnerColor }}>
              {winnerLabel}
            </div>
          </div>
          <div className="flex gap-6 max-md:gap-3 flex-wrap">
            {memberIds.map((id) => {
              const up = id.toUpperCase();
              const m = members[up];
              const score = lb.scores?.[up] ?? m?.lifetime?.cumulative_pnl ?? 0;
              return (
                <div key={id} className="text-right max-md:text-left">
                  <div className="text-[10px] uppercase tracking-wide" style={{ color: accentForStrategy(id) }}>
                    {m?.display_name ?? up}
                  </div>
                  <div className="text-xl font-mono mt-0.5" style={{ color: pnlColor(score) }}>
                    {formatPnL(score)}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Member panels */}
      <div className={`grid ${gridCols} gap-3`}>
        {orderedMembers.map((m) => (
          <MemberPanel key={m.id} m={m} accent={accentForStrategy(m.id)} />
        ))}
      </div>

      {/* Cross-day cumulative chart */}
      <CumulativeChart agg={aggregate} memberIds={memberIds} />
    </div>
  );
}
