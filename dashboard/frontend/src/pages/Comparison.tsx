/**
 * Comparison page — N-way head-to-head dry-run experiment.
 *
 * Variant A is the live HYDRA bot (current spread width). Variants B, C, ...
 * are parallel HYDRA processes running in dry mode with different configs
 * (typically different spread widths). All see identical market data; the
 * only config delta should be the lever being tested, so we can attribute
 * P&L differences to that lever.
 *
 * The set of variants is driven entirely by the backend's
 * ``/api/variants/health`` response — adding a new variant on the backend
 * (settings + reader registry) makes it appear here automatically with no
 * frontend change. ``VARIANT_ACCENTS`` covers up to 5 variants out of the
 * box (A–E); beyond that it falls back to a neutral text color.
 *
 * Hidden when the backend reports ``comparison_mode_enabled=false``. The nav
 * link gating lives in App.tsx; this page also self-protects by rendering a
 * "disabled" state if accessed directly.
 *
 * Polls /api/variants/comparison every 2s. Single endpoint = one round-trip
 * per refresh, gives us all variants' state + leaderboard + history.
 */
import { useEffect, useMemo, useState } from "react";
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
  BarChart,
  Bar,
  Cell,
} from "recharts";
import { colors, pnlColor } from "../lib/tradingColors";
import { formatPnL } from "../lib/formatters";

const POLL_MS = 2000;

// Per-variant accent color. Order: A=blue, B=amber, C=mint, D=coral, E=purple-ish.
// New variants past E render in textPrimary — easy to extend if needed.
const VARIANT_ACCENTS: Record<string, string> = {
  A: colors.info,
  B: colors.warning,
  C: colors.profit,
  D: colors.loss,
  E: "#a371f7",
};

function accentFor(id: string): string {
  return VARIANT_ACCENTS[id] ?? colors.textPrimary;
}

interface VariantConfig {
  max_spread_width?: number;
  contracts_per_entry?: number;
  entry_times?: string[];
  call_starting_otm_multiplier?: number;
  put_starting_otm_multiplier?: number;
  call_stop_buffer?: number;
  put_stop_buffer?: number;
  dry_run?: boolean;
  // Directional pivot strategy (variant B/C, 2026-05-01)
  directional_pivot_enabled?: boolean;
  directional_pivot_close_mode?: string | null;
  directional_pivot_threshold_pct?: number | null;
  directional_pivot_defer_minutes?: number | null;
}

interface VariantSummary {
  date?: string;
  state?: string;
  entries_completed: number;
  entries_failed: number;
  entries_skipped: number;
  total_credit_received: number;
  total_realized_pnl: number;
  total_commission: number;
  net_pnl: number;
  call_stops: number;
  put_stops: number;
  total_stops: number;
  active_entries: number;
  total_entries: number;
}

interface BufferInfo {
  call_pct: number | null;
  put_pct: number | null;
  call_value: number | null;
  put_value: number | null;
}

interface VariantEntry {
  entry_number?: number;
  entry_time?: string;
  short_call_strike?: number;
  long_call_strike?: number;
  short_put_strike?: number;
  long_put_strike?: number;
  call_spread_credit?: number;
  put_spread_credit?: number;
  total_credit?: number;
  call_side_stop?: number;
  put_side_stop?: number;
  call_side_stopped?: boolean;
  put_side_stopped?: boolean;
  call_side_expired?: boolean;
  put_side_expired?: boolean;
  call_side_skipped?: boolean;
  put_side_skipped?: boolean;
  is_complete?: boolean;
  buffer?: BufferInfo;
}

interface PnLPoint {
  time: string;
  pnl: number;
}

interface VariantPayload {
  id: string;
  label: string;
  available: boolean;
  reason?: string;
  state_file_age_seconds?: number;
  config: VariantConfig;
  summary?: VariantSummary;
  entries?: VariantEntry[];
  pnl_history?: PnLPoint[];
  peak_buffer?: { call_pct: number; put_pct: number };
  spx_open?: number;
  spx_high?: number;
  spx_low?: number;
  vix_open?: number;
}

interface ComparisonPayload {
  date: string;
  leaderboard: {
    winner: string; // "A" | "B" | "C" | ... | "tie" | "n/a"
    scores: Record<string, number>;
    deltas_vs_a: Record<string, number>;
    // Legacy fields kept for compat — new code reads scores/deltas_vs_a.
    a_net_pnl?: number;
    b_net_pnl?: number;
    delta_net_pnl?: number;
  };
  variants: Record<string, VariantPayload>;
}

interface AggregateLifetime {
  cumulative_pnl: number;
  winning_days: number;
  losing_days: number;
  total_credit_collected: number;
  total_stops: number;
  total_entries: number;
  win_rate: number;
  sharpe: number;
  max_drawdown: number;
  best_day: number;
  worst_day: number;
  daily_returns_count?: number;
}

interface CumulativePoint {
  date: string;
  net_pnl: number;
  cumulative: number;
}

interface AggregateVariant {
  label: string;
  lifetime: AggregateLifetime;
  cumulative_curve: CumulativePoint[];
  total_days: number;
}

// Per-day H2H rows include dynamic per-variant fields like a_net_pnl,
// b_net_pnl, c_net_pnl, cumulative_a, cumulative_b, cumulative_c, etc.
// We type them as a string-indexed record so new variants don't require
// type changes — the chart looks up by `${id.toLowerCase()}_net_pnl`.
interface H2HPoint {
  date: string;
  winner: string;
  delta: number; // legacy A−B
  [key: string]: number | string;
}

interface AggregatePayload {
  variants: Record<string, AggregateVariant>;
  head_to_head: {
    common_days: number;
    days_tied: number;
    per_day: H2HPoint[];
    days_won_per_variant: Record<string, number>;
    cumulative_per_variant: Record<string, number>;
    // Legacy
    days_a_won?: number;
    days_b_won?: number;
    cumulative_delta_a_minus_b?: number;
  };
}

interface Health {
  enabled: boolean;
  variants: string[]; // ["A", "B", "C"]
  labels: Record<string, string>;
}

async function fetchJSON<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export function Comparison() {
  const [health, setHealth] = useState<Health | null>(null);
  const [data, setData] = useState<ComparisonPayload | null>(null);
  const [aggregate, setAggregate] = useState<AggregatePayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    fetchJSON<Health>("/api/variants/health").then((h) => {
      if (mounted) setHealth(h);
    });

    const tick = async () => {
      const json = await fetchJSON<ComparisonPayload | { detail: string }>(
        "/api/variants/comparison"
      );
      if (!mounted) return;
      if (!json) {
        setError("Network error");
        return;
      }
      if ("detail" in json) {
        setError(json.detail);
        setData(null);
        return;
      }
      setError(null);
      setData(json as ComparisonPayload);
    };

    // Aggregate refreshes much less often — daily-level data only changes at
    // end-of-day (4 PM ET). 30s poll is plenty and keeps the cross-day panel
    // fresh enough for a researcher refreshing during the trading session.
    const aggTick = async () => {
      const json = await fetchJSON<AggregatePayload | { detail: string }>(
        "/api/variants/aggregate"
      );
      if (!mounted) return;
      if (json && !("detail" in json)) {
        setAggregate(json as AggregatePayload);
      }
    };

    tick();
    aggTick();
    const id = setInterval(tick, POLL_MS);
    const aggId = setInterval(aggTick, 30_000);
    return () => {
      mounted = false;
      clearInterval(id);
      clearInterval(aggId);
    };
  }, []);

  // Sorted list of available variant ids drives ALL rendering downstream.
  // Sourcing from health (not data) means we render placeholder cards even
  // before the first /comparison response arrives.
  const variantIds = useMemo(() => {
    if (health?.variants?.length) return [...health.variants].sort();
    if (data?.variants) return Object.keys(data.variants).sort();
    return ["A", "B"];
  }, [health, data]);

  if (health && !health.enabled) {
    return <DisabledNotice />;
  }

  if (error && !data) {
    return (
      <div className="p-6 text-text-secondary">
        <h1 className="text-xl text-text-primary mb-2">Comparison</h1>
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="p-6 text-text-secondary">
        <h1 className="text-xl text-text-primary mb-2">Comparison</h1>
        <p className="text-sm">Loading variant data…</p>
      </div>
    );
  }

  const { variants, leaderboard } = data;

  // Render variant payloads in canonical order. Missing payloads (e.g. a
  // backend hiccup mid-poll) get a neutral placeholder row.
  const orderedVariants = variantIds.map((vid) => variants[vid]).filter(Boolean);

  // Tailwind doesn't support fully-dynamic class names well, so we map known
  // counts to fixed grid-cols-N classes and fall back to grid-cols-1 + flex.
  const gridCols =
    orderedVariants.length === 1
      ? "grid-cols-1"
      : orderedVariants.length === 2
      ? "grid-cols-2 max-lg:grid-cols-1"
      : orderedVariants.length === 3
      ? "grid-cols-3 max-xl:grid-cols-2 max-md:grid-cols-1"
      : "grid-cols-4 max-xl:grid-cols-2 max-md:grid-cols-1";

  return (
    <div className="space-y-3 px-3 pb-3">
      <Leaderboard
        variantIds={variantIds}
        variants={variants}
        winner={leaderboard.winner}
        scores={leaderboard.scores}
      />
      <ConfigDelta variantIds={variantIds} variants={variants} />
      <div className={`grid ${gridCols} gap-3`}>
        {orderedVariants.map((v) => (
          <VariantPanel key={v.id} v={v} accent={accentFor(v.id)} />
        ))}
      </div>
      <PnLChart variantIds={variantIds} variants={variants} />
      <EndOfDayStats variantIds={variantIds} variants={variants} />
      <CrossDayPanel agg={aggregate} variantIds={variantIds} />
    </div>
  );
}

function DisabledNotice() {
  return (
    <div className="p-6 max-w-2xl mx-auto text-text-secondary">
      <h1 className="text-xl text-text-primary mb-3">Comparison Mode</h1>
      <div className="rounded border border-border-dim bg-card p-4 text-sm space-y-2">
        <p className="text-text-primary">Comparison mode is currently disabled.</p>
        <p>
          Enable by setting <code className="text-info">DASHBOARD_COMPARISON_MODE_ENABLED=true</code>{" "}
          in the dashboard environment and restarting <code>dashboard.service</code>.
        </p>
        <p>
          Then start the variant bots, e.g.{" "}
          <code className="text-info">sudo systemctl start hydra_variant_b</code>
          {" "}and{" "}
          <code className="text-info">sudo systemctl start hydra_variant_c</code>.
        </p>
      </div>
    </div>
  );
}

function Leaderboard({
  variantIds,
  variants,
  winner,
  scores,
}: {
  variantIds: string[];
  variants: Record<string, VariantPayload>;
  winner: string;
  scores: Record<string, number>;
}) {
  const winnerVariant = variants[winner];
  const winnerLabel =
    winner === "tie" ? "Tied" : winner === "n/a" ? "—" : winnerVariant?.label ?? winner;
  const winnerColor =
    winner === "tie" || winner === "n/a" ? colors.textSecondary : accentFor(winner);

  return (
    <div className="rounded border border-border-dim bg-card p-4">
      <div className="flex items-baseline justify-between gap-4 max-md:flex-col max-md:items-start">
        <div>
          <div className="text-xs uppercase tracking-wide text-text-secondary">
            Today's Leader
          </div>
          <div className="text-2xl font-semibold mt-1" style={{ color: winnerColor }}>
            {winnerLabel}
          </div>
        </div>
        <div className="flex gap-6 max-md:gap-3 flex-wrap">
          {variantIds.map((vid) => {
            const v = variants[vid];
            const value = scores?.[vid] ?? v?.summary?.net_pnl ?? 0;
            return (
              <PnLBlock
                key={vid}
                label={v?.label ?? vid}
                value={value}
                accent={accentFor(vid)}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

function PnLBlock({ label, value, accent }: { label: string; value: number; accent: string }) {
  return (
    <div className="text-right max-md:text-left">
      <div className="text-[10px] uppercase tracking-wide" style={{ color: accent }}>
        {label}
      </div>
      <div className="text-xl font-mono mt-0.5" style={{ color: pnlColor(value) }}>
        {formatPnL(value)}
      </div>
    </div>
  );
}

function ConfigDelta({
  variantIds,
  variants,
}: {
  variantIds: string[];
  variants: Record<string, VariantPayload>;
}) {
  // Surface the actual config differences so a viewer who lands on the page
  // immediately sees what's being tested. Variant A is the "control"; rows
  // are bolded where ANY variant differs from variant A's value.
  const rows: Array<{ key: keyof VariantConfig; label: string }> = [
    { key: "max_spread_width", label: "Spread width (pt)" },
    { key: "contracts_per_entry", label: "Contracts/entry" },
    { key: "entry_times", label: "Entry times" },
    { key: "call_starting_otm_multiplier", label: "Call start ×" },
    { key: "put_starting_otm_multiplier", label: "Put start ×" },
    { key: "call_stop_buffer", label: "Call stop buffer ($)" },
    { key: "put_stop_buffer", label: "Put stop buffer ($)" },
    // Directional pivot strategy rows (variant B/C, 2026-05-01)
    { key: "directional_pivot_enabled", label: "Directional pivot" },
    { key: "directional_pivot_close_mode", label: "Pivot close mode" },
    { key: "directional_pivot_threshold_pct", label: "Pivot threshold (%)" },
    { key: "directional_pivot_defer_minutes", label: "Pivot defer window (min)" },
    { key: "dry_run", label: "Dry-run" },
  ];

  return (
    <div className="rounded border border-border-dim bg-card p-3">
      <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
        Config Delta
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-secondary text-xs">
            <th className="text-left font-normal pb-1">Knob</th>
            {variantIds.map((vid) => (
              <th
                key={vid}
                className="text-right font-normal pb-1"
                style={{ color: accentFor(vid) }}
              >
                {variants[vid]?.label ?? vid}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ key, label }) => {
            const aValRaw =
              (variants["A"]?.config as Record<string, unknown> | undefined)?.[key as string];
            const aValStr = JSON.stringify(aValRaw);
            return (
              <tr key={key}>
                <td className="py-0.5">{label}</td>
                {variantIds.map((vid) => {
                  const v = (variants[vid]?.config as Record<string, unknown> | undefined)?.[
                    key as string
                  ];
                  const differs = vid !== "A" && JSON.stringify(v) !== aValStr;
                  // Format display based on field type — array → join, threshold
                  // → %, null → em-dash, everything else → String().
                  let display: string;
                  if (v === null || v === undefined) {
                    display = "—";
                  } else if (Array.isArray(v)) {
                    display = v.length ? v.join(", ") : "—";
                  } else if (key === "directional_pivot_threshold_pct" && typeof v === "number") {
                    display = (v * 100).toFixed(2) + "%";
                  } else if (typeof v === "boolean") {
                    display = v ? "yes" : "no";
                  } else {
                    display = String(v);
                  }
                  return (
                    <td
                      key={vid}
                      className="py-0.5 text-right font-mono"
                      style={{ color: differs ? accentFor(vid) : undefined }}
                    >
                      {display}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function VariantPanel({ v, accent }: { v: VariantPayload; accent: string }) {
  if (!v.available) {
    return (
      <div className="rounded border border-border-dim bg-card p-4">
        <div className="text-xs uppercase tracking-wide" style={{ color: accent }}>
          {v.label}
        </div>
        <div className="mt-2 text-text-secondary text-sm">
          Variant not running.
        </div>
        {v.reason && <div className="text-xs text-text-dim mt-1">{v.reason}</div>}
      </div>
    );
  }

  const summary = v.summary!;
  const entries = v.entries ?? [];
  const peak = v.peak_buffer ?? { call_pct: 0, put_pct: 0 };

  return (
    <div className="rounded border border-border-dim bg-card p-4 space-y-3">
      <div className="flex items-baseline justify-between">
        <div className="text-xs uppercase tracking-wide" style={{ color: accent }}>
          {v.label}
        </div>
        <div className="text-xs text-text-secondary">
          {summary.entries_completed} entries • {summary.total_stops} stops
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-sm">
        <Metric label="Net P&L" value={formatPnL(summary.net_pnl)} colored={summary.net_pnl} />
        <Metric label="Credit" value={`$${summary.total_credit_received.toFixed(0)}`} />
        <Metric label="Commission" value={`$${summary.total_commission.toFixed(0)}`} />
      </div>

      {entries.length === 0 ? (
        <div className="text-sm text-text-dim italic">No entries yet today.</div>
      ) : (
        <div className="space-y-2">
          {entries.map((e, i) => (
            <EntryRow key={i} entry={e} accent={accent} />
          ))}
        </div>
      )}

      <div className="border-t border-border-dim pt-2">
        <div className="text-xs text-text-secondary mb-1">Peak Buffer Use Today</div>
        <div className="flex gap-4 text-xs">
          <BufferBar label="Call" pct={peak.call_pct} />
          <BufferBar label="Put" pct={peak.put_pct} />
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value, colored }: { label: string; value: string; colored?: number }) {
  const color = colored !== undefined ? pnlColor(colored) : colors.textPrimary;
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-text-secondary">{label}</div>
      <div className="text-base font-mono mt-0.5" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

function EntryRow({ entry, accent }: { entry: VariantEntry; accent: string }) {
  const num = entry.entry_number ?? "?";
  const callDone = entry.call_side_stopped || entry.call_side_expired || entry.call_side_skipped;
  const putDone = entry.put_side_stopped || entry.put_side_expired || entry.put_side_skipped;
  const status = entry.is_complete ? "DONE" : "LIVE";
  const statusColor = entry.is_complete ? colors.textDim : accent;

  const buffer = entry.buffer ?? { call_pct: null, put_pct: null, call_value: null, put_value: null };

  return (
    <div className="rounded border border-border-dim bg-bg p-2 text-xs">
      <div className="flex items-center justify-between mb-1">
        <div className="font-mono">
          <span style={{ color: accent }}>#{num}</span>{" "}
          <span className="text-text-secondary">{entry.entry_time?.slice(11, 16) ?? "—"}</span>
        </div>
        <div className="text-[10px] font-mono uppercase tracking-wider" style={{ color: statusColor }}>
          {status}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 font-mono text-text-secondary">
        <div>
          <span className="text-text-dim">C:</span>{" "}
          <span style={{ color: callDone ? colors.textDim : colors.textPrimary }}>
            {entry.short_call_strike}/{entry.long_call_strike}
          </span>
          <span className="text-text-dim ml-2">${entry.call_spread_credit?.toFixed(0)}</span>
        </div>
        <div>
          <span className="text-text-dim">P:</span>{" "}
          <span style={{ color: putDone ? colors.textDim : colors.textPrimary }}>
            {entry.short_put_strike}/{entry.long_put_strike}
          </span>
          <span className="text-text-dim ml-2">${entry.put_spread_credit?.toFixed(0)}</span>
        </div>
      </div>
      {!entry.is_complete && (buffer.call_pct !== null || buffer.put_pct !== null) && (
        <div className="flex gap-3 mt-1.5">
          {buffer.call_pct !== null && (
            <div className="flex-1">
              <div className="text-[10px] text-text-dim">
                C buffer ${buffer.call_value?.toFixed(0)} / stop ${entry.call_side_stop?.toFixed(0)}
              </div>
              <BufferBar pct={buffer.call_pct} compact />
            </div>
          )}
          {buffer.put_pct !== null && (
            <div className="flex-1">
              <div className="text-[10px] text-text-dim">
                P buffer ${buffer.put_value?.toFixed(0)} / stop ${entry.put_side_stop?.toFixed(0)}
              </div>
              <BufferBar pct={buffer.put_pct} compact />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BufferBar({
  pct,
  label,
  compact = false,
}: {
  pct: number | null;
  label?: string;
  compact?: boolean;
}) {
  const v = pct ?? 0;
  // Buffer usage color: green low, amber mid, red high (inverted vs cushion).
  const color =
    v >= 80 ? colors.loss : v >= 60 ? "#f0883e" : v >= 40 ? colors.warning : colors.profit;
  const height = compact ? "h-1" : "h-1.5";

  return (
    <div className={compact ? "" : "flex-1"}>
      {label && (
        <div className="flex justify-between text-[10px] mb-0.5">
          <span className="text-text-secondary">{label}</span>
          <span className="font-mono" style={{ color }}>
            {pct === null ? "—" : `${v.toFixed(0)}%`}
          </span>
        </div>
      )}
      <div className={`${height} bg-bg-elevated rounded-full overflow-hidden`}>
        <div
          className={`${height} rounded-full transition-all duration-500`}
          style={{ width: `${v}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}

function PnLChart({
  variantIds,
  variants,
}: {
  variantIds: string[];
  variants: Record<string, VariantPayload>;
}) {
  // Merge all variants' P&L history onto a single time axis. Each variant's
  // pnl_history is a list of {time, pnl} written by the bot every ~10s during
  // market hours. We zip them by index because they share the same heartbeat
  // cadence and start time. If one has fewer points (e.g. a variant started
  // late), the missing series is left null at that index — Recharts skips it.
  //
  // Per-variant series live in row[`pnl_${id.toLowerCase()}`] so we can have
  // any number of variants without changing the row schema.
  const histories = variantIds.map((vid) => ({
    id: vid,
    label: variants[vid]?.label ?? vid,
    series: variants[vid]?.pnl_history ?? [],
  }));
  const maxLen = Math.max(0, ...histories.map((h) => h.series.length));

  if (maxLen === 0) {
    return (
      <div className="rounded border border-border-dim bg-card p-4">
        <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
          P&amp;L Over Time
        </div>
        <div className="text-sm text-text-dim italic">
          No P&amp;L data yet — chart will appear once the variants have been
          monitoring an entry for one heartbeat cycle.
        </div>
      </div>
    );
  }

  const merged: Array<Record<string, string | number | null>> = [];
  for (let i = 0; i < maxLen; i++) {
    const row: Record<string, string | number | null> = { time: "" };
    for (const h of histories) {
      const point = h.series[i];
      if (point && !row.time) {
        row.time = point.time;
      }
      row[`pnl_${h.id.toLowerCase()}`] = point?.pnl ?? null;
    }
    merged.push(row);
  }

  // Tooltip needs to map series key -> variant label
  const labelByKey = Object.fromEntries(
    histories.map((h) => [`pnl_${h.id.toLowerCase()}`, h.label]),
  );

  return (
    <div className="rounded border border-border-dim bg-card p-3">
      <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
        P&amp;L Over Time
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={merged} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={colors.borderDim} strokeDasharray="3 3" />
          <XAxis
            dataKey="time"
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            interval="preserveStartEnd"
          />
          <YAxis
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) => `$${v}`}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: colors.bgElevated,
              border: `1px solid ${colors.border}`,
              borderRadius: 4,
              fontSize: 12,
            }}
            formatter={(value, name) => {
              const label = labelByKey[String(name)] ?? String(name);
              if (value === null || value === undefined) return ["—", label];
              return [formatPnL(Number(value)), label];
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11 }}
            formatter={(v) => labelByKey[String(v)] ?? String(v)}
          />
          <ReferenceLine y={0} stroke={colors.border} />
          {histories.map((h) => (
            <Line
              key={h.id}
              type="monotone"
              dataKey={`pnl_${h.id.toLowerCase()}`}
              stroke={accentFor(h.id)}
              strokeWidth={2}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function EndOfDayStats({
  variantIds,
  variants,
}: {
  variantIds: string[];
  variants: Record<string, VariantPayload>;
}) {
  // Per-variant single-day stats. After 4 PM these become the locked
  // end-of-day comparison; before that they update live.
  const rows: Array<[string, (v: VariantPayload | undefined) => string | number, boolean?]> = [
    ["Entries placed", (v) => v?.summary?.entries_completed ?? 0],
    ["Stops fired", (v) => v?.summary?.total_stops ?? 0],
    ["Total credit", (v) => `$${(v?.summary?.total_credit_received ?? 0).toFixed(0)}`],
    ["Realized P&L", (v) => formatPnL(v?.summary?.total_realized_pnl ?? 0), true],
    ["Commission", (v) => `$${(v?.summary?.total_commission ?? 0).toFixed(0)}`],
    ["Net P&L", (v) => formatPnL(v?.summary?.net_pnl ?? 0), true],
    ["Peak call buffer used", (v) => `${(v?.peak_buffer?.call_pct ?? 0).toFixed(0)}%`],
    ["Peak put buffer used", (v) => `${(v?.peak_buffer?.put_pct ?? 0).toFixed(0)}%`],
  ];

  return (
    <div className="rounded border border-border-dim bg-card p-3">
      <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
        Day Summary (Live)
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-secondary text-xs">
            <th className="text-left font-normal pb-1">Metric</th>
            {variantIds.map((vid) => (
              <th
                key={vid}
                className="text-right font-normal pb-1"
                style={{ color: accentFor(vid) }}
              >
                {variants[vid]?.label ?? vid}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, getter, colored], i) => (
            <tr key={i}>
              <td className="py-0.5 text-text-secondary">{label}</td>
              {variantIds.map((vid) => {
                const v = variants[vid];
                const value = getter(v);
                const netPnL = v?.summary?.net_pnl ?? 0;
                return (
                  <td
                    key={vid}
                    className="py-0.5 text-right font-mono"
                    style={{ color: colored ? pnlColor(netPnL) : undefined }}
                  >
                    {value}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CrossDayPanel({
  agg,
  variantIds,
}: {
  agg: AggregatePayload | null;
  variantIds: string[];
}) {
  // Cross-day rollups are most informative once all variants have at least
  // 2 common days. Below that we render a placeholder explaining what the
  // panel will show, so empty days don't make it look broken.
  if (!agg) {
    return (
      <div className="rounded border border-border-dim bg-card p-3">
        <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
          Cross-Day Performance
        </div>
        <div className="text-sm text-text-dim italic">Loading…</div>
      </div>
    );
  }

  const h2h = agg.head_to_head;
  const hasH2H = h2h.common_days >= 2;

  // Use whichever variant set is in the aggregate response (the source of
  // truth) but keep canonical sort order for display.
  const aggIds = Object.keys(agg.variants).sort();
  const idsToShow = aggIds.length > 0 ? aggIds : variantIds;

  const totalDaysRow = idsToShow
    .map((vid) => `${vid} history: ${agg.variants[vid]?.total_days ?? 0}d`)
    .join(" · ");

  return (
    <div className="rounded border border-border-dim bg-card p-3 space-y-4">
      <div className="flex items-baseline justify-between">
        <div className="text-xs uppercase tracking-wide text-text-secondary">
          Cross-Day Performance
        </div>
        <div className="text-[11px] text-text-dim">
          {totalDaysRow} · H2H window: {h2h.common_days}d
        </div>
      </div>

      <LifetimeStatsTable agg={agg} ids={idsToShow} />

      {hasH2H ? (
        <>
          <H2HCumulativeChart h2h={h2h} ids={idsToShow} agg={agg} />
          <H2HDailyDeltaChart h2h={h2h} ids={idsToShow} agg={agg} />
        </>
      ) : (
        <div className="rounded border border-border-dim bg-bg p-3 text-xs text-text-dim italic">
          Head-to-head charts will appear after all variants have run for 2+
          common trading days. Currently {h2h.common_days} day
          {h2h.common_days === 1 ? "" : "s"} of overlap.
        </div>
      )}
    </div>
  );
}

function LifetimeStatsTable({ agg, ids }: { agg: AggregatePayload; ids: string[] }) {
  // Variant A's history typically goes back further than the other variants',
  // so its lifetime stats include data from before the experiment started.
  // Column headers carry the day count so the user knows the comparison isn't
  // on identical N — the H2H window section below is the apples-to-apples view.
  const rows: Array<[string, (lt: AggregateLifetime) => string, "pnl" | "best" | "worst" | undefined]> =
    [
      ["Cumulative P&L", (lt) => formatPnL(lt.cumulative_pnl), "pnl"],
      ["Win rate", (lt) => `${(lt.win_rate * 100).toFixed(1)}%`, undefined],
      ["Win / Loss days", (lt) => `${lt.winning_days} / ${lt.losing_days}`, undefined],
      ["Best day", (lt) => formatPnL(lt.best_day), "best"],
      ["Worst day", (lt) => formatPnL(lt.worst_day), "worst"],
      ["Max drawdown", (lt) => `$${lt.max_drawdown.toFixed(0)}`, undefined],
      ["Sharpe (daily)", (lt) => lt.sharpe.toFixed(2), undefined],
      ["Total credit", (lt) => `$${lt.total_credit_collected.toFixed(0)}`, undefined],
      ["Total stops", (lt) => `${lt.total_stops}`, undefined],
    ];

  const h2h = agg.head_to_head;
  const daysWon = h2h.days_won_per_variant ?? {};

  return (
    <div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-secondary text-xs">
            <th className="text-left font-normal pb-1">Lifetime metric</th>
            {ids.map((vid) => (
              <th
                key={vid}
                className="text-right font-normal pb-1"
                style={{ color: accentFor(vid) }}
              >
                {agg.variants[vid]?.label ?? vid} ({agg.variants[vid]?.total_days ?? 0}d)
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, fn, kind], i) => (
            <tr key={i}>
              <td className="py-0.5 text-text-secondary">{label}</td>
              {ids.map((vid) => {
                const lt = agg.variants[vid]?.lifetime;
                if (!lt) return <td key={vid} className="py-0.5 text-right text-text-dim">—</td>;
                let color: string | undefined;
                if (kind === "pnl") color = pnlColor(lt.cumulative_pnl);
                else if (kind === "best") color = pnlColor(lt.best_day);
                else if (kind === "worst") color = pnlColor(lt.worst_day);
                return (
                  <td
                    key={vid}
                    className="py-0.5 text-right font-mono"
                    style={{ color }}
                  >
                    {fn(lt)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

      {h2h.common_days > 0 && (
        <div
          className="mt-3 grid gap-2 text-xs"
          style={{ gridTemplateColumns: `repeat(${ids.length + 1}, minmax(0, 1fr))` }}
        >
          {ids.map((vid) => (
            <div key={vid} className="rounded bg-bg p-2 text-center">
              <div className="text-text-dim text-[10px] uppercase tracking-wide">
                {vid} days won
              </div>
              <div
                className="font-mono text-base mt-0.5"
                style={{ color: accentFor(vid) }}
              >
                {daysWon[vid] ?? 0}
              </div>
            </div>
          ))}
          <div className="rounded bg-bg p-2 text-center">
            <div className="text-text-dim text-[10px] uppercase tracking-wide">Tied</div>
            <div className="font-mono text-base mt-0.5 text-text-secondary">
              {h2h.days_tied}
            </div>
          </div>
        </div>
      )}

      {h2h.common_days > 0 && h2h.cumulative_per_variant && (
        <div className="mt-2 text-xs text-text-secondary text-center">
          H2H cumulative:{" "}
          {ids.map((vid, i) => (
            <span key={vid}>
              {i > 0 && " · "}
              <span style={{ color: accentFor(vid) }}>{vid}</span>{" "}
              <span
                className="font-mono"
                style={{ color: pnlColor(h2h.cumulative_per_variant[vid] ?? 0) }}
              >
                {formatPnL(h2h.cumulative_per_variant[vid] ?? 0)}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function H2HCumulativeChart({
  h2h,
  ids,
  agg,
}: {
  h2h: AggregatePayload["head_to_head"];
  ids: string[];
  agg: AggregatePayload;
}) {
  // Cumulative P&L curves over the H2H window. We chart `cumulative_<id>`
  // which are server-computed running sums starting from the first common
  // day — NOT each variant's lifetime cumulative. This way all lines start
  // at $0 on the same day and divergence is purely the experiment's
  // contribution.
  const labelByKey = Object.fromEntries(
    ids.map((vid) => [`cumulative_${vid.toLowerCase()}`, agg.variants[vid]?.label ?? vid]),
  );

  return (
    <div>
      <div className="text-[11px] text-text-secondary mb-1">
        Cumulative P&amp;L (H2H window only — starts at $0 on first common day)
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={h2h.per_day} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={colors.borderDim} strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            interval="preserveStartEnd"
          />
          <YAxis
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) => `$${v}`}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: colors.bgElevated,
              border: `1px solid ${colors.border}`,
              borderRadius: 4,
              fontSize: 12,
            }}
            formatter={(value, name) => {
              const label = labelByKey[String(name)] ?? String(name);
              return [formatPnL(Number(value)), label];
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11 }}
            formatter={(v) => labelByKey[String(v)] ?? String(v)}
          />
          <ReferenceLine y={0} stroke={colors.border} />
          {ids.map((vid) => (
            <Line
              key={vid}
              type="monotone"
              dataKey={`cumulative_${vid.toLowerCase()}`}
              stroke={accentFor(vid)}
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function H2HDailyDeltaChart({
  h2h,
  ids,
  agg,
}: {
  h2h: AggregatePayload["head_to_head"];
  ids: string[];
  agg: AggregatePayload;
}) {
  // For 2 variants: signed delta bar chart (A − B). For 3+ variants: per-day
  // bars per variant with a winner highlight via cell color. The 3-way bar
  // chart shows absolute net P&L per day per variant grouped — easier to
  // read at a glance than three signed-delta series superimposed.
  if (ids.length === 2) {
    const [a, b] = ids;
    const aLabel = agg.variants[a]?.label ?? a;
    const bLabel = agg.variants[b]?.label ?? b;
    return (
      <div>
        <div className="text-[11px] text-text-secondary mb-1">
          Daily delta ({a} − {b}). {a} bar = {a} won, {b} bar = {b} won.
        </div>
        <ResponsiveContainer width="100%" height={150}>
          <BarChart data={h2h.per_day} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={colors.borderDim} strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              stroke={colors.textSecondary}
              tick={{ fontSize: 10 }}
              interval="preserveStartEnd"
            />
            <YAxis
              stroke={colors.textSecondary}
              tick={{ fontSize: 10 }}
              tickFormatter={(v) => `$${v}`}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: colors.bgElevated,
                border: `1px solid ${colors.border}`,
                borderRadius: 4,
                fontSize: 12,
              }}
              formatter={(_value, _name, item) => {
                const p = (item?.payload || {}) as H2HPoint;
                const aPnL = Number(p[`${a.toLowerCase()}_net_pnl`] ?? 0);
                const bPnL = Number(p[`${b.toLowerCase()}_net_pnl`] ?? 0);
                return [
                  `Δ ${formatPnL(Number(p.delta))}  ·  ${aLabel} ${formatPnL(aPnL)}  ·  ${bLabel} ${formatPnL(bPnL)}`,
                  "",
                ];
              }}
            />
            <ReferenceLine y={0} stroke={colors.border} />
            <Bar dataKey="delta" isAnimationActive={false}>
              {h2h.per_day.map((p, i) => (
                <Cell
                  key={i}
                  fill={
                    p.winner === a
                      ? accentFor(a)
                      : p.winner === b
                      ? accentFor(b)
                      : colors.textSecondary
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // 3+ variants: grouped bar chart, one bar per variant per day.
  const labelByKey = Object.fromEntries(
    ids.map((vid) => [`${vid.toLowerCase()}_net_pnl`, agg.variants[vid]?.label ?? vid]),
  );
  return (
    <div>
      <div className="text-[11px] text-text-secondary mb-1">
        Daily net P&amp;L per variant. Tallest bar of the day = winner.
      </div>
      <ResponsiveContainer width="100%" height={170}>
        <BarChart data={h2h.per_day} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={colors.borderDim} strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            interval="preserveStartEnd"
          />
          <YAxis
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) => `$${v}`}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: colors.bgElevated,
              border: `1px solid ${colors.border}`,
              borderRadius: 4,
              fontSize: 12,
            }}
            formatter={(value, name) => {
              const label = labelByKey[String(name)] ?? String(name);
              return [formatPnL(Number(value)), label];
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11 }}
            formatter={(v) => labelByKey[String(v)] ?? String(v)}
          />
          <ReferenceLine y={0} stroke={colors.border} />
          {ids.map((vid) => (
            <Bar
              key={vid}
              dataKey={`${vid.toLowerCase()}_net_pnl`}
              fill={accentFor(vid)}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
