import { useEffect, useState } from "react";
import { CalendarClock, ShieldCheck } from "lucide-react";
import { colors, pnlColor } from "../lib/tradingColors";
import { formatPnL, formatCurrency, formatDateShort } from "../lib/formatters";

// Strategy D "DC Time Machine" — a multi-day SPX double calendar that transforms
// into a (ideally risk-free) iron condor. This page is read-only and D-native:
// it does NOT belong in the 0DTE iron-condor /comparison view (net DEBIT vs net
// credit is apples-to-oranges). Data source: GET /api/dc/status (the dashboard's
// dc_reader, which reads D's dry-run sidecar + isolated dc_calendar.db). The
// browsers send the session cookie automatically on same-origin requests, so a plain fetch is fine.

interface OpenCalendar {
  entry_number: number | null;
  strategy_id: string | null;
  dc_phase: string | null; // "calendar" | "transformed" | "closed"
  is_risk_free: boolean;
  contracts: number | null;
  net_debit: number | null;
  transform_credit: number | null;
  call_strike: number | null;
  put_strike: number | null;
  short_expiry: string | null;
  long_expiry: string | null;
}

interface RecentOutcome {
  entry_date: string;
  close_date: string;
  entry_number: number;
  terminal_state: string; // "settled" | "stop" | "eod" | ...
  realized_pnl: number;
}

interface DCStatus {
  strategy: string;
  label: string;
  open_calendars: OpenCalendar[];
  recent_outcomes: RecentOutcome[];
  summary: {
    open_count: number;
    transformed_count: number;
    risk_free_count: number;
    realized_pnl_recent: number;
  };
}

const POLL_MS = 5000;

async function fetchJSON<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

function isTransformed(phase: string | null): boolean {
  return (phase ?? "").toLowerCase() === "transformed";
}

/** Small label/value metric cell (mirrors the Comparison page idiom). */
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
      <span
        className="text-[9px] font-mono px-1 py-px rounded"
        style={{ color, border: `1px solid ${color}` }}
      >
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

function CalendarCard({ c }: { c: OpenCalendar }) {
  const transformed = isTransformed(c.dc_phase);
  const debit = c.net_debit ?? 0;
  const credit = c.transform_credit ?? 0;
  // Locked P&L floor once transformed = transform credit − net debit (what the
  // IC has captured vs the calendar's cost basis). Informational only.
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

function OutcomesTable({ outcomes }: { outcomes: RecentOutcome[] }) {
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

export function DoubleCalendar() {
  const [data, setData] = useState<DCStatus | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      const json = await fetchJSON<DCStatus>("/api/dc/status");
      if (!mounted) return;
      setLoaded(true);
      if (json && json.strategy === "double_calendar") {
        setData(json);
        setUnavailable(false);
      } else {
        setUnavailable(true);
      }
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, []);

  if (!loaded) {
    return <div className="p-6 text-text-secondary">Loading…</div>;
  }
  if (unavailable || !data) {
    return (
      <div className="p-6 text-text-secondary">
        Strategy D status is unavailable. The DC Time Machine variant may not be running.
      </div>
    );
  }

  const s = data.summary;
  return (
    <div className="space-y-4 p-1">
      <div className="flex items-center gap-2">
        <CalendarClock size={18} style={{ color: colors.info }} />
        <div>
          <h1 className="text-sm font-semibold text-text-primary">{data.label}</h1>
          <div className="text-[11px] text-text-secondary">
            Multi-day SPX double calendar → risk-free iron condor.{" "}
            <span style={{ color: colors.textDim }}>Dry-run shadow — places no real orders.</span>
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
        {data.open_calendars.length ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {data.open_calendars.map((c, i) => (
              <CalendarCard key={`${c.strategy_id ?? c.entry_number}-${i}`} c={c} />
            ))}
          </div>
        ) : (
          <div className="text-sm text-text-secondary">
            No open calendars right now. D opens its next dry-run calendar at 10:00 ET.
          </div>
        )}
      </div>

      {/* Recent outcomes */}
      <div>
        <h2 className="text-[10px] uppercase tracking-wide text-text-secondary mb-2">Recent Outcomes</h2>
        <div className="rounded border border-border-dim bg-card p-4">
          <OutcomesTable outcomes={data.recent_outcomes} />
        </div>
      </div>
    </div>
  );
}
