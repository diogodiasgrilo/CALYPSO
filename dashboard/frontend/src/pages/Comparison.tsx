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
import { colors, pnlColor, cushionColor } from "../lib/tradingColors";
import { formatPnL, isRegularSessionTime } from "../lib/formatters";
import { accentForStrategy } from "../lib/pnlShape";

const POLL_MS = 2000;

// Per-variant accent color now comes from the shared, id-derived map (replaces
// the positional VARIANT_ACCENTS) so the same letter gets the same color on the
// dashboard picker, the comparison panels, and the group tabs alike.
function accentFor(id: string): string {
  return accentForStrategy(id);
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
  // Brandon Trojan Horse stack (v1.27, 2026-05-04)
  brandon_enabled?: boolean;
  brandon_tp_enabled?: boolean;
  brandon_tp_threshold?: number | null;
  brandon_gex_strike_adjuster_enabled?: boolean;
  brandon_gex_breach_exit_enabled?: boolean;
  brandon_overlay_enabled?: boolean;
  brandon_narrow_spread_enabled?: boolean;
  brandon_hydra_stop_shadow_enabled?: boolean;
  // 2026-05-08 restructure: tightener state + delta-target strike selection.
  // Backend exposes both the literal config key (disable_progressive_tightening)
  // AND a positively-phrased derived field (progressive_tightening_enabled)
  // — Config Delta uses the latter to avoid double-negative readings.
  brandon_disable_progressive_tightening?: boolean;
  brandon_progressive_tightening_enabled?: boolean;
  brandon_delta_target_enabled?: boolean;
  // Directional pivot — preserved for historical snapshots; disabled in v1.27
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
  // MKT-042 buffer-decay-adjusted live stop (the actual trigger). Falls back
  // to the static *_side_stop when absent.
  effective_call_stop?: number;
  effective_put_stop?: number;
  call_side_stopped?: boolean;
  put_side_stopped?: boolean;
  call_side_expired?: boolean;
  put_side_expired?: boolean;
  call_side_skipped?: boolean;
  put_side_skipped?: boolean;
  is_complete?: boolean;
  // Server-computed: TP / BREACH / STOP / EXPIRED / SKIPPED / LIVE
  disposition?: string;
  // Server-computed: net realized P&L for this entry's closed sides
  entry_realized_pnl?: number;
  // Raw state passthrough (already in the payload via dict(e); now consumed by
  // the per-side card so a half-stopped entry shows its stopped leg explicitly).
  close_reason?: string;
  skip_reason?: string;
  call_side_pivot_closed?: boolean;
  put_side_pivot_closed?: boolean;
  actual_call_stop_debit?: number;
  actual_put_stop_debit?: number;
  call_stop_time?: string;
  put_stop_time?: string;
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
  min_buffer_margin?: { call_pct: number; put_pct: number };
  spx_price?: number;
  spx_open?: number;
  spx_high?: number;
  spx_low?: number;
  vix_open?: number;
}

export interface ComparisonPayload {
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
  // Capital efficiency (max-risk notional deployed + return on it).
  capital_deployed?: number;
  avg_capital_per_day?: number;
  roi_pct?: number;
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
  // When set (YYYY-MM-DD), this variant's lifetime total + curve are rebased to
  // start from this date (sums only days >= baseline). Empty = full history.
  baseline_date?: string;
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

export interface AggregatePayload {
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

  return <ComparisonView data={data} aggregate={aggregate} variantIds={variantIds} />;
}

/**
 * The CREDIT-shape (iron-condor) comparison renderer — reused by both the
 * legacy /api/variants-driven page above AND the group tabs
 * (GroupComparison feeding the ic_0dte group's members + baseline). Takes its
 * data as props so the same panels render regardless of the data source. A
 * SINGLE chart never receives two pnl_shapes — this view is credit-only.
 */
export function ComparisonView({
  data,
  aggregate,
  variantIds,
}: {
  data: ComparisonPayload;
  aggregate: AggregatePayload | null;
  variantIds: string[];
}) {
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

  // Per-contract normalization — variants trade different contract counts
  // (e.g. A=1c, B=10c, C=7c), so raw-$ leadership isn't a fair strategy
  // comparison. Surface the per-contract leader alongside the raw-$ one.
  const perContract: Record<string, number> = {};
  variantIds.forEach((vid) => {
    const v = variants[vid];
    if (!v?.available) return;
    const c = v.config?.contracts_per_entry ?? 1;
    const s = scores?.[vid] ?? v.summary?.net_pnl ?? 0;
    perContract[vid] = c > 0 ? s / c : s;
  });
  const pcEntries = Object.entries(perContract);
  const pcLeader = pcEntries.length
    ? pcEntries.reduce((a, b) => (b[1] > a[1] ? b : a))[0]
    : null;
  const pcLeaderLabel = pcLeader ? variants[pcLeader]?.label ?? pcLeader : null;

  return (
    <div className="rounded border border-border-dim bg-card p-4">
      <div className="flex items-baseline justify-between gap-4 max-md:flex-col max-md:items-start">
        <div>
          <div className="text-xs uppercase tracking-wide text-text-secondary">
            Today's Leader <span className="text-text-dim normal-case">(raw $)</span>
          </div>
          <div className="text-2xl font-semibold mt-1" style={{ color: winnerColor }}>
            {winnerLabel}
          </div>
          {pcLeaderLabel && pcLeader !== winner && (
            <div className="text-[11px] mt-1" style={{ color: accentFor(pcLeader!) }}>
              Per-contract leader: {pcLeaderLabel}
            </div>
          )}
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
                contracts={v?.config?.contracts_per_entry}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

function PnLBlock({ label, value, accent, contracts }: { label: string; value: number; accent: string; contracts?: number }) {
  return (
    <div className="text-right max-md:text-left">
      <div className="text-[10px] uppercase tracking-wide" style={{ color: accent }}>
        {label}
      </div>
      <div className="text-xl font-mono mt-0.5" style={{ color: pnlColor(value) }}>
        {formatPnL(value)}
      </div>
      {contracts && contracts > 1 && (
        <div
          className="text-[10px] font-mono text-text-dim"
          title={`Per-contract (${contracts}c) — comparable across variants`}
        >
          {formatPnL(value / contracts)}/c
        </div>
      )}
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
  // immediately sees what's being tested. The group's baseline (the first
  // member — variantIds[0], matching the backend's members[0]) is the
  // "control"; rows are bolded where ANY variant differs from it. (Derived,
  // not hardcoded to "A", so calendar groups baseline on D and any future
  // re-ordering just works.)
  const rows: Array<{ key: keyof VariantConfig; label: string }> = [
    { key: "max_spread_width", label: "Spread width cap (pt)" },
    { key: "contracts_per_entry", label: "Contracts/entry" },
    { key: "entry_times", label: "Entry times" },
    { key: "call_starting_otm_multiplier", label: "Call start ×" },
    { key: "put_starting_otm_multiplier", label: "Put start ×" },
    { key: "call_stop_buffer", label: "Call stop buffer ($)" },
    { key: "put_stop_buffer", label: "Put stop buffer ($)" },
    // Brandon Trojan Horse stack (v1.27, 2026-05-04) — primary differentiator
    // between A (no Brandon) and B/C (full stack live).
    { key: "brandon_enabled", label: "Brandon stack" },
    { key: "brandon_tp_enabled", label: "TP at 80% credit" },
    { key: "brandon_gex_strike_adjuster_enabled", label: "GEX strike adjuster" },
    { key: "brandon_gex_breach_exit_enabled", label: "GEX breach exit" },
    { key: "brandon_overlay_enabled", label: "Defensive overlay" },
    { key: "brandon_narrow_spread_enabled", label: "Brandon narrow spread (5/10pt)" },
    { key: "brandon_hydra_stop_shadow_enabled", label: "HYDRA stop (shadow)" },
    // 2026-05-08 restructure — positively phrased to avoid double-negative.
    { key: "brandon_progressive_tightening_enabled", label: "HYDRA tighteners (MKT-020/022)" },
    { key: "brandon_delta_target_enabled", label: "Delta-target strike selection (8δ)" },
    { key: "dry_run", label: "Dry-run" },
  ];

  const baselineId = variantIds[0];

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
            const baseValRaw =
              (variants[baselineId]?.config as Record<string, unknown> | undefined)?.[
                key as string
              ];
            const baseValStr = JSON.stringify(baseValRaw);
            return (
              <tr key={key}>
                <td className="py-0.5">{label}</td>
                {variantIds.map((vid) => {
                  const v = (variants[vid]?.config as Record<string, unknown> | undefined)?.[
                    key as string
                  ];
                  const differs = vid !== baselineId && JSON.stringify(v) !== baseValStr;
                  // Format display based on field type — array → join, threshold
                  // → %, null → em-dash, everything else → String().
                  let display: string;
                  if (v === null || v === undefined) {
                    display = "—";
                  } else if (Array.isArray(v)) {
                    display = v.length ? v.join(", ") : "—";
                  } else if (key === "directional_pivot_threshold_pct" && typeof v === "number") {
                    display = (v * 100).toFixed(2) + "%";
                  } else if (key === "brandon_tp_threshold" && typeof v === "number") {
                    display = (v * 100).toFixed(0) + "%";
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
  const margin = v.min_buffer_margin ?? { call_pct: 100, put_pct: 100 };

  // SIM vs real paper money: A/B run dry (simulated), C places real paper
  // orders. dry_run defaults to "simulated" unless the config explicitly says
  // false, so an unknown mode never masquerades as live money.
  const isSim = v.config?.dry_run !== false;

  // Tightest LIVE cushion across all open sides right now — the panel-level
  // "is anything about to stop?" signal that was previously buried inside each
  // entry's thin bar. null when nothing is live.
  const liveCushions = entries
    .flatMap((e) => [e.buffer?.call_pct, e.buffer?.put_pct])
    .filter((x): x is number => x != null);
  const tightest = liveCushions.length ? Math.min(...liveCushions) : null;

  return (
    <div className="rounded border border-border-dim bg-card p-4 space-y-3">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <span
            className="text-[9px] font-mono px-1 py-px rounded shrink-0"
            style={
              isSim
                ? { color: colors.textDim, border: `1px solid ${colors.borderDim}` }
                : { color: colors.warning, border: `1px solid ${colors.warning}` }
            }
            title={isSim ? "Simulated — no real orders" : "Real paper-account orders"}
          >
            {isSim ? "SIM" : "PAPER $"}
          </span>
          <div className="text-xs uppercase tracking-wide truncate" style={{ color: accent }}>
            {v.label}
          </div>
        </div>
        <div className="text-xs text-text-secondary whitespace-nowrap">
          {summary.entries_completed} {summary.entries_completed === 1 ? "entry" : "entries"}
          {summary.total_stops > 0 && (
            <span style={{ color: colors.loss }}> · {summary.total_stops} stopped</span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-sm">
        <Metric label="Net P&L" value={formatPnL(summary.net_pnl)} colored={summary.net_pnl} />
        <Metric label="Credit" value={`$${summary.total_credit_received.toFixed(0)}`} />
        <Metric label="Commission" value={`$${summary.total_commission.toFixed(0)}`} />
      </div>

      {tightest !== null && tightest < 25 && (
        <div
          className="text-[11px] font-mono rounded px-2 py-1"
          style={{ backgroundColor: "rgba(248,81,73,0.08)", color: cushionColor(tightest) }}
        >
          ⚠ tightest live cushion {tightest.toFixed(0)}% — near a stop
        </div>
      )}

      {entries.length === 0 ? (
        <div className="text-sm text-text-dim italic">No entries yet today.</div>
      ) : (
        <div className="space-y-2">
          {entries.map((e, i) => (
            <EntryRow key={i} entry={e} accent={accent} spx={v.spx_price} />
          ))}
        </div>
      )}

      <div className="border-t border-border-dim pt-2">
        <div className="text-xs text-text-secondary mb-1">
          Worst cushion today{" "}
          <span className="text-text-dim">(low-water — not live · 100% safe → 0% at stop)</span>
        </div>
        <div className="flex gap-4 text-xs">
          <BufferBar label="Call" pct={margin.call_pct} muted />
          <BufferBar label="Put" pct={margin.put_pct} muted />
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

// ── Per-side lifecycle resolution ───────────────────────────────────────────
// An iron condor has TWO independent sides (call spread + put spread) that can
// close on their own. The old card collapsed both into one badge ("LIVE if any
// side still open"), so a half-stopped entry (call stopped, put still live)
// rendered as a calm "LIVE" with the stopped leg silently dropped — which is
// exactly what made variant A look wrong after its call side stopped. We now
// resolve and render each side explicitly.
type SideState = "live" | "stopped" | "breach" | "expired" | "skipped" | "absent";

interface SideInfo {
  state: SideState;
  shortStrike?: number;
  longStrike?: number;
  credit?: number;
  cushionPct: number | null; // live: % distance from the (effective) stop
  cost: number | null;       // live: current cost-to-close ($)
  stop?: number;             // live trigger level ($) — effective (MKT-042) when available
  distancePt: number | null; // live: SPX points from spot to the short strike (+ = OTM)
  unrealized: number | null; // live: mark-to-market P&L for this side ($)
  realized: number | null;   // closed: this side's realized P&L ($), null if unknown
  closeTime?: string;        // closed: HH:MM the side closed
}

function fmtClock(iso?: string): string | undefined {
  if (!iso || iso.length < 16) return undefined;
  return iso.slice(11, 16);
}

function resolveSide(entry: VariantEntry, side: "call" | "put", spx?: number): SideInfo {
  const short = side === "call" ? entry.short_call_strike : entry.short_put_strike;
  const long = side === "call" ? entry.long_call_strike : entry.long_put_strike;
  const credit = side === "call" ? entry.call_spread_credit : entry.put_spread_credit;
  const staticStop = side === "call" ? entry.call_side_stop : entry.put_side_stop;
  // MKT-042 decays the stop over the day — the effective stop is the live
  // trigger (matches the main dashboard). Fall back to the static stop.
  const effStop = (side === "call" ? entry.effective_call_stop : entry.effective_put_stop) ?? staticStop;
  const stopped = side === "call" ? entry.call_side_stopped : entry.put_side_stopped;
  const expired = side === "call" ? entry.call_side_expired : entry.put_side_expired;
  const skipped = side === "call" ? entry.call_side_skipped : entry.put_side_skipped;
  const pivot = side === "call" ? entry.call_side_pivot_closed : entry.put_side_pivot_closed;
  const debit = side === "call" ? entry.actual_call_stop_debit : entry.actual_put_stop_debit;
  const stopTime = side === "call" ? entry.call_stop_time : entry.put_stop_time;
  const cost = (side === "call" ? entry.buffer?.call_value : entry.buffer?.put_value) ?? null;
  const backendPct = (side === "call" ? entry.buffer?.call_pct : entry.buffer?.put_pct) ?? null;

  // Not placed (one-sided HYDRA entry, or a fully-skipped slot with 0 strikes).
  if (!short || short <= 0) {
    return { state: "absent", cushionPct: null, cost: null, distancePt: null, unrealized: null, realized: null };
  }

  // Cushion vs the EFFECTIVE stop so the bar matches the live trigger (not the
  // static one the backend used); fall back to the backend pct if we can't.
  let cushionPct = backendPct;
  if (effStop && effStop > 0 && cost != null) {
    cushionPct = Math.round(Math.max(0, Math.min(100, ((effStop - cost) / effStop) * 100)) * 10) / 10;
  }
  // SPX points from spot to the short strike (+ = still OTM/safe, − = ITM).
  const distancePt = spx && spx > 0 ? (side === "call" ? short - spx : spx - short) : null;
  // Live mark-to-market for this side = credit kept − current cost to close.
  const unrealized = cost != null && credit != null ? credit - cost : null;

  // Realized only when we actually have the close debit — otherwise showing
  // "credit only" would mislabel a stop (a loss) as a gain.
  const haveDebit = debit != null && debit > 0;
  const closedPnl = haveDebit ? (credit ?? 0) - (debit ?? 0) : null;
  // close_reason is entry-level; we only use it to rescue a Brandon take-profit
  // (which sets both *_stopped and *_expired) from being mislabeled a loss.
  const tp = entry.close_reason === "TP";

  let state: SideState = "live";
  let realized: number | null = null;
  let closeTime: string | undefined;
  if (skipped) {
    state = "skipped";
  } else if (tp && (stopped || expired)) {
    state = "expired"; // profitable close → show as a win, not a stop
    realized = closedPnl;
    closeTime = fmtClock(stopTime);
  } else if (pivot) {
    state = "breach";
    realized = closedPnl;
    closeTime = fmtClock(stopTime);
  } else if (stopped) {
    state = "stopped";
    realized = closedPnl;
    closeTime = fmtClock(stopTime);
  } else if (expired) {
    state = "expired";
    realized = credit ?? null; // expired worthless = kept the full credit
  }

  const live = state === "live";
  return {
    state, shortStrike: short, longStrike: long, credit,
    cushionPct: live ? cushionPct : null,
    cost, stop: effStop,
    distancePt: live ? distancePt : null,
    unrealized: live ? unrealized : null,
    realized, closeTime,
  };
}

/** One side (call or put) of an entry — strikes + a state-specific second line. */
function SideLine({ tag, info }: { tag: "C" | "P"; info: SideInfo }) {
  if (info.state === "absent") {
    return (
      <div className="text-text-dim">
        <span className="text-text-dim">{tag}:</span> —
      </div>
    );
  }
  const live = info.state === "live";
  const strikeColor = live ? colors.textPrimary : colors.textSecondary;
  const realizedStr =
    info.realized !== null ? `${info.realized > 0 ? "+" : ""}$${info.realized.toFixed(0)}` : "";

  return (
    <div>
      <div>
        <span className="text-text-dim">{tag}:</span>{" "}
        <span style={{ color: strikeColor }}>
          {info.shortStrike}/{info.longStrike}
        </span>
        <span className="text-text-dim ml-1.5">cr ${info.credit?.toFixed(0)}</span>
      </div>

      {live && info.cushionPct !== null && (
        <div
          className="mt-0.5"
          title={`close $${info.cost?.toFixed(0)} → stop $${info.stop?.toFixed(0)} (effective)`}
        >
          <div className="flex justify-between text-[10px] leading-none mb-0.5">
            <span className="text-text-dim">
              {info.distancePt != null
                ? info.distancePt >= 0
                  ? `${info.distancePt.toFixed(0)}pt OTM`
                  : `ITM ${Math.abs(info.distancePt).toFixed(0)}pt`
                : `close $${info.cost?.toFixed(0)} → $${info.stop?.toFixed(0)}`}
            </span>
            <span className="font-mono" style={{ color: cushionColor(info.cushionPct) }}>
              {info.cushionPct < 15 ? "⚠ " : ""}
              {info.cushionPct.toFixed(0)}%
            </span>
          </div>
          <BufferBar pct={info.cushionPct} compact />
        </div>
      )}

      {(info.state === "stopped" || info.state === "breach") && (
        <div
          className="mt-0.5 font-mono"
          style={{ color: info.state === "breach" ? colors.warning : colors.loss }}
        >
          {info.state === "breach" ? "⚠ BREACH" : "✗ STOPPED"}
          {realizedStr && ` ${realizedStr}`}
          {info.closeTime && <span className="text-text-dim"> @{info.closeTime}</span>}
        </div>
      )}

      {info.state === "expired" && (
        <div className="mt-0.5 font-mono" style={{ color: colors.profit }}>
          ✓ kept {realizedStr || "credit"}
        </div>
      )}

      {info.state === "skipped" && <div className="mt-0.5 text-text-dim">– skipped</div>}
    </div>
  );
}

function EntryRow({ entry, accent, spx }: { entry: VariantEntry; accent: string; spx?: number }) {
  const num = entry.entry_number ?? "?";
  const time = entry.entry_time?.slice(11, 16) ?? "—";
  const disposition = entry.disposition ?? "LIVE";

  // A fully-skipped slot: collapse to one clean line — no phantom "0/0 $0".
  const fullySkipped =
    disposition === "SKIPPED" ||
    (entry.call_side_skipped && entry.put_side_skipped) ||
    (!entry.short_call_strike && !entry.short_put_strike);
  if (fullySkipped) {
    return (
      <div className="rounded border border-border-dim bg-bg p-2 text-xs flex items-center justify-between">
        <span className="font-mono">
          <span style={{ color: accent }}>#{num}</span>{" "}
          <span className="text-text-secondary">{time}</span>
        </span>
        <span
          className="text-[10px] font-mono uppercase tracking-wider"
          style={{ color: colors.textDim }}
        >
          skipped{entry.skip_reason ? ` · ${entry.skip_reason}` : " · credit gate"}
        </span>
      </div>
    );
  }

  const call = resolveSide(entry, "call", spx);
  const put = resolveSide(entry, "put", spx);
  const anyLive = call.state === "live" || put.state === "live";
  const anyStopped =
    call.state === "stopped" || call.state === "breach" ||
    put.state === "stopped" || put.state === "breach";

  // Rollup badge. THE FIX: when one side has stopped but the other is still
  // open, the entry reads "PARTIAL" (amber) — never a calm "LIVE".
  let badge: string;
  let badgeColor: string;
  if (anyLive && anyStopped) {
    badge = "PARTIAL";
    badgeColor = colors.warning;
  } else if (anyLive) {
    badge = disposition === "SETTLING" ? "SETTLING" : "LIVE";
    badgeColor = disposition === "SETTLING" ? colors.textSecondary : accent;
  } else {
    badge = disposition;
    badgeColor =
      disposition === "TP" || disposition === "EXPIRED" ? colors.profit :
      disposition === "STOP" || disposition === "BREACH" ? colors.loss :
      disposition === "SKIPPED" ? colors.warning :
      colors.textSecondary;
  }

  // Current entry P&L = realized from closed side(s) + live mark on open
  // side(s). Surfaces a stopped leg's loss even while the other leg runs.
  const realizedTotal = (call.realized ?? 0) + (put.realized ?? 0);
  const liveUnrealized = (call.unrealized ?? 0) + (put.unrealized ?? 0);
  const currentPnl = realizedTotal + liveUnrealized;

  return (
    <div className="rounded border border-border-dim bg-bg p-2 text-xs">
      <div className="flex items-center justify-between mb-1">
        <div className="font-mono">
          <span style={{ color: accent }}>#{num}</span>{" "}
          <span className="text-text-secondary">{time}</span>
        </div>
        <div className="flex items-center gap-2">
          {Math.round(currentPnl) !== 0 && (
            <span className="text-[11px] font-mono" style={{ color: pnlColor(currentPnl) }}>
              {currentPnl > 0 ? "+" : ""}${currentPnl.toFixed(0)}
            </span>
          )}
          <span
            className="text-[10px] font-mono uppercase tracking-wider"
            style={{ color: badgeColor }}
          >
            {badge}
          </span>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 font-mono text-text-secondary">
        <SideLine tag="C" info={call} />
        <SideLine tag="P" info={put} />
      </div>
    </div>
  );
}

function mutedCushion(pct: number): string {
  if (pct >= 40) return colors.profitMuted;
  if (pct >= 25) return colors.warningMuted;
  if (pct >= 15) return "#a6602b";
  return colors.lossMuted;
}

function BufferBar({
  pct,
  label,
  compact = false,
  muted = false,
}: {
  pct: number | null;
  label?: string;
  compact?: boolean;
  muted?: boolean;
}) {
  const v = pct ?? 100;
  // Buffer MARGIN color (matches the live cards' cushion): green = lots of
  // cushion, red = near the stop. Fixed 2026-06-12 — this used to color
  // utilization (inverted), so a safe day looked red and a near-stop day green.
  // `muted` desaturates the scale for the footer's historical "worst today"
  // bars so they read as past, not as a live alarm.
  const color = muted ? mutedCushion(v) : cushionColor(v);
  const height = compact ? "h-1.5" : "h-2";

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
          // Margin fill: a fuller bar = more cushion (safer). Clamped to [0,100].
          style={{ width: `${Math.max(0, Math.min(v, 100))}%`, backgroundColor: color }}
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
  // Merge all variants' P&L history onto a single time axis.
  //
  // Bug fix 2026-05-07: prior version zipped by ARRAY INDEX, assuming all
  // variants share the same heartbeat cadence and start time. They don't —
  // each variant's pnl_history starts at the minute its first entry was
  // placed (B at 09:31, A at 10:45, C at 10:46 on May 7). Index-zipping
  // produced a non-monotonic x-axis: once A's shorter series ran out, row.
  // time fell through to B's earlier timestamps and the chart visibly
  // jumped backwards in time mid-graph. Now we union all time keys, sort
  // them in HH:MM order, and look up each variant's value at each time —
  // properly handling missing points as null.
  const histories = variantIds.map((vid) => ({
    id: vid,
    label: variants[vid]?.label ?? vid,
    // Intraday overlay: regular session only — drop after-hours/restart points.
    series: (variants[vid]?.pnl_history ?? []).filter((p) => isRegularSessionTime(p.time)),
  }));
  const totalPoints = histories.reduce((acc, h) => acc + h.series.length, 0);

  if (totalPoints === 0) {
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

  // Build a per-variant time→pnl lookup, then union all times and sort.
  const lookups = histories.map((h) => {
    const m = new Map<string, number>();
    for (const p of h.series) m.set(p.time, p.pnl);
    return { id: h.id, label: h.label, map: m };
  });
  const allTimes = new Set<string>();
  for (const l of lookups) for (const t of l.map.keys()) allTimes.add(t);
  // HH:MM strings sort lexicographically in chronological order.
  const sortedTimes = [...allTimes].sort();

  const merged: Array<Record<string, string | number | null>> = sortedTimes.map((t) => {
    const row: Record<string, string | number | null> = { time: t };
    for (const l of lookups) {
      row[`pnl_${l.id.toLowerCase()}`] = l.map.has(t) ? (l.map.get(t) as number) : null;
    }
    return row;
  });

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
    ["Min call buffer margin", (v) => `${(v?.min_buffer_margin?.call_pct ?? 100).toFixed(0)}%`],
    ["Min put buffer margin", (v) => `${(v?.min_buffer_margin?.put_pct ?? 100).toFixed(0)}%`],
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
  const rows: Array<[string, (lt: AggregateLifetime) => string, "pnl" | "best" | "worst" | "roi" | undefined]> =
    [
      ["Cumulative P&L", (lt) => formatPnL(lt.cumulative_pnl), "pnl"],
      ["ROI (on capital)", (lt) => `${(lt.roi_pct ?? 0) >= 0 ? "+" : ""}${(lt.roi_pct ?? 0).toFixed(2)}%`, "roi"],
      ["Avg capital / day", (lt) => `$${Math.round(lt.avg_capital_per_day ?? 0).toLocaleString()}`, undefined],
      ["Capital deployed", (lt) => `$${Math.round(lt.capital_deployed ?? 0).toLocaleString()}`, undefined],
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
                {agg.variants[vid]?.baseline_date && (
                  <div className="text-[10px] text-text-dim font-normal normal-case">
                    since {agg.variants[vid]?.baseline_date}
                  </div>
                )}
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
                else if (kind === "roi") color = pnlColor(lt.roi_pct ?? 0);
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
